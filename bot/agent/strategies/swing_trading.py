"""Swing Trading strategy: buy liquid mid-range markets and sell for quick profit.

Core idea: Instead of holding to resolution, buy markets with upward price
momentum and sell for 1-2% profit within hours. Uses in-memory price history
to detect momentum across scan cycles.

Entry: Liquid mid-range markets ($0.15-$0.85) with confirmed upward momentum.
Exit: Take profit at 1.5%, stop loss at 1.5%, time expiry at 4h, or momentum reversal.
Tier: TIER2+ ($25+) — requires capital efficiency understanding.
"""

from collections import deque
from datetime import datetime, timezone

import structlog

from bot.config import CapitalTier
from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

from .base import BaseStrategy

logger = structlog.get_logger()

# Tunable defaults
TAKE_PROFIT_PCT = 0.015     # 1.5% take profit
STOP_LOSS_PCT = 0.015       # 1.5% stop loss
MAX_HOLD_HOURS = 4.0        # Max time before forced exit
MIN_PRICE = 0.15            # Min market price for entry
MAX_PRICE = 0.85            # Max market price for entry
MIN_MOMENTUM = 0.005        # 0.5% minimum momentum
MIN_MOMENTUM_TICKS = 3      # Consecutive rising ticks required
MAX_SPREAD = 0.03           # Tighter spread than quality filter
MIN_VOLUME_24H = 200.0      # Higher volume threshold for swing
MIN_HOURS_LEFT = 6.0        # Need time for price movement
PRICE_HISTORY_MAXLEN = 20   # Snapshots kept per market


class SwingTradingStrategy(BaseStrategy):
    """Buy liquid mid-range markets and sell for quick profit."""

    name = "swing_trading"
    min_tier = CapitalTier.TIER2

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.TAKE_PROFIT_PCT = TAKE_PROFIT_PCT
        self.STOP_LOSS_PCT = STOP_LOSS_PCT
        self.MAX_HOLD_HOURS = MAX_HOLD_HOURS
        self.MIN_PRICE = MIN_PRICE
        self.MAX_PRICE = MAX_PRICE
        self.MIN_MOMENTUM = MIN_MOMENTUM
        self.MIN_MOMENTUM_TICKS = MIN_MOMENTUM_TICKS
        self.MAX_SPREAD = MAX_SPREAD
        self.MIN_VOLUME_24H = MIN_VOLUME_24H
        self.MIN_HOURS_LEFT = MIN_HOURS_LEFT
        # In-memory price history: market_id → recent bestBid snapshots
        self._price_history: dict[str, deque[float]] = {}

    def _update_price_history(self, markets: list[GammaMarket]) -> None:
        """Update bestBid snapshots for all markets with valid bid data."""
        for market in markets:
            if market.best_bid_price is not None and market.best_bid_price > 0:
                if market.id not in self._price_history:
                    self._price_history[market.id] = deque(
                        maxlen=PRICE_HISTORY_MAXLEN
                    )
                self._price_history[market.id].append(market.best_bid_price)

    def _detect_momentum(
        self, market_id: str
    ) -> tuple[bool, float]:
        """Check if market has upward price momentum.

        Returns (has_momentum, momentum_pct) where momentum_pct is the total
        move across the last MIN_MOMENTUM_TICKS rising ticks.
        """
        history = self._price_history.get(market_id)
        if not history or len(history) < self.MIN_MOMENTUM_TICKS:
            return False, 0.0

        # Check last MIN_MOMENTUM_TICKS entries are rising
        recent = list(history)[-self.MIN_MOMENTUM_TICKS:]
        for i in range(1, len(recent)):
            if recent[i] <= recent[i - 1]:
                return False, 0.0

        # Calculate total momentum
        start_price = recent[0]
        if start_price <= 0:
            return False, 0.0
        momentum_pct = (recent[-1] - start_price) / start_price

        if momentum_pct < self.MIN_MOMENTUM:
            return False, 0.0

        return True, momentum_pct

    def _detect_downward_momentum(self, market_id: str) -> bool:
        """Check if market has downward momentum (3+ falling ticks)."""
        history = self._price_history.get(market_id)
        if not history or len(history) < self.MIN_MOMENTUM_TICKS:
            return False

        recent = list(history)[-self.MIN_MOMENTUM_TICKS:]
        for i in range(1, len(recent)):
            if recent[i] >= recent[i - 1]:
                return False
        return True

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan for swing trading opportunities with upward momentum."""
        # Update price history for all markets
        self._update_price_history(markets)

        signals = []
        now = datetime.now(timezone.utc)

        for market in markets:
            signal = self._evaluate_market(market, now)
            if signal:
                signals.append(signal)

        # Sort by momentum strength
        signals.sort(
            key=lambda s: s.metadata.get("momentum_pct", 0.0), reverse=True
        )

        self.logger.info(
            "swing_trading_scan_complete",
            signals_found=len(signals),
            tracked_markets=len(self._price_history),
        )
        return signals

    def _evaluate_market(
        self, market: GammaMarket, now: datetime
    ) -> TradeSignal | None:
        """Evaluate a single market for swing entry."""
        # Must have end date and enough time
        end = market.end_date
        if end is None:
            return None
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)
        hours_left = (end - now).total_seconds() / 3600
        if hours_left < self.MIN_HOURS_LEFT:
            return None

        # Token IDs required
        token_ids = market.token_ids
        if not token_ids:
            return None

        # Check prices in mid-range
        prices = market.outcome_price_list
        if not prices:
            return None

        # Volume check — need high liquidity for swing trades
        if market.volume_24h < self.MIN_VOLUME_24H:
            return None

        # Spread check — must be tight for quick in/out
        if (
            market.best_bid_price is not None
            and market.best_ask_price is not None
        ):
            spread = market.best_ask_price - market.best_bid_price
            if spread > self.MAX_SPREAD:
                return None
        else:
            # No bid/ask data — skip
            return None

        # Evaluate each outcome
        outcomes = market.outcomes
        if not outcomes:
            return None

        for i, (outcome, price) in enumerate(zip(outcomes, prices)):
            if i >= len(token_ids):
                break

            # Price must be in mid-range
            if price < self.MIN_PRICE or price > self.MAX_PRICE:
                continue

            # Check momentum
            has_momentum, momentum_pct = self._detect_momentum(market.id)
            if not has_momentum:
                continue

            # Confidence based on momentum strength and spread tightness
            confidence = 0.60
            if momentum_pct >= 0.01:
                confidence += 0.10
            elif momentum_pct >= 0.007:
                confidence += 0.05
            if spread <= 0.01:
                confidence += 0.10
            elif spread <= 0.02:
                confidence += 0.05

            return TradeSignal(
                strategy=self.name,
                market_id=market.id,
                token_id=token_ids[i],
                question=market.question,
                side=OrderSide.BUY,
                outcome=outcome,
                estimated_prob=price + momentum_pct,
                market_price=price,
                edge=momentum_pct,
                size_usd=0.0,  # Set by risk manager
                confidence=min(0.90, confidence),
                reasoning=(
                    f"Swing: {outcome} at ${price:.3f} with "
                    f"{momentum_pct:.1%} momentum over "
                    f"{self.MIN_MOMENTUM_TICKS} ticks. "
                    f"Spread: ${spread:.3f}"
                ),
                metadata={
                    "category": market.category,
                    "hours_to_resolution": self.MAX_HOLD_HOURS,
                    "momentum_pct": momentum_pct,
                    "entry_spread": spread,
                },
            )

        return None

    async def should_exit(
        self, market_id: str, current_price: float, **kwargs
    ) -> bool:
        """Check swing exit conditions: take profit, stop loss, time, momentum reversal."""
        avg_price = kwargs.get("avg_price")
        created_at = kwargs.get("created_at")

        # Take profit
        if avg_price and avg_price > 0:
            profit_pct = (current_price - avg_price) / avg_price
            if profit_pct >= self.TAKE_PROFIT_PCT:
                self.logger.info(
                    "swing_take_profit",
                    market_id=market_id,
                    avg_price=round(avg_price, 4),
                    current_price=round(current_price, 4),
                    profit_pct=round(profit_pct, 4),
                )
                return True

            # Stop loss
            if profit_pct <= -self.STOP_LOSS_PCT:
                self.logger.info(
                    "swing_stop_loss",
                    market_id=market_id,
                    avg_price=round(avg_price, 4),
                    current_price=round(current_price, 4),
                    loss_pct=round(profit_pct, 4),
                )
                return True

        # Time expiry
        if created_at:
            now = datetime.now(timezone.utc)
            if created_at.tzinfo is None:
                created_at = created_at.replace(tzinfo=timezone.utc)
            held_hours = (now - created_at).total_seconds() / 3600
            if held_hours >= self.MAX_HOLD_HOURS:
                self.logger.info(
                    "swing_time_expiry",
                    market_id=market_id,
                    held_hours=round(held_hours, 2),
                    max_hours=self.MAX_HOLD_HOURS,
                )
                return True

        # Momentum reversal
        if self._detect_downward_momentum(market_id):
            self.logger.info(
                "swing_momentum_reversal",
                market_id=market_id,
                current_price=round(current_price, 4),
            )
            return True

        return False
