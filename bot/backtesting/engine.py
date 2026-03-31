"""Lightweight backtesting engine for strategy validation."""

import statistics
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime

import structlog

from bot.backtesting.data_loader import MarketHistory, PriceTick
from bot.backtesting.fees import polymarket_fee

logger = structlog.get_logger()


@dataclass(frozen=True)
class BacktestTrade:
    """A simulated trade in backtesting."""

    entry_time: datetime
    exit_time: datetime
    entry_price: float
    exit_price: float
    size: float
    side: str  # "BUY"
    outcome: str  # "Yes" or "No"
    pnl_gross: float
    fees: float
    pnl_net: float
    exit_reason: str  # "resolution", "take_profit", "stop_loss", "time_expiry"


@dataclass
class BacktestResult:
    """Results of a backtest run.

    Intentionally mutable (not frozen): the backtest engine uses a builder
    pattern, appending trades and updating final_balance incrementally
    as it processes price ticks.
    """

    strategy_name: str
    market_slug: str
    question: str
    trades: list[BacktestTrade] = field(default_factory=list)
    initial_balance: float = 100.0
    final_balance: float = 100.0

    @property
    def total_trades(self) -> int:
        return len(self.trades)

    @property
    def winning_trades(self) -> int:
        return len([t for t in self.trades if t.pnl_net > 0])

    @property
    def losing_trades(self) -> int:
        return len([t for t in self.trades if t.pnl_net < 0])

    @property
    def win_rate(self) -> float:
        if self.total_trades == 0:
            return 0.0
        return self.winning_trades / self.total_trades

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl_net for t in self.trades)

    @property
    def total_fees(self) -> float:
        return sum(t.fees for t in self.trades)

    @property
    def roi(self) -> float:
        if self.initial_balance <= 0:
            return 0.0
        return self.total_pnl / self.initial_balance

    @property
    def max_drawdown(self) -> float:
        """Maximum peak-to-trough drawdown."""
        if not self.trades:
            return 0.0
        equity = self.initial_balance
        peak = equity
        max_dd = 0.0
        for trade in self.trades:
            equity += trade.pnl_net
            if equity > peak:
                peak = equity
            dd = (peak - equity) / peak if peak > 0 else 0.0
            max_dd = max(max_dd, dd)
        return max_dd

    @property
    def sharpe_ratio(self) -> float:
        """Simplified Sharpe ratio (no risk-free rate)."""
        if len(self.trades) < 2:
            return 0.0
        returns = [t.pnl_net / self.initial_balance for t in self.trades]
        avg = statistics.mean(returns)
        std = statistics.stdev(returns)
        return avg / std if std > 0 else 0.0

    def summary(self) -> str:
        """Human-readable summary."""
        gross_pnl = sum(t.pnl_gross for t in self.trades)
        lines = [
            f"Backtest: {self.strategy_name} on {self.market_slug}",
            f"Question: {self.question}",
            f"Trades: {self.total_trades} ({self.winning_trades}W/{self.losing_trades}L)",
            f"Win Rate: {self.win_rate:.1%}",
            f"P&L: ${self.total_pnl:+.2f} (gross ${gross_pnl:+.2f}, fees ${self.total_fees:.2f})",
            f"ROI: {self.roi:.1%}",
            f"Max Drawdown: {self.max_drawdown:.1%}",
            f"Sharpe: {self.sharpe_ratio:.2f}",
            f"Balance: ${self.initial_balance:.2f} -> ${self.final_balance:.2f}",
        ]
        return "\n".join(lines)

    def to_dict(self) -> dict:
        """Serialize for API responses."""
        return {
            "strategy_name": self.strategy_name,
            "market_slug": self.market_slug,
            "question": self.question,
            "total_trades": self.total_trades,
            "winning_trades": self.winning_trades,
            "losing_trades": self.losing_trades,
            "win_rate": round(self.win_rate, 4),
            "total_pnl": round(self.total_pnl, 4),
            "total_fees": round(self.total_fees, 4),
            "roi": round(self.roi, 4),
            "max_drawdown": round(self.max_drawdown, 4),
            "sharpe_ratio": round(self.sharpe_ratio, 4),
            "initial_balance": self.initial_balance,
            "final_balance": round(self.final_balance, 4),
            "trades": [
                {
                    "entry_time": t.entry_time.isoformat(),
                    "exit_time": t.exit_time.isoformat(),
                    "entry_price": t.entry_price,
                    "exit_price": t.exit_price,
                    "size": t.size,
                    "side": t.side,
                    "outcome": t.outcome,
                    "pnl_gross": round(t.pnl_gross, 4),
                    "fees": round(t.fees, 4),
                    "pnl_net": round(t.pnl_net, 4),
                    "exit_reason": t.exit_reason,
                }
                for t in self.trades
            ],
        }


def _close_position(
    entry_time: datetime,
    entry_price: float,
    shares: float,
    exit_time: datetime,
    exit_price: float,
    exit_reason: str,
    fee_rate: float,
    fee_exponent: float,
) -> BacktestTrade:
    """Create a BacktestTrade from position data."""
    pnl_gross = (exit_price - entry_price) * shares
    entry_fee = polymarket_fee(shares, entry_price, fee_rate, fee_exponent)
    exit_fee = polymarket_fee(shares, exit_price, fee_rate, fee_exponent)
    fees = entry_fee + exit_fee
    # Long-only (BUY "Yes"): Polymarket strategies buy outcome tokens and
    # profit when the probability rises toward 1.0. Short-selling is not
    # currently supported by the CLOB, so backtests only simulate buys.
    return BacktestTrade(
        entry_time=entry_time,
        exit_time=exit_time,
        entry_price=entry_price,
        exit_price=exit_price,
        size=shares,
        side="BUY",
        outcome="Yes",
        pnl_gross=pnl_gross,
        fees=fees,
        pnl_net=pnl_gross - fees,
        exit_reason=exit_reason,
    )


async def run_backtest(
    strategy_name: str,
    market_history: MarketHistory,
    entry_condition: Callable[[float, int, list[PriceTick]], bool],
    exit_condition: Callable[[float, float, float], str | None],
    trade_size: float = 5.0,
    initial_balance: float = 100.0,
    fee_rate: float = 0.02,
    fee_exponent: float = 2.0,
) -> BacktestResult:
    """Run a backtest on historical market data.

    Args:
        strategy_name: Name for reporting.
        market_history: Historical price data.
        entry_condition: (price, tick_index, ticks) -> True to enter.
        exit_condition: (entry_price, current_price, hold_seconds) -> exit reason or None.
        trade_size: USD per trade.
        initial_balance: Starting balance.
        fee_rate: Polymarket fee rate.
        fee_exponent: Fee exponent (1 for crypto, 2 for sports).

    Returns:
        BacktestResult with all trades and metrics.
    """
    result = BacktestResult(
        strategy_name=strategy_name,
        market_slug=market_history.slug,
        question=market_history.question,
        initial_balance=initial_balance,
    )

    balance = initial_balance
    position: tuple[datetime, float, float] | None = None  # (entry_time, price, shares)

    for i, tick in enumerate(market_history.ticks):
        price = tick.price
        if price <= 0 or price >= 1:
            continue

        if position is None:
            # Check entry
            if balance < trade_size:
                continue
            if not entry_condition(price, i, market_history.ticks):
                continue
            shares = trade_size / price
            entry_fee = polymarket_fee(shares, price, fee_rate, fee_exponent)
            cost = trade_size + entry_fee
            if cost > balance:
                continue
            position = (tick.timestamp, price, shares)
            balance -= cost
        else:
            # Check exit
            entry_time, entry_price, shares = position
            hold_seconds = (tick.timestamp - entry_time).total_seconds()
            exit_reason = exit_condition(entry_price, price, hold_seconds)
            if exit_reason:
                trade = _close_position(
                    entry_time, entry_price, shares,
                    tick.timestamp, price, exit_reason,
                    fee_rate, fee_exponent,
                )
                result.trades.append(trade)
                exit_fee = polymarket_fee(shares, price, fee_rate, fee_exponent)
                balance += shares * price - exit_fee
                position = None

    # Handle open position at settlement
    if position and market_history.resolution is not None:
        entry_time, entry_price, shares = position
        exit_price = market_history.resolution
        trade = _close_position(
            entry_time, entry_price, shares,
            market_history.end_time, exit_price, "resolution",
            fee_rate, fee_exponent,
        )
        result.trades.append(trade)
        exit_fee = polymarket_fee(shares, exit_price, fee_rate, fee_exponent)
        balance += shares * exit_price - exit_fee

    result.final_balance = balance
    return result
