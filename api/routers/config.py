"""Configuration API endpoints."""

import structlog
from fastapi import APIRouter, Depends

from api.dependencies import get_engine
from api.middleware import verify_api_key
from api.schemas import BotConfig, BotConfigUpdate
from bot.config import CapitalTier, TierConfig, settings
from bot.data.settings_store import SettingsStore

logger = structlog.get_logger()
router = APIRouter(prefix="/api/config", tags=["config"])


def _get_strategy_params(engine) -> dict:
    """Read current strategy parameters from the live engine."""
    params = {}
    for strategy in engine.analyzer.strategies:
        s = {}
        for attr in (
            "MAX_HOURS_TO_RESOLUTION", "MIN_IMPLIED_PROB", "MAX_PRICE",
            "MIN_PRICE", "MIN_EDGE", "CONFIDENCE_BASE",
            "MIN_ARB_EDGE", "MIN_SPREAD", "MAX_SPREAD",
            "IMBALANCE_THRESHOLD",
            # price_divergence params
            "MIN_DIVERGENCE_PCT", "TAKE_PROFIT_PCT", "STOP_LOSS_PCT",
            "MAX_HOLD_HOURS_CRYPTO", "MAX_HOLD_HOURS_OTHER",
            # swing_trading params
            "MIN_MOMENTUM", "MAX_HOLD_HOURS", "MIN_HOURS_LEFT",
        ):
            if hasattr(strategy, attr):
                s[attr] = getattr(strategy, attr)
        # Exit threshold
        if hasattr(strategy, "should_exit"):
            for exit_attr in ("EXIT_THRESHOLD",):
                if hasattr(strategy, exit_attr):
                    s[exit_attr] = getattr(strategy, exit_attr)
        if s:
            params[strategy.name] = s
    return params


def _get_quality_params(engine) -> dict:
    """Read current quality filter parameters."""
    analyzer = engine.analyzer
    return {
        "max_spread": analyzer.MAX_SPREAD,
        "max_category_positions": analyzer.MAX_CATEGORY_POSITIONS,
        "min_bid_ratio": analyzer.MIN_BID_RATIO,
        "min_volume_24h": analyzer.MIN_VOLUME_24H,
        "stop_loss_pct": analyzer.STOP_LOSS_PCT,
        "near_worthless_price": analyzer.NEAR_WORTHLESS_PRICE,
        "default_exit_price": analyzer.DEFAULT_EXIT_PRICE,
    }


@router.get("/", response_model=BotConfig)
async def get_config(_: str = Depends(verify_api_key)):
    engine = None
    try:
        engine = get_engine()
        tier = engine.portfolio.tier
        strategy_params = _get_strategy_params(engine)
        quality_params = _get_quality_params(engine)
    except RuntimeError:
        tier = CapitalTier.TIER1
        strategy_params = {}
        quality_params = {}

    disabled = []
    try:
        if engine is not None:
            disabled = sorted(engine.disabled_strategies)
    except Exception as e:
        logger.warning("get_disabled_strategies_failed", error=str(e))

    return BotConfig(
        trading_mode=settings.trading_mode.value,
        scan_interval_seconds=settings.scan_interval_seconds,
        snapshot_interval_seconds=settings.snapshot_interval_seconds,
        max_daily_loss_pct=settings.max_daily_loss_pct,
        max_drawdown_pct=settings.max_drawdown_pct,
        daily_target_pct=settings.daily_target_pct,
        current_tier=tier.value,
        tier_config=TierConfig.get(tier),
        strategy_params=strategy_params,
        quality_params=quality_params,
        disabled_strategies=disabled,
    )


@router.put("/")
async def update_config(update: BotConfigUpdate, _: str = Depends(verify_api_key)):
    changes: list[str] = []

    # Global settings
    if update.scan_interval_seconds is not None:
        settings.scan_interval_seconds = update.scan_interval_seconds
        changes.append(f"scan_interval={update.scan_interval_seconds}s")
    if update.max_daily_loss_pct is not None:
        settings.max_daily_loss_pct = update.max_daily_loss_pct
        changes.append(f"max_daily_loss={update.max_daily_loss_pct:.0%}")
    if update.max_drawdown_pct is not None:
        settings.max_drawdown_pct = update.max_drawdown_pct
        changes.append(f"max_drawdown={update.max_drawdown_pct:.0%}")
    if update.daily_target_pct is not None:
        settings.daily_target_pct = update.daily_target_pct
        changes.append(f"daily_target={update.daily_target_pct:.1%}")

    # Tier config updates
    if update.tier_config:
        try:
            engine = get_engine()
            tier = engine.portfolio.tier
            TierConfig.update(tier, update.tier_config)
            changes.append(f"tier_config({tier.value})")
        except RuntimeError:
            pass

    # Strategy parameter updates (whitelist-validated)
    if update.strategy_params:
        try:
            engine = get_engine()
            for strategy in engine.analyzer.strategies:
                if strategy.name in update.strategy_params:
                    params = update.strategy_params[strategy.name]
                    for key, value in params.items():
                        if strategy.update_param(key, value):
                            changes.append(f"{strategy.name}.{key}={value}")
        except RuntimeError:
            pass

    # Disabled strategies
    if update.disabled_strategies is not None:
        try:
            engine = get_engine()
            valid_names = {s.name for s in engine.analyzer.strategies}
            new_disabled = set(update.disabled_strategies) & valid_names
            engine.disabled_strategies = new_disabled
            engine.analyzer.disabled_strategies = new_disabled
            changes.append(f"disabled_strategies={sorted(new_disabled)}")
        except RuntimeError:
            pass

    # Quality filter updates (explicit mapping + range validation)
    if update.quality_params:
        try:
            engine = get_engine()
            analyzer = engine.analyzer
            _quality_spec = {
                "max_spread": ("MAX_SPREAD", float, 0.0, 1.0),
                "max_category_positions": ("MAX_CATEGORY_POSITIONS", int, 1, 20),
                "min_bid_ratio": ("MIN_BID_RATIO", float, 0.0, 1.0),
                "min_volume_24h": ("MIN_VOLUME_24H", float, 0.0, 100000.0),
                "stop_loss_pct": ("STOP_LOSS_PCT", float, 0.0, 1.0),
                "near_worthless_price": ("NEAR_WORTHLESS_PRICE", float, 0.0, 0.5),
                "default_exit_price": ("DEFAULT_EXIT_PRICE", float, 0.0, 1.0),
            }
            for key, value in update.quality_params.items():
                spec = _quality_spec.get(key)
                if not spec:
                    continue
                attr, typ, lo, hi = spec
                try:
                    value = typ(value)
                except (TypeError, ValueError):
                    continue
                if lo <= value <= hi and hasattr(analyzer, attr):
                    setattr(analyzer, attr, value)
                    changes.append(f"quality.{key}={value}")
        except RuntimeError:
            pass

    # Persist to DB so settings survive restarts
    tier = CapitalTier.TIER1
    try:
        engine = get_engine()
        tier = engine.portfolio.tier
    except RuntimeError:
        pass
    try:
        await SettingsStore.save_from_update(update, tier)
    except Exception as e:
        logger.error("settings_persist_failed", error=str(e))

    logger.info("config_updated", changes=changes)
    return {"status": "updated", "changes": changes}


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


@router.post("/risk/reset")
async def reset_risk_state(_: str = Depends(verify_api_key)):
    """Reset corrupted risk manager and portfolio PnL state.

    Use after bugs that cause phantom PnL accumulation.
    Resets daily PnL counters to zero and peak equity to current.
    """
    engine = get_engine()
    equity = engine.portfolio.total_equity

    # Reset via encapsulated methods (no direct private attribute access)
    engine.risk_manager.reset_daily_state(equity)
    engine.portfolio.reset_daily_state(equity)

    logger.info(
        "risk_state_reset",
        equity=equity,
        tier=engine.portfolio.tier.value,
    )
    return {
        "status": "reset",
        "equity": equity,
        "daily_pnl": 0.0,
        "peak_equity": equity,
    }
