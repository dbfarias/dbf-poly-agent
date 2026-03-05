"""High-probability strategy: buy outcomes with strong implied probability.

Core idea: Markets with high implied probability (>90%) tend to resolve
as expected. Buy YES tokens at 0.60-0.97, collect $1.00 at resolution.

Uses a DYNAMIC time horizon based on daily target urgency:
- Ahead of target → only immediate markets (< 24h)
- On pace → short-term (< 72h)
- Behind target → expand to medium-term (< 168h)

Shorter markets always get higher priority via time-weighted scoring.
"""

from datetime import datetime, timezone

import structlog

from bot.config import CapitalTier
from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

from .base import BaseStrategy

logger = structlog.get_logger()

# Time horizon bands (hours)
HOURS_IMMEDIATE = 24.0   # Resolves today — highest priority
HOURS_SHORT = 48.0       # Resolves in 1-2 days
HOURS_MEDIUM = 168.0     # 7 days — absolute hard cap (urgency system limits effective horizon)

# Urgency-based time horizon caps (separate from the absolute HOURS_MEDIUM cap)
URGENCY_CAP_SHORT = 72.0    # Urgency 1.0 → up to 3 days
URGENCY_CAP_MAX = 168.0     # Urgency 1.3+ → up to 7 days

# Strategy parameters
MIN_IMPLIED_PROB = 0.80
MAX_PRICE = 0.97
MIN_PRICE = 0.80
MIN_EDGE = 0.015  # 1.5% minimum edge (real edge, no phantom bonus)
CONFIDENCE_BASE = 0.75


def _max_hours_for_urgency(urgency: float) -> float:
    """Compute max allowed hours based on urgency level.

    Linear interpolation between urgency breakpoints:
    - urgency 0.7 (ahead) → 24h only (capital efficient)
    - urgency 1.0 (on pace) → 72h
    - urgency 1.3+ (behind) → 168h (expand horizon to find more opportunities)
    """
    if urgency <= 0.7:
        return HOURS_IMMEDIATE
    elif urgency <= 1.0:
        # Interpolate 24h → 72h as urgency goes 0.7 → 1.0
        t = (urgency - 0.7) / 0.3
        return HOURS_IMMEDIATE + t * (URGENCY_CAP_SHORT - HOURS_IMMEDIATE)
    elif urgency <= 1.3:
        # Interpolate 72h → 168h as urgency goes 1.0 → 1.3
        t = (urgency - 1.0) / 0.3
        return URGENCY_CAP_SHORT + t * (URGENCY_CAP_MAX - URGENCY_CAP_SHORT)
    else:
        return URGENCY_CAP_MAX


class TimeDecayStrategy(BaseStrategy):
    """Buy high-probability outcomes, prefer markets near resolution."""

    name = "time_decay"
    min_tier = CapitalTier.TIER1

    EXIT_TAKE_PROFIT_PCT = 0.015  # 1.5% take-profit threshold (was 3%)
    EXIT_MIN_HOLD_HOURS = 4.0  # Min hold before take-profit triggers (was 12h)
    EXIT_STOP_LOSS_PCT = 0.10   # 10% stop-loss (tighter than universal 15%)

    # Anti-churn: minimum hold before rebalance can sell this strategy's positions
    MIN_HOLD_SECONDS = 7200  # 2h (high-prob markets need patience to resolve)

    _MUTABLE_PARAMS = {
        "MIN_EDGE": {"type": float, "min": 0.0, "max": 0.5},
        "MIN_PRICE": {"type": float, "min": 0.0, "max": 1.0},
        "MIN_IMPLIED_PROB": {"type": float, "min": 0.0, "max": 1.0},
        "CONFIDENCE_BASE": {"type": float, "min": 0.0, "max": 1.0},
        "MAX_HOURS_TO_RESOLUTION": {"type": float, "min": 1.0, "max": 720.0},
        "EXIT_TAKE_PROFIT_PCT": {"type": float, "min": 0.0, "max": 1.0},
        "EXIT_MIN_HOLD_HOURS": {"type": float, "min": 0.0, "max": 168.0},
        "EXIT_STOP_LOSS_PCT": {"type": float, "min": 0.0, "max": 1.0},
        "MIN_HOLD_SECONDS": {"type": int, "min": 0, "max": 14400},
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Tunable parameters (exposed to admin API via Settings page)
        self.MIN_EDGE = MIN_EDGE
        self.MIN_PRICE = MIN_PRICE
        self.MIN_IMPLIED_PROB = MIN_IMPLIED_PROB
        self.CONFIDENCE_BASE = CONFIDENCE_BASE
        self.MAX_HOURS_TO_RESOLUTION = HOURS_MEDIUM  # Hard cap on market horizon
        # Adaptive parameters (adjusted by learner)
        self._max_price = MAX_PRICE
        self._confidence_adjustment: dict[str, float] = {}
        self._urgency: float = 1.0

    def adjust_params(self, adjustments: dict) -> None:
        """Apply learner adjustments to time decay parameters.

        Accepts:
        - edge_multipliers: dict[(strategy, category), float]
        - category_confidences: dict[category, float]
        - calibration: dict[bucket, float]
        - urgency_multiplier: float (from daily target progress)
        """
        calibration = adjustments.get("calibration", {})

        # If high-confidence trades are poorly calibrated, tighten MAX_PRICE
        high_cal = calibration.get("95-99", 1.0)
        if high_cal < 0.7:
            self._max_price = 0.96
            self.logger.info(
                "time_decay_max_price_adjusted",
                max_price=self._max_price,
                calibration_95_99=high_cal,
            )
        else:
            self._max_price = MAX_PRICE

        # Store category confidences for use in scan
        self._confidence_adjustment = adjustments.get("category_confidences", {})

        # Store urgency for dynamic time horizon
        self._urgency = adjustments.get("urgency_multiplier", 1.0)

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan for high-probability outcomes."""
        signals = []
        now = datetime.now(timezone.utc)
        max_hours = min(
            _max_hours_for_urgency(self._urgency),
            self.MAX_HOURS_TO_RESOLUTION,
        )

        for market in markets:
            signal = await self._evaluate_market(market, now, max_hours)
            if signal:
                signals.append(signal)

        # Sort by time-weighted score: shorter resolution + higher edge = top priority
        signals.sort(key=lambda s: self._score_signal(s), reverse=True)

        self.logger.info(
            "time_decay_scan_complete",
            signals_found=len(signals),
            max_hours=round(max_hours, 0),
            urgency=round(self._urgency, 2),
        )
        return signals

    @staticmethod
    def _dynamic_max_price(hours_left: float) -> float:
        """Compute max acceptable price based on time to resolution.

        Closer to resolution → higher max price acceptable because
        less uncertainty remains and the position resolves quickly.
        Capital efficiency: at $0.97, need $0.03 profit. In 72h that's
        only 0.6%/day — below the 1% target. So cap prices tighter for
        longer markets.
        """
        if hours_left <= 12:
            return 0.99   # Resolves in hours — near certainty OK
        elif hours_left <= 24:
            return 0.98   # Resolves today
        elif hours_left <= 48:
            return 0.97   # Resolves in 1-2 days
        else:
            return 0.96   # 2-3 days — need more room for profit

    async def _evaluate_market(
        self, market: GammaMarket, now: datetime, max_hours: float
    ) -> TradeSignal | None:
        """Evaluate a single market for high-probability opportunity."""
        # Must have an end date
        end = market.end_date
        if end is None:
            return None
        if end.tzinfo is None:
            end = end.replace(tzinfo=timezone.utc)

        # Check hours to resolution (dynamic based on urgency)
        hours_left = (end - now).total_seconds() / 3600
        if hours_left <= 0 or hours_left > max_hours:
            return None

        # Check token IDs
        token_ids = market.token_ids
        if not token_ids:
            return None

        # Evaluate each outcome
        prices = market.outcome_price_list
        outcomes = market.outcomes
        if not prices or not outcomes:
            return None

        # Dynamic max price: allow higher prices for near-resolution markets
        max_price = self._dynamic_max_price(hours_left)

        for i, (outcome, price) in enumerate(zip(outcomes, prices)):
            if i >= len(token_ids):
                break

            # Is this a high-probability outcome?
            if price < self.MIN_PRICE or price > max_price:
                continue

            # Use actual ask price for BUY evaluation when available.
            # Gamma bestAsk is for YES token (index 0). This prevents
            # generating signals that evaporate at the real CLOB ask.
            buy_price = price  # default to mid-price
            if i == 0 and market.best_ask_price is not None:
                buy_price = market.best_ask_price

            # Estimate real probability (based on market consensus mid-price)
            estimated_prob = self._estimate_probability(price, hours_left)

            if estimated_prob < self.MIN_IMPLIED_PROB:
                continue

            # Edge against actual buy price (ask), not mid-price
            edge_val = estimated_prob - buy_price
            if edge_val < self.MIN_EDGE:
                continue

            # Capital efficiency check: expected daily return must justify
            # tying up capital.
            daily_return = edge_val / max(hours_left, 1.0) * 24.0
            if daily_return < 0.003:  # 0.3% daily minimum
                continue

            # Calculate confidence based on multiple factors
            confidence = self._calculate_confidence(price, hours_left)

            # Apply category confidence from learner
            cat_confidence = self._confidence_adjustment.get(
                market.category, 1.0
            )
            confidence = min(0.99, confidence * cat_confidence)

            return TradeSignal(
                strategy=self.name,
                market_id=market.id,
                token_id=token_ids[i],
                question=market.question,
                side=OrderSide.BUY,
                outcome=outcome,
                estimated_prob=estimated_prob,
                market_price=buy_price,
                edge=edge_val,
                size_usd=0.0,  # Will be set by risk manager
                confidence=confidence,
                reasoning=(
                    f"High-prob: {outcome} at ${buy_price:.2f} with "
                    f"{hours_left:.0f}h to resolution. "
                    f"Est. prob: {estimated_prob:.1%}, Edge: {edge_val:.1%}"
                ),
                metadata={
                    "category": market.category,
                    "hours_to_resolution": hours_left,
                },
            )

        return None

    def _estimate_probability(
        self, market_price: float, hours_left: float
    ) -> float:
        """Estimate real probability from market data.

        Conservative estimation: market price IS the best estimate.
        Time factor adds only a small bonus (max 2%) for near-resolution
        convergence — no phantom "near_certainty" bonuses that inflate edge.
        """
        base_prob = market_price

        # Time factor: less time = price converges to 0 or 1 (scales 0 to 0.02)
        time_factor = max(0, 1.0 - hours_left / HOURS_MEDIUM) * 0.02

        estimated = base_prob + time_factor
        return min(0.99, estimated)

    def _calculate_confidence(self, price: float, hours_left: float) -> float:
        """Calculate strategy confidence (0-1).

        Strongly rewards shorter time horizons.
        """
        confidence = self.CONFIDENCE_BASE

        # Higher price = more confident
        if price >= 0.95:
            confidence += 0.10
        elif price >= 0.92:
            confidence += 0.06
        elif price >= 0.90:
            confidence += 0.03

        # Time-based confidence: shorter = much more confident
        if hours_left <= 12:
            confidence += 0.12   # Resolves in hours
        elif hours_left <= 24:
            confidence += 0.10   # Resolves today
        elif hours_left <= 48:
            confidence += 0.06   # Resolves in 1-2 days
        elif hours_left <= 72:
            confidence += 0.03   # 2-3 days

        return min(0.99, confidence)

    @staticmethod
    def _score_signal(signal: TradeSignal) -> float:
        """Score a signal for sorting: shorter resolution + higher edge = better.

        Returns a composite score where time is weighted 60%, edge 40%.
        A market resolving in 6h with 2% edge beats one in 5 days with 3% edge.
        """
        hours = signal.metadata.get("hours_to_resolution", HOURS_MEDIUM)
        # Time score: 1.0 for immediate, 0.0 for 168h
        time_score = max(0.0, 1.0 - hours / HOURS_MEDIUM)
        # Edge score: normalized to ~0-1 range (3% = ~1.0)
        edge_score = min(1.0, signal.edge / 0.03)
        return time_score * 0.6 + edge_score * 0.4

    async def should_exit(self, market_id: str, current_price: float, **kwargs) -> bool:
        """Exit on strategy-specific stop-loss or take-profit."""
        avg_price = kwargs.get("avg_price", 0.0)
        created_at = kwargs.get("created_at")

        # Strategy-specific stop-loss (tighter than universal 15%)
        if avg_price > 0:
            loss_pct = (avg_price - current_price) / avg_price
            if loss_pct >= self.EXIT_STOP_LOSS_PCT:
                self.logger.warning(
                    "time_decay_stop_loss",
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
                        "time_decay_take_profit",
                        market_id=market_id,
                        profit_pct=f"{profit_pct:.1%}",
                        held_hours=f"{held_hours:.0f}",
                    )
                    return True

        return False
