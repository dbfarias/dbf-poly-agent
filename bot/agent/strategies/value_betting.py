"""Value Betting strategy: detect mispriced markets using order book analysis.

Uses order book imbalance, volume momentum, and cross-market correlation
to estimate true probability.

Dynamic time horizon based on daily target urgency — same as time_decay:
- Ahead → immediate only (< 24h)
- On pace → short-term (< 72h)
- Behind → medium-term (< 168h)
"""

import time
from datetime import datetime, timezone

import structlog

from bot.agent.market_analyzer import classify_market_type
from bot.config import CapitalTier
from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal
from bot.utils.risk_metrics import compute_vpin

from .base import BaseStrategy
from .time_decay import HOURS_MEDIUM, _max_hours_for_urgency

logger = structlog.get_logger()

MIN_EDGE = 0.05  # 5% minimum edge for value bets (was 3% — too many thin-edge losing trades)
IMBALANCE_THRESHOLD = 0.15  # 15% order book imbalance (was 10% — too noisy)
SPORTS_IMBALANCE_THRESHOLD = 0.25  # 25% for sports — only obvious favorites
MAX_PRICE = 0.95  # Skip markets above 95¢ (thin margin, high risk)
MIN_PRICE = 0.05  # Skip ultra-cheap markets (speculative noise)
MIN_BOOK_VOLUME = 200.0  # Min total order book volume (was 50 — thin books unreliable)
RELATIVE_STOP_LOSS = 0.12  # Exit if lost 12% from entry (was 5% — too tight, spread alone triggers)


class ValueBettingStrategy(BaseStrategy):
    """Detect mispriced markets via order book and volume analysis."""

    name = "value_betting"
    min_tier = CapitalTier.TIER1

    EXIT_TAKE_PROFIT_PCT = 0.015  # 1.5% take-profit threshold (was 3%)
    EXIT_MIN_HOLD_HOURS = 2.0  # Min hold before take-profit triggers (was 6h)

    # Anti-churn: minimum hold before rebalance can sell this strategy's positions
    MIN_HOLD_SECONDS = 7200  # 2h

    VPIN_THRESHOLD = 0.95  # Skip markets with nearly one-sided flow

    _MUTABLE_PARAMS = {
        "MIN_EDGE": {"type": float, "min": 0.0, "max": 0.5},
        "IMBALANCE_THRESHOLD": {"type": float, "min": 0.0, "max": 1.0},
        "SPORTS_IMBALANCE_THRESHOLD": {"type": float, "min": 0.0, "max": 1.0},
        "EXIT_TAKE_PROFIT_PCT": {"type": float, "min": 0.0, "max": 1.0},
        "EXIT_MIN_HOLD_HOURS": {"type": float, "min": 0.0, "max": 168.0},
        "RELATIVE_STOP_LOSS": {"type": float, "min": 0.0, "max": 1.0},
        "MIN_PRICE": {"type": float, "min": 0.0, "max": 1.0},
        "MAX_PRICE": {"type": float, "min": 0.0, "max": 1.0},
        "MIN_BOOK_VOLUME": {"type": float, "min": 0.0, "max": 10000.0},
        "MIN_HOLD_SECONDS": {"type": int, "min": 0, "max": 14400},
        "MEAN_REVERSION_THRESHOLD": {"type": float, "min": 0.0, "max": 0.5},
        "VELOCITY_BOOST": {"type": float, "min": 0.0, "max": 0.3},
        "VELOCITY_PENALTY": {"type": float, "min": 0.0, "max": 0.3},
        "VPIN_THRESHOLD": {"type": float, "min": 0.0, "max": 1.0},
    }

    def __init__(self, *args, price_tracker=None, **kwargs):
        super().__init__(*args, **kwargs)
        self.MIN_EDGE = MIN_EDGE
        self.IMBALANCE_THRESHOLD = IMBALANCE_THRESHOLD
        self.SPORTS_IMBALANCE_THRESHOLD = SPORTS_IMBALANCE_THRESHOLD
        self.RELATIVE_STOP_LOSS = RELATIVE_STOP_LOSS
        self.MIN_PRICE = MIN_PRICE
        self.MAX_PRICE = MAX_PRICE
        self.MIN_BOOK_VOLUME = MIN_BOOK_VOLUME
        self._urgency: float = 1.0
        self._price_tracker = price_tracker
        self.MEAN_REVERSION_THRESHOLD = 0.05  # 5% price move in 5min → skip
        # Order book velocity tracking: market_id → (imbalance, timestamp)
        self._prev_imbalance: dict[str, tuple[float, float]] = {}
        self.VELOCITY_BOOST = 0.08     # Confidence boost when imbalance growing fast
        self.VELOCITY_PENALTY = 0.08   # Confidence penalty when imbalance collapsing

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
        # Classify market type for tiered filtering
        market_type = classify_market_type(market.question)

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
        yes_in_range = self.MIN_PRICE <= yes_price <= self.MAX_PRICE
        no_in_range = self.MIN_PRICE <= no_price_est <= self.MAX_PRICE and len(token_ids) >= 2
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
        if total_volume < self.MIN_BOOK_VOLUME:
            return None

        # VPIN: measure informed flow (logged in metadata, gate is configurable)
        vpin = compute_vpin(bid_volume, ask_volume)
        if vpin > self.VPIN_THRESHOLD:
            logger.info(
                "vpin_toxic_skip",
                market_id=market.id[:20],
                vpin=round(vpin, 2),
            )
            return None

        # Compute price_std from order book for Z-score check downstream
        prices = [b.price for b in book.bids[:5]] + [a.price for a in book.asks[:5]]
        price_std = 0.05  # default
        if len(prices) >= 2:
            mean_p = sum(prices) / len(prices)
            variance = sum((p - mean_p) ** 2 for p in prices) / len(prices)
            price_std = max(variance ** 0.5, 0.01)

        imbalance = (bid_volume - ask_volume) / total_volume

        # Tiered imbalance: sports markets need stronger signal (obvious favorites only)
        threshold = (
            self.SPORTS_IMBALANCE_THRESHOLD
            if market_type == "sports"
            else self.IMBALANCE_THRESHOLD
        )
        if abs(imbalance) < threshold:
            return None

        # Evaluate BOTH sides and pick the one with the higher edge.
        # Both sides share the same edge magnitude (abs_imbalance * 0.1),
        # so imbalance direction decides which side is underpriced.
        no_price = market.no_price or (1.0 - yes_price)
        has_no_token = len(token_ids) >= 2

        # Empirically-calibrated edge formula:
        # 1) Base edge from imbalance (nonlinear: bigger imbalance → disproportionately higher edge)
        # 2) Volume depth bonus: thicker books = more reliable signal
        # 3) Time discount: shorter resolution → higher confidence in signal
        abs_imb = abs(imbalance)
        # nonlinear: 0.15→0.52%, 0.25→1.18%, 0.40→2.40%, 0.60→4.39%
        base_edge = abs_imb ** 1.5 * 0.3

        # Volume depth factor: scale 0.8-1.2 based on book thickness
        # 500 vol = 1.0 baseline, 2000+ = 1.2 max, <200 = 0.8 min
        vol_factor = max(0.8, min(1.2, 0.8 + (total_volume - 200) / 4500))
        edge_val = base_edge * vol_factor

        # Time-to-resolution bonus: granular scale for near-resolution markets
        if hours_left <= 6:
            resolution_bonus = 1.6
        elif hours_left <= 12:
            resolution_bonus = 1.4
        elif hours_left <= 24:
            resolution_bonus = 1.25
        elif hours_left <= 48:
            resolution_bonus = 1.15
        elif hours_left <= 72:
            resolution_bonus = 1.05
        else:
            resolution_bonus = 1.0
        edge_val *= resolution_bonus

        if edge_val < self.MIN_EDGE:
            return None

        # Near-certainty detector: markets close to resolution with extreme prices
        # are highly predictable — boost edge and confidence
        near_certainty = False
        if hours_left <= 48 and (yes_price >= 0.80 or (1.0 - yes_price) >= 0.80):
            edge_val *= 1.5  # +50% edge boost
            near_certainty = True
            self.logger.info(
                "near_certainty_boost_applied",
                market_id=market.id[:20],
                hours_left=round(hours_left, 1),
                yes_price=yes_price,
                edge_after_boost=round(edge_val, 4),
            )

        # Mean-reversion filter: skip when short-term momentum aligns with
        # imbalance direction (likely a spike that will revert)
        if self._price_tracker is not None and market.id:
            mom_5m = self._price_tracker.momentum(market.id, 5)
            if mom_5m is not None and abs(mom_5m) > self.MEAN_REVERSION_THRESHOLD:
                # Imbalance > 0 means buy-side pressure; positive momentum = same direction
                if (imbalance > 0 and mom_5m > 0) or (imbalance < 0 and mom_5m < 0):
                    self.logger.info(
                        "value_betting_mean_reversion_skip",
                        market_id=market.id[:20],
                        imbalance=round(imbalance, 3),
                        momentum_5m=round(mom_5m, 4),
                    )
                    return None

        # Determine which sides are in tradeable price range
        yes_ok = self.MIN_PRICE <= yes_price <= self.MAX_PRICE
        no_ok = has_no_token and self.MIN_PRICE <= no_price <= self.MAX_PRICE

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
        if hours_left <= 6:
            confidence += 0.15
        elif hours_left <= 12:
            confidence += 0.12
        elif hours_left <= 24:
            confidence += 0.08
        elif hours_left <= 48:
            confidence += 0.05
        elif hours_left <= 72:
            confidence += 0.04

        # Near-certainty confidence boost
        if near_certainty:
            confidence += 0.15

        # Danger zone filter: buying at high prices (>0.75) means risking
        # $0.75+ to win $0.25- (3:1 against you). Need much higher edge.
        # Based on @LeisenCrypto analysis: 94%+ accuracy needed above 0.80
        if price > 0.80 and edge_val < 0.10:
            return None  # Widow-maker zone: need 10%+ edge
        if price > 0.75 and edge_val < 0.07:
            return None  # High-risk zone: need 7%+ edge

        # Momentum adjustment from shared price tracker
        mom: float | None = None
        if self._price_tracker is not None:
            mom = self._price_tracker.momentum(market.id, 60)
            if mom is not None:
                if pick_yes and mom > 0.02:
                    confidence += 0.05
                elif pick_yes and mom < -0.02:
                    confidence -= 0.05
                elif not pick_yes and mom < -0.02:
                    confidence += 0.05
                elif not pick_yes and mom > 0.02:
                    confidence -= 0.05

        # Order book velocity: compare imbalance change rate
        now_ts = time.monotonic()
        prev = self._prev_imbalance.get(market.id)
        if prev is not None:
            prev_imb, prev_ts = prev
            elapsed_min = (now_ts - prev_ts) / 60.0
            if elapsed_min > 0.1:  # At least 6 seconds between samples
                delta_per_min = (imbalance - prev_imb) / elapsed_min
                if delta_per_min > 0.1:
                    confidence += self.VELOCITY_BOOST
                elif delta_per_min < -0.1:
                    confidence -= self.VELOCITY_PENALTY
        # Store current imbalance for next scan
        self._prev_imbalance[market.id] = (imbalance, now_ts)
        # Evict stale entries (>10 min old)
        stale_cutoff = now_ts - 600.0
        stale_keys = [
            k for k, (_, ts) in self._prev_imbalance.items() if ts < stale_cutoff
        ]
        for k in stale_keys:
            del self._prev_imbalance[k]

        metadata = {
            "category": market.category,
            "hours_to_resolution": hours_left,
            "imbalance": imbalance,
            "bid_volume": bid_volume,
            "ask_volume": ask_volume,
            "near_certainty": near_certainty,
            "resolution_bonus": resolution_bonus,
            "vpin": round(vpin, 3),
            "price_std": round(price_std, 4),
        }
        if mom is not None:
            metadata["momentum_1h"] = mom

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
            metadata=metadata,
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

        # Relative stop-loss: exit if lost 12%+ from entry
        # NOTE: removed absolute floor (current_price < 0.40) — it was triggering
        # false exits on cheap No tokens bought at 0.30-0.40 range
        if avg_price > 0:
            loss_pct = (avg_price - current_price) / avg_price
            if loss_pct >= self.RELATIVE_STOP_LOSS:
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
