"""Historical pattern matching — computes base rates from similar past trades."""

import re
import time

import structlog

from bot.data.database import async_session
from bot.data.repositories import TradeRepository

logger = structlog.get_logger()

# Cache TTL in seconds (1 hour)
_CACHE_TTL = 3600

# Minimum similar trades to compute a base rate
_MIN_SIMILAR_TRADES = 5

# Stop words for tokenization (same as correlation_detector)
_STOP_WORDS = frozenset({
    "a", "an", "the", "is", "are", "was", "were", "be", "been", "being",
    "will", "would", "could", "should", "may", "might", "can", "do", "does",
    "did", "has", "have", "had", "if", "or", "and", "but", "not", "no",
    "in", "on", "at", "to", "for", "of", "with", "by", "from", "up",
    "about", "into", "through", "during", "before", "after", "above",
    "below", "between", "out", "off", "over", "under", "than", "too",
    "very", "just", "also", "more", "most", "other", "some", "such",
    "any", "each", "every", "all", "both", "few", "how", "what", "which",
    "who", "whom", "this", "that", "these", "those", "when", "where",
    "why", "so", "because", "as", "until", "while", "it", "its",
    "he", "she", "they", "them", "his", "her", "their", "there",
    "yes", "no", "market", "resolve",
})

_MIN_TOKEN_LEN = 3

# Pattern detection regexes
_PRICE_TARGET_RE = re.compile(
    r"\b(reach|hit|above|below|exceed)\b.*\$[\d,]+", re.IGNORECASE
)
_DEADLINE_RE = re.compile(
    r"\b(before|by)\b.*\b(\d{1,2}[/-]\d{1,2}|\w+ \d{1,2}|\d{4})\b",
    re.IGNORECASE,
)
_WIN_OUTCOME_RE = re.compile(r"\bwill\b.+\bwin\b", re.IGNORECASE)
_PERCENTAGE_RE = re.compile(
    r"\b(above|below|exceed)\b.*\d+(\.\d+)?%", re.IGNORECASE
)


def _tokenize(text: str) -> frozenset[str]:
    """Tokenize text into meaningful words (same logic as correlation_detector)."""
    cleaned = re.sub(r"[^\w\s]", " ", text.lower())
    tokens = {
        word
        for word in cleaned.split()
        if len(word) >= _MIN_TOKEN_LEN and word not in _STOP_WORDS
    }
    return frozenset(tokens)


def _jaccard(a: frozenset[str], b: frozenset[str]) -> float:
    """Compute Jaccard similarity between two token sets."""
    if not a or not b:
        return 0.0
    intersection = len(a & b)
    union = len(a | b)
    return intersection / union if union > 0 else 0.0


def _extract_pattern_type(question: str) -> str:
    """Extract the pattern type from a market question.

    Returns one of: price_target, deadline_event, win_outcome,
    percentage, binary_event.
    """
    if _PRICE_TARGET_RE.search(question):
        return "price_target"
    if _WIN_OUTCOME_RE.search(question):
        return "win_outcome"
    if _PERCENTAGE_RE.search(question):
        return "percentage"
    if _DEADLINE_RE.search(question):
        return "deadline_event"
    return "binary_event"


class PatternAnalyzer:
    """Analyzes historical trade patterns to compute base rates.

    Uses keyword overlap (Jaccard similarity) + pattern type matching
    to find similar past trades and compute win rates.
    """

    # Jaccard threshold for considering questions similar
    SIMILARITY_THRESHOLD = 0.3

    def __init__(self) -> None:
        self._pattern_cache: dict[str, float] = {}
        self._cache_timestamps: dict[str, float] = {}

    async def compute_base_rate(self, question: str) -> float | None:
        """Return historical win rate for similar questions, or None if insufficient data.

        Results are cached for 1 hour per question pattern key.
        """
        pattern_type = _extract_pattern_type(question)
        cache_key = f"{pattern_type}:{question[:100]}"

        # Check cache
        cached_ts = self._cache_timestamps.get(cache_key)
        if cached_ts is not None and (time.monotonic() - cached_ts) < _CACHE_TTL:
            return self._pattern_cache.get(cache_key)

        try:
            async with async_session() as session:
                repo = TradeRepository(session)
                resolved_trades = await repo.get_resolved_with_questions(days=90)
        except Exception as e:
            logger.error("pattern_analyzer_db_error", error=str(e))
            return None

        if not resolved_trades:
            return None

        question_tokens = _tokenize(question)

        # Find similar trades by pattern type + keyword overlap
        similar_trades = []
        for trade in resolved_trades:
            trade_pattern = _extract_pattern_type(trade.question)

            # Pattern type must match
            if trade_pattern != pattern_type:
                continue

            trade_tokens = _tokenize(trade.question)
            similarity = _jaccard(question_tokens, trade_tokens)

            if similarity >= self.SIMILARITY_THRESHOLD:
                similar_trades.append(trade)

        if len(similar_trades) < _MIN_SIMILAR_TRADES:
            # Cache the "no data" result too
            self._pattern_cache[cache_key] = 0.0
            self._cache_timestamps[cache_key] = time.monotonic()
            return None

        wins = sum(1 for t in similar_trades if t.pnl > 0)
        base_rate = wins / len(similar_trades)

        # Cache result
        self._pattern_cache[cache_key] = base_rate
        self._cache_timestamps[cache_key] = time.monotonic()

        logger.info(
            "pattern_base_rate_computed",
            pattern_type=pattern_type,
            similar_count=len(similar_trades),
            base_rate=round(base_rate, 3),
            question=question[:60],
        )

        return base_rate

    async def refresh_patterns(self) -> None:
        """Rebuild cache from DB (called by research engine periodically)."""
        self._pattern_cache.clear()
        self._cache_timestamps.clear()
        logger.info("pattern_cache_refreshed")
