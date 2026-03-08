"""LLM-powered market category classifier with regex fast-path and caching."""

import re

import structlog

from bot.config import settings

logger = structlog.get_logger()

_MODEL = "claude-haiku-4-5-20251001"
_TIMEOUT = 10.0
_MAX_TOKENS = 20

_VALID_CATEGORIES = frozenset({
    "crypto", "politics", "economics", "science",
    "entertainment", "legal", "weather", "sports", "other",
})

_SYSTEM_PROMPT = (
    "Classify this prediction market question into exactly one category: "
    "crypto, politics, economics, science, entertainment, legal, weather, "
    "sports, other. Output ONLY the category name."
)

# Module-level cache: market_id -> category string
_category_cache: dict[str, str] = {}


def _calc_cost(input_tokens: int, output_tokens: int) -> float:
    """Haiku cost: $0.80/M input, $4.00/M output."""
    return (input_tokens * 0.80 + output_tokens * 4.00) / 1_000_000


def _sanitize(text: str) -> str:
    """Sanitize external text before interpolation into LLM prompts."""
    cleaned = re.sub(r"[\x00-\x1f\x7f]", " ", text)
    return re.sub(r" {2,}", " ", cleaned).strip()[:200]


def get_cached_category(market_id: str) -> str | None:
    """Sync lookup of cached category. Returns None on miss."""
    return _category_cache.get(market_id)


class CategoryClassifier:
    """Classify prediction markets into categories using regex + LLM fallback."""

    async def classify_market(self, market_id: str, question: str) -> str:
        """Classify a market question. Returns a category string.

        Flow: cache -> regex fast-path -> LLM (if available and within budget).
        """
        # 1. Cache hit
        cached = _category_cache.get(market_id)
        if cached is not None:
            return cached

        # 2. Regex fast-path via existing classifier
        category = self._regex_classify(question)
        if category != "other":
            _category_cache[market_id] = category
            return category

        # 3. LLM classification (if available)
        llm_category = await self._llm_classify(question)
        if llm_category is not None:
            category = llm_category

        _category_cache[market_id] = category
        return category

    def _regex_classify(self, question: str) -> str:
        """Use existing regex-based classifier as fast-path."""
        from bot.agent.market_analyzer import classify_market_type

        return classify_market_type(question)

    async def _llm_classify(self, question: str) -> str | None:
        """Use Haiku LLM for classification. Returns None if unavailable."""
        if not settings.anthropic_api_key:
            return None

        from bot.research.llm_debate import cost_tracker

        if cost_tracker.is_over_budget:
            logger.debug("category_classifier_over_budget")
            return None

        try:
            from anthropic import AsyncAnthropic
        except ImportError:
            return None

        sanitized = _sanitize(question)

        try:
            client = AsyncAnthropic(
                api_key=settings.anthropic_api_key,
                timeout=_TIMEOUT,
            )
            resp = await client.messages.create(
                model=_MODEL,
                max_tokens=_MAX_TOKENS,
                system=_SYSTEM_PROMPT,
                messages=[{"role": "user", "content": sanitized}],
            )

            text = resp.content[0].text.strip().lower()
            cost = _calc_cost(resp.usage.input_tokens, resp.usage.output_tokens)
            cost_tracker.add(cost)

            logger.debug(
                "category_llm_classified",
                question=question[:60],
                category=text,
                cost=round(cost, 6),
            )

            if text in _VALID_CATEGORIES:
                return text

            # LLM returned something unexpected — fall back
            logger.warning("category_llm_invalid_response", response=text)
            return None

        except Exception as e:
            logger.error("category_llm_error", error=str(e))
            return None
