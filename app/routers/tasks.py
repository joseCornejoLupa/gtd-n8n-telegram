from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import or_, select, text
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_session
from app.models import Context, Project, Task, User
from app.presenters import load_page, load_task
from app.routers.lists import context_filter
from app.schemas import (
    ALLOWED_FIELDS,
    BIGINT_MAX,
    STATUS_FOR_ACTION,
    ClarifyIn,
    CompleteOut,
    ProjectBrief,
    TaskListOut,
    TaskPatch,
    TaskStatus,
    TaskWithAvailability,
)
from app.services import (
    append_to_project,
    get_or_create_context,
    is_available,
    localize,
    project_counts,
    user_zone,
)

router = APIRouter(tags=["tareas"])

TaskId = Annotated[int, Path(ge=1, le=BIGINT_MAX)]

# Estados abiertos entre los que PATCH puede mover una tarea.
_EDITABLE = ("next", "waiting", "someday", "reference")

_NOT_EDITABLE_REASON = {
    "inbox": "La tarea sigue en el inbox: usa /clarify",
    "done": "La tarea está completada: reábrela primero",
    "trashed": "La tarea está descartada: reábrela primero",
    "converted": "La tarea ya se convirtió en proyecto",
}


def _owned_task(session: Session, user: User, task_id: int) -> Task:
    """La tarea del usuario, bloqueada (FOR UPDATE) para serializar cambios concurrentes."""
    task = session.scalar(
        select(Task).where(Task.id == task_id, Task.user_id == user.id).with_for_update()
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return task


def _usable_project(session: Session, user: User, project_id: int) -> Project:
    """El proyecto del usuario (bloqueado) si admite tareas nuevas; si no, 404 o 409."""
    project = session.scalar(
        select(Project)
        .where(Project.id == project_id, Project.user_id == user.id)
        .with_for_update()
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    if project.status not in ("active", "someday"):
        raise HTTPException(
            status_code=409, detail=f"El proyecto está cerrado (estado: {project.status})"
        )
    return project


def _with_availability(session: Session, user: User, task: Task) -> TaskWithAvailability:
    return load_task(
        session, task.id, user_zone(user),
        model=TaskWithAvailability, available_now=is_available(session, task.id),
    )


# --- Consulta --------------------------------------------------------------------------


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


@router.get("/tasks", response_model=TaskListOut)
def list_tasks(
    status: Annotated[list[TaskStatus] | None, Query()] = None,
    project_id: int | None = Query(default=None, ge=1, le=BIGINT_MAX),
    context: str | None = Query(default=None, max_length=60),
    q: str | None = Query(default=None, max_length=100, description="Busca en título, captura y notas"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> TaskListOut:
    """Búsqueda general, de lo más reciente a lo más antiguo. Sin `status`, no incluye las
    descartadas ni las convertidas en proyecto (se pueden pedir explícitamente)."""
    conditions = [Task.user_id == user.id]
    if status:
        conditions.append(Task.status.in_(status))
    else:
        conditions.append(Task.status.notin_(("trashed", "converted")))
    if project_id is not None:
        conditions.append(Task.project_id == project_id)
    if (name := context_filter(context)) is not None:
        conditions.append(Context.name == name)
    if q is not None and q.strip():
        like = f"%{_escape_like(q.strip())}%"
        conditions.append(
            or_(
                Task.title.ilike(like, escape="\\"),
                Task.raw_text.ilike(like, escape="\\"),
                Task.notes.ilike(like, escape="\\"),
            )
        )
    total, items = load_page(
        session, conditions, (Task.captured_at.desc(), Task.id.desc()), limit, user_zone(user), offset
    )
    return TaskListOut(count=total, items=items)


@router.get("/tasks/{task_id}", response_model=TaskWithAvailability)
def get_task(
    task_id: TaskId,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> TaskWithAvailability:
    task = session.scalar(select(Task).where(Task.id == task_id, Task.user_id == user.id))
    if task is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    return _with_availability(session, user, task)


# --- Clarificar ------------------------------------------------------------------------


@router.post("/tasks/{task_id}/clarify", response_model=TaskWithAvailability)
def clarify(
    task_id: TaskId,
    body: ClarifyIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> TaskWithAvailability:
    """Convierte una captura del inbox en lo que realmente es.

    `action`: next (siguiente acción), waiting (a la espera de alguien), someday (algún día),
    reference (solo información) o trash (descartar). Cada acción admite solo algunos campos;
    ver el esquema. `raw_text` (lo capturado) nunca se modifica.
    """
    # FOR UPDATE: si n8n reintenta en paralelo, la segunda llamada espera y recibe 409.
    task = _owned_task(session, user, task_id)
    if task.status != "inbox":
        raise HTTPException(
            status_code=409, detail=f"La tarea ya no está en el inbox (estado: {task.status})"
        )

    project = _usable_project(session, user, body.project_id) if body.project_id else None

    now = datetime.now(timezone.utc)
    task.status = STATUS_FOR_ACTION[body.action]
    if body.action == "trash":
        task.closed_from = "inbox"  # reabrirla la devuelve al inbox
    task.title = body.title
    task.notes = body.notes
    task.waiting_on = body.waiting_on
    if body.action == "waiting":
        task.waiting_since = now
    task.due_at = localize(body.due_at, user.timezone)
    task.defer_until = localize(body.defer_until, user.timezone)
    if body.context is not None:
        task.context_id = get_or_create_context(session, user.id, body.context).id
    if project is not None:
        append_to_project(session, task, project)
    task.clarified_at = now
    session.flush()

    result = _with_availability(session, user, task)
    session.commit()
    return result


# --- Editar ----------------------------------------------------------------------------


@router.patch("/tasks/{task_id}", response_model=TaskWithAvailability)
def patch_task(
    task_id: TaskId,
    body: TaskPatch,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> TaskWithAvailability:
    """Edita una tarea abierta y/o la mueve entre next, waiting, someday y reference.

    Sigue las mismas reglas que /clarify según el estado resultante: `next` exige título,
    `waiting` exige título y `waiting_on`, y solo se aceptan los campos de ese estado. Al
    salir de `waiting` se borran `waiting_on` y `waiting_since`. Completar, descartar y
    reabrir tienen sus propios endpoints.
    """
    task = _owned_task(session, user, task_id)
    if task.status not in _EDITABLE:
        raise HTTPException(status_code=409, detail=_NOT_EDITABLE_REASON[task.status])

    new_status = body.status or task.status
    clear = set(body.clear or [])

    extra = body.sent_fields - ALLOWED_FIELDS[new_status]
    if extra:
        raise HTTPException(
            status_code=422,
            detail=f"Campos no permitidos para '{new_status}': {', '.join(sorted(extra))}",
        )

    title = body.title if body.title is not None else task.title
    if new_status != "waiting":
        waiting_on = None
    elif body.waiting_on is not None:
        waiting_on = body.waiting_on
    else:
        waiting_on = None if "waiting_on" in clear else task.waiting_on

    missing = []
    if new_status in ("next", "waiting") and not title:
        missing.append("title")
    if new_status == "waiting" and not waiting_on:
        missing.append("waiting_on")
    if missing:
        raise HTTPException(
            status_code=422,
            detail=f"Para '{new_status}' son obligatorios: {', '.join(missing)}",
        )

    project = None
    if body.project_id is not None and body.project_id != task.project_id:
        project = _usable_project(session, user, body.project_id)

    now = datetime.now(timezone.utc)
    if new_status == "waiting":
        if task.status != "waiting":
            task.waiting_since = now
    else:
        task.waiting_since = None
    task.status = new_status
    task.title = title
    task.waiting_on = waiting_on

    if body.notes is not None:
        task.notes = body.notes
    elif "notes" in clear:
        task.notes = None
    if body.due_at is not None:
        task.due_at = localize(body.due_at, user.timezone)
    elif "due_at" in clear:
        task.due_at = None
    if body.defer_until is not None:
        task.defer_until = localize(body.defer_until, user.timezone)
    elif "defer_until" in clear:
        task.defer_until = None

    if body.context is not None:
        task.context_id = get_or_create_context(session, user.id, body.context).id
    elif "context" in clear:
        task.context_id = None

    if project is not None:
        append_to_project(session, task, project)
    elif "project_id" in clear:
        task.project_id = None
        task.position = None
    session.flush()

    result = _with_availability(session, user, task)
    session.commit()
    return result


# --- Completar, reabrir, descartar -----------------------------------------------------


@router.post("/tasks/{task_id}/complete", response_model=CompleteOut)
def complete(
    task_id: TaskId,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> CompleteOut:
    """Marca la tarea como hecha y dice cómo queda su proyecto.

    Si era la última acción viva de un proyecto activo, `project_stalled` es true: el bot
    debería preguntar por la siguiente acción (o si el proyecto terminó, si
    `project_open_tasks` es 0). En un proyecto secuencial, `next_task` es el paso que acaba
    de quedar disponible. Es idempotente: completar una tarea ya completada responde 200 con
    `already_completed: true`.
    """
    task = _owned_task(session, user, task_id)
    already = task.status == "done"
    if not already:
        if task.status not in ("inbox", "next", "waiting", "someday"):
            raise HTTPException(
                status_code=409,
                detail=f"No se puede completar una tarea en estado '{task.status}'",
            )
        now = datetime.now(timezone.utc)
        task.closed_from = task.status
        task.status = "done"
        task.completed_at = now
        if task.clarified_at is None:  # completada directo desde el inbox
            task.clarified_at = now
        session.flush()

    # Se bloquea el proyecto: así, si se completan varias tareas a la vez, la última en
    # confirmar ve a las demás ya hechas y sí detecta que el proyecto quedó estancado.
    project = (
        session.scalar(select(Project).where(Project.id == task.project_id).with_for_update())
        if task.project_id is not None
        else None
    )
    stalled, open_tasks, next_task = False, 0, None
    if project is not None:
        live, open_tasks = project_counts(session, project.id)
        stalled = project.status == "active" and live == 0
        if project.sequential and not already:
            row = session.execute(
                text(
                    "SELECT id FROM available_tasks WHERE project_id = :p "
                    "ORDER BY position, id LIMIT 1"
                ),
                {"p": project.id},
            ).first()
            if row is not None:
                next_task = load_task(session, row.id, user_zone(user))

    result = CompleteOut(
        task=load_task(session, task.id, user_zone(user)),
        already_completed=already,
        project=ProjectBrief(
            id=project.id, title=project.title, sequential=project.sequential, status=project.status
        ) if project is not None else None,
        project_stalled=stalled,
        project_open_tasks=open_tasks,
        next_task=next_task,
    )
    session.commit()
    return result


@router.post("/tasks/{task_id}/reopen", response_model=TaskWithAvailability)
def reopen(
    task_id: TaskId,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> TaskWithAvailability:
    """Deshace completar, descartar o convertir en proyecto: vuelve al estado en que estaba."""
    task = _owned_task(session, user, task_id)
    if task.status == "converted":
        # Se puede deshacer una conversión, pero solo si su proyecto ya se descartó.
        origin = session.scalar(
            select(Project)
            .where(Project.origin_task_id == task.id)
            .order_by(Project.id.desc())
            .limit(1)
        )
        if origin is None or origin.status != "dropped":
            raise HTTPException(
                status_code=409,
                detail="La tarea se convirtió en proyecto: para deshacerlo descarta el proyecto "
                "(POST /projects/{id}/drop) y reabre la tarea",
            )
    elif task.status not in ("done", "trashed"):
        raise HTTPException(
            status_code=409,
            detail=f"La tarea no está completada ni descartada (estado: {task.status})",
        )
    # closed_from guarda el estado previo; si falta (datos antiguos) se deduce de lo que tiene.
    restored = task.closed_from or (
        "waiting" if task.waiting_on else "next" if task.title else "inbox"
    )
    task.status = restored
    task.closed_from = None
    task.completed_at = None
    if restored == "waiting" and task.waiting_since is None:
        task.waiting_since = datetime.now(timezone.utc)
    if restored == "inbox":
        task.clarified_at = None
    session.flush()

    result = _with_availability(session, user, task)
    session.commit()
    return result


@router.delete("/tasks/{task_id}", response_model=TaskWithAvailability)
def discard(
    task_id: TaskId,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> TaskWithAvailability:
    """Descarta la tarea (no la borra: queda en estado `trashed` y se puede reabrir).
    Idempotente: descartar una ya descartada responde 200."""
    task = _owned_task(session, user, task_id)
    if task.status == "converted":
        raise HTTPException(status_code=409, detail=_NOT_EDITABLE_REASON["converted"])
    if task.status != "trashed":
        if task.status != "done":  # si ya estaba completada se conserva el estado previo
            task.closed_from = task.status
        task.status = "trashed"
        session.flush()

    result = _with_availability(session, user, task)
    session.commit()
    return result
