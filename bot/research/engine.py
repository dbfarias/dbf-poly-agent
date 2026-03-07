"""Research engine — background task that fetches news and computes sentiment."""

import asyncio
from datetime import datetime, timezone

import structlog

from bot.config import settings
from bot.data.market_cache import MarketCache
from bot.research.cache import ResearchCache
from bot.research.crypto_fetcher import CryptoFetcher
from bot.research.keyword_extractor import extract_keywords
from bot.research.llm_sentiment import analyze_sentiment_llm
from bot.research.news_fetcher import NewsFetcher
from bot.research.sentiment import analyze_sentiment, compute_research_multiplier
from bot.research.types import ResearchResult

logger = structlog.get_logger()


class ResearchEngine:
    """Background engine that scans markets for news and sentiment.

    Runs every 30 minutes (independent of the 60s trading cycle).
    Populates ResearchCache with per-market sentiment data.
    """

    SCAN_INTERVAL = 900  # 15 minutes (fresher crypto + sentiment data)
    WARMUP_DELAY = 60  # Wait for market cache before first scan
    MAX_MARKETS = 50  # Scan more markets per cycle

    def __init__(
        self,
        research_cache: ResearchCache,
        market_cache: MarketCache,
    ):
        self.research_cache = research_cache
        self.market_cache = market_cache
        self.news_fetcher = NewsFetcher()
        self.crypto_fetcher = CryptoFetcher()
        self._running = False
        self._priority_market_ids: set[str] = set()

    def set_priority_markets(self, market_ids: set[str]) -> None:
        """Update priority markets (open positions + recent signals).

        Called by the trading engine after each cycle so research focuses
        on markets the bot actually cares about.
        """
        self._priority_market_ids = set(market_ids)

    @property
    def status(self) -> dict:
        """Engine status for API/dashboard."""
        return {
            "running": self._running,
            "scan_interval_seconds": self.SCAN_INTERVAL,
            "max_markets": self.MAX_MARKETS,
        }

    async def start(self) -> None:
        """Background loop: scan markets for news every SCAN_INTERVAL seconds."""
        self._running = True
        logger.info("research_engine_started", interval=self.SCAN_INTERVAL)

        # Wait briefly for market cache to populate from first trading cycle,
        # then run an immediate warm-up scan so strategies have data from cycle 1
        await asyncio.sleep(self.WARMUP_DELAY)

        while self._running:
            try:
                await self._scan_all_markets()
            except Exception as e:
                logger.error("research_scan_failed", error=str(e))

            await asyncio.sleep(self.SCAN_INTERVAL)

    async def stop(self) -> None:
        """Stop the background loop and close clients."""
        self._running = False
        await self.news_fetcher.close()
        await self.crypto_fetcher.close()
        logger.info("research_engine_stopped")

    async def _scan_all_markets(self) -> None:
        """For each cached market: extract keywords, fetch news, compute sentiment."""
        markets = self.market_cache.get_all_markets()

        # Prioritize markets with open positions or recent signals
        priority_ids = self._priority_market_ids
        priority = [m for m in markets if m.id in priority_ids]
        others = [m for m in markets if m.id not in priority_ids]
        remaining_slots = max(0, self.MAX_MARKETS - len(priority))
        scan_markets = priority + others[:remaining_slots]

        if not scan_markets:
            logger.debug("research_no_markets_to_scan")
            self.research_cache.record_scan(0)
            return

        # Fetch crypto sentiment and prices once per scan (shared across all markets)
        crypto_sentiment = await self.crypto_fetcher.get_market_sentiment()
        crypto_prices = await self.crypto_fetcher.get_prices()
        scanned = 0

        for market in scan_markets:
            try:
                result = await self._research_market(
                    market_id=market.id,
                    question=market.question,
                    crypto_sentiment=crypto_sentiment,
                    crypto_prices=crypto_prices,
                )
                if result is not None:
                    self.research_cache.set(market.id, result)
                    scanned += 1
            except Exception as e:
                logger.debug(
                    "research_market_failed",
                    market_id=market.id[:20],
                    error=str(e),
                )

        self.research_cache.record_scan(scanned)
        logger.info(
            "research_scan_complete",
            markets_scanned=scanned,
            total_markets=len(scan_markets),
        )

    async def _research_market(
        self,
        market_id: str,
        question: str,
        crypto_sentiment: dict[str, float],
        crypto_prices: dict[str, float] | None = None,
    ) -> ResearchResult | None:
        """Research a single market: keywords -> news -> sentiment -> multiplier."""
        keywords = extract_keywords(question)
        if not keywords:
            return None

        # Fetch news for this market's keywords
        news_items = await self.news_fetcher.fetch_news(keywords, max_results=10)

        # Compute headline sentiment
        headlines = [item.title for item in news_items]
        if settings.use_llm_sentiment and headlines:
            sentiment_score = await analyze_sentiment_llm(question, headlines)
        else:
            sentiment_score = analyze_sentiment(headlines)

        # Confidence based on article count (0-1)
        confidence = min(len(news_items) / 10.0, 1.0)

        # Compute research multiplier
        research_multiplier = compute_research_multiplier(
            sentiment=sentiment_score,
            article_count=len(news_items),
        )

        # Include crypto market trend for crypto-related markets
        crypto_score = 0.0
        question_lower = question.lower()
        crypto_keywords = {"bitcoin", "btc", "ethereum", "eth", "crypto", "blockchain"}
        if any(kw in question_lower for kw in crypto_keywords):
            crypto_score = crypto_sentiment.get("market_trend", 0.0)

        # Convert prices dict to immutable tuple of tuples
        prices_tuple = tuple(
            (coin, price) for coin, price in (crypto_prices or {}).items()
        )

        return ResearchResult(
            market_id=market_id,
            keywords=tuple(keywords),
            news_items=tuple(news_items),
            sentiment_score=sentiment_score,
            confidence=confidence,
            research_multiplier=research_multiplier,
            updated_at=datetime.now(timezone.utc),
            crypto_sentiment=crypto_score,
            crypto_prices=prices_tuple,
        )
