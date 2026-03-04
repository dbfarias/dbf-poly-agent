"""Portfolio API endpoints."""

from collections import defaultdict

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db, get_engine
from api.middleware import verify_api_key
from api.schemas import AllocationItem, EquityPoint, PortfolioOverview, PositionResponse
from bot.config import settings
from bot.data.repositories import PortfolioSnapshotRepository, PositionRepository

logger = structlog.get_logger()
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
    days: int = Query(default=30, ge=1, le=365),
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


class DailyPnlPoint(BaseModel):
    date: str
    start_equity: float
    end_equity: float
    pnl: float
    pnl_pct: float
    target: float
    hit_target: bool


@router.get("/daily-pnl", response_model=list[DailyPnlPoint])
async def get_daily_pnl(
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Daily PnL summary aggregated from portfolio snapshots.

    Groups snapshots by UTC date. For each day, compares the first
    and last snapshot equity to compute the day's PnL.
    """
    repo = PortfolioSnapshotRepository(db)
    snapshots = await repo.get_equity_curve(days=365)

    if not snapshots:
        return []

    # Group by UTC date
    by_day: dict[str, dict] = defaultdict(
        lambda: {"first": None, "last": None},
    )
    for s in snapshots:
        day = s.timestamp.strftime("%Y-%m-%d")
        entry = by_day[day]
        if entry["first"] is None:
            entry["first"] = s
        entry["last"] = s

    target_pct = settings.daily_target_pct
    result = []
    for day in sorted(by_day):
        data = by_day[day]
        start_eq = data["first"].total_equity
        end_eq = data["last"].total_equity
        pnl = end_eq - start_eq
        target = start_eq * target_pct
        pnl_pct = (
            (pnl / start_eq * 100) if start_eq > 0 else 0.0
        )
        result.append(DailyPnlPoint(
            date=day,
            start_equity=round(start_eq, 2),
            end_equity=round(end_eq, 2),
            pnl=round(pnl, 4),
            pnl_pct=round(pnl_pct, 2),
            target=round(target, 4),
            hit_target=pnl >= target,
        ))

    return result


@router.get("/allocation", response_model=list[AllocationItem])
async def get_allocation(_: str = Depends(verify_api_key), db: AsyncSession = Depends(get_db)):
    repo = PositionRepository(db)
    by_category = await repo.get_by_category()
    total = sum(by_category.values()) or 1.0
    return [
        AllocationItem(category=cat, value=val, percentage=val / total)
        for cat, val in by_category.items()
    ]


class ForceCloseRequest(BaseModel):
    position_id: int
    reason: str = Field(
        default="manual_close",
        max_length=200,
        pattern=r"^[a-zA-Z0-9_\- ]+$",
    )


class ForceCloseResponse(BaseModel):
    success: bool
    position_id: int
    market_id: str
    pnl: float
    message: str


@router.post("/positions/close", response_model=ForceCloseResponse)
async def force_close_position(
    req: ForceCloseRequest,
    _: str = Depends(verify_api_key),
):
    """Force-close an open position by selling at current market price."""
    engine = get_engine()

    # Find the position
    position = next(
        (p for p in engine.portfolio.positions if p.id == req.position_id and p.is_open),
        None,
    )
    if not position:
        raise HTTPException(status_code=404, detail=f"Open position {req.position_id} not found")

    logger.info(
        "force_close_requested",
        position_id=req.position_id,
        market_id=position.market_id,
        reason=req.reason,
    )

    # Place sell order
    trade = await engine.order_manager.close_position(
        market_id=position.market_id,
        token_id=position.token_id,
        size=position.size,
        current_price=position.current_price,
        question=position.question,
        outcome=position.outcome,
        category=position.category,
        strategy=position.strategy,
    )

    if not trade:
        if position.size < 5.0:
            msg = (
                f"Position too small to sell ({position.size:.2f} shares, "
                f"min 5). Must wait for resolution."
            )
            raise HTTPException(status_code=400, detail=msg)
        raise HTTPException(
            status_code=500, detail="Sell order rejected by exchange"
        )

    # Record PnL only if immediately filled (paper mode or CLOB matched).
    # In live mode with pending sell, PnL is deferred to handle_sell_fill callback.
    if trade.status == "filled":
        pnl = await engine.portfolio.record_trade_close(
            position.market_id, position.current_price
        )
        engine.risk_manager.update_daily_pnl(pnl)

        # Write exit_reason to the original BUY trade (so learner can learn)
        try:
            from bot.data.database import async_session
            from bot.data.repositories import TradeRepository

            async with async_session() as session:
                repo = TradeRepository(session)
                await repo.close_trade_for_position(
                    position.market_id, pnl, req.reason,
                )
        except Exception as e:
            logger.warning(
                "force_close_trade_update_failed",
                market_id=position.market_id,
                error=str(e),
            )
    else:
        pnl = 0.0  # Deferred to handle_sell_fill callback

    logger.info(
        "force_close_completed",
        position_id=req.position_id,
        pnl=pnl,
        reason=req.reason,
    )

    return ForceCloseResponse(
        success=True,
        position_id=req.position_id,
        market_id=position.market_id,
        pnl=pnl,
        message=f"Position closed. PnL: ${pnl:.4f}",
    )
