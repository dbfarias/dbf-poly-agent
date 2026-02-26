"""Tests for research cross-validation improvements.

Covers:
- Research direction check (sentiment agrees/disagrees with trade direction)
- compute_enhanced_multiplier (news + twitter agreement/divergence)
- Research alignment in debate context
"""

import pytest

from bot.research.llm_debate import DebateContext, _format_context_block
from bot.research.sentiment import (
    compute_enhanced_multiplier,
    compute_research_multiplier,
)

# ---------------------------------------------------------------------------
# compute_enhanced_multiplier tests
# ---------------------------------------------------------------------------


class TestComputeEnhancedMultiplier:
    """Test enhanced multiplier combining news + twitter."""

    def test_no_tweets_falls_back_to_base(self):
        base = compute_research_multiplier(sentiment=0.5, article_count=5)
        enhanced = compute_enhanced_multiplier(
            sentiment=0.5,
            twitter_sentiment=0.0,
            article_count=5,
            tweet_count=0,
        )
        assert enhanced == base

    def test_both_positive_amplifies(self):
        """Both news and twitter positive → amplified effect."""
        base = compute_research_multiplier(sentiment=0.5, article_count=5)
        enhanced = compute_enhanced_multiplier(
            sentiment=0.5,
            twitter_sentiment=0.6,
            article_count=5,
            tweet_count=3,
        )
        # Both positive → more permissive (lower multiplier)
        # Amplified effect should push further from 1.0
        assert enhanced < base  # Amplified = more extreme

    def test_both_negative_amplifies(self):
        """Both news and twitter negative → amplified cautious effect."""
        base = compute_research_multiplier(sentiment=-0.5, article_count=5)
        enhanced = compute_enhanced_multiplier(
            sentiment=-0.5,
            twitter_sentiment=-0.6,
            article_count=5,
            tweet_count=3,
        )
        # Both negative → more cautious (higher multiplier)
        assert enhanced > base

    def test_diverging_neutralizes(self):
        """News positive + twitter negative → neutralized toward 1.0."""
        enhanced = compute_enhanced_multiplier(
            sentiment=0.5,
            twitter_sentiment=-0.5,
            article_count=5,
            tweet_count=3,
        )
        # Should be closer to 1.0 than either individual multiplier
        base_news = compute_research_multiplier(0.5, 5)
        assert abs(enhanced - 1.0) < abs(base_news - 1.0)

    def test_neutral_sentiment_returns_near_one(self):
        enhanced = compute_enhanced_multiplier(
            sentiment=0.0,
            twitter_sentiment=0.0,
            article_count=5,
            tweet_count=3,
        )
        assert abs(enhanced - 1.0) < 0.01

    def test_output_range_bounded(self):
        """Multiplier stays within [0.5, 1.5]."""
        for s in [-1.0, -0.5, 0.0, 0.5, 1.0]:
            for ts in [-1.0, -0.5, 0.0, 0.5, 1.0]:
                result = compute_enhanced_multiplier(
                    sentiment=s,
                    twitter_sentiment=ts,
                    article_count=10,
                    tweet_count=5,
                )
                assert 0.5 <= result <= 1.5, f"Out of range: {result} for s={s}, ts={ts}"


# ---------------------------------------------------------------------------
# Research direction check tests (logic validation)
# ---------------------------------------------------------------------------


class TestResearchDirectionCheck:
    """Test the direction check logic that would run in engine._evaluate_signals."""

    def _simulate_direction_check(
        self,
        buying_yes: bool,
        sentiment_score: float,
        confidence: float,
        edge_multiplier: float = 1.0,
    ) -> tuple[float, dict]:
        """Simulate the direction check logic from engine.py."""
        metadata: dict = {}

        if confidence >= 0.3:
            sentiment_agrees = (
                (buying_yes and sentiment_score > 0.1)
                or (not buying_yes and sentiment_score < -0.1)
            )
            if not sentiment_agrees and abs(sentiment_score) > 0.2:
                edge_multiplier *= 0.7
                metadata["research_disagrees"] = True
            elif sentiment_agrees and confidence >= 0.5:
                edge_multiplier *= 1.2
                metadata["research_agrees"] = True

        return edge_multiplier, metadata

    def test_buying_yes_positive_sentiment_agrees(self):
        mult, meta = self._simulate_direction_check(
            buying_yes=True, sentiment_score=0.5, confidence=0.6,
        )
        assert meta.get("research_agrees") is True
        assert mult == pytest.approx(1.2)

    def test_buying_yes_negative_sentiment_disagrees(self):
        mult, meta = self._simulate_direction_check(
            buying_yes=True, sentiment_score=-0.4, confidence=0.5,
        )
        assert meta.get("research_disagrees") is True
        assert mult == pytest.approx(0.7)

    def test_buying_no_negative_sentiment_agrees(self):
        mult, meta = self._simulate_direction_check(
            buying_yes=False, sentiment_score=-0.5, confidence=0.6,
        )
        assert meta.get("research_agrees") is True
        assert mult == pytest.approx(1.2)

    def test_buying_no_positive_sentiment_disagrees(self):
        mult, meta = self._simulate_direction_check(
            buying_yes=False, sentiment_score=0.4, confidence=0.5,
        )
        assert meta.get("research_disagrees") is True
        assert mult == pytest.approx(0.7)

    def test_low_confidence_no_adjustment(self):
        mult, meta = self._simulate_direction_check(
            buying_yes=True, sentiment_score=-0.5, confidence=0.2,
        )
        assert "research_agrees" not in meta
        assert "research_disagrees" not in meta
        assert mult == 1.0

    def test_weak_sentiment_no_penalty(self):
        """Sentiment between -0.2 and 0.2 doesn't trigger penalty."""
        mult, meta = self._simulate_direction_check(
            buying_yes=True, sentiment_score=-0.15, confidence=0.5,
        )
        assert "research_disagrees" not in meta
        assert mult == 1.0

    def test_agrees_requires_high_confidence(self):
        """Agreement boost requires confidence >= 0.5."""
        mult, meta = self._simulate_direction_check(
            buying_yes=True, sentiment_score=0.5, confidence=0.35,
        )
        # confidence < 0.5 → no boost even if sentiment agrees
        assert "research_agrees" not in meta
        assert mult == 1.0


# ---------------------------------------------------------------------------
# Debate context research alignment tests
# ---------------------------------------------------------------------------


class TestDebateContextResearchAlignment:
    """Test research alignment info in debate context formatting."""

    def test_research_agrees_shows_checkmark(self):
        ctx = DebateContext(research_agrees=True)
        block = _format_context_block(ctx)
        assert "✅" in block
        assert "AGREES" in block

    def test_research_disagrees_shows_warning(self):
        ctx = DebateContext(research_agrees=False)
        block = _format_context_block(ctx)
        assert "⚠️" in block
        assert "DISAGREES" in block
        assert "RED FLAG" in block

    def test_research_unknown_is_silent(self):
        """When research_agrees is None, no alignment info is emitted (neutral)."""
        ctx = DebateContext(research_agrees=None)
        block = _format_context_block(ctx)
        assert "RESEARCH ALIGNMENT" not in block

    def test_twitter_sentiment_included(self):
        ctx = DebateContext(twitter_sentiment=0.45, tweet_count=3)
        block = _format_context_block(ctx)
        assert "TWITTER/X" in block
        assert "+0.45" in block

    def test_zero_tweet_count_excluded(self):
        ctx = DebateContext(twitter_sentiment=0.45, tweet_count=0)
        block = _format_context_block(ctx)
        assert "TWITTER/X" not in block

    def test_zero_twitter_sentiment_excluded(self):
        ctx = DebateContext(twitter_sentiment=0.0, tweet_count=0)
        block = _format_context_block(ctx)
        assert "TWITTER/X" not in block
