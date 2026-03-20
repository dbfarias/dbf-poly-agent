"""Tests for Web Push notification system."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.utils.push_notifications import (
    _get_subscriptions,
    _has_vapid,
    _safe_error_msg,
    _send_to_all,
    add_subscription,
    push_notify_daily_summary,
    push_notify_error,
    push_notify_risk_limit,
    push_notify_strategy_paused,
    push_notify_trade,
    remove_subscription,
)


class TestHasVapid:
    def test_returns_false_when_not_configured(self):
        with patch("bot.utils.push_notifications.settings") as mock_settings:
            mock_settings.vapid_public_key = ""
            mock_settings.vapid_private_key = ""
            mock_settings.vapid_email = ""
            assert _has_vapid() is False

    def test_returns_true_when_configured(self):
        with patch("bot.utils.push_notifications.settings") as mock_settings:
            mock_settings.vapid_public_key = "BNtest123"
            mock_settings.vapid_private_key = "private123"
            mock_settings.vapid_email = "test@example.com"
            assert _has_vapid() is True

    def test_returns_false_when_partial(self):
        with patch("bot.utils.push_notifications.settings") as mock_settings:
            mock_settings.vapid_public_key = "BNtest123"
            mock_settings.vapid_private_key = ""
            mock_settings.vapid_email = "test@example.com"
            assert _has_vapid() is False


class TestSafeErrorMsg:
    def test_truncates_long_messages(self):
        msg = "x" * 300
        assert len(_safe_error_msg(msg)) == 200

    def test_redacts_hex_addresses(self):
        msg = "Error with address 0xabcdef1234567890abcdef1234567890"
        result = _safe_error_msg(msg)
        assert "0x[REDACTED]" in result
        assert "abcdef" not in result

    def test_short_message_unchanged(self):
        msg = "Simple error"
        assert _safe_error_msg(msg) == msg


class TestSubscriptionManagement:
    @pytest.fixture
    def mock_db(self):
        """Mock DB session and repo for subscription tests."""
        with patch("bot.utils.push_notifications.async_session") as mock_session:
            mock_repo = MagicMock()
            mock_ctx = AsyncMock()
            mock_ctx.__aenter__ = AsyncMock(return_value=MagicMock())
            mock_session.return_value = mock_ctx

            # Patch SettingsRepository to return our mock
            with patch("bot.utils.push_notifications.SettingsRepository") as mock_repo_cls:
                mock_repo_inst = MagicMock()
                mock_repo_inst.get = AsyncMock(return_value=None)
                mock_repo_inst.set_many = AsyncMock()
                mock_repo_cls.return_value = mock_repo_inst
                yield mock_repo_inst

    @pytest.mark.asyncio
    async def test_get_subscriptions_empty(self, mock_db):
        mock_db.get.return_value = None
        subs = await _get_subscriptions()
        assert subs == []

    @pytest.mark.asyncio
    async def test_get_subscriptions_returns_list(self, mock_db):
        mock_db.get.return_value = json.dumps([{"endpoint": "https://push.example.com/sub1"}])
        subs = await _get_subscriptions()
        assert len(subs) == 1
        assert subs[0]["endpoint"] == "https://push.example.com/sub1"

    @pytest.mark.asyncio
    async def test_get_subscriptions_invalid_json(self, mock_db):
        mock_db.get.return_value = "not-json"
        subs = await _get_subscriptions()
        assert subs == []

    @pytest.mark.asyncio
    async def test_add_subscription(self, mock_db):
        mock_db.get.return_value = json.dumps([])
        sub = {"endpoint": "https://push.example.com/sub1", "keys": {"p256dh": "key", "auth": "auth"}}
        await add_subscription(sub)
        mock_db.set_many.assert_called_once()

    @pytest.mark.asyncio
    async def test_add_subscription_deduplicates(self, mock_db):
        existing = [{"endpoint": "https://push.example.com/sub1", "keys": {"old": "key"}}]
        mock_db.get.return_value = json.dumps(existing)
        new_sub = {"endpoint": "https://push.example.com/sub1", "keys": {"new": "key"}}
        await add_subscription(new_sub)
        # Should save with only the new sub (old replaced)
        saved_data = json.loads(mock_db.set_many.call_args[0][0]["push.subscriptions"])
        assert len(saved_data) == 1
        assert saved_data[0]["keys"] == {"new": "key"}

    @pytest.mark.asyncio
    async def test_remove_subscription(self, mock_db):
        existing = [{"endpoint": "https://push.example.com/sub1"}]
        mock_db.get.return_value = json.dumps(existing)
        result = await remove_subscription("https://push.example.com/sub1")
        assert result is True
        mock_db.set_many.assert_called_once()

    @pytest.mark.asyncio
    async def test_remove_subscription_not_found(self, mock_db):
        mock_db.get.return_value = json.dumps([])
        result = await remove_subscription("https://push.example.com/nonexistent")
        assert result is False


class TestSendToAll:
    @pytest.mark.asyncio
    async def test_noop_when_no_vapid(self):
        with patch("bot.utils.push_notifications._has_vapid", return_value=False):
            result = await _send_to_all({"title": "test"})
            assert result == 0

    @pytest.mark.asyncio
    async def test_noop_when_no_subscriptions(self):
        with (
            patch("bot.utils.push_notifications._has_vapid", return_value=True),
            patch("bot.utils.push_notifications._get_subscriptions", new_callable=AsyncMock, return_value=[]),
        ):
            result = await _send_to_all({"title": "test"})
            assert result == 0

    @pytest.mark.asyncio
    async def test_sends_to_subscribers(self):
        subs = [
            {"endpoint": "https://push1.example.com", "keys": {"p256dh": "k1", "auth": "a1"}},
            {"endpoint": "https://push2.example.com", "keys": {"p256dh": "k2", "auth": "a2"}},
        ]
        mock_webpush = MagicMock()
        with (
            patch("bot.utils.push_notifications._has_vapid", return_value=True),
            patch("bot.utils.push_notifications._get_subscriptions", new_callable=AsyncMock, return_value=subs),
            patch("bot.utils.push_notifications.settings") as mock_settings,
            patch.dict("sys.modules", {"pywebpush": MagicMock(webpush=mock_webpush, WebPushException=Exception)}),
        ):
            mock_settings.vapid_private_key = "private_key"
            mock_settings.vapid_email = "test@example.com"
            result = await _send_to_all({"title": "test"})
            assert result == 2
            assert mock_webpush.call_count == 2

    @pytest.mark.asyncio
    async def test_removes_expired_subscriptions(self):
        subs = [{"endpoint": "https://expired.example.com", "keys": {"p256dh": "k", "auth": "a"}}]

        # Create a proper WebPushException with response status 410
        class MockWebPushException(Exception):
            def __init__(self):
                self.response = MagicMock(status_code=410)

        mock_webpush = MagicMock(side_effect=MockWebPushException())
        mock_save = AsyncMock()

        with (
            patch("bot.utils.push_notifications._has_vapid", return_value=True),
            patch("bot.utils.push_notifications._get_subscriptions", new_callable=AsyncMock, return_value=subs),
            patch("bot.utils.push_notifications._save_subscriptions", mock_save),
            patch("bot.utils.push_notifications.settings") as mock_settings,
            patch.dict("sys.modules", {"pywebpush": MagicMock(webpush=mock_webpush, WebPushException=MockWebPushException)}),
        ):
            mock_settings.vapid_private_key = "private_key"
            mock_settings.vapid_email = "test@example.com"
            result = await _send_to_all({"title": "test"})
            assert result == 0
            # Should save empty list after removing expired
            mock_save.assert_called_once_with([])

    @pytest.mark.asyncio
    async def test_vapid_claims_include_aud_derived_from_endpoint(self):
        """Each webpush() call must include an `aud` claim derived from the
        subscription endpoint origin (RFC 8292 requirement).

        Without `aud`, push services return 403 "aud claim MUST include origin".
        """
        subs = [
            {"endpoint": "https://fcm.googleapis.com/fcm/send/abc123", "keys": {"p256dh": "k1", "auth": "a1"}},
        ]
        captured_claims: list[dict] = []

        def fake_webpush(subscription_info, data, vapid_private_key, vapid_claims):
            captured_claims.append(dict(vapid_claims))

        with (
            patch("bot.utils.push_notifications._has_vapid", return_value=True),
            patch("bot.utils.push_notifications._get_subscriptions", new_callable=AsyncMock, return_value=subs),
            patch("bot.utils.push_notifications.settings") as mock_settings,
            patch.dict("sys.modules", {"pywebpush": MagicMock(webpush=fake_webpush, WebPushException=Exception)}),
        ):
            mock_settings.vapid_private_key = "private_key"
            mock_settings.vapid_email = "bot@example.com"
            result = await _send_to_all({"title": "test"})

        assert result == 1
        assert len(captured_claims) == 1
        claims = captured_claims[0]
        assert "aud" in claims
        assert claims["aud"] == "https://fcm.googleapis.com"
        assert claims["sub"] == "mailto:bot@example.com"

    @pytest.mark.asyncio
    async def test_vapid_aud_differs_per_subscription_origin(self):
        """When subscriptions have different push service origins, each call
        must send the correct `aud` for its specific endpoint."""
        subs = [
            {"endpoint": "https://fcm.googleapis.com/fcm/send/sub1", "keys": {"p256dh": "k1", "auth": "a1"}},
            {"endpoint": "https://updates.push.services.mozilla.com/push/sub2", "keys": {"p256dh": "k2", "auth": "a2"}},
        ]
        captured_claims: list[dict] = []

        def fake_webpush(subscription_info, data, vapid_private_key, vapid_claims):
            captured_claims.append(dict(vapid_claims))

        with (
            patch("bot.utils.push_notifications._has_vapid", return_value=True),
            patch("bot.utils.push_notifications._get_subscriptions", new_callable=AsyncMock, return_value=subs),
            patch("bot.utils.push_notifications.settings") as mock_settings,
            patch.dict("sys.modules", {"pywebpush": MagicMock(webpush=fake_webpush, WebPushException=Exception)}),
        ):
            mock_settings.vapid_private_key = "private_key"
            mock_settings.vapid_email = "bot@example.com"
            result = await _send_to_all({"title": "test"})

        assert result == 2
        assert captured_claims[0]["aud"] == "https://fcm.googleapis.com"
        assert captured_claims[1]["aud"] == "https://updates.push.services.mozilla.com"


class TestNotificationPayloads:
    @pytest.mark.asyncio
    async def test_push_notify_trade(self):
        with patch("bot.utils.push_notifications._send_to_all", new_callable=AsyncMock) as mock_send:
            await push_notify_trade("opened", "value_betting", "Will BTC hit $100k?", "BUY", 0.85, 10)
            mock_send.assert_called_once()
            payload = mock_send.call_args[0][0]
            assert "BUY" in payload["title"]
            assert "value_betting" in payload["body"]
            assert payload["data"]["url"] == "/trades"

    @pytest.mark.asyncio
    async def test_push_notify_trade_closed_with_pnl(self):
        with patch("bot.utils.push_notifications._send_to_all", new_callable=AsyncMock) as mock_send:
            await push_notify_trade("closed", "time_decay", "Question?", "SELL", 0.92, 5, pnl=0.35)
            payload = mock_send.call_args[0][0]
            assert "PnL" in payload["body"]
            assert "+0.35" in payload["body"]

    @pytest.mark.asyncio
    async def test_push_notify_error(self):
        with patch("bot.utils.push_notifications._send_to_all", new_callable=AsyncMock) as mock_send:
            await push_notify_error("trading_cycle", "Something went wrong")
            payload = mock_send.call_args[0][0]
            assert "Error" in payload["title"]
            assert "Something went wrong" in payload["body"]

    @pytest.mark.asyncio
    async def test_push_notify_strategy_paused(self):
        with patch("bot.utils.push_notifications._send_to_all", new_callable=AsyncMock) as mock_send:
            await push_notify_strategy_paused("value_betting", "Win rate 30%")
            payload = mock_send.call_args[0][0]
            assert "Paused" in payload["title"]
            assert "value_betting" in payload["body"]

    @pytest.mark.asyncio
    async def test_push_notify_risk_limit(self):
        with patch("bot.utils.push_notifications._send_to_all", new_callable=AsyncMock) as mock_send:
            await push_notify_risk_limit("daily_loss", 0.12, 0.10)
            payload = mock_send.call_args[0][0]
            assert "Risk" in payload["title"]
            assert "daily_loss" in payload["body"]

    @pytest.mark.asyncio
    async def test_push_notify_daily_summary(self):
        with patch("bot.utils.push_notifications._send_to_all", new_callable=AsyncMock) as mock_send:
            await push_notify_daily_summary(25.50, 0.50, 0.02, 5, 0.60)
            payload = mock_send.call_args[0][0]
            assert "Daily Summary" in payload["title"]
            assert "$25.50" in payload["body"]
