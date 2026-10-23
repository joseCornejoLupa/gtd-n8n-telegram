from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_session
from app.models import Project, Task, User
from app.schemas import STATUS_FOR_ACTION, ClarifiedTask, ClarifyIn
from app.services import append_to_project, get_or_create_context, localize

router = APIRouter(tags=["tareas"])


@router.post("/tasks/{task_id}/clarify", response_model=ClarifiedTask)
def clarify(
    task_id: int,
    body: ClarifyIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ClarifiedTask:
    """Convierte una captura del inbox en lo que realmente es.

    `action`: next (siguiente acción), waiting (a la espera de alguien), someday (algún día),
    reference (solo información) o trash (descartar). Cada acción admite solo algunos campos;
    ver el esquema. `raw_text` (lo capturado) nunca se modifica.
    """
    # FOR UPDATE: si n8n reintenta en paralelo, la segunda llamada espera y recibe 409.
    task = session.scalar(
        select(Task).where(Task.id == task_id, Task.user_id == user.id).with_for_update()
    )
    if task is None:
        raise HTTPException(status_code=404, detail="Tarea no encontrada")
    if task.status != "inbox":
        raise HTTPException(
            status_code=409, detail=f"La tarea ya no está en el inbox (estado: {task.status})"
        )

    project = None
    if body.project_id is not None:
        project = session.scalar(
            select(Project)
            .where(Project.id == body.project_id, Project.user_id == user.id)
            .with_for_update()
        )
        if project is None:
            raise HTTPException(status_code=404, detail="Proyecto no encontrado")
        if project.status not in ("active", "someday"):
            raise HTTPException(
                status_code=409, detail=f"El proyecto está cerrado (estado: {project.status})"
            )

    now = datetime.now(timezone.utc)
    task.status = STATUS_FOR_ACTION[body.action]
    task.title = body.title
    task.notes = body.notes
    task.waiting_on = body.waiting_on
    if body.action == "waiting":
        task.waiting_since = now
    task.due_at = localize(body.due_at, user.timezone)
    task.defer_until = localize(body.defer_until, user.timezone)

    context_name = None
    if body.context is not None:
        context = get_or_create_context(session, user.id, body.context)
        task.context_id = context.id
        context_name = context.name
    if project is not None:
        append_to_project(session, task, project)
    task.clarified_at = now
    session.flush()

    available_now = (
        session.execute(
            text("SELECT 1 FROM available_tasks WHERE id = :id"), {"id": task.id}
        ).first()
        is not None
    )
    session.commit()

    return ClarifiedTask(
        id=task.id,
        status=task.status,
        raw_text=task.raw_text,
        title=task.title,
        notes=task.notes,
        project_id=task.project_id,
        position=task.position,
        context=context_name,
        waiting_on=task.waiting_on,
        waiting_since=task.waiting_since,
        due_at=task.due_at,
        defer_until=task.defer_until,
        captured_at=task.captured_at,
        clarified_at=task.clarified_at,
        available_now=available_now,
    )
