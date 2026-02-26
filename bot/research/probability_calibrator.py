"""Calibrated probability model — adjusts estimated probabilities using historical accuracy."""

import structlog

from bot.data.models import Trade

logger = structlog.get_logger()

# Minimum trades per bin before calibration kicks in
MIN_TRADES_PER_BIN = 5

# Calibration bins (lower bound, upper bound, label)
_BINS: tuple[tuple[float, float, str], ...] = (
    (0.50, 0.60, "0.50-0.60"),
    (0.60, 0.70, "0.60-0.70"),
    (0.70, 0.80, "0.70-0.80"),
    (0.80, 0.90, "0.80-0.90"),
    (0.90, 1.00, "0.90-1.00"),
)


def _bin_label(prob: float) -> str:
    """Return the bin label for a probability value."""
    for lo, hi, label in _BINS:
        if lo <= prob < hi:
            return label
    # Edge case: prob == 1.0
    if prob >= 1.0:
        return "0.90-1.00"
    # Below 0.50 — no bin
    return ""


class ProbabilityCalibrator:
    """Simple binned probability calibrator.

    Compares historical estimated_prob vs actual outcomes to compute
    per-bin calibration factors. No external dependencies (no sklearn).
    """

    def __init__(self) -> None:
        # bin_label -> calibration_factor
        self._calibration_factors: dict[str, float] = {}
        self._trained = False

    @property
    def is_trained(self) -> bool:
        return self._trained

    async def train(self, trades: list[Trade]) -> None:
        """Train calibration from resolved trades.

        Only uses trades with exit_reason set and non-zero pnl
        (actually resolved, not still open).
        """
        # Filter to resolved trades with meaningful outcomes
        resolved = [
            t for t in trades
            if t.exit_reason and t.pnl != 0
        ]

        if not resolved:
            self._trained = False
            return

        # Group by bin
        bins: dict[str, list[Trade]] = {label: [] for _, _, label in _BINS}
        for trade in resolved:
            label = _bin_label(trade.estimated_prob)
            if label and label in bins:
                bins[label].append(trade)

        # Compute calibration factor per bin
        factors: dict[str, float] = {}
        for label, bin_trades in bins.items():
            if len(bin_trades) < MIN_TRADES_PER_BIN:
                factors[label] = 1.0
                continue

            actual_wins = sum(1 for t in bin_trades if t.pnl > 0)
            actual_win_rate = actual_wins / len(bin_trades)
            avg_estimated = sum(
                t.estimated_prob for t in bin_trades
            ) / len(bin_trades)

            if avg_estimated > 0:
                factors[label] = actual_win_rate / avg_estimated
            else:
                factors[label] = 1.0

        self._calibration_factors = factors
        self._trained = True

        logger.info(
            "probability_calibrator_trained",
            total_trades=len(resolved),
            bins={k: len(v) for k, v in bins.items()},
            factors={k: round(v, 3) for k, v in factors.items()},
        )

    def calibrate(self, estimated_prob: float) -> float:
        """Return calibrated probability, clamped to [0.01, 0.99].

        If not trained or bin has insufficient data, returns the
        original probability unchanged.
        """
        if not self._trained:
            return estimated_prob

        label = _bin_label(estimated_prob)
        if not label:
            return estimated_prob

        factor = self._calibration_factors.get(label, 1.0)
        calibrated = estimated_prob * factor
        return max(0.01, min(0.99, calibrated))

    def brier_score(self, trades: list[Trade]) -> float:
        """Compute Brier score: mean((estimated_prob - outcome)^2).

        outcome = 1 if pnl > 0, 0 otherwise.
        Lower is better (0 = perfect, 0.25 = random at 50/50).
        """
        resolved = [
            t for t in trades
            if t.exit_reason and t.pnl != 0
        ]
        if not resolved:
            return 0.0

        total = 0.0
        for trade in resolved:
            outcome = 1.0 if trade.pnl > 0 else 0.0
            total += (trade.estimated_prob - outcome) ** 2

        return total / len(resolved)

    def per_strategy_brier(self, trades: list[Trade]) -> dict[str, float]:
        """Compute Brier score per strategy.

        Returns {strategy_name: brier_score}.
        """
        resolved = [
            t for t in trades
            if t.exit_reason and t.pnl != 0
        ]

        # Group by strategy
        by_strategy: dict[str, list[Trade]] = {}
        for trade in resolved:
            by_strategy.setdefault(trade.strategy, []).append(trade)

        scores: dict[str, float] = {}
        for strategy, strat_trades in by_strategy.items():
            total = 0.0
            for trade in strat_trades:
                outcome = 1.0 if trade.pnl > 0 else 0.0
                total += (trade.estimated_prob - outcome) ** 2
            scores[strategy] = total / len(strat_trades)

        return scores
