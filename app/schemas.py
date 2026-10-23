from datetime import datetime
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services import normalize_context_name

# Telegram limita los mensajes a 4096 caracteres.
MAX_CAPTURE_LENGTH = 4096

# Los IDs son bigint en Postgres: un entero mayor daría un error 500 en vez de un 404/422.
BIGINT_MAX = 2**63 - 1


class CaptureIn(BaseModel):
    text: str = Field(max_length=MAX_CAPTURE_LENGTH, description="Texto tal como lo escribió el usuario")
    source: str = Field(default="telegram", pattern=r"^[a-z0-9_-]{1,32}$")
    source_ref: str | None = Field(
        default=None,
        max_length=200,
        description="Identificador del mensaje de origen (ej. message_id de Telegram). "
        "Si se repite, no se crea otra tarea: se devuelve la existente.",
    )

    @field_validator("text")
    @classmethod
    def not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("El texto no puede estar vacío")
        return value


class CaptureOut(BaseModel):
    id: int
    status: str
    raw_text: str
    captured_at: datetime
    created: bool = Field(description="false si ya existía una captura con ese source_ref")


# --- Inbox y clarificación -------------------------------------------------------------


class InboxItem(BaseModel):
    id: int
    raw_text: str
    captured_at: datetime


class InboxOut(BaseModel):
    count: int = Field(description="Total pendiente de clarificar (no solo los devueltos)")
    items: list[InboxItem]


ClarifyAction = Literal["next", "waiting", "someday", "reference", "trash"]

# Estado resultante de cada acción.
STATUS_FOR_ACTION: dict[str, str] = {
    "next": "next",
    "waiting": "waiting",
    "someday": "someday",
    "reference": "reference",
    "trash": "trashed",
}

# Campos que admite cada acción, y los obligatorios. Rechazar lo que sobra evita errores
# silenciosos (p. ej. mandar waiting_on con 'next' y que se ignore).
ALLOWED_FIELDS: dict[str, set[str]] = {
    "next": {"title", "notes", "context", "project_id", "due_at", "defer_until"},
    "waiting": {"title", "notes", "project_id", "due_at", "waiting_on"},
    "someday": {"title", "notes", "project_id"},
    "reference": {"title", "notes"},
    "trash": set(),
}
REQUIRED_FIELDS: dict[str, set[str]] = {
    "next": {"title"},
    "waiting": {"title", "waiting_on"},
}
_CLARIFY_FIELDS = ("title", "notes", "context", "project_id", "waiting_on", "due_at", "defer_until")


class ClarifyIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    action: ClarifyAction
    title: str | None = Field(default=None, max_length=500, description="La acción, ya clara")
    notes: str | None = Field(default=None, max_length=10_000)
    context: str | None = Field(default=None, description="Nombre del contexto, ej. '@pc'")
    project_id: int | None = Field(default=None, gt=0, le=BIGINT_MAX)
    waiting_on: str | None = Field(default=None, max_length=200, description="A quién se espera")
    due_at: datetime | None = Field(
        default=None, description="Fecha dura. Sin zona horaria = la del usuario"
    )
    defer_until: datetime | None = Field(
        default=None, description="No mostrar hasta esta fecha. Sin zona horaria = la del usuario"
    )

    @field_validator("*", mode="before")
    @classmethod
    def blank_is_none(cls, value):
        # n8n suele mandar "" o null para los campos vacíos: ambos significan "no enviado".
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("title", "notes", "waiting_on")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("context")
    @classmethod
    def normalize_context(cls, value: str | None) -> str | None:
        return normalize_context_name(value) if value is not None else None

    @model_validator(mode="after")
    def check_fields_for_action(self) -> "ClarifyIn":
        given = {f for f in _CLARIFY_FIELDS if getattr(self, f) is not None}
        extra = given - ALLOWED_FIELDS[self.action]
        if extra:
            raise ValueError(
                f"Campos no permitidos para '{self.action}': {', '.join(sorted(extra))}"
            )
        missing = REQUIRED_FIELDS.get(self.action, set()) - given
        if missing:
            raise ValueError(
                f"Para '{self.action}' son obligatorios: {', '.join(sorted(missing))}"
            )
        return self


class TaskOut(BaseModel):
    id: int
    status: str
    raw_text: str
    title: str | None
    notes: str | None
    project_id: int | None
    project_title: str | None
    position: int | None
    context: str | None
    waiting_on: str | None
    waiting_since: datetime | None
    due_at: datetime | None
    defer_until: datetime | None
    captured_at: datetime
    clarified_at: datetime | None
    completed_at: datetime | None


class TaskWithAvailability(TaskOut):
    available_now: bool = Field(
        description="true si ya aparece en 'siguientes acciones' (false si está diferida, "
        "bloqueada por la secuencia del proyecto, o no es una siguiente acción)"
    )


# Se mantiene el nombre usado por /clarify.
ClarifiedTask = TaskWithAvailability


# --- Listas y edición ------------------------------------------------------------------

TaskStatus = Literal[
    "inbox", "next", "waiting", "someday", "reference", "done", "trashed", "converted"
]


class TaskListOut(BaseModel):
    count: int = Field(description="Total que cumple el filtro (no solo los devueltos)")
    items: list[TaskOut]


class WaitingItem(TaskOut):
    days_waiting: int | None = Field(description="Días enteros esperando respuesta")


class WaitingListOut(BaseModel):
    count: int
    items: list[WaitingItem]


# Estados entre los que se puede mover una tarea abierta con PATCH. Completar, descartar y
# reabrir tienen sus propios endpoints; salir del inbox es /clarify.
PatchStatus = Literal["next", "waiting", "someday", "reference"]
ClearableField = Literal["notes", "context", "project_id", "waiting_on", "due_at", "defer_until"]
_PATCH_FIELDS = ("title", "notes", "context", "project_id", "waiting_on", "due_at", "defer_until")


class TaskPatch(BaseModel):
    """Cambios parciales. Solo se aplica lo que se envía; `null` y `""` cuentan como no enviado
    (así n8n puede mandar el mismo cuerpo con campos vacíos). Para borrar un valor, nómbralo
    en `clear`."""

    model_config = ConfigDict(extra="forbid")

    status: PatchStatus | None = None
    title: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None, max_length=10_000)
    context: str | None = None
    project_id: int | None = Field(default=None, gt=0, le=BIGINT_MAX)
    waiting_on: str | None = Field(default=None, max_length=200)
    due_at: datetime | None = None
    defer_until: datetime | None = None
    clear: list[ClearableField] | None = Field(
        default=None, description="Campos a borrar (dejarlos vacíos)"
    )

    @field_validator("*", mode="before")
    @classmethod
    def blank_is_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("title", "notes", "waiting_on")
    @classmethod
    def strip_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else None

    @field_validator("context")
    @classmethod
    def normalize_context(cls, value: str | None) -> str | None:
        return normalize_context_name(value) if value is not None else None

    @property
    def sent_fields(self) -> set[str]:
        return {f for f in _PATCH_FIELDS if getattr(self, f) is not None}

    @model_validator(mode="after")
    def check_clear(self) -> "TaskPatch":
        clear = set(self.clear or [])
        both = clear & self.sent_fields
        if both:
            raise ValueError(f"No puedes enviar y borrar a la vez: {', '.join(sorted(both))}")
        if not (self.sent_fields or clear or self.status):
            raise ValueError("No se envió ningún cambio")
        return self


class ProjectBrief(BaseModel):
    id: int
    title: str
    sequential: bool
    status: str


class CompleteOut(BaseModel):
    task: TaskOut
    already_completed: bool = Field(description="true si ya estaba completada (reintento)")
    project: ProjectBrief | None
    project_stalled: bool = Field(
        description="true si el proyecto sigue activo pero ya no tiene siguiente acción ni espera"
    )
    project_open_tasks: int = Field(
        description="Tareas del proyecto aún abiertas (siguiente, espera o algún día). "
        "Si project_stalled y esto es 0, probablemente el proyecto terminó."
    )
    next_task: TaskOut | None = Field(
        description="En un proyecto secuencial, el paso que acaba de quedar disponible"
    )


# --- Usuario ---------------------------------------------------------------------------


class UserSettings(BaseModel):
    timezone: str = Field(description="Zona horaria IANA, ej. 'America/Lima'")


class UserSettingsIn(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timezone: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_+\-/]+$")

    @field_validator("timezone")
    @classmethod
    def must_exist(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError("Zona horaria desconocida (usa un nombre IANA, ej. 'America/Lima')") from exc
        return value
