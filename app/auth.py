"""Autenticación.

Dos capas, en este orden:
1. `X-API-Key`: identifica a n8n frente al backend (401 si falta o no coincide).
2. `X-Telegram-User-Id`: quién escribe en el bot. Solo se aceptan IDs de la allowlist
   (403 si no está). El usuario se crea al vuelo la primera vez, y solo si está autorizado.

La API key y la allowlist son secretos montados como archivos; no viven en el repo.
El token del bot de Telegram nunca llega a este backend: vive solo en n8n.
"""

import hmac

from fastapi import Depends, Header, HTTPException
from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.orm import Session

from app.config import get_allowed_telegram_ids, get_api_key
from app.db import get_session
from app.models import User


def require_api_key(x_api_key: str | None = Header(default=None)) -> None:
    # Comparación en tiempo constante para no filtrar la clave por timing.
    if x_api_key is None or not hmac.compare_digest(
        x_api_key.encode("utf-8"), get_api_key().encode("utf-8")
    ):
        raise HTTPException(status_code=401, detail="API key inválida o ausente")


def current_user(
    _: None = Depends(require_api_key),  # primero la API key: así nada más se revela sin ella
    x_telegram_user_id: str | None = Header(default=None),
    session: Session = Depends(get_session),
) -> User:
    if x_telegram_user_id is None:
        raise HTTPException(status_code=400, detail="Falta el header X-Telegram-User-Id")
    if not (x_telegram_user_id.isascii() and x_telegram_user_id.isdigit()):
        raise HTTPException(status_code=400, detail="X-Telegram-User-Id inválido")
    telegram_user_id = int(x_telegram_user_id)
    if telegram_user_id <= 0:
        raise HTTPException(status_code=400, detail="X-Telegram-User-Id inválido")

    if telegram_user_id not in get_allowed_telegram_ids():
        raise HTTPException(status_code=403, detail="Usuario de Telegram no autorizado")

    user = session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
    if user is None:
        session.execute(
            pg_insert(User)
            .values(telegram_user_id=telegram_user_id)
            .on_conflict_do_nothing(index_elements=["telegram_user_id"])
        )
        user = session.scalar(select(User).where(User.telegram_user_id == telegram_user_id))
    return user
