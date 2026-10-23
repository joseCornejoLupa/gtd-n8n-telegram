"""Resumen diario y revisión semanal.

El resumen diario es lo que n8n envía cada mañana por Telegram. La revisión semanal es el hábito
que mantiene el sistema confiable: vaciar el inbox, mirar proyectos, esperas y "algún día".
"""

from datetime import datetime, timedelta, timezone
from typing import Literal
from zoneinfo import ZoneInfo

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import PlainTextResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_session
from app.digest_text import daily_text, review_text
from app.models import Project, Review, Task, User, available_tasks
from app.presenters import load_page, project_out
from app.schemas import (
    DailyDigest,
    InboxItem,
    InboxOut,
    ProjectListOut,
    ReviewListOut,
    ReviewOut,
    ReviewRecord,
    ReviewSnapshot,
    TaskListOut,
    WaitingItem,
    WaitingListOut,
    WeeklyReview,
)
from app.services import day_bounds, project_stats, stalled_conditions, user_zone

router = APIRouter(tags=["resumen y revisión"])

Format = Literal["json", "text"]
_ACTIVE = ("next", "waiting")


def _count(session: Session, *conditions) -> int:
    return session.scalar(select(func.count(Task.id)).where(*conditions)) or 0


def _waiting(session: Session, user: User, now: datetime, tz: ZoneInfo, older_than_days: int,
             limit: int) -> WaitingListOut:
    def days_waiting(task: Task) -> dict:
        since = task.waiting_since
        return {"days_waiting": (now - since).days if since is not None else None}

    total, items = load_page(
        session,
        [Task.user_id == user.id, Task.status == "waiting",
         Task.waiting_since <= now - timedelta(days=older_than_days)],
        (Task.waiting_since.asc(), Task.id), limit, tz, model=WaitingItem, extra=days_waiting,
    )
    return WaitingListOut(count=total, items=items)


def _stalled(session: Session, user: User, tz: ZoneInfo, limit: int) -> ProjectListOut:
    conditions = stalled_conditions(user.id)
    total = session.scalar(select(func.count(Project.id)).where(*conditions)) or 0
    rows = session.scalars(select(Project).where(*conditions).order_by(Project.id).limit(limit)).all()
    stats = project_stats(session, [p.id for p in rows])
    return ProjectListOut(
        count=total, items=[project_out(p, stats.get(p.id, (0, 0)), tz) for p in rows]
    )


def _overdue(session: Session, user: User, now: datetime, tz: ZoneInfo, limit: int) -> TaskListOut:
    total, items = load_page(
        session,
        [Task.user_id == user.id, Task.status.in_(_ACTIVE), Task.due_at < now],
        (Task.due_at.asc(), Task.id), limit, tz,
    )
    return TaskListOut(count=total, items=items)


# --- Resumen diario --------------------------------------------------------------------


@router.get("/digest/daily", response_model=DailyDigest)
def daily_digest(
    format: Format = Query(default="json", description="`text` devuelve texto plano listo para Telegram"),
    limit: int = Query(default=5, ge=1, le=20, description="Máximo de ítems por sección"),
    waiting_days: int = Query(default=7, ge=0, le=365, description="Días sin respuesta para el seguimiento"),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Qué mirar hoy: vencidas, para hoy, lo que volvió a estar disponible, las siguientes
    acciones, esperas sin respuesta y proyectos estancados. Las secciones vacías traen
    `count: 0`; en el texto simplemente no aparecen. "Hoy" es el día en la zona del usuario."""
    tz, now = user_zone(user), datetime.now(timezone.utc)
    start, end = day_bounds(now, tz)
    mine = Task.user_id == user.id

    def tasks(conditions, order) -> TaskListOut:
        total, items = load_page(session, [mine, *conditions], order, limit, tz)
        return TaskListOut(count=total, items=items)

    available = Task.id.in_(select(available_tasks.c.id).where(available_tasks.c.user_id == user.id))
    digest = DailyDigest(
        date=now.astimezone(tz).date().isoformat(),
        inbox_count=_count(session, mine, Task.status == "inbox"),
        overdue=_overdue(session, user, now, tz, limit),
        due_today=tasks(
            [Task.status.in_(_ACTIVE), Task.due_at >= now, Task.due_at < end],
            (Task.due_at.asc(), Task.id),
        ),
        reactivated=tasks(
            [Task.status == "next", Task.defer_until >= start, Task.defer_until <= now, available],
            (Task.defer_until.asc(), Task.id),
        ),
        next_actions=tasks(
            [available], (Task.due_at.asc().nulls_last(), Task.captured_at, Task.id)
        ),
        waiting_followups=_waiting(session, user, now, tz, waiting_days, limit),
        stalled_projects=_stalled(session, user, tz, limit),
    )
    if format == "text":
        return PlainTextResponse(daily_text(digest, now, tz))
    return digest


# --- Revisión semanal ------------------------------------------------------------------


def snapshot(session: Session, user: User, now: datetime) -> ReviewSnapshot:
    mine = Task.user_id == user.id
    week_ago = now - timedelta(days=7)
    return ReviewSnapshot(
        inbox=_count(session, mine, Task.status == "inbox"),
        next=_count(session, mine, Task.status == "next"),
        waiting=_count(session, mine, Task.status == "waiting"),
        someday=_count(session, mine, Task.status == "someday"),
        active_projects=session.scalar(
            select(func.count(Project.id)).where(Project.user_id == user.id, Project.status == "active")
        ) or 0,
        stalled_projects=session.scalar(
            select(func.count(Project.id)).where(*stalled_conditions(user.id))
        ) or 0,
        overdue=_count(session, mine, Task.status.in_(_ACTIVE), Task.due_at < now),
        completed_last_7_days=_count(session, mine, Task.status == "done", Task.completed_at >= week_ago),
        captured_last_7_days=_count(session, mine, Task.captured_at >= week_ago),
    )


def _last_review(session: Session, user: User) -> Review | None:
    return session.scalar(
        select(Review).where(Review.user_id == user.id).order_by(Review.done_at.desc(), Review.id.desc()).limit(1)
    )


@router.get("/review/weekly", response_model=WeeklyReview)
def weekly_review(
    format: Format = Query(default="json", description="`text` devuelve un resumen en texto plano"),
    limit: int = Query(default=20, ge=1, le=100, description="Máximo de ítems por sección"),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
):
    """Material para la revisión: inbox por vaciar, proyectos sin siguiente acción, vencidas,
    todas las esperas y la lista de "algún día", con los números de la semana. No cambia nada;
    al terminar, `POST /review/weekly/complete` la registra."""
    tz, now = user_zone(user), datetime.now(timezone.utc)
    last = _last_review(session, user)
    inbox_rows = session.scalars(
        select(Task).where(Task.user_id == user.id, Task.status == "inbox")
        .order_by(Task.captured_at, Task.id).limit(limit)
    ).all()
    inbox_total = _count(session, Task.user_id == user.id, Task.status == "inbox")
    _, someday_items = load_page(
        session, [Task.user_id == user.id, Task.status == "someday"],
        (Task.captured_at, Task.id), limit, tz,
    )
    someday_total = _count(session, Task.user_id == user.id, Task.status == "someday")
    review = WeeklyReview(
        last_review_at=last.done_at.astimezone(tz) if last else None,
        days_since_last_review=(
            (now.astimezone(tz).date() - last.done_at.astimezone(tz).date()).days if last else None
        ),
        snapshot=snapshot(session, user, now),
        inbox=InboxOut(
            count=inbox_total,
            items=[InboxItem(id=t.id, raw_text=t.raw_text, captured_at=t.captured_at.astimezone(tz))
                   for t in inbox_rows],
        ),
        stalled_projects=_stalled(session, user, tz, limit),
        overdue=_overdue(session, user, now, tz, limit),
        waiting=_waiting(session, user, now, tz, 0, limit),
        someday=TaskListOut(count=someday_total, items=someday_items),
    )
    if format == "text":
        return PlainTextResponse(review_text(review, now, tz))
    return review


def _record(review: Review, tz: ZoneInfo, model=ReviewRecord, **extra):
    return model(id=review.id, done_at=review.done_at.astimezone(tz), snapshot=review.snapshot, **extra)


@router.post("/review/weekly/complete", response_model=ReviewOut)
def complete_weekly_review(
    response: Response,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ReviewOut:
    """Registra que terminaste la revisión, con los números de ese momento.

    Idempotente por día (en tu zona horaria): si hoy ya se registró una, responde `200` con esa
    y `already_recorded: true`; si no, `201`. Así un reintento de n8n no duplica el registro.
    """
    tz, now = user_zone(user), datetime.now(timezone.utc)
    # Se bloquea al usuario: dos peticiones simultáneas no pueden registrar dos revisiones el mismo día.
    session.scalar(select(User.id).where(User.id == user.id).with_for_update())
    start, _ = day_bounds(now, tz)
    existing = session.scalar(
        select(Review).where(Review.user_id == user.id, Review.done_at >= start)
        .order_by(Review.done_at.desc(), Review.id.desc()).limit(1)
    )
    if existing is not None:
        response.status_code = 200
        return _record(existing, tz, ReviewOut, already_recorded=True)
    review = Review(user_id=user.id, snapshot=snapshot(session, user, now).model_dump())
    session.add(review)
    session.flush()
    session.refresh(review)
    result = _record(review, tz, ReviewOut, already_recorded=False)
    session.commit()
    response.status_code = 201
    return result


@router.get("/reviews", response_model=ReviewListOut)
def list_reviews(
    limit: int = Query(default=10, ge=1, le=100),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> ReviewListOut:
    """Historial de revisiones semanales, de la más reciente a la más antigua."""
    tz = user_zone(user)
    total = session.scalar(select(func.count(Review.id)).where(Review.user_id == user.id)) or 0
    rows = session.scalars(
        select(Review).where(Review.user_id == user.id)
        .order_by(Review.done_at.desc(), Review.id.desc()).limit(limit)
    ).all()
    return ReviewListOut(count=total, items=[_record(r, tz) for r in rows])
