"""Wrapper around py-clob-client with async support, retry, and paper trading mode."""

import asyncio
from datetime import datetime

import structlog

from bot.config import settings
from bot.polymarket.types import OrderBook, OrderBookEntry, OrderSide
from bot.utils.retry import async_retry

logger = structlog.get_logger()

# Tick size constants for Polymarket CLOB
TICK_SIZE = 0.01
MIN_ORDER_SIZE_USD = 5.0


class PolymarketClient:
    """Async wrapper around py-clob-client.

    Connects to Polymarket CLOB API using the private key.
    API credentials are auto-derived — no need to provide them manually.
    Connects even in paper mode so we can display real balances.
    """

    def __init__(self):
        self._clob_client = None
        self._initialized = False
        self._paper_orders: list[dict] = []
        self._paper_order_counter = 0

    async def initialize(self) -> None:
        """Initialize the CLOB client. Must be called before use.

        Always connects to Polymarket if a private key is available,
        even in paper mode (for balance display). API credentials are
        auto-derived from the private key.
        """
        if settings.poly_private_key:
            try:
                from py_clob_client.client import ClobClient

                # Step 1: Create Level-1 client (private key + signature type)
                self._clob_client = ClobClient(
                    host="https://clob.polymarket.com",
                    chain_id=settings.poly_chain_id,
                    key=settings.poly_private_key,
                    signature_type=settings.poly_signature_type,
                )

                # Step 2: Auto-derive API credentials from private key
                creds = await asyncio.to_thread(
                    self._clob_client.create_or_derive_api_creds
                )
                await asyncio.to_thread(
                    self._clob_client.set_api_creds, creds
                )

                address = self._clob_client.get_address()
                self._initialized = True
                logger.info(
                    "clob_client_initialized",
                    mode=settings.trading_mode.value,
                    address=address,
                    api_key=creds.api_key,
                )
            except Exception as e:
                logger.error("clob_client_init_failed", error=str(e))
                # In paper mode, failing to connect is non-fatal
                if settings.is_paper:
                    self._initialized = True
                    logger.warning("clob_fallback_paper_only")
                else:
                    raise
        else:
            self._initialized = True
            logger.info("clob_client_initialized", mode="paper_no_key")

    @property
    def is_paper(self) -> bool:
        return settings.is_paper

    @property
    def is_connected(self) -> bool:
        return self._clob_client is not None

    def get_address(self) -> str | None:
        """Get the wallet address from the connected client."""
        if self._clob_client is None:
            return None
        try:
            return self._clob_client.get_address()
        except Exception:
            return None

    @async_retry(max_attempts=3, min_wait=1, max_wait=15)
    async def get_balance(self) -> float | None:
        """Fetch real USDC balance from Polymarket.

        Returns None if not connected (no private key).
        Works in both paper and live mode.
        """
        if self._clob_client is None:
            return None

        try:
            from py_clob_client.clob_types import BalanceAllowanceParams

            params = BalanceAllowanceParams(asset_type="COLLATERAL")
            result = await asyncio.to_thread(
                self._clob_client.get_balance_allowance, params
            )
            balance = float(result.get("balance", 0))
            # USDC has 6 decimals on Polygon
            balance_usd = balance / 1e6
            logger.debug("polymarket_balance_fetched", balance_usd=balance_usd)
            return balance_usd
        except Exception as e:
            logger.error("get_balance_failed", error=str(e))
            return None

    @async_retry(max_attempts=3, min_wait=1, max_wait=15)
    async def get_order_book(self, token_id: str) -> OrderBook:
        """Fetch order book for a token."""
        if self.is_paper or self._clob_client is None:
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

        if self._clob_client is None:
            logger.error("order_failed_no_client")
            return {"error": "clob_client_not_initialized"}

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

        if self._clob_client is None:
            return False

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

        if self._clob_client is None:
            return False

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

        if self._clob_client is None:
            return []

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
