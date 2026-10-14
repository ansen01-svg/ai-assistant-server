from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from sqlalchemy import text

from ai_assistant_server.api.dependencies import DbSession
from ai_assistant_server.api.router import api_router
from ai_assistant_server.core.config import settings
from ai_assistant_server.core.logging import setup_logging
from ai_assistant_server.db.database import engine


@asynccontextmanager
async def lifespan(app: FastAPI):
    setup_logging(settings.log_level)
    yield
    await engine.dispose()


app = FastAPI(title="AI Assistant API", version="0.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(api_router)


@app.get("/health", tags=["health"])
async def health(db: DbSession) -> dict[str, str]:
    await db.execute(text("SELECT 1"))
    return {"status": "ok"}
