"""Tests for Telegram notification functions."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from unittest.mock import AsyncMock, patch

import pytest

from bot.utils.notifications import (
    notify_daily_target,
    notify_risk_limit,
    notify_strategy_paused,
    send_telegram,
)


# ---------------------------------------------------------------------------
# send_telegram — short-circuit when no Telegram configured
# ---------------------------------------------------------------------------


class TestSendTelegram:
    async def test_returns_false_when_no_telegram(self):
        """send_telegram should return False when has_telegram is False."""
        with patch("bot.utils.notifications.settings") as mock_settings:
            mock_settings.has_telegram = False
            result = await send_telegram("test")
            assert result is False


# ---------------------------------------------------------------------------
# notify_strategy_paused
# ---------------------------------------------------------------------------


class TestNotifyStrategyPaused:
    async def test_sends_correct_message(self):
        """notify_strategy_paused should call send_telegram with strategy name."""
        with patch("bot.utils.notifications.send_telegram", new_callable=AsyncMock) as mock_send:
            await notify_strategy_paused("time_decay", "Win rate 20%, PnL $-2.50")
            mock_send.assert_called_once()
            msg = mock_send.call_args[0][0]
            assert "Strategy Paused" in msg
            assert "time_decay" in msg
            assert "Win rate 20%" in msg


# ---------------------------------------------------------------------------
# notify_risk_limit
# ---------------------------------------------------------------------------


class TestNotifyRiskLimit:
    async def test_sends_correct_message(self):
        """notify_risk_limit should call send_telegram with limit details."""
        with patch("bot.utils.notifications.send_telegram", new_callable=AsyncMock) as mock_send:
            await notify_risk_limit("daily_loss", 0.12, 0.10)
            mock_send.assert_called_once()
            msg = mock_send.call_args[0][0]
            assert "Risk Limit Hit" in msg
            assert "daily_loss" in msg

    async def test_formats_percentages(self):
        """Percentage values should be formatted in the message."""
        with patch("bot.utils.notifications.send_telegram", new_callable=AsyncMock) as mock_send:
            await notify_risk_limit("max_drawdown", 0.25, 0.20)
            msg = mock_send.call_args[0][0]
            assert "25.0%" in msg
            assert "20.0%" in msg


# ---------------------------------------------------------------------------
# notify_daily_target
# ---------------------------------------------------------------------------


class TestNotifyDailyTarget:
    async def test_sends_correct_message(self):
        """notify_daily_target should call send_telegram with target details."""
        with patch("bot.utils.notifications.send_telegram", new_callable=AsyncMock) as mock_send:
            await notify_daily_target(15.0, 0.20, 0.01)
            mock_send.assert_called_once()
            msg = mock_send.call_args[0][0]
            assert "Daily Target Reached" in msg
            assert "$15.00" in msg
            assert "$+0.20" in msg
