"""Abstract base class for trading strategies."""

from abc import ABC, abstractmethod

import structlog

from bot.data.market_cache import MarketCache
from bot.polymarket.client import PolymarketClient
from bot.polymarket.gamma import GammaClient
from bot.polymarket.types import GammaMarket, OrderBook, TradeSignal

logger = structlog.get_logger()


class BaseStrategy(ABC):
    """Base class for all trading strategies."""

    name: str = "base"

    # Subclasses define mutable params with type + range constraints.
    # Format: {"PARAM_NAME": {"type": float, "min": 0.0, "max": 1.0}}
    _MUTABLE_PARAMS: dict[str, dict] = {}

    def __init__(
        self,
        clob_client: PolymarketClient,
        gamma_client: GammaClient,
        cache: MarketCache,
    ):
        self.clob = clob_client
        self.gamma = gamma_client
        self.cache = cache
        self.logger = logger.bind(strategy=self.name)

    def update_param(self, name: str, value) -> bool:
        """Update a mutable parameter with validation.

        Returns True if the param was accepted, False if rejected.
        """
        spec = self._MUTABLE_PARAMS.get(name)
        if spec is None:
            self.logger.warning("param_rejected_unknown", param=name)
            return False

        expected_type = spec.get("type", float)
        try:
            value = expected_type(value)
        except (TypeError, ValueError):
            self.logger.warning(
                "param_rejected_type", param=name, expected=expected_type.__name__
            )
            return False

        min_val = spec.get("min")
        max_val = spec.get("max")
        if min_val is not None and value < min_val:
            self.logger.warning("param_rejected_range", param=name, value=value, min=min_val)
            return False
        if max_val is not None and value > max_val:
            self.logger.warning("param_rejected_range", param=name, value=value, max=max_val)
            return False

        setattr(self, name, value)
        self.logger.info("param_updated", param=name, value=value)
        return True

    async def get_order_book(self, token_id: str) -> OrderBook:
        """Fetch order book with cache (10s TTL).

        Checks MarketCache first. On miss, fetches from CLOB and caches.
        Prevents duplicate API calls when multiple strategies scan the
        same markets in the same cycle.
        """
        cached = self.cache.get_order_book(token_id)
        if cached is not None:
            return cached
        book = await self.clob.get_order_book(token_id)
        self.cache.set_order_book(token_id, book, ttl=10)
        return book


    def adjust_params(self, adjustments: dict) -> None:
        """Apply learner adjustments to strategy parameters.

        Subclasses can override for strategy-specific tuning.
        Default implementation is a no-op.

        Args:
            adjustments: dict with keys:
                - 'edge_multipliers': dict[tuple[str, str], float]
                - 'category_confidences': dict[str, float]
                - 'calibration': dict[str, float]
        """

    @abstractmethod
    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan markets and return trade signals.

        Each signal should include:
        - market_id, token_id, side
        - estimated_prob, market_price, edge
        - size_usd (suggested, will be adjusted by risk manager)
        - confidence (0-1)
        - reasoning (human-readable explanation)
        """
        ...

    @abstractmethod
    async def should_exit(self, market_id: str, current_price: float, **kwargs) -> bool:
        """Determine if an existing position should be exited.

        Args:
            market_id: The market identifier.
            current_price: Current market price for the position.
            **kwargs: Additional position context (e.g. avg_price, created_at).
        """
        ...

    def __repr__(self) -> str:
        return f"<Strategy:{self.name}>"
