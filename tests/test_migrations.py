"""La migración y los modelos tienen que describir el mismo esquema."""

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.runtime.migration import MigrationContext
from sqlalchemy import text

from app.db import Base
from tests.conftest import alembic_config


def test_models_match_migrations(engine):
    with engine.connect() as conn:
        diff = compare_metadata(
            MigrationContext.configure(
                conn,
                opts={
                    "include_object": lambda obj, name, type_, reflected, compare_to: (
                        not (type_ == "table" and name == "alembic_version")
                    )
                },
            ),
            Base.metadata,
        )
    assert diff == []


def test_downgrade_and_upgrade_roundtrip(engine):
    try:
        with engine.begin() as conn:
            command.downgrade(alembic_config(conn), "base")
        with engine.connect() as conn:
            tables = conn.execute(
                text("SELECT count(*) FROM pg_tables WHERE schemaname = 'public' "
                     "AND tablename <> 'alembic_version'")
            ).scalar()
            assert tables == 0
    finally:
        with engine.begin() as conn:
            command.upgrade(alembic_config(conn), "head")
    with engine.connect() as conn:
        assert conn.execute(text("SELECT count(*) FROM available_tasks")).scalar() == 0
