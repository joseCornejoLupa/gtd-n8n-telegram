from datetime import datetime

from pydantic import BaseModel, Field, field_validator

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
