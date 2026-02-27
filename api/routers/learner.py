"""Learner visibility endpoints — exposes adaptive learning state."""

from datetime import datetime, timedelta, timezone

from fastapi import APIRouter, Depends

from api.dependencies import get_engine
from api.middleware import verify_api_key

router = APIRouter(prefix="/api/learner", tags=["learner"])


@router.get("/multipliers")
async def get_multipliers(_: str = Depends(verify_api_key)):
    """Return current edge multipliers and category confidences.

    Shows which strategy+category combos are being relaxed or tightened
    by the learner, plus per-category confidence scores.
    """
    engine = get_engine()
    learner = engine.learner
    adjustments = learner._last_adjustments

    if adjustments is None:
        return {
            "edge_multipliers": [],
            "category_confidences": [],
            "paused_strategies": [],
            "last_computed": None,
        }

    # Format edge multipliers for JSON (tuple keys -> objects)
    edge_list = []
    for (strategy, category), multiplier in adjustments.edge_multipliers.items():
        stats = learner._stats.get((strategy, category))
        edge_list.append({
            "strategy": strategy,
            "category": category,
            "multiplier": round(multiplier, 2),
            "win_rate": round(stats.actual_win_rate, 3) if stats else None,
            "total_trades": stats.total_trades if stats else 0,
            "total_pnl": round(stats.total_pnl, 4) if stats else 0.0,
            "avg_edge": round(stats.avg_edge, 4) if stats else 0.0,
            "status": _multiplier_status(multiplier),
        })

    # Category confidences
    cat_list = []
    for category, confidence in adjustments.category_confidences.items():
        # Aggregate stats for this category
        cat_trades = [
            s for (_, c), s in learner._stats.items() if c == category
        ]
        total = sum(s.total_trades for s in cat_trades)
        wins = sum(s.winning_trades for s in cat_trades)
        pnl = sum(s.total_pnl for s in cat_trades)
        cat_list.append({
            "category": category,
            "confidence": round(confidence, 2),
            "total_trades": total,
            "win_rate": round(wins / total, 3) if total > 0 else 0.0,
            "total_pnl": round(pnl, 4),
            "status": _confidence_status(confidence),
        })

    return {
        "edge_multipliers": edge_list,
        "category_confidences": cat_list,
        "paused_strategies": list(adjustments.paused_strategies),
        "last_computed": (
            learner._last_computed.isoformat()
            if learner._last_computed
            else None
        ),
    }


@router.get("/calibration")
async def get_calibration(_: str = Depends(verify_api_key)):
    """Return confidence calibration data.

    Shows how well the bot's probability estimates match actual outcomes.
    Each bucket compares estimated probability vs actual win rate.
    """
    engine = get_engine()
    learner = engine.learner
    adjustments = learner._last_adjustments

    if adjustments is None:
        return {"buckets": [], "last_computed": None}

    # Build calibration data with more context
    buckets = []
    bucket_ranges = {
        "80-85": (0.80, 0.85),
        "85-90": (0.85, 0.90),
        "90-95": (0.90, 0.95),
        "95-99": (0.95, 0.99),
    }

    # Re-process trades to get counts per bucket
    from bot.data.database import async_session
    from bot.data.repositories import TradeRepository

    async with async_session() as session:
        repo = TradeRepository(session)
        trades = await repo.get_recent(limit=500)

    cutoff = datetime.now(timezone.utc) - timedelta(days=30)
    recent = [
        t for t in trades
        if t.status in ("filled", "completed") and t.created_at >= cutoff
    ]

    for label, (low, high) in bucket_ranges.items():
        bucket_trades = [
            t for t in recent if low <= t.estimated_prob < high
        ]
        total = len(bucket_trades)
        wins = sum(1 for t in bucket_trades if t.pnl > 0)
        avg_estimated = (
            sum(t.estimated_prob for t in bucket_trades) / total
            if total > 0
            else (low + high) / 2
        )
        actual_win_rate = wins / total if total > 0 else 0.0
        calibration_ratio = adjustments.calibration.get(label, 1.0)

        buckets.append({
            "bucket": label,
            "estimated_prob": round(avg_estimated * 100, 1),
            "actual_win_rate": round(actual_win_rate * 100, 1),
            "calibration_ratio": round(calibration_ratio, 3),
            "total_trades": total,
            "wins": wins,
            "losses": total - wins,
            "is_calibrated": 0.8 <= calibration_ratio <= 1.2,
        })

    return {
        "buckets": buckets,
        "last_computed": (
            learner._last_computed.isoformat()
            if learner._last_computed
            else None
        ),
    }


@router.get("/pauses")
async def get_pause_history(_: str = Depends(verify_api_key)):
    """Return strategy pause state and history.

    Shows which strategies are currently paused, when they were paused,
    and when the cooldown expires.
    """
    engine = get_engine()
    learner = engine.learner
    adjustments = learner._last_adjustments

    # Current pause state
    pauses = []
    for strategy, paused_at in learner._paused_strategies.items():
        elapsed_hours = (
            (datetime.now(timezone.utc) - paused_at).total_seconds() / 3600
        )
        remaining_hours = max(0, 24 - elapsed_hours)
        expires_at = paused_at + timedelta(hours=24)
        pauses.append({
            "strategy": strategy,
            "paused_at": paused_at.isoformat(),
            "elapsed_hours": round(elapsed_hours, 1),
            "remaining_hours": round(remaining_hours, 1),
            "expires_at": expires_at.isoformat(),
        })

    # All strategies status
    all_strategies = ["time_decay", "arbitrage", "value_betting", "market_making"]
    strategy_status = []
    for s in all_strategies:
        is_paused = (
            adjustments is not None and s in adjustments.paused_strategies
        )
        pause_info = next((p for p in pauses if p["strategy"] == s), None)
        strategy_status.append({
            "strategy": s,
            "is_paused": is_paused,
            "pause_info": pause_info,
        })

    return {
        "strategies": strategy_status,
        "active_pauses": len(pauses),
        "last_computed": (
            learner._last_computed.isoformat()
            if learner._last_computed
            else None
        ),
    }


def _multiplier_status(multiplier: float) -> str:
    """Human-readable status from edge multiplier."""
    if multiplier <= 0.8:
        return "relaxed"
    elif multiplier <= 1.0:
        return "normal"
    elif multiplier <= 1.2:
        return "cautious"
    else:
        return "strict"


def _confidence_status(confidence: float) -> str:
    """Human-readable status from category confidence."""
    if confidence >= 1.2:
        return "boosted"
    elif confidence >= 1.0:
        return "neutral"
    elif confidence >= 0.8:
        return "cautious"
    else:
        return "penalized"
