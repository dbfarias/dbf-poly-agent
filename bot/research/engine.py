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
from bot.research.fear_greed_fetcher import FearGreedFetcher
from bot.research.fred_fetcher import FredFetcher
from bot.research.keyword_extractor import extract_keywords, extract_keywords_llm
from bot.research.llm_sentiment import analyze_sentiment_llm, should_use_llm
from bot.research.manifold_fetcher import ManifoldFetcher
from bot.research.news_fetcher import NewsFetcher
from bot.research.pattern_analyzer import PatternAnalyzer
from bot.research.reddit_fetcher import RedditFetcher
from bot.research.resolution_parser import parse_resolution_criteria
from bot.research.sentiment import analyze_sentiment, compute_enhanced_multiplier
from bot.research.sports_fetcher import SportsFetcher, is_sports_market
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
        self.sports_fetcher = SportsFetcher()
        self.fear_greed = FearGreedFetcher()
        self.manifold = ManifoldFetcher()
        self.fred = FredFetcher()
        self.weather_fetcher: object | None = None  # Set by TradingEngine
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
        await self.sports_fetcher.close()
        logger.info("research_engine_stopped")

    async def trigger_scan(self) -> int:
        """Manual scan trigger via API. Returns number of markets scanned."""
        if not self._running:
            return 0
        await self._scan_all_markets()
        return self.MAX_MARKETS

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

        # Build twitter-eligible set: top 100 by volume + near-resolution (<48h)
        # + high-volume markets up to 7 days out
        # Priority markets always get twitter regardless
        sorted_by_volume = sorted(
            scan_markets,
            key=lambda m: getattr(m, "volume", 0) or 0,
            reverse=True,
        )
        twitter_eligible_ids = set(priority_ids)
        for m in sorted_by_volume[:100]:
            twitter_eligible_ids.add(m.id)
        # Near-resolution markets always get twitter
        # High-volume markets up to 7 days out also get twitter
        now_utc = datetime.now(timezone.utc)
        for m in scan_markets:
            end = getattr(m, "end_date", None)
            if end is not None:
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                hours_left = (end - now_utc).total_seconds() / 3600
                volume = getattr(m, "volume", 0) or 0
                if 0 < hours_left <= 48:
                    # All near-resolution markets
                    twitter_eligible_ids.add(m.id)
                elif 48 < hours_left <= 168 and volume >= 5000:
                    # High-volume markets up to 7 days out
                    twitter_eligible_ids.add(m.id)
        self._twitter_eligible_ids = twitter_eligible_ids

        # Sort scan_markets: near-resolution first, then rest
        def _resolution_priority(m):
            end = getattr(m, "end_date", None)
            if end is None:
                return 9999.0
            if end.tzinfo is None:
                end = end.replace(tzinfo=timezone.utc)
            hours = (end - now_utc).total_seconds() / 3600
            if hours <= 48:
                return hours  # Near-resolution at front
            return 1000.0 + hours

        scan_markets = sorted(scan_markets, key=_resolution_priority)

        # Update volume anomaly detector and correlation groups
        anomaly_ids = self.volume_detector.update(scan_markets)
        self.correlation_detector.update(scan_markets)

        # Prioritize anomaly markets (move to front)
        if anomaly_ids:
            anomaly_set = set(anomaly_ids)
            anomaly_markets = [m for m in scan_markets if m.id in anomaly_set]
            non_anomaly = [m for m in scan_markets if m.id not in anomaly_set]
            scan_markets = anomaly_markets + non_anomaly

        # Fetch shared data once per scan
        crypto_sentiment = await self.crypto_fetcher.get_market_sentiment()
        crypto_prices = await self.crypto_fetcher.get_prices()
        fear_greed_val, _ = await self.fear_greed.get_index()
        await self.manifold.refresh_markets()
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
                    fear_greed_val=fear_greed_val,
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
        fear_greed_val: int = 50,
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

        # Fetch Twitter/X posts (top 50 by volume + near-resolution + priority)
        twitter_items: list[NewsItem] = []
        twitter_eligible = getattr(self, "_twitter_eligible_ids", set())
        if settings.use_twitter_fetcher and market_id in twitter_eligible:
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

        # Compute headline sentiment (news + reddit only, without twitter)
        # Hybrid mode: always VADER first, upgrade to LLM when uncertain
        news_headlines = [item.title for item in merged_items if item.source != "Twitter/X"]
        all_headlines = [item.title for item in merged_items]
        vader_score = analyze_sentiment(news_headlines) if news_headlines else 0.0
        if should_use_llm(vader_score, len(all_headlines)) and all_headlines:
            sentiment_score = await analyze_sentiment_llm(question, all_headlines)
        else:
            sentiment_score = vader_score

        # Separate Twitter sentiment
        tweet_headlines = [item.title for item in twitter_items]
        twitter_sentiment = analyze_sentiment(tweet_headlines) if tweet_headlines else 0.0
        tweet_count = len(twitter_items)

        # Confidence based on article count (0-1)
        confidence = min(len(merged_items) / 10.0, 1.0)

        # Compute research multiplier (enhanced when tweets available)
        research_multiplier = compute_enhanced_multiplier(
            sentiment=sentiment_score,
            twitter_sentiment=twitter_sentiment,
            article_count=len(merged_items),
            tweet_count=tweet_count,
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

        # Cross-platform: Manifold probability
        manifold_prob = 0.0
        try:
            mp = self.manifold.find_matching_probability(question)
            if mp is not None:
                manifold_prob = mp
        except Exception:
            pass

        # Economic data: FRED
        fred_value = 0.0
        fred_series = ""
        try:
            series_name = self.fred.is_relevant_to_market(question)
            if series_name:
                val = await self.fred.get_latest(series_name)
                if val is not None:
                    fred_value = val
                    fred_series = series_name
        except Exception:
            pass

        # Sports odds lookup — compare sportsbook consensus vs Polymarket price
        sports_odds_prob = 0.0
        sports_bookmaker_count = 0
        if is_sports_market(question):
            try:
                all_odds = await self.sports_fetcher.get_all_odds()
                match = self.sports_fetcher.match_polymarket_to_game(
                    question, all_odds,
                )
                if match:
                    sports_odds_prob, sports_bookmaker_count, _ = match
            except Exception as e:
                logger.debug("sports_odds_lookup_error", error=str(e))

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
            twitter_sentiment=twitter_sentiment,
            tweet_count=tweet_count,
            sports_odds_prob=sports_odds_prob,
            sports_bookmaker_count=sports_bookmaker_count,
            fear_greed_index=fear_greed_val,
            manifold_prob=manifold_prob,
            fred_value=fred_value,
            fred_series=fred_series,
        )
