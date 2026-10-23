"""Lógica compartida por los endpoints."""

import re
from datetime import datetime
from zoneinfo import ZoneInfo

from sqlalchemy import func, select, text
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


def user_zone(user) -> ZoneInfo:
    """Zona horaria del usuario: en ella se leen las fechas sin zona y se escriben las respuestas."""
    return ZoneInfo(user.timezone)


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
    else:
        # Un proyecto paralelo no usa posiciones; la de un proyecto anterior no debe arrastrarse.
        task.position = None


def is_available(session: Session, task_id: int) -> bool:
    """¿Aparece ya en 'siguientes acciones'? Consulta la vista available_tasks."""
    return (
        session.execute(
            text("SELECT 1 FROM available_tasks WHERE id = :id"), {"id": task_id}
        ).first()
        is not None
    )


def project_counts(session: Session, project_id: int) -> tuple[int, int]:
    """(tareas vivas, tareas abiertas) de un proyecto.

    Viva = siguiente o en espera y no diferida a futuro: es lo que mantiene el proyecto
    avanzando. Abierta = viva, diferida o de 'algún día'. Un proyecto activo sin tareas
    vivas está estancado.
    """
    row = session.execute(
        text("""
        SELECT
          count(*) FILTER (WHERE status IN ('next','waiting')
                           AND (defer_until IS NULL OR defer_until <= now())) AS live,
          count(*) FILTER (WHERE status IN ('next','waiting','someday')) AS open
        FROM tasks WHERE project_id = :pid"""),
        {"pid": project_id},
    ).one()
    return row.live, row.open
