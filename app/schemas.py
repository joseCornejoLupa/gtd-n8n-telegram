from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from app.services import normalize_context_name

# Telegram limita los mensajes a 4096 caracteres.
MAX_CAPTURE_LENGTH = 4096


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
    project_id: int | None = Field(default=None, gt=0)
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


class ClarifiedTask(BaseModel):
    id: int
    status: str
    raw_text: str
    title: str | None
    notes: str | None
    project_id: int | None
    position: int | None
    context: str | None
    waiting_on: str | None
    waiting_since: datetime | None
    due_at: datetime | None
    defer_until: datetime | None
    captured_at: datetime
    clarified_at: datetime
    available_now: bool = Field(
        description="true si ya aparece en 'siguientes acciones' (false si está diferida, "
        "bloqueada por la secuencia del proyecto, o no es una siguiente acción)"
    )
