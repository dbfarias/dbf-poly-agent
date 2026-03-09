"""Risk management system with tier-based rules and cascading checks."""

import structlog

from bot.agent.market_analyzer import normalize_category
from bot.config import CapitalTier, TierConfig, settings, trading_day
from bot.data.models import Position
from bot.polymarket.types import TradeSignal
from bot.utils.math_utils import (
    current_drawdown,
    position_size_usd,
)
from bot.utils.risk_metrics import mispricing_zscore

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

    # Configurable risk thresholds (exposed via admin API)
    VAR_LIMIT = -0.05        # -5% daily VaR limit
    ZSCORE_THRESHOLD = 1.5   # Min |Z-score| for trade approval

    def __init__(self, returns_tracker=None):
        self._daily_pnl: float = 0.0
        self._daily_pnl_date: str = ""
        self._peak_equity: float = settings.initial_bankroll
        self._day_start_equity: float = settings.initial_bankroll
        self._is_paused: bool = False
        self._pnl_dirty: bool = False
        self._returns_tracker = returns_tracker
        self.var_limit: float = self.VAR_LIMIT
        self.zscore_threshold: float = self.ZSCORE_THRESHOLD

    @property
    def is_paused(self) -> bool:
        return self._is_paused

    def set_day_start_equity(self, equity: float) -> None:
        """Set start-of-day equity for accurate daily PnL calculation."""
        self._day_start_equity = equity

    def reset_daily_state(self, equity: float) -> None:
        """Reset daily PnL counters and peak equity to current equity."""
        self._daily_pnl = 0.0
        self._day_start_equity = equity
        self._peak_equity = equity
        logger.info("risk_manager_state_reset", equity=equity)

    def pause(self) -> None:
        self._is_paused = True
        logger.warning("trading_paused")

    def resume(self) -> None:
        self._is_paused = False
        logger.info("trading_resumed")

    def update_peak_equity(self, equity: float) -> None:
        """Update peak equity tracker.

        Also resets peak at daily boundary so drawdown doesn't carry
        forward from stale/inflated peaks (e.g., ghost positions).
        """
        today = trading_day()
        if self._daily_pnl_date != today:
            # New day — reset peak to current equity
            self._peak_equity = equity
        elif equity > self._peak_equity:
            self._peak_equity = equity

    def update_daily_pnl(self, pnl_change: float) -> None:
        today = trading_day()
        if self._daily_pnl_date != today:
            self._daily_pnl = 0.0
            self._daily_pnl_date = today
        self._daily_pnl += pnl_change
        self._pnl_dirty = True

    async def persist_daily_pnl(self) -> None:
        """Save daily PnL to DB (called by engine after trades)."""
        if not self._pnl_dirty:
            return
        try:
            from bot.data.settings_store import StateStore

            await StateStore.save_daily_pnl(self._daily_pnl, self._daily_pnl_date)
            self._pnl_dirty = False
        except Exception as e:
            logger.error("persist_daily_pnl_failed", error=str(e))

    async def restore_daily_pnl(self) -> None:
        """Restore daily PnL from DB on startup."""
        try:
            from bot.data.settings_store import StateStore

            pnl, date_str = await StateStore.load_daily_pnl()
            today = trading_day()
            if date_str == today:
                self._daily_pnl = pnl
                self._daily_pnl_date = date_str
                logger.info(
                    "daily_pnl_restored",
                    daily_pnl=pnl,
                    date=date_str,
                )
        except Exception as e:
            logger.error("restore_daily_pnl_failed", error=str(e))

    async def evaluate_signal(
        self,
        signal: TradeSignal,
        bankroll: float,
        open_positions: list[Position],
        tier: CapitalTier,
        pending_count: int = 0,
        edge_multiplier: float = 1.0,
        urgency: float = 1.0,
        calibration: dict | None = None,
    ) -> tuple[bool, float, str]:
        """Evaluate a trade signal against all risk checks.

        Returns (approved, adjusted_size, reason).
        pending_count: number of pending CLOB orders (not yet filled)
                       that should count toward position limits.
        edge_multiplier: from learner — adjusts min_edge threshold.
                         >1.0 = stricter (losing strategy), <1.0 = relaxed (winning).
        """
        config = TierConfig.get(tier)

        # Run cascading checks
        checks = [
            self._check_paused(),
            self._check_duplicate_position(signal, open_positions),
            self._check_daily_loss(bankroll, config),
            self._check_drawdown(bankroll, config),
            self._check_daily_var(bankroll),
            self._check_max_positions(open_positions, config, pending_count),
            self._check_total_deployed(open_positions, bankroll, config, urgency),
            self._check_category_exposure(signal, open_positions, bankroll, config),
            self._check_min_edge(signal, config, edge_multiplier),
            self._check_min_win_prob(signal, config),
            self._check_zscore(signal),
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

        # Calculate position size (capped to available capital)
        deployed = sum(p.cost_basis for p in open_positions if p.is_open)
        available = bankroll - deployed
        size = self._calculate_size(
            signal, bankroll, config,
            available_capital=available, calibration=calibration,
        )
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
        # Use equity-based PnL (not accumulated trade PnL which can be inflated)
        daily_pnl = bankroll - self._day_start_equity
        limit = self._day_start_equity * config["daily_loss_limit_pct"]
        if daily_pnl < -limit:
            return RiskCheckResult(
                False, f"Daily loss limit reached: ${daily_pnl:.2f} < -${limit:.2f}"
            )
        return RiskCheckResult(True)

    def _check_drawdown(self, bankroll: float, config: dict) -> RiskCheckResult:
        dd = current_drawdown(bankroll, self._peak_equity)
        if dd > config["max_drawdown_pct"]:
            return RiskCheckResult(
                False, f"Max drawdown exceeded: {dd:.1%} > {config['max_drawdown_pct']:.1%}"
            )
        return RiskCheckResult(True)

    # Polymarket CLOB minimum for selling; positions below this are "stuck"
    MIN_SELLABLE_SHARES = 5.0

    def _check_max_positions(
        self, open_positions: list[Position], config: dict, pending_count: int = 0
    ) -> RiskCheckResult:
        # Don't count positions too small to sell — they're stuck and
        # shouldn't block new trades (they'll resolve on-chain eventually)
        sellable = [
            p for p in open_positions
            if p.size >= self.MIN_SELLABLE_SHARES or settings.is_paper
        ]
        total = len(sellable) + pending_count
        if total >= config["max_positions"]:
            return RiskCheckResult(
                False,
                f"Max positions reached: {total} >= {config['max_positions']} "
                f"({len(sellable)} sellable + {pending_count} pending"
                f", {len(open_positions) - len(sellable)} stuck)",
            )
        return RiskCheckResult(True)

    def _check_total_deployed(
        self,
        open_positions: list[Position],
        bankroll: float,
        config: dict,
        urgency: float = 1.0,
    ) -> RiskCheckResult:
        """Reject if too much capital is already deployed.

        Enforces max_deployed_pct from tier config (default 60%).
        When urgency > 1.0 (behind daily target), scales up to 95% max.
        Keeps a cash reserve so the bot can react to better opportunities.
        """
        deployed = sum(p.cost_basis for p in open_positions if p.is_open)
        available = bankroll - deployed
        base_pct = config.get("max_deployed_pct", 0.60)
        # Scale up when behind daily target (urgency > 1.0)
        if urgency > 1.0:
            max_deployed_pct = min(0.95, base_pct + (urgency - 1.0) * 0.05)
        else:
            max_deployed_pct = base_pct
        max_deployed = bankroll * max_deployed_pct

        if deployed >= max_deployed:
            return RiskCheckResult(
                False,
                f"Max deployed capital: ${deployed:.2f} >= "
                f"${max_deployed:.2f} ({max_deployed_pct:.0%} of ${bankroll:.2f})",
            )
        if available < 1.0:
            return RiskCheckResult(
                False,
                f"Insufficient capital: ${available:.2f} available "
                f"(${deployed:.2f} deployed of ${bankroll:.2f})",
            )
        return RiskCheckResult(True)

    def _check_category_exposure(
        self,
        signal: TradeSignal,
        open_positions: list[Position],
        bankroll: float,
        config: dict,
    ) -> RiskCheckResult:
        raw_category = signal.metadata.get("category", "")
        if not raw_category:
            raw_category = "other"

        # Use normalized categories so "Republican Primary" and "Politics" group together
        category = normalize_category(raw_category)

        category_exposure = sum(
            p.cost_basis for p in open_positions
            if normalize_category(p.category) == category and p.is_open
        )
        max_exposure = bankroll * config["max_per_category_pct"]
        if category_exposure >= max_exposure:
            return RiskCheckResult(
                False,
                f"Category exposure limit: {category} "
                f"at ${category_exposure:.2f} >= ${max_exposure:.2f}",
            )
        return RiskCheckResult(True)

    def _check_min_edge(
        self, signal: TradeSignal, config: dict, edge_multiplier: float = 1.0
    ) -> RiskCheckResult:
        adjusted_min = config["min_edge_pct"] * edge_multiplier

        # Time-adjusted edge: near-resolution markets need less edge
        # because there's less uncertainty and the capital is tied up briefly
        hours = signal.metadata.get("hours_to_resolution")
        if hours is not None:
            if hours <= 12:
                adjusted_min *= 0.3   # 12h: ~0.6% edge OK
            elif hours <= 24:
                adjusted_min *= 0.4   # 24h: ~0.8% edge OK
            elif hours <= 48:
                adjusted_min *= 0.5   # 48h: ~1.0% edge OK
            elif hours <= 96:
                adjusted_min *= 0.7   # 4 days: ~1.4% edge OK

        if signal.edge < adjusted_min:
            return RiskCheckResult(
                False,
                f"Edge too low: {signal.edge:.1%} < {adjusted_min:.1%} "
                f"(base {config['min_edge_pct']:.1%} x {edge_multiplier:.1f})",
            )
        return RiskCheckResult(True)

    def _check_min_win_prob(self, signal: TradeSignal, config: dict) -> RiskCheckResult:
        if signal.estimated_prob < config["min_win_prob"]:
            return RiskCheckResult(
                False,
                f"Win prob too low: {signal.estimated_prob:.1%} < {config['min_win_prob']:.1%}",
            )
        return RiskCheckResult(True)

    def _check_daily_var(self, bankroll: float) -> RiskCheckResult:
        """Block trading if daily VaR exceeds limit.

        Scales with bankroll: small accounts get looser limits since
        historical VaR from early losses shouldn't permanently freeze trading.
        - bankroll < $25:  -20% (recovery mode)
        - bankroll < $50:  -15%
        - bankroll < $100: -10%
        - bankroll >= $100: -5% (strict, default)
        """
        if self._returns_tracker is None:
            return RiskCheckResult(True)
        if len(self._returns_tracker.returns) < 7:
            return RiskCheckResult(True)  # Not enough data, allow

        # Scale VaR limit with bankroll size
        if bankroll < 25:
            effective_limit = -0.20
        elif bankroll < 50:
            effective_limit = -0.15
        elif bankroll < 100:
            effective_limit = -0.10
        else:
            effective_limit = self.var_limit  # -5% default

        var_95 = self._returns_tracker.daily_var_95
        if var_95 < effective_limit:
            return RiskCheckResult(
                False,
                f"Daily VaR too high: {var_95:.1%} < {effective_limit:.1%}",
            )
        return RiskCheckResult(True)

    def _check_zscore(self, signal: TradeSignal) -> RiskCheckResult:
        """Require mispricing Z-score above threshold for trade approval."""
        std_dev = signal.metadata.get("price_std", 0.05)  # Default 5% volatility
        z = mispricing_zscore(signal.estimated_prob, signal.market_price, std_dev)
        signal.metadata["zscore"] = round(z, 2)
        if abs(z) < self.zscore_threshold:
            return RiskCheckResult(
                False,
                f"Z-score too low: |{z:.2f}| < {self.zscore_threshold}",
            )
        return RiskCheckResult(True)

    @staticmethod
    def _calibration_bucket(prob: float) -> str:
        """Map a probability to its calibration bucket key."""
        pct = int(prob * 100)
        if pct >= 95:
            return "95-99"
        if pct >= 90:
            return "90-95"
        if pct >= 85:
            return "85-90"
        if pct >= 80:
            return "80-85"
        if pct >= 70:
            return "70-80"
        if pct >= 60:
            return "60-70"
        return "50-60"

    def _calculate_size(
        self,
        signal: TradeSignal,
        bankroll: float,
        config: dict,
        available_capital: float | None = None,
        calibration: dict | None = None,
    ) -> float:
        """Calculate position size using fractional Kelly with tier constraints.

        Uses min_order_usd=0 so Kelly can produce sub-$1 sizes naturally.
        The 1-share floor only applies when Kelly recommends a positive amount
        that falls just below 1 share — never inflates a zero-size signal.
        Calibration penalty: scale Kelly down if this probability bucket is
        historically overconfident, or up if underconfident.
        """
        from bot.utils.math_utils import kelly_criterion

        full_kelly = kelly_criterion(signal.estimated_prob, signal.market_price)
        kelly_frac = config["kelly_fraction"] * full_kelly

        # Calibration adjustment: penalize overconfident probability buckets
        if calibration:
            bucket = self._calibration_bucket(signal.estimated_prob)
            cal_ratio = calibration.get(bucket, 1.0)
            if cal_ratio > 1.1:
                # Overconfident: reduce size
                kelly_frac *= 0.8
            elif cal_ratio < 0.9:
                # Underconfident: slightly increase size
                kelly_frac *= 1.1
            kelly_frac = max(0.05, min(0.5, kelly_frac))

        # Let Kelly produce natural sizes (no internal $1 floor)
        size = position_size_usd(
            bankroll=bankroll,
            kelly_frac=kelly_frac,
            max_per_position_pct=config["max_per_position_pct"],
            min_order_usd=0.0,
        )

        # Cap to available capital (leave 5% buffer for fees/slippage)
        if available_capital is not None and size > available_capital * 0.95:
            size = available_capital * 0.95

        # Floor to minimum sellable position (5 shares) to ensure every
        # position can later be closed via SELL order on the CLOB.
        if signal.market_price > 0 and size > 0:
            min_shares = 5.0  # Polymarket CLOB minimum for sell orders
            min_usd = min_shares * signal.market_price
            max_position = bankroll * config["max_per_position_pct"]
            if size < min_usd:
                # Only bump if min position is within risk limits
                if min_usd <= max_position and min_usd <= (available_capital or bankroll) * 0.95:
                    logger.info(
                        "size_bumped_to_min_5_shares",
                        kelly_usd=round(size, 2),
                        min_usd=round(min_usd, 2),
                    )
                    size = min_usd
                else:
                    size = 0.0

        return size

    def get_risk_metrics(self, bankroll: float) -> dict:
        """Get current risk metrics for the dashboard."""
        tier = CapitalTier.from_bankroll(bankroll)
        config = TierConfig.get(tier)
        dd = current_drawdown(bankroll, self._peak_equity)

        rt = self._returns_tracker
        return {
            "tier": tier.value,
            "bankroll": bankroll,
            "peak_equity": self._peak_equity,
            "current_drawdown_pct": dd,
            "max_drawdown_limit_pct": config["max_drawdown_pct"],
            "daily_pnl": bankroll - self._day_start_equity,
            "daily_loss_limit_pct": config["daily_loss_limit_pct"],
            "max_positions": config["max_positions"],
            "is_paused": self._is_paused,
            "daily_var_95": rt.daily_var_95 if rt else None,
            "rolling_sharpe": rt.rolling_sharpe if rt else None,
            "profit_factor": rt.profit_factor_value if rt else None,
        }
