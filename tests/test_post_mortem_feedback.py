"""Tests for post-mortem → learner feedback loop."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.learner import PerformanceLearner
from bot.data.models import Trade

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_trade(
    strategy: str = "value_betting",
    category: str = "politics",
    pnl: float = 0.10,
    edge: float = 0.03,
    estimated_prob: float = 0.92,
    status: str = "filled",
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
    strategy: str = "value_betting",
    category: str = "politics",
) -> list[Trade]:
    trades = []
    for i in range(count):
        pnl = 0.10 if i < win_count else -0.50
        trades.append(
            make_trade(strategy=strategy, category=category, pnl=pnl)
        )
    return trades


# ---------------------------------------------------------------------------
# get_post_mortem_stats
# ---------------------------------------------------------------------------


class TestGetPostMortemStats:
    @pytest.mark.asyncio
    async def test_empty_returns_empty_dict(self):
        """No llm_post_mortem events → empty dict."""
        mock_result = MagicMock()
        mock_result.all.return_value = []

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "bot.data.activity.async_session", return_value=mock_session
        ):
            from bot.data.activity import get_post_mortem_stats
            result = await get_post_mortem_stats(30)

        assert result == {}

    @pytest.mark.asyncio
    async def test_aggregates_correctly(self):
        """Multiple rows with different fits are counted correctly."""
        # Simulate DB rows: (strategy, fit_value, count)
        rows = [
            ("value_betting", "GOOD_FIT", 5),
            ("value_betting", "POOR_FIT", 2),
            ("value_betting", "NEUTRAL", 1),
            ("time_decay", "POOR_FIT", 4),
            ("time_decay", "GOOD_FIT", 1),
        ]
        mock_result = MagicMock()
        mock_result.all.return_value = rows

        mock_session = AsyncMock()
        mock_session.execute = AsyncMock(return_value=mock_result)
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        with patch(
            "bot.data.activity.async_session", return_value=mock_session
        ):
            from bot.data.activity import get_post_mortem_stats
            result = await get_post_mortem_stats(30)

        assert result["value_betting"]["total"] == 8
        assert result["value_betting"]["good_fit"] == 5
        assert result["value_betting"]["poor_fit"] == 2
        assert result["value_betting"]["neutral"] == 1

        assert result["time_decay"]["total"] == 5
        assert result["time_decay"]["poor_fit"] == 4
        assert result["time_decay"]["good_fit"] == 1


# ---------------------------------------------------------------------------
# Learner post-mortem integration
# ---------------------------------------------------------------------------


class TestLearnerPostMortemFeedback:
    @pytest.mark.asyncio
    async def test_tightens_multiplier_on_poor_fit(self):
        """Strategy with >50% POOR_FIT → edge multiplier * 1.15."""
        learner = PerformanceLearner()
        learner.set_daily_context(0.0, 100.0, 0.01)

        trades = make_trades(12, 8, "value_betting", "politics")

        pm_stats = {
            "value_betting": {
                "total": 6, "good_fit": 1, "poor_fit": 4, "neutral": 1,
            },
        }

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_repo = MagicMock()
        mock_repo.get_recent = AsyncMock(return_value=trades)
        mock_repo.upsert = AsyncMock()

        with (
            patch("bot.agent.learner.async_session", return_value=mock_session),
            patch(
                "bot.agent.learner.TradeRepository", return_value=mock_repo
            ),
            patch(
                "bot.agent.learner.StrategyMetricRepository",
                return_value=MagicMock(upsert=AsyncMock()),
            ),
            patch(
                "bot.agent.learner.get_post_mortem_stats",
                new_callable=AsyncMock,
                return_value=pm_stats,
            ),
        ):
            adjustments = await learner.compute_stats()

        key = ("value_betting", "politics")
        multiplier = adjustments.edge_multipliers[key]
        # Base multiplier for >60% win rate (8/12) = 0.8
        # After poor_fit tightening: 0.8 * 1.15 = 0.92
        assert abs(multiplier - 0.8 * 1.15) < 0.01

    @pytest.mark.asyncio
    async def test_relaxes_multiplier_on_good_fit(self):
        """Strategy with >50% GOOD_FIT → edge multiplier * 0.90."""
        learner = PerformanceLearner()
        learner.set_daily_context(0.0, 100.0, 0.01)

        trades = make_trades(12, 8, "value_betting", "politics")

        pm_stats = {
            "value_betting": {
                "total": 6, "good_fit": 4, "poor_fit": 1, "neutral": 1,
            },
        }

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_repo = MagicMock()
        mock_repo.get_recent = AsyncMock(return_value=trades)

        with (
            patch("bot.agent.learner.async_session", return_value=mock_session),
            patch(
                "bot.agent.learner.TradeRepository", return_value=mock_repo
            ),
            patch(
                "bot.agent.learner.StrategyMetricRepository",
                return_value=MagicMock(upsert=AsyncMock()),
            ),
            patch(
                "bot.agent.learner.get_post_mortem_stats",
                new_callable=AsyncMock,
                return_value=pm_stats,
            ),
        ):
            adjustments = await learner.compute_stats()

        key = ("value_betting", "politics")
        multiplier = adjustments.edge_multipliers[key]
        # Base multiplier for >60% win rate (8/12) = 0.8
        # After good_fit relaxing: 0.8 * 0.90 = 0.72
        assert abs(multiplier - 0.8 * 0.90) < 0.01

    @pytest.mark.asyncio
    async def test_skips_insufficient_post_mortems(self):
        """<3 post-mortems → no change to multiplier."""
        learner = PerformanceLearner()
        learner.set_daily_context(0.0, 100.0, 0.01)

        trades = make_trades(12, 8, "value_betting", "politics")

        pm_stats = {
            "value_betting": {
                "total": 2, "good_fit": 2, "poor_fit": 0, "neutral": 0,
            },
        }

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_repo = MagicMock()
        mock_repo.get_recent = AsyncMock(return_value=trades)

        with (
            patch("bot.agent.learner.async_session", return_value=mock_session),
            patch(
                "bot.agent.learner.TradeRepository", return_value=mock_repo
            ),
            patch(
                "bot.agent.learner.StrategyMetricRepository",
                return_value=MagicMock(upsert=AsyncMock()),
            ),
            patch(
                "bot.agent.learner.get_post_mortem_stats",
                new_callable=AsyncMock,
                return_value=pm_stats,
            ),
        ):
            adjustments = await learner.compute_stats()

        key = ("value_betting", "politics")
        multiplier = adjustments.edge_multipliers[key]
        # Base multiplier for >60% win rate = 0.8, no PM adjustment
        assert abs(multiplier - 0.8) < 0.01

    @pytest.mark.asyncio
    async def test_pm_stats_stored_on_learner(self):
        """compute_stats stores _pm_stats for API visibility."""
        learner = PerformanceLearner()
        learner.set_daily_context(0.0, 100.0, 0.01)

        trades = make_trades(12, 8, "value_betting", "politics")
        pm_stats = {
            "value_betting": {
                "total": 5, "good_fit": 3, "poor_fit": 1, "neutral": 1,
            },
        }

        mock_session = AsyncMock()
        mock_session.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session.__aexit__ = AsyncMock(return_value=False)

        mock_repo = MagicMock()
        mock_repo.get_recent = AsyncMock(return_value=trades)

        with (
            patch("bot.agent.learner.async_session", return_value=mock_session),
            patch(
                "bot.agent.learner.TradeRepository", return_value=mock_repo
            ),
            patch(
                "bot.agent.learner.StrategyMetricRepository",
                return_value=MagicMock(upsert=AsyncMock()),
            ),
            patch(
                "bot.agent.learner.get_post_mortem_stats",
                new_callable=AsyncMock,
                return_value=pm_stats,
            ),
        ):
            await learner.compute_stats()

        assert learner._pm_stats == pm_stats


# ---------------------------------------------------------------------------
# API endpoint
# ---------------------------------------------------------------------------


class TestApiMultipliersIncludesPmInfluence:
    @pytest.mark.asyncio
    async def test_response_has_post_mortem_influence(self):
        """GET /api/learner/multipliers includes post_mortem_influence."""
        from bot.agent.learner import LearnerAdjustments

        mock_learner = MagicMock()
        mock_learner._last_adjustments = LearnerAdjustments(
            edge_multipliers={("value_betting", "politics"): 0.92},
            category_confidences={"politics": 1.0},
            paused_strategies=set(),
            calibration={},
        )
        mock_learner._last_computed = datetime.now(timezone.utc)
        mock_learner._stats = {}
        mock_learner._pm_stats = {
            "value_betting": {
                "total": 6, "good_fit": 1, "poor_fit": 4, "neutral": 1,
            },
        }

        mock_engine = MagicMock()
        mock_engine.learner = mock_learner

        with patch("api.routers.learner.get_engine", return_value=mock_engine):
            from api.routers.learner import get_multipliers
            result = await get_multipliers(_="dummy")

        assert "post_mortem_influence" in result
        pm_list = result["post_mortem_influence"]
        assert len(pm_list) == 1
        assert pm_list[0]["strategy"] == "value_betting"
        assert pm_list[0]["influence"] == "tightening"
        assert pm_list[0]["total"] == 6
        assert pm_list[0]["poor_fit_pct"] == pytest.approx(66.7, abs=0.1)

    @pytest.mark.asyncio
    async def test_empty_pm_stats(self):
        """No post-mortem data → empty list."""
        from bot.agent.learner import LearnerAdjustments

        mock_learner = MagicMock()
        mock_learner._last_adjustments = LearnerAdjustments(
            edge_multipliers={},
            category_confidences={},
            paused_strategies=set(),
            calibration={},
        )
        mock_learner._last_computed = datetime.now(timezone.utc)
        mock_learner._stats = {}
        mock_learner._pm_stats = {}

        mock_engine = MagicMock()
        mock_engine.learner = mock_learner

        with patch("api.routers.learner.get_engine", return_value=mock_engine):
            from api.routers.learner import get_multipliers
            result = await get_multipliers(_="dummy")

        assert result["post_mortem_influence"] == []
