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


# --- Proyectos -------------------------------------------------------------------------

ProjectStatus = Literal["active", "someday", "done", "dropped"]
StepStatus = Literal["next", "waiting", "someday"]
_STEP_FIELDS = ("notes", "context", "waiting_on", "due_at", "defer_until")


class Body(BaseModel):
    """Base de los cuerpos de entrada: rechaza campos desconocidos y trata `null` y `""` como
    "no enviado" (n8n suele mandar el mismo cuerpo con los campos vacíos)."""

    model_config = ConfigDict(extra="forbid")

    @field_validator("*", mode="before")
    @classmethod
    def blank_is_none(cls, value):
        if isinstance(value, str) and not value.strip():
            return None
        return value

    @field_validator("title", "notes", "waiting_on", "outcome", check_fields=False)
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("context", check_fields=False)
    @classmethod
    def normalize_context(cls, value: str | None) -> str | None:
        return normalize_context_name(value) if value is not None else None


class NewStep(Body):
    """Una tarea nueva dentro de un proyecto. Admite los mismos campos que /clarify según su
    estado: `context` y `defer_until` solo en next; `waiting_on` (obligatorio) solo en waiting."""

    title: str = Field(max_length=500, description="La acción, clara")
    status: StepStatus | None = Field(default=None, description="Por defecto: next")
    notes: str | None = Field(default=None, max_length=10_000)
    context: str | None = Field(default=None, description="Ej. '@pc'")
    waiting_on: str | None = Field(default=None, max_length=200)
    due_at: datetime | None = Field(default=None, description="Sin zona horaria = la del usuario")
    defer_until: datetime | None = Field(
        default=None, description="Sin zona horaria = la del usuario"
    )

    @model_validator(mode="after")
    def check_fields_for_status(self) -> "NewStep":
        status = self.status or "next"
        given = {f for f in _STEP_FIELDS if getattr(self, f) is not None}
        extra = given - ALLOWED_FIELDS[status]
        if extra:
            raise ValueError(f"Campos no permitidos para '{status}': {', '.join(sorted(extra))}")
        if status == "waiting" and self.waiting_on is None:
            raise ValueError("Para 'waiting' son obligatorios: waiting_on")
        return self


class Placement(Body):
    """Dónde va en un proyecto secuencial. Sin nada, al final."""

    after_task_id: int | None = Field(
        default=None, gt=0, le=BIGINT_MAX, description="Va justo después de esta tarea"
    )
    first: bool | None = Field(default=None, description="true = va al principio")

    @model_validator(mode="after")
    def not_both(self) -> "Placement":
        if self.first and self.after_task_id is not None:
            raise ValueError("Usa first o after_task_id, no ambos")
        return self


class StepsIn(Placement):
    tasks: list[NewStep] = Field(min_length=1, max_length=50)
    request_id: str | None = Field(
        default=None,
        max_length=100,
        description="Clave de idempotencia (ej. el id de la ejecución de n8n): si se repite, "
        "no se crean las tareas otra vez",
    )


class ReorderIn(Placement):
    task_id: int = Field(gt=0, le=BIGINT_MAX, description="La tarea que se mueve")

    @model_validator(mode="after")
    def needs_destination(self) -> "ReorderIn":
        if not self.first and self.after_task_id is None:
            raise ValueError("Indica after_task_id o first: true")
        if self.after_task_id == self.task_id:
            raise ValueError("after_task_id no puede ser la misma tarea")
        return self


class ProjectCreate(Body):
    title: str = Field(max_length=500, description="El resultado deseado")
    outcome: str | None = Field(default=None, max_length=2000, description="'Hecho' significa...")
    sequential: bool | None = Field(default=None, description="Por defecto: true")
    status: Literal["active", "someday"] | None = Field(default=None, description="Por defecto: active")
    tasks: list[NewStep] = Field(default_factory=list, max_length=50)


class ConvertIn(Body):
    """Convierte una tarea en proyecto. Sin `title`, el proyecto se llama como la tarea."""

    title: str | None = Field(default=None, max_length=500)
    outcome: str | None = Field(default=None, max_length=2000)
    sequential: bool | None = Field(default=None, description="Por defecto: true")
    status: Literal["active", "someday"] | None = None
    tasks: list[NewStep] = Field(default_factory=list, max_length=50)


class ProjectPatch(Body):
    title: str | None = Field(default=None, max_length=500)
    outcome: str | None = Field(default=None, max_length=2000)
    sequential: bool | None = None
    status: Literal["active", "someday"] | None = Field(
        default=None, description="Pausar (someday) o reactivar (active; también reabre uno cerrado)"
    )
    clear: list[Literal["outcome"]] | None = Field(default=None, description="Campos a borrar")

    @model_validator(mode="after")
    def check_changes(self) -> "ProjectPatch":
        clear = set(self.clear or [])
        if "outcome" in clear and self.outcome is not None:
            raise ValueError("No puedes enviar y borrar a la vez: outcome")
        sent = any(v is not None for v in (self.title, self.outcome, self.sequential, self.status))
        if not (sent or clear):
            raise ValueError("No se envió ningún cambio")
        return self


class ProjectClose(Body):
    open_tasks: Literal["refuse", "complete", "trash"] | None = Field(
        default=None,
        description="Qué hacer con las tareas abiertas del proyecto: refuse (por defecto) "
        "responde 409 si las hay; complete las marca hechas; trash las descarta",
    )


class ProjectOut(BaseModel):
    id: int
    title: str
    outcome: str | None
    status: str
    sequential: bool
    origin_task_id: int | None = Field(description="Tarea de la que nació el proyecto")
    created_at: datetime
    completed_at: datetime | None
    live_tasks: int = Field(description="Siguientes o en espera, no diferidas: lo que lo mantiene vivo")
    open_tasks: int = Field(description="Siguientes, en espera o algún día")
    stalled: bool = Field(description="Activo y sin ninguna tarea viva: necesita una siguiente acción")


class ProjectDetail(ProjectOut):
    next_task: TaskOut | None = Field(description="Primera tarea disponible ahora")
    tasks: list[TaskOut]


class ProjectListOut(BaseModel):
    count: int = Field(description="Total que cumple el filtro (no solo los devueltos)")
    items: list[ProjectOut]


class StepsOut(BaseModel):
    created: bool = Field(description="false si la petición ya se había procesado (request_id)")
    project: ProjectOut
    tasks: list[TaskOut]


class ConvertOut(BaseModel):
    already_converted: bool = Field(description="true si la tarea ya era proyecto (reintento)")
    origin_task: TaskOut
    project: ProjectDetail


class CloseOut(BaseModel):
    already_closed: bool
    tasks_closed: int
    project: ProjectOut


# --- Contextos -------------------------------------------------------------------------


class ContextIn(Body):
    name: str = Field(max_length=60, description="Ej. '@pc' (el @ es opcional)")

    @field_validator("name")
    @classmethod
    def valid_name(cls, value: str) -> str:
        return normalize_context_name(value)


class ContextOut(BaseModel):
    id: int
    name: str
    next_tasks: int = Field(description="Siguientes acciones con este contexto")
    available_tasks: int = Field(description="De ellas, las que se pueden hacer ahora")


class ContextWrite(ContextOut):
    created: bool


class ContextListOut(BaseModel):
    count: int
    items: list[ContextOut]


class ContextDeleted(BaseModel):
    id: int
    deleted: bool
    tasks_updated: int = Field(description="Tareas que se quedaron sin contexto")


# --- Resumen diario y revisión semanal ---------------------------------------------------


class DailyDigest(BaseModel):
    date: str = Field(description="Fecha de hoy en la zona del usuario (AAAA-MM-DD)")
    inbox_count: int = Field(description="Capturas sin clarificar")
    overdue: TaskListOut = Field(description="Siguientes o esperas con fecha límite ya pasada")
    due_today: TaskListOut = Field(description="Con fecha límite en lo que resta de hoy")
    reactivated: TaskListOut = Field(description="Estaban diferidas y hoy quedaron disponibles")
    next_actions: TaskListOut = Field(description="Lo que se puede hacer ahora (las primeras)")
    waiting_followups: WaitingListOut = Field(description="Esperas sin respuesta hace días")
    stalled_projects: ProjectListOut = Field(description="Proyectos activos sin siguiente acción")


class ReviewSnapshot(BaseModel):
    inbox: int
    next: int
    waiting: int
    someday: int
    active_projects: int
    stalled_projects: int
    overdue: int
    completed_last_7_days: int
    captured_last_7_days: int


class WeeklyReview(BaseModel):
    last_review_at: datetime | None
    days_since_last_review: int | None
    snapshot: ReviewSnapshot = Field(description="Los números de hoy; se guardan al cerrar la revisión")
    inbox: InboxOut = Field(description="Debe quedar en cero antes de revisar lo demás")
    stalled_projects: ProjectListOut
    overdue: TaskListOut
    waiting: WaitingListOut
    someday: TaskListOut


class ReviewRecord(BaseModel):
    id: int
    done_at: datetime
    snapshot: dict


class ReviewOut(ReviewRecord):
    already_recorded: bool = Field(description="true si hoy ya se había registrado una revisión")


class ReviewListOut(BaseModel):
    count: int
    items: list[ReviewRecord]
