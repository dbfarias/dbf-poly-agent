"""Heartbeat manager for keeping API sessions alive.

Polymarket requires a heartbeat every 5 seconds for accounts with open orders.
"""

import asyncio
from collections.abc import Awaitable, Callable

import structlog

from bot.polymarket.client import PolymarketClient

logger = structlog.get_logger()

HEARTBEAT_INTERVAL = 5  # seconds
CRITICAL_MISS_THRESHOLD = 5


class HeartbeatManager:
    """Send periodic heartbeats to keep CLOB API session alive."""

    def __init__(self, clob_client: PolymarketClient):
        self.clob = clob_client
        self._running = False
        self._miss_count = 0
        self._on_critical: Callable[[], Awaitable[None]] | None = None
        self._critical_fired = False

    def set_on_critical_callback(
        self, callback: Callable[[], Awaitable[None]]
    ) -> None:
        """Set callback invoked when heartbeat reaches critical miss threshold."""
        self._on_critical = callback

    async def start(self) -> None:
        """Start the heartbeat loop."""
        self._running = True
        logger.info("heartbeat_started", interval=HEARTBEAT_INTERVAL)

        while self._running:
            await self._heartbeat_once()
            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def _heartbeat_once(self) -> None:
        """Execute a single heartbeat. Extracted for testability."""
        try:
            if not self.clob.is_paper and self.clob._clob_client:
                await asyncio.to_thread(self.clob._clob_client.get_ok)
                self._miss_count = 0
                self._critical_fired = False
        except Exception as e:
            self._miss_count += 1
            logger.warning(
                "heartbeat_failed",
                error=str(e),
                consecutive_misses=self._miss_count,
            )
            if self._miss_count >= CRITICAL_MISS_THRESHOLD and not self._critical_fired:
                logger.error("heartbeat_critical", misses=self._miss_count)
                self._critical_fired = True
                if self._on_critical:
                    try:
                        await self._on_critical()
                    except Exception as cb_err:
                        logger.error("heartbeat_critical_callback_failed", error=str(cb_err))

    async def stop(self) -> None:
        """Stop the heartbeat loop."""
        self._running = False
        logger.info("heartbeat_stopped")
