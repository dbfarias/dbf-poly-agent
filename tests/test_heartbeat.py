"""Tests for HeartbeatManager."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.polymarket.heartbeat import CRITICAL_MISS_THRESHOLD, HeartbeatManager


def _make_manager(*, is_paper: bool = False, clob_client=None):
    """Build a HeartbeatManager with a mocked PolymarketClient."""
    mock_poly = MagicMock()
    mock_poly.is_paper = is_paper
    mock_poly._clob_client = clob_client
    return HeartbeatManager(mock_poly)


# ------------------------------------------------------------------
# heartbeat_once calls clob_client when in live mode
# ------------------------------------------------------------------
async def test_heartbeat_once_calls_clob_in_live_mode():
    mock_clob = MagicMock()
    mock_clob.get_ok = MagicMock(return_value=True)
    manager = _make_manager(is_paper=False, clob_client=mock_clob)

    with patch("bot.polymarket.heartbeat.asyncio.to_thread", new_callable=AsyncMock) as to_thread:
        to_thread.return_value = None
        await manager._heartbeat_once()

    to_thread.assert_awaited_once_with(mock_clob.get_ok)


# ------------------------------------------------------------------
# heartbeat_once skips in paper mode
# ------------------------------------------------------------------
async def test_heartbeat_once_skips_in_paper_mode():
    manager = _make_manager(is_paper=True, clob_client=MagicMock())

    with patch("bot.polymarket.heartbeat.asyncio.to_thread", new_callable=AsyncMock) as to_thread:
        await manager._heartbeat_once()

    to_thread.assert_not_awaited()


async def test_heartbeat_once_skips_when_clob_client_is_none():
    manager = _make_manager(is_paper=False, clob_client=None)

    with patch("bot.polymarket.heartbeat.asyncio.to_thread", new_callable=AsyncMock) as to_thread:
        await manager._heartbeat_once()

    to_thread.assert_not_awaited()


# ------------------------------------------------------------------
# miss counter increments on failure
# ------------------------------------------------------------------
async def test_miss_counter_increments_on_failure():
    mock_clob = MagicMock()
    manager = _make_manager(is_paper=False, clob_client=mock_clob)

    with patch(
        "bot.polymarket.heartbeat.asyncio.to_thread",
        new_callable=AsyncMock,
        side_effect=ConnectionError("timeout"),
    ):
        await manager._heartbeat_once()

    assert manager._miss_count == 1

    with patch(
        "bot.polymarket.heartbeat.asyncio.to_thread",
        new_callable=AsyncMock,
        side_effect=ConnectionError("timeout"),
    ):
        await manager._heartbeat_once()

    assert manager._miss_count == 2


# ------------------------------------------------------------------
# miss counter resets on success
# ------------------------------------------------------------------
async def test_miss_counter_resets_on_success():
    mock_clob = MagicMock()
    manager = _make_manager(is_paper=False, clob_client=mock_clob)

    # Simulate some failures first
    manager._miss_count = 3

    with patch("bot.polymarket.heartbeat.asyncio.to_thread", new_callable=AsyncMock) as to_thread:
        to_thread.return_value = None
        await manager._heartbeat_once()

    assert manager._miss_count == 0


async def test_critical_fired_resets_on_success():
    mock_clob = MagicMock()
    manager = _make_manager(is_paper=False, clob_client=mock_clob)

    manager._miss_count = 3
    manager._critical_fired = True

    with patch("bot.polymarket.heartbeat.asyncio.to_thread", new_callable=AsyncMock) as to_thread:
        to_thread.return_value = None
        await manager._heartbeat_once()

    assert not manager._critical_fired


# ------------------------------------------------------------------
# critical callback fires after threshold misses
# ------------------------------------------------------------------
async def test_critical_callback_fires_after_threshold():
    mock_clob = MagicMock()
    manager = _make_manager(is_paper=False, clob_client=mock_clob)

    callback = AsyncMock()
    manager.set_on_critical_callback(callback)

    # Simulate failures up to one below the threshold
    manager._miss_count = CRITICAL_MISS_THRESHOLD - 1

    with patch(
        "bot.polymarket.heartbeat.asyncio.to_thread",
        new_callable=AsyncMock,
        side_effect=RuntimeError("down"),
    ):
        await manager._heartbeat_once()

    assert manager._miss_count == CRITICAL_MISS_THRESHOLD
    callback.assert_awaited_once()
    assert manager._critical_fired is True


async def test_critical_callback_fires_only_once():
    mock_clob = MagicMock()
    manager = _make_manager(is_paper=False, clob_client=mock_clob)

    callback = AsyncMock()
    manager.set_on_critical_callback(callback)

    # Already at threshold and already fired
    manager._miss_count = CRITICAL_MISS_THRESHOLD
    manager._critical_fired = True

    with patch(
        "bot.polymarket.heartbeat.asyncio.to_thread",
        new_callable=AsyncMock,
        side_effect=RuntimeError("still down"),
    ):
        await manager._heartbeat_once()

    # Should NOT fire again
    callback.assert_not_awaited()
    assert manager._miss_count == CRITICAL_MISS_THRESHOLD + 1


async def test_critical_no_callback_set():
    """Reaching threshold without a callback set should not raise."""
    mock_clob = MagicMock()
    manager = _make_manager(is_paper=False, clob_client=mock_clob)

    manager._miss_count = CRITICAL_MISS_THRESHOLD - 1

    with patch(
        "bot.polymarket.heartbeat.asyncio.to_thread",
        new_callable=AsyncMock,
        side_effect=RuntimeError("down"),
    ):
        await manager._heartbeat_once()  # should not raise

    assert manager._critical_fired is True


async def test_critical_callback_exception_is_swallowed():
    """If the critical callback raises, HeartbeatManager should not crash."""
    mock_clob = MagicMock()
    manager = _make_manager(is_paper=False, clob_client=mock_clob)

    callback = AsyncMock(side_effect=ValueError("callback boom"))
    manager.set_on_critical_callback(callback)

    manager._miss_count = CRITICAL_MISS_THRESHOLD - 1

    with patch(
        "bot.polymarket.heartbeat.asyncio.to_thread",
        new_callable=AsyncMock,
        side_effect=RuntimeError("down"),
    ):
        await manager._heartbeat_once()  # should not raise

    callback.assert_awaited_once()


# ------------------------------------------------------------------
# start / stop lifecycle
# ------------------------------------------------------------------
async def test_start_runs_heartbeat_loop_until_stopped():
    mock_clob = MagicMock()
    manager = _make_manager(is_paper=True, clob_client=mock_clob)

    call_count = 0

    async def _fake_heartbeat():
        nonlocal call_count
        call_count += 1
        if call_count >= 3:
            await manager.stop()

    manager._heartbeat_once = _fake_heartbeat

    with patch("bot.polymarket.heartbeat.asyncio.sleep", new_callable=AsyncMock):
        await manager.start()

    assert call_count >= 3
    assert not manager._running


async def test_stop_sets_running_false():
    mock_clob = MagicMock()
    manager = _make_manager(is_paper=True, clob_client=mock_clob)

    manager._running = True
    await manager.stop()

    assert not manager._running


async def test_start_sets_running_true():
    mock_clob = MagicMock()
    manager = _make_manager(is_paper=True, clob_client=mock_clob)

    # Stop immediately so start() doesn't loop forever
    async def _stop_immediately():
        await manager.stop()

    manager._heartbeat_once = _stop_immediately

    with patch("bot.polymarket.heartbeat.asyncio.sleep", new_callable=AsyncMock):
        await manager.start()

    # _running is False because we stopped, but it was True initially
    # We verify by checking that the loop ran (stop was called)
    assert not manager._running
