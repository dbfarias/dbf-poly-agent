"""Auto-redeem resolved CTF positions with on-chain verification."""

import os
from dataclasses import dataclass
from typing import Any

import structlog

from bot.config import settings
from bot.polymarket.redeemer_abi import CTF_ABI, NEG_RISK_ABI

logger = structlog.get_logger()

# Polygon mainnet contract addresses
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_COLLATERAL = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
NEG_RISK_ADAPTER = "0xd91E80cF2E7be2e162c6513ceD06f1dD0dA35296"

MIN_GAS_PRICE = 30 * 10**9  # 30 gwei floor
CHAIN_ID = 137
POSITIONS_API = "https://data-api.polymarket.com/positions"


@dataclass(frozen=True)
class RedeemablePosition:
    """Immutable snapshot of a position eligible for redemption."""

    condition_id: str
    token_id: str
    size: float
    is_neg_risk: bool
    winning_index_sets: list[int]


class PositionRedeemer:
    """Redeems resolved CTF positions for USDC (standard + NegRisk)."""

    def __init__(self, proxy_address: str | None = None) -> None:
        self._w3: Any = None
        self._ctf: Any = None
        self._neg_risk: Any = None
        self._account: Any = None
        self._proxy_address = proxy_address or os.environ.get("POLY_PROXY_ADDRESS", "")
        self._initialized = False

    async def initialize(self) -> bool:
        """Connect to Polygon RPC and set up contracts."""
        try:
            from web3 import Web3
            from web3.middleware import ExtraDataToPOAMiddleware

            self._w3 = Web3(Web3.HTTPProvider(settings.polygon_rpc_url))
            self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

            if not self._w3.is_connected():
                logger.warning("redeemer_rpc_not_connected", url=settings.polygon_rpc_url)
                return False

            checksum = Web3.to_checksum_address
            self._ctf = self._w3.eth.contract(address=checksum(CTF_ADDRESS), abi=CTF_ABI)
            self._neg_risk = self._w3.eth.contract(
                address=checksum(NEG_RISK_ADAPTER), abi=NEG_RISK_ABI,
            )
            self._account = self._w3.eth.account.from_key(settings.poly_private_key)

            if not self._proxy_address:
                from bot.polymarket.client import derive_proxy_wallet
                self._proxy_address = derive_proxy_wallet(self._account.address)

            self._initialized = True
            logger.info(
                "redeemer_initialized",
                eoa=self._account.address,
                proxy=self._proxy_address[:16],
            )
            return True
        except Exception as e:
            logger.warning("redeemer_init_failed", error=str(e))
            return False

    async def _ensure_init(self) -> bool:
        if not self._initialized:
            return await self.initialize()
        return True

    def _gas_price(self) -> int:
        return max(self._w3.eth.gas_price, MIN_GAS_PRICE)

    def _condition_bytes(self, condition_id: str) -> bytes:
        return bytes.fromhex(condition_id.replace("0x", ""))

    def is_resolved(self, condition_id: str) -> bool:
        """Check if a market is resolved on-chain (payoutDenominator > 0)."""
        cond = self._condition_bytes(condition_id)
        return self._ctf.functions.payoutDenominator(cond).call() > 0

    def get_winning_index_sets(self, condition_id: str) -> list[int]:
        """Return index sets for winning outcomes (where payoutNumerator > 0)."""
        cond = self._condition_bytes(condition_id)
        winning = []
        for i in range(2):  # binary markets: outcomes 0 and 1
            numerator = self._ctf.functions.payoutNumerators(cond, i).call()
            if numerator > 0:
                winning.append(1 << i)  # index set: 1 for outcome 0, 2 for outcome 1
        return winning

    def get_balance(self, token_id: str, address: str | None = None) -> int:
        """Get CTF token balance for an address (defaults to proxy wallet)."""
        from web3 import Web3
        addr = Web3.to_checksum_address(address or self._proxy_address)
        return self._ctf.functions.balanceOf(addr, int(token_id)).call()

    def is_approved_for_neg_risk(self, address: str | None = None) -> bool:
        """Check if NegRisk adapter is approved as operator for the address."""
        from web3 import Web3
        addr = Web3.to_checksum_address(address or self._proxy_address)
        operator = Web3.to_checksum_address(NEG_RISK_ADAPTER)
        return self._ctf.functions.isApprovedForAll(addr, operator).call()

    async def redeem(
        self,
        condition_id: str,
        is_neg_risk: bool = False,
        index_sets: list[int] | None = None,
    ) -> str | None:
        """Redeem resolved positions. Returns tx hash or None."""
        if not await self._ensure_init():
            return None

        try:
            if not self.is_resolved(condition_id):
                logger.info("redeem_skip_unresolved", condition_id=condition_id[:16])
                return None

            if index_sets is None:
                index_sets = self.get_winning_index_sets(condition_id)

            if not index_sets:
                logger.info("redeem_skip_no_winners", condition_id=condition_id[:16])
                return None

            cond = self._condition_bytes(condition_id)

            if is_neg_risk:
                tx_data = self._neg_risk.functions.redeemPositions(
                    cond, index_sets,
                ).build_transaction(self._tx_params())
            else:
                from web3 import Web3
                parent_collection = bytes(32)
                tx_data = self._ctf.functions.redeemPositions(
                    Web3.to_checksum_address(USDC_COLLATERAL),
                    parent_collection,
                    cond,
                    index_sets,
                ).build_transaction(self._tx_params())

            signed = self._account.sign_transaction(tx_data)
            tx_hash = self._w3.eth.send_raw_transaction(signed.raw_transaction)
            hex_hash = tx_hash.hex()
            logger.info(
                "redeem_tx_sent",
                condition_id=condition_id[:16],
                tx_hash=hex_hash,
                neg_risk=is_neg_risk,
            )
            return hex_hash

        except Exception as e:
            cid = condition_id[:16] if condition_id else "unknown"
            logger.warning("redeem_failed", condition_id=cid, error=str(e))
            return None

    def _tx_params(self) -> dict[str, Any]:
        return {
            "from": self._account.address,
            "nonce": self._w3.eth.get_transaction_count(self._account.address),
            "gas": 200_000,
            "gasPrice": self._gas_price(),
            "chainId": CHAIN_ID,
        }

    async def get_redeemable_positions(self) -> list[RedeemablePosition]:
        """Discover all redeemable positions via Polymarket data API + on-chain checks."""
        if not await self._ensure_init():
            return []

        try:
            import aiohttp

            url = f"{POSITIONS_API}?user={self._proxy_address.lower()}"
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=15)) as resp:
                    if resp.status != 200:
                        logger.warning("redeemer_api_error", status=resp.status)
                        return []
                    positions = await resp.json()

            redeemable: list[RedeemablePosition] = []
            for pos in positions:
                condition_id = pos.get("conditionId", "")
                token_id = pos.get("tokenId", "")
                size = float(pos.get("size", 0))
                is_neg_risk = bool(pos.get("negRisk", False))

                if size <= 0 or not condition_id:
                    continue

                try:
                    if not self.is_resolved(condition_id):
                        continue
                    winning = self.get_winning_index_sets(condition_id)
                    if not winning:
                        continue
                except Exception:
                    logger.debug("redeemer_check_failed", condition_id=condition_id[:16])
                    continue

                redeemable.append(RedeemablePosition(
                    condition_id=condition_id,
                    token_id=token_id,
                    size=size,
                    is_neg_risk=is_neg_risk,
                    winning_index_sets=winning,
                ))

            logger.info("redeemer_scan_complete", total=len(positions), redeemable=len(redeemable))
            return redeemable

        except Exception as e:
            logger.warning("redeemer_scan_failed", error=str(e))
            return []

    async def redeem_all(self) -> list[str]:
        """Find and redeem all redeemable positions. Returns list of tx hashes."""
        positions = await self.get_redeemable_positions()
        tx_hashes: list[str] = []

        for pos in positions:
            tx_hash = await self.redeem(
                condition_id=pos.condition_id,
                is_neg_risk=pos.is_neg_risk,
                index_sets=pos.winning_index_sets,
            )
            if tx_hash:
                tx_hashes.append(tx_hash)

        logger.info("redeem_all_complete", redeemed=len(tx_hashes), total=len(positions))
        return tx_hashes
