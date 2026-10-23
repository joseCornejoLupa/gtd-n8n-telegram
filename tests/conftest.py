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
    eng = create_engine(url)
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
        p = Project(user_id=user.id, title=title, **kwargs)
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
