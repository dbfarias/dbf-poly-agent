"""Tests for Telegram notification functions."""

import os

os.environ.setdefault("API_SECRET_KEY", "test-key-32chars-long-enough-xx")

from unittest.mock import AsyncMock, patch

import pytest

from bot.utils.notifications import (
    _get_client,
    _safe_error_msg,
    close_telegram_client,
    notify_daily_target,
    notify_risk_limit,
    notify_strategy_paused,
    notify_trade,
    notify_error,
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


# ---------------------------------------------------------------------------
# send_telegram — HTTP path when Telegram IS configured
# ---------------------------------------------------------------------------


class TestSendTelegramHTTP:
    async def test_sends_http_post(self):
        """send_telegram should POST to the Telegram API with correct URL and payload."""
        mock_response = AsyncMock()
        mock_response.raise_for_status = lambda: None

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_response

        with (
            patch("bot.utils.notifications.settings") as mock_settings,
            patch("bot.utils.notifications._get_client", return_value=mock_client),
        ):
            mock_settings.has_telegram = True
            mock_settings.telegram_bot_token = "fake-token"
            mock_settings.telegram_chat_id = "12345"

            result = await send_telegram("hello world")

            assert result is True
            mock_client.post.assert_called_once()
            call_args = mock_client.post.call_args
            assert "/botfake-token/sendMessage" in call_args[0][0]
            payload = call_args[1]["json"]
            assert payload["chat_id"] == "12345"
            assert payload["text"] == "hello world"
            assert payload["parse_mode"] == "HTML"
            assert payload["disable_web_page_preview"] is True

    async def test_returns_false_on_http_error(self):
        """send_telegram should return False when the HTTP request fails."""
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("connection refused")

        with (
            patch("bot.utils.notifications.settings") as mock_settings,
            patch("bot.utils.notifications._get_client", return_value=mock_client),
        ):
            mock_settings.has_telegram = True
            mock_settings.telegram_bot_token = "fake-token"
            mock_settings.telegram_chat_id = "12345"

            result = await send_telegram("hello")

            assert result is False


# ---------------------------------------------------------------------------
# notify_trade
# ---------------------------------------------------------------------------


class TestNotifyTrade:
    async def test_opened_trade_format(self):
        """notify_trade('opened') should format with Trade OPENED, green emoji, and trade details."""
        with patch("bot.utils.notifications.send_telegram", new_callable=AsyncMock) as mock_send:
            await notify_trade(
                action="opened",
                strategy="time_decay",
                question="Will X happen?",
                side="YES",
                price=0.65,
                size=3.25,
            )
            mock_send.assert_called_once()
            msg = mock_send.call_args[0][0]
            assert "Trade OPENED" in msg
            assert "\U0001f7e2" in msg  # green circle emoji
            assert "time_decay" in msg
            assert "Will X happen?" in msg
            assert "YES" in msg
            assert "$0.6500" in msg
            assert "$3.25" in msg
            # PnL line should NOT appear for opened trades
            assert "PnL" not in msg

    async def test_closed_trade_with_pnl(self):
        """notify_trade('closed') with negative PnL should show red emoji and PnL line."""
        with patch("bot.utils.notifications.send_telegram", new_callable=AsyncMock) as mock_send:
            await notify_trade(
                action="closed",
                strategy="value_betting",
                question="Some market question",
                side="NO",
                price=0.40,
                size=2.00,
                pnl=-0.50,
            )
            mock_send.assert_called_once()
            msg = mock_send.call_args[0][0]
            assert "Trade CLOSED" in msg
            assert "\U0001f534" in msg  # red circle emoji (negative PnL)
            assert "PnL: $-0.50" in msg
            assert "value_betting" in msg


# ---------------------------------------------------------------------------
# _safe_error_msg
# ---------------------------------------------------------------------------


class TestSafeErrorMsg:
    def test_truncates_long_messages(self):
        """Messages longer than 200 chars should be truncated."""
        long_msg = "A" * 500
        result = _safe_error_msg(long_msg)
        assert len(result) <= 200

    def test_redacts_hex_addresses(self):
        """Hex strings with 20+ hex chars after '0x' should be redacted."""
        addr = "Transfer to 0x1234567890abcdef1234567890abcdef failed"
        result = _safe_error_msg(addr)
        assert "0x[REDACTED]" in result
        assert "1234567890abcdef" not in result

    def test_short_hex_not_redacted(self):
        """Hex strings shorter than 20 hex chars should NOT be redacted."""
        short_hex = "Error code 0x1234 occurred"
        result = _safe_error_msg(short_hex)
        assert "0x1234" in result
        assert "[REDACTED]" not in result


# ---------------------------------------------------------------------------
# Connection pooling — _get_client / close_telegram_client
# ---------------------------------------------------------------------------


class TestConnectionPooling:
    def test_get_client_returns_same_instance(self):
        """_get_client() should return the same httpx.AsyncClient on subsequent calls."""
        import bot.utils.notifications as mod

        original_client = mod._client
        try:
            mod._client = None  # reset
            first = _get_client()
            second = _get_client()
            assert first is second
        finally:
            # Restore original to avoid leaking state to other tests
            mod._client = original_client

    async def test_close_telegram_client(self):
        """close_telegram_client() should close the client and set _client to None."""
        import bot.utils.notifications as mod

        original_client = mod._client
        try:
            mod._client = None  # reset
            _get_client()  # create a client
            assert mod._client is not None

            await close_telegram_client()
            assert mod._client is None
        finally:
            mod._client = original_client
