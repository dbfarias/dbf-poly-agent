"""Tests for PerformanceLearner — adaptive learning system."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timedelta, timezone
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
    exit_reason: str = "strategy_exit",
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
        exit_reason=exit_reason,
        created_at=created_at or datetime.now(timezone.utc),
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
        # PnL balanced so PF > 1.0 when majority wins (avoids
        # profit_factor edge tightening distorting multiplier tests)
        pnl = 0.30 if i < win_count else -0.20
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
        # Smooth linear: 75% win rate → ~0.583 (low bar for profitable strategy)
        mult = learner.get_edge_multiplier("time_decay", "politics")
        assert 0.5 <= mult <= 0.65, f"Expected ~0.58, got {mult}"

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
        # Smooth linear: 25% win rate → 1.75 (demand much higher edge)
        mult = learner.get_edge_multiplier("time_decay", "sports")
        assert 1.7 <= mult <= 1.8, f"Expected ~1.75, got {mult}"

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
        # Smooth linear: 50% win rate → ~1.167 (slightly above baseline)
        mult = learner.get_edge_multiplier("time_decay", "crypto")
        assert 1.1 <= mult <= 1.2, f"Expected ~1.17, got {mult}"

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
        assert learner.get_edge_multiplier("time_decay", "new") == 1.0

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
            total_trades=20,
            winning_trades=16,  # 80%
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
        trades = make_trades(3, 0, strategy="time_decay")  # All losses, but < PAUSE_LOOKBACK
        assert learner.should_pause_strategy("time_decay", trades) is False

    def test_bad_performance_pauses(self):
        learner = PerformanceLearner()
        # 5 trades, 1 win (20%), losses → total PnL negative
        trades = make_trades(PAUSE_LOOKBACK, 1, strategy="time_decay")
        assert learner.should_pause_strategy("time_decay", trades) is True

    def test_good_performance_does_not_pause(self):
        learner = PerformanceLearner()
        trades = make_trades(PAUSE_LOOKBACK, 4, strategy="time_decay")  # 80% win
        assert learner.should_pause_strategy("time_decay", trades) is False

    def test_already_paused_stays_paused(self):
        learner = PerformanceLearner()
        # Pause it (1 win out of 5 = 20% WR, triggers pause)
        trades = make_trades(PAUSE_LOOKBACK, 1, strategy="time_decay")
        learner.should_pause_strategy("time_decay", trades)
        # Call again — should still be paused
        assert learner.should_pause_strategy("time_decay", []) is True

    def test_cooldown_expires(self):
        learner = PerformanceLearner()
        # Set pause time 25 hours ago
        learner._paused_strategies["time_decay"] = (
            datetime.now(timezone.utc) - timedelta(hours=25)
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

    def test_force_unpause_removes_pause(self):
        """force_unpause clears a paused strategy and adjustments."""
        learner = PerformanceLearner()
        learner._paused_strategies["value_betting"] = (
            datetime.now(timezone.utc)
        )
        # Simulate existing adjustments with VB paused
        from bot.agent.learner import LearnerAdjustments

        learner._last_adjustments = LearnerAdjustments(
            edge_multipliers={},
            category_confidences={},
            paused_strategies={"value_betting"},
            calibration={},
        )
        assert learner.force_unpause("value_betting") is True
        assert "value_betting" not in learner._paused_strategies
        assert "value_betting" in learner._unpause_immunity
        # Adjustments also updated
        assert (
            "value_betting"
            not in learner._last_adjustments.paused_strategies
        )

    def test_force_unpause_not_paused_returns_false(self):
        """force_unpause on a non-paused strategy returns False."""
        learner = PerformanceLearner()
        assert learner.force_unpause("value_betting") is False

    def test_force_unpause_grants_immunity(self):
        """After force_unpause, strategy is immune to re-pause."""
        learner = PerformanceLearner()
        learner._paused_strategies["value_betting"] = (
            datetime.now(timezone.utc)
        )
        learner.force_unpause("value_betting")
        # Bad trades that would normally trigger a pause
        trades = make_trades(
            PAUSE_LOOKBACK, 1, strategy="value_betting",
        )
        assert learner.should_pause_strategy(
            "value_betting", trades,
        ) is False

    def test_immunity_expires_after_grace_period(self):
        """Immunity expires after UNPAUSE_GRACE_HOURS."""
        learner = PerformanceLearner()
        learner._unpause_immunity["value_betting"] = (
            datetime.now(timezone.utc)
            - timedelta(hours=learner.UNPAUSE_GRACE_HOURS + 1)
        )
        # Bad trades → should now pause since immunity expired
        trades = make_trades(
            PAUSE_LOOKBACK, 1, strategy="value_betting",
        )
        assert learner.should_pause_strategy(
            "value_betting", trades,
        ) is True


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

                    # 75% win rate → smooth linear ≈ 0.583
                    key = ("time_decay", "politics")
                    assert key in adjustments.edge_multipliers
                    mult = adjustments.edge_multipliers[key]
                    assert 0.5 <= mult <= 0.65, f"Expected ~0.58, got {mult}"

                    # Category confidence for high win rate → 1.2
                    assert adjustments.category_confidences.get("politics") == 1.2

                    # No strategy should be paused (75% win rate is good)
                    assert "time_decay" not in adjustments.paused_strategies


# ---------------------------------------------------------------------------
# _compute_category_confidences
# ---------------------------------------------------------------------------


# ---------------------------------------------------------------------------
# _compute_urgency (daily target integration)
# ---------------------------------------------------------------------------


class TestComputeUrgency:
    def test_no_equity_returns_default(self):
        learner = PerformanceLearner()
        learner.set_daily_context(realized_pnl=0.0, equity=0.0, target_pct=0.01)
        assert learner._compute_urgency() == 1.0

    def test_target_hit_returns_conservative(self):
        """When daily target is met, urgency should be low (conservative)."""
        learner = PerformanceLearner()
        learner.set_daily_context(realized_pnl=0.50, equity=30.0, target_pct=0.01)
        # Target = $0.30, PnL = $0.50 → progress = 1.67 → hit target
        assert learner._compute_urgency() == 0.7

    def test_negative_pnl_returns_most_aggressive(self):
        """When PnL is negative, urgency should be highest."""
        learner = PerformanceLearner()
        learner.set_daily_context(realized_pnl=-0.20, equity=30.0, target_pct=0.01)
        assert learner._compute_urgency() == 1.5

    def test_zero_pnl_early_in_day_normal(self):
        """At start of day with no PnL, should be close to normal."""
        learner = PerformanceLearner()
        learner.set_daily_context(realized_pnl=0.0, equity=30.0, target_pct=0.01)
        urgency = learner._compute_urgency()
        # progress = 0, but day_fraction is small early on → behind but not by much
        # Result depends on current time, but should be between 1.0 and 1.3
        assert 1.0 <= urgency <= 1.3

    def test_urgency_clamped_range(self):
        """All urgency values should be within [0.7, 1.5]."""
        learner = PerformanceLearner()
        for pnl in [-5.0, -1.0, 0.0, 0.15, 0.30, 1.0, 5.0]:
            learner.set_daily_context(realized_pnl=pnl, equity=30.0, target_pct=0.01)
            urgency = learner._compute_urgency()
            assert 0.7 <= urgency <= 1.5, f"urgency={urgency} for pnl={pnl}"


class TestComputeDailyProgress:
    def test_zero_equity(self):
        learner = PerformanceLearner()
        learner.set_daily_context(realized_pnl=0.0, equity=0.0, target_pct=0.01)
        assert learner._compute_daily_progress() == 0.0

    def test_progress_calculation(self):
        learner = PerformanceLearner()
        learner.set_daily_context(realized_pnl=0.15, equity=30.0, target_pct=0.01)
        # Target = $0.30, PnL = $0.15 → 50%
        assert learner._compute_daily_progress() == 0.5

    def test_exceeded_target(self):
        learner = PerformanceLearner()
        learner.set_daily_context(realized_pnl=0.60, equity=30.0, target_pct=0.01)
        # Target = $0.30, PnL = $0.60 → 200%
        assert learner._compute_daily_progress() == 2.0

    def test_negative_pnl(self):
        learner = PerformanceLearner()
        learner.set_daily_context(realized_pnl=-0.15, equity=30.0, target_pct=0.01)
        # Target = $0.30, PnL = -$0.15 → -50%
        assert learner._compute_daily_progress() == -0.5


    async def test_compute_stats_ignores_unresolved_trades(self):
        """compute_stats should exclude trades without exit_reason (unresolved BUYs)."""
        learner = PerformanceLearner()

        # Mix of resolved and unresolved trades
        resolved = make_trades(10, 8, strategy="time_decay", category="politics")
        unresolved = [
            make_trade(strategy="time_decay", category="politics", pnl=0.0, exit_reason="")
            for _ in range(20)
        ]
        all_trades = resolved + unresolved

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
                mock_trade_repo.get_recent.return_value = all_trades
                mock_trade_repo_cls.return_value = mock_trade_repo

                with patch(
                    "bot.agent.learner.StrategyMetricRepository"
                ) as mock_metric_repo_cls:
                    mock_metric_repo = AsyncMock()
                    mock_metric_repo_cls.return_value = mock_metric_repo

                    await learner.compute_stats()

                    # Only the 10 resolved trades should be counted
                    key = ("time_decay", "politics")
                    assert key in learner._stats
                    assert learner._stats[key].total_trades == 10
                    assert learner._stats[key].winning_trades == 8


class TestLearnerAdjustmentsUrgency:
    async def test_adjustments_include_urgency(self):
        """compute_stats should include urgency in adjustments."""
        learner = PerformanceLearner()
        learner.set_daily_context(realized_pnl=0.50, equity=30.0, target_pct=0.01)

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
                mock_trade_repo.get_recent.return_value = []
                mock_trade_repo_cls.return_value = mock_trade_repo

                with patch(
                    "bot.agent.learner.StrategyMetricRepository"
                ) as mock_metric_repo_cls:
                    mock_metric_repo = AsyncMock()
                    mock_metric_repo_cls.return_value = mock_metric_repo

                    adjustments = await learner.compute_stats()

                    # Target hit → conservative urgency
                    assert adjustments.urgency_multiplier == 0.7
                    # Progress > 1.0 (PnL $0.50 / target $0.30)
                    assert adjustments.daily_progress > 1.0


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


# ---------------------------------------------------------------------------
# _compute_strategy_profit_factors
# ---------------------------------------------------------------------------


class TestComputeStrategyProfitFactors:
    def test_empty_trades(self):
        result = PerformanceLearner._compute_strategy_profit_factors([])
        assert result == {}

    def test_single_strategy_profitable(self):
        """Winning strategy should have PF > 1.0."""
        trades = [
            make_trade(strategy="value_betting", pnl=0.50),
            make_trade(strategy="value_betting", pnl=0.30),
            make_trade(strategy="value_betting", pnl=-0.20),
        ]
        result = PerformanceLearner._compute_strategy_profit_factors(trades)
        assert "value_betting" in result
        pf = result["value_betting"]
        assert pf["trades"] == 3
        assert pf["profit_factor"] == 4.0  # (0.50 + 0.30) / 0.20 = 4.0
        assert pf["gross_profit"] == 0.8
        assert pf["gross_loss"] == 0.2

    def test_single_strategy_losing(self):
        """Losing strategy should have PF < 1.0."""
        trades = [
            make_trade(strategy="arbitrage", pnl=-0.50),
            make_trade(strategy="arbitrage", pnl=-0.30),
            make_trade(strategy="arbitrage", pnl=0.10),
        ]
        result = PerformanceLearner._compute_strategy_profit_factors(trades)
        pf = result["arbitrage"]
        assert pf["profit_factor"] < 1.0  # 0.10 / 0.80 = 0.125
        assert pf["trades"] == 3

    def test_multiple_strategies(self):
        """Each strategy gets its own PF."""
        trades = [
            make_trade(strategy="value_betting", pnl=1.00),
            make_trade(strategy="value_betting", pnl=-0.50),
            make_trade(strategy="arbitrage", pnl=0.10),
            make_trade(strategy="arbitrage", pnl=-0.80),
        ]
        result = PerformanceLearner._compute_strategy_profit_factors(trades)
        assert result["value_betting"]["profit_factor"] == 2.0  # 1.0 / 0.5
        assert result["arbitrage"]["profit_factor"] < 1.0  # 0.1 / 0.8 = 0.125

    def test_no_losses_caps_at_10(self):
        """No losses → PF capped at 10.0."""
        trades = [
            make_trade(strategy="time_decay", pnl=0.50),
            make_trade(strategy="time_decay", pnl=0.30),
        ]
        result = PerformanceLearner._compute_strategy_profit_factors(trades)
        assert result["time_decay"]["profit_factor"] == 10.0

    def test_no_wins_returns_zero(self):
        """All losses → PF = 0.0."""
        trades = [
            make_trade(strategy="time_decay", pnl=-0.50),
            make_trade(strategy="time_decay", pnl=-0.30),
        ]
        result = PerformanceLearner._compute_strategy_profit_factors(trades)
        assert result["time_decay"]["profit_factor"] == 0.0


class TestProfitFactorEdgeAdjustment:
    """Test that per-strategy PF adjusts edge multipliers correctly."""

    async def test_bad_pf_tightens_edge(self):
        """Strategy with PF < 0.8 should get 40% tighter edge."""
        learner = PerformanceLearner()

        # 20 trades: 6 small wins, 14 big losses → PF < 0.8
        trades = []
        for i in range(20):
            pnl = 0.05 if i < 6 else -0.30  # PF = 0.30 / 4.20 ≈ 0.07
            trades.append(make_trade(
                strategy="time_decay", category="politics", pnl=pnl,
            ))

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

                    # PF < 0.8 should tighten edge multiplier
                    key = ("time_decay", "politics")
                    mult = adjustments.edge_multipliers[key]
                    # Base multiplier for 30% win rate ≈ 1.633
                    # Tightened by 1.4x → ≈ 2.0 (clamped to MAX)
                    assert mult >= 1.5

    async def test_excellent_pf_relaxes_edge(self):
        """Strategy with PF > 2.0 and 20+ trades should get 10% relaxed edge."""
        learner = PerformanceLearner()

        # 20 trades: 16 big wins, 4 small losses → PF > 2.0
        trades = []
        for i in range(20):
            pnl = 0.40 if i < 16 else -0.20  # PF = 6.40 / 0.80 = 8.0
            trades.append(make_trade(
                strategy="time_decay", category="politics", pnl=pnl,
            ))

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

                    key = ("time_decay", "politics")
                    mult = adjustments.edge_multipliers[key]
                    # 80% win rate → base ≈ 0.467 (clamped to 0.5)
                    # Relaxed by 0.9x → stays at 0.5 (already at min)
                    assert mult <= 0.55

    async def test_strategy_profit_factors_in_adjustments(self):
        """compute_stats should populate strategy_profit_factors."""
        learner = PerformanceLearner()

        trades = make_trades(15, 12, strategy="value_betting", category="politics")

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

                    assert "value_betting" in adjustments.strategy_profit_factors
                    pf = adjustments.strategy_profit_factors["value_betting"]
                    # 12 wins * 0.30 = 3.60, 3 losses * 0.20 = 0.60
                    # PF = 3.60 / 0.60 = 6.0
                    assert pf == 6.0

    async def test_few_trades_no_pf_adjustment(self):
        """Strategies with < 5 trades should not get PF edge adjustment."""
        learner = PerformanceLearner()

        # 3 losing trades — PF bad but too few
        trades = [
            make_trade(strategy="time_decay", category="politics", pnl=-0.50)
            for _ in range(3)
        ]

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

                    # 3 trades < MIN_TRADES_FOR_ADJUSTMENT → neutral 1.0
                    # No PF tightening applied (< 15 trades for PF)
                    key = ("time_decay", "politics")
                    if key in adjustments.edge_multipliers:
                        assert adjustments.edge_multipliers[key] == 1.0
