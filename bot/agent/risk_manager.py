"""Risk management system with tier-based rules and cascading checks."""

from datetime import datetime

import structlog

from bot.config import CapitalTier, TierConfig, settings
from bot.data.models import Position
from bot.polymarket.types import TradeSignal
from bot.utils.math_utils import (
    current_drawdown,
    position_size_usd,
)

logger = structlog.get_logger()


class RiskCheckResult:
    """Result of a risk check."""

    def __init__(self, passed: bool, reason: str = ""):
        self.passed = passed
        self.reason = reason

    def __bool__(self) -> bool:
        return self.passed

    def __repr__(self) -> str:
        status = "PASS" if self.passed else f"FAIL: {self.reason}"
        return f"<RiskCheck: {status}>"


class RiskManager:
    """Tier-based risk management with cascading checks."""

    def __init__(self):
        self._daily_pnl: float = 0.0
        self._daily_pnl_date: str = ""
        self._peak_equity: float = settings.initial_bankroll
        self._is_paused: bool = False

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    def pause(self) -> None:
        self._is_paused = True
        logger.warning("trading_paused")

    def resume(self) -> None:
        self._is_paused = False
        logger.info("trading_resumed")

    def update_peak_equity(self, equity: float) -> None:
        if equity > self._peak_equity:
            self._peak_equity = equity

    def update_daily_pnl(self, pnl_change: float) -> None:
        today = datetime.utcnow().strftime("%Y-%m-%d")
        if self._daily_pnl_date != today:
            self._daily_pnl = 0.0
            self._daily_pnl_date = today
        self._daily_pnl += pnl_change

    async def evaluate_signal(
        self,
        signal: TradeSignal,
        bankroll: float,
        open_positions: list[Position],
        tier: CapitalTier,
        pending_count: int = 0,
    ) -> tuple[bool, float, str]:
        """Evaluate a trade signal against all risk checks.

        Returns (approved, adjusted_size, reason).
        pending_count: number of pending CLOB orders (not yet filled)
                       that should count toward position limits.
        """
        config = TierConfig.get(tier)

        # Run cascading checks
        checks = [
            self._check_paused(),
            self._check_duplicate_position(signal, open_positions),
            self._check_daily_loss(bankroll, config),
            self._check_drawdown(bankroll, config),
            self._check_max_positions(open_positions, config, pending_count),
            self._check_total_deployed(open_positions, bankroll, config),
            self._check_category_exposure(signal, open_positions, bankroll, config),
            self._check_min_edge(signal, config),
            self._check_min_win_prob(signal, config),
        ]

        for check in checks:
            if not check:
                logger.info(
                    "risk_check_failed",
                    strategy=signal.strategy,
                    market_id=signal.market_id,
                    reason=check.reason,
                )
                return False, 0.0, check.reason

        # Calculate position size
        size = self._calculate_size(signal, bankroll, config)
        if size <= 0:
            return False, 0.0, "Position size too small"

        logger.info(
            "risk_check_passed",
            strategy=signal.strategy,
            market_id=signal.market_id,
            size=size,
            tier=tier.value,
        )
        return True, size, "approved"

    def _check_paused(self) -> RiskCheckResult:
        if self._is_paused:
            return RiskCheckResult(False, "Trading is paused")
        return RiskCheckResult(True)

    def _check_duplicate_position(
        self, signal: TradeSignal, open_positions: list[Position]
    ) -> RiskCheckResult:
        """Reject if we already have an open position on this market."""
        for pos in open_positions:
            if pos.market_id == signal.market_id and pos.is_open:
                return RiskCheckResult(
                    False,
                    f"Duplicate position: already holding {pos.market_id[:16]}...",
                )
        return RiskCheckResult(True)

    def _check_daily_loss(self, bankroll: float, config: dict) -> RiskCheckResult:
        limit = bankroll * config["daily_loss_limit_pct"]
        if self._daily_pnl < -limit:
            return RiskCheckResult(
                False, f"Daily loss limit reached: ${self._daily_pnl:.2f} < -${limit:.2f}"
            )
        return RiskCheckResult(True)

    def _check_drawdown(self, bankroll: float, config: dict) -> RiskCheckResult:
        dd = current_drawdown(bankroll, self._peak_equity)
        if dd > config["max_drawdown_pct"]:
            return RiskCheckResult(
                False, f"Max drawdown exceeded: {dd:.1%} > {config['max_drawdown_pct']:.1%}"
            )
        return RiskCheckResult(True)

    def _check_max_positions(
        self, open_positions: list[Position], config: dict, pending_count: int = 0
    ) -> RiskCheckResult:
        total = len(open_positions) + pending_count
        if total >= config["max_positions"]:
            return RiskCheckResult(
                False,
                f"Max positions reached: {total} >= {config['max_positions']} "
                f"({len(open_positions)} open + {pending_count} pending)",
            )
        return RiskCheckResult(True)

    def _check_total_deployed(
        self,
        open_positions: list[Position],
        bankroll: float,
        config: dict,
    ) -> RiskCheckResult:
        """Reject if total deployed capital exceeds max_deployed_pct of bankroll."""
        deployed = sum(p.cost_basis for p in open_positions if p.is_open)
        max_deployed = bankroll * config["max_deployed_pct"]
        if deployed >= max_deployed:
            return RiskCheckResult(
                False,
                f"Total deployed limit: ${deployed:.2f} >= ${max_deployed:.2f} "
                f"({config['max_deployed_pct']:.0%} of bankroll)",
            )
        return RiskCheckResult(True)

    def _check_category_exposure(
        self,
        signal: TradeSignal,
        open_positions: list[Position],
        bankroll: float,
        config: dict,
    ) -> RiskCheckResult:
        category = signal.metadata.get("category", "")
        if not category:
            return RiskCheckResult(True)

        category_exposure = sum(
            p.cost_basis for p in open_positions if p.category == category and p.is_open
        )
        max_exposure = bankroll * config["max_per_category_pct"]
        if category_exposure >= max_exposure:
            return RiskCheckResult(
                False,
                f"Category exposure limit: {category} "
                f"at ${category_exposure:.2f} >= ${max_exposure:.2f}",
            )
        return RiskCheckResult(True)

    def _check_min_edge(self, signal: TradeSignal, config: dict) -> RiskCheckResult:
        if signal.edge < config["min_edge_pct"]:
            return RiskCheckResult(
                False, f"Edge too low: {signal.edge:.1%} < {config['min_edge_pct']:.1%}"
            )
        return RiskCheckResult(True)

    def _check_min_win_prob(self, signal: TradeSignal, config: dict) -> RiskCheckResult:
        if signal.estimated_prob < config["min_win_prob"]:
            return RiskCheckResult(
                False,
                f"Win prob too low: {signal.estimated_prob:.1%} < {config['min_win_prob']:.1%}",
            )
        return RiskCheckResult(True)

    def _calculate_size(
        self, signal: TradeSignal, bankroll: float, config: dict
    ) -> float:
        """Calculate position size using fractional Kelly with tier constraints.

        Ensures the resulting order meets Polymarket's minimum of 5 shares
        in live mode. If Kelly suggests less, bump up to the minimum.
        """
        from bot.config import settings
        from bot.utils.math_utils import kelly_criterion

        full_kelly = kelly_criterion(signal.estimated_prob, signal.market_price)
        kelly_frac = config["kelly_fraction"] * full_kelly

        size = position_size_usd(
            bankroll=bankroll,
            kelly_frac=kelly_frac,
            max_per_position_pct=config["max_per_position_pct"],
        )

        # Ensure minimum shares for live mode (Polymarket requires >= 5 shares)
        if not settings.is_paper and signal.market_price > 0:
            min_shares = 5.0
            min_usd = min_shares * signal.market_price
            if 0 < size < min_usd:
                max_allowed = bankroll * config["max_per_position_pct"]
                if min_usd <= max_allowed:
                    size = min_usd
                else:
                    size = 0.0  # Can't afford minimum

        return size

    def get_risk_metrics(self, bankroll: float) -> dict:
        """Get current risk metrics for the dashboard."""
        tier = CapitalTier.from_bankroll(bankroll)
        config = TierConfig.get(tier)
        dd = current_drawdown(bankroll, self._peak_equity)

        return {
            "tier": tier.value,
            "bankroll": bankroll,
            "peak_equity": self._peak_equity,
            "current_drawdown_pct": dd,
            "max_drawdown_limit_pct": config["max_drawdown_pct"],
            "daily_pnl": self._daily_pnl,
            "daily_loss_limit_pct": config["daily_loss_limit_pct"],
            "max_positions": config["max_positions"],
            "is_paused": self._is_paused,
        }
