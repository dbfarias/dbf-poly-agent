"""LLM-based sentiment analysis using Claude Haiku."""

import time

import structlog

from bot.config import settings

logger = structlog.get_logger()

_SYSTEM_PROMPT = (
    "You are a market sentiment analyst for prediction markets. "
    "Given a market question and news headlines, return a sentiment score "
    "from -1.0 (very bearish — evidence suggests the event will NOT happen) "
    "to 1.0 (very bullish — evidence suggests the event WILL happen). "
    "Return ONLY the number, nothing else."
)

_MODEL = "claude-haiku-4-5-20251001"
_TIMEOUT = 10.0
_MAX_TOKENS = 16


async def analyze_sentiment_llm(question: str, headlines: list[str]) -> float:
    """Analyze sentiment via Claude Haiku.

    Returns a score in [-1, 1]. Falls back to 0.0 on any error.
    """
    if not headlines:
        return 0.0

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        logger.warning("anthropic_not_installed", fallback="vader")
        return 0.0

    api_key = settings.anthropic_api_key
    if not api_key:
        logger.warning("anthropic_api_key_missing", fallback="vader")
        return 0.0

    headlines_text = "\n".join(f"- {h}" for h in headlines[:10])
    user_message = (
        f"Market question: {question}\n\n"
        f"Recent headlines:\n{headlines_text}\n\n"
        "Sentiment score:"
    )

    start = time.monotonic()
    try:
        client = AsyncAnthropic(api_key=api_key, timeout=_TIMEOUT)
        response = await client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_message}],
        )
        raw = response.content[0].text.strip()
        score = float(raw)
        score = max(-1.0, min(1.0, score))

        elapsed = time.monotonic() - start
        input_tokens = response.usage.input_tokens
        output_tokens = response.usage.output_tokens
        cost = (input_tokens * 0.80 + output_tokens * 4.00) / 1_000_000

        logger.debug(
            "llm_sentiment_call",
            question=question[:60],
            score=score,
            elapsed_s=round(elapsed, 2),
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=round(cost, 6),
        )
        return score

    except Exception as e:
        elapsed = time.monotonic() - start
        logger.warning(
            "llm_sentiment_error",
            error=str(e),
            elapsed_s=round(elapsed, 2),
            fallback=0.0,
        )
        return 0.0
