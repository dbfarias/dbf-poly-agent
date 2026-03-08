"""Configuration API endpoints."""

import structlog
from fastapi import APIRouter, Depends

from api.dependencies import get_engine
from api.middleware import verify_api_key
from api.schemas import BotConfig, BotConfigUpdate
from bot.config import CapitalTier, TierConfig, settings
from bot.data.settings_store import SettingsStore
from bot.research.llm_debate import cost_tracker as llm_cost_tracker

logger = structlog.get_logger()
router = APIRouter(prefix="/api/config", tags=["config"])


def _get_strategy_params(engine) -> dict:
    """Read current strategy parameters from the live engine.

    Dynamically reads from each strategy's _MUTABLE_PARAMS registry
    so new params are automatically exposed without hardcoded lists.
    """
    params = {}
    for strategy in engine.analyzer.strategies:
        s = {}
        for attr in strategy._MUTABLE_PARAMS:
            if hasattr(strategy, attr):
                s[attr] = getattr(strategy, attr)
        if s:
            params[strategy.name] = s
    return params


def _get_quality_params(engine) -> dict:
    """Read current quality filter and engine-level parameters."""
    analyzer = engine.analyzer
    result = {
        "max_spread": analyzer.MAX_SPREAD,
        "max_category_positions": analyzer.MAX_CATEGORY_POSITIONS,
        "min_bid_ratio": analyzer.MIN_BID_RATIO,
        "min_volume_24h": analyzer.MIN_VOLUME_24H,
        "stop_loss_pct": analyzer.STOP_LOSS_PCT,
        "near_worthless_price": analyzer.NEAR_WORTHLESS_PRICE,
        "default_exit_price": analyzer.DEFAULT_EXIT_PRICE,
        "max_position_age_hours": analyzer.MAX_POSITION_AGE_HOURS,
        "take_profit_price": analyzer.TAKE_PROFIT_PRICE,
        "take_profit_min_hold_hours": analyzer.TAKE_PROFIT_MIN_HOLD_HOURS,
    }
    # Learner params
    if hasattr(engine, "learner"):
        learner = engine.learner
        result["pause_lookback"] = learner.PAUSE_LOOKBACK
        result["pause_win_rate"] = learner.PAUSE_WIN_RATE
        result["pause_min_loss"] = learner.PAUSE_MIN_LOSS
        result["pause_cooldown_hours"] = learner.PAUSE_COOLDOWN_HOURS
        result["multiplier_min"] = learner.MULTIPLIER_MIN
        result["multiplier_max"] = learner.MULTIPLIER_MAX
        result["min_trades_for_adjustment"] = learner.MIN_TRADES_FOR_ADJUSTMENT
    # PositionCloser params
    if hasattr(engine, "closer"):
        closer = engine.closer
        result["min_rebalance_edge"] = closer.min_rebalance_edge
        result["min_hold_seconds"] = closer.min_hold_seconds
        result["rebalance_resolution_shield_hours"] = closer.rebalance_resolution_shield_hours
        result["rebalance_resolution_max_loss_pct"] = closer.rebalance_resolution_max_loss_pct
    # Engine-level params
    result["market_cooldown_hours"] = engine.market_cooldown_hours
    result["min_balance_for_trades"] = engine.min_balance_for_trades
    return result


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
    blocked_types = []
    try:
        if engine is not None:
            disabled = sorted(engine.disabled_strategies)
            blocked_types = sorted(engine.analyzer.blocked_market_types)
    except Exception as e:
        logger.warning("get_disabled_strategies_failed", error=str(e))

    return BotConfig(
        trading_mode=settings.trading_mode.value,
        scan_interval_seconds=settings.scan_interval_seconds,
        snapshot_interval_seconds=settings.snapshot_interval_seconds,
        max_daily_loss_pct=settings.max_daily_loss_pct,
        max_drawdown_pct=settings.max_drawdown_pct,
        daily_target_pct=settings.daily_target_pct,
        use_llm_sentiment=settings.use_llm_sentiment,
        use_llm_debate=settings.use_llm_debate,
        use_llm_reviewer=settings.use_llm_reviewer,
        use_multi_round_debate=settings.use_multi_round_debate,
        use_llm_keywords=settings.use_llm_keywords,
        use_llm_post_mortem=settings.use_llm_post_mortem,
        use_auto_claim=settings.use_auto_claim,
        llm_daily_budget=settings.llm_daily_budget,
        llm_today_cost=round(llm_cost_tracker.today_cost, 4),
        current_tier=tier.value,
        tier_config=TierConfig.get(tier),
        strategy_params=strategy_params,
        quality_params=quality_params,
        disabled_strategies=disabled,
        blocked_market_types=blocked_types,
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
    if update.use_llm_sentiment is not None:
        settings.use_llm_sentiment = update.use_llm_sentiment
        changes.append(f"use_llm_sentiment={update.use_llm_sentiment}")
    if update.use_llm_debate is not None:
        settings.use_llm_debate = update.use_llm_debate
        changes.append(f"use_llm_debate={update.use_llm_debate}")
    if update.use_llm_reviewer is not None:
        settings.use_llm_reviewer = update.use_llm_reviewer
        changes.append(f"use_llm_reviewer={update.use_llm_reviewer}")
    if update.use_multi_round_debate is not None:
        settings.use_multi_round_debate = update.use_multi_round_debate
        changes.append(f"use_multi_round_debate={update.use_multi_round_debate}")
    if update.use_llm_keywords is not None:
        settings.use_llm_keywords = update.use_llm_keywords
        changes.append(f"use_llm_keywords={update.use_llm_keywords}")
    if update.use_llm_post_mortem is not None:
        settings.use_llm_post_mortem = update.use_llm_post_mortem
        changes.append(f"use_llm_post_mortem={update.use_llm_post_mortem}")
    if update.use_auto_claim is not None:
        settings.use_auto_claim = update.use_auto_claim
        changes.append(f"use_auto_claim={update.use_auto_claim}")
    if update.llm_daily_budget is not None:
        settings.llm_daily_budget = update.llm_daily_budget
        llm_cost_tracker.daily_budget = update.llm_daily_budget
        changes.append(f"llm_daily_budget=${update.llm_daily_budget:.2f}")

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
                            # Sync per-strategy hold to closer
                            if key == "MIN_HOLD_SECONDS":
                                engine.closer.strategy_min_hold[strategy.name] = int(value)
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

    # Blocked market types (question-keyword detection: "sports", "crypto", "other")
    if update.blocked_market_types is not None:
        try:
            engine = get_engine()
            valid_types = {"sports", "crypto", "other"}
            new_blocked = set(update.blocked_market_types) & valid_types
            engine.analyzer.blocked_market_types = new_blocked
            changes.append(f"blocked_market_types={sorted(new_blocked)}")
        except RuntimeError:
            pass

    # Quality filter updates (explicit mapping + range validation)
    if update.quality_params:
        try:
            engine = get_engine()
            _quality_spec = {
                # MarketAnalyzer params
                "max_spread": ("analyzer", "MAX_SPREAD", float, 0.0, 1.0),
                "max_category_positions": ("analyzer", "MAX_CATEGORY_POSITIONS", int, 1, 20),
                "min_bid_ratio": ("analyzer", "MIN_BID_RATIO", float, 0.0, 1.0),
                "min_volume_24h": ("analyzer", "MIN_VOLUME_24H", float, 0.0, 100000.0),
                "stop_loss_pct": ("analyzer", "STOP_LOSS_PCT", float, 0.0, 1.0),
                "near_worthless_price": ("analyzer", "NEAR_WORTHLESS_PRICE", float, 0.0, 0.5),
                "default_exit_price": ("analyzer", "DEFAULT_EXIT_PRICE", float, 0.0, 1.0),
                "max_position_age_hours": ("analyzer", "MAX_POSITION_AGE_HOURS", float, 1.0, 720.0),
                "take_profit_price": ("analyzer", "TAKE_PROFIT_PRICE", float, 0.5, 1.0),
                "take_profit_min_hold_hours": (
                    "analyzer", "TAKE_PROFIT_MIN_HOLD_HOURS", float, 0.0, 168.0,
                ),
                # Learner params
                "pause_lookback": ("learner", "PAUSE_LOOKBACK", int, 2, 50),
                "pause_win_rate": ("learner", "PAUSE_WIN_RATE", float, 0.0, 1.0),
                "pause_min_loss": ("learner", "PAUSE_MIN_LOSS", float, -100.0, 0.0),
                "pause_cooldown_hours": ("learner", "PAUSE_COOLDOWN_HOURS", float, 1.0, 168.0),
                "multiplier_min": ("learner", "MULTIPLIER_MIN", float, 0.1, 1.0),
                "multiplier_max": ("learner", "MULTIPLIER_MAX", float, 1.0, 5.0),
                "min_trades_for_adjustment": (
                    "learner", "MIN_TRADES_FOR_ADJUSTMENT", int, 1, 50,
                ),
                # PositionCloser params
                "min_rebalance_edge": ("closer", "min_rebalance_edge", float, 0.0, 0.5),
                "market_cooldown_hours": ("engine", "market_cooldown_hours", float, 0.25, 24.0),
                "min_balance_for_trades": ("engine", "min_balance_for_trades", float, 0.0, 100.0),
                "min_hold_seconds": ("closer", "min_hold_seconds", int, 0, 14400),
                "rebalance_resolution_shield_hours": (
                    "closer", "rebalance_resolution_shield_hours",
                    float, 0.0, 168.0,
                ),
                "rebalance_resolution_max_loss_pct": (
                    "closer", "rebalance_resolution_max_loss_pct",
                    float, 0.01, 0.5,
                ),
            }
            for key, value in update.quality_params.items():
                spec = _quality_spec.get(key)
                if not spec:
                    continue
                target_name, attr, typ, lo, hi = spec
                try:
                    value = typ(value)
                except (TypeError, ValueError):
                    continue
                target = engine if target_name == "engine" else getattr(engine, target_name, None)
                if target is None or not (lo <= value <= hi):
                    continue
                if hasattr(target, attr):
                    setattr(target, attr, value)
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
