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
    """Convert sentiment score to trading multiplier [0.5, 1.5].

    Logic:
    - Positive sentiment (>0.15) -> multiplier < 1.0 (market likely priced in, lower edge bar)
    - Negative sentiment (<-0.15) -> multiplier > 1.0 (raise edge bar, be cautious)
    - Neutral / no articles -> 1.0 (no effect)
    - Min 1 article for any effect (Polymarket questions rarely get 3+ hits)

    High-confidence signals (5+ articles, strong sentiment) get amplified range.

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

    # High-confidence amplified range: 5+ articles + strong sentiment
    high_confidence = article_count >= 5 and abs(sentiment) > 0.5
    max_effect = 0.5 if high_confidence else 0.3
    floor = 0.5 if high_confidence else 0.7
    ceil = 1.5 if high_confidence else 1.3

    if sentiment > 0.15:
        # Positive news: lower the edge bar (multiplier < 1.0)
        effect = (sentiment - 0.15) / 0.85  # 0 to 1
        return max(floor, 1.0 - (max_effect * effect * confidence))

    # Negative news: raise the edge bar (multiplier > 1.0)
    effect = (-sentiment - 0.15) / 0.85  # 0 to 1
    return min(ceil, 1.0 + (max_effect * effect * confidence))


def compute_enhanced_multiplier(
    sentiment: float,
    twitter_sentiment: float,
    article_count: int,
    tweet_count: int,
) -> float:
    """Enhanced research multiplier that combines news + Twitter signals.

    When both sources agree → amplified effect (stronger signal).
    When they diverge → neutralized (confused signal → stay neutral).
    Falls back to compute_research_multiplier when no tweets available.

    Returns multiplier in [0.5, 1.5] range.
    """
    if tweet_count == 0:
        return compute_research_multiplier(sentiment, article_count)

    base_mult = compute_research_multiplier(sentiment, article_count)
    twitter_mult = compute_research_multiplier(twitter_sentiment, tweet_count)

    # Check agreement: both push same direction from 1.0
    news_direction = base_mult - 1.0  # positive = cautious, negative = permissive
    twitter_direction = twitter_mult - 1.0

    both_agree = (news_direction > 0 and twitter_direction > 0) or (
        news_direction < 0 and twitter_direction < 0
    )

    if both_agree:
        # Amplify: average the deviations and boost by 40%
        avg_deviation = (news_direction + twitter_direction) / 2.0
        amplified = 1.0 + avg_deviation * 1.4
        return max(0.5, min(1.5, amplified))

    # Diverge: neutralize toward 1.0
    # Stronger divergence → closer to 1.0
    divergence = abs(news_direction - twitter_direction)
    dampening = min(1.0, divergence * 2.0)  # 0.5 divergence → full dampening
    avg_mult = (base_mult + twitter_mult) / 2.0
    neutralized = avg_mult + (1.0 - avg_mult) * dampening
    return max(0.5, min(1.5, neutralized))
