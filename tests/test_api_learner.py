"""Tests for learner API endpoints — multipliers, calibration, pauses."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock, patch

import pytest
from httpx import ASGITransport, AsyncClient

from api.dependencies import get_engine
from api.middleware import verify_api_key

TEST_API_KEY = os.environ["API_SECRET_KEY"]


def _make_mock_learner(
    *,
    adjustments=None,
    stats=None,
    paused_strategies=None,
    last_computed=None,
):
    """Build a mock learner with configurable state."""
    learner = MagicMock()
    learner._last_adjustments = adjustments
    learner._stats = stats or {}
    learner._paused_strategies = paused_strategies or {}
    learner._last_computed = last_computed
    learner.PAUSE_COOLDOWN_HOURS = 12
    return learner


def _make_adjustments(
    *,
    edge_multipliers=None,
    category_confidences=None,
    paused_strategies=None,
    calibration=None,
):
    """Build a mock LearnerAdjustments object."""
    adj = MagicMock()
    adj.edge_multipliers = edge_multipliers or {}
    adj.category_confidences = category_confidences or {}
    adj.paused_strategies = paused_strategies or set()
    adj.calibration = calibration or {}
    return adj


def _make_stats(
    *, actual_win_rate=0.6, total_trades=20,
    total_pnl=1.5, avg_edge=0.03, winning_trades=12,
):
    """Build a mock StrategyStats object."""
    s = MagicMock()
    s.actual_win_rate = actual_win_rate
    s.total_trades = total_trades
    s.total_pnl = total_pnl
    s.avg_edge = avg_edge
    s.winning_trades = winning_trades
    return s


_STRATEGY_NAMES = [
    "time_decay", "arbitrage", "value_betting",
    "market_making", "price_divergence", "swing_trading",
]


@pytest.fixture
def mock_engine_learner():
    """Engine with a mock learner attached."""
    engine = MagicMock()
    engine.learner = _make_mock_learner()
    # Provide strategy stubs so the pauses endpoint can iterate them
    stubs = []
    for name in _STRATEGY_NAMES:
        s = MagicMock()
        s.name = name
        stubs.append(s)
    engine.analyzer.strategies = stubs
    engine.disabled_strategies = set()
    return engine


@pytest.fixture
async def learner_client(mock_engine_learner):
    """Async HTTP client wired to the learner router only."""
    from fastapi import FastAPI

    from api.routers import learner

    test_app = FastAPI()
    test_app.include_router(learner.router)

    async def override_verify(_=None):
        return "test-user"

    test_app.dependency_overrides[verify_api_key] = override_verify
    test_app.dependency_overrides[get_engine] = lambda: mock_engine_learner

    with patch("bot.main.engine", mock_engine_learner):
        transport = ASGITransport(app=test_app)
        async with AsyncClient(
            transport=transport,
            base_url="http://test",
            headers={"X-API-Key": TEST_API_KEY},
        ) as ac:
            yield ac


# ---------------------------------------------------------------------------
# GET /api/learner/multipliers
# ---------------------------------------------------------------------------


class TestGetMultipliers:
    async def test_returns_multipliers_with_adjustments(
        self, learner_client, mock_engine_learner
    ):
        """Returns edge_multipliers and category_confidences when adjustments exist."""
        stats_key = ("time_decay", "crypto")
        stats = _make_stats(
            actual_win_rate=0.65,
            total_trades=30,
            total_pnl=2.5,
            avg_edge=0.04,
            winning_trades=20,
        )

        adj = _make_adjustments(
            edge_multipliers={stats_key: 0.95},
            category_confidences={"crypto": 1.1},
            paused_strategies=set(),
        )

        mock_engine_learner.learner = _make_mock_learner(
            adjustments=adj,
            stats={stats_key: stats},
            last_computed=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
        )

        resp = await learner_client.get("/api/learner/multipliers")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["edge_multipliers"]) == 1
        em = data["edge_multipliers"][0]
        assert em["strategy"] == "time_decay"
        assert em["category"] == "crypto"
        assert em["multiplier"] == 0.95
        assert em["win_rate"] == 0.65
        assert em["total_trades"] == 30
        assert em["total_pnl"] == 2.5
        assert em["avg_edge"] == 0.04

        assert len(data["category_confidences"]) == 1
        cc = data["category_confidences"][0]
        assert cc["category"] == "crypto"
        assert cc["confidence"] == 1.1
        assert cc["total_trades"] == 30
        assert cc["win_rate"] == pytest.approx(20 / 30, abs=0.001)
        assert cc["total_pnl"] == 2.5

        assert data["paused_strategies"] == []
        assert data["last_computed"] is not None

    async def test_returns_empty_when_no_adjustments(
        self, learner_client, mock_engine_learner
    ):
        """Returns empty lists when _last_adjustments is None."""
        mock_engine_learner.learner = _make_mock_learner(adjustments=None)

        resp = await learner_client.get("/api/learner/multipliers")
        assert resp.status_code == 200
        data = resp.json()

        assert data["edge_multipliers"] == []
        assert data["category_confidences"] == []
        assert data["paused_strategies"] == []
        assert data["last_computed"] is None

    async def test_multiplier_status_mapping(
        self, learner_client, mock_engine_learner
    ):
        """Multiplier status maps correctly: relaxed, normal, cautious, strict."""
        stats = {
            ("s1", "c1"): _make_stats(),
            ("s2", "c2"): _make_stats(),
            ("s3", "c3"): _make_stats(),
            ("s4", "c4"): _make_stats(),
        }
        adj = _make_adjustments(
            edge_multipliers={
                ("s1", "c1"): 0.7,   # <= 0.8 -> relaxed
                ("s2", "c2"): 0.95,  # <= 1.0 -> normal
                ("s3", "c3"): 1.15,  # <= 1.2 -> cautious
                ("s4", "c4"): 1.5,   # > 1.2  -> strict
            },
            category_confidences={},
        )

        mock_engine_learner.learner = _make_mock_learner(
            adjustments=adj,
            stats=stats,
            last_computed=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )

        resp = await learner_client.get("/api/learner/multipliers")
        assert resp.status_code == 200
        data = resp.json()

        multipliers = data["edge_multipliers"]
        status_by_strategy = {m["strategy"]: m["status"] for m in multipliers}
        assert status_by_strategy["s1"] == "relaxed"
        assert status_by_strategy["s2"] == "normal"
        assert status_by_strategy["s3"] == "cautious"
        assert status_by_strategy["s4"] == "strict"


# ---------------------------------------------------------------------------
# GET /api/learner/calibration
# ---------------------------------------------------------------------------


class TestGetCalibration:
    async def test_returns_buckets_with_adjustments(
        self, learner_client, mock_engine_learner
    ):
        """Returns calibration buckets when adjustments exist."""
        from unittest.mock import AsyncMock

        adj = _make_adjustments(
            calibration={
                "80-85": 0.95,
                "85-90": 1.05,
                "90-95": 1.10,
                "95-99": 0.85,
            },
        )

        mock_engine_learner.learner = _make_mock_learner(
            adjustments=adj,
            last_computed=datetime(2026, 3, 1, 12, 0, tzinfo=timezone.utc),
        )

        # Patch the DB call inside the calibration endpoint to return no trades.
        # The endpoint does `from bot.data.database import async_session` inside
        # the function body, so we patch at the source module.
        mock_repo = MagicMock()
        mock_repo.get_recent = AsyncMock(return_value=[])

        mock_session = AsyncMock()

        # Build an async context manager for async_session()
        mock_session_ctx = AsyncMock()
        mock_session_ctx.__aenter__ = AsyncMock(return_value=mock_session)
        mock_session_ctx.__aexit__ = AsyncMock(return_value=False)

        mock_session_factory = MagicMock(return_value=mock_session_ctx)

        with patch("bot.data.database.async_session", mock_session_factory):
            with patch("bot.data.repositories.TradeRepository", return_value=mock_repo):
                resp = await learner_client.get("/api/learner/calibration")

        assert resp.status_code == 200
        data = resp.json()

        assert len(data["buckets"]) == 4
        labels = [b["bucket"] for b in data["buckets"]]
        assert labels == ["80-85", "85-90", "90-95", "95-99"]

        # With no trades, calibration_ratio comes from adjustments
        for b in data["buckets"]:
            assert b["total_trades"] == 0
            assert b["wins"] == 0
            assert b["actual_win_rate"] == 0.0

        # Check calibration ratios were used
        assert data["buckets"][0]["calibration_ratio"] == 0.95
        assert data["buckets"][1]["calibration_ratio"] == 1.05
        assert data["buckets"][3]["calibration_ratio"] == 0.85

        assert data["last_computed"] is not None

    async def test_returns_empty_when_no_adjustments(
        self, learner_client, mock_engine_learner
    ):
        """Returns empty buckets when _last_adjustments is None."""
        mock_engine_learner.learner = _make_mock_learner(adjustments=None)

        resp = await learner_client.get("/api/learner/calibration")
        assert resp.status_code == 200
        data = resp.json()

        assert data["buckets"] == []
        assert data["last_computed"] is None


# ---------------------------------------------------------------------------
# GET /api/learner/pauses
# ---------------------------------------------------------------------------


class TestGetPauses:
    async def test_returns_strategy_status_list(
        self, learner_client, mock_engine_learner
    ):
        """Returns all 6 strategies with is_paused status."""
        adj = _make_adjustments(paused_strategies=set())

        mock_engine_learner.learner = _make_mock_learner(
            adjustments=adj,
            paused_strategies={},
            last_computed=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )

        resp = await learner_client.get("/api/learner/pauses")
        assert resp.status_code == 200
        data = resp.json()

        assert len(data["strategies"]) == 6
        names = [s["strategy"] for s in data["strategies"]]
        assert "time_decay" in names
        assert "arbitrage" in names
        assert "market_making" in names
        assert data["active_pauses"] == 0

        for s in data["strategies"]:
            assert s["is_paused"] is False
            assert s["is_admin_disabled"] is False
            assert s["pause_info"] is None

    async def test_returns_active_pause_with_elapsed_remaining(
        self, learner_client, mock_engine_learner
    ):
        """Returns pause info with elapsed and remaining hours."""
        paused_at = datetime.now(timezone.utc) - timedelta(hours=6)

        adj = _make_adjustments(paused_strategies={"time_decay"})

        mock_engine_learner.learner = _make_mock_learner(
            adjustments=adj,
            paused_strategies={"time_decay": paused_at},
            last_computed=datetime(2026, 3, 1, tzinfo=timezone.utc),
        )

        resp = await learner_client.get("/api/learner/pauses")
        assert resp.status_code == 200
        data = resp.json()

        assert data["active_pauses"] == 1

        # Find time_decay in strategies
        td = next(s for s in data["strategies"] if s["strategy"] == "time_decay")
        assert td["is_paused"] is True
        assert td["pause_info"] is not None
        assert td["pause_info"]["elapsed_hours"] == pytest.approx(6.0, abs=0.2)
        assert td["pause_info"]["remaining_hours"] == pytest.approx(6.0, abs=0.2)
        assert "expires_at" in td["pause_info"]

    async def test_returns_empty_pauses_when_nothing_paused(
        self, learner_client, mock_engine_learner
    ):
        """Returns empty pauses when no strategies are paused and adjustments is None."""
        mock_engine_learner.learner = _make_mock_learner(
            adjustments=None,
            paused_strategies={},
            last_computed=None,
        )

        resp = await learner_client.get("/api/learner/pauses")
        assert resp.status_code == 200
        data = resp.json()

        assert data["active_pauses"] == 0
        assert data["last_computed"] is None
        for s in data["strategies"]:
            assert s["is_paused"] is False


# ---------------------------------------------------------------------------
# POST /api/learner/unpause
# ---------------------------------------------------------------------------


class TestUnpauseStrategy:
    async def test_unpause_paused_strategy(
        self, learner_client, mock_engine_learner
    ):
        """Unpausing a paused strategy returns was_paused=True."""
        from unittest.mock import AsyncMock

        learner = _make_mock_learner(
            paused_strategies={
                "value_betting": datetime.now(timezone.utc),
            },
        )
        learner.force_unpause = MagicMock(return_value=True)
        learner.persist_paused_strategies = AsyncMock()
        learner.persist_unpause_immunity = AsyncMock()
        mock_engine_learner.learner = learner

        resp = await learner_client.post(
            "/api/learner/unpause",
            json={"strategy": "value_betting"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["strategy"] == "value_betting"
        assert data["was_paused"] is True
        assert data["status"] == "unpaused"
        learner.force_unpause.assert_called_once_with(
            "value_betting",
        )
        learner.persist_paused_strategies.assert_awaited_once()
        learner.persist_unpause_immunity.assert_awaited_once()

    async def test_unpause_not_paused_strategy(
        self, learner_client, mock_engine_learner
    ):
        """Unpausing a strategy that wasn't paused returns was_paused=False."""
        from unittest.mock import AsyncMock

        learner = _make_mock_learner()
        learner.force_unpause = MagicMock(return_value=False)
        learner.persist_paused_strategies = AsyncMock()
        learner.persist_unpause_immunity = AsyncMock()
        mock_engine_learner.learner = learner

        resp = await learner_client.post(
            "/api/learner/unpause",
            json={"strategy": "time_decay"},
        )
        assert resp.status_code == 200
        data = resp.json()
        assert data["was_paused"] is False

    async def test_unpause_unknown_strategy_returns_400(
        self, learner_client, mock_engine_learner
    ):
        """Unpausing an unknown strategy returns 400."""
        mock_engine_learner.learner = _make_mock_learner()

        resp = await learner_client.post(
            "/api/learner/unpause",
            json={"strategy": "nonexistent"},
        )
        assert resp.status_code == 400
