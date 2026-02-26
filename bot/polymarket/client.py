"""Wrapper around py-clob-client with async support, retry, and paper trading mode."""

import asyncio
import math
from datetime import datetime, timezone

import structlog

from bot.config import settings
from bot.polymarket.types import OrderBook, OrderBookEntry, OrderSide
from bot.utils.retry import async_retry

logger = structlog.get_logger()

# Polymarket proxy wallet constants (for Magic Link / email users)
PROXY_FACTORY = "0xaB45c5A4B0c941a2F231C04C3f49182e1A254052"
PROXY_INIT_CODE_HASH = bytes.fromhex(
    "d21df8dc65880a8606f09fe0ce3df9b8869287ab0b058be05aa9e8af6330a00b"
)

# Tick size constants for Polymarket CLOB
TICK_SIZE = 0.01
# Minimum order constraints.  Polymarket requires a minimum notional value;
# the per-share minimum is kept at 1 so the bot can trade with limited capital.
MIN_ORDER_SHARES = 1.0
MIN_ORDER_SIZE_USD = 1.0


def derive_proxy_wallet(signer_address: str) -> str:
    """Derive the Polymarket proxy wallet address from a signer address.

    Uses CREATE2: keccak256(0xff ++ factory ++ salt ++ initCodeHash)[12:]
    where salt = keccak256(encodePacked(signer_address)).
    """
    from Crypto.Hash import keccak

    signer_bytes = bytes.fromhex(signer_address[2:])  # 20 bytes (encodePacked)
    salt_hash = keccak.new(digest_bits=256, data=signer_bytes).digest()

    factory_bytes = bytes.fromhex(PROXY_FACTORY[2:])
    create2_input = bytes([0xFF]) + factory_bytes + salt_hash + PROXY_INIT_CODE_HASH
    addr_hash = keccak.new(digest_bits=256, data=create2_input).digest()
    return "0x" + addr_hash[-20:].hex()


class PolymarketClient:
    """Async wrapper around py-clob-client.

    Connects to Polymarket CLOB API using the private key.
    API credentials are auto-derived — no need to provide them manually.
    Proxy wallet address is derived via CREATE2 for order signing.
    Connects even in paper mode so we can display real balances.
    """

    def __init__(self):
        self._clob_client = None
        self._initialized = False
        self._proxy_wallet: str | None = None
        self._paper_orders: list[dict] = []
        self._paper_order_counter = 0

    async def initialize(self) -> None:
        """Initialize the CLOB client. Must be called before use.

        Always connects to Polymarket if a private key is available,
        even in paper mode (for balance display). API credentials are
        auto-derived from the private key. Proxy wallet is derived via
        CREATE2 for order signing.
        """
        if settings.poly_private_key:
            try:
                from eth_account import Account
                from py_clob_client.client import ClobClient

                account = Account.from_key(settings.poly_private_key)
                signer_address = account.address
                self._proxy_wallet = derive_proxy_wallet(signer_address)

                # Step 2: Create client with proxy wallet as funder
                self._clob_client = ClobClient(
                    host="https://clob.polymarket.com",
                    chain_id=settings.poly_chain_id,
                    key=settings.poly_private_key,
                    signature_type=settings.poly_signature_type,
                    funder=self._proxy_wallet,
                )

                # Step 3: Auto-derive API credentials from private key
                creds = await asyncio.to_thread(
                    self._clob_client.create_or_derive_api_creds
                )
                await asyncio.to_thread(
                    self._clob_client.set_api_creds, creds
                )

                self._initialized = True
                logger.info(
                    "clob_client_initialized",
                    mode=settings.trading_mode.value,
                    signer=signer_address[:10] + "...",
                    proxy_wallet=self._proxy_wallet[:10] + "...",
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

    async def close(self) -> None:
        """Close the underlying CLOB client HTTP session."""
        if self._clob_client is not None:
            try:
                session = getattr(self._clob_client, "session", None)
                if session and hasattr(session, "close"):
                    session.close()
            except Exception:
                pass
            self._clob_client = None

    @property
    def is_paper(self) -> bool:
        return settings.is_paper

    @property
    def is_connected(self) -> bool:
        return self._clob_client is not None

    def get_address(self) -> str | None:
        """Get the proxy wallet address (the one holding funds on Polymarket)."""
        return self._proxy_wallet

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
    async def get_fee_rate(self, token_id: str) -> int:
        """Get fee rate in basis points for a token. 0 = no fee market."""
        if self.is_paper or self._clob_client is None:
            return 0
        try:
            bps = await asyncio.to_thread(
                self._clob_client.get_fee_rate_bps, token_id
            )
            return int(bps)
        except Exception as e:
            logger.warning("get_fee_rate_failed", token_id=token_id[:20], error=str(e))
            return 0

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
            timestamp=datetime.now(timezone.utc),
        )

    @async_retry(max_attempts=3, min_wait=1, max_wait=15)
    async def place_order(
        self,
        token_id: str,
        side: OrderSide,
        price: float,
        size: float,
        spread_cross_offset: float = 0.0,
    ) -> dict:
        """Place a limit order. Returns order info dict.

        Args:
            spread_cross_offset: Aggressive pricing offset to cross the spread
                for faster fills. BUY prices increase, SELL prices decrease.
                Capped at [0.01, 0.99] and re-rounded to tick size.
        """
        # Round price to tick size — direction-aware to ensure fills:
        # BUY: round UP (ceil) to match or beat the ask
        # SELL: round DOWN (floor) to match or beat the bid
        if side == OrderSide.BUY:
            price = round(math.ceil(price / TICK_SIZE) * TICK_SIZE, 2)
        else:
            price = round(math.floor(price / TICK_SIZE) * TICK_SIZE, 2)

        # Aggressive pricing: cross the spread for faster fills
        if spread_cross_offset > 0:
            if side == OrderSide.BUY:
                price = min(price + spread_cross_offset, 0.99)
            else:
                price = max(price - spread_cross_offset, 0.01)
            # Re-round to tick size after offset
            if side == OrderSide.BUY:
                price = round(math.ceil(price / TICK_SIZE) * TICK_SIZE, 2)
            else:
                price = round(math.floor(price / TICK_SIZE) * TICK_SIZE, 2)

        if size < MIN_ORDER_SHARES:
            logger.warning("order_below_min_shares", size=size, min_shares=MIN_ORDER_SHARES)
            return {"error": "below_minimum_order_size"}

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
                clob_status=signed_order.get("status", ""),
                clob_success=signed_order.get("success"),
                clob_error=signed_order.get("errorMsg", ""),
                tx_hashes=signed_order.get("transactionsHashes", []),
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
            "created_at": datetime.now(timezone.utc).isoformat(),
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
