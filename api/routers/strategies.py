"""Strategies API endpoints."""

from fastapi import APIRouter, Depends
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

    # Get pre-computed metrics for extra fields (sharpe, drawdown, hold_time)
    metric_repo = StrategyMetricRepository(db)
    metrics = await metric_repo.get_all_latest()
    metric_map = {m.strategy: m for m in metrics}

    # Merge: all known strategy names from both sources
    all_strategies = set(trade_map.keys()) | set(metric_map.keys())

    result = []
    for name in all_strategies:
        ts = trade_map.get(name)
        m = metric_map.get(name)

        result.append(StrategyPerformance(
            strategy=name,
            total_trades=ts["total_trades"] if ts else (m.total_trades if m else 0),
            winning_trades=ts["winning_trades"] if ts else (m.winning_trades if m else 0),
            losing_trades=ts["losing_trades"] if ts else (m.losing_trades if m else 0),
            win_rate=ts["win_rate"] if ts else (m.win_rate if m else 0.0),
            total_pnl=ts["total_pnl"] if ts else (m.total_pnl if m else 0.0),
            avg_edge=ts["avg_edge"] if ts else (m.avg_edge if m else 0.0),
            sharpe_ratio=m.sharpe_ratio if m else 0.0,
            max_drawdown=m.max_drawdown if m else 0.0,
            avg_hold_time_hours=m.avg_hold_time_hours if m else 0.0,
        ))

    return result
