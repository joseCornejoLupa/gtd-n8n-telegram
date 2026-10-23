"""Modelo de datos GTD.

Un único `tasks` con estado en lugar de tablas separadas (inbox, next, waiting...):
clarificar es un UPDATE, no mover filas entre tablas. La vista `available_tasks`
(ver la migración) calcula qué se puede hacer ahora.
"""

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Identity,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base

PROJECT_STATUSES = ("active", "someday", "done", "dropped")
TASK_STATUSES = (
    "inbox",
    "next",
    "waiting",
    "someday",
    "reference",
    "done",
    "trashed",
    "converted",
)


def _in_list(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, unique=True)
    timezone: Mapped[str] = mapped_column(Text, server_default="America/Lima")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class Context(Base):
    __tablename__ = "contexts"
    __table_args__ = (UniqueConstraint("user_id", "name"),)

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    name: Mapped[str] = mapped_column(Text)  # '@pc', '@casa', '@calle'


class Project(Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint(_in_list("status", PROJECT_STATUSES), name="status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    title: Mapped[str] = mapped_column(Text)  # el resultado deseado
    outcome: Mapped[str | None] = mapped_column(Text)  # "hecho significa..."
    status: Mapped[str] = mapped_column(Text, server_default="active")
    sequential: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    # Tarea de la que nació el proyecto (conserva la captura original).
    origin_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id", use_alter=True, name="fk_projects_origin_task_id_tasks")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(_in_list("status", TASK_STATUSES), name="status"),
        CheckConstraint("status <> 'next' OR title IS NOT NULL", name="next_has_title"),
        CheckConstraint(
            "status <> 'waiting' OR waiting_on IS NOT NULL", name="waiting_has_waiting_on"
        ),
        # Idempotencia: el mismo mensaje de Telegram no crea dos tareas.
        UniqueConstraint("user_id", "source", "source_ref"),
        # DEFERRABLE para poder intercambiar posiciones dentro de una transacción.
        UniqueConstraint(
            "project_id", "position", deferrable=True, initially="DEFERRED"
        ),
        Index(
            "tasks_inbox",
            "user_id",
            "captured_at",
            postgresql_where=text("status = 'inbox'"),
        ),
        Index(
            "tasks_next",
            "user_id",
            "context_id",
            postgresql_where=text("status = 'next'"),
        ),
        Index(
            "tasks_wait",
            "user_id",
            "waiting_since",
            postgresql_where=text("status = 'waiting'"),
        ),
        Index("tasks_proj", "project_id", "position"),
    )

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    raw_text: Mapped[str] = mapped_column(Text)  # captura original, nunca se edita
    title: Mapped[str | None] = mapped_column(Text)  # acción clara tras clarificar
    notes: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(Text, server_default="inbox")
    project_id: Mapped[int | None] = mapped_column(ForeignKey("projects.id"))
    position: Mapped[int | None] = mapped_column(Integer)  # orden dentro del proyecto
    context_id: Mapped[int | None] = mapped_column(ForeignKey("contexts.id"))
    waiting_on: Mapped[str | None] = mapped_column(Text)
    waiting_since: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # fecha dura
    defer_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))  # tickler
    source: Mapped[str] = mapped_column(Text, server_default="telegram")
    source_ref: Mapped[str | None] = mapped_column(Text)  # message_id de Telegram
    captured_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    clarified_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class Review(Base):
    __tablename__ = "reviews"

    id: Mapped[int] = mapped_column(BigInteger, Identity(always=True), primary_key=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"))
    done_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    snapshot: Mapped[dict] = mapped_column(JSONB)  # conteos al cerrar la revisión
