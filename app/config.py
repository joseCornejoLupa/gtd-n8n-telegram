"""Configuración.

Regla del proyecto: el repositorio es público, así que no contiene contraseñas,
API keys ni IPs. Los secretos se leen de archivos (Docker secrets) montados en
tiempo de ejecución; los valores no sensibles tienen defaults seguros y se pueden
sobreescribir con variables de entorno prefijadas con GTD_.
"""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict
from sqlalchemy.engine import URL


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

    def _read_secret(self, path: Path) -> str:
        try:
            value = path.read_text(encoding="utf-8").strip()
        except FileNotFoundError as exc:
            raise RuntimeError(f"Falta el secreto: {path}") from exc
        if not value:
            raise RuntimeError(f"El secreto está vacío: {path}")
        return value

    @property
    def api_key(self) -> str:
        return self._read_secret(self.api_key_file)

    @property
    def database_url(self) -> URL:
        # URL.create escapa correctamente caracteres especiales del password.
        return URL.create(
            "postgresql+psycopg",
            username=self.db_user,
            password=self._read_secret(self.db_password_file),
            host=self.db_host,
            port=self.db_port,
            database=self.db_name,
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
