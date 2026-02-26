"""Heartbeat manager for keeping API sessions alive.

Polymarket requires a heartbeat every 5 seconds for accounts with open orders.
"""

import asyncio

import structlog

from bot.polymarket.client import PolymarketClient

logger = structlog.get_logger()

HEARTBEAT_INTERVAL = 5  # seconds


class HeartbeatManager:
    """Send periodic heartbeats to keep CLOB API session alive."""

    def __init__(self, clob_client: PolymarketClient):
        self.clob = clob_client
        self._running = False
        self._miss_count = 0

    async def start(self) -> None:
        """Start the heartbeat loop."""
        self._running = True
        logger.info("heartbeat_started", interval=HEARTBEAT_INTERVAL)

        while self._running:
            try:
                if not self.clob.is_paper and self.clob._clob_client:
                    await asyncio.to_thread(self.clob._clob_client.get_ok)
                    self._miss_count = 0
            except Exception as e:
                self._miss_count += 1
                logger.warning(
                    "heartbeat_failed",
                    error=str(e),
                    consecutive_misses=self._miss_count,
                )
                if self._miss_count >= 5:
                    logger.error("heartbeat_critical", misses=self._miss_count)

            await asyncio.sleep(HEARTBEAT_INTERVAL)

    async def stop(self) -> None:
        """Stop the heartbeat loop."""
        self._running = False
        logger.info("heartbeat_stopped")
