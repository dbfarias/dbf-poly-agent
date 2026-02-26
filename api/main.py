"""FastAPI application — runs the bot as an asyncio background task."""

import asyncio
import time
from contextlib import asynccontextmanager

import structlog
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from slowapi.errors import RateLimitExceeded

from api.auth import router as auth_router
from api.middleware import verify_api_key
from api.rate_limit import limiter
from api.routers import (
    activity,
    assistant,
    backtest,
    config,
    learner,
    markets,
    portfolio,
    push,
    report,
    research,
    risk,
    strategies,
    trades,
    watchers,
    websocket,
)
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

    # Wire EventBus → WebSocket broadcast (bot emits, API delivers)
    from bot.agent.events import event_bus

    event_bus.on("trade_filled", websocket.broadcast_trade_event)

    # Wire EventBus → Push notifications (trade fills → mobile push)
    from bot.utils.push_notifications import push_notify_trade

    async def _push_broadcast_trade(**kwargs):
        try:
            side = kwargs.get("side", "BUY")
            pnl = kwargs.get("pnl", 0) or 0
            action = "closed" if side.upper() == "SELL" else "opened"
            await push_notify_trade(
                action=action,
                strategy=kwargs.get("strategy", ""),
                question=kwargs.get("question", ""),
                side=side,
                price=kwargs.get("price", 0),
                size=kwargs.get("size", 0),
                pnl=pnl,
            )
        except Exception:
            pass  # Never let push failures affect the trading loop

    event_bus.on("trade_filled", _push_broadcast_trade)

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

app.state.limiter = limiter


async def _rate_limit_handler(request: Request, exc: RateLimitExceeded):
    return JSONResponse(
        status_code=429,
        content={"detail": "Rate limit exceeded. Try again later."},
    )


app.add_exception_handler(RateLimitExceeded, _rate_limit_handler)

app.add_middleware(
    CORSMiddleware,
    allow_origins=[o.strip() for o in settings.allowed_origins.split(",") if o.strip()],
    allow_credentials=True,
    allow_methods=["GET", "PUT", "POST", "OPTIONS"],
    allow_headers=["Content-Type", "X-API-Key", "Authorization"],
)

# Register routers
app.include_router(auth_router)
app.include_router(backtest.router)
app.include_router(assistant.router)
app.include_router(portfolio.router)
app.include_router(trades.router)
app.include_router(strategies.router)
app.include_router(markets.router)
app.include_router(risk.router)
app.include_router(config.router)
app.include_router(learner.router)
app.include_router(activity.router)
app.include_router(research.router)
app.include_router(report.router)
app.include_router(push.router)
app.include_router(watchers.router)
app.include_router(websocket.router)


@app.get("/api/health")
async def health_check():
    """Minimal health probe — no sensitive info for unauthenticated callers."""
    return {"status": "ok"}


@app.get("/api/status")
async def get_status(_: str = Depends(verify_api_key)):
    from bot.main import engine

    if engine is None:
        return {"error": "Engine not initialized"}
    return engine.get_status()
