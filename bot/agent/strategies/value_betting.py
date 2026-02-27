"""Value Betting strategy: detect mispriced markets using order book analysis.

Uses order book imbalance, volume momentum, and cross-market correlation
to estimate true probability.

Dynamic time horizon based on daily target urgency — same as time_decay:
- Ahead → immediate only (< 24h)
- On pace → short-term (< 72h)
- Behind → medium-term (< 168h)
"""

from datetime import datetime, timezone

import structlog

from bot.config import CapitalTier
from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

from .base import BaseStrategy
from .time_decay import HOURS_MEDIUM, _max_hours_for_urgency

logger = structlog.get_logger()

MIN_EDGE = 0.03  # 3% minimum edge for value bets
IMBALANCE_THRESHOLD = 0.15  # 15% order book imbalance


class ValueBettingStrategy(BaseStrategy):
    """Detect mispriced markets via order book and volume analysis."""

    name = "value_betting"
    min_tier = CapitalTier.TIER1

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.MIN_EDGE = MIN_EDGE
        self.IMBALANCE_THRESHOLD = IMBALANCE_THRESHOLD
        self._urgency: float = 1.0

    def adjust_params(self, adjustments: dict) -> None:
        """Store urgency for dynamic time horizon."""
        self._urgency = adjustments.get("urgency_multiplier", 1.0)

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan markets for value betting opportunities."""
        signals = []
        max_hours = _max_hours_for_urgency(self._urgency)

        for market in markets:
            signal = await self._evaluate_market(market, max_hours)
            if signal:
                signals.append(signal)

        # Sort by time-weighted score: shorter + higher edge = top
        signals.sort(key=lambda s: self._score_signal(s), reverse=True)

        self.logger.info(
            "value_betting_scan_complete",
            signals_found=len(signals),
            max_hours=round(max_hours, 0),
            urgency=round(self._urgency, 2),
        )
        return signals

    async def _evaluate_market(
        self, market: GammaMarket, max_hours: float
    ) -> TradeSignal | None:
        """Evaluate a market for mispricing using order book analysis."""
        # Must resolve within dynamic max hours
        end = market.end_date
        if end is None:
            return None
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        hours_left = (end - datetime.now(timezone.utc)).total_seconds() / 3600
        if hours_left <= 0 or hours_left > max_hours:
            return None

        token_ids = market.token_ids
        if not token_ids:
            return None

        yes_price = market.yes_price
        if yes_price is None:
            return None

        # Get order book for analysis
        try:
            book = await self.clob.get_order_book(token_ids[0])
        except Exception:
            return None

        if not book.bids or not book.asks:
            return None

        # Calculate order book imbalance
        bid_volume = sum(b.size for b in book.bids[:5])
        ask_volume = sum(a.size for a in book.asks[:5])
        total_volume = bid_volume + ask_volume

        if total_volume == 0:
            return None

        imbalance = (bid_volume - ask_volume) / total_volume

        # Strong buy pressure suggests market might be underpriced
        if abs(imbalance) < self.IMBALANCE_THRESHOLD:
            return None

        # Estimate real probability based on imbalance
        if imbalance > 0:
            # More bids than asks → price should go up → YES is underpriced
            estimated_prob = yes_price + imbalance * 0.1
            if estimated_prob - yes_price < self.MIN_EDGE:
                return None
            side = OrderSide.BUY
            token_id = token_ids[0]
            outcome = "Yes"
            price = yes_price
        else:
            # More asks than bids → price should go down → NO might be value
            if len(token_ids) < 2:
                return None
            no_price = market.no_price or (1.0 - yes_price)
            estimated_prob = no_price + abs(imbalance) * 0.1
            if estimated_prob - no_price < self.MIN_EDGE:
                return None
            side = OrderSide.BUY
            token_id = token_ids[1]
            outcome = "No"
            price = no_price

        edge_val = estimated_prob - price

        # Confidence: base imbalance + time bonus for shorter markets
        confidence = 0.6 + min(0.2, abs(imbalance))
        if hours_left <= 24:
            confidence += 0.08
        elif hours_left <= 72:
            confidence += 0.04

        return TradeSignal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_id,
            question=market.question,
            side=side,
            outcome=outcome,
            estimated_prob=min(0.95, estimated_prob),
            market_price=price,
            edge=edge_val,
            size_usd=0.0,
            confidence=min(0.95, confidence),
            reasoning=(
                f"Value bet: {outcome} at ${price:.3f}. "
                f"Book imbalance: {imbalance:+.1%} "
                f"(bid_vol={bid_volume:.0f}, ask_vol={ask_volume:.0f}). "
                f"Est. prob: {estimated_prob:.1%}, {hours_left:.0f}h to resolve"
            ),
            metadata={
                "category": market.category,
                "hours_to_resolution": hours_left,
                "imbalance": imbalance,
                "bid_volume": bid_volume,
                "ask_volume": ask_volume,
            },
        )

    @staticmethod
    def _score_signal(signal: TradeSignal) -> float:
        """Score: shorter resolution + higher edge = better."""
        hours = signal.metadata.get("hours_to_resolution", HOURS_MEDIUM)
        time_score = max(0.0, 1.0 - hours / HOURS_MEDIUM)
        edge_score = min(1.0, signal.edge / 0.05)
        return time_score * 0.6 + edge_score * 0.4

    async def should_exit(self, market_id: str, current_price: float) -> bool:
        """Exit if edge has been captured or price moves against us."""
        if current_price < 0.40:
            return True
        return False
