"""Tests for CategoryClassifier — regex fast-path, cache, and LLM fallback."""

import pytest

from bot.research.category_classifier import (
    CategoryClassifier,
    _category_cache,
    get_cached_category,
)


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the module-level cache before each test."""
    _category_cache.clear()
    yield
    _category_cache.clear()


class TestRegexFastPath:
    """Test that regex-based classification works without LLM."""

    @pytest.mark.asyncio
    async def test_sports_classification(self):
        classifier = CategoryClassifier()
        result = await classifier.classify_market("m1", "Will the Lakers win on 2026-03-10?")
        assert result == "sports"

    @pytest.mark.asyncio
    async def test_crypto_classification(self):
        classifier = CategoryClassifier()
        result = await classifier.classify_market("m2", "Will Bitcoin reach $200k by July?")
        assert result == "crypto"

    @pytest.mark.asyncio
    async def test_other_falls_through(self):
        """Non-sports, non-crypto questions get 'other' from regex fast-path."""
        classifier = CategoryClassifier()
        result = await classifier.classify_market("m3", "Will the Fed cut rates in June?")
        # Without LLM (no API key), this should be "other"
        assert result == "other"

    @pytest.mark.asyncio
    async def test_esports_classification(self):
        classifier = CategoryClassifier()
        result = await classifier.classify_market("m4", "Team A vs Team B in Valorant BO3")
        assert result == "sports"


class TestCaching:
    """Test cache hit/miss behavior."""

    @pytest.mark.asyncio
    async def test_cache_miss_then_hit(self):
        classifier = CategoryClassifier()
        # First call — cache miss, goes through regex
        result1 = await classifier.classify_market("m1", "Will the Lakers win?")
        assert result1 == "sports"

        # Second call — cache hit (even with different question text)
        result2 = await classifier.classify_market("m1", "totally different question")
        assert result2 == "sports"

    def test_get_cached_category_miss(self):
        assert get_cached_category("nonexistent") is None

    def test_get_cached_category_hit(self):
        _category_cache["m5"] = "economics"
        assert get_cached_category("m5") == "economics"

    @pytest.mark.asyncio
    async def test_cache_populated_after_classify(self):
        classifier = CategoryClassifier()
        assert get_cached_category("m6") is None
        await classifier.classify_market("m6", "Will Ethereum hit $10k?")
        assert get_cached_category("m6") == "crypto"


class TestLLMFallback:
    """Test LLM fallback behavior (without actually calling the API)."""

    @pytest.mark.asyncio
    async def test_no_api_key_returns_other(self, monkeypatch):
        """Without an API key, LLM fallback is skipped."""
        monkeypatch.setattr("bot.research.category_classifier.settings.anthropic_api_key", "")
        classifier = CategoryClassifier()
        result = await classifier.classify_market("m7", "Will Congress pass the bill?")
        assert result == "other"

    @pytest.mark.asyncio
    async def test_over_budget_returns_other(self, monkeypatch):
        """When over budget, LLM fallback is skipped."""
        monkeypatch.setattr(
            "bot.research.category_classifier.settings.anthropic_api_key", "sk-test",
        )

        from bot.research.llm_debate import cost_tracker
        original_budget = cost_tracker.daily_budget
        cost_tracker.daily_budget = 0.0  # Set budget to 0 so it's always over

        classifier = CategoryClassifier()
        result = await classifier.classify_market("m8", "Will the Senate confirm the nominee?")
        assert result == "other"

        cost_tracker.daily_budget = original_budget  # Restore
