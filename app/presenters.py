"""Construcción de las respuestas de tareas (con el nombre del contexto y del proyecto).

Las fechas se guardan como instantes (timestamptz) y se devuelven en la zona horaria del usuario.
"""

from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Context, Project, Task
from app.schemas import TaskOut

_PLAIN_FIELDS = (
    "id", "status", "raw_text", "title", "notes", "project_id", "position", "waiting_on",
    "waiting_since", "due_at", "defer_until", "captured_at", "clarified_at", "completed_at",
)

_DATETIME_FIELDS = (
    "waiting_since", "due_at", "defer_until", "captured_at", "clarified_at", "completed_at",
)

_JOINED = (
    Task.__table__
    .outerjoin(Context, Context.id == Task.context_id)
    .outerjoin(Project, Project.id == Task.project_id)
)


def task_select():
    """SELECT de tareas con el nombre de su contexto y el título de su proyecto."""
    return (
        select(Task, Context.name.label("context_name"), Project.title.label("project_title"))
        .select_from(_JOINED)
    )


def to_out(task: Task, context_name: str | None, project_title: str | None, tz: ZoneInfo,
           model: type[TaskOut] = TaskOut, **extra) -> TaskOut:
    data = {field: getattr(task, field) for field in _PLAIN_FIELDS}
    for field in _DATETIME_FIELDS:
        if data[field] is not None:
            data[field] = data[field].astimezone(tz)
    return model(**data, context=context_name, project_title=project_title, **extra)


def load_task(session: Session, task_id: int, tz: ZoneInfo,
              model: type[TaskOut] = TaskOut, **extra) -> TaskOut:
    row = session.execute(task_select().where(Task.id == task_id)).one()
    return to_out(*row, tz=tz, model=model, **extra)


def load_page(session: Session, conditions: list, order_by: tuple, limit: int, tz: ZoneInfo,
              offset: int = 0, model: type[TaskOut] = TaskOut, extra=None):
    """Devuelve (total, items). `extra(task)` puede aportar campos adicionales por ítem."""
    total = session.scalar(
        select(func.count(Task.id)).select_from(_JOINED).where(*conditions)
    )
    rows = session.execute(
        task_select().where(*conditions).order_by(*order_by).limit(limit).offset(offset)
    ).all()
    items = [
        to_out(t, ctx, proj, tz=tz, model=model, **(extra(t) if extra else {}))
        for t, ctx, proj in rows
    ]
    return total, items
