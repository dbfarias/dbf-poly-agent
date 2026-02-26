"""Wrapper around py-clob-client with async support, retry, and paper trading mode."""

import asyncio
from datetime import datetime

import structlog

from bot.config import TradingMode, settings
from bot.polymarket.types import OrderBook, OrderBookEntry, OrderSide
from bot.utils.retry import async_retry

logger = structlog.get_logger()

# Tick size constants for Polymarket CLOB
TICK_SIZE = 0.01
MIN_ORDER_SIZE_USD = 5.0


class PolymarketClient:
    """Async wrapper around py-clob-client."""

    def __init__(self):
        self._clob_client = None
        self._initialized = False
        self._paper_orders: list[dict] = []
        self._paper_order_counter = 0

    async def initialize(self) -> None:
        """Initialize the CLOB client. Must be called before use."""
        if settings.trading_mode == TradingMode.LIVE and settings.poly_api_key:
            try:
                from py_clob_client.client import ClobClient
                from py_clob_client.clob_types import ApiCreds

                creds = ApiCreds(
                    api_key=settings.poly_api_key,
                    api_secret=settings.poly_api_secret,
                    api_passphrase=settings.poly_api_passphrase,
                )
                self._clob_client = ClobClient(
                    host="https://clob.polymarket.com",
                    chain_id=settings.poly_chain_id,
                    key=settings.poly_private_key,
                    creds=creds,
                )
                # Derive API key if needed
                await asyncio.to_thread(self._clob_client.set_api_creds, creds)
                self._initialized = True
                logger.info("clob_client_initialized", mode="live")
            except Exception as e:
                logger.error("clob_client_init_failed", error=str(e))
                raise
        else:
            self._initialized = True
            logger.info("clob_client_initialized", mode="paper")

    @property
    def is_paper(self) -> bool:
        return settings.is_paper or self._clob_client is None

    @async_retry(max_attempts=3, min_wait=1, max_wait=15)
    async def get_order_book(self, token_id: str) -> OrderBook:
        """Fetch order book for a token."""
        if self.is_paper:
            return OrderBook(asset_id=token_id)

        raw = await asyncio.to_thread(self._clob_client.get_order_book, token_id)
        bids = [OrderBookEntry(price=float(b.price), size=float(b.size)) for b in raw.bids]
        asks = [OrderBookEntry(price=float(a.price), size=float(a.size)) for a in raw.asks]
        return OrderBook(
            asset_id=token_id,
            bids=sorted(bids, key=lambda x: x.price, reverse=True),
            asks=sorted(asks, key=lambda x: x.price),
            timestamp=datetime.utcnow(),
        )

    @async_retry(max_attempts=3, min_wait=1, max_wait=15)
    async def place_order(
        self,
        token_id: str,
        side: OrderSide,
        price: float,
        size: float,
    ) -> dict:
        """Place a limit order. Returns order info dict."""
        # Round price to tick size
        price = round(round(price / TICK_SIZE) * TICK_SIZE, 2)

        if size * price < MIN_ORDER_SIZE_USD:
            logger.warning("order_below_minimum", size=size, price=price)
            return {"error": "below_minimum_order_size"}

        if self.is_paper:
            return self._paper_place_order(token_id, side, price, size)

        from py_clob_client.clob_types import OrderArgs

        order_args = OrderArgs(
            token_id=token_id,
            price=price,
            size=size,
            side=side.value,
        )

        try:
            signed_order = await asyncio.to_thread(
                self._clob_client.create_and_post_order, order_args
            )
            logger.info(
                "order_placed",
                order_id=signed_order.get("orderID", ""),
                side=side.value,
                price=price,
                size=size,
            )
            return signed_order
        except Exception as e:
            logger.error("order_failed", error=str(e), side=side.value, price=price, size=size)
            raise

    @async_retry(max_attempts=3, min_wait=1, max_wait=10)
    async def cancel_order(self, order_id: str) -> bool:
        """Cancel an open order."""
        if self.is_paper:
            self._paper_orders = [o for o in self._paper_orders if o["order_id"] != order_id]
            logger.info("paper_order_cancelled", order_id=order_id)
            return True

        try:
            await asyncio.to_thread(self._clob_client.cancel, order_id)
            logger.info("order_cancelled", order_id=order_id)
            return True
        except Exception as e:
            logger.error("cancel_failed", order_id=order_id, error=str(e))
            return False

    async def cancel_all_orders(self) -> bool:
        """Cancel all open orders."""
        if self.is_paper:
            self._paper_orders.clear()
            return True

        try:
            await asyncio.to_thread(self._clob_client.cancel_all)
            logger.info("all_orders_cancelled")
            return True
        except Exception as e:
            logger.error("cancel_all_failed", error=str(e))
            return False

    @async_retry(max_attempts=3, min_wait=1, max_wait=15)
    async def get_open_orders(self) -> list[dict]:
        """Get all open orders."""
        if self.is_paper:
            return [o for o in self._paper_orders if o["status"] == "LIVE"]

        try:
            orders = await asyncio.to_thread(self._clob_client.get_orders)
            return orders
        except Exception as e:
            logger.error("get_orders_failed", error=str(e))
            return []

    def _paper_place_order(
        self, token_id: str, side: OrderSide, price: float, size: float
    ) -> dict:
        """Simulate order placement in paper trading mode."""
        self._paper_order_counter += 1
        order = {
            "orderID": f"paper_{self._paper_order_counter}",
            "order_id": f"paper_{self._paper_order_counter}",
            "token_id": token_id,
            "side": side.value,
            "price": price,
            "size": size,
            "status": "MATCHED",  # Paper orders fill immediately
            "filled_size": size,
            "created_at": datetime.utcnow().isoformat(),
        }
        self._paper_orders.append(order)
        logger.info(
            "paper_order_placed",
            order_id=order["order_id"],
            side=side.value,
            price=price,
            size=size,
        )
        return order
