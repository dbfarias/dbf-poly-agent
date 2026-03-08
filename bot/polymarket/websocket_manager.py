"""WebSocket manager for real-time market data from Polymarket."""

import asyncio
import json

import structlog
import websockets

from bot.data.market_cache import MarketCache
from bot.data.price_tracker import PriceTracker
from bot.polymarket.types import OrderBook, OrderBookEntry
from bot.research.whale_detector import WhaleDetector

logger = structlog.get_logger()

WS_URL = "wss://ws-subscriptions-clob.polymarket.com/ws/market"


class WebSocketManager:
    """Manages WebSocket connections for real-time market data."""

    def __init__(self, cache: MarketCache):
        self.cache = cache
        self.whale_detector = WhaleDetector()
        self.price_tracker: PriceTracker | None = None
        self._ws = None
        self._running = False
        self._subscribed_tokens: set[str] = set()
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0
        self._callbacks: list = []
        self._alert_callbacks: list = []

    def on_update(self, callback) -> None:
        """Register a callback for price updates."""
        self._callbacks.append(callback)

    async def connect(self) -> None:
        """Connect to WebSocket and start listening."""
        self._running = True
        while self._running:
            try:
                async with websockets.connect(WS_URL, ping_interval=20) as ws:
                    self._ws = ws
                    self._reconnect_delay = 1.0
                    logger.info("websocket_connected")

                    # Resubscribe to tokens
                    for token_id in self._subscribed_tokens:
                        await self._send_subscribe(token_id)

                    await self._listen(ws)
            except websockets.ConnectionClosed:
                logger.warning("websocket_disconnected")
            except Exception as e:
                logger.error("websocket_error", error=str(e))

            if self._running:
                logger.info("websocket_reconnecting", delay=self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay
                )

    async def subscribe(self, token_id: str) -> None:
        """Subscribe to price updates for a token."""
        self._subscribed_tokens.add(token_id)
        if self._ws:
            await self._send_subscribe(token_id)

    async def unsubscribe(self, token_id: str) -> None:
        """Unsubscribe from price updates."""
        self._subscribed_tokens.discard(token_id)
        if self._ws:
            try:
                msg = json.dumps({"type": "unsubscribe", "assets_ids": [token_id]})
                await self._ws.send(msg)
            except Exception as e:
                logger.error("unsubscribe_failed", error=str(e))

    async def disconnect(self) -> None:
        """Disconnect the WebSocket."""
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    async def _send_subscribe(self, token_id: str) -> None:
        try:
            msg = json.dumps({
                "type": "subscribe",
                "assets_ids": [token_id],
                "channels": ["book"],
            })
            await self._ws.send(msg)
            logger.debug("websocket_subscribed", token_id=token_id[:16])
        except Exception as e:
            logger.error("subscribe_failed", error=str(e))

    async def _listen(self, ws) -> None:
        """Listen for incoming messages."""
        async for message in ws:
            try:
                data = json.loads(message)
                await self._handle_message(data)
            except json.JSONDecodeError:
                continue
            except Exception as e:
                logger.error("ws_message_error", error=str(e))

    async def _handle_message(self, data: dict) -> None:
        """Handle a WebSocket message."""
        msg_type = data.get("type", "")

        if msg_type == "book":
            asset_id = data.get("asset_id", "")
            if not asset_id:
                return

            book = OrderBook(
                asset_id=asset_id,
                bids=[OrderBookEntry(price=float(b["price"]), size=float(b["size"]))
                      for b in data.get("bids", [])],
                asks=[OrderBookEntry(price=float(a["price"]), size=float(a["size"]))
                      for a in data.get("asks", [])],
            )
            self.cache.set_order_book(asset_id, book, ttl=30)

            # Feed whale detector
            self.whale_detector.record_book_update(asset_id, book)

            # Feed price tracker from best bid
            if self.price_tracker and book.bids:
                best_bid = float(book.bids[0].price)
                self.price_tracker.record(asset_id, best_bid)

                # Check price alerts
                alert = self.price_tracker.check_alerts(asset_id, best_bid)
                if alert:
                    for acb in self._alert_callbacks:
                        try:
                            await acb(asset_id, alert, best_bid)
                        except Exception as e:
                            logger.error("alert_callback_error", error=str(e))

            # Notify callbacks
            for cb in self._callbacks:
                try:
                    await cb(asset_id, book)
                except Exception as e:
                    logger.error("ws_callback_error", error=str(e))
