"""Resolution criteria parser — extracts HOW markets resolve from descriptions."""

import re
import time
from dataclasses import dataclass

import structlog

logger = structlog.get_logger()

_MODEL = "claude-haiku-4-5-20251001"
_TIMEOUT = 10.0
_MAX_TOKENS = 200
_MIN_DESCRIPTION_LEN = 20


@dataclass(frozen=True)
class ResolutionCriteria:
    """Parsed resolution criteria for a market."""

    condition: str  # "BTC closes above $100k on Coinbase"
    data_source: str  # "Coinbase", "Official government data"
    is_binary: bool  # True for simple yes/no


# Module-level cache — markets don't change resolution criteria
_resolution_cache: dict[str, ResolutionCriteria] = {}

# Regex patterns for fast-path extraction
_RESOLVE_YES_IF = re.compile(
    r"(?:will\s+)?resolve[sd]?\s+(?:to\s+)?(?:\"?yes\"?|positively)\s+"
    r"if\s+(.+?)(?:\.|$)",
    re.IGNORECASE,
)
_RESOLUTION_SOURCE = re.compile(
    r"(?:resolution\s+source|according\s+to|based\s+on|as\s+reported\s+by)"
    r"[:\s]+([^.]+)",
    re.IGNORECASE,
)
_RESOLVE_CONDITION = re.compile(
    r"(?:this\s+market\s+)?(?:will\s+)?resolve[sd]?\s+(?:to\s+)?\"?yes\"?\s+"
    r"(?:if|when)\s+(.+?)(?:\.\s|$)",
    re.IGNORECASE,
)

_SYSTEM_PROMPT = (
    "Extract resolution criteria from a prediction market description.\n"
    "Output EXACTLY 3 lines:\n"
    "CONDITION: <what must happen for YES resolution>\n"
    "SOURCE: <data source for resolution, or 'Unknown'>\n"
    "BINARY: YES or NO\n\n"
    "Be concise. If unclear, use 'Unknown'."
)


def get_cached_criteria(market_id: str) -> ResolutionCriteria | None:
    """Sync lookup of cached resolution criteria."""
    return _resolution_cache.get(market_id)


async def parse_resolution_criteria(
    market_id: str,
    question: str,
    description: str,
) -> ResolutionCriteria | None:
    """Parse resolution criteria from market description.

    1. Check cache (instant return)
    2. Try regex fast path (no API cost)
    3. Fall back to Haiku LLM (~$0.00005 per call)
    """
    # 1. Cache hit
    cached = _resolution_cache.get(market_id)
    if cached is not None:
        return cached

    # Validate description
    if not description or len(description.strip()) < _MIN_DESCRIPTION_LEN:
        return None

    # 2. Regex fast path
    result = _regex_parse(description)
    if result is not None:
        _resolution_cache[market_id] = result
        logger.debug(
            "resolution_parsed_regex",
            market_id=market_id[:16],
            condition=result.condition[:60],
        )
        return result

    # 3. LLM fallback (only if API key configured and budget available)
    result = await _llm_parse(market_id, question, description)
    if result is not None:
        _resolution_cache[market_id] = result
    return result


def _regex_parse(description: str) -> ResolutionCriteria | None:
    """Try to extract resolution criteria via regex patterns."""
    condition = None
    source = "Unknown"

    # Try "resolve to Yes if..." pattern
    match = _RESOLVE_YES_IF.search(description)
    if match:
        condition = match.group(1).strip()

    # Try broader "resolve yes when/if..." pattern
    if condition is None:
        match = _RESOLVE_CONDITION.search(description)
        if match:
            condition = match.group(1).strip()

    # Extract resolution source
    source_match = _RESOLUTION_SOURCE.search(description)
    if source_match:
        source = source_match.group(1).strip()

    if condition is None:
        return None

    # Clean up condition
    condition = condition[:200]  # Truncate long conditions

    return ResolutionCriteria(
        condition=condition,
        data_source=source,
        is_binary=True,  # Polymarket markets are always binary
    )


async def _llm_parse(
    market_id: str, question: str, description: str
) -> ResolutionCriteria | None:
    """Parse resolution criteria using Haiku LLM."""
    from bot.config import settings
    from bot.research.llm_debate import cost_tracker

    if not settings.use_llm_keywords:
        return None

    api_key = settings.anthropic_api_key
    if not api_key:
        return None

    if cost_tracker.is_over_budget:
        return None

    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        return None

    # Sanitize inputs
    safe_q = re.sub(r"[\x00-\x1f\x7f]", " ", question)[:200].strip()
    safe_desc = re.sub(r"[\x00-\x1f\x7f]", " ", description)[:500].strip()

    user_msg = f"Question: {safe_q}\n\nDescription: {safe_desc}"

    try:
        client = AsyncAnthropic(api_key=api_key, timeout=_TIMEOUT)
        start = time.monotonic()
        resp = await client.messages.create(
            model=_MODEL,
            max_tokens=_MAX_TOKENS,
            system=_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": user_msg}],
        )
        text = resp.content[0].text.strip()

        cost = (
            resp.usage.input_tokens * 0.80
            + resp.usage.output_tokens * 4.00
        ) / 1_000_000
        cost_tracker.add(cost)
        elapsed = time.monotonic() - start

        # Parse structured output
        condition = "Unknown"
        source = "Unknown"
        is_binary = True

        for line in text.split("\n"):
            upper = line.upper().strip()
            if upper.startswith("CONDITION:"):
                condition = line.split(":", 1)[1].strip()
            elif upper.startswith("SOURCE:"):
                source = line.split(":", 1)[1].strip()
            elif upper.startswith("BINARY:"):
                is_binary = "YES" in upper

        if condition == "Unknown":
            return None

        result = ResolutionCriteria(
            condition=condition[:200],
            data_source=source[:100],
            is_binary=is_binary,
        )

        logger.debug(
            "resolution_parsed_llm",
            market_id=market_id[:16],
            condition=condition[:60],
            source=source[:30],
            cost_usd=round(cost, 6),
            elapsed_s=round(elapsed, 2),
        )

        return result
    except Exception as e:
        logger.warning(
            "resolution_llm_parse_failed",
            market_id=market_id[:16],
            error=str(e),
        )
        return None
