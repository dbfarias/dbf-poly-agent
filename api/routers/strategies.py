"""Strategies API endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.middleware import verify_api_key
from api.schemas import StrategyPerformance
from bot.data.repositories import StrategyMetricRepository, TradeRepository

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


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
