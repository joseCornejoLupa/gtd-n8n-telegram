"""Configuración.

Regla del proyecto: el repositorio es público, así que no contiene contraseñas,
API keys, tokens ni IDs de usuario. Todo lo sensible se lee de archivos (Docker
secrets) montados en tiempo de ejecución; los valores no sensibles tienen defaults
seguros y se pueden sobreescribir con variables de entorno prefijadas con GTD_.

Los secretos se leen una sola vez (al arrancar) y se cachean: para rotarlos hay que
reiniciar el contenedor.
"""

import re
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


def read_secret(path: Path) -> str:
    try:
        value = path.read_text(encoding="utf-8").strip()
    except FileNotFoundError as exc:
        raise RuntimeError(f"Falta el secreto: {path}") from exc
    if not value:
        raise RuntimeError(f"El secreto está vacío: {path}")
    return value


def parse_telegram_ids(raw: str, source: str = "allowlist") -> frozenset[int]:
    """IDs separados por comas, espacios o saltos de línea. Falla si algo no es un entero
    positivo: una allowlist mal escrita nunca debe interpretarse a medias."""
    tokens = [t for t in re.split(r"[,\s]+", raw) if t]
    if not tokens:
        raise RuntimeError(f"La {source} no contiene ningún ID de Telegram")
    ids: set[int] = set()
    for token in tokens:
        if not token.isascii() or not token.isdigit() or int(token) <= 0:
            raise RuntimeError(f"La {source} contiene un valor que no es un ID válido")
        ids.add(int(token))
    return frozenset(ids)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_prefix="GTD_", extra="ignore")

    # Postgres (no sensible). host.docker.internal lo resuelve compose hacia el host.
    db_host: str = "host.docker.internal"
    db_port: int = 5432
    db_name: str = "gtd"
    db_user: str = "gtd"

    # Rutas de los secretos montados por Docker.
    db_password_file: Path = Path("/run/secrets/gtd_db_password")
    api_key_file: Path = Path("/run/secrets/gtd_api_key")
    allowed_telegram_ids_file: Path = Path("/run/secrets/gtd_allowed_telegram_ids")

    @property
    def database_url(self) -> URL:
        # URL.create escapa correctamente caracteres especiales del password.
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=read_secret(self.db_password_file),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()


@lru_cache
def get_api_key() -> str:
    """Clave que n8n envía en el header X-API-Key."""
    return read_secret(get_settings().api_key_file)


@lru_cache
def get_allowed_telegram_ids() -> frozenset[int]:
    """Usuarios de Telegram autorizados. Sin esta lista el servicio no arranca (fail closed)."""
    path = get_settings().allowed_telegram_ids_file
    return parse_telegram_ids(read_secret(path), source=f"allowlist ({path.name})")


def reset_caches() -> None:
    """Para tests: fuerza a releer configuración y secretos."""
    get_settings.cache_clear()
    get_api_key.cache_clear()
    get_allowed_telegram_ids.cache_clear()
