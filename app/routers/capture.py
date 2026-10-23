from fastapi import APIRouter, Depends, Response
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_session
from app.models import Task, User
from app.schemas import CaptureIn, CaptureOut

router = APIRouter(tags=["captura"])

_COLUMNS = (Task.id, Task.status, Task.raw_text, Task.captured_at)


@router.post("/capture", response_model=CaptureOut, status_code=201)
def capture(
    body: CaptureIn,
    response: Response,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> CaptureOut:
    """Guarda lo capturado en el inbox, tal cual. Clarificar viene después.

    Idempotente por (usuario, source, source_ref): si n8n reintenta el mismo mensaje,
    responde 200 con la tarea original y `created: false` (no actualiza su texto).
    """
    row = session.execute(
        pg_insert(Task)
        .values(
            user_id=user.id,
            raw_text=body.text,
            status="inbox",
            source=body.source,
            source_ref=body.source_ref,
        )
        .on_conflict_do_nothing(index_elements=["user_id", "source", "source_ref"])
        .returning(*_COLUMNS)
    ).first()
    created = row is not None

    if row is None:  # ya existía; con source_ref NULL nunca se llega aquí
        row = session.execute(
            select(*_COLUMNS).where(
                Task.user_id == user.id,
                Task.source == body.source,
                Task.source_ref == body.source_ref,
            )
        ).one()
        response.status_code = 200

    session.commit()
    return CaptureOut(
        id=row.id,
        status=row.status,
        raw_text=row.raw_text,
        captured_at=row.captured_at,
        created=created,
    )
