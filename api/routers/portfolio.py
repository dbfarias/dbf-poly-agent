"""Portfolio API endpoints."""

from collections import defaultdict

import structlog
from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from api.dependencies import get_db, get_engine
from api.middleware import verify_api_key
from api.schemas import (
    AllocationItem,
    EquityPoint,
    PortfolioOverview,
    PositionResponse,
    SellPositionRequest,
    SellPositionResponse,
)
from bot.config import settings
from bot.data.repositories import PortfolioSnapshotRepository, PositionRepository

logger = structlog.get_logger()
router = APIRouter(prefix="/api/portfolio", tags=["portfolio"])


@router.get("/overview", response_model=PortfolioOverview)
async def get_overview(_: str = Depends(verify_api_key)):
    engine = get_engine()
    overview = engine.portfolio.get_overview()
    overview["stuck_positions"] = engine.closer.stuck_positions
    return PortfolioOverview(**overview)


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

    Groups snapshots by local trading day (adjusted by timezone_offset_hours).
    For each day, compares the first and last snapshot equity.
    """
    from datetime import timedelta

    from bot.config import settings as bot_settings

    repo = PortfolioSnapshotRepository(db)
    snapshots = await repo.get_equity_curve(days=365)

    if not snapshots:
        return []

    offset = timedelta(hours=bot_settings.timezone_offset_hours)

    # Group by local trading day — accumulate trading_pnl (deposit-immune)
    by_day: dict[str, dict] = defaultdict(
        lambda: {"first": None, "last": None},
    )
    for s in snapshots:
        local_ts = s.timestamp + offset
        day = local_ts.strftime("%Y-%m-%d")
        entry = by_day[day]
        if entry["first"] is None:
            entry["first"] = s
        entry["last"] = s

    target_pct = settings.daily_target_pct
    result = []
    for day in sorted(by_day):
        data = by_day[day]
        first_snap = data["first"]
        last_snap = data["last"]
        start_eq = first_snap.total_equity
        end_eq = last_snap.total_equity

        # Use trading_pnl from the last snapshot of the day (deposit-immune).
        # Falls back to equity delta for old snapshots without trading_pnl.
        # Use trading_pnl (deposit-immune) when available.
        # Only fall back to equity delta for old snapshots without the column.
        last_tpnl = getattr(last_snap, "trading_pnl", None)
        pnl = last_tpnl if last_tpnl is not None else (end_eq - start_eq)

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
                    close_price=position.current_price,
                    position_size=position.size,
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


@router.post("/positions/sell", response_model=SellPositionResponse)
async def sell_position(
    req: SellPositionRequest,
    _: str = Depends(verify_api_key),
):
    """Sell an open position at current best bid price."""
    engine = get_engine()

    # Find position by market_id
    position = next(
        (p for p in engine.portfolio.positions if p.market_id == req.market_id and p.is_open),
        None,
    )
    if not position:
        raise HTTPException(
            status_code=404,
            detail=f"Open position for market {req.market_id} not found",
        )

    sell_size = req.size if req.size is not None else position.size

    if sell_size <= 0:
        raise HTTPException(status_code=400, detail="Sell size must be positive")
    if sell_size > position.size:
        raise HTTPException(
            status_code=400,
            detail=f"Sell size {sell_size} exceeds position size {position.size}",
        )

    # Fetch best bid from orderbook
    try:
        orderbook = await engine.clob.get_order_book(position.token_id)
        best_bid = orderbook.bids[0].price if orderbook.bids else position.current_price
    except Exception:
        best_bid = position.current_price

    logger.info(
        "sell_position_requested",
        market_id=req.market_id,
        size=sell_size,
        best_bid=best_bid,
    )

    # Place sell order via order_manager
    trade = await engine.order_manager.close_position(
        market_id=position.market_id,
        token_id=position.token_id,
        size=sell_size,
        current_price=best_bid,
        question=position.question,
        outcome=position.outcome,
        category=position.category,
        strategy=position.strategy,
    )

    if not trade:
        if sell_size < 5.0:
            return SellPositionResponse(
                success=False,
                market_id=req.market_id,
                question=position.question,
                error=(
                    f"Position too small to sell ({sell_size:.2f} shares, "
                    f"min ~5). Must wait for resolution."
                ),
            )
        return SellPositionResponse(
            success=False,
            market_id=req.market_id,
            question=position.question,
            error="Sell order rejected by exchange",
        )

    proceeds = sell_size * best_bid

    # Record PnL if immediately filled
    if trade.status == "filled":
        pnl = await engine.portfolio.record_trade_close(position.market_id, best_bid)
        engine.risk_manager.update_daily_pnl(pnl)

        # Write PnL to trades table so learner can learn from this exit
        try:
            from bot.data.database import async_session
            from bot.data.repositories import TradeRepository

            async with async_session() as session:
                repo = TradeRepository(session)
                await repo.close_trade_for_position(
                    position.market_id, pnl, "manual_sell",
                    close_price=best_bid,
                    position_size=sell_size,
                )
        except Exception as e:
            logger.warning(
                "sell_position_trade_update_failed",
                market_id=position.market_id,
                error=str(e),
            )

    logger.info(
        "sell_position_completed",
        market_id=req.market_id,
        size_sold=sell_size,
        price=best_bid,
        proceeds=proceeds,
        order_id=trade.order_id if trade else None,
    )

    return SellPositionResponse(
        success=True,
        market_id=req.market_id,
        question=position.question,
        size_sold=sell_size,
        price=best_bid,
        proceeds=round(proceeds, 4),
        order_id=trade.order_id if trade else None,
    )


class CapitalFlowResponse(BaseModel):
    id: int
    timestamp: str
    amount: float
    flow_type: str
    source: str
    note: str
    is_paper: bool


@router.get("/capital-flows", response_model=list[CapitalFlowResponse])
async def get_capital_flows(
    limit: int = Query(default=50, ge=1, le=200),
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Get recent capital flow history (deposits/withdrawals)."""
    from bot.data.repositories import CapitalFlowRepository

    repo = CapitalFlowRepository(db)
    flows = await repo.get_recent(limit=limit)
    return [
        CapitalFlowResponse(
            id=f.id,
            timestamp=f.timestamp.isoformat() if f.timestamp else "",
            amount=f.amount,
            flow_type=f.flow_type,
            source=f.source,
            note=f.note,
            is_paper=f.is_paper,
        )
        for f in flows
    ]


@router.delete("/capital-flows/{flow_id}")
async def delete_capital_flow(
    flow_id: int,
    _: str = Depends(verify_api_key),
    db: AsyncSession = Depends(get_db),
):
    """Delete a capital flow entry (e.g. false auto-detected deposit)."""
    from bot.data.repositories import CapitalFlowRepository

    repo = CapitalFlowRepository(db)
    logger.info("capital_flow_delete_requested", flow_id=flow_id)
    deleted = await repo.delete_by_id(flow_id)
    if not deleted:
        raise HTTPException(status_code=404, detail=f"Capital flow {flow_id} not found")
    return {"success": True, "deleted_id": flow_id}


class ForceRemoveRequest(BaseModel):
    """Remove a ghost position from DB without selling on CLOB."""
    position_id: int
    pnl: float = Field(default=0.0, description="Manual PnL override (e.g. -2.56 for known loss)")
    reason: str = Field(
        default="ghost_position_removed",
        max_length=200,
        pattern=r"^[a-zA-Z0-9_\- ]+$",
    )


@router.post("/positions/force-remove", response_model=ForceCloseResponse)
async def force_remove_position(
    req: ForceRemoveRequest,
    _: str = Depends(verify_api_key),
):
    """Remove a stuck/ghost position from DB without attempting a CLOB sell.

    Use when the on-chain state is irrecoverable (e.g. allowance errors)
    and the user wants to free the portfolio slot.
    """
    engine = get_engine()

    position = next(
        (p for p in engine.portfolio.positions if p.id == req.position_id and p.is_open),
        None,
    )
    if not position:
        raise HTTPException(status_code=404, detail=f"Open position {req.position_id} not found")

    logger.warning(
        "force_remove_ghost_position",
        position_id=req.position_id,
        market_id=position.market_id,
        pnl=req.pnl,
        reason=req.reason,
    )

    # Close in portfolio (no CLOB interaction)
    await engine.portfolio.record_trade_close(position.market_id, position.current_price)
    engine.risk_manager.update_daily_pnl(req.pnl)

    # Update trade DB
    try:
        from bot.data.database import async_session
        from bot.data.repositories import TradeRepository

        async with async_session() as session:
            repo = TradeRepository(session)
            await repo.close_trade_for_position(
                position.market_id, req.pnl, req.reason,
                close_price=position.current_price,
                position_size=position.size,
            )
    except Exception as e:
        logger.warning("force_remove_db_error", error=str(e))

    # Clear sell failure counter so it doesn't linger
    engine.closer._sell_fail_count.pop(position.market_id, None)

    return ForceCloseResponse(
        success=True,
        position_id=req.position_id,
        market_id=position.market_id,
        pnl=req.pnl,
        message=f"Ghost position removed from DB. PnL: ${req.pnl:.4f}",
    )
