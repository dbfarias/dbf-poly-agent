"""Tests for cross-platform convergence scoring."""


from bot.research.engine import _compute_convergence


class TestConvergence:
    def test_no_signals_returns_zero(self) -> None:
        score = _compute_convergence(
            sentiment_score=0.0,
            twitter_sentiment=0.0,
            manifold_prob=0.0,
            sports_odds_prob=0.0,
            fear_greed_val=50,
            is_crypto=False,
        )
        assert score == 0.0

    def test_all_bullish_returns_one(self) -> None:
        score = _compute_convergence(
            sentiment_score=0.5,
            twitter_sentiment=0.3,
            manifold_prob=0.7,
            sports_odds_prob=0.8,
            fear_greed_val=75,
            is_crypto=True,
        )
        assert score == 1.0

    def test_all_bearish_returns_one(self) -> None:
        """Full agreement on bearish direction is still convergence = 1.0."""
        score = _compute_convergence(
            sentiment_score=-0.5,
            twitter_sentiment=-0.3,
            manifold_prob=0.2,
            sports_odds_prob=0.1,
            fear_greed_val=20,
            is_crypto=True,
        )
        assert score == 1.0

    def test_mixed_signals(self) -> None:
        """Split signals should give convergence around 0.5."""
        score = _compute_convergence(
            sentiment_score=0.5,   # bullish
            twitter_sentiment=-0.3,  # bearish
            manifold_prob=0.7,     # bullish
            sports_odds_prob=0.3,  # bearish
            fear_greed_val=50,     # neutral (not counted)
            is_crypto=True,
        )
        # 2 bullish, 2 bearish → 2/4 = 0.5
        assert score == 0.5

    def test_fear_greed_only_for_crypto(self) -> None:
        """Fear & Greed should NOT be counted for non-crypto markets."""
        score_crypto = _compute_convergence(
            sentiment_score=0.0,
            twitter_sentiment=0.0,
            manifold_prob=0.0,
            sports_odds_prob=0.0,
            fear_greed_val=80,
            is_crypto=True,
        )
        score_non_crypto = _compute_convergence(
            sentiment_score=0.0,
            twitter_sentiment=0.0,
            manifold_prob=0.0,
            sports_odds_prob=0.0,
            fear_greed_val=80,
            is_crypto=False,
        )
        assert score_crypto == 1.0  # Only F&G signal, fully agrees with itself
        assert score_non_crypto == 0.0  # No signals

    def test_weak_sentiment_not_counted(self) -> None:
        """Sentiment below 0.1 threshold should not be counted."""
        score = _compute_convergence(
            sentiment_score=0.05,   # Too weak
            twitter_sentiment=0.08,  # Too weak
            manifold_prob=0.0,
            sports_odds_prob=0.0,
            fear_greed_val=50,
            is_crypto=False,
        )
        assert score == 0.0

    def test_single_strong_signal(self) -> None:
        score = _compute_convergence(
            sentiment_score=0.8,
            twitter_sentiment=0.0,
            manifold_prob=0.0,
            sports_odds_prob=0.0,
            fear_greed_val=50,
            is_crypto=False,
        )
        assert score == 1.0  # 1 signal agreeing with itself

    def test_three_agree_one_disagrees(self) -> None:
        score = _compute_convergence(
            sentiment_score=0.5,   # bullish
            twitter_sentiment=0.3,  # bullish
            manifold_prob=0.7,     # bullish
            sports_odds_prob=0.3,  # bearish
            fear_greed_val=50,
            is_crypto=False,
        )
        # 3 bullish, 1 bearish → 3/4 = 0.75
        assert score == 0.75
