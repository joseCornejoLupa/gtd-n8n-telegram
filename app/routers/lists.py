from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_session
from app.models import Context, Task, User, available_tasks
from app.presenters import load_page
from app.schemas import BIGINT_MAX, TaskListOut, WaitingItem, WaitingListOut
from app.services import normalize_context_name, user_zone

router = APIRouter(tags=["listas"])


def context_filter(raw: str | None) -> str | None:
    """Nombre normalizado del contexto a filtrar; vacío = sin filtro."""
    if raw is None or not raw.strip():
        return None
    try:
        return normalize_context_name(raw)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/next", response_model=TaskListOut)
def next_actions(
    context: str | None = Query(default=None, max_length=60, description="Ej. '@pc'"),
    project_id: int | None = Query(default=None, ge=1, le=BIGINT_MAX),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> TaskListOut:
    """Lo que puedes hacer ahora: siguientes acciones no diferidas, de proyectos activos y,
    en los secuenciales, solo el paso que toca. Primero las que tienen fecha límite más próxima."""
    conditions = [
        Task.user_id == user.id,
        Task.id.in_(select(available_tasks.c.id).where(available_tasks.c.user_id == user.id)),
    ]
    if (name := context_filter(context)) is not None:
        conditions.append(Context.name == name)
    if project_id is not None:
        conditions.append(Task.project_id == project_id)
    total, items = load_page(
        session, conditions,
        (Task.due_at.asc().nulls_last(), Task.captured_at, Task.id),
        limit, user_zone(user),
    )
    return TaskListOut(count=total, items=items)


@router.get("/waiting", response_model=WaitingListOut)
def waiting(
    older_than_days: int | None = Query(default=None, ge=0, le=3650),
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> WaitingListOut:
    """Lo que esperas de otras personas, de la espera más antigua a la más reciente.
    Con `older_than_days` solo las que llevan al menos esos días (para dar seguimiento)."""
    now = datetime.now(timezone.utc)
    conditions = [Task.user_id == user.id, Task.status == "waiting"]
    if older_than_days is not None:
        conditions.append(Task.waiting_since <= now - timedelta(days=older_than_days))

    def days_waiting(task: Task) -> dict:
        since = task.waiting_since
        return {"days_waiting": (now - since).days if since is not None else None}

    total, items = load_page(
        session, conditions, (Task.waiting_since.asc().nulls_last(), Task.id), limit,
        user_zone(user), model=WaitingItem, extra=days_waiting,
    )
    return WaitingListOut(count=total, items=items)


@router.get("/someday", response_model=TaskListOut)
def someday(
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> TaskListOut:
    """Algún día / tal vez, de lo más antiguo a lo más nuevo (útil en la revisión semanal)."""
    total, items = load_page(
        session, [Task.user_id == user.id, Task.status == "someday"],
        (Task.captured_at, Task.id), limit, user_zone(user),
    )
    return TaskListOut(count=total, items=items)
