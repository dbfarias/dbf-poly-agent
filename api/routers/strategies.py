"""Strategies API endpoints."""

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db, get_engine
from api.middleware import verify_api_key
from api.schemas import StrategyPerformance, StrategyStatus
from bot.data.repositories import StrategyMetricRepository, TradeRepository

router = APIRouter(prefix="/api/strategies", tags=["strategies"])

_LABELS = {
    "arbitrage": "Arbitrage",
    "time_decay": "Time Decay",
    "price_divergence": "Price Divergence",
    "swing_trading": "Swing Trading",
    "value_betting": "Value Betting",
    "market_making": "Market Making",
}


@router.get("/performance", response_model=list[StrategyPerformance])
async def get_performance(_: str = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    # Compute stats directly from trades (real-time, no async lag)
    trade_repo = TradeRepository(db)
    trade_stats = await trade_repo.get_strategy_stats()
    trade_map = {s["strategy"]: s for s in trade_stats}

    # Compute advanced metrics (sharpe, drawdown, hold_time) from trade data
    advanced = await trade_repo.get_strategy_advanced_stats()

    # Get seeded strategy metric names so all strategies show up
    metric_repo = StrategyMetricRepository(db)
    metrics = await metric_repo.get_all_latest()
    metric_names = {m.strategy for m in metrics}

    # Merge: all known strategy names from both sources
    all_strategies = set(trade_map.keys()) | metric_names

    result = []
    for name in all_strategies:
        ts = trade_map.get(name)
        adv = advanced.get(name, {})

        result.append(StrategyPerformance(
            strategy=name,
            total_trades=ts["total_trades"] if ts else 0,
            winning_trades=ts["winning_trades"] if ts else 0,
            losing_trades=ts["losing_trades"] if ts else 0,
            win_rate=ts["win_rate"] if ts else 0.0,
            total_pnl=ts["total_pnl"] if ts else 0.0,
            avg_edge=ts["avg_edge"] if ts else 0.0,
            sharpe_ratio=adv.get("sharpe_ratio", 0.0),
            max_drawdown=adv.get("max_drawdown", 0.0),
            avg_hold_time_hours=adv.get("avg_hold_time_hours", 0.0),
            profit_factor=adv.get("profit_factor", 0.0),
        ))

    return result


@router.get("/category-stats")
async def get_category_stats(
    days: int = Query(default=30, ge=1, le=365),
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Get trade stats grouped by (strategy, category) for the last N days."""
    trade_repo = TradeRepository(db)
    return await trade_repo.get_strategy_category_stats(days=days)


@router.get("/status", response_model=list[StrategyStatus])
async def get_strategy_status(
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Live runtime status for each strategy (tier, disabled, paused, perf)."""
    try:
        engine = get_engine()
    except RuntimeError:
        return []

    tier = engine.portfolio.tier
    disabled = engine.disabled_strategies
    learner = engine.learner
    now = datetime.now(timezone.utc)

    # Trade stats from DB
    trade_repo = TradeRepository(db)
    trade_stats = await trade_repo.get_strategy_stats()
    stats_map = {s["strategy"]: s for s in trade_stats}

    result = []
    for strategy in engine.analyzer.strategies:
        name = strategy.name
        tier_ok = strategy.is_enabled_for_tier(tier)
        admin_disabled = name in disabled

        # Learner pause check
        paused = False
        remaining = 0.0
        paused_at = learner._paused_strategies.get(name)
        if paused_at is not None:
            elapsed = (now - paused_at).total_seconds() / 3600
            remaining = max(0.0, learner.PAUSE_COOLDOWN_HOURS - elapsed)
            paused = remaining > 0

        ts = stats_map.get(name)

        result.append(StrategyStatus(
            name=name,
            label=_LABELS.get(name, name.replace("_", " ").title()),
            min_tier=strategy.min_tier.value,
            is_tier_available=tier_ok,
            is_admin_disabled=admin_disabled,
            is_learner_paused=paused,
            pause_remaining_hours=round(remaining, 1),
            is_active=tier_ok and not admin_disabled and not paused,
            total_trades=ts["total_trades"] if ts else 0,
            win_rate=ts["win_rate"] if ts else 0.0,
            total_pnl=round(ts["total_pnl"], 4) if ts else 0.0,
        ))

    return result
