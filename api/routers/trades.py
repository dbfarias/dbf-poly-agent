"""Trades API endpoints."""

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db
from api.middleware import verify_api_key
from api.schemas import TradeResponse, TradeStats
from bot.data.repositories import TradeRepository

router = APIRouter(prefix="/api/trades", tags=["trades"])


@router.get("/history", response_model=list[TradeResponse])
async def get_trade_history(
    limit: int = Query(default=50, ge=1, le=500),
    strategy: str | None = Query(default=None, max_length=100),
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    repo = TradeRepository(db)
    if strategy:
        trades = await repo.get_by_strategy(strategy, limit=limit)
    else:
        trades = await repo.get_recent(limit=limit)
    return [
        TradeResponse(
            id=t.id,
            created_at=t.created_at,
            market_id=t.market_id,
            question=t.question,
            outcome=t.outcome,
            side=t.side,
            price=t.price,
            size=t.size,
            cost_usd=t.cost_usd,
            strategy=t.strategy,
            edge=t.edge,
            estimated_prob=t.estimated_prob,
            confidence=t.confidence,
            reasoning=t.reasoning,
            status=t.status,
            pnl=t.pnl,
            exit_reason=getattr(t, "exit_reason", None),
            is_paper=t.is_paper,
        )
        for t in trades
    ]


@router.get("/stats", response_model=TradeStats)
async def get_trade_stats(_: str = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    repo = TradeRepository(db)
    stats = await repo.get_stats()
    return TradeStats(**stats)
