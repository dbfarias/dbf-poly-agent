"""Bot activity logger — writes structured events to the bot_activity table.

These events power the Activity page in the dashboard, providing a
human-readable log of what the bot did and why.
"""

import json

import structlog

from bot.data.database import async_session
from bot.data.models import BotActivity, MarketScan

logger = structlog.get_logger()

# Maximum rows kept in each table (auto-pruned periodically).
MAX_ACTIVITY_ROWS = 5000
MAX_SCAN_ROWS = 5000


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


async def log_rebalance(
    closed_market_id: str,
    closed_question: str,
    closed_strategy: str,
    closed_pnl: float,
    new_market_id: str,
    new_question: str,
    new_strategy: str,
    new_edge: float,
) -> None:
    """Log a position rebalance: closed a loser to make room for a better signal."""
    sign = "+" if closed_pnl >= 0 else ""
    await _write(BotActivity(
        event_type="rebalance",
        level="info",
        title=f"Rebalanced: closed {closed_strategy} for {new_strategy}",
        detail=(
            f"Closed: {closed_question[:80]} ({sign}${closed_pnl:.2f})\n"
            f"Opened room for: {new_question[:80]} (edge {new_edge:.1%})"
        ),
        market_id=new_market_id,
        strategy=new_strategy,
        metadata_json=_meta({
            "closed_market_id": closed_market_id,
            "closed_strategy": closed_strategy,
            "closed_pnl": closed_pnl,
            "new_market_id": new_market_id,
            "new_strategy": new_strategy,
            "new_edge": new_edge,
        }),
    ))


async def log_strategy_paused(
    strategy: str,
    win_rate: float,
    total_pnl: float,
) -> None:
    """Log when the learner auto-pauses a strategy."""
    await _write(BotActivity(
        event_type="bot_event",
        level="warning",
        title=f"Strategy paused: {strategy}",
        detail=(
            f"Auto-paused for 24h due to poor performance.\n"
            f"Last {10} trades: {win_rate:.0%} win rate, ${total_pnl:+.2f} PnL"
        ),
        strategy=strategy,
        metadata_json=_meta({
            "reason": "auto_pause",
            "win_rate": win_rate,
            "total_pnl": total_pnl,
        }),
    ))


async def log_risk_limit_hit(
    limit_type: str,
    current: float,
    threshold: float,
) -> None:
    """Log when a risk limit is breached."""
    await _write(BotActivity(
        event_type="bot_event",
        level="error",
        title=f"Risk limit hit: {limit_type}",
        detail=f"{limit_type}: {current:.1%} exceeds {threshold:.1%} threshold.",
        metadata_json=_meta({
            "limit_type": limit_type,
            "current": current,
            "threshold": threshold,
        }),
    ))


async def log_daily_target_reached(
    equity: float,
    daily_pnl: float,
    target_pct: float,
) -> None:
    """Log when the daily profit target is achieved."""
    await _write(BotActivity(
        event_type="bot_event",
        level="success",
        title="Daily target reached!",
        detail=f"PnL: ${daily_pnl:+.2f} on ${equity:.2f} equity (target: {target_pct:.1%})",
        metadata_json=_meta({
            "equity": equity,
            "daily_pnl": daily_pnl,
            "target_pct": target_pct,
        }),
    ))


async def log_llm_debate(
    strategy: str,
    market_id: str,
    question: str,
    approved: bool,
    proposer_verdict: str,
    proposer_confidence: float,
    proposer_reasoning: str,
    challenger_verdict: str,
    challenger_risk: str,
    challenger_objections: str,
    edge: float,
    price: float,
    cost_usd: float,
) -> None:
    """Log an LLM debate result (both approved and rejected)."""
    icon = "Approved" if approved else "Rejected"
    level = "success" if approved else "warning"
    await _write(BotActivity(
        event_type="llm_debate",
        level=level,
        title=f"AI Debate {icon}: {question[:50]}",
        detail=(
            f"Strategy: {strategy} | Price: ${price:.3f} | Edge: {edge:.1%}\n"
            f"Proposer: {proposer_verdict} (conf {proposer_confidence:.0%}) — {proposer_reasoning}\n"
            f"Challenger: {challenger_verdict} (risk {challenger_risk}) — {challenger_objections}\n"
            f"Cost: ${cost_usd:.4f}"
        ),
        market_id=market_id,
        strategy=strategy,
        metadata_json=_meta({
            "approved": approved,
            "proposer_verdict": proposer_verdict,
            "proposer_confidence": proposer_confidence,
            "proposer_reasoning": proposer_reasoning,
            "challenger_verdict": challenger_verdict,
            "challenger_risk": challenger_risk,
            "challenger_objections": challenger_objections,
            "edge": edge, "price": price, "cost_usd": cost_usd,
        }),
    ))


async def log_llm_review(
    market_id: str,
    question: str,
    strategy: str,
    verdict: str,
    urgency: str,
    reasoning: str,
    entry_price: float,
    current_price: float,
    unrealized_pnl: float,
    cost_usd: float,
) -> None:
    """Log an LLM position review result."""
    level = "warning" if verdict == "EXIT" else "info"
    pnl_sign = "+" if unrealized_pnl >= 0 else ""
    await _write(BotActivity(
        event_type="llm_review",
        level=level,
        title=f"AI Review: {verdict} ({urgency}) — {question[:45]}",
        detail=(
            f"Strategy: {strategy}\n"
            f"Entry: ${entry_price:.3f} → Current: ${current_price:.3f} "
            f"(PnL: {pnl_sign}${unrealized_pnl:.2f})\n"
            f"Verdict: {verdict} | Urgency: {urgency}\n"
            f"Reasoning: {reasoning}\n"
            f"Cost: ${cost_usd:.4f}"
        ),
        market_id=market_id,
        strategy=strategy,
        metadata_json=_meta({
            "verdict": verdict, "urgency": urgency, "reasoning": reasoning,
            "entry_price": entry_price, "current_price": current_price,
            "unrealized_pnl": unrealized_pnl, "cost_usd": cost_usd,
        }),
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
    """Delete oldest rows if tables exceed their max row limits.

    Uses subquery DELETE to avoid fetch-then-delete round trips.
    Prunes both BotActivity and MarketScan tables.
    """
    try:
        from sqlalchemy import delete, func, select
        async with async_session() as session:
            # Prune BotActivity
            count = await session.scalar(select(func.count(BotActivity.id)))
            if count and count > MAX_ACTIVITY_ROWS:
                excess = count - MAX_ACTIVITY_ROWS
                subq = (
                    select(BotActivity.id)
                    .order_by(BotActivity.timestamp.asc())
                    .limit(excess)
                    .scalar_subquery()
                )
                await session.execute(
                    delete(BotActivity).where(BotActivity.id.in_(subq))
                )

            # Prune MarketScan
            scan_count = await session.scalar(select(func.count(MarketScan.id)))
            if scan_count and scan_count > MAX_SCAN_ROWS:
                excess = scan_count - MAX_SCAN_ROWS
                scan_subq = (
                    select(MarketScan.id)
                    .order_by(MarketScan.scanned_at.asc())
                    .limit(excess)
                    .scalar_subquery()
                )
                await session.execute(
                    delete(MarketScan).where(MarketScan.id.in_(scan_subq))
                )

            await session.commit()
    except Exception as e:
        logger.debug("activity_prune_failed", error=str(e))
