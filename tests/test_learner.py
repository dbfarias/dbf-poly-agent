"""Tests for PerformanceLearner — adaptive learning system."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timedelta
from unittest.mock import AsyncMock, patch

from bot.agent.learner import (
    MULTIPLIER_MAX,
    MULTIPLIER_MIN,
    PAUSE_LOOKBACK,
    PerformanceLearner,
    StrategyStats,
)
from bot.data.models import Trade

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_trade(
    strategy: str = "time_decay",
    category: str = "politics",
    pnl: float = 0.10,
    edge: float = 0.03,
    estimated_prob: float = 0.92,
    status: str = "completed",
    created_at: datetime | None = None,
) -> Trade:
    return Trade(
        market_id="mkt1",
        token_id="token1",
        side="BUY",
        price=0.90,
        size=5.0,
        strategy=strategy,
        category=category,
        pnl=pnl,
        edge=edge,
        estimated_prob=estimated_prob,
        status=status,
        created_at=created_at or datetime.utcnow(),
    )


def make_trades(
    count: int,
    win_count: int,
    strategy: str = "time_decay",
    category: str = "politics",
) -> list[Trade]:
    """Create a list of trades with specified win/loss ratio."""
    trades = []
    for i in range(count):
        pnl = 0.10 if i < win_count else -0.50
        trades.append(make_trade(strategy=strategy, category=category, pnl=pnl))
    return trades


# ---------------------------------------------------------------------------
# StrategyStats
# ---------------------------------------------------------------------------


class TestStrategyStats:
    def test_immutable_container(self):
        stats = StrategyStats(
            strategy="time_decay",
            category="politics",
            total_trades=20,
            winning_trades=14,
            total_pnl=1.50,
            avg_edge=0.03,
            avg_estimated_prob=0.92,
            actual_win_rate=0.70,
        )
        assert stats.strategy == "time_decay"
        assert stats.category == "politics"
        assert stats.total_trades == 20
        assert stats.winning_trades == 14
        assert stats.actual_win_rate == 0.70


# ---------------------------------------------------------------------------
# get_edge_multiplier
# ---------------------------------------------------------------------------


class TestGetEdgeMultiplier:
    def test_unknown_category_returns_cautious(self):
        learner = PerformanceLearner()
        assert learner.get_edge_multiplier("time_decay", "unknown") == 1.2

    def test_winning_strategy_returns_low_multiplier(self):
        learner = PerformanceLearner()
        learner._stats[("time_decay", "politics")] = StrategyStats(
            strategy="time_decay",
            category="politics",
            total_trades=20,
            winning_trades=15,  # 75% win rate
            total_pnl=2.0,
            avg_edge=0.03,
            avg_estimated_prob=0.92,
            actual_win_rate=0.75,
        )
        assert learner.get_edge_multiplier("time_decay", "politics") == 0.8

    def test_losing_strategy_returns_high_multiplier(self):
        learner = PerformanceLearner()
        learner._stats[("time_decay", "sports")] = StrategyStats(
            strategy="time_decay",
            category="sports",
            total_trades=20,
            winning_trades=5,  # 25% win rate
            total_pnl=-3.0,
            avg_edge=0.02,
            avg_estimated_prob=0.90,
            actual_win_rate=0.25,
        )
        assert learner.get_edge_multiplier("time_decay", "sports") == 1.5

    def test_normal_strategy_returns_default(self):
        learner = PerformanceLearner()
        learner._stats[("time_decay", "crypto")] = StrategyStats(
            strategy="time_decay",
            category="crypto",
            total_trades=20,
            winning_trades=10,  # 50% win rate
            total_pnl=0.5,
            avg_edge=0.03,
            avg_estimated_prob=0.91,
            actual_win_rate=0.50,
        )
        assert learner.get_edge_multiplier("time_decay", "crypto") == 1.0

    def test_few_trades_returns_cautious(self):
        learner = PerformanceLearner()
        learner._stats[("time_decay", "new")] = StrategyStats(
            strategy="time_decay",
            category="new",
            total_trades=5,  # Below MIN_TRADES_FOR_ADJUSTMENT
            winning_trades=4,
            total_pnl=0.5,
            avg_edge=0.03,
            avg_estimated_prob=0.92,
            actual_win_rate=0.80,
        )
        assert learner.get_edge_multiplier("time_decay", "new") == 1.2

    def test_multiplier_clamped_to_range(self):
        learner = PerformanceLearner()
        # Verify all returned multipliers are within bounds
        for win_rate in [0.0, 0.25, 0.50, 0.75, 1.0]:
            stats = StrategyStats(
                strategy="test",
                category="test",
                total_trades=20,
                winning_trades=int(20 * win_rate),
                total_pnl=0.0,
                avg_edge=0.03,
                avg_estimated_prob=0.90,
                actual_win_rate=win_rate,
            )
            mult = learner._compute_edge_multiplier(stats)
            assert MULTIPLIER_MIN <= mult <= MULTIPLIER_MAX


# ---------------------------------------------------------------------------
# get_category_confidence
# ---------------------------------------------------------------------------


class TestGetCategoryConfidence:
    def test_no_data_returns_cautious(self):
        learner = PerformanceLearner()
        assert learner.get_category_confidence("unknown") == 0.8

    def test_high_win_rate_boosts(self):
        learner = PerformanceLearner()
        learner._stats[("time_decay", "politics")] = StrategyStats(
            strategy="time_decay",
            category="politics",
            total_trades=15,
            winning_trades=12,  # 80%
            total_pnl=2.0,
            avg_edge=0.03,
            avg_estimated_prob=0.92,
            actual_win_rate=0.80,
        )
        assert learner.get_category_confidence("politics") == 1.2

    def test_medium_win_rate_neutral(self):
        learner = PerformanceLearner()
        learner._stats[("time_decay", "crypto")] = StrategyStats(
            strategy="time_decay",
            category="crypto",
            total_trades=20,
            winning_trades=12,  # 60%
            total_pnl=1.0,
            avg_edge=0.03,
            avg_estimated_prob=0.91,
            actual_win_rate=0.60,
        )
        assert learner.get_category_confidence("crypto") == 1.0

    def test_low_win_rate_penalizes(self):
        learner = PerformanceLearner()
        learner._stats[("time_decay", "sports")] = StrategyStats(
            strategy="time_decay",
            category="sports",
            total_trades=20,
            winning_trades=7,  # 35%
            total_pnl=-2.0,
            avg_edge=0.02,
            avg_estimated_prob=0.90,
            actual_win_rate=0.35,
        )
        assert learner.get_category_confidence("sports") == 0.7

    def test_aggregates_across_strategies(self):
        learner = PerformanceLearner()
        # Two strategies in same category
        learner._stats[("time_decay", "politics")] = StrategyStats(
            strategy="time_decay",
            category="politics",
            total_trades=10,
            winning_trades=8,
            total_pnl=1.0,
            avg_edge=0.03,
            avg_estimated_prob=0.92,
            actual_win_rate=0.80,
        )
        learner._stats[("arbitrage", "politics")] = StrategyStats(
            strategy="arbitrage",
            category="politics",
            total_trades=10,
            winning_trades=7,
            total_pnl=0.5,
            avg_edge=0.02,
            avg_estimated_prob=0.88,
            actual_win_rate=0.70,
        )
        # Combined: 20 trades, 15 wins = 75% (>70%) → boost
        assert learner.get_category_confidence("politics") == 1.2

    def test_few_trades_returns_cautious(self):
        learner = PerformanceLearner()
        learner._stats[("time_decay", "new_cat")] = StrategyStats(
            strategy="time_decay",
            category="new_cat",
            total_trades=3,
            winning_trades=3,
            total_pnl=0.3,
            avg_edge=0.03,
            avg_estimated_prob=0.92,
            actual_win_rate=1.0,
        )
        assert learner.get_category_confidence("new_cat") == 0.8


# ---------------------------------------------------------------------------
# should_pause_strategy
# ---------------------------------------------------------------------------


class TestShouldPauseStrategy:
    def test_no_trades_does_not_pause(self):
        learner = PerformanceLearner()
        assert learner.should_pause_strategy("time_decay", []) is False

    def test_few_trades_does_not_pause(self):
        learner = PerformanceLearner()
        trades = make_trades(5, 0, strategy="time_decay")  # All losses, but < 10
        assert learner.should_pause_strategy("time_decay", trades) is False

    def test_bad_performance_pauses(self):
        learner = PerformanceLearner()
        # 10 trades, 2 wins (20%), losses of $0.50 each → total -$4.0
        trades = make_trades(PAUSE_LOOKBACK, 2, strategy="time_decay")
        assert learner.should_pause_strategy("time_decay", trades) is True

    def test_good_performance_does_not_pause(self):
        learner = PerformanceLearner()
        trades = make_trades(PAUSE_LOOKBACK, 8, strategy="time_decay")  # 80% win
        assert learner.should_pause_strategy("time_decay", trades) is False

    def test_already_paused_stays_paused(self):
        learner = PerformanceLearner()
        # Pause it
        trades = make_trades(PAUSE_LOOKBACK, 2, strategy="time_decay")
        learner.should_pause_strategy("time_decay", trades)
        # Call again — should still be paused
        assert learner.should_pause_strategy("time_decay", []) is True

    def test_cooldown_expires(self):
        learner = PerformanceLearner()
        # Set pause time 25 hours ago
        learner._paused_strategies["time_decay"] = (
            datetime.utcnow() - timedelta(hours=25)
        )
        assert learner.should_pause_strategy("time_decay", []) is False
        # Should be removed from paused dict
        assert "time_decay" not in learner._paused_strategies

    def test_losing_but_profitable_does_not_pause(self):
        """Low win rate but positive PnL should not pause."""
        learner = PerformanceLearner()
        # 10 trades, 2 wins with big wins, 8 losses with small losses
        trades = []
        for i in range(PAUSE_LOOKBACK):
            pnl = 5.0 if i < 2 else -0.10  # 2 big wins, 8 small losses
            trades.append(make_trade(strategy="time_decay", pnl=pnl))
        # Win rate 20% but total PnL = 10.0 - 0.80 = 9.20 > -1.0
        assert learner.should_pause_strategy("time_decay", trades) is False


# ---------------------------------------------------------------------------
# _compute_calibration
# ---------------------------------------------------------------------------


class TestComputeCalibration:
    def test_empty_trades(self):
        learner = PerformanceLearner()
        cal = learner._compute_calibration([])
        # All buckets should have 1.0 (no data)
        assert all(v == 1.0 for v in cal.values())

    def test_calibrated_bucket(self):
        learner = PerformanceLearner()
        # 10 trades at 92% estimated prob, 9 wins (90% actual) → ratio ~0.978
        trades = [
            make_trade(estimated_prob=0.92, pnl=0.10 if i < 9 else -0.50)
            for i in range(10)
        ]
        cal = learner._compute_calibration(trades)
        assert 0.9 < cal["90-95"] < 1.1  # Well-calibrated

    def test_overconfident_bucket(self):
        learner = PerformanceLearner()
        # 10 trades at 96% estimated prob, only 6 wins (60% actual)
        trades = [
            make_trade(estimated_prob=0.96, pnl=0.10 if i < 6 else -0.50)
            for i in range(10)
        ]
        cal = learner._compute_calibration(trades)
        assert cal["95-99"] < 0.7  # Overconfident → low calibration ratio

    def test_bucket_with_too_few_trades(self):
        learner = PerformanceLearner()
        # Only 3 trades in 80-85 range → should default to 1.0
        trades = [make_trade(estimated_prob=0.82, pnl=0.10) for _ in range(3)]
        cal = learner._compute_calibration(trades)
        assert cal["80-85"] == 1.0


# ---------------------------------------------------------------------------
# compute_stats (async integration)
# ---------------------------------------------------------------------------


class TestComputeStats:
    async def test_compute_stats_empty_db(self):
        """compute_stats should handle empty trade history gracefully."""
        learner = PerformanceLearner()

        with patch("bot.agent.learner.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            # Mock TradeRepository.get_recent to return empty list
            with patch(
                "bot.agent.learner.TradeRepository"
            ) as mock_trade_repo_cls:
                mock_trade_repo = AsyncMock()
                mock_trade_repo.get_recent.return_value = []
                mock_trade_repo_cls.return_value = mock_trade_repo

                # Mock StrategyMetricRepository
                with patch(
                    "bot.agent.learner.StrategyMetricRepository"
                ) as mock_metric_repo_cls:
                    mock_metric_repo = AsyncMock()
                    mock_metric_repo_cls.return_value = mock_metric_repo

                    adjustments = await learner.compute_stats()

                    assert adjustments.edge_multipliers == {}
                    assert adjustments.category_confidences == {}
                    assert adjustments.paused_strategies == set()
                    assert learner._last_computed is not None

    async def test_compute_stats_with_trades(self):
        """compute_stats should compute correct stats from trade data."""
        learner = PerformanceLearner()

        # Create trade data: 15 wins, 5 losses in politics/time_decay
        trades = make_trades(20, 15, strategy="time_decay", category="politics")

        with patch("bot.agent.learner.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(
                return_value=mock_session
            )
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "bot.agent.learner.TradeRepository"
            ) as mock_trade_repo_cls:
                mock_trade_repo = AsyncMock()
                mock_trade_repo.get_recent.return_value = trades
                mock_trade_repo_cls.return_value = mock_trade_repo

                with patch(
                    "bot.agent.learner.StrategyMetricRepository"
                ) as mock_metric_repo_cls:
                    mock_metric_repo = AsyncMock()
                    mock_metric_repo_cls.return_value = mock_metric_repo

                    adjustments = await learner.compute_stats()

                    # 75% win rate → edge multiplier should be 0.8
                    key = ("time_decay", "politics")
                    assert key in adjustments.edge_multipliers
                    assert adjustments.edge_multipliers[key] == 0.8

                    # Category confidence for high win rate → 1.2
                    assert adjustments.category_confidences.get("politics") == 1.2

                    # No strategy should be paused (75% win rate is good)
                    assert "time_decay" not in adjustments.paused_strategies


# ---------------------------------------------------------------------------
# _compute_category_confidences
# ---------------------------------------------------------------------------


class TestComputeCategoryConfidences:
    def test_empty_stats(self):
        learner = PerformanceLearner()
        result = learner._compute_category_confidences({})
        assert result == {}

    def test_multiple_categories(self):
        learner = PerformanceLearner()
        stats = {
            ("time_decay", "politics"): StrategyStats(
                strategy="time_decay", category="politics",
                total_trades=20, winning_trades=16,
                total_pnl=2.0, avg_edge=0.03,
                avg_estimated_prob=0.92, actual_win_rate=0.80,
            ),
            ("time_decay", "sports"): StrategyStats(
                strategy="time_decay", category="sports",
                total_trades=20, winning_trades=6,
                total_pnl=-1.0, avg_edge=0.02,
                avg_estimated_prob=0.88, actual_win_rate=0.30,
            ),
        }
        result = learner._compute_category_confidences(stats)
        assert result["politics"] == 1.2  # High win rate
        assert result["sports"] == 0.7  # Low win rate
