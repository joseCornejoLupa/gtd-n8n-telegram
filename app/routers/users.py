from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.auth import current_user
from app.db import get_session
from app.models import User
from app.schemas import UserSettings, UserSettingsIn

router = APIRouter(tags=["usuario"])


@router.get("/users/me", response_model=UserSettings)
def get_me(user: User = Depends(current_user)) -> UserSettings:
    return UserSettings(timezone=user.timezone)


@router.put("/users/me", response_model=UserSettings)
def update_me(
    body: UserSettingsIn,
    user: User = Depends(current_user),
    session: Session = Depends(get_session),
) -> UserSettings:
    """Define la zona horaria del usuario (nombre IANA, ej. 'America/Lima').

    Las fechas sin zona horaria que envías (`due_at`, `defer_until`) se leen en esta zona, y
    todas las fechas de las respuestas se devuelven en ella, con su desfase.
    """
    user.timezone = body.timezone
    session.commit()
    return UserSettings(timezone=user.timezone)
