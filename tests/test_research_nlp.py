"""Tests for keyword extraction and sentiment analysis."""

import pytest

from bot.research.keyword_extractor import extract_keywords
from bot.research.sentiment import (
    analyze_sentiment,
    compute_research_multiplier,
    get_headline_sentiment,
)


class TestExtractKeywords:
    def test_political_question(self):
        keywords = extract_keywords("Will Trump win the 2024 presidential election?")
        assert "trump" in keywords
        assert len(keywords) >= 2

    def test_crypto_question(self):
        keywords = extract_keywords("Will Bitcoin reach $100k by December 2025?")
        assert "bitcoin" in keywords or "btc" in keywords
        assert len(keywords) >= 2

    def test_generic_question(self):
        keywords = extract_keywords(
            "Will the Federal Reserve cut interest rates in March?"
        )
        assert "federal reserve" in keywords or "interest" in keywords
        assert len(keywords) >= 2

    def test_empty_question(self):
        assert extract_keywords("") == []
        assert extract_keywords("Hi") == []

    def test_short_question(self):
        assert extract_keywords("Yes?") == []

    def test_strips_prefix(self):
        kw1 = extract_keywords("Will Tesla stock price exceed $500?")
        kw2 = extract_keywords("Tesla stock price exceed $500?")
        # Both should contain tesla
        assert "tesla" in kw1
        assert "tesla" in kw2

    def test_max_five_keywords(self):
        q = "Will Donald Trump defeat Joe Biden in the US Presidential Election November 2026?"
        keywords = extract_keywords(q)
        assert len(keywords) <= 5

    def test_sports_question(self):
        keywords = extract_keywords(
            "Will the Kansas City Chiefs win Super Bowl LIX?"
        )
        assert any(kw in keywords for kw in ["super bowl", "chiefs", "kansas"])


class TestAnalyzeSentiment:
    def test_positive_headlines(self):
        headlines = [
            "Great success as prices soar beautifully!",
            "Wonderful achievement with excellent results!",
            "Amazing gains bring joy and happiness!",
        ]
        score = analyze_sentiment(headlines)
        assert score > 0.2

    def test_negative_headlines(self):
        headlines = [
            "Market crashes as fears grow!",
            "Devastating losses hit crypto sector!",
            "Worst day in market history!",
        ]
        score = analyze_sentiment(headlines)
        assert score < -0.2

    def test_mixed_headlines(self):
        headlines = [
            "Bitcoin surges to all-time high!",
            "Terrible crash wipes out gains!",
            "Market is stable and unchanged.",
        ]
        score = analyze_sentiment(headlines)
        assert -0.5 < score < 0.5  # Mixed should be near zero

    def test_empty_returns_zero(self):
        assert analyze_sentiment([]) == 0.0

    def test_single_headline(self):
        score = analyze_sentiment(["Excellent success and great achievement!"])
        assert score > 0.0


class TestGetHeadlineSentiment:
    def test_positive(self):
        assert get_headline_sentiment("Great success and wonderful achievement!") > 0

    def test_negative(self):
        assert get_headline_sentiment("Terrible disaster and horrible failure!") < 0


class TestComputeResearchMultiplier:
    def test_positive_sentiment_lowers_multiplier(self):
        mult = compute_research_multiplier(sentiment=0.7, article_count=10)
        assert mult < 1.0
        assert mult >= 0.7

    def test_negative_sentiment_raises_multiplier(self):
        mult = compute_research_multiplier(sentiment=-0.7, article_count=10)
        assert mult > 1.0
        assert mult <= 1.3

    def test_neutral_returns_one(self):
        # Within ±0.15 neutral zone
        assert compute_research_multiplier(sentiment=0.1, article_count=10) == 1.0
        assert compute_research_multiplier(sentiment=-0.1, article_count=10) == 1.0

    def test_outside_neutral_zone_has_effect(self):
        # ±0.2 is outside the ±0.15 zone → should have effect
        assert compute_research_multiplier(sentiment=0.2, article_count=5) < 1.0
        assert compute_research_multiplier(sentiment=-0.2, article_count=5) > 1.0

    def test_few_articles_returns_one(self):
        # 0 articles = no effect
        assert compute_research_multiplier(sentiment=0.9, article_count=0) == 1.0

    def test_one_article_has_effect(self):
        # 1 article with strong sentiment → should have effect (threshold lowered)
        mult = compute_research_multiplier(sentiment=0.8, article_count=1)
        assert mult < 1.0

    def test_three_articles_has_effect(self):
        mult = compute_research_multiplier(sentiment=0.8, article_count=3)
        assert mult < 1.0  # Positive sentiment should lower multiplier

    def test_more_articles_stronger_effect(self):
        mult_1 = compute_research_multiplier(sentiment=0.8, article_count=1)
        mult_5 = compute_research_multiplier(sentiment=0.8, article_count=5)
        assert mult_5 < mult_1  # More articles = stronger effect

    def test_bounds(self):
        # Max positive: should not go below 0.7
        assert compute_research_multiplier(sentiment=1.0, article_count=100) >= 0.7
        # Max negative: should not go above 1.3
        assert compute_research_multiplier(sentiment=-1.0, article_count=100) <= 1.3
