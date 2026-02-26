"""Daily report endpoint — JSON version of the Telegram daily report."""

from collections import Counter
from datetime import datetime, timezone

from fastapi import APIRouter, Depends

from api.dependencies import get_engine
from api.middleware import verify_api_key

router = APIRouter(prefix="/api/report", tags=["report"])


def _get_question(research_engine, market_id: str) -> str:
    """Get market question from cache, with fallback."""
    try:
        market = research_engine.market_cache.get_market(market_id)
        if market is not None:
            return market.question
    except Exception:
        pass
    return market_id[:30]


def _get_end_date(research_engine, market_id: str) -> str | None:
    """Get market end date from cache."""
    try:
        market = research_engine.market_cache.get_market(market_id)
        if market is not None and market.end_date_iso:
            return market.end_date_iso
    except Exception:
        pass
    return None


@router.get("/daily")
async def get_daily_report(_: str = Depends(verify_api_key)):
    """Return the daily report data as structured JSON.

    Mirrors the Telegram report but with richer data for the dashboard.
    """
    engine = get_engine()
    portfolio = engine.portfolio
    learner = engine.learner
    research_cache = engine.research_cache
    research_engine = engine.research_engine

    # --- Portfolio Summary ---
    overview = portfolio.get_overview()
    equity = overview.get("total_equity", 0.0)
    day_start = overview.get("day_start_equity", equity)
    daily_pnl = equity - day_start
    daily_return = daily_pnl / day_start if day_start > 0 else 0.0

    portfolio_summary = {
        "total_equity": round(equity, 2),
        "cash_balance": round(overview.get("cash_balance", 0.0), 2),
        "positions_value": round(overview.get("positions_value", 0.0), 2),
        "day_start_equity": round(day_start, 2),
        "daily_pnl": round(daily_pnl, 2),
        "daily_return_pct": round(daily_return * 100, 2),
        "open_positions": len(portfolio.positions),
        "trading_mode": overview.get("is_paper", True) and "paper" or "live",
        "daily_target_pct": overview.get("daily_target_pct", 1.0),
        "daily_progress_pct": round(overview.get("daily_progress_pct", 0.0), 1),
    }

    # --- Top Opportunities (by sentiment strength) ---
    all_results = research_cache.get_all()
    top_markets = []
    if all_results:
        sorted_results = sorted(
            all_results,
            key=lambda r: abs(r.sentiment_score),
            reverse=True,
        )[:10]

        for r in sorted_results:
            question = _get_question(research_engine, r.market_id)
            end_date = _get_end_date(research_engine, r.market_id)
            top_markets.append({
                "market_id": r.market_id,
                "question": question,
                "sentiment_score": round(r.sentiment_score, 4),
                "confidence": round(r.confidence, 2),
                "research_multiplier": round(r.research_multiplier, 3),
                "category": getattr(r, "market_category", ""),
                "article_count": len(r.news_items),
                "end_date": end_date,
                "is_volume_anomaly": getattr(r, "is_volume_anomaly", False),
                "whale_activity": getattr(r, "whale_activity", False),
                "updated_at": r.updated_at.isoformat(),
            })

    # --- Strategy Health ---
    strategies = []
    stats = learner._stats
    strategy_totals: dict[str, dict] = {}
    for (strategy, _category), s in stats.items():
        if strategy not in strategy_totals:
            strategy_totals[strategy] = {
                "total": 0, "wins": 0, "pnl": 0.0,
            }
        agg = strategy_totals[strategy]
        strategy_totals[strategy] = {
            "total": agg["total"] + s.total_trades,
            "wins": agg["wins"] + s.winning_trades,
            "pnl": agg["pnl"] + s.total_pnl,
        }

    paused_strategies = getattr(learner, "_paused_strategies", {})
    for strategy, agg in sorted(strategy_totals.items()):
        wr = agg["wins"] / agg["total"] if agg["total"] > 0 else 0.0
        is_paused = strategy in paused_strategies
        strategies.append({
            "name": strategy,
            "win_rate": round(wr * 100, 1),
            "total_pnl": round(agg["pnl"], 2),
            "total_trades": agg["total"],
            "is_paused": is_paused,
        })

    # --- Risk Alerts ---
    alerts: list[dict] = []

    # Category concentration
    category_counts: Counter[str] = Counter()
    for pos in portfolio.positions:
        cat = getattr(pos, "category", "unknown")
        category_counts[cat] += 1

    for cat, count in category_counts.items():
        if count > 3:
            alerts.append({
                "type": "category_concentration",
                "severity": "warning",
                "message": f"Category '{cat}' has {count} positions (>3)",
            })

    # Daily PnL warning
    if day_start > 0 and daily_return < -0.01:
        alerts.append({
            "type": "daily_loss",
            "severity": "danger",
            "message": f"Daily PnL {daily_return:+.1%} below -1% threshold",
        })

    # Drawdown warning
    current_dd = overview.get("current_drawdown_pct", 0.0)
    if isinstance(current_dd, (int, float)) and current_dd > 5.0:
        alerts.append({
            "type": "drawdown",
            "severity": "danger" if current_dd > 10.0 else "warning",
            "message": f"Current drawdown {current_dd:.1f}% from peak equity",
        })

    # Stuck positions
    stuck = getattr(engine.closer, "stuck_positions", set())
    if stuck:
        alerts.append({
            "type": "stuck_positions",
            "severity": "warning",
            "message": f"{len(stuck)} stuck position(s) — sell failures detected",
        })

    # --- Market Sentiment Overview ---
    sentiment_overview = {
        "avg_sentiment": 0.0, "total_markets": 0,
        "positive": 0, "negative": 0, "neutral": 0,
    }
    if all_results:
        scores = [r.sentiment_score for r in all_results]
        sentiment_overview = {
            "avg_sentiment": round(sum(scores) / len(scores), 4),
            "total_markets": len(scores),
            "positive": sum(1 for s in scores if s > 0.1),
            "negative": sum(1 for s in scores if s < -0.1),
            "neutral": sum(1 for s in scores if -0.1 <= s <= 0.1),
        }

    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "portfolio_summary": portfolio_summary,
        "top_markets": top_markets,
        "strategy_health": strategies,
        "risk_alerts": alerts,
        "sentiment_overview": sentiment_overview,
    }
