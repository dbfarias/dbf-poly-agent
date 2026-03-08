"""Daily market report generator — pure data aggregation, no LLM calls."""

from collections import Counter

import structlog

logger = structlog.get_logger()

_MAX_TELEGRAM_LEN = 4000


async def generate_daily_report(
    research_cache,
    portfolio,
    learner,
    research_engine,
) -> str:
    """Generate an HTML-formatted daily report for Telegram.

    Sections:
    1. Portfolio Summary
    2. Top 5 Markets by sentiment strength
    3. Strategy Health
    4. Risk Alerts
    """
    sections: list[str] = []

    # --- 1. Portfolio Summary ---
    overview = portfolio.get_overview()
    equity = overview.get("total_equity", 0.0)
    day_start = overview.get("day_start_equity", equity)
    daily_pnl = equity - day_start
    daily_return = daily_pnl / day_start if day_start > 0 else 0.0
    positions_count = len(portfolio.positions)

    sections.append(
        f"<b>Portfolio Summary</b>\n"
        f"Equity: <code>${equity:.2f}</code>\n"
        f"Daily PnL: <code>${daily_pnl:+.2f} ({daily_return:+.1%})</code>\n"
        f"Open Positions: {positions_count}"
    )

    # --- 2. Top 5 Markets ---
    all_results = research_cache.get_all()
    if all_results:
        # Sort by absolute sentiment score (strongest signals first)
        sorted_results = sorted(
            all_results,
            key=lambda r: abs(r.sentiment_score),
            reverse=True,
        )[:5]

        market_lines: list[str] = []
        for r in sorted_results:
            # Find the question from the market cache via research_engine
            question = _get_question(research_engine, r.market_id)
            sentiment_dir = "+" if r.sentiment_score >= 0 else ""
            cat_label = f" [{r.market_category}]" if r.market_category else ""
            market_lines.append(
                f"  {sentiment_dir}{r.sentiment_score:.2f} "
                f"(conf {r.confidence:.0%}){cat_label}\n"
                f"  <i>{question[:70]}</i>"
            )

        sections.append(
            "<b>Top 5 Markets</b>\n" + "\n".join(market_lines)
        )
    else:
        sections.append("<b>Top 5 Markets</b>\nNo research data yet.")

    # --- 3. Strategy Health ---
    strategy_lines: list[str] = []
    stats = learner._stats  # dict[(strategy, category), StrategyStats]
    # Aggregate by strategy
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

    for strategy, agg in sorted(strategy_totals.items()):
        wr = agg["wins"] / agg["total"] if agg["total"] > 0 else 0.0
        paused = strategy in getattr(learner, "_paused_strategies", {})
        status = " (PAUSED)" if paused else ""
        strategy_lines.append(
            f"  {strategy}{status}: "
            f"WR {wr:.0%} | PnL ${agg['pnl']:+.2f} | {agg['total']} trades"
        )

    if strategy_lines:
        sections.append(
            "<b>Strategy Health</b>\n" + "\n".join(strategy_lines)
        )
    else:
        sections.append("<b>Strategy Health</b>\nNo strategy data yet.")

    # --- 4. Risk Alerts ---
    alerts: list[str] = []

    # Check category concentration
    category_counts: Counter[str] = Counter()
    for pos in portfolio.positions:
        cat = getattr(pos, "category", "unknown")
        category_counts[cat] += 1

    for cat, count in category_counts.items():
        if count > 3:
            alerts.append(f"  Category <b>{cat}</b>: {count} positions (>3)")

    # Check daily PnL
    if day_start > 0 and daily_return < -0.01:
        alerts.append(
            f"  Daily PnL <b>{daily_return:+.1%}</b> below -1% threshold"
        )

    if alerts:
        sections.append("<b>Risk Alerts</b>\n" + "\n".join(alerts))
    else:
        sections.append("<b>Risk Alerts</b>\nNo alerts.")

    # --- Assemble ---
    report = "\n\n".join(sections)

    # Truncate to Telegram limit
    if len(report) > _MAX_TELEGRAM_LEN:
        report = report[: _MAX_TELEGRAM_LEN - 3] + "..."

    return report


def _get_question(research_engine, market_id: str) -> str:
    """Get market question from cache, with fallback."""
    try:
        market = research_engine.market_cache.get_market(market_id)
        if market is not None:
            return market.question
    except Exception:
        pass
    return market_id[:30]
