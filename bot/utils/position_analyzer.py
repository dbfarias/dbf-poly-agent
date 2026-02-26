"""LLM-based position analysis for EXIT alert notifications.

Uses Claude Haiku to evaluate each open position and return a verdict.
Called by TradingEngine every 30 cycles (~30 min) to detect positions
that should be manually exited.
"""

import structlog

from bot.config import settings

logger = structlog.get_logger()

_MODEL = "claude-haiku-4-5-20251001"
_TIMEOUT = 15


async def analyze_position_for_exit(
    question: str,
    outcome: str,
    avg_price: float,
    current_price: float,
    size: float,
    unrealized_pnl: float,
    days_to_expiry: float | None = None,
) -> tuple[str, str, str]:
    """Analyze a single position and return (verdict, confidence, reason).

    verdict: 'EXIT' or 'HOLD'
    confidence: 'High', 'Medium', or 'Low'
    reason: one-sentence explanation
    """
    api_key = settings.anthropic_api_key
    if not api_key:
        return "HOLD", "Low", ""

    cost_basis = avg_price * size
    pnl_pct = (unrealized_pnl / cost_basis * 100) if cost_basis > 0 else 0.0
    expiry_str = f"{days_to_expiry:.0f} days" if days_to_expiry is not None else "unknown"

    prompt = (
        f"You are a prediction market position analyst.\n"
        f"Analyze this position and decide: EXIT now or HOLD.\n\n"
        f"Market: {question}\n"
        f"Outcome held: {outcome}\n"
        f"Entry: ${avg_price:.3f} | Current: ${current_price:.3f}\n"
        f"PnL: ${unrealized_pnl:+.2f} ({pnl_pct:+.1f}%)\n"
        f"Size: {size:.0f} shares | Time to expiry: {expiry_str}\n\n"
        f"Reply ONLY in this exact format:\n"
        f"VERDICT: EXIT or HOLD\n"
        f"CONFIDENCE: High, Medium, or Low\n"
        f"REASON: one sentence\n"
    )

    try:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=api_key, timeout=_TIMEOUT)
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=120,
            messages=[{"role": "user", "content": prompt}],
        )
        text = response.content[0].text.strip()

        verdict = "HOLD"
        confidence = "Low"
        reason = ""

        for line in text.splitlines():
            if line.startswith("VERDICT:"):
                v = line.split(":", 1)[1].strip().upper()
                verdict = "EXIT" if "EXIT" in v else "HOLD"
            elif line.startswith("CONFIDENCE:"):
                c = line.split(":", 1)[1].strip().upper()
                if "HIGH" in c:
                    confidence = "High"
                elif "MEDIUM" in c:
                    confidence = "Medium"
            elif line.startswith("REASON:"):
                reason = line.split(":", 1)[1].strip()

        logger.debug(
            "position_analyzed",
            question=question[:50],
            verdict=verdict,
            confidence=confidence,
        )
        return verdict, confidence, reason

    except Exception as e:
        logger.debug("position_analysis_failed", error=str(e))
        return "HOLD", "Low", ""
