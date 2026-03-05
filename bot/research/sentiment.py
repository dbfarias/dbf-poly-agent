"""VADER-based sentiment analysis for news headlines."""

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

_analyzer = SentimentIntensityAnalyzer()


def analyze_sentiment(headlines: list[str]) -> float:
    """Return weighted average VADER compound score [-1, 1].

    More recent headlines (earlier in list) get higher weight.
    Returns 0.0 if no headlines.
    """
    if not headlines:
        return 0.0

    total_weight = 0.0
    weighted_sum = 0.0

    for i, headline in enumerate(headlines):
        # Weight decreases linearly: most recent = highest weight
        weight = len(headlines) - i
        score = _analyzer.polarity_scores(headline)["compound"]
        weighted_sum += score * weight
        total_weight += weight

    return weighted_sum / total_weight if total_weight > 0 else 0.0


def get_headline_sentiment(headline: str) -> float:
    """Get VADER compound sentiment for a single headline."""
    return _analyzer.polarity_scores(headline)["compound"]


def compute_research_multiplier(sentiment: float, article_count: int) -> float:
    """Convert sentiment score to trading multiplier [0.7, 1.3].

    Logic:
    - Positive sentiment (>0.15) -> multiplier < 1.0 (market likely priced in, lower edge bar)
    - Negative sentiment (<-0.15) -> multiplier > 1.0 (raise edge bar, be cautious)
    - Neutral / no articles -> 1.0 (no effect)
    - Min 1 article for any effect (Polymarket questions rarely get 3+ hits)

    The multiplier adjusts the edge requirement:
    - multiplier < 1.0 = more permissive (lower effective min_edge)
    - multiplier > 1.0 = more cautious (higher effective min_edge)
    """
    # Need at least 1 article for any signal
    if article_count < 1:
        return 1.0

    # Neutral zone: no adjustment
    if -0.15 <= sentiment <= 0.15:
        return 1.0

    # Scale confidence by article count (max at 5+ articles)
    confidence = min(article_count / 5.0, 1.0)

    if sentiment > 0.15:
        # Positive news: lower the edge bar (multiplier < 1.0)
        # Max effect: 0.7 at sentiment=1.0 with 5+ articles
        effect = (sentiment - 0.15) / 0.85  # 0 to 1
        return max(0.7, 1.0 - (0.3 * effect * confidence))

    # Negative news: raise the edge bar (multiplier > 1.0)
    # Max effect: 1.3 at sentiment=-1.0 with 5+ articles
    effect = (-sentiment - 0.15) / 0.85  # 0 to 1
    return min(1.3, 1.0 + (0.3 * effect * confidence))
