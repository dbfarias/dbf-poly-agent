"""Tests for WatcherManager lifecycle management."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.agent.watcher_manager import MAX_WATCHERS, WatcherManager
from bot.data.models import Watcher


@pytest.fixture
def manager():
    return WatcherManager(engine=None)


def _make_watcher(watcher_id: int, market_id: str, status: str = "active") -> Watcher:
    """Create a mock Watcher object for testing."""
    w = MagicMock(spec=Watcher)
    w.id = watcher_id
    w.market_id = market_id
    w.status = status
    w.question = "Test question"
    w.highest_price = 0.5
    w.token_id = "tok_123"
    w.keywords = "[]"
    w.thesis = "test"
    w.max_exposure_usd = 20.0
    w.stop_loss_pct = 0.25
    w.max_age_hours = 168.0
    w.check_interval_sec = 900
    w.current_exposure = 0.0
    w.avg_entry_price = 0.5
    w.scale_count = 0
    w.max_scale_count = 3
    w.last_check_at = None
    w.last_news_at = None
    w.source_strategy = ""
    w.auto_created = False
    return w


class TestActiveWatchers:
    def test_empty_manager(self, manager):
        assert manager.active_count == 0
        assert manager.active_watchers == []

    def test_counts_only_active(self, manager):
        manager._watchers[1] = _make_watcher(1, "m1", "active")
        manager._watchers[2] = _make_watcher(2, "m2", "killed")
        assert manager.active_count == 1


class TestCreateWatcher:
    @pytest.mark.asyncio
    @patch("bot.agent.watcher_manager.async_session")
    @patch("bot.agent.watcher_manager.WatcherManager._spawn_task")
    async def test_create_success(self, mock_spawn, mock_session, manager):
        mock_ctx = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        # Make refresh set an id on the watcher
        async def fake_refresh(w):
            w.id = 1

        mock_ctx.refresh = fake_refresh
        mock_ctx.add = MagicMock()
        mock_ctx.commit = AsyncMock()

        result = await manager.create_watcher(
            market_id="m1",
            token_id="t1",
            question="Will X happen?",
            outcome="Yes",
            keywords=["x", "y"],
            thesis="X is likely",
            current_price=0.5,
        )

        assert result is not None
        assert result.market_id == "m1"
        assert manager.active_count == 1
        mock_spawn.assert_called_once()

    @pytest.mark.asyncio
    async def test_create_rejected_max_count(self, manager):
        # Fill up to max
        for i in range(MAX_WATCHERS):
            manager._watchers[i] = _make_watcher(i, f"m{i}", "active")

        result = await manager.create_watcher(
            market_id="new",
            token_id="t",
            question="Q",
            outcome="Y",
            keywords=[],
            thesis="T",
            current_price=0.5,
        )
        assert result is None

    @pytest.mark.asyncio
    async def test_create_rejected_duplicate(self, manager):
        manager._watchers[1] = _make_watcher(1, "m1", "active")

        result = await manager.create_watcher(
            market_id="m1",
            token_id="t",
            question="Q",
            outcome="Y",
            keywords=[],
            thesis="T",
            current_price=0.5,
        )
        assert result is None


class TestKillWatcher:
    @pytest.mark.asyncio
    @patch("bot.agent.watcher_manager.async_session")
    async def test_kill_active(self, mock_session, manager):
        mock_ctx = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)
        mock_ctx.merge = AsyncMock()
        mock_ctx.commit = AsyncMock()
        mock_ctx.add = MagicMock()

        w = _make_watcher(1, "m1", "active")
        manager._watchers[1] = w

        result = await manager.kill_watcher(1, reason="test")
        assert result is True

    @pytest.mark.asyncio
    async def test_kill_nonexistent(self, manager):
        result = await manager.kill_watcher(999, reason="test")
        assert result is False

    @pytest.mark.asyncio
    async def test_kill_already_killed(self, manager):
        manager._watchers[1] = _make_watcher(1, "m1", "killed")
        result = await manager.kill_watcher(1, reason="test")
        assert result is False


class TestGetAllWatchers:
    @pytest.mark.asyncio
    @patch("bot.agent.watcher_manager.async_session")
    async def test_returns_all(self, mock_session, manager):
        mock_ctx = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [
            _make_watcher(1, "m1"),
            _make_watcher(2, "m2", "killed"),
        ]
        mock_result.scalars.return_value = mock_scalars
        mock_ctx.execute = AsyncMock(return_value=mock_result)

        watchers = await manager.get_all_watchers()
        assert len(watchers) == 2


class TestRestoreFromDb:
    @pytest.mark.asyncio
    @patch("bot.agent.watcher_manager.WatcherManager._spawn_task")
    @patch("bot.agent.watcher_manager.async_session")
    async def test_restore(self, mock_session, mock_spawn, manager):
        mock_ctx = AsyncMock()
        mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
        mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

        w1 = _make_watcher(1, "m1")
        mock_result = MagicMock()
        mock_scalars = MagicMock()
        mock_scalars.all.return_value = [w1]
        mock_result.scalars.return_value = mock_scalars
        mock_ctx.execute = AsyncMock(return_value=mock_result)

        await manager.restore_from_db()

        assert manager.active_count == 1
        assert 1 in manager._watchers
        mock_spawn.assert_called_once_with(w1)


class TestShutdown:
    @pytest.mark.asyncio
    async def test_cancels_tasks(self, manager):
        task = MagicMock()
        task.done.return_value = False
        manager._tasks[1] = task

        await manager.shutdown()

        task.cancel.assert_called_once()
        assert len(manager._tasks) == 0
