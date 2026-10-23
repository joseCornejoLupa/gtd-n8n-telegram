# gtd-n8n-telegram

Backend de **Getting Things Done (GTD)** pensado para usarse desde **n8n** y un **bot de Telegram**.
Solo API REST: el frontend es el bot, orquestado por n8n.

- FastAPI + SQLAlchemy 2 + Alembic
- PostgreSQL (se conecta a una instancia existente, no incluye una)
- Un único contenedor Docker

> Estado: en construcción. Ya disponible: estructura base, `GET /health`, esquema de base de datos
> (modelos + migración inicial) y tests del esquema.

## Principio: nada sensible en el repo

Este repositorio es público. No hay contraseñas, API keys ni IPs en el código ni en el compose:

- Los secretos se montan como **Docker secrets** desde archivos locales ignorados por git (`./secrets/`).
- El host de Postgres se resuelve con `host.docker.internal` (mapeado al host por `extra_hosts`).
- Valores no sensibles (puerto, nombre de BD, usuario) tienen defaults y se pueden cambiar con
  variables `GTD_DB_HOST`, `GTD_DB_PORT`, `GTD_DB_NAME`, `GTD_DB_USER`.

## Despliegue

### 1. Base de datos (una vez)

En el Postgres del host, desde `psql` como superusuario. `\password` pide la clave de forma
interactiva, así no queda en ningún archivo ni en el historial:

```sql
CREATE ROLE gtd LOGIN;
\password gtd
CREATE DATABASE gtd OWNER gtd;
```

Postgres debe aceptar conexiones desde la red de Docker:

- `listen_addresses` en `postgresql.conf` debe incluir la interfaz del bridge de Docker (no solo `localhost`).
- `pg_hba.conf` debe permitir al usuario `gtd` sobre la BD `gtd` desde la subred de Docker.

### 2. Secretos

```bash
mkdir -p secrets
umask 077
printf '%s' 'LA_CLAVE_DEL_ROL_GTD' > secrets/db_password.txt
openssl rand -hex 32 | tr -d '\n' > secrets/api_key.txt
```

Guarda el contenido de `secrets/api_key.txt`: es el valor que n8n enviará en el header `X-API-Key`.

### 3. Levantar

```bash
docker compose up -d --build
curl http://localhost:8088/health
```

Al arrancar, el contenedor aplica las migraciones pendientes (`alembic upgrade head`) y luego
levanta la API. Si no puede conectarse a Postgres, falla con el error en `docker compose logs gtd-api`.

El puerto publicado es el `8088`. Si n8n corre en el mismo host, restringe el acceso con el firewall
a la red que corresponda: la API key es la única barrera de autenticación.

## Desarrollo

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e '.[dev]'
uvicorn app.main:app --reload
```

## Tests

Corren contra un Postgres real (la vista `available_tasks` y los índices parciales dependen de
Postgres). El compose de pruebas levanta uno desechable, sin contraseña y solo en `127.0.0.1`:

```bash
docker compose -f docker-compose.test.yml up -d
export GTD_TEST_DATABASE_URL=postgresql+psycopg://postgres@127.0.0.1:55432/gtd_test
pytest
docker compose -f docker-compose.test.yml down
```

Los tests **borran el schema `public`** de la base indicada; por eso se niegan a correr si su
nombre no contiene `test`. No apuntes `GTD_TEST_DATABASE_URL` a tu base real.

## Modelo de datos

- `tasks` es una sola tabla con estado: `inbox`, `next`, `waiting`, `someday`, `reference`,
  `done`, `trashed`, `converted`. Clarificar es un `UPDATE`.
- `projects` puede ser `sequential`: sus tareas llevan `position` y solo la primera pendiente
  aparece como disponible.
- La vista `available_tasks` calcula lo que se puede hacer ahora (respeta secuencia,
  `defer_until` y proyectos que no están activos).
- `raw_text` guarda la captura original sin tocarla; `title` es la acción ya clarificada.

## Hoja de ruta

1. Estructura base y `/health` ✅
2. Modelos SQLAlchemy y migración inicial ✅
3. Autenticación (API key + usuario por `X-Telegram-User-Id`)
4. Captura, inbox y clarify
5. Tareas, siguientes acciones, esperas, algún día
6. Proyectos (secuenciales, reordenar, convertir tarea en proyecto, estancados)
7. Digest diario y revisión semanal
8. Tests
