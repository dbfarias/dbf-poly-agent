"""Tests for StateStore persistence — daily PnL, market cooldowns, paused strategies.

Covers round-trip save/load, missing data defaults, corrupted JSON handling,
and integration with RiskManager and PerformanceLearner persist/restore methods.

Uses in-memory SQLite so tests are fully isolated.
"""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

import json
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from bot.config import trading_day

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from bot.data.models import Base, BotSetting
from bot.data.repositories import SettingsRepository
from bot.data.settings_store import StateStore


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def state_engine():
    """Fresh in-memory SQLite engine with all tables created."""
    eng = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with eng.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield eng
    await eng.dispose()


@pytest.fixture
async def session_factory(state_engine):
    """Session factory bound to the in-memory engine."""
    return async_sessionmaker(
        state_engine, class_=AsyncSession, expire_on_commit=False
    )


@pytest.fixture
def patch_session(session_factory):
    """Context manager that patches async_session in settings_store module."""
    import bot.data.settings_store as store_mod

    original = store_mod.async_session

    class _Patcher:
        def __enter__(self):
            store_mod.async_session = session_factory
            return self

        def __exit__(self, *args):
            store_mod.async_session = original

    return _Patcher()


# ---------------------------------------------------------------------------
# save/load daily_pnl round-trip
# ---------------------------------------------------------------------------


class TestDailyPnl:
    @pytest.mark.asyncio
    async def test_save_load_round_trip(self, session_factory, patch_session):
        with patch_session:
            await StateStore.save_daily_pnl(1.23, "2026-03-01")
            pnl, date_str = await StateStore.load_daily_pnl()

        assert pnl == 1.23
        assert date_str == "2026-03-01"

    @pytest.mark.asyncio
    async def test_save_overwrites_previous(self, session_factory, patch_session):
        with patch_session:
            await StateStore.save_daily_pnl(1.00, "2026-03-01")
            await StateStore.save_daily_pnl(2.50, "2026-03-01")
            pnl, date_str = await StateStore.load_daily_pnl()

        assert pnl == 2.50
        assert date_str == "2026-03-01"

    @pytest.mark.asyncio
    async def test_negative_pnl(self, session_factory, patch_session):
        with patch_session:
            await StateStore.save_daily_pnl(-0.75, "2026-03-01")
            pnl, date_str = await StateStore.load_daily_pnl()

        assert pnl == -0.75

    @pytest.mark.asyncio
    async def test_zero_pnl(self, session_factory, patch_session):
        with patch_session:
            await StateStore.save_daily_pnl(0.0, "2026-03-01")
            pnl, date_str = await StateStore.load_daily_pnl()

        assert pnl == 0.0
        assert date_str == "2026-03-01"


# ---------------------------------------------------------------------------
# load_daily_pnl returns defaults when no data exists
# ---------------------------------------------------------------------------


class TestDailyPnlDefaults:
    @pytest.mark.asyncio
    async def test_load_returns_defaults_on_empty_db(self, session_factory, patch_session):
        with patch_session:
            pnl, date_str = await StateStore.load_daily_pnl()

        assert pnl == 0.0
        assert date_str == ""

    @pytest.mark.asyncio
    async def test_load_returns_defaults_when_only_pnl_exists(
        self, session_factory, patch_session
    ):
        """If only one of the two keys exists, still return defaults."""
        async with session_factory() as session:
            repo = SettingsRepository(session)
            await repo.set_many({"state.daily_pnl": json.dumps(1.5)})

        with patch_session:
            pnl, date_str = await StateStore.load_daily_pnl()

        assert pnl == 0.0
        assert date_str == ""

    @pytest.mark.asyncio
    async def test_load_returns_defaults_when_only_date_exists(
        self, session_factory, patch_session
    ):
        async with session_factory() as session:
            repo = SettingsRepository(session)
            await repo.set_many({"state.daily_pnl_date": json.dumps("2026-03-01")})

        with patch_session:
            pnl, date_str = await StateStore.load_daily_pnl()

        assert pnl == 0.0
        assert date_str == ""


# ---------------------------------------------------------------------------
# save/load market_cooldowns round-trip
# ---------------------------------------------------------------------------


class TestMarketCooldowns:
    @pytest.mark.asyncio
    async def test_save_load_round_trip(self, session_factory, patch_session):
        cooldowns = {
            "market_abc": "2026-03-01T12:00:00+00:00",
            "market_xyz": "2026-03-01T14:30:00+00:00",
        }
        with patch_session:
            await StateStore.save_market_cooldowns(cooldowns)
            loaded = await StateStore.load_market_cooldowns()

        assert loaded == cooldowns

    @pytest.mark.asyncio
    async def test_empty_cooldowns(self, session_factory, patch_session):
        with patch_session:
            await StateStore.save_market_cooldowns({})
            loaded = await StateStore.load_market_cooldowns()

        assert loaded == {}

    @pytest.mark.asyncio
    async def test_load_returns_empty_on_missing(self, session_factory, patch_session):
        with patch_session:
            loaded = await StateStore.load_market_cooldowns()

        assert loaded == {}

    @pytest.mark.asyncio
    async def test_overwrite_cooldowns(self, session_factory, patch_session):
        with patch_session:
            await StateStore.save_market_cooldowns({"m1": "2026-01-01T00:00:00+00:00"})
            await StateStore.save_market_cooldowns({"m2": "2026-02-01T00:00:00+00:00"})
            loaded = await StateStore.load_market_cooldowns()

        assert "m1" not in loaded
        assert loaded == {"m2": "2026-02-01T00:00:00+00:00"}


# ---------------------------------------------------------------------------
# save/load paused_strategies round-trip
# ---------------------------------------------------------------------------


class TestPausedStrategies:
    @pytest.mark.asyncio
    async def test_save_load_round_trip(self, session_factory, patch_session):
        paused = {
            "time_decay": "2026-03-01T10:00:00+00:00",
            "value_betting": "2026-03-01T11:00:00+00:00",
        }
        with patch_session:
            await StateStore.save_paused_strategies(paused)
            loaded = await StateStore.load_paused_strategies()

        assert loaded == paused

    @pytest.mark.asyncio
    async def test_empty_paused(self, session_factory, patch_session):
        with patch_session:
            await StateStore.save_paused_strategies({})
            loaded = await StateStore.load_paused_strategies()

        assert loaded == {}

    @pytest.mark.asyncio
    async def test_load_returns_empty_on_missing(self, session_factory, patch_session):
        with patch_session:
            loaded = await StateStore.load_paused_strategies()

        assert loaded == {}

    @pytest.mark.asyncio
    async def test_overwrite_replaces(self, session_factory, patch_session):
        with patch_session:
            await StateStore.save_paused_strategies({"s1": "2026-01-01T00:00:00+00:00"})
            await StateStore.save_paused_strategies({"s2": "2026-02-01T00:00:00+00:00"})
            loaded = await StateStore.load_paused_strategies()

        assert loaded == {"s2": "2026-02-01T00:00:00+00:00"}


# ---------------------------------------------------------------------------
# load with corrupted JSON returns defaults
# ---------------------------------------------------------------------------


class TestCorruptedJson:
    @pytest.mark.asyncio
    async def test_corrupted_daily_pnl_returns_defaults(self, session_factory, patch_session):
        """Non-JSON values in DB should return safe defaults."""
        async with session_factory() as session:
            repo = SettingsRepository(session)
            await repo.set_many({
                "state.daily_pnl": "not-json{{{",
                "state.daily_pnl_date": "also-broken",
            })

        with patch_session:
            pnl, date_str = await StateStore.load_daily_pnl()

        assert pnl == 0.0
        assert date_str == ""

    @pytest.mark.asyncio
    async def test_corrupted_cooldowns_returns_empty(self, session_factory, patch_session):
        async with session_factory() as session:
            repo = SettingsRepository(session)
            await repo.set_many({"state.market_cooldowns": "{{broken"})

        with patch_session:
            loaded = await StateStore.load_market_cooldowns()

        assert loaded == {}

    @pytest.mark.asyncio
    async def test_non_dict_cooldowns_returns_empty(self, session_factory, patch_session):
        """If the stored value is valid JSON but not a dict, return empty."""
        async with session_factory() as session:
            repo = SettingsRepository(session)
            await repo.set_many({"state.market_cooldowns": json.dumps([1, 2, 3])})

        with patch_session:
            loaded = await StateStore.load_market_cooldowns()

        assert loaded == {}

    @pytest.mark.asyncio
    async def test_corrupted_paused_strategies_returns_empty(
        self, session_factory, patch_session
    ):
        async with session_factory() as session:
            repo = SettingsRepository(session)
            await repo.set_many({"state.paused_strategies": "not valid json"})

        with patch_session:
            loaded = await StateStore.load_paused_strategies()

        assert loaded == {}

    @pytest.mark.asyncio
    async def test_non_dict_paused_strategies_returns_empty(
        self, session_factory, patch_session
    ):
        async with session_factory() as session:
            repo = SettingsRepository(session)
            await repo.set_many({"state.paused_strategies": json.dumps("a string")})

        with patch_session:
            loaded = await StateStore.load_paused_strategies()

        assert loaded == {}


# ---------------------------------------------------------------------------
# RiskManager integration: persist_daily_pnl / restore_daily_pnl
# ---------------------------------------------------------------------------


class TestRiskManagerIntegration:
    @pytest.mark.asyncio
    async def test_persist_and_restore_daily_pnl(self, session_factory, patch_session):
        from bot.agent.risk_manager import RiskManager

        rm = RiskManager()

        # Simulate a trading day with PnL updates
        rm.update_daily_pnl(0.50)
        rm.update_daily_pnl(0.25)

        assert rm._daily_pnl == pytest.approx(0.75)
        assert rm._daily_pnl_date == trading_day()

        # Persist
        with patch_session:
            await rm.persist_daily_pnl()

        # Create a fresh RiskManager and restore
        rm2 = RiskManager()
        assert rm2._daily_pnl == 0.0

        with patch_session:
            await rm2.restore_daily_pnl()

        assert rm2._daily_pnl == pytest.approx(0.75)
        assert rm2._daily_pnl_date == trading_day()

    @pytest.mark.asyncio
    async def test_persist_skips_when_not_dirty(self, session_factory, patch_session):
        from bot.agent.risk_manager import RiskManager

        rm = RiskManager()
        # No PnL updates => _pnl_dirty not set
        with patch_session:
            await rm.persist_daily_pnl()

        # Verify nothing was saved
        with patch_session:
            pnl, date_str = await StateStore.load_daily_pnl()

        assert pnl == 0.0
        assert date_str == ""

    @pytest.mark.asyncio
    async def test_restore_ignores_stale_date(self, session_factory, patch_session):
        """If the persisted date is not today, restore should NOT load it."""
        from bot.agent.risk_manager import RiskManager

        with patch_session:
            await StateStore.save_daily_pnl(5.0, "2020-01-01")

        rm = RiskManager()
        with patch_session:
            await rm.restore_daily_pnl()

        # Should remain at defaults since the date is stale
        assert rm._daily_pnl == 0.0

    @pytest.mark.asyncio
    async def test_persist_clears_dirty_flag(self, session_factory, patch_session):
        from bot.agent.risk_manager import RiskManager

        rm = RiskManager()
        rm.update_daily_pnl(1.0)
        assert rm._pnl_dirty is True

        with patch_session:
            await rm.persist_daily_pnl()

        assert rm._pnl_dirty is False


# ---------------------------------------------------------------------------
# PerformanceLearner integration: persist/restore paused_strategies
# ---------------------------------------------------------------------------


class TestLearnerIntegration:
    @pytest.mark.asyncio
    async def test_persist_and_restore_paused_strategies(
        self, session_factory, patch_session
    ):
        from bot.agent.learner import PerformanceLearner

        learner = PerformanceLearner()

        # Simulate pausing a strategy
        now = datetime.now(timezone.utc)
        learner._paused_strategies = {
            "time_decay": now - timedelta(hours=1),
            "value_betting": now - timedelta(hours=2),
        }

        with patch_session:
            await learner.persist_paused_strategies()

        # Create a fresh learner and restore
        learner2 = PerformanceLearner()
        assert len(learner2._paused_strategies) == 0

        with patch_session:
            await learner2.restore_paused_strategies()

        assert "time_decay" in learner2._paused_strategies
        assert "value_betting" in learner2._paused_strategies

    @pytest.mark.asyncio
    async def test_restore_skips_expired_pauses(self, session_factory, patch_session):
        """Pauses older than PAUSE_COOLDOWN_HOURS should NOT be restored."""
        from bot.agent.learner import PAUSE_COOLDOWN_HOURS, PerformanceLearner

        learner = PerformanceLearner()

        # Pause happened 25 hours ago (past the 24h cooldown)
        old_time = datetime.now(timezone.utc) - timedelta(hours=PAUSE_COOLDOWN_HOURS + 1)
        learner._paused_strategies = {"time_decay": old_time}

        with patch_session:
            await learner.persist_paused_strategies()

        learner2 = PerformanceLearner()
        with patch_session:
            await learner2.restore_paused_strategies()

        assert len(learner2._paused_strategies) == 0

    @pytest.mark.asyncio
    async def test_restore_keeps_recent_pauses(self, session_factory, patch_session):
        """Pauses within PAUSE_COOLDOWN_HOURS should be restored."""
        from bot.agent.learner import PAUSE_COOLDOWN_HOURS, PerformanceLearner

        learner = PerformanceLearner()

        recent_time = datetime.now(timezone.utc) - timedelta(hours=1)
        learner._paused_strategies = {"time_decay": recent_time}

        with patch_session:
            await learner.persist_paused_strategies()

        learner2 = PerformanceLearner()
        with patch_session:
            await learner2.restore_paused_strategies()

        assert "time_decay" in learner2._paused_strategies

    @pytest.mark.asyncio
    async def test_persist_empty_pauses_clears_state(self, session_factory, patch_session):
        """Persisting with no paused strategies should save empty dict."""
        from bot.agent.learner import PerformanceLearner

        learner = PerformanceLearner()

        # First, save something
        now = datetime.now(timezone.utc)
        learner._paused_strategies = {"time_decay": now}
        with patch_session:
            await learner.persist_paused_strategies()

        # Then clear and persist
        learner._paused_strategies = {}
        with patch_session:
            await learner.persist_paused_strategies()

        # Verify it's cleared
        with patch_session:
            loaded = await StateStore.load_paused_strategies()

        assert loaded == {}

    @pytest.mark.asyncio
    async def test_restore_mixed_old_and_recent(self, session_factory, patch_session):
        """Only recent pauses should survive restore; old ones discarded."""
        from bot.agent.learner import PAUSE_COOLDOWN_HOURS, PerformanceLearner

        learner = PerformanceLearner()

        now = datetime.now(timezone.utc)
        learner._paused_strategies = {
            "recent_strategy": now - timedelta(hours=1),
            "old_strategy": now - timedelta(hours=PAUSE_COOLDOWN_HOURS + 1),
        }

        with patch_session:
            await learner.persist_paused_strategies()

        learner2 = PerformanceLearner()
        with patch_session:
            await learner2.restore_paused_strategies()

        assert "recent_strategy" in learner2._paused_strategies
        assert "old_strategy" not in learner2._paused_strategies


# ---------------------------------------------------------------------------
# PerformanceLearner integration: persist/restore unpause_immunity
# ---------------------------------------------------------------------------


class TestLearnerImmunityIntegration:
    @pytest.mark.asyncio
    async def test_persist_and_restore_unpause_immunity(
        self, session_factory, patch_session
    ):
        from bot.agent.learner import PerformanceLearner

        learner = PerformanceLearner()

        # Simulate granting immunity
        now = datetime.now(timezone.utc)
        learner._unpause_immunity = {
            "value_betting": now - timedelta(hours=1),
            "swing_trading": now - timedelta(hours=2),
        }

        with patch_session:
            await learner.persist_unpause_immunity()

        # Fresh learner and restore
        learner2 = PerformanceLearner()
        assert len(learner2._unpause_immunity) == 0

        with patch_session:
            await learner2.restore_unpause_immunity()

        assert "value_betting" in learner2._unpause_immunity
        assert "swing_trading" in learner2._unpause_immunity

    @pytest.mark.asyncio
    async def test_restore_skips_expired_immunity(
        self, session_factory, patch_session
    ):
        """Immunity older than UNPAUSE_GRACE_HOURS should NOT be restored."""
        from bot.agent.learner import PerformanceLearner

        learner = PerformanceLearner()

        # Immunity granted 7 hours ago (past the 6h grace)
        old_time = datetime.now(timezone.utc) - timedelta(hours=7)
        learner._unpause_immunity = {"value_betting": old_time}

        with patch_session:
            await learner.persist_unpause_immunity()

        learner2 = PerformanceLearner()
        with patch_session:
            await learner2.restore_unpause_immunity()

        assert len(learner2._unpause_immunity) == 0

    @pytest.mark.asyncio
    async def test_restore_keeps_recent_immunity(
        self, session_factory, patch_session
    ):
        """Immunity within UNPAUSE_GRACE_HOURS should be restored."""
        from bot.agent.learner import PerformanceLearner

        learner = PerformanceLearner()

        recent_time = datetime.now(timezone.utc) - timedelta(hours=1)
        learner._unpause_immunity = {"value_betting": recent_time}

        with patch_session:
            await learner.persist_unpause_immunity()

        learner2 = PerformanceLearner()
        with patch_session:
            await learner2.restore_unpause_immunity()

        assert "value_betting" in learner2._unpause_immunity

    @pytest.mark.asyncio
    async def test_persist_empty_immunity(self, session_factory, patch_session):
        """Persisting with no immunity should save empty dict."""
        from bot.agent.learner import PerformanceLearner

        learner = PerformanceLearner()
        now = datetime.now(timezone.utc)
        learner._unpause_immunity = {"value_betting": now}

        with patch_session:
            await learner.persist_unpause_immunity()

        learner._unpause_immunity = {}
        with patch_session:
            await learner.persist_unpause_immunity()

        with patch_session:
            loaded = await StateStore.load_unpause_immunity()

        assert loaded == {}

    @pytest.mark.asyncio
    async def test_restore_mixed_old_and_recent_immunity(
        self, session_factory, patch_session
    ):
        """Only recent immunity should survive restore; expired ones discarded."""
        from bot.agent.learner import PerformanceLearner

        learner = PerformanceLearner()

        now = datetime.now(timezone.utc)
        learner._unpause_immunity = {
            "recent_strat": now - timedelta(hours=1),
            "old_strat": now - timedelta(hours=7),
        }

        with patch_session:
            await learner.persist_unpause_immunity()

        learner2 = PerformanceLearner()
        with patch_session:
            await learner2.restore_unpause_immunity()

        assert "recent_strat" in learner2._unpause_immunity
        assert "old_strat" not in learner2._unpause_immunity
