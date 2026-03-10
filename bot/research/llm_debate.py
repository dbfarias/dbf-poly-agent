"""LLM debate gate — Proposer vs Challenger pattern for trade signals."""

import asyncio
import re
import time
from dataclasses import dataclass, replace

import structlog

from bot.config import settings

logger = structlog.get_logger()

_MODEL = "claude-haiku-4-5-20251001"
_TIMEOUT = 15.0
_MAX_TOKENS = 300
_MAX_PROMPT_INPUT_LEN = 200
_MIN_CONVICTION_FOR_ANALYST = 0.4
_CACHE_TTL_APPROVED = 3600.0 * 3   # 3h for approved trades (no need to re-debate)
_CACHE_TTL_REJECTED = 1800.0       # 30min for rejections (allow re-try sooner)


@dataclass(frozen=True)
class DebateContext:
    """Rich context from engine → debate for better LLM decisions."""

    strategy_win_rate: float = 0.0
    strategy_total_trades: int = 0
    edge_multiplier: float = 1.0
    category_confidence: float = 1.0
    daily_progress: float = 0.0
    urgency_multiplier: float = 1.0
    research_confidence: float = 0.0
    market_category: str = ""
    news_headlines: tuple[str, ...] = ()
    crypto_prices: tuple[tuple[str, float], ...] = ()
    is_volume_anomaly: bool = False
    historical_base_rate: float = 0.0
    research_agrees: bool | None = None  # True=agrees, False=disagrees, None=unknown
    twitter_sentiment: float = 0.0


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

def _build_proposer_system(daily_target_pct: float = 1.0) -> str:
    """Build proposer system prompt with dynamic daily target + superforecaster reasoning."""
    return (
        "You are an aggressive prediction market superforecaster on Polymarket. "
        "You combine systematic probabilistic reasoning with a strong bias toward action.\n\n"
        "SUPERFORECASTER METHOD (use this mental framework quickly):\n"
        "1. What is the BASE RATE for this type of event?\n"
        "2. What SPECIFIC FACTORS push probability up or down from the base rate?\n"
        "3. How does the CURRENT PRICE compare to your estimated probability?\n"
        "4. Is the RESOLUTION TIMELINE favorable (shorter = less uncertainty = better)?\n"
        "5. DECIDE: is the gap between your estimate and market price exploitable?\n\n"
        f"DAILY PROFIT TARGET: {daily_target_pct}% net daily profit. Every missed trade "
        "costs you progress. Favor high-conviction, quick-resolution trades.\n\n"
        "RISK RULES (hard limits — always PASS if violated):\n"
        "- Price < $0.10 → near-worthless zone, exit system will immediately close\n"
        "- Price > $0.95 → too close to certainty, minimal upside\n"
        "- Avoid markets where you'd buy AND the exit system would instantly sell\n\n"
        "Your bias is BUY. Only say PASS if:\n"
        "- Edge < 1% (too thin after fees)\n"
        "- You genuinely believe the market price is MORE accurate than the signal\n"
        "- Resolution is years away with no near-term catalyst\n"
        "- Price violates risk rules above\n\n"
        "For edges of 2%+ with reasonable probability, you should BUY. "
        "Short-resolution markets (<72h) are especially attractive.\n\n"
        "EDGE CONTEXT: The edge is algorithmically computed from orderbook data — do NOT "
        "question its validity. Evaluate the TRADE OPPORTUNITY.\n\n"
        "Respond in this exact format:\n"
        "VERDICT: BUY or PASS\n"
        "CONFIDENCE: 0.0 to 1.0\n"
        "REASONING: 1-2 sentences using the superforecaster framework"
    )

def _build_challenger_system(daily_target_pct: float = 1.0) -> str:
    """Build challenger system prompt with dynamic daily target."""
    return (
        "You are a fair but thorough risk analyst reviewing a prediction market trade. "
        "Your job is to evaluate whether this trade has reasonable risk/reward.\n\n"
        f"DAILY PROFIT OBSESSION: The bot targets at least {daily_target_pct}% net daily profit "
        "(after fees and LLM costs). Your role is to protect capital, but remember "
        "that being too conservative kills profitability. Rejecting a solid trade "
        "means we fall behind the daily target. Only reject when the risk genuinely "
        "outweighs the reward — not out of caution alone.\n\n"
        "APPROVE the trade if:\n"
        "- Edge is 2%+ and the reasoning is sound\n"
        "- Short resolution time (<72h) reduces risk significantly\n"
        "- The proposer's thesis is logical even if not perfect\n"
        "- Risk is manageable with the small position sizes we use ($1-5)\n\n"
        "REJECT only if:\n"
        "- Edge is clearly fabricated or based on stale data\n"
        "- There's a fundamental flaw the proposer missed entirely\n"
        "- The market is a pure coin flip with no information edge\n"
        "- Resolution is very far away (>30 days) with thin edge\n\n"
        "RISK LEVEL guidance:\n"
        "- LOW: solid trade, no concerns\n"
        "- MEDIUM: some concerns but trade has merit — use this for most rejections\n"
        "- HIGH: ONLY for fatal flaws (wrong market, impossible outcome, obvious scam)\n"
        "Default to MEDIUM when rejecting. HIGH should be rare (<10% of rejections).\n\n"
        "Remember: we trade small sizes ($1-5). The cost of missing a good trade "
        "is worse than taking a slightly marginal one.\n\n"
        "Respond in this exact format:\n"
        "VERDICT: APPROVE or REJECT\n"
        "RISK_LEVEL: LOW, MEDIUM, or HIGH\n"
        "OBJECTIONS: 1-2 sentences with specific concerns (or 'None' if truly solid)"
    )

_COUNTER_PROPOSER_SYSTEM = (
    "You are the same aggressive prediction market trader. Your trade was challenged "
    "by a risk analyst. You must counter-argue their objections with hard data.\n\n"
    "Remember: the bot needs 0.5% net daily profit. Every rejected trade pushes us "
    "further from that target. Fight hard for trades with genuine edge.\n\n"
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
    "Given current market data, decide what action to take.\n\n"
    "DAILY PROFIT OBSESSION: The bot targets 0.5% net daily profit. Capital tied "
    "up in stagnant or losing positions can't be redeployed to better opportunities. "
    "Be aggressive about cutting losers and freeing capital. A small realized loss "
    "now is better than a larger one later.\n\n"
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
    "DAILY PROFIT OBSESSION: The bot needs 0.5% net daily profit. Overly conservative "
    "risk limits that block legitimate trades are the enemy of profitability. Push back "
    "hard on marginal rejections — every blocked trade is missed profit.\n\n"
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
    "DAILY PROFIT OBSESSION: The bot targets 0.5% net daily profit. Your job is to "
    "protect capital, but excessive caution that blocks profitable trades undermines "
    "the daily target. Balance protection with profitability — concede when the "
    "proposer makes a compelling case with real numbers.\n\n"
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


_CONSERVATIVE_SYSTEM = (
    "You are a conservative, risk-first prediction market analyst. "
    "You require a strong 3%+ edge before recommending any trade. "
    "You are skeptical of short-resolution markets (high noise, low signal). "
    "Capital preservation matters more than daily targets.\n\n"
    "Only say BUY if:\n"
    "- Edge is 3%+ with solid reasoning\n"
    "- Win probability is well-supported, not just orderbook noise\n"
    "- Time to resolution gives enough runway for thesis to play out\n\n"
    "Respond in this exact format:\n"
    "VERDICT: BUY or PASS\n"
    "CONFIDENCE: 0.0 to 1.0\n"
    "REASONING: 1-2 sentences"
)

_AGGRESSIVE_SYSTEM = (
    "You are an aggressive prediction market trader on Polymarket. You look for "
    "any reasonable edge and push to take it. You WANT to trade — that's how you "
    "make money. A 2-5% edge is worth taking, especially on short-resolution markets.\n\n"
    "Your bias is BUY. Only say PASS if the signal is clearly garbage:\n"
    "- Edge < 1% (too thin to overcome fees)\n"
    "- Market is clearly mispriced against you\n"
    "- Resolution is years away with no catalyst\n\n"
    "For edges of 2%+ with reasonable probability, you should BUY.\n\n"
    "Respond in this exact format:\n"
    "VERDICT: BUY or PASS\n"
    "CONFIDENCE: 0.0 to 1.0\n"
    "REASONING: 1-2 sentences"
)

_BALANCED_SYSTEM = (
    "You are a data-driven prediction market analyst. You weigh three factors equally:\n"
    "1. Edge magnitude — is the edge large enough to justify the risk?\n"
    "2. Time to resolution — shorter is better (less uncertainty, faster capital turnover)\n"
    "3. Category performance — has this type of market historically been profitable?\n\n"
    "You have no inherent bias toward BUY or PASS. Let the data decide.\n"
    "A 2-3% edge on a short-resolution market in a profitable category = BUY.\n"
    "A 5% edge on a long-resolution market in a losing category = PASS.\n\n"
    "Respond in this exact format:\n"
    "VERDICT: BUY or PASS\n"
    "CONFIDENCE: 0.0 to 1.0\n"
    "REASONING: 1-2 sentences"
)


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
    edge_valid: bool = True
    counter_rebuttal: str = ""
    counter_conviction: float = 0.0
    final_verdict: str = ""  # "APPROVE" or "REJECT" — challenger's second look
    final_reasoning: str = ""


@dataclass(frozen=True)
class PostMortemResult:
    """Result of a post-trade LLM analysis."""

    outcome_quality: str  # "GOOD", "BAD", "NEUTRAL"
    key_lesson: str
    strategy_fit: str  # "GOOD_FIT", "POOR_FIT", "NEUTRAL"
    analysis: str
    cost_usd: float


@dataclass(frozen=True)
class ConsensusResult:
    """Result of a multi-persona consensus vote."""

    approved: bool
    verdicts: list[str]  # ["BUY", "PASS", "BUY"]
    confidences: list[float]
    avg_confidence: float
    total_cost_usd: float


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

# Debate result cache — avoids re-debating the same market repeatedly.
# Key: "question|strategy", Value: (DebateResult, timestamp)
_debate_cache: dict[str, tuple["DebateResult", float]] = {}


def _debate_cache_key(
    question: str, strategy: str, price: float = 0.0, edge: float = 0.0,
) -> str:
    """Build a cache key from question + strategy + bucketed price/edge.

    Price is bucketed to 2 decimal places and edge to 1% increments.
    This means a meaningful price or edge change will trigger a fresh debate,
    while minor fluctuations reuse the cache.
    """
    price_bucket = round(price, 2)
    edge_bucket = round(edge, 2)  # 1% granularity
    return f"{question.strip().lower()}|{strategy}|{price_bucket}|{edge_bucket}"


def _get_cached_debate(
    question: str,
    strategy: str,
    price: float = 0.0,
    edge: float = 0.0,
) -> "DebateResult | None":
    """Return cached DebateResult if still valid, else None.

    Approved trades are cached for 3h (no need to re-debate).
    Rejected trades are cached for only 30min (allow re-try sooner).
    """
    key = _debate_cache_key(question, strategy, price, edge)
    entry = _debate_cache.get(key)
    if entry is None:
        return None
    result, ts = entry
    ttl = _CACHE_TTL_APPROVED if result.approved else _CACHE_TTL_REJECTED
    if time.monotonic() - ts > ttl:
        _debate_cache.pop(key, None)
        return None
    # Return copy with zero cost (no API call was made)
    return replace(result, total_cost_usd=0.0, elapsed_s=0.0)


def _cache_debate(
    question: str, strategy: str, result: "DebateResult",
    price: float = 0.0, edge: float = 0.0,
) -> None:
    """Store a debate result in the cache."""
    key = _debate_cache_key(question, strategy, price, edge)
    _debate_cache[key] = (result, time.monotonic())


def clear_debate_cache() -> None:
    """Clear the debate cache (for testing or manual reset)."""
    _debate_cache.clear()


async def _get_market_history(question: str) -> str:
    """Query recent trades for the same market and format as context text.

    Uses first 60 chars of the question for LIKE matching.
    Returns formatted text block, or empty string if no history found.
    """
    try:
        from sqlalchemy import select

        from bot.data.database import async_session
        from bot.data.models import Trade
    except ImportError:
        return ""

    prefix = question.strip()[:60]
    if not prefix:
        return ""

    try:
        async with async_session() as session:
            result = await session.execute(
                select(Trade)
                .where(Trade.question.like(f"{prefix}%"))
                .where(Trade.status == "filled")
                .order_by(Trade.created_at.desc())
                .limit(5)
            )
            trades = list(result.scalars().all())

        if not trades:
            return ""

        lines = []
        for t in trades:
            pnl_str = f"${t.pnl:+.2f}" if t.pnl else "N/A"
            exit_str = t.exit_reason or "N/A"
            lines.append(
                f"- {t.side} @ ${t.price:.3f}, PnL: {pnl_str}, "
                f"exit: {exit_str}, strategy: {t.strategy}"
            )
        return "\n".join(lines)
    except Exception as e:
        logger.debug("market_history_query_error", error=str(e))
        return ""


def _parse_consensus_persona(text: str) -> tuple[str, float]:
    """Parse a consensus persona response. Returns (verdict, confidence)."""
    verdict = "PASS"
    confidence = 0.5

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

    return verdict, confidence


async def debate_with_consensus(
    question: str,
    strategy: str,
    edge: float,
    price: float,
    estimated_prob: float,
    confidence: float,
    reasoning: str,
    sentiment_score: float | None = None,
    hours_to_resolution: float | None = None,
) -> ConsensusResult | None:
    """Run 3 Haiku personas in parallel and take majority vote.

    Returns ConsensusResult, or None if consensus couldn't run.
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

    client = AsyncAnthropic(api_key=api_key, timeout=_TIMEOUT)

    safe_q = _sanitize_prompt_input(question)
    safe_r = _sanitize_prompt_input(reasoning)
    user_msg = (
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
        user_msg += f"Hours until resolution: {hours_to_resolution:.1f}\n"
    if sentiment_score is not None:
        user_msg += f"News sentiment: {sentiment_score:+.2f} (-1=bearish, +1=bullish)\n"
    user_msg += "\nShould we BUY or PASS?"

    personas = [
        ("conservative", _CONSERVATIVE_SYSTEM),
        ("aggressive", _AGGRESSIVE_SYSTEM),
        ("balanced", _BALANCED_SYSTEM),
    ]

    async def _call_persona(system_prompt: str) -> tuple[str, float, float]:
        """Call a single persona. Returns (verdict, confidence, cost)."""
        resp = await client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=system_prompt,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text.strip()
        cost = _calc_cost(resp.usage.input_tokens, resp.usage.output_tokens)
        verdict, conf = _parse_consensus_persona(text)
        return verdict, conf, cost

    try:
        results = await asyncio.gather(
            *[_call_persona(sys) for _, sys in personas],
            return_exceptions=True,
        )
    except Exception as e:
        logger.warning("consensus_gather_error", error=str(e))
        return None

    verdicts: list[str] = []
    confidences: list[float] = []
    total_cost = 0.0

    for i, res in enumerate(results):
        if isinstance(res, Exception):
            logger.warning(
                "consensus_persona_error",
                persona=personas[i][0],
                error=str(res),
            )
            # Default to PASS on error
            verdicts.append("PASS")
            confidences.append(0.0)
        else:
            verdict, conf, cost = res
            verdicts.append(verdict)
            confidences.append(conf)
            total_cost += cost

    cost_tracker.add(total_cost)

    buy_count = sum(1 for v in verdicts if v == "BUY")
    approved = buy_count >= 2  # 2/3 majority needed
    avg_confidence = sum(confidences) / len(confidences) if confidences else 0.0

    logger.info(
        "llm_consensus_complete",
        question=question[:60],
        verdicts=verdicts,
        approved=approved,
        avg_confidence=round(avg_confidence, 2),
        cost_usd=round(total_cost, 5),
    )

    return ConsensusResult(
        approved=approved,
        verdicts=list(verdicts),
        confidences=list(confidences),
        avg_confidence=round(avg_confidence, 3),
        total_cost_usd=total_cost,
    )


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
    resolution_condition: str = "",
    resolution_source: str = "",
    whale_activity: bool = False,
    whale_summary: str = "",
    context: DebateContext | None = None,
) -> DebateResult | None:
    """Run a Proposer vs Challenger debate on a trade signal.

    Returns DebateResult, or None if debate couldn't run (fallback to approve).
    """
    if cost_tracker.is_over_budget:
        logger.info("llm_debate_budget_exhausted", today_cost=cost_tracker.today_cost)
        return None

    # Check cache — avoid re-debating the same market/strategy/price/edge
    cached = _get_cached_debate(question, strategy, price=price, edge=edge)
    if cached is not None:
        logger.info(
            "llm_debate_cache_hit",
            question=question[:60],
            strategy=strategy,
            cached_approved=cached.approved,
        )
        return cached

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

    # --- DEBATE MEMORY: fetch past trades on this market ---
    history_text = await _get_market_history(question)

    # --- CONSENSUS MODE (optional, replaces single proposer) ---
    consensus_used = False
    if settings.use_llm_consensus:
        consensus = await debate_with_consensus(
            question=question,
            strategy=strategy,
            edge=edge,
            price=price,
            estimated_prob=estimated_prob,
            confidence=confidence,
            reasoning=reasoning,
            sentiment_score=sentiment_score,
            hours_to_resolution=hours_to_resolution,
        )
        if consensus is not None:
            consensus_used = True
            total_cost += consensus.total_cost_usd
            prop_verdict = "BUY" if consensus.approved else "PASS"
            prop_confidence = consensus.avg_confidence
            prop_reasoning = (
                f"Consensus: {'/'.join(consensus.verdicts)} "
                f"(avg conf {consensus.avg_confidence:.2f})"
            )
            # Edge validation deprecated — edges are algo-computed

            if not consensus.approved:
                # Consensus said PASS — skip challenger
                elapsed = time.monotonic() - start
                cost_tracker.add(total_cost)
                result = DebateResult(
                    approved=False,
                    proposer_verdict="PASS",
                    proposer_confidence=prop_confidence,
                    proposer_reasoning=prop_reasoning,
                    challenger_verdict="skipped",
                    challenger_risk="N/A",
                    challenger_objections="Consensus voted PASS — no challenger needed",
                    total_cost_usd=total_cost,
                    elapsed_s=round(elapsed, 2),
                )
                _cache_debate(question, strategy, result, price=price, edge=edge)
                return result

    # --- PROPOSER (single mode, or consensus fallback) ---
    if not consensus_used:
        proposer_msg = _format_proposer_prompt(
            question, strategy, edge, price, estimated_prob,
            confidence, reasoning, sentiment_score, hours_to_resolution,
            resolution_condition=resolution_condition,
            resolution_source=resolution_source,
            whale_activity=whale_activity,
            context=context,
        )

        # Inject market history context
        if history_text:
            proposer_msg += f"\n\nPREVIOUS TRADES ON THIS MARKET:\n{history_text}"

        # Inject whale summary context
        if whale_summary:
            proposer_msg += f"\n\nWHALE ACTIVITY:\n{whale_summary}"

        try:
            prop_resp = await client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_build_proposer_system(settings.daily_target_pct),
                messages=[{"role": "user", "content": proposer_msg}],
            )
            prop_text = prop_resp.content[0].text.strip()
            prop_cost = _calc_cost(prop_resp.usage.input_tokens, prop_resp.usage.output_tokens)
            total_cost += prop_cost
        except Exception as e:
            logger.warning("llm_proposer_error", error=str(e))
            return None

        prop_verdict, prop_confidence, prop_reasoning, _prop_edge_valid = _parse_proposer(
            prop_text,
        )

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
        result = DebateResult(
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
        _cache_debate(question, strategy, result, price=price, edge=edge)
        return result

    # --- CHALLENGER ---
    challenger_msg = _format_challenger_prompt(
        question, strategy, edge, price, estimated_prob,
        prop_reasoning, sentiment_score, hours_to_resolution,
        resolution_condition=resolution_condition,
        resolution_source=resolution_source,
        whale_activity=whale_activity,
        context=context,
    )

    # Inject market history context for challenger
    if history_text:
        challenger_msg += f"\n\nPREVIOUS TRADES ON THIS MARKET:\n{history_text}"

    # Inject whale summary context for challenger
    if whale_summary:
        challenger_msg += f"\n\nWHALE ACTIVITY:\n{whale_summary}"

    try:
        chal_resp = await client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_build_challenger_system(settings.daily_target_pct),
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
            challenger_objections="Challenger unavailable — proposer's BUY stands",
            total_cost_usd=total_cost,
            elapsed_s=round(elapsed, 2),
        )

    chal_verdict, chal_risk, chal_objections = _parse_challenger(chal_text)

    # --- MULTI-ROUND COUNTER (optional) ---
    counter_rebuttal, counter_conviction, final_verdict, final_reasoning, counter_cost = (
        await _run_counter_round(
            client, question, price, edge, estimated_prob,
            prop_verdict, prop_reasoning, chal_verdict, chal_risk, chal_objections,
        )
    )
    total_cost += counter_cost

    # Decision logic
    if final_verdict:
        approved = (prop_verdict == "BUY" and final_verdict == "APPROVE")
    else:
        approved = (prop_verdict == "BUY" and chal_verdict == "APPROVE")

    # Override: confident proposer can override challenger rejection
    # MEDIUM risk + conf >= 0.8 → approve (raised from 0.7 to reduce bad trades)
    # HIGH risk + conf >= 0.9 → approve (raised from 0.85 for extra safety)
    if not approved and prop_verdict == "BUY" and chal_verdict == "REJECT":
        if chal_risk == "MEDIUM" and prop_confidence >= 0.8:
            approved = True
        elif chal_risk == "HIGH" and prop_confidence >= 0.9:
            approved = True

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

    result = DebateResult(
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

    # Cache result — subsequent cycles reuse this instead of re-debating
    _cache_debate(question, strategy, result, price=price, edge=edge)

    return result


async def _run_counter_round(
    client: object,
    question: str,
    price: float,
    edge: float,
    estimated_prob: float,
    prop_verdict: str,
    prop_reasoning: str,
    chal_verdict: str,
    chal_risk: str,
    chal_objections: str,
) -> tuple[str, float, str, str, float]:
    """Run the multi-round counter if enabled and challenger rejected.

    Returns (counter_rebuttal, counter_conviction, final_verdict, final_reasoning, cost).
    All empty strings / 0.0 if not triggered.
    """
    if not (
        prop_verdict == "BUY"
        and chal_verdict == "REJECT"
        and settings.use_multi_round_debate
    ):
        return "", 0.0, "", "", 0.0

    safe_q = _sanitize_prompt_input(question)
    safe_reasoning = _sanitize_prompt_input(prop_reasoning, max_len=300)
    safe_objections = _sanitize_prompt_input(chal_objections, max_len=300)
    round_cost = 0.0

    counter_msg = (
        f"Your trade proposal was REJECTED by the risk analyst.\n"
        f"Market: {safe_q}\n"
        f"Price: ${price:.3f} | Edge: {edge:.1%} | Prob: {estimated_prob:.1%}\n"
        f"Your original reasoning: {safe_reasoning}\n\n"
        f"Challenger's objections ({chal_risk} risk): {safe_objections}\n\n"
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
        cost_tracker.add(counter_cost)
        round_cost += counter_cost
        counter_rebuttal, counter_conviction = _parse_counter_proposer(counter_text)
    except Exception as e:
        logger.warning("llm_counter_proposer_error", error=str(e))
        return "", 0.0, "", "", 0.0

    # Low conviction — proposer concedes, no final review
    if not counter_rebuttal or counter_conviction < 0.4:
        return counter_rebuttal, counter_conviction, "", "", round_cost

    # Budget re-check before final call
    if cost_tracker.is_over_budget:
        logger.info("llm_budget_exhausted_before_final")
        return counter_rebuttal, counter_conviction, "", "", round_cost

    safe_counter = _sanitize_prompt_input(counter_rebuttal, max_len=400)
    final_msg = (
        f"SECOND REVIEW — The proposer has counter-argued your rejection.\n"
        f"Market: {safe_q}\n"
        f"Price: ${price:.3f} | Edge: {edge:.1%} | Prob: {estimated_prob:.1%}\n"
        f"Your original objections: {safe_objections}\n\n"
        f"Proposer's counter-argument (conviction {counter_conviction:.0%}): "
        f"{safe_counter}\n\n"
        f"Make your FINAL decision: APPROVE or REJECT?"
    )

    try:
        final_resp = await client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_build_challenger_system(settings.daily_target_pct),
            messages=[{"role": "user", "content": final_msg}],
        )
        final_text = final_resp.content[0].text.strip()
        final_cost = _calc_cost(
            final_resp.usage.input_tokens, final_resp.usage.output_tokens,
        )
        cost_tracker.add(final_cost)
        round_cost += final_cost
        final_verdict, _, final_reasoning = _parse_challenger(final_text)
        return counter_rebuttal, counter_conviction, final_verdict, final_reasoning, round_cost
    except Exception as e:
        logger.warning("llm_final_challenger_error", error=str(e))
        fail_reason = "Final review unavailable — rejection stands"
        return counter_rebuttal, counter_conviction, "REJECT", fail_reason, round_cost


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


def _edge_multiplier_label(mult: float) -> str:
    if mult > 1.0:
        return "tightened"
    if mult < 1.0:
        return "relaxed"
    return "neutral"


def _format_context_block(ctx: DebateContext) -> str:
    """Format rich context block for proposer/challenger prompts."""
    parts: list[str] = []

    # Bot track record
    if ctx.strategy_total_trades > 0:
        parts.append(
            f"\nBOT TRACK RECORD: {ctx.strategy_win_rate:.0%} win rate over "
            f"{ctx.strategy_total_trades} trades with this strategy. "
            f"Edge multiplier: {ctx.edge_multiplier:.2f} "
            f"({_edge_multiplier_label(ctx.edge_multiplier)})."
        )

    # Daily progress
    if ctx.daily_progress != 0.0:
        status = "AHEAD" if ctx.daily_progress >= 1.0 else "BEHIND"
        parts.append(
            f"Daily progress: {ctx.daily_progress:.0%} of target ({status}). "
            f"Urgency: {ctx.urgency_multiplier:.2f}x."
        )

    # Market category
    if ctx.market_category:
        cat_line = f"Market category: {ctx.market_category}"
        if ctx.category_confidence != 1.0:
            cat_line += f" (confidence: {ctx.category_confidence:.2f})"
        parts.append(cat_line)

    # Research
    if ctx.research_confidence > 0:
        parts.append(f"Research confidence: {ctx.research_confidence:.2f}")
    if ctx.news_headlines:
        headlines = "; ".join(ctx.news_headlines[:3])
        parts.append(f"Top headlines: {headlines}")
    if ctx.crypto_prices:
        prices = ", ".join(
            f"{name}: ${price:,.0f}" for name, price in ctx.crypto_prices[:3]
        )
        parts.append(f"Crypto prices: {prices}")

    # Research alignment
    if ctx.research_agrees is True:
        parts.append(
            "RESEARCH ALIGNMENT: ✅ Research AGREES with trade direction. "
            "News sentiment supports this trade — higher conviction warranted."
        )
    elif ctx.research_agrees is False:
        parts.append(
            "RESEARCH ALIGNMENT: ⚠️ Research DISAGREES with trade direction. "
            "RED FLAG — news sentiment contradicts this trade. "
            "Require stronger evidence to proceed."
        )
    else:
        parts.append("RESEARCH ALIGNMENT: ❓ Insufficient data to confirm direction.")

    if ctx.twitter_sentiment != 0.0:
        parts.append(f"Twitter/X sentiment: {ctx.twitter_sentiment:+.2f}")

    # Signals
    if ctx.is_volume_anomaly:
        parts.append("VOLUME ANOMALY: Unusual trading activity detected.")
    if ctx.historical_base_rate > 0:
        parts.append(f"Historical base rate: {ctx.historical_base_rate:.0%}")

    return "\n".join(parts)


def _format_proposer_prompt(
    question: str, strategy: str, edge: float, price: float,
    estimated_prob: float, confidence: float, reasoning: str,
    sentiment_score: float | None, hours_to_resolution: float | None,
    resolution_condition: str = "", resolution_source: str = "",
    whale_activity: bool = False,
    context: DebateContext | None = None,
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

    # Include resolution criteria if available
    if resolution_condition:
        safe_cond = _sanitize_prompt_input(resolution_condition)
        msg += f"Resolution: This market resolves YES if {safe_cond}"
        if resolution_source and resolution_source != "Unknown":
            safe_src = _sanitize_prompt_input(resolution_source, max_len=100)
            msg += f" (source: {safe_src})"
        msg += "\n"

    if whale_activity:
        msg += "WHALE ALERT: Large orders detected on this market's order book\n"

    # Enrich with extracted crypto threshold (if applicable)
    crypto = extract_crypto_threshold(question)
    if crypto is not None:
        msg += (
            f"Crypto threshold: {crypto['asset']} must go {crypto['direction']} "
            f"${crypto['threshold']:,.0f} to resolve YES\n"
        )

    # Enrich with learner/research context
    if context is not None:
        msg += _format_context_block(context)

    msg += "\nShould we BUY or PASS?"
    return msg


def _format_challenger_prompt(
    question: str, strategy: str, edge: float, price: float,
    estimated_prob: float, proposer_reasoning: str,
    sentiment_score: float | None, hours_to_resolution: float | None,
    resolution_condition: str = "",
    resolution_source: str = "",
    whale_activity: bool = False,
    context: DebateContext | None = None,
) -> str:
    safe_q = _sanitize_prompt_input(question)
    safe_reasoning = _sanitize_prompt_input(proposer_reasoning, max_len=300)
    msg = (
        f"Proposed trade to review:\n"
        f"Market: {safe_q}\n"
        f"Strategy: {strategy}\n"
        f"Market price: ${price:.3f}, Estimated prob: {estimated_prob:.1%}\n"
        f"Edge: {edge:.1%}\n"
        f"Proposer's case: {safe_reasoning}\n"
    )
    if hours_to_resolution is not None:
        msg += f"Hours until resolution: {hours_to_resolution:.1f}\n"
    if sentiment_score is not None:
        msg += f"News sentiment: {sentiment_score:+.2f}\n"

    # Resolution criteria (was proposer-only, now shared)
    if resolution_condition:
        safe_cond = _sanitize_prompt_input(resolution_condition)
        msg += f"Resolution: This market resolves YES if {safe_cond}"
        if resolution_source and resolution_source != "Unknown":
            safe_src = _sanitize_prompt_input(resolution_source, max_len=100)
            msg += f" (source: {safe_src})"
        msg += "\n"

    if whale_activity:
        msg += "WHALE ALERT: Large orders detected on this market's order book\n"

    # Enrich with learner/research context
    if context is not None:
        msg += _format_context_block(context)

    msg += "\nAPPROVE or REJECT this trade?"
    return msg


def _calc_cost(input_tokens: int, output_tokens: int) -> float:
    return (input_tokens * 0.80 + output_tokens * 4.00) / 1_000_000


def _parse_proposer(text: str) -> tuple[str, float, str, bool]:
    """Parse proposer response. Returns (verdict, confidence, reasoning, edge_valid)."""
    verdict = "PASS"
    confidence = 0.5
    reasoning = text
    edge_valid = True

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
        elif upper.startswith("EDGE_VALID:"):
            pass  # Deprecated: edges are algo-computed, always valid
        elif upper.startswith("REASONING:"):
            reasoning = line.split(":", 1)[1].strip()

    return verdict, confidence, reasoning, edge_valid


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


_POST_MORTEM_SYSTEM = (
    "You are a trading performance analyst reviewing a closed prediction market trade. "
    "Analyze what went right or wrong and extract a lesson for future trades.\n\n"
    "Consider:\n"
    "- Was the entry thesis correct? Did the edge materialize?\n"
    "- Was the exit timing good? Too early? Too late?\n"
    "- Was this strategy a good fit for this type of market?\n"
    "- What should the bot do differently next time?\n\n"
    "Respond in this exact format:\n"
    "OUTCOME_QUALITY: GOOD, BAD, or NEUTRAL\n"
    "KEY_LESSON: One sentence — the most important takeaway\n"
    "STRATEGY_FIT: GOOD_FIT, POOR_FIT, or NEUTRAL\n"
    "ANALYSIS: 2-3 sentences analyzing the trade"
)


def _parse_post_mortem(text: str) -> tuple[str, str, str, str]:
    """Parse post-mortem response.

    Returns (outcome_quality, key_lesson, strategy_fit, analysis).
    """
    outcome_quality = "NEUTRAL"
    key_lesson = ""
    strategy_fit = "NEUTRAL"
    analysis = text

    for line in text.split("\n"):
        upper = line.upper().strip()
        if upper.startswith("OUTCOME_QUALITY:") or upper.startswith("OUTCOME QUALITY:"):
            val = upper.split(":", 1)[1].strip()
            if "GOOD" in val:
                outcome_quality = "GOOD"
            elif "BAD" in val:
                outcome_quality = "BAD"
            else:
                outcome_quality = "NEUTRAL"
        elif upper.startswith("KEY_LESSON:") or upper.startswith("KEY LESSON:"):
            key_lesson = line.split(":", 1)[1].strip()
        elif upper.startswith("STRATEGY_FIT:") or upper.startswith("STRATEGY FIT:"):
            val = upper.split(":", 1)[1].strip()
            if "GOOD" in val:
                strategy_fit = "GOOD_FIT"
            elif "POOR" in val:
                strategy_fit = "POOR_FIT"
            else:
                strategy_fit = "NEUTRAL"
        elif upper.startswith("ANALYSIS:"):
            analysis = line.split(":", 1)[1].strip()

    return outcome_quality, key_lesson, strategy_fit, analysis


async def post_mortem_analysis(
    question: str,
    strategy: str,
    entry_price: float,
    exit_price: float,
    pnl: float,
    exit_reason: str,
    hold_hours: float,
) -> PostMortemResult | None:
    """Analyze a closed trade for lessons. Fire-and-forget safe.

    Returns PostMortemResult, or None if analysis couldn't run.
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

    safe_q = _sanitize_prompt_input(question)
    pnl_pct = (
        ((exit_price - entry_price) / entry_price * 100)
        if entry_price > 0 else 0.0
    )
    outcome = "WIN" if pnl > 0 else "LOSS" if pnl < 0 else "BREAKEVEN"

    user_msg = (
        f"Closed trade analysis:\n"
        f"Market: {safe_q}\n"
        f"Strategy: {strategy}\n"
        f"Entry: ${entry_price:.3f} → Exit: ${exit_price:.3f} ({pnl_pct:+.1f}%)\n"
        f"PnL: ${pnl:+.2f} ({outcome})\n"
        f"Hold time: {hold_hours:.1f} hours\n"
        f"Exit reason: {exit_reason}\n\n"
        f"What went right or wrong?"
    )

    try:
        client = AsyncAnthropic(api_key=api_key, timeout=_TIMEOUT)
        resp = await client.messages.create(
            model=_MODEL,
            max_tokens=200,
            system=_POST_MORTEM_SYSTEM,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text.strip()
        cost = _calc_cost(resp.usage.input_tokens, resp.usage.output_tokens)
        cost_tracker.add(cost)

        outcome_quality, key_lesson, strategy_fit, analysis = _parse_post_mortem(text)

        logger.info(
            "llm_post_mortem",
            question=question[:60],
            strategy=strategy,
            outcome=outcome,
            outcome_quality=outcome_quality,
            cost_usd=round(cost, 5),
        )

        return PostMortemResult(
            outcome_quality=outcome_quality,
            key_lesson=key_lesson,
            strategy_fit=strategy_fit,
            analysis=analysis,
            cost_usd=cost,
        )
    except Exception as e:
        logger.warning("llm_post_mortem_error", error=str(e))
        return None


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


# --- Crypto threshold extraction (#10) ---

_CRYPTO_ASSETS = (
    r"BTC|Bitcoin|ETH|Ethereum|SOL|Solana|XRP|ADA|Cardano|DOGE|Dogecoin"
    r"|DOT|Polkadot|LINK|Chainlink|AVAX|Avalanche|MATIC|Polygon"
    r"|LTC|Litecoin|UNI|Uniswap|AAVE"
)

_CRYPTO_THRESHOLD_RE = re.compile(
    rf"(?:Will|Can|Does)\s+({_CRYPTO_ASSETS})\s+"
    rf"(?:reach|hit|exceed|go\s+above|go\s+below|drop\s+below|fall\s+below|"
    rf"stay\s+above|stay\s+below|be\s+above|be\s+below|trade\s+above|trade\s+below)"
    rf"\s+\$?([\d,]+(?:\.\d+)?)",
    re.IGNORECASE,
)

_CRYPTO_DIRECTION_DOWN = re.compile(
    r"drop\s+below|fall\s+below|go\s+below|stay\s+below|be\s+below|trade\s+below",
    re.IGNORECASE,
)


def extract_crypto_threshold(question: str) -> dict | None:
    """Extract crypto price threshold from a market question.

    Fast regex-first approach. Returns dict with asset, threshold, direction,
    or None if not a crypto threshold question.

    Examples:
        "Will BTC reach $100,000 by June?"
          → {"asset": "BTC", "threshold": 100000, "direction": "above"}
        "Will ETH drop below $2,000?"
          → {"asset": "ETH", "threshold": 2000, "direction": "below"}
    """
    match = _CRYPTO_THRESHOLD_RE.search(question)
    if match is None:
        return None

    asset_raw = match.group(1).upper()
    # Normalize full names to tickers
    name_to_ticker = {
        "BITCOIN": "BTC", "ETHEREUM": "ETH", "SOLANA": "SOL",
        "CARDANO": "ADA", "DOGECOIN": "DOGE", "POLKADOT": "DOT",
        "CHAINLINK": "LINK", "AVALANCHE": "AVAX", "POLYGON": "MATIC",
        "LITECOIN": "LTC", "UNISWAP": "UNI",
    }
    asset = name_to_ticker.get(asset_raw, asset_raw)

    try:
        threshold = float(match.group(2).replace(",", ""))
    except ValueError:
        return None

    direction = "below" if _CRYPTO_DIRECTION_DOWN.search(question) else "above"

    return {"asset": asset, "threshold": threshold, "direction": direction}


async def extract_crypto_threshold_llm(question: str) -> dict | None:
    """Fallback: use Claude Haiku to extract crypto threshold when regex fails.

    Only called if extract_crypto_threshold() returns None but the question
    contains crypto keywords. Returns same format as regex version.
    """
    if cost_tracker.is_over_budget:
        return None

    # Quick check: does the question even mention crypto?
    crypto_check = re.compile(
        rf"\b({_CRYPTO_ASSETS})\b", re.IGNORECASE,
    )
    if not crypto_check.search(question):
        return None

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        return None

    api_key = settings.anthropic_api_key
    if not api_key:
        return None

    safe_q = _sanitize_prompt_input(question, max_len=300)
    client = AsyncAnthropic(api_key=api_key, timeout=10.0)

    try:
        resp = await client.messages.create(
            model=_MODEL,
            max_tokens=80,
            system=(
                "Extract crypto price threshold from this prediction market question. "
                "Respond ONLY in this format (no extra text):\n"
                "ASSET: <ticker>\n"
                "THRESHOLD: <number>\n"
                "DIRECTION: above or below\n\n"
                "If the question is not about a crypto price threshold, respond: NONE"
            ),
            messages=[{"role": "user", "content": safe_q}],
        )
        text = resp.content[0].text.strip()
        cost = _calc_cost(resp.usage.input_tokens, resp.usage.output_tokens)
        cost_tracker.add(cost)

        if "NONE" in text.upper():
            return None

        asset = ""
        threshold = 0.0
        direction = "above"
        for line in text.split("\n"):
            upper = line.upper().strip()
            if upper.startswith("ASSET:"):
                asset = line.split(":", 1)[1].strip().upper()
            elif upper.startswith("THRESHOLD:"):
                try:
                    threshold = float(
                        line.split(":", 1)[1].strip().replace(",", "").replace("$", "")
                    )
                except ValueError:
                    pass
            elif upper.startswith("DIRECTION:"):
                val = line.split(":", 1)[1].strip().lower()
                direction = "below" if "below" in val else "above"

        if asset and threshold > 0:
            logger.info(
                "crypto_threshold_llm_extracted",
                asset=asset,
                threshold=threshold,
                direction=direction,
                cost_usd=round(cost, 5),
            )
            return {"asset": asset, "threshold": threshold, "direction": direction}
        return None
    except Exception as e:
        logger.warning("crypto_threshold_llm_error", error=str(e))
        return None
