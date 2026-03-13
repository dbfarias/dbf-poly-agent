"""News sniping pipeline — poll RSS, match headlines to markets, score.

Zero LLM cost: uses keyword recall + VADER sentiment to detect
breaking news that may move Polymarket prices before the market reacts.
"""

import asyncio
import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

from bot.agent.market_analyzer import classify_market_type
from bot.data.market_cache import MarketCache
from bot.research.keyword_extractor import extract_keywords
from bot.research.news_fetcher import NewsFetcher
from bot.research.sentiment import get_headline_sentiment

logger = structlog.get_logger()

# LRU dedup capacity
_DEDUP_MAX = 5000

# Minimum thresholds for a snipe candidate
MIN_KEYWORD_OVERLAP = 0.30  # Keyword recall: % of market keywords found in headline
MIN_SENTIMENT_ABS = 0.08
MIN_PRICE = 0.05
MAX_PRICE = 0.95


@dataclass(frozen=True)
class SnipeCandidate:
    """A headline that matches a market and passes scoring thresholds."""

    market_id: str
    question: str
    headline: str
    source: str
    sentiment: float
    keyword_overlap: float
    yes_price: float
    published: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class NewsSniper:
    """Poll RSS feeds and match breaking headlines to Polymarket markets.

    Algorithm (60s cycle):
    1. Build keyword index from cached markets (refreshed every 5 min)
    2. Fetch RSS via NewsFetcher (top 50 markets by volume)
    3. For each new headline (dedup by SHA-256, LRU 5000):
       - Compute keyword recall (market keywords found in headline)
       - If recall >= 0.30 AND abs(VADER sentiment) >= 0.08
         AND price in [0.05, 0.95]: emit SnipeCandidate
    """

    KEYWORD_REFRESH_INTERVAL = 300  # 5 min

    def __init__(self, market_cache: MarketCache):
        self._market_cache = market_cache
        self._news_fetcher = NewsFetcher()
        self._seen_hashes: OrderedDict[str, bool] = OrderedDict()
        self._keyword_index: dict[str, set[str]] = {}  # market_id -> keywords set
        self._market_questions: dict[str, str] = {}
        self._market_prices: dict[str, float] = {}  # market_id -> yes_price
        self._last_keyword_refresh: float = 0.0
        self._candidates: list[SnipeCandidate] = []

    def _refresh_keyword_index(self) -> None:
        """Rebuild keyword index from cached markets."""
        now = datetime.now(timezone.utc).timestamp()
        if now - self._last_keyword_refresh < self.KEYWORD_REFRESH_INTERVAL:
            return

        markets = self._market_cache.get_all_markets()
        new_index: dict[str, set[str]] = {}
        new_questions: dict[str, str] = {}
        new_prices: dict[str, float] = {}

        for market in markets:
            # Skip sports
            mtype = classify_market_type(market.question)
            if mtype == "sports":
                continue

            yes_price = market.yes_price
            if yes_price is None:
                continue

            keywords = extract_keywords(market.question)
            if len(keywords) < 2:
                continue

            kw_set = {kw.lower() for kw in keywords}
            new_index[market.id] = kw_set
            new_questions[market.id] = market.question
            new_prices[market.id] = yes_price

        self._keyword_index = new_index
        self._market_questions = new_questions
        self._market_prices = new_prices
        self._last_keyword_refresh = now

        logger.info(
            "news_sniper_keywords_refreshed",
            markets_indexed=len(new_index),
        )

    def _headline_hash(self, headline: str) -> str:
        """SHA-256 hash of normalized headline for dedup."""
        normalized = headline.strip().lower()
        return hashlib.sha256(normalized.encode()).hexdigest()[:16]

    def _is_seen(self, headline: str) -> bool:
        """Check if headline was already processed (LRU dedup)."""
        h = self._headline_hash(headline)
        if h in self._seen_hashes:
            self._seen_hashes.move_to_end(h)
            return True
        return False

    def _mark_seen(self, headline: str) -> None:
        """Mark headline as seen, evicting oldest if over capacity."""
        h = self._headline_hash(headline)
        self._seen_hashes[h] = True
        self._seen_hashes.move_to_end(h)
        while len(self._seen_hashes) > _DEDUP_MAX:
            self._seen_hashes.popitem(last=False)

    @staticmethod
    def jaccard_overlap(set_a: set[str], set_b: set[str]) -> float:
        """Compute Jaccard similarity between two keyword sets."""
        if not set_a or not set_b:
            return 0.0
        intersection = len(set_a & set_b)
        union = len(set_a | set_b)
        return intersection / union if union > 0 else 0.0

    @staticmethod
    def keyword_recall(headline_words: set[str], market_keywords: set[str]) -> float:
        """Fraction of market keywords found in headline.

        Better than Jaccard for matching: headlines have many extra words
        that dilute Jaccard, but recall focuses on how many *market* keywords
        the headline covers.
        """
        if not market_keywords or not headline_words:
            return 0.0
        return len(headline_words & market_keywords) / len(market_keywords)

    def _build_search_queries(self, max_queries: int = 5) -> list[list[str]]:
        """Build diverse search queries from market keywords.

        Strategy: pick one market per query, use its top 2-3 keywords.
        This yields specific RSS results that are more likely to match
        individual markets (vs. a single generic query).
        """
        # Score markets by keyword uniqueness (fewer shared keywords = more specific)
        all_kw_count: dict[str, int] = {}
        for kw_set in self._keyword_index.values():
            for kw in kw_set:
                all_kw_count[kw] = all_kw_count.get(kw, 0) + 1

        market_scores: list[tuple[str, float]] = []
        for market_id, kw_set in self._keyword_index.items():
            if len(kw_set) < 2:
                continue
            # Prefer markets with specific (low-frequency) keywords
            specificity = sum(1.0 / all_kw_count[kw] for kw in kw_set)
            market_scores.append((market_id, specificity))

        # Sort by specificity (most specific first)
        market_scores.sort(key=lambda x: x[1], reverse=True)

        queries: list[list[str]] = []
        used_keywords: set[str] = set()

        for market_id, _ in market_scores:
            if len(queries) >= max_queries:
                break

            kw_set = self._keyword_index[market_id]
            # Pick up to 3 keywords, preferring specific ones
            sorted_kws = sorted(kw_set, key=lambda k: all_kw_count.get(k, 0))
            query_kws = [kw for kw in sorted_kws[:3] if len(kw) > 2]

            if len(query_kws) < 2:
                continue

            # Skip if too similar to existing queries
            kw_frozen = frozenset(query_kws)
            if kw_frozen & used_keywords == kw_frozen:
                continue

            queries.append(query_kws)
            used_keywords.update(query_kws)

        return queries

    async def poll(self) -> list[SnipeCandidate]:
        """Run one polling cycle: fetch RSS, match to markets, return candidates.

        Returns list of SnipeCandidates found this cycle.
        """
        self._refresh_keyword_index()

        if not self._keyword_index:
            return []

        # Build diverse search queries from market keywords.
        # Instead of one query with the most-frequent keywords (which yields
        # generic results), we create several small queries from different
        # markets so the RSS results are more likely to match.
        queries = self._build_search_queries(max_queries=5)

        if not queries:
            return []

        logger.info(
            "news_sniper_polling",
            queries=queries[:3],
            markets_indexed=len(self._keyword_index),
        )

        # Fetch in parallel (one HTTP call per query)
        fetch_tasks = [
            self._news_fetcher.fetch_news(q, max_results=10)
            for q in queries
        ]
        results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        # Merge & deduplicate by title
        seen_titles: set[str] = set()
        news_items = []
        for result in results:
            if isinstance(result, Exception):
                logger.warning("news_query_failed", error=str(result))
                continue
            for item in result:
                title_key = item.title.strip().lower()
                if title_key not in seen_titles:
                    seen_titles.add(title_key)
                    news_items.append(item)

        logger.info(
            "news_sniper_fetched",
            items=len(news_items),
            queries=len(queries),
        )

        candidates: list[SnipeCandidate] = []

        for item in news_items:
            if self._is_seen(item.title):
                continue
            self._mark_seen(item.title)

            headline_words = {w.lower() for w in item.title.split() if len(w) > 2}
            sentiment = get_headline_sentiment(item.title)

            if abs(sentiment) < MIN_SENTIMENT_ABS:
                continue

            # Match against all indexed markets using keyword recall
            best_overlap = 0.0
            matched_any = False
            for market_id, market_kw in self._keyword_index.items():
                overlap = self.keyword_recall(headline_words, market_kw)
                best_overlap = max(best_overlap, overlap)
                if overlap < MIN_KEYWORD_OVERLAP:
                    continue
                matched_any = True

                yes_price = self._market_prices.get(market_id, 0.5)
                if yes_price < MIN_PRICE or yes_price > MAX_PRICE:
                    continue

                candidates.append(
                    SnipeCandidate(
                        market_id=market_id,
                        question=self._market_questions.get(market_id, ""),
                        headline=item.title,
                        source=item.source,
                        sentiment=sentiment,
                        keyword_overlap=overlap,
                        yes_price=yes_price,
                        published=item.published,
                    )
                )

            if not matched_any and best_overlap > 0:
                logger.debug(
                    "news_headline_no_match",
                    headline=item.title[:60],
                    sentiment=round(sentiment, 3),
                    best_overlap=round(best_overlap, 3),
                    min_overlap=MIN_KEYWORD_OVERLAP,
                )

        if candidates:
            logger.info(
                "news_sniper_candidates_found",
                count=len(candidates),
                headlines=[c.headline[:60] for c in candidates[:3]],
            )

        self._candidates = candidates
        return candidates

    def get_candidates(self) -> list[SnipeCandidate]:
        """Return candidates from last poll cycle."""
        return list(self._candidates)

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._news_fetcher.close()
