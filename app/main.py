from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

from app.config import get_allowed_telegram_ids, get_api_key
from app.routers import capture, contexts, inbox, lists, projects, reviews, tasks, users


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    # Fail closed: sin API key o sin allowlist válidas el servicio no arranca,
    # en lugar de arrancar abierto. El error sale en `docker compose logs`.
    get_api_key()
    get_allowed_telegram_ids()
    yield


app = FastAPI(
    title="GTD backend",
    description="Backend GTD para n8n + bot de Telegram",
    version="0.7.0",
    lifespan=lifespan,
)

app.include_router(capture.router)
app.include_router(inbox.router)
app.include_router(lists.router)
app.include_router(tasks.router)
app.include_router(projects.router)
app.include_router(contexts.router)
app.include_router(reviews.router)
app.include_router(users.router)


@app.exception_handler(DBAPIError)
async def concurrency_conflict(_: Request, exc: DBAPIError) -> JSONResponse:
    """Dos peticiones simultáneas pueden chocar en la base de datos (interbloqueo, o una
    referencia que otra petición acaba de borrar). Postgres ya revirtió la transacción de
    una de ellas: se responde un error claro para que n8n reintente, en lugar de un 500."""
    sqlstate = getattr(exc.orig, "sqlstate", None)
    if sqlstate in ("40P01", "40001"):
        return JSONResponse(
            {"detail": "Conflicto de concurrencia: reintenta la petición"},
            status_code=503,
            headers={"Retry-After": "1"},
        )
    if sqlstate == "23503":
        return JSONResponse(
            {"detail": "Un dato referenciado cambió mientras se procesaba: reintenta"},
            status_code=409,
        )
    raise exc


@app.get("/health", tags=["sistema"])
def health() -> dict[str, str]:
    return {"status": "ok"}
