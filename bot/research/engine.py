"""Research engine — background task that fetches news and computes sentiment."""

import asyncio
from datetime import datetime, timezone

import structlog

from bot.agent.market_analyzer import classify_market_type
from bot.config import settings
from bot.data.market_cache import MarketCache
from bot.research.cache import ResearchCache
from bot.research.category_classifier import CategoryClassifier
from bot.research.correlation_detector import CorrelationDetector
from bot.research.crypto_fetcher import CryptoFetcher
from bot.research.keyword_extractor import extract_keywords, extract_keywords_llm
from bot.research.llm_sentiment import analyze_sentiment_llm
from bot.research.news_fetcher import NewsFetcher
from bot.research.pattern_analyzer import PatternAnalyzer
from bot.research.reddit_fetcher import RedditFetcher
from bot.research.resolution_parser import parse_resolution_criteria
from bot.research.sentiment import analyze_sentiment, compute_research_multiplier
from bot.research.twitter_fetcher import TwitterFetcher
from bot.research.types import NewsItem, ResearchResult
from bot.research.volume_detector import VolumeAnomalyDetector
from bot.research.whale_detector import WhaleDetector

logger = structlog.get_logger()


class ResearchEngine:
    """Background engine that scans markets for news and sentiment.

    Runs every 30 minutes (independent of the 60s trading cycle).
    Populates ResearchCache with per-market sentiment data.
    """

    SCAN_INTERVAL = 900  # 15 minutes (fresher crypto + sentiment data)
    WARMUP_DELAY = 60  # Wait for market cache before first scan
    MAX_MARKETS = 150  # Cover 3x more markets with sentiment data

    def __init__(
        self,
        research_cache: ResearchCache,
        market_cache: MarketCache,
    ):
        self.research_cache = research_cache
        self.market_cache = market_cache
        self.news_fetcher = NewsFetcher()
        self.crypto_fetcher = CryptoFetcher()
        self.reddit_fetcher = RedditFetcher()
        self.twitter_fetcher = TwitterFetcher()
        self.volume_detector = VolumeAnomalyDetector()
        self.correlation_detector = CorrelationDetector()
        self.category_classifier = CategoryClassifier()
        self.pattern_analyzer = PatternAnalyzer()
        self.whale_detector: WhaleDetector | None = None
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
        await self.reddit_fetcher.close()
        await self.twitter_fetcher.close()
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

        # Update volume anomaly detector and correlation groups
        anomaly_ids = self.volume_detector.update(scan_markets)
        self.correlation_detector.update(scan_markets)

        # Prioritize anomaly markets (move to front)
        if anomaly_ids:
            anomaly_set = set(anomaly_ids)
            anomaly_markets = [m for m in scan_markets if m.id in anomaly_set]
            non_anomaly = [m for m in scan_markets if m.id not in anomaly_set]
            scan_markets = anomaly_markets + non_anomaly

        # Fetch crypto sentiment and prices once per scan (shared across all markets)
        crypto_sentiment = await self.crypto_fetcher.get_market_sentiment()
        crypto_prices = await self.crypto_fetcher.get_prices()
        scanned = 0

        for market in scan_markets:
            try:
                result = await self._research_market(
                    market_id=market.id,
                    question=market.question,
                    description=getattr(market, "description", ""),
                    crypto_sentiment=crypto_sentiment,
                    crypto_prices=crypto_prices,
                    is_volume_anomaly=self.volume_detector.is_anomaly(market.id),
                    token_ids=getattr(market, "token_ids", []),
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
        description: str = "",
        crypto_sentiment: dict[str, float] | None = None,
        crypto_prices: dict[str, float] | None = None,
        is_volume_anomaly: bool = False,
        token_ids: list[str] | None = None,
    ) -> ResearchResult | None:
        """Research a single market: keywords -> news + reddit -> sentiment -> multiplier."""
        if crypto_sentiment is None:
            crypto_sentiment = {}

        if settings.use_llm_keywords:
            keywords = await extract_keywords_llm(question)
        else:
            keywords = extract_keywords(question)
        if not keywords:
            return None

        # Parse resolution criteria from description (cached per market)
        resolution_condition = ""
        resolution_source = ""
        description_context = ""
        if description:
            criteria = await parse_resolution_criteria(
                market_id, question, description,
            )
            if criteria is not None:
                resolution_condition = criteria.condition
                resolution_source = criteria.data_source
                # Enrich keywords with data source
                if (
                    criteria.data_source != "Unknown"
                    and criteria.data_source.lower() not in " ".join(keywords).lower()
                ):
                    keywords = keywords + [criteria.data_source]
            # Truncate description for context
            description_context = description[:300]

        # Fetch news from Google News
        news_items = await self.news_fetcher.fetch_news(keywords, max_results=10)

        # Fetch Reddit posts (supplementary)
        category = classify_market_type(question)
        reddit_items = await self.reddit_fetcher.fetch_posts(
            keywords, category=category, max_results=5,
        )

        # Fetch Twitter/X posts (priority markets only to stay within daily budget)
        twitter_items: list[NewsItem] = []
        if settings.use_twitter_fetcher and market_id in self._priority_market_ids:
            twitter_items = await self.twitter_fetcher.fetch_tweets(
                keywords, category=category, max_results=5,
            )

        # Merge and deduplicate (Jaccard > 0.6 on titles = duplicate)
        supplementary_items = list(reddit_items) + list(twitter_items)
        merged_items = list(news_items)
        for item in supplementary_items:
            item_words = set(item.title.lower().split())
            is_dup = False
            for existing in merged_items:
                existing_words = set(existing.title.lower().split())
                if item_words and existing_words:
                    jaccard = len(item_words & existing_words) / len(
                        item_words | existing_words
                    )
                    if jaccard > 0.6:
                        is_dup = True
                        break
            if not is_dup:
                merged_items.append(item)

        # Compute headline sentiment
        headlines = [item.title for item in merged_items]
        if settings.use_llm_sentiment and headlines:
            sentiment_score = await analyze_sentiment_llm(question, headlines)
        else:
            sentiment_score = analyze_sentiment(headlines)

        # Confidence based on article count (0-1)
        confidence = min(len(merged_items) / 10.0, 1.0)

        # Compute research multiplier
        research_multiplier = compute_research_multiplier(
            sentiment=sentiment_score,
            article_count=len(merged_items),
        )

        # Classify market category (regex fast-path + LLM fallback)
        market_category = await self.category_classifier.classify_market(
            market_id, question,
        )

        # Historical pattern matching (base rate from similar past trades)
        base_rate = await self.pattern_analyzer.compute_base_rate(question)
        historical_base_rate = base_rate if base_rate is not None else 0.0

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

        # Check whale activity from WebSocket order book data
        whale_activity = False
        if self.whale_detector and token_ids:
            for tid in token_ids:
                if self.whale_detector.has_whale_activity_by_token(tid):
                    whale_activity = True
                    break

        return ResearchResult(
            market_id=market_id,
            keywords=tuple(keywords),
            news_items=tuple(merged_items),
            sentiment_score=sentiment_score,
            confidence=confidence,
            research_multiplier=research_multiplier,
            updated_at=datetime.now(timezone.utc),
            crypto_sentiment=crypto_score,
            crypto_prices=prices_tuple,
            description_context=description_context,
            resolution_condition=resolution_condition,
            resolution_source=resolution_source,
            is_volume_anomaly=is_volume_anomaly,
            whale_activity=whale_activity,
            market_category=market_category,
            historical_base_rate=historical_base_rate,
        )
