"""Tests for bot.research.whale_tracker — WhaleTracker + rate limiter."""

import asyncio
import time
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.research.whale_tracker import (
    ACTIVITY_POLL_DELAY,
    LEADERBOARD_REFRESH_INTERVAL,
    MAX_TRACKED_WALLETS,
    MIN_VOLUME,
    MIN_WIN_RATE,
    TokenBucketRateLimiter,
    WhaleTrade,
    WhaleTracker,
)


class TestWhaleTrade:
    def test_frozen_dataclass(self):
        t = WhaleTrade(
            proxy_address="0xabc",
            username="whale1",
            market_id="m1",
            question="Will X?",
            outcome="Yes",
            side="BUY",
            size=100.0,
            price=0.60,
            win_rate=0.65,
        )
        assert t.proxy_address == "0xabc"
        assert t.side == "BUY"
        with pytest.raises(AttributeError):
            t.proxy_address = "0xdef"


class TestTokenBucketRateLimiter:
    @pytest.mark.asyncio
    async def test_acquire_immediate(self):
        limiter = TokenBucketRateLimiter(tokens_per_minute=60)
        # Should acquire immediately (bucket is full)
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        assert elapsed < 0.1

    @pytest.mark.asyncio
    async def test_acquire_depleted(self):
        limiter = TokenBucketRateLimiter(tokens_per_minute=60)
        # Drain all tokens
        limiter._tokens = 0.0
        start = time.monotonic()
        await limiter.acquire()
        elapsed = time.monotonic() - start
        # Should wait ~1 second for 1 token at 1 token/sec
        assert elapsed >= 0.5

    def test_refill(self):
        limiter = TokenBucketRateLimiter(tokens_per_minute=60)
        limiter._tokens = 0.0
        limiter._last_refill = time.monotonic() - 10  # 10 seconds ago
        limiter._refill()
        assert limiter._tokens >= 9.0  # ~10 tokens in 10 seconds

    def test_max_tokens_cap(self):
        limiter = TokenBucketRateLimiter(tokens_per_minute=55)
        limiter._tokens = 0.0
        limiter._last_refill = time.monotonic() - 120  # 2 minutes ago
        limiter._refill()
        assert limiter._tokens == 55  # Capped at max


@pytest.fixture
def mock_data_api():
    api = MagicMock()
    api.get_leaderboard = AsyncMock(return_value=[])
    api.get_user_trades = AsyncMock(return_value=[])
    api.get_user_activity = AsyncMock(return_value=[])
    return api


@pytest.fixture
def tracker(mock_data_api):
    return WhaleTracker(mock_data_api)


class TestWhaleTrackerInit:
    def test_initial_state(self, tracker):
        assert tracker.tracked_count == 0
        assert tracker._running is False
        assert tracker.status["running"] is False

    def test_status_dict(self, tracker):
        s = tracker.status
        assert "tracked_wallets" in s
        assert "pending_whale_trades" in s


class TestRefreshLeaderboard:
    @pytest.mark.asyncio
    async def test_filters_by_win_rate(self, tracker, mock_data_api):
        mock_data_api.get_leaderboard.return_value = [
            {"proxyAddress": "0xa", "username": "good", "winRate": 0.70,
             "volume": 10000, "pnl": 500},
            {"proxyAddress": "0xb", "username": "bad", "winRate": 0.40,
             "volume": 10000, "pnl": 100},
        ]

        with patch("bot.research.whale_tracker.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_repo = MagicMock()
            mock_repo.deactivate_all = AsyncMock()
            mock_repo.upsert = AsyncMock(side_effect=lambda w: w)

            with patch(
                "bot.research.whale_tracker.TrackedWalletRepository",
                return_value=mock_repo,
            ):
                count = await tracker.refresh_leaderboard()

        # Only the one with win_rate >= 0.55 should be tracked
        assert mock_repo.upsert.call_count == 1

    @pytest.mark.asyncio
    async def test_filters_by_volume(self, tracker, mock_data_api):
        mock_data_api.get_leaderboard.return_value = [
            {"proxyAddress": "0xa", "username": "rich", "winRate": 0.70,
             "volume": 10000, "pnl": 500},
            {"proxyAddress": "0xb", "username": "poor", "winRate": 0.70,
             "volume": 100, "pnl": 10},  # Below MIN_VOLUME
        ]

        with patch("bot.research.whale_tracker.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_repo = MagicMock()
            mock_repo.deactivate_all = AsyncMock()
            mock_repo.upsert = AsyncMock(side_effect=lambda w: w)

            with patch(
                "bot.research.whale_tracker.TrackedWalletRepository",
                return_value=mock_repo,
            ):
                count = await tracker.refresh_leaderboard()

        assert mock_repo.upsert.call_count == 1

    @pytest.mark.asyncio
    async def test_caps_at_max_wallets(self, tracker, mock_data_api):
        entries = [
            {"proxyAddress": f"0x{i:04x}", "username": f"w{i}", "winRate": 0.70,
             "volume": 10000, "pnl": 1000 - i}
            for i in range(50)
        ]
        mock_data_api.get_leaderboard.return_value = entries

        with patch("bot.research.whale_tracker.async_session") as mock_session_ctx:
            mock_session = AsyncMock()
            mock_session_ctx.return_value.__aenter__ = AsyncMock(return_value=mock_session)
            mock_session_ctx.return_value.__aexit__ = AsyncMock(return_value=False)

            mock_repo = MagicMock()
            mock_repo.deactivate_all = AsyncMock()
            mock_repo.upsert = AsyncMock(side_effect=lambda w: w)

            with patch(
                "bot.research.whale_tracker.TrackedWalletRepository",
                return_value=mock_repo,
            ):
                count = await tracker.refresh_leaderboard()

        assert mock_repo.upsert.call_count == MAX_TRACKED_WALLETS

    @pytest.mark.asyncio
    async def test_handles_api_error(self, tracker, mock_data_api):
        mock_data_api.get_leaderboard.side_effect = Exception("API down")
        count = await tracker.refresh_leaderboard()
        assert count == 0


class TestPollWalletActivity:
    @pytest.mark.asyncio
    async def test_detects_new_trades(self, tracker, mock_data_api):
        wallet = MagicMock()
        wallet.proxy_address = "0xabc"
        wallet.username = "whale1"
        wallet.win_rate = 0.70

        mock_data_api.get_user_trades.return_value = [
            {"id": "trade_1", "conditionId": "m1", "title": "Will X?",
             "outcome": "Yes", "side": "BUY", "size": 100, "price": 0.6},
        ]

        trades = await tracker.poll_wallet_activity(wallet)
        assert len(trades) == 1
        assert trades[0].trade_id == "trade_1"
        assert trades[0].side == "BUY"

    @pytest.mark.asyncio
    async def test_dedup_seen_trades(self, tracker, mock_data_api):
        wallet = MagicMock()
        wallet.proxy_address = "0xabc"
        wallet.username = "whale1"
        wallet.win_rate = 0.70

        # First poll: see trade_1
        mock_data_api.get_user_trades.return_value = [
            {"id": "trade_1", "conditionId": "m1", "title": "Q?",
             "outcome": "Yes", "side": "BUY", "size": 50, "price": 0.5},
        ]
        trades1 = await tracker.poll_wallet_activity(wallet)
        assert len(trades1) == 1

        # Second poll: same trade_1, should be deduped
        trades2 = await tracker.poll_wallet_activity(wallet)
        assert len(trades2) == 0

    @pytest.mark.asyncio
    async def test_skips_sports(self, tracker, mock_data_api):
        wallet = MagicMock()
        wallet.proxy_address = "0xabc"
        wallet.username = "whale1"
        wallet.win_rate = 0.70

        mock_data_api.get_user_trades.return_value = [
            {"id": "t1", "conditionId": "m1",
             "title": "Will the Lakers win the NBA championship?",
             "outcome": "Yes", "side": "BUY", "size": 100, "price": 0.5},
        ]
        trades = await tracker.poll_wallet_activity(wallet)
        assert len(trades) == 0

    @pytest.mark.asyncio
    async def test_handles_api_error(self, tracker, mock_data_api):
        wallet = MagicMock()
        wallet.proxy_address = "0xabc"
        wallet.username = "w"
        wallet.win_rate = 0.6

        mock_data_api.get_user_trades.side_effect = Exception("timeout")
        trades = await tracker.poll_wallet_activity(wallet)
        assert trades == []

    @pytest.mark.asyncio
    async def test_empty_trades(self, tracker, mock_data_api):
        wallet = MagicMock()
        wallet.proxy_address = "0xabc"
        wallet.username = "w"
        wallet.win_rate = 0.6

        mock_data_api.get_user_trades.return_value = []
        trades = await tracker.poll_wallet_activity(wallet)
        assert trades == []


class TestGetWhaleTrades:
    def test_returns_copy(self, tracker):
        tracker._whale_trades = [
            WhaleTrade(
                proxy_address="0x1", username="w", market_id="m1",
                question="Q?", outcome="Yes", side="BUY",
                size=50, price=0.5, win_rate=0.6,
            )
        ]
        result = tracker.get_whale_trades()
        assert len(result) == 1
        assert result is not tracker._whale_trades


class TestStop:
    @pytest.mark.asyncio
    async def test_stop_sets_flag(self, tracker):
        tracker._running = True
        await tracker.stop()
        assert tracker._running is False
