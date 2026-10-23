"""Posponer tareas: ocultar una siguiente acción hasta una fecha (`defer_until`)."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_session
from app.models import Project, Task, User
from app.presenters import load_page
from app.routers.tasks import TaskId, _owned_task, _with_availability
from app.schemas import (
    SNOOZE_MAX_DAYS,
    ProjectBrief,
    SnoozeIn,
    SnoozeOut,
    TaskListOut,
    TaskWithAvailability,
)
from app.services import day_bounds, localize, project_counts, user_zone

router = APIRouter(tags=["snooze"])

_WHY_NOT = {
    "inbox": "La tarea sigue en el inbox: clarifícala primero",
    "waiting": "Una espera no se pospone: ya está fuera de tus siguientes acciones",
    "someday": "Una tarea de 'algún día' ya está oculta",
    "reference": "Una referencia no es una acción",
    "done": "La tarea está completada",
    "trashed": "La tarea está descartada",
    "converted": "La tarea ya se convirtió en proyecto",
}


def _snoozable(session: Session, user: User, task_id: int) -> Task:
    task = _owned_task(session, user, task_id)
    if task.status != "next":
        raise HTTPException(status_code=409, detail=_WHY_NOT[task.status])
    return task


def _wakeup(body: SnoozeIn, user: User, now: datetime) -> datetime:
    tz = user_zone(user)
    if body.until is not None:
        wakeup = localize(body.until, user.timezone)
    elif body.days is not None:
        start, _ = day_bounds(now, tz)
        wakeup = datetime.combine(start.date() + timedelta(days=body.days), start.time(), tzinfo=tz)
    else:
        wakeup = now + timedelta(hours=body.hours)
    if wakeup <= now:
        raise HTTPException(status_code=422, detail="La fecha para posponer debe estar en el futuro")
    if wakeup > now + timedelta(days=SNOOZE_MAX_DAYS):
        raise HTTPException(status_code=422, detail="No se puede posponer a más de 5 años")
    return wakeup


@router.post("/tasks/{task_id}/snooze", response_model=SnoozeOut)
def snooze(
    task_id: TaskId,
    body: SnoozeIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> SnoozeOut:
    """Oculta una siguiente acción hasta que llegue la fecha; entonces reaparece sola en `/next`.

    Indica `until` (fecha exacta; sin zona horaria = la tuya), `days` (reaparece al comenzar ese
    día, 00:00 en tu zona) u `hours`. Posponer otra vez reemplaza la fecha anterior. Solo las
    siguientes acciones (`next`) se posponen. Cuidado: en un proyecto secuencial, posponer el paso
    que toca deja al proyecto sin nada disponible (`project_blocked`), y mientras tanto cuenta como
    estancado (`project_stalled`).
    """
    task = _snoozable(session, user, task_id)
    now = datetime.now(timezone.utc)
    task.defer_until = _wakeup(body, user, now)
    session.flush()

    project = blocked = stalled = None
    if task.project_id is not None:
        project = session.get(Project, task.project_id)
        live, _ = project_counts(session, project.id)
        stalled = project.status == "active" and live == 0
        head = session.scalar(
            select(func.min(Task.position)).where(
                Task.project_id == project.id, Task.status.in_(("next", "waiting"))
            )
        )
        blocked = project.status == "active" and project.sequential and head == task.position
    result = SnoozeOut(
        task=_with_availability(session, user, task),
        project=ProjectBrief(
            id=project.id, title=project.title, sequential=project.sequential, status=project.status
        ) if project is not None else None,
        project_stalled=bool(stalled),
        project_blocked=bool(blocked),
        due_before_wakeup=task.due_at is not None and task.due_at < task.defer_until,
    )
    session.commit()
    return result


@router.delete("/tasks/{task_id}/snooze", response_model=TaskWithAvailability)
def wake_up(
    task_id: TaskId,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> TaskWithAvailability:
    """Quita el snooze: la tarea vuelve a estar disponible ya. Idempotente."""
    task = _snoozable(session, user, task_id)
    task.defer_until = None
    session.flush()
    result = _with_availability(session, user, task)
    session.commit()
    return result


@router.get("/snoozed", response_model=TaskListOut)
def snoozed(
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> TaskListOut:
    """Lo pospuesto que aún no reaparece, de la que vuelve antes a la que vuelve después."""
    total, items = load_page(
        session,
        [Task.user_id == user.id, Task.status == "next", Task.defer_until > func.now()],
        (Task.defer_until.asc(), Task.id), limit, user_zone(user),
    )
    return TaskListOut(count=total, items=items)
