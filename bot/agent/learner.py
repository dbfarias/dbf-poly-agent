"""Adaptive learning system that adjusts strategy parameters from trade history.

Queries historical trades to compute per-strategy and per-category performance
stats, then returns multipliers that tighten or loosen risk parameters.

The learning loop:
  SCAN -> VALIDATE -> TRADE -> TRACK -> LEARN -> (adjust params) -> SCAN
"""

from datetime import datetime, timedelta, timezone

import structlog

from bot.data.database import async_session
from bot.data.models import StrategyMetric, Trade
from bot.data.repositories import StrategyMetricRepository, TradeRepository

logger = structlog.get_logger()

# Minimum trades before adjustments kick in
MIN_TRADES_FOR_ADJUSTMENT = 10

# Multiplier clamps — never removes all guards or makes them impossibly strict
MULTIPLIER_MIN = 0.5
MULTIPLIER_MAX = 2.0

# Auto-pause thresholds
PAUSE_LOOKBACK = 10  # Last N trades
PAUSE_WIN_RATE = 0.30  # Below this win rate triggers pause consideration
PAUSE_MIN_LOSS = -1.0  # Must also be losing money to pause
PAUSE_COOLDOWN_HOURS = 24


class StrategyStats:
    """Immutable container for per-strategy + per-category stats."""

    def __init__(
        self,
        strategy: str,
        category: str,
        total_trades: int,
        winning_trades: int,
        total_pnl: float,
        avg_edge: float,
        avg_estimated_prob: float,
        actual_win_rate: float,
    ):
        self.strategy = strategy
        self.category = category
        self.total_trades = total_trades
        self.winning_trades = winning_trades
        self.total_pnl = total_pnl
        self.avg_edge = avg_edge
        self.avg_estimated_prob = avg_estimated_prob
        self.actual_win_rate = actual_win_rate


class LearnerAdjustments:
    """Immutable container for adjustments computed by the learner."""

    def __init__(
        self,
        edge_multipliers: dict[tuple[str, str], float],
        category_confidences: dict[str, float],
        paused_strategies: set[str],
        calibration: dict[str, float],
        urgency_multiplier: float = 1.0,
        daily_progress: float = 0.0,
    ):
        self.edge_multipliers = edge_multipliers
        self.category_confidences = category_confidences
        self.paused_strategies = paused_strategies
        self.calibration = calibration
        self.urgency_multiplier = urgency_multiplier
        self.daily_progress = daily_progress


class PerformanceLearner:
    """Learns from trade history to adjust strategy parameters.

    Tracks daily target progress and computes an urgency multiplier
    that feeds back into edge requirements and strategy aggressiveness.
    """

    # Minimum seconds between full recomputation (avoid hammering DB every 30s cycle)
    RECOMPUTE_INTERVAL = 300  # 5 minutes

    def __init__(self):
        self._stats: dict[tuple[str, str], StrategyStats] = {}
        self._paused_strategies: dict[str, datetime] = {}
        self._last_computed: datetime | None = None
        self._last_adjustments: LearnerAdjustments | None = None
        self._newly_paused: list[tuple[str, float, float]] = []

        # Daily target context (set by engine each cycle)
        self._daily_pnl: float = 0.0
        self._daily_equity: float = 0.0
        self._daily_target_pct: float = 0.01

    def set_daily_context(
        self,
        realized_pnl: float,
        equity: float,
        target_pct: float,
    ) -> None:
        """Set daily trading context for urgency calculation.

        Called by the engine before compute_stats() each cycle.
        """
        self._daily_pnl = realized_pnl
        self._daily_equity = equity
        self._daily_target_pct = target_pct

    async def compute_stats(self) -> LearnerAdjustments:
        """Compute per-strategy, per-category performance stats.

        Returns LearnerAdjustments with edge multipliers, category confidences,
        and strategy pause states. Skips recomputation if called within
        RECOMPUTE_INTERVAL seconds of the last computation.
        """
        self._newly_paused = []

        # Skip if recently computed (avoid hammering DB every 30s cycle)
        if (
            self._last_computed is not None
            and self._last_adjustments is not None
            and (datetime.now(timezone.utc) - self._last_computed).total_seconds()
            < self.RECOMPUTE_INTERVAL
        ):
            return self._last_adjustments

        async with async_session() as session:
            repo = TradeRepository(session)
            trades = await repo.get_recent(limit=500)

        # Filter to completed trades from last 30 days
        cutoff = datetime.now(timezone.utc) - timedelta(days=30)
        recent = []
        for t in trades:
            if t.status not in ("filled", "completed"):
                continue
            created = t.created_at
            if created is not None and created.tzinfo is None:
                created = created.replace(tzinfo=timezone.utc)
            if created is not None and created >= cutoff:
                recent.append(t)

        # Group by (strategy, category)
        groups: dict[tuple[str, str], list[Trade]] = {}
        for trade in recent:
            key = (trade.strategy, trade.category)
            groups.setdefault(key, []).append(trade)

        # Compute stats per group
        stats: dict[tuple[str, str], StrategyStats] = {}
        for (strategy, category), group_trades in groups.items():
            total = len(group_trades)
            wins = sum(1 for t in group_trades if t.pnl > 0)
            total_pnl = sum(t.pnl for t in group_trades)
            avg_edge = (
                sum(t.edge for t in group_trades) / total if total > 0 else 0.0
            )
            avg_prob = (
                sum(t.estimated_prob for t in group_trades) / total
                if total > 0
                else 0.0
            )
            win_rate = wins / total if total > 0 else 0.0

            stats[(strategy, category)] = StrategyStats(
                strategy=strategy,
                category=category,
                total_trades=total,
                winning_trades=wins,
                total_pnl=total_pnl,
                avg_edge=avg_edge,
                avg_estimated_prob=avg_prob,
                actual_win_rate=win_rate,
            )

        self._stats = stats

        # Compute edge multipliers
        edge_multipliers = {
            key: self._compute_edge_multiplier(s)
            for key, s in stats.items()
        }

        # Compute category confidences
        category_confidences = self._compute_category_confidences(stats)

        # Check for strategies to pause
        paused = set()
        for strategy in {k[0] for k in stats}:
            if self.should_pause_strategy(strategy, recent):
                paused.add(strategy)

        # Confidence calibration
        calibration = self._compute_calibration(recent)

        # Update strategy metrics in DB
        await self._update_strategy_metrics(stats)

        # Compute daily target urgency
        urgency = self._compute_urgency()
        daily_progress = self._compute_daily_progress()

        self._last_computed = datetime.now(timezone.utc)

        adjustments: LearnerAdjustments = LearnerAdjustments(
            edge_multipliers=edge_multipliers,
            category_confidences=category_confidences,
            paused_strategies=paused,
            calibration=calibration,
            urgency_multiplier=urgency,
            daily_progress=daily_progress,
        )

        logger.info(
            "learner_stats_computed",
            groups=len(stats),
            paused=list(paused),
            total_recent_trades=len(recent),
            urgency=round(urgency, 2),
            daily_progress=round(daily_progress, 2),
        )

        self._last_adjustments = adjustments
        return adjustments

    def get_edge_multiplier(
        self, strategy: str, category: str
    ) -> float:
        """Return edge multiplier based on historical performance.

        - Strategy winning consistently (>60% win rate): 0.8 (allow lower edge)
        - Strategy performing normally (40-60%): 1.0 (default)
        - Strategy losing (<40% win rate): 1.5 (require higher edge)
        - Category with 0 historical trades: 1.2 (cautious on unknown)
        """
        key = (strategy, category)
        stats = self._stats.get(key)

        if stats is None:
            return 1.2  # Cautious on unknown

        return self._compute_edge_multiplier(stats)

    def get_category_confidence(self, category: str) -> float:
        """Return confidence modifier for a category (0.5 to 1.5).

        Based on historical win rate across all strategies.
        - >70% win rate: 1.2 (boost)
        - 50-70%: 1.0 (neutral)
        - <50%: 0.7 (penalize)
        - No data: 0.8 (cautious)
        """
        # Aggregate across all strategies for this category
        category_trades = [
            s for (_, cat), s in self._stats.items()
            if cat == category
        ]

        if not category_trades:
            return 0.8

        total = sum(s.total_trades for s in category_trades)
        if total < MIN_TRADES_FOR_ADJUSTMENT:
            return 0.8

        wins = sum(s.winning_trades for s in category_trades)
        win_rate = wins / total if total > 0 else 0.0

        if win_rate > 0.70:
            return 1.2
        elif win_rate >= 0.50:
            return 1.0
        else:
            return 0.7

    def should_pause_strategy(
        self, strategy: str, recent_trades: list[Trade] | None = None
    ) -> bool:
        """Auto-pause a strategy if recent performance is terrible.

        Pause if: last 10 trades have <30% win rate AND total_pnl < -$1.
        Resume after 24h cooldown.
        """
        # Check cooldown — if paused, check if cooldown expired
        if strategy in self._paused_strategies:
            paused_at = self._paused_strategies[strategy]
            elapsed = (datetime.now(timezone.utc) - paused_at).total_seconds() / 3600
            if elapsed >= PAUSE_COOLDOWN_HOURS:
                del self._paused_strategies[strategy]
                logger.info(
                    "strategy_pause_cooldown_expired",
                    strategy=strategy,
                )
                return False
            return True

        # Get recent trades for this strategy
        if recent_trades is None:
            return False

        strategy_trades = [
            t for t in recent_trades if t.strategy == strategy
        ]

        # Need at least PAUSE_LOOKBACK trades to evaluate
        last_n = strategy_trades[:PAUSE_LOOKBACK]
        if len(last_n) < PAUSE_LOOKBACK:
            return False

        wins = sum(1 for t in last_n if t.pnl > 0)
        win_rate = wins / len(last_n)
        total_pnl = sum(t.pnl for t in last_n)

        if win_rate < PAUSE_WIN_RATE and total_pnl < PAUSE_MIN_LOSS:
            self._paused_strategies[strategy] = datetime.now(timezone.utc)
            self._newly_paused.append(
                (strategy, win_rate, total_pnl)
            )
            logger.warning(
                "strategy_auto_paused",
                strategy=strategy,
                win_rate=win_rate,
                total_pnl=total_pnl,
                lookback=PAUSE_LOOKBACK,
            )
            return True

        return False

    def _compute_daily_progress(self) -> float:
        """Compute progress toward daily target (0.0 = none, 1.0 = hit)."""
        target_usd = self._daily_equity * self._daily_target_pct
        if target_usd <= 0:
            return 0.0
        return self._daily_pnl / target_usd

    def _compute_urgency(self) -> float:
        """Compute urgency multiplier based on daily target progress.

        The urgency multiplier adjusts edge requirements:
        - urgency > 1.0 = behind target → engine DIVIDES edge_multiplier
          by urgency, lowering the bar → more trades get through
        - urgency < 1.0 = ahead of target → engine DIVIDES edge_multiplier
          by urgency, raising the bar → fewer trades, protect gains
        - urgency = 1.0 = on pace → no change

        Returns a float clamped to [0.7, 1.5].
        """
        target_usd = self._daily_equity * self._daily_target_pct
        if target_usd <= 0:
            return 1.0

        progress = self._daily_pnl / target_usd

        # Time factor: how far through the UTC day are we?
        now = datetime.now(timezone.utc)
        hours_elapsed = now.hour + now.minute / 60.0
        day_fraction = max(hours_elapsed / 24.0, 0.01)

        if progress >= 1.0:
            # Hit or exceeded target — be conservative, protect gains
            return 0.7
        elif progress >= day_fraction:
            # On pace or ahead of pace — normal trading
            return 1.0
        elif progress >= 0:
            # Behind pace but positive — slightly more aggressive
            # Scale from 1.0 to 1.3 based on how far behind
            behind_ratio = 1.0 - (progress / day_fraction)
            return min(1.3, 1.0 + behind_ratio * 0.3)
        else:
            # Negative PnL — most aggressive (but capped for safety)
            return 1.5

    def _compute_edge_multiplier(self, stats: StrategyStats) -> float:
        """Compute edge multiplier from stats, clamped to safe range."""
        if stats.total_trades < MIN_TRADES_FOR_ADJUSTMENT:
            return 1.2  # Cautious until enough data

        win_rate = stats.actual_win_rate

        if win_rate > 0.60:
            multiplier = 0.8
        elif win_rate >= 0.40:
            multiplier = 1.0
        else:
            multiplier = 1.5

        return max(MULTIPLIER_MIN, min(MULTIPLIER_MAX, multiplier))

    def _compute_category_confidences(
        self, stats: dict[tuple[str, str], StrategyStats]
    ) -> dict[str, float]:
        """Compute confidence multiplier per category across all strategies."""
        # Group by category
        by_category: dict[str, list[StrategyStats]] = {}
        for (_, category), s in stats.items():
            by_category.setdefault(category, []).append(s)

        result: dict[str, float] = {}
        for category, cat_stats in by_category.items():
            total = sum(s.total_trades for s in cat_stats)
            if total < MIN_TRADES_FOR_ADJUSTMENT:
                result[category] = 0.8
                continue

            wins = sum(s.winning_trades for s in cat_stats)
            win_rate = wins / total

            if win_rate > 0.70:
                result[category] = 1.2
            elif win_rate >= 0.50:
                result[category] = 1.0
            else:
                result[category] = 0.7

        return result

    def _compute_calibration(self, trades: list[Trade]) -> dict[str, float]:
        """Compute confidence calibration per probability bucket.

        Groups trades by confidence bucket and compares estimated prob
        vs actual win rate. Returns {bucket_label: calibration_ratio}.
        """
        buckets: dict[str, list[Trade]] = {
            "80-85": [],
            "85-90": [],
            "90-95": [],
            "95-99": [],
        }

        for trade in trades:
            # Skip open trades (pnl=0, no exit) — they haven't resolved yet
            # and would distort calibration by appearing as losses
            if trade.pnl == 0 and not trade.exit_reason:
                continue
            prob = trade.estimated_prob
            if 0.80 <= prob < 0.85:
                buckets["80-85"].append(trade)
            elif 0.85 <= prob < 0.90:
                buckets["85-90"].append(trade)
            elif 0.90 <= prob < 0.95:
                buckets["90-95"].append(trade)
            elif 0.95 <= prob <= 0.99:
                buckets["95-99"].append(trade)

        calibration: dict[str, float] = {}
        for label, bucket_trades in buckets.items():
            if len(bucket_trades) < 5:
                calibration[label] = 1.0
                continue
            actual_wins = sum(1 for t in bucket_trades if t.pnl > 0)
            actual_rate = actual_wins / len(bucket_trades)
            avg_estimated = sum(t.estimated_prob for t in bucket_trades) / len(
                bucket_trades
            )
            # Ratio: if estimated 95% but only winning 60%, ratio = 0.63
            calibration[label] = (
                actual_rate / avg_estimated if avg_estimated > 0 else 1.0
            )

        return calibration

    async def _update_strategy_metrics(
        self, stats: dict[tuple[str, str], StrategyStats]
    ) -> None:
        """Update StrategyMetric records in DB for dashboard display."""
        # Aggregate per strategy (across categories)
        by_strategy: dict[str, list[StrategyStats]] = {}
        for (strategy, _), s in stats.items():
            by_strategy.setdefault(strategy, []).append(s)

        try:
            async with async_session() as session:
                repo = StrategyMetricRepository(session)
                for strategy, strat_stats in by_strategy.items():
                    total = sum(s.total_trades for s in strat_stats)
                    wins = sum(s.winning_trades for s in strat_stats)
                    losses = total - wins
                    total_pnl = sum(s.total_pnl for s in strat_stats)
                    avg_edge = (
                        sum(s.avg_edge * s.total_trades for s in strat_stats) / total
                        if total > 0
                        else 0.0
                    )

                    metric = StrategyMetric(
                        strategy=strategy,
                        total_trades=total,
                        winning_trades=wins,
                        losing_trades=losses,
                        win_rate=wins / total if total > 0 else 0.0,
                        total_pnl=total_pnl,
                        avg_edge=avg_edge,
                    )
                    await repo.upsert(metric)
        except Exception as e:
            logger.error("strategy_metrics_update_failed", error=str(e))
