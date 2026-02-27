"""Bot activity logger — writes structured events to the bot_activity table.

These events power the Activity page in the dashboard, providing a
human-readable log of what the bot did and why.
"""

import json

import structlog

from bot.data.database import async_session
from bot.data.models import BotActivity

logger = structlog.get_logger()

# Maximum rows kept in the activity table (auto-pruned on each write batch).
MAX_ACTIVITY_ROWS = 5000


async def _write(event: BotActivity) -> None:
    """Persist a single activity event (fire-and-forget safe)."""
    try:
        async with async_session() as session:
            session.add(event)
            await session.commit()
    except Exception as e:
        logger.debug("activity_write_failed", error=str(e))


def _meta(data: dict) -> str:
    """Serialize metadata dict to JSON string."""
    return json.dumps(data, default=str)


# ---------------------------------------------------------------------------
# Public helpers — one per event type
# ---------------------------------------------------------------------------


async def log_signal_found(
    strategy: str,
    market_id: str,
    question: str,
    edge: float,
    price: float,
    prob: float,
    hours: float | None = None,
) -> None:
    await _write(BotActivity(
        event_type="signal_found",
        level="info",
        title=f"Signal found by {strategy}",
        detail=(
            f"Market: {question[:80]}\n"
            f"Price: ${price:.3f} | Edge: {edge:.1%} | Prob: {prob:.1%}"
            + (f" | {hours:.0f}h to resolution" if hours else "")
        ),
        market_id=market_id,
        strategy=strategy,
        metadata_json=_meta({
            "edge": edge, "price": price, "prob": prob,
            "hours_to_resolution": hours,
        }),
    ))


async def log_signal_rejected(
    strategy: str,
    market_id: str,
    question: str,
    reason: str,
    edge: float = 0.0,
    price: float = 0.0,
) -> None:
    await _write(BotActivity(
        event_type="signal_rejected",
        level="warning",
        title=f"Signal rejected: {reason[:60]}",
        detail=(
            f"Strategy: {strategy} | Market: {question[:80]}\n"
            f"Price: ${price:.3f} | Edge: {edge:.1%}\n"
            f"Reason: {reason}"
        ),
        market_id=market_id,
        strategy=strategy,
        metadata_json=_meta({"reason": reason, "edge": edge, "price": price}),
    ))


async def log_order_placed(
    strategy: str,
    market_id: str,
    question: str,
    side: str,
    price: float,
    size_usd: float,
    shares: float,
    status: str,
) -> None:
    level = "success" if status == "filled" else "info"
    title = f"Order {'filled' if status == 'filled' else 'placed'}: {side} {question[:50]}"
    await _write(BotActivity(
        event_type="order_placed",
        level=level,
        title=title,
        detail=(
            f"{side} {shares:.1f} shares @ ${price:.3f} = ${size_usd:.2f}\n"
            f"Strategy: {strategy} | Status: {status}"
        ),
        market_id=market_id,
        strategy=strategy,
        metadata_json=_meta({
            "side": side, "price": price, "size_usd": size_usd,
            "shares": shares, "status": status,
        }),
    ))


async def log_order_expired(
    market_id: str,
    order_id: str,
    age_seconds: float,
) -> None:
    await _write(BotActivity(
        event_type="order_expired",
        level="warning",
        title="Order expired — not filled",
        detail=f"Order {order_id[:16]}... expired after {age_seconds:.0f}s without being filled.",
        market_id=market_id,
        metadata_json=_meta({"order_id": order_id, "age_seconds": age_seconds}),
    ))


async def log_order_filled(
    market_id: str,
    order_id: str,
    strategy: str,
) -> None:
    await _write(BotActivity(
        event_type="order_filled",
        level="success",
        title=f"Pending order filled ({strategy})",
        detail=f"Order {order_id[:16]}... confirmed filled on Polymarket.",
        market_id=market_id,
        strategy=strategy,
        metadata_json=_meta({"order_id": order_id}),
    ))


async def log_position_closed(
    market_id: str,
    question: str,
    strategy: str,
    pnl: float,
    exit_reason: str = "",
) -> None:
    level = "success" if pnl >= 0 else "warning"
    sign = "+" if pnl >= 0 else ""
    await _write(BotActivity(
        event_type="position_closed",
        level=level,
        title=f"Position closed: {sign}${pnl:.2f}",
        detail=(
            f"Market: {question[:80]}\n"
            f"Strategy: {strategy} | P&L: {sign}${pnl:.2f}"
            + (f" | Reason: {exit_reason}" if exit_reason else "")
        ),
        market_id=market_id,
        strategy=strategy,
        metadata_json=_meta({"pnl": pnl, "exit_reason": exit_reason}),
    ))


async def log_exit_triggered(
    market_id: str,
    question: str,
    strategy: str,
    current_price: float,
) -> None:
    await _write(BotActivity(
        event_type="exit_triggered",
        level="info",
        title=f"Exit signal: {strategy}",
        detail=f"Market: {question[:80]}\nCurrent price: ${current_price:.3f}",
        market_id=market_id,
        strategy=strategy,
        metadata_json=_meta({"current_price": current_price}),
    ))


async def log_liquidity_rejected(
    market_id: str,
    reason: str,
    spread: float | None = None,
    best_bid: float | None = None,
) -> None:
    await _write(BotActivity(
        event_type="signal_rejected",
        level="warning",
        title="Liquidity check failed",
        detail=f"Market: {market_id[:20]}...\n{reason}",
        market_id=market_id,
        metadata_json=_meta({
            "reason": reason, "spread": spread, "best_bid": best_bid,
        }),
    ))


async def log_cycle_summary(
    cycle: int,
    equity: float,
    signals_found: int,
    signals_approved: int,
    orders_placed: int,
    pending_orders: int,
    urgency: float,
    daily_progress: float,
) -> None:
    await _write(BotActivity(
        event_type="cycle_summary",
        level="info",
        title=f"Cycle #{cycle} complete",
        detail=(
            f"Equity: ${equity:.2f} | "
            f"Signals: {signals_found} found, {signals_approved} approved, "
            f"{orders_placed} executed\n"
            f"Pending orders: {pending_orders} | "
            f"Urgency: {urgency:.2f} | Progress: {daily_progress:.0%}"
        ),
        metadata_json=_meta({
            "cycle": cycle, "equity": equity,
            "signals_found": signals_found, "signals_approved": signals_approved,
            "orders_placed": orders_placed, "pending_orders": pending_orders,
            "urgency": urgency, "daily_progress": daily_progress,
        }),
    ))


async def log_bot_event(
    title: str,
    detail: str = "",
    level: str = "info",
    metadata: dict | None = None,
) -> None:
    """Generic bot lifecycle event (started, stopped, error, etc.)."""
    await _write(BotActivity(
        event_type="bot_event",
        level=level,
        title=title,
        detail=detail,
        metadata_json=_meta(metadata or {}),
    ))


async def log_price_adjustment(
    market_id: str,
    strategy: str,
    signal_price: float,
    actual_price: float,
    reason: str,
) -> None:
    """Log when order book price differs from signal price."""
    slippage = actual_price - signal_price
    await _write(BotActivity(
        event_type="price_adjust",
        level="info",
        title=f"Price adjusted: ${signal_price:.3f} → ${actual_price:.3f}",
        detail=(
            f"Strategy: {strategy} | Slippage: ${slippage:+.3f}\n"
            f"Reason: {reason}"
        ),
        market_id=market_id,
        strategy=strategy,
        metadata_json=_meta({
            "signal_price": signal_price, "actual_price": actual_price,
            "slippage": slippage, "reason": reason,
        }),
    ))


async def prune_old_activity() -> None:
    """Delete oldest rows if table exceeds MAX_ACTIVITY_ROWS."""
    try:
        from sqlalchemy import delete, func, select
        async with async_session() as session:
            count = await session.scalar(select(func.count(BotActivity.id)))
            if count and count > MAX_ACTIVITY_ROWS:
                excess = count - MAX_ACTIVITY_ROWS
                oldest = await session.execute(
                    select(BotActivity.id)
                    .order_by(BotActivity.timestamp.asc())
                    .limit(excess)
                )
                ids_to_delete = [row[0] for row in oldest.all()]
                if ids_to_delete:
                    await session.execute(
                        delete(BotActivity).where(BotActivity.id.in_(ids_to_delete))
                    )
                    await session.commit()
    except Exception as e:
        logger.debug("activity_prune_failed", error=str(e))
