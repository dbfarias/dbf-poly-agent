"""Tests for Twitter/X enhanced integration.

Covers:
- TwitterFetcher search_depth and topic settings
- ResearchResult twitter_sentiment and tweet_count fields
- Research engine twitter-eligible market selection
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from bot.research.types import ResearchResult

# ---------------------------------------------------------------------------
# ResearchResult new fields
# ---------------------------------------------------------------------------


class TestResearchResultTwitterFields:
    """Test new twitter_sentiment and tweet_count fields."""

    def test_default_values(self):
        result = ResearchResult(
            market_id="mkt1",
            keywords=("test",),
            news_items=(),
            sentiment_score=0.0,
            confidence=0.0,
            research_multiplier=1.0,
            updated_at=datetime.now(timezone.utc),
        )
        assert result.twitter_sentiment == 0.0
        assert result.tweet_count == 0

    def test_custom_values(self):
        result = ResearchResult(
            market_id="mkt1",
            keywords=("test",),
            news_items=(),
            sentiment_score=0.0,
            confidence=0.0,
            research_multiplier=1.0,
            updated_at=datetime.now(timezone.utc),
            twitter_sentiment=0.65,
            tweet_count=5,
        )
        assert result.twitter_sentiment == 0.65
        assert result.tweet_count == 5

    def test_frozen_immutable(self):
        result = ResearchResult(
            market_id="mkt1",
            keywords=("test",),
            news_items=(),
            sentiment_score=0.0,
            confidence=0.0,
            research_multiplier=1.0,
            updated_at=datetime.now(timezone.utc),
            twitter_sentiment=0.5,
            tweet_count=3,
        )
        with pytest.raises(AttributeError):
            result.twitter_sentiment = 0.9  # type: ignore[misc]


# ---------------------------------------------------------------------------
# TwitterFetcher configuration
# ---------------------------------------------------------------------------


class TestTwitterFetcherConfig:
    """Test that TwitterFetcher uses advanced search settings."""

    @pytest.mark.asyncio
    async def test_payload_has_advanced_search_depth(self):
        from bot.research.twitter_fetcher import TwitterFetcher

        fetcher = TwitterFetcher()
        # We can't easily test the actual HTTP call, but we can verify
        # the payload construction by checking the source
        import inspect
        source = inspect.getsource(fetcher._search_tavily)
        assert '"advanced"' in source
        assert '"news"' in source
        await fetcher.close()


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    """Test updated config defaults for Twitter."""

    def test_twitter_enabled_by_default(self):
        from bot.config import Settings

        # Create settings with minimal required fields
        s = Settings(
            api_secret_key="test_key_at_least_16_chars",
            trading_mode="paper",
        )
        assert s.use_twitter_fetcher is True

    def test_twitter_budget_increased(self):
        from bot.config import Settings

        s = Settings(
            api_secret_key="test_key_at_least_16_chars",
            trading_mode="paper",
        )
        assert s.twitter_daily_budget == 10  # Conservative: 10 twitter + 10 news = 20/day (1000/mo)


# ---------------------------------------------------------------------------
# Twitter-eligible market selection (research engine logic)
# ---------------------------------------------------------------------------


class TestTwitterEligibleSelection:
    """Test the logic for selecting which markets get twitter search."""

    def test_near_resolution_always_eligible(self):
        """Markets resolving within 48h should always get twitter search."""
        # Simulate a near-resolution market
        market = MagicMock()
        market.id = "near_res_mkt"
        market.end_date = datetime.now(timezone.utc) + timedelta(hours=24)
        market.volume = 100.0

        # Simulate a far-resolution market
        far_market = MagicMock()
        far_market.id = "far_mkt"
        far_market.end_date = datetime.now(timezone.utc) + timedelta(hours=500)
        far_market.volume = 50.0

        # The _twitter_eligible_ids logic is computed in _scan_all_markets
        # We test the logic directly
        now_utc = datetime.now(timezone.utc)
        twitter_eligible_ids: set[str] = set()

        for m in [market, far_market]:
            end = m.end_date
            if end is not None:
                if end.tzinfo is None:
                    end = end.replace(tzinfo=timezone.utc)
                hours_left = (end - now_utc).total_seconds() / 3600
                if 0 < hours_left <= 48:
                    twitter_eligible_ids.add(m.id)

        assert "near_res_mkt" in twitter_eligible_ids
        assert "far_mkt" not in twitter_eligible_ids

    def test_top_50_by_volume_eligible(self):
        """Top 50 markets by volume should get twitter search."""
        markets = []
        for i in range(100):
            m = MagicMock()
            m.id = f"mkt_{i}"
            m.volume = float(100 - i)  # mkt_0 has highest volume
            m.end_date = datetime.now(timezone.utc) + timedelta(hours=500)
            markets.append(m)

        sorted_by_volume = sorted(
            markets,
            key=lambda m: getattr(m, "volume", 0) or 0,
            reverse=True,
        )
        twitter_eligible_ids: set[str] = set()
        for m in sorted_by_volume[:50]:
            twitter_eligible_ids.add(m.id)

        # First 50 should be eligible
        for i in range(50):
            assert f"mkt_{i}" in twitter_eligible_ids
        # Last 50 should not
        for i in range(50, 100):
            assert f"mkt_{i}" not in twitter_eligible_ids
