from alembic import context
from sqlalchemy import create_engine, pool

from app import models  # noqa: F401  (registra las tablas en Base.metadata)
from app.config import get_settings
from app.db import Base

config = context.config
target_metadata = Base.metadata


def _engine():
    # Un URL explícito (tests) tiene prioridad; si no, el de la app con secretos montados.
    url = config.get_main_option("sqlalchemy.url")
    return create_engine(url or get_settings().database_url, poolclass=pool.NullPool)


def run_migrations_online() -> None:
    # Los tests pueden pasar una conexión ya abierta.
    connection = config.attributes.get("connection")
    if connection is not None:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()
        return

    with _engine().connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    raise RuntimeError("Las migraciones solo se ejecutan en modo online.")
run_migrations_online()
