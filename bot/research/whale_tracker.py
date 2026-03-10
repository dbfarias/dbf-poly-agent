"""Whale tracker — monitors top Polymarket traders and emits copy signals.

Zero LLM cost: scrapes leaderboard, polls wallet activity, detects new trades.
Uses token bucket rate limiter to stay under Data API limits (60 req/min).
"""

import asyncio
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone

import structlog

from bot.agent.market_analyzer import classify_market_type
from bot.data.database import async_session
from bot.data.models import TrackedWallet
from bot.data.repositories import TrackedWalletRepository
from bot.polymarket.data_api import DataApiClient

logger = structlog.get_logger()

# Constants
LEADERBOARD_REFRESH_INTERVAL = 1800  # 30 min
MAX_TRACKED_WALLETS = 20
MIN_WIN_RATE = 0.55
MIN_VOLUME = 5000.0  # $5K minimum volume
RATE_LIMIT_TOKENS_PER_MIN = 55  # Under 60 limit
ACTIVITY_POLL_DELAY = 1.5  # seconds between wallet polls


@dataclass(frozen=True)
class WhaleTrade:
    """A detected trade from a tracked whale wallet."""

    proxy_address: str
    username: str
    market_id: str
    question: str
    outcome: str
    side: str  # BUY or SELL
    size: float
    price: float
    win_rate: float
    trade_id: str = ""
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))


class TokenBucketRateLimiter:
    """Simple token bucket rate limiter for API calls."""

    def __init__(self, tokens_per_minute: int = RATE_LIMIT_TOKENS_PER_MIN):
        self._max_tokens = tokens_per_minute
        self._tokens = float(tokens_per_minute)
        self._last_refill = time.monotonic()
        self._refill_rate = tokens_per_minute / 60.0  # tokens per second

    async def acquire(self) -> None:
        """Wait until a token is available, then consume it."""
        while True:
            self._refill()
            if self._tokens >= 1.0:
                self._tokens -= 1.0
                return
            # Wait for at least one token to be available
            wait_time = (1.0 - self._tokens) / self._refill_rate
            await asyncio.sleep(min(wait_time, 2.0))

    def _refill(self) -> None:
        """Refill tokens based on elapsed time."""
        now = time.monotonic()
        elapsed = now - self._last_refill
        self._tokens = min(
            self._max_tokens, self._tokens + elapsed * self._refill_rate,
        )
        self._last_refill = now


class WhaleTracker:
    """Track top Polymarket traders and detect their new trades.

    Algorithm:
    1. Leaderboard refresh (every 30 min):
       - GET /leaderboard?window=7d&limit=100
       - Filter: win_rate > 55%, volume > $5K
       - Track top 20 wallets, persist to DB

    2. Activity polling (round-robin, 1.5s/wallet = 30s full cycle):
       - GET /activity?user={proxy_address} for each tracked wallet
       - Detect new trades via _last_seen_trade_id per wallet
       - Emit WhaleTrade events
    """

    def __init__(self, data_api: DataApiClient):
        self._data_api = data_api
        self._rate_limiter = TokenBucketRateLimiter()
        self._tracked_wallets: list[TrackedWallet] = []
        self._last_seen_trade_id: dict[str, str] = {}  # proxy_address -> last trade id
        self._last_leaderboard_refresh: float = 0.0
        self._whale_trades: list[WhaleTrade] = []
        self._running = False

    @property
    def tracked_count(self) -> int:
        return len(self._tracked_wallets)

    @property
    def status(self) -> dict:
        return {
            "running": self._running,
            "tracked_wallets": self.tracked_count,
            "pending_whale_trades": len(self._whale_trades),
        }

    async def refresh_leaderboard(self) -> int:
        """Fetch leaderboard and update tracked wallets in DB.

        Returns number of wallets now being tracked.
        """
        try:
            await self._rate_limiter.acquire()
            leaders = await self._data_api.get_leaderboard(
                window="7d", limit=100,
            )
        except Exception as e:
            logger.error("whale_tracker_leaderboard_failed", error=str(e))
            return self.tracked_count

        # Filter and rank
        # API fields: proxyWallet, userName, vol, pnl, rank
        # No winRate field — estimate from pnl/vol ratio
        qualified = []
        for entry in leaders:
            proxy = entry.get(
                "proxyWallet",
                entry.get("proxyAddress", entry.get("proxy_address", "")),
            )
            volume = float(
                entry.get("vol", entry.get("volume", entry.get("volume30d", 0))),
            )
            pnl = float(entry.get("pnl", entry.get("pnl7d", 0)))
            username = entry.get(
                "userName", entry.get("username", entry.get("name", "")),
            )

            if not proxy:
                continue
            if volume < MIN_VOLUME:
                continue

            # Estimate win rate: profitable traders with good pnl/vol ratio
            # pnl/vol > 0.05 → ~65% win rate, > 0.10 → ~70%, etc.
            win_rate_est = float(
                entry.get("winRate", entry.get("win_rate", 0)),
            )
            if win_rate_est == 0 and volume > 0:
                pnl_ratio = pnl / volume
                win_rate_est = min(0.85, 0.50 + pnl_ratio * 2.0)
                win_rate_est = max(0.0, win_rate_est)

            if win_rate_est < MIN_WIN_RATE:
                continue

            qualified.append({
                "proxy_address": proxy,
                "username": username,
                "pnl_7d": pnl,
                "pnl_30d": float(entry.get("pnl30d", entry.get("pnl_30d", 0))),
                "win_rate": win_rate_est,
                "volume_30d": volume,
            })

        # Sort by PnL descending, take top N
        qualified.sort(key=lambda x: x["pnl_7d"], reverse=True)
        top_wallets = qualified[:MAX_TRACKED_WALLETS]

        # Persist to DB
        try:
            async with async_session() as session:
                repo = TrackedWalletRepository(session)
                await repo.deactivate_all()

                persisted: list[TrackedWallet] = []
                for w in top_wallets:
                    wallet = TrackedWallet(
                        proxy_address=w["proxy_address"],
                        username=w["username"],
                        pnl_7d=w["pnl_7d"],
                        pnl_30d=w["pnl_30d"],
                        win_rate=w["win_rate"],
                        volume_30d=w["volume_30d"],
                        is_active=True,
                    )
                    persisted.append(await repo.upsert(wallet))

                self._tracked_wallets = persisted
        except Exception as e:
            logger.error("whale_tracker_persist_failed", error=str(e))

        self._last_leaderboard_refresh = time.monotonic()

        logger.info(
            "whale_tracker_leaderboard_refreshed",
            candidates=len(leaders),
            qualified=len(qualified),
            tracked=len(self._tracked_wallets),
        )
        return len(self._tracked_wallets)

    async def poll_wallet_activity(self, wallet: TrackedWallet) -> list[WhaleTrade]:
        """Poll a single wallet for new trades.

        Returns list of newly detected WhaleTrade events.
        """
        try:
            await self._rate_limiter.acquire()
            trades = await self._data_api.get_user_trades(
                wallet.proxy_address, limit=10,
            )
        except Exception as e:
            logger.debug(
                "whale_tracker_poll_failed",
                wallet=wallet.proxy_address[:12],
                error=str(e),
            )
            return []

        if not trades:
            return []

        last_seen = self._last_seen_trade_id.get(wallet.proxy_address, "")
        new_trades: list[WhaleTrade] = []

        for trade in trades:
            trade_id = str(
                trade.get("transactionHash", trade.get("id", trade.get("tradeId", ""))),
            )
            if not trade_id:
                continue

            # Stop at already-seen trades
            if trade_id == last_seen:
                break

            market_id = trade.get("conditionId", trade.get("market_id", ""))
            question = trade.get("title", trade.get("question", ""))
            outcome = trade.get("outcome", "")
            side = trade.get("side", "BUY").upper()
            size = float(trade.get("size", trade.get("amount", 0)))
            price = float(trade.get("price", 0))

            # Skip sports markets
            if question and classify_market_type(question) == "sports":
                continue

            new_trades.append(
                WhaleTrade(
                    proxy_address=wallet.proxy_address,
                    username=wallet.username,
                    market_id=market_id,
                    question=question,
                    outcome=outcome,
                    side=side,
                    size=size,
                    price=price,
                    win_rate=wallet.win_rate,
                    trade_id=trade_id,
                )
            )

        # Update last seen
        if trades:
            first_id = str(
                trades[0].get(
                    "transactionHash",
                    trades[0].get("id", trades[0].get("tradeId", "")),
                ),
            )
            if first_id:
                self._last_seen_trade_id[wallet.proxy_address] = first_id

        return new_trades

    async def poll_all_wallets(self) -> list[WhaleTrade]:
        """Poll all tracked wallets round-robin, respecting rate limits.

        Returns all new whale trades detected this cycle.
        """
        # Refresh leaderboard if needed
        elapsed = time.monotonic() - self._last_leaderboard_refresh
        if elapsed >= LEADERBOARD_REFRESH_INTERVAL or not self._tracked_wallets:
            await self.refresh_leaderboard()

        all_new_trades: list[WhaleTrade] = []

        for wallet in self._tracked_wallets:
            new_trades = await self.poll_wallet_activity(wallet)
            all_new_trades.extend(new_trades)
            # Delay between wallets to spread requests
            await asyncio.sleep(ACTIVITY_POLL_DELAY)

        if all_new_trades:
            logger.info(
                "whale_tracker_new_trades",
                count=len(all_new_trades),
                wallets_polled=len(self._tracked_wallets),
            )

        self._whale_trades = all_new_trades
        return all_new_trades

    def get_whale_trades(self) -> list[WhaleTrade]:
        """Return whale trades from last polling cycle."""
        return list(self._whale_trades)

    async def start(self) -> None:
        """Background loop: poll wallets continuously."""
        self._running = True
        logger.info("whale_tracker_started")

        # Initial delay for other services to warm up
        await asyncio.sleep(60)

        while self._running:
            try:
                await self.poll_all_wallets()
            except Exception as e:
                logger.error("whale_tracker_cycle_error", error=str(e))

            # Wait before next full cycle (wallets already have internal delays)
            await asyncio.sleep(5)

    async def stop(self) -> None:
        """Stop the background loop."""
        self._running = False
        logger.info("whale_tracker_stopped")
