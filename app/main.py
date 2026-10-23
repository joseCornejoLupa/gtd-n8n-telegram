from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config import get_allowed_telegram_ids, get_api_key
from app.routers import capture, inbox, lists, tasks, users


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
    version="0.5.0",
    lifespan=lifespan,
)

app.include_router(capture.router)
app.include_router(inbox.router)
app.include_router(lists.router)
app.include_router(tasks.router)
app.include_router(users.router)


@app.get("/health", tags=["sistema"])
def health() -> dict[str, str]:
    return {"status": "ok"}
