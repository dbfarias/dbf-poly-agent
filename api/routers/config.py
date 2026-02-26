"""Configuration API endpoints."""

from fastapi import APIRouter, Depends

from api.dependencies import get_engine
from api.middleware import verify_api_key
from api.schemas import BotConfig, BotConfigUpdate
from bot.config import settings

router = APIRouter(prefix="/api/config", tags=["config"])


@router.get("/", response_model=BotConfig)
async def get_config(_: str = Depends(verify_api_key)):
    return BotConfig(
        trading_mode=settings.trading_mode.value,
        scan_interval_seconds=settings.scan_interval_seconds,
        snapshot_interval_seconds=settings.snapshot_interval_seconds,
        max_daily_loss_pct=settings.max_daily_loss_pct,
        max_drawdown_pct=settings.max_drawdown_pct,
    )


@router.put("/")
async def update_config(update: BotConfigUpdate, _: str = Depends(verify_api_key)):
    if update.scan_interval_seconds is not None:
        settings.scan_interval_seconds = update.scan_interval_seconds
    if update.max_daily_loss_pct is not None:
        settings.max_daily_loss_pct = update.max_daily_loss_pct
    if update.max_drawdown_pct is not None:
        settings.max_drawdown_pct = update.max_drawdown_pct
    return {"status": "updated"}


@router.post("/trading/pause")
async def pause_trading(_: str = Depends(verify_api_key)):
    engine = get_engine()
    engine.risk_manager.pause()
    return {"status": "paused"}


@router.post("/trading/resume")
async def resume_trading(_: str = Depends(verify_api_key)):
    engine = get_engine()
    engine.risk_manager.resume()
    return {"status": "resumed"}
