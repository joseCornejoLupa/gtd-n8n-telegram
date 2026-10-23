from fastapi import APIRouter, Depends, Query
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_session
from app.models import Task, User
from app.schemas import InboxItem, InboxOut
from app.services import user_zone

router = APIRouter(tags=["inbox"])


@router.get("/inbox", response_model=InboxOut)
def inbox(
    limit: int = Query(default=50, ge=1, le=200),
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> InboxOut:
    """Lo capturado y aún sin clarificar, del más antiguo al más nuevo."""
    pending = (Task.user_id == user.id) & (Task.status == "inbox")
    count = session.scalar(select(func.count()).select_from(Task).where(pending))
    rows = session.execute(
        select(Task.id, Task.raw_text, Task.captured_at)
        .where(pending)
        .order_by(Task.captured_at, Task.id)
        .limit(limit)
    ).all()
    zone = user_zone(user)
    return InboxOut(
        count=count,
        items=[
            InboxItem(id=r.id, raw_text=r.raw_text, captured_at=r.captured_at.astimezone(zone))
            for r in rows
        ],
    )
