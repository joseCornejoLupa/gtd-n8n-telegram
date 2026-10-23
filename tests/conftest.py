"""Tests contra un Postgres real (la vista, los DEFERRABLE y los índices parciales
dependen de Postgres; SQLite no sirve).

Uso:
    docker compose -f docker-compose.test.yml up -d
    export GTD_TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/gtd_test
    pytest

SEGURIDAD: la fixture BORRA el schema `public` de la base indicada. Por eso se
niega a correr si el nombre de la base no contiene "test".
"""

import os
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from app.db import CONNECT_ARGS
from app.models import Context, Project, Task, User

ROOT = Path(__file__).resolve().parent.parent


def alembic_config(connection) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "alembic"))
    cfg.attributes["connection"] = connection
    return cfg


@pytest.fixture(scope="session")
def engine():
    url = os.environ.get("GTD_TEST_DATABASE_URL")
    if not url:
        pytest.skip("Define GTD_TEST_DATABASE_URL para correr los tests de base de datos")
    eng = create_engine(url, connect_args=CONNECT_ARGS)  # igual que en producción
    database = eng.url.database or ""
    if "test" not in database:
        pytest.exit(
            f"Rechazo correr: la base '{database}' no parece de pruebas "
            "(el nombre debe contener 'test'). Los tests borran el schema public.",
            returncode=2,
        )
    with eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))
        command.upgrade(alembic_config(conn), "head")
    yield eng
    eng.dispose()


@pytest.fixture
def session(engine):
    """Sesión dentro de una transacción externa que se revierte al terminar el test."""
    connection = engine.connect()
    transaction = connection.begin()
    s = Session(bind=connection, join_transaction_mode="create_savepoint")
    yield s
    s.close()
    transaction.rollback()
    connection.close()


@pytest.fixture
def user(session) -> User:
    u = User(telegram_user_id=1001)
    session.add(u)
    session.flush()
    return u


@pytest.fixture
def make_task(session, user):
    def _make(status="next", title="acción", **kwargs) -> Task:
        if status in ("inbox",) and "title" not in kwargs:
            title = None
        kwargs.setdefault("raw_text", title or "captura")
        kwargs.setdefault("user_id", user.id)
        t = Task(status=status, title=title, **kwargs)
        session.add(t)
        session.flush()
        return t

    return _make


@pytest.fixture
def make_project(session, user):
    def _make(title="proyecto", **kwargs) -> Project:
        kwargs.setdefault("user_id", user.id)
        p = Project(title=title, **kwargs)
        session.add(p)
        session.flush()
        return p

    return _make


@pytest.fixture
def make_context(session, user):
    def _make(name="@pc") -> Context:
        c = Context(user_id=user.id, name=name)
        session.add(c)
        session.flush()
        return c

    return _make


@pytest.fixture
def available(session, user):
    """IDs de lo que muestra la vista available_tasks para el usuario, ordenados."""

    def _available() -> list[int]:
        rows = session.execute(
            text("SELECT id FROM available_tasks WHERE user_id = :u ORDER BY id"),
            {"u": user.id},
        )
        return [r[0] for r in rows]

    return _available


# --- API -------------------------------------------------------------------------------

API_KEY = "clave-de-prueba-no-es-real"
ALLOWED_IDS = (1001, 2002)


@pytest.fixture
def secrets(tmp_path, monkeypatch):
    """Secretos falsos en archivos temporales, como los monta Docker."""
    from app.config import reset_caches

    key = tmp_path / "api_key"
    ids = tmp_path / "allowed_ids"
    key.write_text(API_KEY + "\n")
    ids.write_text("\n".join(str(i) for i in ALLOWED_IDS) + "\n")
    monkeypatch.setenv("GTD_API_KEY_FILE", str(key))
    monkeypatch.setenv("GTD_ALLOWED_TELEGRAM_IDS_FILE", str(ids))
    reset_caches()
    yield {"api_key": key, "allowed_ids": ids}
    reset_caches()


@pytest.fixture
def client(secrets, session):
    """Cliente HTTP cuyo acceso a la BD es la sesión transaccional del test."""
    from fastapi.testclient import TestClient

    from app.db import get_session
    from app.main import app

    app.dependency_overrides[get_session] = lambda: session
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


@pytest.fixture
def auth():
    """Headers válidos para el usuario autorizado 1001."""
    return {"X-API-Key": API_KEY, "X-Telegram-User-Id": "1001"}
