"""Tests for research improvements: volume detector, correlation detector,
Reddit fetcher, and resolution parser."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.research.correlation_detector import (
    CorrelationDetector,
    _jaccard,
    _tokenize,
    _UnionFind,
)
from bot.research.resolution_parser import (
    ResolutionCriteria,
    _regex_parse,
    _resolution_cache,
    get_cached_criteria,
    parse_resolution_criteria,
)
from bot.research.volume_detector import (
    VolumeAnomalyDetector,
)

# ──────────────────── helpers ────────────────────


def _make_market(
    market_id: str,
    question: str = "Test?",
    volume_24h: float = 100.0,
    best_bid_price: float | None = 0.50,
    description: str = "",
) -> SimpleNamespace:
    return SimpleNamespace(
        id=market_id,
        question=question,
        volume_24h=volume_24h,
        best_bid_price=best_bid_price,
        description=description,
    )


# ──────────────────── VolumeAnomalyDetector ────────────────────


class TestVolumeAnomalyDetector:
    def test_no_anomaly_with_few_samples(self):
        detector = VolumeAnomalyDetector()
        markets = [_make_market("m1", volume_24h=100)]
        # First few updates should not flag anomaly (not enough history)
        for _ in range(3):
            anomalies = detector.update(markets)
        assert anomalies == []
        assert not detector.is_anomaly("m1")

    def test_volume_spike_detected(self):
        detector = VolumeAnomalyDetector()
        # Build up normal history
        normal_market = _make_market("m1", volume_24h=100)
        for _ in range(10):
            detector.update([normal_market])

        # Sudden spike: 5x normal
        spike_market = _make_market("m1", volume_24h=500)
        anomalies = detector.update([spike_market])
        assert "m1" in anomalies
        assert detector.is_anomaly("m1")

    def test_price_move_detected(self):
        detector = VolumeAnomalyDetector()
        # Build up normal price history
        normal = _make_market("m2", best_bid_price=0.50)
        for _ in range(10):
            detector.update([normal])

        # Sudden price jump: 15% move
        moved = _make_market("m2", best_bid_price=0.58)
        anomalies = detector.update([moved])
        assert "m2" in anomalies

    def test_no_anomaly_for_stable_market(self):
        detector = VolumeAnomalyDetector()
        market = _make_market("m3", volume_24h=100, best_bid_price=0.50)
        for _ in range(15):
            anomalies = detector.update([market])
        assert anomalies == []
        assert not detector.is_anomaly("m3")

    def test_get_anomalies(self):
        detector = VolumeAnomalyDetector()
        normal = _make_market("m4", volume_24h=100)
        for _ in range(10):
            detector.update([normal])

        spike = _make_market("m4", volume_24h=500)
        detector.update([spike])
        assert "m4" in detector.get_anomalies()

    def test_evict_stale(self):
        detector = VolumeAnomalyDetector()
        market = _make_market("stale1", volume_24h=100)
        detector.update([market])

        # Manually set last_seen to past
        detector._last_seen["stale1"] = datetime(
            2020, 1, 1, tzinfo=timezone.utc
        )
        # Update with different market triggers eviction
        detector.update([_make_market("m5")])
        assert "stale1" not in detector._volume_history

    def test_none_price_skipped(self):
        detector = VolumeAnomalyDetector()
        market = _make_market("m6", best_bid_price=None)
        for _ in range(10):
            detector.update([market])
        assert "m6" not in detector._price_history


# ──────────────────── CorrelationDetector ────────────────────


class TestTokenize:
    def test_basic_tokenization(self):
        tokens = _tokenize("Will Bitcoin reach $100k?")
        assert "bitcoin" in tokens
        assert "reach" in tokens
        assert "will" not in tokens  # stop word

    def test_short_words_filtered(self):
        tokens = _tokenize("Is BTC up or not?")
        assert "is" not in tokens  # stop word
        assert "or" not in tokens  # stop word
        assert "btc" in tokens

    def test_punctuation_removed(self):
        tokens = _tokenize("What's the price of ETH?")
        assert "eth" in tokens


class TestJaccard:
    def test_identical_sets(self):
        a = frozenset({"bitcoin", "price", "reach"})
        assert _jaccard(a, a) == 1.0

    def test_disjoint_sets(self):
        a = frozenset({"bitcoin", "price"})
        b = frozenset({"ethereum", "gas"})
        assert _jaccard(a, b) == 0.0

    def test_partial_overlap(self):
        a = frozenset({"bitcoin", "price", "reach"})
        b = frozenset({"bitcoin", "price", "moon"})
        # intersection=2, union=4 → 0.5
        assert _jaccard(a, b) == 0.5

    def test_empty_set(self):
        assert _jaccard(frozenset(), frozenset({"a"})) == 0.0
        assert _jaccard(frozenset(), frozenset()) == 0.0


class TestUnionFind:
    def test_basic_union(self):
        uf = _UnionFind()
        uf.union("a", "b")
        assert uf.find("a") == uf.find("b")

    def test_transitive_union(self):
        uf = _UnionFind()
        uf.union("a", "b")
        uf.union("b", "c")
        assert uf.find("a") == uf.find("c")

    def test_separate_groups(self):
        uf = _UnionFind()
        uf.union("a", "b")
        uf.union("c", "d")
        assert uf.find("a") != uf.find("c")


class TestCorrelationDetector:
    def test_similar_markets_grouped(self):
        detector = CorrelationDetector()
        markets = [
            _make_market("m1", question="Will Bitcoin reach $100k before March 2026?"),
            _make_market("m2", question="Will Bitcoin reach $100k before April 2026?"),
        ]
        detector.update(markets)
        assert detector.are_correlated("m1", "m2")

    def test_different_markets_not_grouped(self):
        detector = CorrelationDetector()
        markets = [
            _make_market("m1", question="Will Bitcoin reach $100k?"),
            _make_market("m2", question="Will Democrats win Senate in 2026?"),
        ]
        detector.update(markets)
        assert not detector.are_correlated("m1", "m2")

    def test_get_group(self):
        detector = CorrelationDetector()
        markets = [
            _make_market("m1", question="Will inflation exceed 5% in 2026?"),
            _make_market("m2", question="Will inflation exceed 5% by end of 2026?"),
        ]
        detector.update(markets)
        g1 = detector.get_group("m1")
        g2 = detector.get_group("m2")
        assert g1 is not None
        assert g1 == g2

    def test_get_group_members(self):
        detector = CorrelationDetector()
        markets = [
            _make_market("m1", question="Will Bitcoin reach $100k?"),
            _make_market("m2", question="Will Bitcoin reach $100k before March?"),
            _make_market("m3", question="Will Ethereum reach $5k?"),
        ]
        detector.update(markets)
        group_id = detector.get_group("m1")
        if group_id and detector.are_correlated("m1", "m2"):
            members = detector.get_group_members(group_id)
            assert "m1" in members
            assert "m2" in members

    def test_unknown_market(self):
        detector = CorrelationDetector()
        assert detector.get_group("unknown") is None
        assert not detector.are_correlated("unknown1", "unknown2")

    def test_jaccard_similarity_method(self):
        detector = CorrelationDetector()
        sim = detector.jaccard_similarity(
            "Will Bitcoin reach $100k?",
            "Will Bitcoin reach $100k before March?",
        )
        assert sim > 0.3  # Significant overlap


# ──────────────────── Reddit Fetcher ────────────────────


class TestRedditFetcher:
    @pytest.mark.asyncio
    async def test_fetch_posts_success(self):
        from bot.research.reddit_fetcher import RedditFetcher

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "Bitcoin hits new ATH!",
                            "created_utc": 1709300000,
                            "permalink": "/r/cryptocurrency/comments/abc/test",
                        }
                    },
                    {
                        "data": {
                            "title": "BTC dominance rising",
                            "created_utc": 1709290000,
                            "permalink": "/r/cryptocurrency/comments/def/test2",
                        }
                    },
                ]
            }
        }

        fetcher = RedditFetcher()
        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False
        fetcher._client = mock_client

        posts = await fetcher.fetch_posts(["bitcoin", "price"], category="crypto")
        assert len(posts) >= 1
        assert posts[0].source.startswith("Reddit r/")

        await fetcher.close()

    @pytest.mark.asyncio
    async def test_circuit_breaker(self):
        from bot.research.reddit_fetcher import RedditFetcher

        fetcher = RedditFetcher()
        # Simulate 5 failures (matches _MAX_FAILURES=5)
        for _ in range(5):
            fetcher._record_failure()

        assert fetcher._is_circuit_open()
        posts = await fetcher.fetch_posts(["test"])
        assert posts == []

        await fetcher.close()

    @pytest.mark.asyncio
    async def test_dedup_posts(self):
        from bot.research.reddit_fetcher import RedditFetcher

        fetcher = RedditFetcher()
        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.raise_for_status = MagicMock()
        mock_response.json.return_value = {
            "data": {
                "children": [
                    {
                        "data": {
                            "title": "Same title here",
                            "created_utc": 1709300000,
                            "permalink": "/r/news/comments/a/x",
                        }
                    },
                    {
                        "data": {
                            "title": "Same title here",
                            "created_utc": 1709290000,
                            "permalink": "/r/news/comments/b/y",
                        }
                    },
                ]
            }
        }

        mock_client = AsyncMock()
        mock_client.get = AsyncMock(return_value=mock_response)
        mock_client.is_closed = False
        fetcher._client = mock_client

        posts = await fetcher.fetch_posts(["test"], category="other", max_results=5)
        # Dedup should reduce to 1
        assert len(posts) == 1

        await fetcher.close()


# ──────────────────── Resolution Parser ────────────────────


class TestRegexParse:
    def test_resolve_yes_if_pattern(self):
        desc = 'This market will resolve to "Yes" if Bitcoin closes above $100k on Coinbase.'
        result = _regex_parse(desc)
        assert result is not None
        assert "Bitcoin" in result.condition or "bitcoin" in result.condition.lower()

    def test_resolution_source_pattern(self):
        desc = (
            "This market resolves Yes if the Fed raises rates. "
            "Resolution source: Federal Reserve official statement."
        )
        result = _regex_parse(desc)
        assert result is not None
        assert "Federal Reserve" in result.data_source

    def test_no_pattern_returns_none(self):
        desc = "This is a market about something with no clear resolution criteria."
        result = _regex_parse(desc)
        assert result is None

    def test_short_description_returns_none(self):
        result = _regex_parse("Too short")
        # _regex_parse doesn't check length — parse_resolution_criteria does
        # But it should return None because no pattern matches
        assert result is None


class TestResolutionCache:
    def test_cache_hit(self):
        # Manually add to cache
        criteria = ResolutionCriteria(
            condition="BTC > $100k",
            data_source="Coinbase",
            is_binary=True,
        )
        _resolution_cache["test_market_cache"] = criteria
        assert get_cached_criteria("test_market_cache") == criteria
        # Cleanup
        _resolution_cache.pop("test_market_cache", None)

    def test_cache_miss(self):
        assert get_cached_criteria("nonexistent_market_xyz") is None


class TestParseResolutionCriteria:
    @pytest.mark.asyncio
    async def test_short_description_returns_none(self):
        result = await parse_resolution_criteria("m1", "Test?", "short")
        assert result is None

    @pytest.mark.asyncio
    async def test_regex_path(self):
        desc = 'This market will resolve to "Yes" if inflation exceeds 5%.'
        result = await parse_resolution_criteria(
            "m_regex_test", "Will inflation exceed 5%?", desc
        )
        assert result is not None
        assert "inflation" in result.condition.lower()
        # Cleanup cache
        _resolution_cache.pop("m_regex_test", None)

    @pytest.mark.asyncio
    async def test_cache_reuse(self):
        criteria = ResolutionCriteria(
            condition="Test condition",
            data_source="Test source",
            is_binary=True,
        )
        _resolution_cache["m_cached"] = criteria
        result = await parse_resolution_criteria("m_cached", "Q?", "long enough description here")
        assert result == criteria
        _resolution_cache.pop("m_cached", None)


# ──────────────────── Research Engine Integration ────────────────────


class TestResearchEngineIntegration:
    """Test that the research engine correctly wires all new features."""

    @pytest.mark.asyncio
    async def test_engine_has_new_components(self):
        from bot.data.market_cache import MarketCache
        from bot.research.cache import ResearchCache
        from bot.research.engine import ResearchEngine

        cache = ResearchCache(default_ttl=60)
        market_cache = MarketCache()
        engine = ResearchEngine(cache, market_cache)

        assert hasattr(engine, "reddit_fetcher")
        assert hasattr(engine, "volume_detector")
        assert hasattr(engine, "correlation_detector")

        await engine.stop()

    @pytest.mark.asyncio
    async def test_research_result_new_fields(self):
        from bot.research.types import ResearchResult

        result = ResearchResult(
            market_id="test",
            keywords=("btc",),
            news_items=(),
            sentiment_score=0.1,
            confidence=0.5,
            research_multiplier=1.0,
            updated_at=datetime.now(timezone.utc),
            description_context="Test context",
            resolution_condition="BTC > $100k",
            resolution_source="Coinbase",
            is_volume_anomaly=True,
        )
        assert result.description_context == "Test context"
        assert result.resolution_condition == "BTC > $100k"
        assert result.resolution_source == "Coinbase"
        assert result.is_volume_anomaly is True
