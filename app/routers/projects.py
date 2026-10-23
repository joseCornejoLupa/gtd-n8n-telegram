"""Proyectos: un resultado que requiere más de una acción.

Un proyecto activo siempre debería tener una siguiente acción; si no la tiene está
`stalled` y el bot debe preguntar por ella. En un proyecto secuencial solo se muestra la
primera tarea abierta (ver la vista `available_tasks`).
"""

from datetime import datetime, timezone
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Response
from sqlalchemy import func, or_, select, text
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_session
from app.models import Project, Task, User
from app.presenters import load_page, load_task, project_out
from app.routers.tasks import TaskId, _escape_like, _owned_task
from app.schemas import (
    BIGINT_MAX,
    CloseOut,
    ConvertIn,
    ConvertOut,
    ProjectClose,
    ProjectCreate,
    ProjectDetail,
    ProjectListOut,
    ProjectOut,
    ProjectPatch,
    ProjectStatus,
    ReorderIn,
    StepsIn,
    StepsOut,
)
from app.services import (
    NotSequentialError,
    create_steps,
    insertion_index,
    lock_project,
    ordered_tasks,
    project_stats,
    renumber,
    stalled_conditions,
    step_ref,
    user_zone,
)

router = APIRouter(tags=["proyectos"])

ProjectId = Annotated[int, Path(ge=1, le=BIGINT_MAX)]

_OPEN = ("next", "waiting", "someday")


def _locked_project(session: Session, user: User, project_id: int) -> Project:
    project = lock_project(session, user.id, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    return project


def _brief(session: Session, user: User, project: Project, model=ProjectOut, **extra):
    stats = project_stats(session, [project.id]).get(project.id, (0, 0))
    return project_out(project, stats, user_zone(user), model=model, **extra)


def _detail(session: Session, user: User, project: Project, include_closed: bool = False):
    tz = user_zone(user)
    conditions = [Task.user_id == user.id, Task.project_id == project.id]
    if not include_closed:
        conditions.append(Task.status.in_(_OPEN))
    _, tasks = load_page(
        session, conditions, (Task.position.asc().nulls_last(), Task.id), 500, tz
    )
    row = session.execute(
        text(
            "SELECT id FROM available_tasks WHERE project_id = :p "
            "ORDER BY position NULLS LAST, id LIMIT 1"
        ),
        {"p": project.id},
    ).first()
    next_task = load_task(session, row.id, tz) if row is not None else None
    return _brief(session, user, project, model=ProjectDetail, next_task=next_task, tasks=tasks)


def _create_steps(session, user, project, body, **placement):
    try:
        return create_steps(session, user, project, body, **placement)
    except NotSequentialError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    except LookupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


# --- Consulta --------------------------------------------------------------------------


@router.get("/projects", response_model=ProjectListOut)
def list_projects(
    status: Annotated[list[ProjectStatus] | None, Query()] = None,
    q: str | None = Query(default=None, max_length=100, description="Busca en título y resultado"),
    limit: int = Query(default=50, ge=1, le=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ProjectListOut:
    """Tus proyectos, del más antiguo al más nuevo. Sin `status`: activos y algún día."""
    conditions = [
        Project.user_id == user.id,
        Project.status.in_(status or ("active", "someday")),
    ]
    if q is not None and q.strip():
        like = f"%{_escape_like(q.strip())}%"
        conditions.append(
            or_(Project.title.ilike(like, escape="\\"), Project.outcome.ilike(like, escape="\\"))
        )
    total = session.scalar(select(func.count(Project.id)).where(*conditions))
    rows = session.scalars(
        select(Project).where(*conditions).order_by(Project.id).limit(limit).offset(offset)
    ).all()
    stats = project_stats(session, [p.id for p in rows])
    tz = user_zone(user)
    return ProjectListOut(
        count=total, items=[project_out(p, stats.get(p.id, (0, 0)), tz) for p in rows]
    )


@router.get("/projects/stalled", response_model=ProjectListOut)
def stalled_projects(
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ProjectListOut:
    """Proyectos activos sin ninguna siguiente acción ni espera: cada uno necesita que
    decidas el próximo paso (o que lo pauses, lo cierres o lo descartes)."""
    conditions = stalled_conditions(user.id)
    total = session.scalar(select(func.count(Project.id)).where(*conditions))
    rows = session.scalars(
        select(Project).where(*conditions).order_by(Project.id).limit(limit)
    ).all()
    stats = project_stats(session, [p.id for p in rows])
    tz = user_zone(user)
    return ProjectListOut(
        count=total, items=[project_out(p, stats.get(p.id, (0, 0)), tz) for p in rows]
    )


@router.get("/projects/{project_id}", response_model=ProjectDetail)
def get_project(
    project_id: ProjectId,
    include_closed: bool = Query(default=False, description="Incluir tareas hechas y descartadas"),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ProjectDetail:
    project = session.scalar(
        select(Project).where(Project.id == project_id, Project.user_id == user.id)
    )
    if project is None:
        raise HTTPException(status_code=404, detail="Proyecto no encontrado")
    return _detail(session, user, project, include_closed)


# --- Crear y editar --------------------------------------------------------------------


@router.post("/projects", response_model=ProjectDetail, status_code=201)
def create_project(
    body: ProjectCreate,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ProjectDetail:
    """Crea un proyecto, opcionalmente con sus primeras tareas (`tasks`).

    Por defecto es secuencial (`sequential: true`). Ojo: no es idempotente; si n8n reintenta
    se crea otro. Para convertir una captura en proyecto usa `/tasks/{id}/convert-to-project`,
    que sí lo es.
    """
    project = Project(
        user_id=user.id,
        title=body.title,
        outcome=body.outcome,
        status=body.status or "active",
        sequential=True if body.sequential is None else body.sequential,
    )
    session.add(project)
    session.flush()
    _create_steps(session, user, project, body.tasks)
    result = _detail(session, user, project)
    session.commit()
    return result


@router.patch("/projects/{project_id}", response_model=ProjectDetail)
def patch_project(
    project_id: ProjectId,
    body: ProjectPatch,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ProjectDetail:
    """Edita título y resultado, pausa/reactiva (`status`) o cambia entre secuencial y paralelo.

    Al pasar a secuencial las tareas se ordenan por antigüedad; al pasar a paralelo se
    olvida el orden. Reactivar un proyecto cerrado (`status: active`) no reabre sus tareas.
    """
    project = _locked_project(session, user, project_id)
    if body.title is not None:
        project.title = body.title
    if body.outcome is not None:
        project.outcome = body.outcome
    elif "outcome" in (body.clear or []):
        project.outcome = None
    if body.status is not None:
        if project.status in ("done", "dropped"):
            project.completed_at = None
        project.status = body.status
    if body.sequential is not None and body.sequential != project.sequential:
        project.sequential = body.sequential
        tasks = list(
            session.scalars(
                select(Task).where(Task.project_id == project.id).order_by(Task.captured_at, Task.id)
            )
        )
        if body.sequential:
            renumber(session, tasks)
        else:
            for task in tasks:
                task.position = None
            session.flush()
    session.flush()
    result = _detail(session, user, project)
    session.commit()
    return result


# --- Tareas del proyecto ---------------------------------------------------------------


@router.post("/projects/{project_id}/tasks", response_model=StepsOut, status_code=201)
def add_tasks(
    project_id: ProjectId,
    body: StepsIn,
    response: Response,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> StepsOut:
    """Añade tareas nuevas, ya clarificadas, a un proyecto (hasta 50 de una vez).

    En un proyecto secuencial van al final, o donde indiques con `after_task_id` / `first`.
    Con `request_id` es idempotente: repetir la petición responde 200 con las mismas tareas.
    """
    project = _locked_project(session, user, project_id)
    if project.status in ("done", "dropped"):
        raise HTTPException(
            status_code=409, detail=f"El proyecto está cerrado (estado: {project.status})"
        )
    tz = user_zone(user)
    if body.request_id:
        refs = [step_ref(project.id, body.request_id, i) for i in range(len(body.tasks))]
        existing = session.scalars(
            select(Task)
            .where(Task.user_id == user.id, Task.source == "api", Task.source_ref.in_(refs))
            .order_by(Task.id)
        ).all()
        if existing:
            response.status_code = 200
            return StepsOut(
                created=False,
                project=_brief(session, user, project),
                tasks=[load_task(session, t.id, tz) for t in existing],
            )
    tasks = _create_steps(
        session, user, project, body.tasks,
        after_task_id=body.after_task_id, first=body.first, request_id=body.request_id,
    )
    result = StepsOut(
        created=True,
        project=_brief(session, user, project),
        tasks=[load_task(session, t.id, tz) for t in tasks],
    )
    session.commit()
    return result


@router.post("/projects/{project_id}/reorder", response_model=ProjectDetail)
def reorder(
    project_id: ProjectId,
    body: ReorderIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ProjectDetail:
    """Mueve una tarea de un proyecto secuencial: al principio (`first`) o tras otra
    (`after_task_id`). Las posiciones quedan contiguas (1, 2, 3...)."""
    project = _locked_project(session, user, project_id)
    if not project.sequential:
        raise HTTPException(
            status_code=409, detail="El proyecto no es secuencial: sus tareas no tienen orden"
        )
    ordered = ordered_tasks(session, project.id)
    moved = next((t for t in ordered if t.id == body.task_id), None)
    if moved is None:
        raise HTTPException(status_code=404, detail="La tarea no está en este proyecto")
    ordered.remove(moved)
    try:
        index = insertion_index(ordered, body.after_task_id, body.first)
    except LookupError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    ordered.insert(index, moved)
    renumber(session, ordered)
    result = _detail(session, user, project)
    session.commit()
    return result


# --- Cerrar ----------------------------------------------------------------------------


def _close(session: Session, user: User, project_id: int, target: str,
           body: ProjectClose | None) -> CloseOut:
    project = _locked_project(session, user, project_id)
    if project.status == target:
        return CloseOut(already_closed=True, tasks_closed=0, project=_brief(session, user, project))
    if project.status in ("done", "dropped"):
        raise HTTPException(
            status_code=409, detail=f"El proyecto ya está cerrado (estado: {project.status})"
        )
    mode = (body.open_tasks if body else None) or "refuse"
    open_tasks = list(
        session.scalars(
            select(Task).where(Task.project_id == project.id, Task.status.in_(_OPEN))
        )
    )
    if open_tasks and mode == "refuse":
        raise HTTPException(
            status_code=409,
            detail=f"El proyecto tiene {len(open_tasks)} tareas abiertas: "
            "envía open_tasks = 'complete' o 'trash'",
        )
    now = datetime.now(timezone.utc)
    for task in open_tasks:
        task.closed_from = task.status
        if mode == "complete":
            task.status = "done"
            task.completed_at = now
        else:
            task.status = "trashed"
    project.status = target
    project.completed_at = now
    session.flush()
    result = CloseOut(
        already_closed=False, tasks_closed=len(open_tasks), project=_brief(session, user, project)
    )
    session.commit()
    return result


@router.post("/projects/{project_id}/complete", response_model=CloseOut)
def complete_project(
    project_id: ProjectId,
    body: ProjectClose | None = None,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> CloseOut:
    """El resultado se logró. Idempotente. Si quedan tareas abiertas responde 409, salvo que
    indiques qué hacer con ellas en `open_tasks`."""
    return _close(session, user, project_id, "done", body)


@router.post("/projects/{project_id}/drop", response_model=CloseOut)
def drop_project(
    project_id: ProjectId,
    body: ProjectClose | None = None,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> CloseOut:
    """Abandonar el proyecto sin lograrlo. Idempotente; mismas reglas que `complete`.
    Si nació de una tarea, descartarlo con `open_tasks: trash` y reabrir esa tarea deshace
    la conversión."""
    return _close(session, user, project_id, "dropped", body)


# --- Convertir una tarea en proyecto ---------------------------------------------------

_CONVERTIBLE = ("inbox", "next", "waiting", "someday")


@router.post("/tasks/{task_id}/convert-to-project", response_model=ConvertOut, status_code=201)
def convert_to_project(
    task_id: TaskId,
    response: Response,
    body: ConvertIn | None = None,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ConvertOut:
    """Convierte una tarea (normalmente una captura del inbox) en un proyecto, de forma atómica.

    La tarea original queda en estado `converted` (conserva lo que escribiste) y el proyecto
    apunta a ella (`origin_task_id`). Las tareas de `tasks` son sus primeras acciones; sin
    ellas el proyecto nace `stalled`. Idempotente: si ya estaba convertida responde 200 con
    su proyecto (`already_converted: true`) y no cambia nada.
    """
    body = body or ConvertIn()
    task = _owned_task(session, user, task_id)
    tz = user_zone(user)

    if task.status == "converted":
        project = session.scalar(
            select(Project)
            .where(Project.origin_task_id == task.id, Project.user_id == user.id)
            .order_by(Project.id.desc())
            .limit(1)
        )
        if project is None:
            raise HTTPException(status_code=409, detail="La tarea está convertida pero no se encontró su proyecto")
        response.status_code = 200
        return ConvertOut(
            already_converted=True,
            origin_task=load_task(session, task.id, tz),
            project=_detail(session, user, project),
        )

    if task.status not in _CONVERTIBLE:
        reasons = {
            "reference": "Una referencia no es una acción: pásala a 'next' antes de convertirla",
            "done": "La tarea está completada: reábrela primero",
            "trashed": "La tarea está descartada: reábrela primero",
        }
        raise HTTPException(status_code=409, detail=reasons.get(task.status, "No se puede convertir"))

    title = body.title or task.title or (task.raw_text if len(task.raw_text) <= 500 else None)
    if title is None:
        raise HTTPException(
            status_code=422, detail="La captura es muy larga para ser el título: envía title"
        )
    project = Project(
        user_id=user.id,
        title=title.strip(),
        outcome=body.outcome,
        status=body.status or "active",
        sequential=True if body.sequential is None else body.sequential,
        origin_task_id=task.id,
    )
    session.add(project)
    session.flush()

    task.closed_from = task.status
    task.status = "converted"
    if task.clarified_at is None:
        task.clarified_at = datetime.now(timezone.utc)
    session.flush()

    _create_steps(session, user, project, body.tasks)
    result = ConvertOut(
        already_converted=False,
        origin_task=load_task(session, task.id, tz),
        project=_detail(session, user, project),
    )
    session.commit()
    return result
