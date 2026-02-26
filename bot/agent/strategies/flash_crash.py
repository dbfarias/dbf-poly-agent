"""Flash crash mean-reversion strategy.

Buys when a market's probability drops significantly within a short time
window, assuming the move is an overreaction. Targets a quick bounce-back
toward the pre-crash price level.
"""

from __future__ import annotations

from datetime import datetime, timezone

import structlog

from bot.polymarket.orderbook_tracker import OrderbookTracker
from bot.polymarket.types import GammaMarket, OrderSide, TradeSignal

from .base import BaseStrategy

logger = structlog.get_logger()


class FlashCrashStrategy(BaseStrategy):
    """Mean-reversion strategy that buys flash-crashed markets."""

    name = "flash_crash"

    # --- Detection params ---
    DROP_THRESHOLD_PCT = 0.30  # 30% drop triggers buy
    DETECTION_WINDOW_SECONDS = 30
    MIN_VOLUME_24H = 5000.0
    MIN_LIQUIDITY = 1000.0
    MAX_PRICE_BEFORE_DROP = 0.85  # Skip if pre-crash price was already cheap

    # --- Exit params ---
    TAKE_PROFIT_PCT = 0.10
    STOP_LOSS_PCT = 0.15
    MAX_HOLD_SECONDS = 300  # 5 min
    MIN_HOLD_SECONDS = 30

    _MUTABLE_PARAMS = {
        "DROP_THRESHOLD_PCT": {"type": float, "min": 0.10, "max": 0.80},
        "DETECTION_WINDOW_SECONDS": {"type": int, "min": 5, "max": 120},
        "MIN_VOLUME_24H": {"type": float, "min": 0.0, "max": 100_000.0},
        "MIN_LIQUIDITY": {"type": float, "min": 0.0, "max": 50_000.0},
        "MAX_PRICE_BEFORE_DROP": {"type": float, "min": 0.20, "max": 0.99},
        "TAKE_PROFIT_PCT": {"type": float, "min": 0.01, "max": 0.50},
        "STOP_LOSS_PCT": {"type": float, "min": 0.01, "max": 0.50},
        "MAX_HOLD_SECONDS": {"type": int, "min": 30, "max": 1800},
        "MIN_HOLD_SECONDS": {"type": int, "min": 0, "max": 300},
    }

    def __init__(self, *args, orderbook_tracker: OrderbookTracker | None = None, **kwargs):
        super().__init__(*args, **kwargs)
        self._tracker = orderbook_tracker

    async def scan(self, markets: list[GammaMarket]) -> list[TradeSignal]:
        """Scan markets for flash crash opportunities."""
        if self._tracker is None:
            return []

        signals: list[TradeSignal] = []
        for market in markets:
            signal = self._evaluate_market(market)
            if signal is not None:
                signals.append(signal)

        signals.sort(key=lambda s: s.edge, reverse=True)

        self.logger.info(
            "flash_crash_scan_complete",
            markets_checked=len(markets),
            signals_found=len(signals),
        )
        return signals

    def _evaluate_market(self, market: GammaMarket) -> TradeSignal | None:
        """Evaluate a single market for flash crash signal."""
        if not self._is_eligible(market):
            return None

        token_ids = market.token_ids
        if not token_ids:
            return None

        for idx, token_id in enumerate(token_ids):
            signal = self._check_token(market, token_id, idx)
            if signal is not None:
                return signal
        return None

    def _is_eligible(self, market: GammaMarket) -> bool:
        """Check basic market eligibility filters."""
        if not market.active or not market.accepting_orders:
            return False
        if market.volume < self.MIN_VOLUME_24H:
            return False
        if market.liquidity < self.MIN_LIQUIDITY:
            return False
        return True

    def _check_token(
        self, market: GammaMarket, token_id: str, token_idx: int
    ) -> TradeSignal | None:
        """Check a single token for flash crash and build signal."""
        if self._tracker is None:
            return None

        crashed, drop_magnitude = self._tracker.detect_flash_crash(
            token_id,
            drop_pct=self.DROP_THRESHOLD_PCT,
            window_seconds=self.DETECTION_WINDOW_SECONDS,
        )
        if not crashed:
            return None

        history = self._tracker.mid_price_history(
            token_id, self.DETECTION_WINDOW_SECONDS
        )
        if len(history) < 2:
            return None

        pre_crash_price = max(p.mid_price for p in history)
        if pre_crash_price > self.MAX_PRICE_BEFORE_DROP:
            return None

        current_mid = self._tracker.get_mid_price(token_id)
        if current_mid is None or current_mid <= 0:
            return None

        edge = (pre_crash_price - current_mid) / current_mid
        confidence = self._compute_confidence(drop_magnitude)
        # Resolve outcome from market's outcomes metadata instead of
        # assuming index 0 = "Yes", 1 = "No" (may differ for multi-outcome).
        outcomes = market.outcomes
        outcome = outcomes[token_idx] if token_idx < len(outcomes) else "Yes"

        self.logger.info(
            "flash_crash_detected",
            market_id=market.id,
            token_id=token_id,
            pre_crash=f"{pre_crash_price:.3f}",
            current=f"{current_mid:.3f}",
            drop=f"{drop_magnitude:.1%}",
            edge=f"{edge:.1%}",
        )

        return TradeSignal(
            strategy=self.name,
            market_id=market.id,
            token_id=token_id,
            question=market.question,
            side=OrderSide.BUY,
            outcome=outcome,
            estimated_prob=pre_crash_price,
            market_price=current_mid,
            edge=edge,
            size_usd=0.0,
            confidence=confidence,
            reasoning=(
                f"Flash crash: {outcome} dropped {drop_magnitude:.0%} "
                f"in {self.DETECTION_WINDOW_SECONDS}s. "
                f"Pre-crash: {pre_crash_price:.3f}, "
                f"current: {current_mid:.3f}, "
                f"mean-reversion edge: {edge:.1%}"
            ),
            metadata={
                "pre_crash_price": pre_crash_price,
                "drop_magnitude": drop_magnitude,
                "detection_window": self.DETECTION_WINDOW_SECONDS,
            },
        )

    @staticmethod
    def _compute_confidence(drop_magnitude: float) -> float:
        """Map drop magnitude to confidence (bigger drop = higher confidence).

        Precondition: drop_magnitude >= 0.30 (enforced by detect_flash_crash).
        Scale: 30% drop → 0.50, 50% → 0.70, 80%+ → 0.90, capped at 0.95.
        """
        base = 0.50
        bonus = min(0.45, (drop_magnitude - 0.30) * 1.0)
        return min(0.95, base + bonus)

    async def should_exit(
        self, market_id: str, current_price: float, **kwargs
    ) -> str | bool:
        """Exit on stop-loss, take-profit, or time expiry."""
        avg_price = kwargs.get("avg_price", 0.0)
        created_at = kwargs.get("created_at")

        if avg_price <= 0:
            return False

        profit_pct = (current_price - avg_price) / avg_price

        # Respect MIN_HOLD_SECONDS before any exit
        if created_at is not None:
            held_seconds = self._held_seconds(created_at)
            if held_seconds < self.MIN_HOLD_SECONDS:
                return False
        else:
            held_seconds = 0.0

        # Stop-loss
        if profit_pct <= -self.STOP_LOSS_PCT:
            self.logger.warning(
                "flash_crash_exit_stop_loss",
                market_id=market_id,
                loss_pct=f"{profit_pct:.1%}",
            )
            return "stop_loss"

        # Take-profit
        if profit_pct >= self.TAKE_PROFIT_PCT:
            self.logger.info(
                "flash_crash_exit_take_profit",
                market_id=market_id,
                profit_pct=f"{profit_pct:.1%}",
            )
            return "take_profit"

        # Time expiry
        if created_at is not None and held_seconds >= self.MAX_HOLD_SECONDS:
            self.logger.info(
                "flash_crash_exit_time_expiry",
                market_id=market_id,
                held_seconds=f"{held_seconds:.0f}",
            )
            return "time_expiry"

        return False

    @staticmethod
    def _held_seconds(created_at: datetime) -> float:
        """Compute how many seconds a position has been held."""
        now = datetime.now(timezone.utc)
        if created_at.tzinfo is None:
            created_at = created_at.replace(tzinfo=timezone.utc)
        return (now - created_at).total_seconds()
