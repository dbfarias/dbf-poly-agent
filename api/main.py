"""FastAPI application — runs the bot as an asyncio background task."""

import asyncio
import time
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.auth import router as auth_router
from api.middleware import verify_api_key
from api.routers import (
    activity,
    config,
    learner,
    markets,
    portfolio,
    research,
    risk,
    strategies,
    trades,
    websocket,
)
from api.schemas import HealthCheck
from bot.config import settings
from bot.data.database import init_db
from bot.main import create_engine
from bot.utils.logging_config import setup_logging

logger = structlog.get_logger()
_start_time = time.time()


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Start bot as background task on app startup."""
    setup_logging()
    await init_db()

    engine = await create_engine()
    bot_task = asyncio.create_task(engine.run())
    logger.info("bot_started_as_background_task")

    yield

    engine._running = False
    bot_task.cancel()
    try:
        await bot_task
    except asyncio.CancelledError:
        pass
    await engine.shutdown()
    logger.info("app_shutdown_complete")


app = FastAPI(
    title="Polymarket Trading Bot",
    version="0.1.0",
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "PUT", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)

# Register routers
app.include_router(auth_router)
app.include_router(portfolio.router)
app.include_router(trades.router)
app.include_router(strategies.router)
app.include_router(markets.router)
app.include_router(risk.router)
app.include_router(config.router)
app.include_router(learner.router)
app.include_router(activity.router)
app.include_router(research.router)
app.include_router(websocket.router)


@app.get("/api/health", response_model=HealthCheck)
async def health_check():
    from bot.main import engine

    return HealthCheck(
        status="ok",
        uptime_seconds=time.time() - _start_time,
        engine_running=engine.is_running if engine else False,
        cycle_count=engine._cycle_count if engine else 0,
    )


@app.get("/api/status")
async def get_status(_: str = Depends(verify_api_key)):
    from bot.main import engine

    if engine is None:
        return {"error": "Engine not initialized"}
    return engine.get_status()
