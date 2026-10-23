from fastapi import FastAPI

app = FastAPI(
    title="GTD backend",
    description="Backend GTD para n8n + bot de Telegram",
    version="0.1.0",
)


@app.get("/health", tags=["sistema"])
def health() -> dict[str, str]:
    return {"status": "ok"}
