"""Real-time crypto spot prices via Coinbase WebSocket — free, no API key."""

import asyncio
import json
import time

import structlog
import websockets

from bot.research.technical_indicators import compute_macd, compute_rsi

logger = structlog.get_logger()


class SpotPriceWS:
    """Tracks real-time BTC/ETH/SOL prices from Coinbase WebSocket."""

    COINBASE_WS = "wss://ws-feed.exchange.coinbase.com"
    SYMBOLS = ["BTC-USD", "ETH-USD", "SOL-USD"]
    ROLLING_WINDOW = 1800  # 30-min rolling window for momentum (supports 15-min lookback)

    def __init__(self):
        self._prices: dict[str, float] = {}
        # symbol -> [(monotonic_ts, price)]
        self._history: dict[str, list[tuple[float, float]]] = {}
        self._running = False
        self._ws = None
        self._reconnect_delay = 1.0
        self._max_reconnect_delay = 60.0

    async def connect(self) -> None:
        """Connect to Coinbase WS and listen for ticker updates."""
        self._running = True
        while self._running:
            try:
                async with websockets.connect(
                    self.COINBASE_WS, ping_interval=20
                ) as ws:
                    self._ws = ws
                    self._reconnect_delay = 1.0
                    logger.info("spot_price_ws_connected")

                    # Subscribe to ticker channel
                    subscribe_msg = json.dumps({
                        "type": "subscribe",
                        "product_ids": self.SYMBOLS,
                        "channels": ["ticker"],
                    })
                    await ws.send(subscribe_msg)

                    await self._listen(ws)
            except websockets.ConnectionClosed:
                logger.warning("spot_price_ws_disconnected")
            except Exception as e:
                logger.error("spot_price_ws_error", error=str(e))

            if self._running:
                logger.info("spot_price_ws_reconnecting", delay=self._reconnect_delay)
                await asyncio.sleep(self._reconnect_delay)
                self._reconnect_delay = min(
                    self._reconnect_delay * 2, self._max_reconnect_delay
                )

    async def disconnect(self) -> None:
        self._running = False
        if self._ws:
            await self._ws.close()
            self._ws = None

    def get_price(self, symbol: str) -> float | None:
        """Get latest price for a symbol (e.g. 'BTC-USD')."""
        return self._prices.get(symbol)

    def get_prices(self) -> dict[str, float]:
        """Get all tracked prices."""
        return dict(self._prices)

    def get_price_history(self, symbol: str, window: int = 100) -> list[float]:
        """Return last N prices from the rolling history.

        Extracts price values only (no timestamps) from the internal history.
        Returns empty list if no data for the symbol.
        """
        history = self._history.get(symbol)
        if not history:
            return []
        # Extract only price values from (timestamp, price) tuples
        prices = [price for _, price in history]
        return prices[-window:]

    def get_rsi(self, symbol: str, period: int = 14) -> float | None:
        """Compute RSI from price history for a given symbol.

        Returns RSI (0-100) or None if insufficient data.
        """
        prices = self.get_price_history(symbol, window=period * 3)
        return compute_rsi(prices, period=period)

    def get_technical_summary(self, symbol: str) -> dict:
        """Return all available technical indicators for a symbol.

        Returns dict with keys: rsi, macd, macd_signal, macd_histogram,
        momentum_5m, momentum_15m, price. Values are None when insufficient data.
        """
        prices = self.get_price_history(symbol, window=100)
        current_price = self._prices.get(symbol)

        rsi = compute_rsi(prices) if len(prices) >= 15 else None
        macd_result = compute_macd(prices) if len(prices) >= 35 else None
        momentum_5m = self.get_momentum(symbol, window_seconds=300)
        momentum_15m = self.get_momentum(symbol, window_seconds=900)

        summary: dict = {
            "symbol": symbol,
            "price": current_price,
            "rsi": round(rsi, 2) if rsi is not None else None,
            "macd": None,
            "macd_signal": None,
            "macd_histogram": None,
            "momentum_5m": (
                round(momentum_5m, 6) if momentum_5m is not None else None
            ),
            "momentum_15m": (
                round(momentum_15m, 6) if momentum_15m is not None else None
            ),
        }

        if macd_result is not None:
            macd_line, signal_line, histogram = macd_result
            summary["macd"] = round(macd_line, 4)
            summary["macd_signal"] = round(signal_line, 4)
            summary["macd_histogram"] = round(histogram, 4)

        return summary

    def get_momentum(self, symbol: str, window_seconds: int = 300) -> float | None:
        """Calculate price change % over the given window.

        Returns (current - oldest) / oldest as a fraction.
        """
        history = self._history.get(symbol)
        if not history or len(history) < 2:
            return None

        now = time.monotonic()
        cutoff = now - window_seconds

        # Find oldest price within window
        oldest_price = None
        for ts, price in history:
            if ts >= cutoff:
                oldest_price = price
                break

        if oldest_price is None or oldest_price == 0:
            return None

        current_price = history[-1][1]
        return (current_price - oldest_price) / oldest_price

    async def _listen(self, ws) -> None:
        async for message in ws:
            try:
                data = json.loads(message)
                self._handle_ticker(data)
            except json.JSONDecodeError:
                continue
            except Exception as e:
                logger.error("spot_price_ws_message_error", error=str(e))

    def _handle_ticker(self, data: dict) -> None:
        """Handle a ticker message from Coinbase."""
        if data.get("type") != "ticker":
            return

        product_id = data.get("product_id", "")
        price_str = data.get("price")
        if not product_id or not price_str:
            return

        try:
            price = float(price_str)
        except (ValueError, TypeError):
            return

        self._prices[product_id] = price

        # Record in history
        now = time.monotonic()
        if product_id not in self._history:
            self._history[product_id] = []
        self._history[product_id].append((now, price))

        # Evict old entries
        cutoff = now - self.ROLLING_WINDOW * 2  # Keep 2x window for safety
        history = self._history[product_id]
        while history and history[0][0] < cutoff:
            history.pop(0)
