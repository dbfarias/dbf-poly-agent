"""FastAPI application — runs the bot as an asyncio background task."""

import asyncio
import time
from contextlib import asynccontextmanager

import structlog
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from api.routers import config, markets, portfolio, risk, strategies, trades, websocket
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
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Register routers
app.include_router(portfolio.router)
app.include_router(trades.router)
app.include_router(strategies.router)
app.include_router(markets.router)
app.include_router(risk.router)
app.include_router(config.router)
app.include_router(websocket.router)


@app.get("/api/health", response_model=HealthCheck)
async def health_check():
    from bot.main import engine

    return HealthCheck(
        status="ok",
        mode=settings.trading_mode.value,
        uptime_seconds=time.time() - _start_time,
        engine_running=engine.is_running if engine else False,
        cycle_count=engine._cycle_count if engine else 0,
        equity=engine.portfolio.total_equity if engine else 0,
    )


@app.get("/api/status")
async def get_status():
    from bot.main import engine

    if engine is None:
        return {"error": "Engine not initialized"}
    return engine.get_status()
