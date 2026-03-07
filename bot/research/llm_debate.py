"""LLM debate gate — Proposer vs Challenger pattern for trade signals."""

import time
from dataclasses import dataclass

import structlog

from bot.config import settings

logger = structlog.get_logger()

_MODEL = "claude-haiku-4-5-20251001"
_TIMEOUT = 15.0
_MAX_TOKENS = 300

_PROPOSER_SYSTEM = (
    "You are a prediction market trading analyst. You evaluate trade opportunities "
    "on Polymarket. Given market data and a trading signal, decide if this is a "
    "good trade. Consider:\n"
    "- Is the edge real or noise?\n"
    "- Does the news/sentiment support the direction?\n"
    "- Is the timing right (resolution date, current events)?\n"
    "- What could go wrong?\n\n"
    "Respond in this exact format:\n"
    "VERDICT: BUY or PASS\n"
    "CONFIDENCE: 0.0 to 1.0\n"
    "REASONING: 1-2 sentences explaining your decision"
)

_CHALLENGER_SYSTEM = (
    "You are a skeptical risk analyst reviewing a proposed prediction market trade. "
    "Your job is to find weaknesses in the proposal. Challenge the reasoning:\n"
    "- Is the edge calculation reliable or based on flawed assumptions?\n"
    "- Are there risks the proposer missed (event timing, market manipulation, "
    "information asymmetry)?\n"
    "- Could the market price already reflect the news?\n"
    "- Is the position size appropriate for the risk?\n\n"
    "Respond in this exact format:\n"
    "VERDICT: APPROVE or REJECT\n"
    "RISK_LEVEL: LOW, MEDIUM, or HIGH\n"
    "OBJECTIONS: 1-2 sentences with specific concerns (or 'None' if truly solid)"
)

_POSITION_REVIEWER_SYSTEM = (
    "You are a portfolio analyst reviewing an open prediction market position. "
    "Given current market data, decide if we should HOLD or EXIT.\n"
    "Consider:\n"
    "- Has the thesis changed since entry?\n"
    "- Is the current price reflecting new information?\n"
    "- Are we better off freeing this capital for other opportunities?\n"
    "- How close is resolution and does that change the risk?\n\n"
    "Respond in this exact format:\n"
    "VERDICT: HOLD or EXIT\n"
    "URGENCY: LOW, MEDIUM, or HIGH\n"
    "REASONING: 1-2 sentences"
)


@dataclass(frozen=True)
class DebateResult:
    """Result of a Proposer vs Challenger debate."""

    approved: bool
    proposer_verdict: str  # "BUY" or "PASS"
    proposer_confidence: float
    proposer_reasoning: str
    challenger_verdict: str  # "APPROVE" or "REJECT"
    challenger_risk: str  # "LOW", "MEDIUM", "HIGH"
    challenger_objections: str
    total_cost_usd: float
    elapsed_s: float


@dataclass(frozen=True)
class ReviewResult:
    """Result of an LLM position review."""

    should_exit: bool
    urgency: str  # "LOW", "MEDIUM", "HIGH"
    reasoning: str
    cost_usd: float


class LlmCostTracker:
    """Track daily LLM spend and enforce budget cap."""

    def __init__(self, daily_budget: float = 3.0):
        self.daily_budget = daily_budget
        self._today: str = ""
        self._today_cost: float = 0.0

    def add(self, cost: float) -> None:
        today = _today_key()
        if today != self._today:
            self._today = today
            self._today_cost = 0.0
        self._today_cost += cost

    @property
    def today_cost(self) -> float:
        if _today_key() != self._today:
            return 0.0
        return self._today_cost

    @property
    def budget_remaining(self) -> float:
        return max(0.0, self.daily_budget - self.today_cost)

    @property
    def is_over_budget(self) -> bool:
        return self.today_cost >= self.daily_budget


def _today_key() -> str:
    from datetime import datetime, timezone
    return datetime.now(timezone.utc).strftime("%Y-%m-%d")


# Global cost tracker (shared across debate + sentiment + reviewer)
cost_tracker = LlmCostTracker(daily_budget=3.0)


async def debate_signal(
    question: str,
    strategy: str,
    edge: float,
    price: float,
    estimated_prob: float,
    confidence: float,
    reasoning: str,
    sentiment_score: float | None = None,
    hours_to_resolution: float | None = None,
) -> DebateResult | None:
    """Run a Proposer vs Challenger debate on a trade signal.

    Returns DebateResult, or None if debate couldn't run (fallback to approve).
    """
    if cost_tracker.is_over_budget:
        logger.info("llm_debate_budget_exhausted", today_cost=cost_tracker.today_cost)
        return None

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        return None

    api_key = settings.anthropic_api_key
    if not api_key:
        return None

    client = AsyncAnthropic(api_key=api_key, timeout=_TIMEOUT)
    start = time.monotonic()
    total_cost = 0.0

    # --- PROPOSER ---
    proposer_msg = _format_proposer_prompt(
        question, strategy, edge, price, estimated_prob,
        confidence, reasoning, sentiment_score, hours_to_resolution,
    )

    try:
        prop_resp = await client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_PROPOSER_SYSTEM,
            messages=[{"role": "user", "content": proposer_msg}],
        )
        prop_text = prop_resp.content[0].text.strip()
        prop_cost = _calc_cost(prop_resp.usage.input_tokens, prop_resp.usage.output_tokens)
        total_cost += prop_cost
    except Exception as e:
        logger.warning("llm_proposer_error", error=str(e))
        return None

    prop_verdict, prop_confidence, prop_reasoning = _parse_proposer(prop_text)

    # If proposer says PASS, skip challenger (save cost)
    if prop_verdict == "PASS":
        elapsed = time.monotonic() - start
        cost_tracker.add(total_cost)
        logger.info(
            "llm_debate_complete",
            question=question[:60],
            proposer="PASS",
            challenger="skipped",
            approved=False,
            cost_usd=round(total_cost, 5),
        )
        return DebateResult(
            approved=False,
            proposer_verdict="PASS",
            proposer_confidence=prop_confidence,
            proposer_reasoning=prop_reasoning,
            challenger_verdict="skipped",
            challenger_risk="N/A",
            challenger_objections="Proposer passed — no challenger needed",
            total_cost_usd=total_cost,
            elapsed_s=round(elapsed, 2),
        )

    # --- CHALLENGER ---
    challenger_msg = _format_challenger_prompt(
        question, strategy, edge, price, estimated_prob,
        prop_reasoning, sentiment_score, hours_to_resolution,
    )

    try:
        chal_resp = await client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_CHALLENGER_SYSTEM,
            messages=[{"role": "user", "content": challenger_msg}],
        )
        chal_text = chal_resp.content[0].text.strip()
        chal_cost = _calc_cost(chal_resp.usage.input_tokens, chal_resp.usage.output_tokens)
        total_cost += chal_cost
    except Exception as e:
        logger.warning("llm_challenger_error", error=str(e))
        # Challenger failed — let proposer's BUY stand
        elapsed = time.monotonic() - start
        cost_tracker.add(total_cost)
        return DebateResult(
            approved=True,
            proposer_verdict="BUY",
            proposer_confidence=prop_confidence,
            proposer_reasoning=prop_reasoning,
            challenger_verdict="error",
            challenger_risk="UNKNOWN",
            challenger_objections=f"Challenger error: {e}",
            total_cost_usd=total_cost,
            elapsed_s=round(elapsed, 2),
        )

    chal_verdict, chal_risk, chal_objections = _parse_challenger(chal_text)

    # Decision: approved only if proposer says BUY AND challenger doesn't REJECT
    approved = (prop_verdict == "BUY" and chal_verdict != "REJECT")

    elapsed = time.monotonic() - start
    cost_tracker.add(total_cost)

    logger.info(
        "llm_debate_complete",
        question=question[:60],
        proposer=prop_verdict,
        proposer_conf=prop_confidence,
        challenger=chal_verdict,
        challenger_risk=chal_risk,
        approved=approved,
        cost_usd=round(total_cost, 5),
        elapsed_s=round(elapsed, 2),
    )

    return DebateResult(
        approved=approved,
        proposer_verdict=prop_verdict,
        proposer_confidence=prop_confidence,
        proposer_reasoning=prop_reasoning,
        challenger_verdict=chal_verdict,
        challenger_risk=chal_risk,
        challenger_objections=chal_objections,
        total_cost_usd=total_cost,
        elapsed_s=round(elapsed, 2),
    )


async def review_position(
    question: str,
    strategy: str,
    entry_price: float,
    current_price: float,
    size: float,
    age_hours: float,
    unrealized_pnl: float,
    hours_to_resolution: float | None = None,
    sentiment_score: float | None = None,
) -> ReviewResult | None:
    """LLM review of an open position — should we HOLD or EXIT?

    Returns ReviewResult, or None if review couldn't run.
    """
    if cost_tracker.is_over_budget:
        return None

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        return None

    api_key = settings.anthropic_api_key
    if not api_key:
        return None

    pnl_pct = ((current_price - entry_price) / entry_price * 100) if entry_price > 0 else 0.0
    user_msg = (
        f"Position review:\n"
        f"Market: {question}\n"
        f"Strategy: {strategy}\n"
        f"Entry price: ${entry_price:.3f} → Current: ${current_price:.3f} "
        f"({pnl_pct:+.1f}%)\n"
        f"Size: {size:.1f} shares, Unrealized PnL: ${unrealized_pnl:+.2f}\n"
        f"Position age: {age_hours:.1f} hours\n"
    )
    if hours_to_resolution is not None:
        user_msg += f"Hours to resolution: {hours_to_resolution:.1f}\n"
    if sentiment_score is not None:
        user_msg += f"Current news sentiment: {sentiment_score:+.2f} (-1=bearish, +1=bullish)\n"
    user_msg += "\nShould we HOLD or EXIT this position?"

    try:
        client = AsyncAnthropic(api_key=api_key, timeout=_TIMEOUT)
        start = time.monotonic()
        resp = await client.messages.create(
            model=_MODEL,
            max_tokens=200,
            system=_POSITION_REVIEWER_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text.strip()
        cost = _calc_cost(resp.usage.input_tokens, resp.usage.output_tokens)
        cost_tracker.add(cost)
        elapsed = time.monotonic() - start

        verdict, urgency, reasoning = _parse_reviewer(text)

        logger.info(
            "llm_position_review",
            question=question[:60],
            verdict=verdict,
            urgency=urgency,
            cost_usd=round(cost, 5),
            elapsed_s=round(elapsed, 2),
        )

        return ReviewResult(
            should_exit=(verdict == "EXIT"),
            urgency=urgency,
            reasoning=reasoning,
            cost_usd=cost,
        )
    except Exception as e:
        logger.warning("llm_position_review_error", error=str(e))
        return None


def _format_proposer_prompt(
    question: str, strategy: str, edge: float, price: float,
    estimated_prob: float, confidence: float, reasoning: str,
    sentiment_score: float | None, hours_to_resolution: float | None,
) -> str:
    msg = (
        f"Trade opportunity:\n"
        f"Market: {question}\n"
        f"Strategy: {strategy}\n"
        f"Market price: ${price:.3f}\n"
        f"Our estimated probability: {estimated_prob:.1%}\n"
        f"Edge: {edge:.1%}\n"
        f"Signal confidence: {confidence:.2f}\n"
        f"Strategy reasoning: {reasoning}\n"
    )
    if hours_to_resolution is not None:
        msg += f"Hours until resolution: {hours_to_resolution:.1f}\n"
    if sentiment_score is not None:
        msg += f"News sentiment: {sentiment_score:+.2f} (-1=bearish, +1=bullish)\n"
    msg += "\nShould we BUY or PASS?"
    return msg


def _format_challenger_prompt(
    question: str, strategy: str, edge: float, price: float,
    estimated_prob: float, proposer_reasoning: str,
    sentiment_score: float | None, hours_to_resolution: float | None,
) -> str:
    msg = (
        f"Proposed trade to review:\n"
        f"Market: {question}\n"
        f"Strategy: {strategy}\n"
        f"Market price: ${price:.3f}, Estimated prob: {estimated_prob:.1%}\n"
        f"Edge: {edge:.1%}\n"
        f"Proposer's case: {proposer_reasoning}\n"
    )
    if hours_to_resolution is not None:
        msg += f"Hours until resolution: {hours_to_resolution:.1f}\n"
    if sentiment_score is not None:
        msg += f"News sentiment: {sentiment_score:+.2f}\n"
    msg += "\nAPPROVE or REJECT this trade?"
    return msg


def _calc_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * 0.80 + output_tokens * 4.00) / 1_000_000


def _parse_proposer(text: str) -> tuple[str, float, str]:
    """Parse proposer response. Returns (verdict, confidence, reasoning)."""
    verdict = "PASS"
    confidence = 0.5
    reasoning = text

    for line in text.split("\n"):
        upper = line.upper().strip()
        if upper.startswith("VERDICT:"):
            val = upper.split(":", 1)[1].strip()
            if "BUY" in val:
                verdict = "BUY"
            else:
                verdict = "PASS"
        elif upper.startswith("CONFIDENCE:"):
            try:
                confidence = float(line.split(":", 1)[1].strip())
                confidence = max(0.0, min(1.0, confidence))
            except ValueError:
                pass
        elif upper.startswith("REASONING:"):
            reasoning = line.split(":", 1)[1].strip()

    return verdict, confidence, reasoning


def _parse_challenger(text: str) -> tuple[str, str, str]:
    """Parse challenger response. Returns (verdict, risk_level, objections)."""
    verdict = "APPROVE"
    risk = "MEDIUM"
    objections = text

    for line in text.split("\n"):
        upper = line.upper().strip()
        if upper.startswith("VERDICT:"):
            val = upper.split(":", 1)[1].strip()
            if "REJECT" in val:
                verdict = "REJECT"
            else:
                verdict = "APPROVE"
        elif upper.startswith("RISK_LEVEL:") or upper.startswith("RISK LEVEL:"):
            val = upper.split(":", 1)[1].strip()
            if "HIGH" in val:
                risk = "HIGH"
            elif "LOW" in val:
                risk = "LOW"
            else:
                risk = "MEDIUM"
        elif upper.startswith("OBJECTIONS:"):
            objections = line.split(":", 1)[1].strip()

    return verdict, risk, objections


def _parse_reviewer(text: str) -> tuple[str, str, str]:
    """Parse reviewer response. Returns (verdict, urgency, reasoning)."""
    verdict = "HOLD"
    urgency = "LOW"
    reasoning = text

    for line in text.split("\n"):
        upper = line.upper().strip()
        if upper.startswith("VERDICT:"):
            val = upper.split(":", 1)[1].strip()
            if "EXIT" in val:
                verdict = "EXIT"
            else:
                verdict = "HOLD"
        elif upper.startswith("URGENCY:"):
            val = upper.split(":", 1)[1].strip()
            if "HIGH" in val:
                urgency = "HIGH"
            elif "MEDIUM" in val:
                urgency = "MEDIUM"
            else:
                urgency = "LOW"
        elif upper.startswith("REASONING:"):
            reasoning = line.split(":", 1)[1].strip()

    return verdict, urgency, reasoning
