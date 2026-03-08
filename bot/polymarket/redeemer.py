"""Auto-redeem resolved positions on the ConditionalTokens (CTF) contract."""

import structlog

from bot.config import settings

logger = structlog.get_logger()

# Polygon mainnet contract addresses
CTF_ADDRESS = "0x4D97DCd97eC945f40cF65F87097ACe5EA0476045"
USDC_COLLATERAL = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"

# Minimal ABI: only redeemPositions
CTF_ABI = [
    {
        "inputs": [
            {"name": "collateralToken", "type": "address"},
            {"name": "parentCollectionId", "type": "bytes32"},
            {"name": "conditionId", "type": "bytes32"},
            {"name": "indexSets", "type": "uint256[]"},
        ],
        "name": "redeemPositions",
        "outputs": [],
        "stateMutability": "nonpayable",
        "type": "function",
    }
]


class PositionRedeemer:
    """Redeems resolved conditional token positions for USDC."""

    def __init__(self) -> None:
        self._w3 = None
        self._contract = None
        self._account = None
        self._initialized = False

    async def initialize(self) -> bool:
        """Connect to Polygon RPC and set up contract. Returns True on success."""
        try:
            from web3 import Web3
            from web3.middleware import ExtraDataToPOAMiddleware

            self._w3 = Web3(Web3.HTTPProvider(settings.polygon_rpc_url))
            self._w3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)

            if not self._w3.is_connected():
                logger.warning(
                    "redeemer_rpc_not_connected",
                    url=settings.polygon_rpc_url,
                )
                return False

            self._contract = self._w3.eth.contract(
                address=Web3.to_checksum_address(CTF_ADDRESS),
                abi=CTF_ABI,
            )
            # Derive account from the same private key used for Polymarket
            self._account = self._w3.eth.account.from_key(
                settings.poly_private_key,
            )
            self._initialized = True
            logger.info("redeemer_initialized", address=self._account.address)
            return True
        except Exception as e:
            logger.warning("redeemer_init_failed", error=str(e))
            return False

    async def redeem(
        self,
        condition_id: str,
        index_sets: list[int] | None = None,
    ) -> str | None:
        """Redeem resolved positions. Returns tx hash on success, None on failure.

        Args:
            condition_id: The condition ID (bytes32 hex string) of the resolved market.
            index_sets: Which outcome slots to redeem. Default [1, 2] redeems both.
        """
        if not self._initialized:
            ok = await self.initialize()
            if not ok:
                return None

        if index_sets is None:
            index_sets = [1, 2]

        try:
            from web3 import Web3

            # Build transaction
            parent_collection = bytes(32)  # 0x0...0 for root
            cond_bytes = bytes.fromhex(condition_id.replace("0x", ""))

            tx = self._contract.functions.redeemPositions(
                Web3.to_checksum_address(USDC_COLLATERAL),
                parent_collection,
                cond_bytes,
                index_sets,
            ).build_transaction({
                "from": self._account.address,
                "nonce": self._w3.eth.get_transaction_count(
                    self._account.address,
                ),
                "gas": 200_000,
                "gasPrice": self._w3.eth.gas_price,
                "chainId": 137,
            })

            # Sign and send
            signed = self._account.sign_transaction(tx)
            tx_hash = self._w3.eth.send_raw_transaction(
                signed.raw_transaction,
            )
            hex_hash = tx_hash.hex()

            logger.info(
                "redeem_tx_sent",
                condition_id=condition_id[:16],
                tx_hash=hex_hash,
            )
            return hex_hash

        except Exception as e:
            logger.warning(
                "redeem_failed",
                condition_id=(
                    condition_id[:16] if condition_id else "unknown"
                ),
                error=str(e),
            )
            return None
