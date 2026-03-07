"""LLM debate gate — Proposer vs Challenger pattern for trade signals."""

import re
import time
from dataclasses import dataclass

import structlog

from bot.config import settings

logger = structlog.get_logger()

_MODEL = "claude-haiku-4-5-20251001"
_TIMEOUT = 15.0
_MAX_TOKENS = 300
_MAX_PROMPT_INPUT_LEN = 200
_MIN_CONVICTION_FOR_ANALYST = 0.4


def _sanitize_prompt_input(text: str, max_len: int = _MAX_PROMPT_INPUT_LEN) -> str:
    """Sanitize external text before interpolation into LLM prompts.

    Strips control characters, newlines (potential prompt injection),
    and truncates to max_len.
    """
    # Remove control chars and newlines that could inject prompt structure
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    # Collapse multiple spaces
    cleaned = re.sub(r" {2,}", " ", cleaned).strip()
    return cleaned[:max_len]

_PROPOSER_SYSTEM = (
    "You are an aggressive prediction market trader on Polymarket. You look for "
    "any reasonable edge and push to take it. You WANT to trade — that's how you "
    "make money. A 2-5% edge is worth taking, especially on short-resolution markets.\n\n"
    "Your bias is BUY. Only say PASS if the signal is clearly garbage:\n"
    "- Edge < 1% (too thin to overcome fees)\n"
    "- Market is clearly mispriced against you (no information edge)\n"
    "- Resolution is years away with no catalyst\n\n"
    "For edges of 2%+ with reasonable probability, you should BUY. "
    "Short-resolution markets (<72h) are especially attractive — less time for "
    "things to go wrong. Higher confidence = stronger BUY.\n\n"
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

_COUNTER_PROPOSER_SYSTEM = (
    "You are the same aggressive prediction market trader. Your trade was challenged "
    "by a risk analyst. You must counter-argue their objections with hard data.\n\n"
    "Address each objection directly:\n"
    "- If they say edge is too thin, explain why it's sufficient for this market\n"
    "- If they cite risk, explain why the risk is mitigated (short resolution, "
    "diversification, small size)\n"
    "- If they question the thesis, strengthen it with additional reasoning\n\n"
    "Be specific and data-driven. Don't just repeat yourself.\n\n"
    "Respond in this exact format:\n"
    "COUNTER: 2-3 sentences directly addressing the challenger's objections\n"
    "CONVICTION: 0.0 to 1.0 (has your conviction changed after hearing objections?)"
)

_POSITION_REVIEWER_SYSTEM = (
    "You are a portfolio analyst reviewing an open prediction market position. "
    "Given current market data, decide what action to take.\n"
    "Consider:\n"
    "- Has the thesis changed since entry?\n"
    "- Is the current price reflecting new information?\n"
    "- Are we better off freeing this capital for other opportunities?\n"
    "- How close is resolution and does that change the risk?\n"
    "- Is this a good swing trade opportunity (price improved, thesis stronger)?\n\n"
    "Actions:\n"
    "- HOLD: thesis intact, keep position as is\n"
    "- EXIT: thesis broken or risk too high, sell everything\n"
    "- REDUCE: take partial profits or cut exposure, sell half\n"
    "- INCREASE: thesis strengthened and price improved, buy more shares\n\n"
    "Respond in this exact format:\n"
    "VERDICT: HOLD, EXIT, REDUCE, or INCREASE\n"
    "URGENCY: LOW, MEDIUM, or HIGH\n"
    "REASONING: 1-2 sentences"
)


_RISK_PROPOSER_SYSTEM = (
    "You are an aggressive, data-driven trade proposer on a prediction market bot. "
    "A risk manager has rejected a promising trade signal. Your job is to challenge "
    "the rejection with hard numbers and propose concrete fixes.\n\n"
    "Arguments you can make:\n"
    "- Edge is close to threshold — small relaxation is justified\n"
    "- Short time to resolution reduces risk exposure\n"
    "- Reduced position size mitigates the concern\n"
    "- Category exposure limit is overly conservative for this market\n"
    "- Strong sentiment/research support for this trade\n\n"
    "Respond in this exact format:\n"
    "REBUTTAL: 2-3 sentences challenging the rejection with specific data\n"
    "PROPOSED_FIX: One concrete fix (e.g., 'reduce size to 50%', 'accept lower edge')\n"
    "CONVICTION: 0.0 to 1.0 (how strongly you believe the trade should go through)"
)

_RISK_ANALYST_SYSTEM = (
    "You are a senior risk analyst reviewing a risk debate. A trade was rejected "
    "by the risk manager, and an aggressive proposer is pushing back. You must be "
    "fair but firm.\n\n"
    "When to CONCEDE:\n"
    "- Edge is within 20% of threshold and other factors are favorable\n"
    "- Resolution is soon (<24h) and max-age won't be an issue\n"
    "- Reduced position size genuinely mitigates the risk\n"
    "- Category exposure is near limit but not critically over\n\n"
    "When to MAINTAIN rejection:\n"
    "- Edge is far below threshold (>30% under)\n"
    "- Fundamental risk limits (daily loss, drawdown, duplicate position)\n"
    "- Win probability too low with no mitigating factors\n"
    "- Proposer's arguments don't address the core concern\n\n"
    "Respond in this exact format:\n"
    "VERDICT: CONCEDE or MAINTAIN\n"
    "SIZE_ADJUSTMENT: 0.5 to 1.0 (only if CONCEDE — fraction of original size)\n"
    "REASONING: 2-3 sentences explaining your decision"
)

# Hard rejections: never debatable, fundamental safety limits (lowercase)
_HARD_REJECTIONS = frozenset({
    "trading is paused",
    "daily loss limit",
    "max drawdown",
    "duplicate position",
})

# Debatable rejection keywords — partial match, lowercase
_DEBATABLE_KEYWORDS = frozenset({
    "edge too low",
    "category exposure",
    "win prob too low",
    "max positions",
    "max deployed",
})


def _is_debatable_rejection(reason: str) -> bool:
    """Check if a risk rejection is debatable (not a hard safety limit)."""
    lower = reason.lower()
    for hard in _HARD_REJECTIONS:
        if hard in lower:
            return False
    return any(kw in lower for kw in _DEBATABLE_KEYWORDS)


@dataclass(frozen=True)
class RiskDebateResult:
    """Result of a risk rejection debate (proposer vs analyst)."""

    override: bool
    rejection_reason: str
    proposer_rebuttal: str
    analyst_verdict: str  # "CONCEDE" or "MAINTAIN"
    analyst_reasoning: str
    adjusted_size_pct: float  # 0.5-1.0 if conceded, 0.0 if maintained
    total_cost_usd: float
    elapsed_s: float


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
    # Multi-round counter fields (empty string = single-round or not triggered)
    counter_rebuttal: str = ""
    counter_conviction: float = 0.0
    final_verdict: str = ""  # "APPROVE" or "REJECT" — challenger's second look
    final_reasoning: str = ""


@dataclass(frozen=True)
class ReviewResult:
    """Result of an LLM position review."""

    verdict: str  # "HOLD", "EXIT", "REDUCE", "INCREASE"
    should_exit: bool  # True if verdict is EXIT
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

    # --- MULTI-ROUND COUNTER (optional) ---
    counter_rebuttal = ""
    counter_conviction = 0.0
    final_verdict = ""
    final_reasoning = ""

    if (
        prop_verdict == "BUY"
        and chal_verdict == "REJECT"
        and settings.use_multi_round_debate
    ):
        # Proposer counter-argues the challenger's objections
        safe_q = _sanitize_prompt_input(question)
        counter_msg = (
            f"Your trade proposal was REJECTED by the risk analyst.\n"
            f"Market: {safe_q}\n"
            f"Price: ${price:.3f} | Edge: {edge:.1%} | Prob: {estimated_prob:.1%}\n"
            f"Your original reasoning: {prop_reasoning}\n\n"
            f"Challenger's objections ({chal_risk} risk): {chal_objections}\n\n"
            f"Counter-argue these objections."
        )

        try:
            counter_resp = await client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_COUNTER_PROPOSER_SYSTEM,
                messages=[{"role": "user", "content": counter_msg}],
            )
            counter_text = counter_resp.content[0].text.strip()
            counter_cost = _calc_cost(
                counter_resp.usage.input_tokens, counter_resp.usage.output_tokens,
            )
            total_cost += counter_cost
            counter_rebuttal, counter_conviction = _parse_counter_proposer(counter_text)
        except Exception as e:
            logger.warning("llm_counter_proposer_error", error=str(e))
            # Counter failed — challenger's REJECT stands
            counter_rebuttal = ""
            counter_conviction = 0.0

        # If proposer still has conviction, give challenger a final look
        if counter_rebuttal and counter_conviction >= 0.4:
            final_msg = (
                f"SECOND REVIEW — The proposer has counter-argued your rejection.\n"
                f"Market: {safe_q}\n"
                f"Price: ${price:.3f} | Edge: {edge:.1%} | Prob: {estimated_prob:.1%}\n"
                f"Your original objections: {chal_objections}\n\n"
                f"Proposer's counter-argument (conviction {counter_conviction:.0%}): "
                f"{counter_rebuttal}\n\n"
                f"Make your FINAL decision: APPROVE or REJECT?"
            )

            try:
                final_resp = await client.messages.create(
                    model=_MODEL,
                    max_tokens=_MAX_TOKENS,
                    system=_CHALLENGER_SYSTEM,
                    messages=[{"role": "user", "content": final_msg}],
                )
                final_text = final_resp.content[0].text.strip()
                final_cost = _calc_cost(
                    final_resp.usage.input_tokens, final_resp.usage.output_tokens,
                )
                total_cost += final_cost
                final_verdict, _, final_reasoning = _parse_challenger(final_text)
            except Exception as e:
                logger.warning("llm_final_challenger_error", error=str(e))
                final_verdict = "REJECT"
                final_reasoning = "Final review unavailable — rejection stands"

    # Decision logic
    if final_verdict:
        # Multi-round: final verdict overrides initial challenger
        approved = (prop_verdict == "BUY" and final_verdict != "REJECT")
    else:
        # Single-round: original logic
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
        multi_round=bool(final_verdict),
        final_verdict=final_verdict or "N/A",
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
        counter_rebuttal=counter_rebuttal,
        counter_conviction=counter_conviction,
        final_verdict=final_verdict,
        final_reasoning=final_reasoning,
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
    safe_q = _sanitize_prompt_input(question)
    user_msg = (
        f"Position review:\n"
        f"Market: {safe_q}\n"
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
            verdict=verdict,
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
    safe_q = _sanitize_prompt_input(question)
    safe_r = _sanitize_prompt_input(reasoning)
    msg = (
        f"Trade opportunity:\n"
        f"Market: {safe_q}\n"
        f"Strategy: {strategy}\n"
        f"Market price: ${price:.3f}\n"
        f"Our estimated probability: {estimated_prob:.1%}\n"
        f"Edge: {edge:.1%}\n"
        f"Signal confidence: {confidence:.2f}\n"
        f"Strategy reasoning: {safe_r}\n"
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
    safe_q = _sanitize_prompt_input(question)
    msg = (
        f"Proposed trade to review:\n"
        f"Market: {safe_q}\n"
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
            elif "REDUCE" in val:
                verdict = "REDUCE"
            elif "INCREASE" in val:
                verdict = "INCREASE"
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


def _parse_counter_proposer(text: str) -> tuple[str, float]:
    """Parse counter-argument response. Returns (counter, conviction)."""
    counter = text
    conviction = 0.5

    for line in text.split("\n"):
        upper = line.upper().strip()
        if upper.startswith("COUNTER:"):
            counter = line.split(":", 1)[1].strip()
        elif upper.startswith("CONVICTION:"):
            try:
                conviction = float(line.split(":", 1)[1].strip())
                conviction = max(0.0, min(1.0, conviction))
            except ValueError:
                pass

    return counter, conviction


def _parse_risk_proposer(text: str) -> tuple[str, str, float]:
    """Parse risk proposer response. Returns (rebuttal, proposed_fix, conviction)."""
    rebuttal = text
    proposed_fix = ""
    conviction = 0.5

    for line in text.split("\n"):
        upper = line.upper().strip()
        if upper.startswith("REBUTTAL:"):
            rebuttal = line.split(":", 1)[1].strip()
        elif upper.startswith("PROPOSED_FIX:") or upper.startswith("PROPOSED FIX:"):
            proposed_fix = line.split(":", 1)[1].strip()
        elif upper.startswith("CONVICTION:"):
            try:
                conviction = float(line.split(":", 1)[1].strip())
                conviction = max(0.0, min(1.0, conviction))
            except ValueError:
                pass

    return rebuttal, proposed_fix, conviction


def _parse_risk_analyst(text: str) -> tuple[str, float, str]:
    """Parse risk analyst response. Returns (verdict, size_adjustment, reasoning)."""
    verdict = "MAINTAIN"
    size_adjustment = 1.0
    reasoning = text

    for line in text.split("\n"):
        upper = line.upper().strip()
        if upper.startswith("VERDICT:"):
            val = upper.split(":", 1)[1].strip()
            if "CONCEDE" in val:
                verdict = "CONCEDE"
            else:
                verdict = "MAINTAIN"
        elif upper.startswith("SIZE_ADJUSTMENT:") or upper.startswith("SIZE ADJUSTMENT:"):
            try:
                size_adjustment = float(line.split(":", 1)[1].strip())
                size_adjustment = max(0.5, min(1.0, size_adjustment))
            except ValueError:
                pass
        elif upper.startswith("REASONING:"):
            reasoning = line.split(":", 1)[1].strip()

    return verdict, size_adjustment, reasoning


async def debate_risk_rejection(
    question: str,
    strategy: str,
    rejection_reason: str,
    edge: float,
    price: float,
    estimated_prob: float,
    size_usd: float,
    hours_to_resolution: float | None = None,
) -> RiskDebateResult | None:
    """Debate a risk manager rejection: proposer argues, analyst decides.

    Returns RiskDebateResult, or None if debate can't run (budget, hard rejection).
    """
    if cost_tracker.is_over_budget:
        logger.info("risk_debate_budget_exhausted", today_cost=cost_tracker.today_cost)
        return None

    if not _is_debatable_rejection(rejection_reason):
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
    safe_q = _sanitize_prompt_input(question)

    # --- PROPOSER ---
    proposer_msg = (
        f"Risk rejection to challenge:\n"
        f"Market: {safe_q}\n"
        f"Strategy: {strategy}\n"
        f"Price: ${price:.3f} | Edge: {edge:.1%} | Prob: {estimated_prob:.1%}\n"
        f"Proposed size: ${size_usd:.2f}\n"
        f"Rejection reason: {rejection_reason}\n"
    )
    if hours_to_resolution is not None:
        proposer_msg += f"Hours to resolution: {hours_to_resolution:.1f}\n"
    proposer_msg += "\nChallenge this rejection."

    try:
        prop_resp = await client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_RISK_PROPOSER_SYSTEM,
            messages=[{"role": "user", "content": proposer_msg}],
        )
        prop_text = prop_resp.content[0].text.strip()
        prop_cost = _calc_cost(prop_resp.usage.input_tokens, prop_resp.usage.output_tokens)
        total_cost += prop_cost
    except Exception as e:
        logger.warning("risk_proposer_error", error=str(e))
        return None

    rebuttal, proposed_fix, conviction = _parse_risk_proposer(prop_text)

    # Low conviction — proposer agrees with rejection
    if conviction < _MIN_CONVICTION_FOR_ANALYST:
        elapsed = time.monotonic() - start
        cost_tracker.add(total_cost)
        logger.info(
            "risk_debate_low_conviction",
            question=question[:60],
            conviction=conviction,
            cost_usd=round(total_cost, 5),
        )
        return RiskDebateResult(
            override=False,
            rejection_reason=rejection_reason,
            proposer_rebuttal=rebuttal,
            analyst_verdict="skipped",
            analyst_reasoning=f"Proposer conviction too low ({conviction:.0%})",
            adjusted_size_pct=0.0,
            total_cost_usd=total_cost,
            elapsed_s=round(elapsed, 2),
        )

    # --- ANALYST ---
    analyst_msg = (
        f"Risk debate review:\n"
        f"Market: {safe_q}\n"
        f"Strategy: {strategy}\n"
        f"Price: ${price:.3f} | Edge: {edge:.1%} | Prob: {estimated_prob:.1%}\n"
        f"Original rejection: {rejection_reason}\n\n"
        f"Proposer's rebuttal: {rebuttal}\n"
        f"Proposed fix: {proposed_fix}\n"
        f"Proposer conviction: {conviction:.0%}\n"
    )
    if hours_to_resolution is not None:
        analyst_msg += f"Hours to resolution: {hours_to_resolution:.1f}\n"
    analyst_msg += "\nShould we CONCEDE or MAINTAIN the rejection?"

    try:
        analyst_resp = await client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_RISK_ANALYST_SYSTEM,
            messages=[{"role": "user", "content": analyst_msg}],
        )
        analyst_text = analyst_resp.content[0].text.strip()
        analyst_cost = _calc_cost(
            analyst_resp.usage.input_tokens, analyst_resp.usage.output_tokens,
        )
        total_cost += analyst_cost
    except Exception as e:
        logger.warning("risk_analyst_error", error=str(e))
        # Fail-safe: maintain rejection
        elapsed = time.monotonic() - start
        cost_tracker.add(total_cost)
        return RiskDebateResult(
            override=False,
            rejection_reason=rejection_reason,
            proposer_rebuttal=rebuttal,
            analyst_verdict="MAINTAIN",
            analyst_reasoning="Analyst unavailable — rejection maintained",
            adjusted_size_pct=0.0,
            total_cost_usd=total_cost,
            elapsed_s=round(elapsed, 2),
        )

    verdict, size_adjustment, reasoning = _parse_risk_analyst(analyst_text)
    override = verdict == "CONCEDE"
    elapsed = time.monotonic() - start
    cost_tracker.add(total_cost)

    logger.info(
        "risk_debate_complete",
        question=question[:60],
        rejection=rejection_reason[:40],
        conviction=conviction,
        verdict=verdict,
        size_adjustment=size_adjustment if override else 0.0,
        override=override,
        cost_usd=round(total_cost, 5),
        elapsed_s=round(elapsed, 2),
    )

    return RiskDebateResult(
        override=override,
        rejection_reason=rejection_reason,
        proposer_rebuttal=rebuttal,
        analyst_verdict=verdict,
        analyst_reasoning=reasoning,
        adjusted_size_pct=size_adjustment if override else 0.0,
        total_cost_usd=total_cost,
        elapsed_s=round(elapsed, 2),
    )
