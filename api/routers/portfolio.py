"""Portfolio API endpoints."""

from fastapi import APIRouter, Depends
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db, get_engine
from api.middleware import verify_api_key
from api.schemas import AllocationItem, EquityPoint, PortfolioOverview, PositionResponse
from bot.data.repositories import PortfolioSnapshotRepository, PositionRepository

router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/overview", response_model=PortfolioOverview)
async def get_overview(_: str = Depends(verify_api_key)):
    engine = get_engine()
    return PortfolioOverview(**engine.portfolio.get_overview())


@router.get("/positions", response_model=list[PositionResponse])
async def get_positions(_: str = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    repo = PositionRepository(db)
    positions = await repo.get_open()
    return [
        PositionResponse(
            id=p.id,
            market_id=p.market_id,
            token_id=p.token_id,
            question=p.question,
            outcome=p.outcome,
            category=p.category,
            strategy=p.strategy,
            side=p.side,
            size=p.size,
            avg_price=p.avg_price,
            current_price=p.current_price,
            cost_basis=p.cost_basis,
            unrealized_pnl=p.unrealized_pnl,
            is_open=p.is_open,
            created_at=p.created_at,
        )
        for p in positions
    ]


@router.get("/equity-curve", response_model=list[EquityPoint])
async def get_equity_curve(
    days: int = 30,
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    repo = PortfolioSnapshotRepository(db)
    snapshots = await repo.get_equity_curve(days=days)
    return [
        EquityPoint(
            timestamp=s.timestamp,
            total_equity=s.total_equity,
            cash_balance=s.cash_balance,
            positions_value=s.positions_value,
            daily_return_pct=s.daily_return_pct,
        )
        for s in snapshots
    ]


@router.get("/allocation", response_model=list[AllocationItem])
async def get_allocation(_: str = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    repo = PositionRepository(db)
    by_category = await repo.get_by_category()
    total = sum(by_category.values()) or 1.0
    return [
        AllocationItem(category=cat, value=val, percentage=val / total)
        for cat, val in by_category.items()
    ]
