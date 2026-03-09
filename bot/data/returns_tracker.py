"""Rolling returns tracker for VaR and Sharpe computation.

Loads daily returns from equity snapshots and provides
real-time risk metrics (VaR, Sharpe, Profit Factor).
"""

import structlog

from bot.utils.math_utils import sharpe_ratio
from bot.utils.risk_metrics import parametric_var, profit_factor

logger = structlog.get_logger()

# Rolling window size (days)
WINDOW = 30


class ReturnsTracker:
    """Track daily returns for VaR and Sharpe computation.

    Loads historical returns from equity_snapshots table,
    computes daily returns as (equity_today - equity_yesterday) / equity_yesterday.
    """

    def __init__(self, window: int = WINDOW):
        self._window = window
        self._returns: list[float] = []
        self._gross_profit: float = 0.0
        self._gross_loss: float = 0.0

    async def load_from_db(self) -> None:
        """Load daily returns from equity snapshots."""
        from bot.data.database import async_session
        from bot.data.models import PortfolioSnapshot

        try:
            from sqlalchemy import select

            async with async_session() as session:
                stmt = (
                    select(PortfolioSnapshot)
                    .order_by(PortfolioSnapshot.timestamp.desc())
                    .limit(self._window * 288)  # ~288 snapshots/day (every 5min)
                )
                result = await session.execute(stmt)
                snapshots = list(result.scalars().all())

            if len(snapshots) < 2:
                logger.info("returns_tracker_insufficient_data", snapshots=len(snapshots))
                return

            # Group by date, take last snapshot of each day
            daily: dict[str, float] = {}
            for snap in snapshots:
                date_key = snap.timestamp.strftime("%Y-%m-%d")
                if date_key not in daily:
                    daily[date_key] = snap.total_equity

            # Sort by date and compute returns
            sorted_dates = sorted(daily.keys())
            returns = []
            for i in range(1, len(sorted_dates)):
                prev_eq = daily[sorted_dates[i - 1]]
                curr_eq = daily[sorted_dates[i]]
                if prev_eq > 0:
                    ret = (curr_eq - prev_eq) / prev_eq
                    returns.append(ret)

            self._returns = returns[-self._window:]

            logger.info(
                "returns_tracker_loaded",
                days=len(self._returns),
                window=self._window,
            )
        except Exception as e:
            logger.error("returns_tracker_load_failed", error=str(e))

    async def load_trade_pnl(self) -> None:
        """Load gross profit/loss from recent resolved trades."""
        from bot.data.database import async_session
        from bot.data.repositories import TradeRepository

        try:
            async with async_session() as session:
                repo = TradeRepository(session)
                trades = await repo.get_recent(limit=200)

            resolved = [
                t for t in trades
                if t.exit_reason and t.status in ("filled", "completed")
            ]

            self._gross_profit = sum(t.pnl for t in resolved if t.pnl > 0)
            self._gross_loss = abs(sum(t.pnl for t in resolved if t.pnl < 0))

            logger.info(
                "returns_tracker_pnl_loaded",
                trades=len(resolved),
                gross_profit=round(self._gross_profit, 2),
                gross_loss=round(self._gross_loss, 2),
            )
        except Exception as e:
            logger.error("returns_tracker_pnl_load_failed", error=str(e))

    def record_return(self, daily_return: float) -> None:
        """Record a new daily return (called at day boundary)."""
        updated = list(self._returns)
        updated.append(daily_return)
        # Keep only the last N days
        self._returns = updated[-self._window:]

    @property
    def returns(self) -> tuple[float, ...]:
        """Immutable copy of daily returns."""
        return tuple(self._returns)

    @property
    def daily_var_95(self) -> float:
        """95% parametric VaR (negative number = loss)."""
        if len(self._returns) < 7:
            return 0.0
        return parametric_var(self._returns, confidence=0.95)

    @property
    def rolling_sharpe(self) -> float:
        """Annualized Sharpe ratio from rolling window."""
        if len(self._returns) < 7:
            return 0.0
        return sharpe_ratio(self._returns)

    @property
    def profit_factor_value(self) -> float:
        """Profit factor from resolved trades."""
        return profit_factor(self._gross_profit, self._gross_loss)
