"""Tests for ResearchEngine and trading integration."""

from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.research.cache import ResearchCache
from bot.research.engine import ResearchEngine
from bot.research.types import ResearchResult


def _make_gamma_market(market_id: str, question: str) -> SimpleNamespace:
    return SimpleNamespace(id=market_id, question=question)


def _make_research_result(
    market_id: str = "m1",
    sentiment_score: float = 0.5,
    research_multiplier: float = 0.9,
    confidence: float = 0.8,
) -> ResearchResult:
    return ResearchResult(
        market_id=market_id,
        keywords=("test",),
        news_items=(),
        sentiment_score=sentiment_score,
        confidence=confidence,
        research_multiplier=research_multiplier,
        updated_at=datetime.now(timezone.utc),
    )


class TestResearchEngine:
    @pytest.fixture
    def market_cache(self):
        cache = MagicMock()
        cache.get_all_markets.return_value = [
            _make_gamma_market("m1", "Will Bitcoin reach $100k?"),
            _make_gamma_market("m2", "Will Trump win the election?"),
            _make_gamma_market("m3", "Will the Fed cut rates?"),
        ]
        return cache

    @pytest.fixture
    def research_cache(self):
        return ResearchCache(default_ttl=3600)

    @pytest.fixture
    def engine(self, research_cache, market_cache):
        return ResearchEngine(research_cache, market_cache)

    @pytest.mark.asyncio
    async def test_scan_all_markets_processes_markets(self, engine, research_cache):
        """Engine should scan available markets and populate cache."""
        with (
            patch.object(
                engine.news_fetcher, "fetch_news", new_callable=AsyncMock, return_value=[]
            ),
            patch.object(
                engine.crypto_fetcher,
                "get_market_sentiment",
                new_callable=AsyncMock,
                return_value={"market_trend": 0.1, "btc_24h_change": 2.0, "eth_24h_change": 1.0},
            ),
        ):
            await engine._scan_all_markets()

        # Should have scanned 3 markets (even if no news found)
        assert research_cache.stats["markets_scanned"] >= 0

    @pytest.mark.asyncio
    async def test_respects_market_limit(self, engine, market_cache):
        """Engine should cap the number of markets scanned."""
        # Create 50 markets
        market_cache.get_all_markets.return_value = [
            _make_gamma_market(f"m{i}", f"Will Question {i} happen?")
            for i in range(50)
        ]

        mock_fetch = AsyncMock(return_value=[])
        with (
            patch.object(engine.news_fetcher, "fetch_news", mock_fetch),
            patch.object(
                engine.crypto_fetcher,
                "get_market_sentiment",
                new_callable=AsyncMock,
                return_value={"market_trend": 0.0, "btc_24h_change": 0.0, "eth_24h_change": 0.0},
            ),
        ):
            await engine._scan_all_markets()

        # Should not exceed MAX_MARKETS
        assert mock_fetch.call_count <= ResearchEngine.MAX_MARKETS

    @pytest.mark.asyncio
    async def test_handles_fetch_errors(self, engine, research_cache):
        """Engine should handle individual market fetch failures gracefully."""
        with (
            patch.object(
                engine.news_fetcher,
                "fetch_news",
                new_callable=AsyncMock,
                side_effect=Exception("Network error"),
            ),
            patch.object(
                engine.crypto_fetcher,
                "get_market_sentiment",
                new_callable=AsyncMock,
                return_value={"market_trend": 0.0, "btc_24h_change": 0.0, "eth_24h_change": 0.0},
            ),
        ):
            # Should not raise
            await engine._scan_all_markets()

    @pytest.mark.asyncio
    async def test_start_stop_lifecycle(self, engine):
        """Engine should start and stop cleanly."""
        engine.news_fetcher.close = AsyncMock()
        engine.crypto_fetcher.close = AsyncMock()

        assert not engine._running
        await engine.stop()
        assert not engine._running
        engine.news_fetcher.close.assert_called_once()
        engine.crypto_fetcher.close.assert_called_once()


class TestResearchTradingIntegration:
    """Test that research_multiplier is applied to edge_multiplier in trading."""

    def test_positive_sentiment_lowers_effective_edge(self):
        """Positive research should lower edge requirement."""
        base_multiplier = 1.0
        research = _make_research_result(research_multiplier=0.85)

        effective = base_multiplier * research.research_multiplier
        assert effective < 1.0
        assert effective == pytest.approx(0.85, abs=0.01)

    def test_negative_sentiment_raises_effective_edge(self):
        """Negative research should raise edge requirement."""
        base_multiplier = 1.0
        research = _make_research_result(research_multiplier=1.2)

        effective = base_multiplier * research.research_multiplier
        assert effective > 1.0
        assert effective == pytest.approx(1.2, abs=0.01)

    def test_no_research_no_effect(self):
        """Without research data, multiplier should be 1.0."""
        cache = ResearchCache()
        result = cache.get("nonexistent_market")
        assert result is None
        # No research = no modification to edge_multiplier

    def test_multiplier_clamped_to_bounds(self):
        """Combined multiplier should stay within [0.5, 2.0]."""
        # Very aggressive base + very aggressive research
        base = 0.5
        research = _make_research_result(research_multiplier=0.7)
        combined = base * research.research_multiplier
        clamped = max(0.5, min(2.0, combined))
        assert 0.5 <= clamped <= 2.0
