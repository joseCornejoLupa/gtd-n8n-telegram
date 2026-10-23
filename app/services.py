"""Lógica compartida por los endpoints."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.models import Context, Project, Task

_CONTEXT_RE = re.compile(r"^@[\w-]{1,40}$")


def normalize_context_name(raw: str) -> str:
    """'PC' -> '@pc'. Minúsculas y '@' obligatorio, para que '@PC' y '@pc' no sean dos contextos."""
    name = raw.strip().lower()
    if not name.startswith("@"):
        name = "@" + name
    if not _CONTEXT_RE.fullmatch(name):
        raise ValueError(
            "Contexto inválido: usa letras, números o guiones (hasta 40 caracteres), sin espacios"
        )
    return name


def get_or_create_context(session: Session, user_id: int, name: str) -> Context:
    context = session.scalar(
        select(Context).where(Context.user_id == user_id, Context.name == name)
    )
    if context is None:
        session.execute(
            pg_insert(Context)
            .values(user_id=user_id, name=name)
            .on_conflict_do_nothing(index_elements=["user_id", "name"])
        )
        context = session.scalar(
            select(Context).where(Context.user_id == user_id, Context.name == name)
        )
    return context


def localize(value: datetime | None, timezone_name: str) -> datetime | None:
    """Una fecha sin zona horaria se interpreta en la zona del usuario; con zona, se respeta."""
    if value is None or value.tzinfo is not None:
        return value
    return value.replace(tzinfo=ZoneInfo(timezone_name))


def append_to_project(session: Session, task: Task, project: Project) -> None:
    """Asigna la tarea al proyecto. En un proyecto secuencial va al final de la fila.

    El llamador debe tener bloqueada la fila del proyecto (SELECT ... FOR UPDATE) para que
    dos peticiones simultáneas no calculen la misma posición.
    """
    task.project_id = project.id
    if project.sequential:
        last = session.scalar(
            select(func.max(Task.position)).where(Task.project_id == project.id)
        )
        task.position = (last or 0) + 1
