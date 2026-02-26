"""Strategies API endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.schemas import StrategyPerformance
from bot.data.repositories import StrategyMetricRepository

router = APIRouter(prefix="/api/strategies", tags=["strategies"])


@router.get("/performance", response_model=list[StrategyPerformance])
async def get_performance(db: AsyncSession = Depends(get_db)):
    repo = StrategyMetricRepository(db)
    metrics = await repo.get_all_latest()
    return [
        StrategyPerformance(
            strategy=m.strategy,
            total_trades=m.total_trades,
            winning_trades=m.winning_trades,
            losing_trades=m.losing_trades,
            win_rate=m.win_rate,
            total_pnl=m.total_pnl,
            avg_edge=m.avg_edge,
            sharpe_ratio=m.sharpe_ratio,
            max_drawdown=m.max_drawdown,
            avg_hold_time_hours=m.avg_hold_time_hours,
        )
        for m in metrics
    ]
