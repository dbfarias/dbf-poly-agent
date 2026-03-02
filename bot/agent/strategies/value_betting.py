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

MIN_EDGE = 0.03  # 3% minimum edge for value bets (was 2% — not enough)
IMBALANCE_THRESHOLD = 0.15  # 15% order book imbalance (was 10% — too noisy)
MAX_PRICE = 0.95  # Skip markets above 95¢ (thin margin, high risk)
MIN_PRICE = 0.05  # Skip ultra-cheap markets (speculative noise)
MIN_BOOK_VOLUME = 200.0  # Min total order book volume (was 50 — thin books unreliable)
RELATIVE_STOP_LOSS = 0.05  # Exit if lost 5% from entry (was 10% — too slow)


class ValueBettingStrategy(BaseStrategy):
    """Detect mispriced markets via order book and volume analysis."""

    name = "value_betting"
    min_tier = CapitalTier.TIER1

    EXIT_TAKE_PROFIT_PCT = 0.015  # 1.5% take-profit threshold (was 3%)
    EXIT_MIN_HOLD_HOURS = 2.0  # Min hold before take-profit triggers (was 6h)

    _MUTABLE_PARAMS = {
        "MIN_EDGE": {"type": float, "min": 0.0, "max": 0.5},
        "IMBALANCE_THRESHOLD": {"type": float, "min": 0.0, "max": 1.0},
        "EXIT_TAKE_PROFIT_PCT": {"type": float, "min": 0.0, "max": 1.0},
        "EXIT_MIN_HOLD_HOURS": {"type": float, "min": 0.0, "max": 168.0},
    }

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

        # Per-side price check: at least one side must be in tradeable range
        no_price_est = market.no_price or (1.0 - yes_price)
        yes_in_range = MIN_PRICE <= yes_price <= MAX_PRICE
        no_in_range = MIN_PRICE <= no_price_est <= MAX_PRICE and len(token_ids) >= 2
        if not yes_in_range and not no_in_range:
            return None

        # Get order book for analysis
        try:
            book = await self.get_order_book(token_ids[0])
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

        # Reject thin order books (unreliable signals)
        if total_volume < MIN_BOOK_VOLUME:
            return None

        imbalance = (bid_volume - ask_volume) / total_volume

        # Strong buy pressure suggests market might be underpriced
        if abs(imbalance) < self.IMBALANCE_THRESHOLD:
            return None

        # Evaluate BOTH sides and pick the one with the higher edge.
        # Both sides share the same edge magnitude (abs_imbalance * 0.1),
        # so imbalance direction decides which side is underpriced.
        no_price = market.no_price or (1.0 - yes_price)
        has_no_token = len(token_ids) >= 2

        edge_val = abs(imbalance) * 0.1
        if edge_val < self.MIN_EDGE:
            return None

        # Determine which sides are in tradeable price range
        yes_ok = MIN_PRICE <= yes_price <= MAX_PRICE
        no_ok = has_no_token and MIN_PRICE <= no_price <= MAX_PRICE

        if yes_ok and no_ok:
            # Both in range — imbalance direction picks the underpriced side
            pick_yes = imbalance > 0
        elif yes_ok:
            pick_yes = True
        elif no_ok:
            pick_yes = False
        else:
            return None

        if pick_yes:
            estimated_prob = yes_price + edge_val
            side = OrderSide.BUY
            token_id = token_ids[0]
            outcome = "Yes"
            price = yes_price
        else:
            estimated_prob = no_price + edge_val
            side = OrderSide.BUY
            token_id = token_ids[1]
            outcome = "No"
            price = no_price

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

    async def should_exit(self, market_id: str, current_price: float, **kwargs) -> bool:
        """Exit on price drop, stop-loss, or take-profit."""
        avg_price = kwargs.get("avg_price", 0.0)
        created_at = kwargs.get("created_at")

        # Absolute floor: something went very wrong
        if current_price < 0.40:
            return True

        # Relative stop-loss: exit if lost 10%+ from entry
        if avg_price > 0:
            loss_pct = (avg_price - current_price) / avg_price
            if loss_pct >= RELATIVE_STOP_LOSS:
                self.logger.warning(
                    "value_betting_exit_stop_loss",
                    market_id=market_id,
                    avg_price=avg_price,
                    current_price=current_price,
                    loss_pct=f"{loss_pct:.1%}",
                )
                return True

        # Take-profit: lock in gains after minimum hold period
        if avg_price > 0 and created_at is not None:
            profit_pct = (current_price - avg_price) / avg_price
            if profit_pct >= self.EXIT_TAKE_PROFIT_PCT:
                now = datetime.now(timezone.utc)
                if created_at.tzinfo is None:
                    created_at = created_at.replace(tzinfo=timezone.utc)
                held_hours = (now - created_at).total_seconds() / 3600
                if held_hours >= self.EXIT_MIN_HOLD_HOURS:
                    self.logger.info(
                        "value_betting_take_profit",
                        market_id=market_id,
                        profit_pct=f"{profit_pct:.1%}",
                        held_hours=f"{held_hours:.0f}",
                    )
                    return True

        return False
