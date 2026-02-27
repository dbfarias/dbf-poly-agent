"""Abstract base class for trading strategies."""

from abc import ABC, abstractmethod

import structlog

from bot.config import CapitalTier
from bot.data.market_cache import MarketCache
from bot.polymarket.client import PolymarketClient
from bot.polymarket.gamma import GammaClient
from bot.polymarket.types import GammaMarket, TradeSignal

logger = structlog.get_logger()


class BaseStrategy(ABC):
    """Base class for all trading strategies."""

    name: str = "base"
    min_tier: CapitalTier = CapitalTier.TIER1

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

    def is_enabled_for_tier(self, tier: CapitalTier) -> bool:
        """Check if this strategy is enabled for the given capital tier."""
        tier_order = {CapitalTier.TIER1: 1, CapitalTier.TIER2: 2, CapitalTier.TIER3: 3}
        return tier_order.get(tier, 0) >= tier_order.get(self.min_tier, 0)

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
    async def should_exit(self, market_id: str, current_price: float) -> bool:
        """Determine if an existing position should be exited."""
        ...

    def __repr__(self) -> str:
        return f"<Strategy:{self.name}>"
