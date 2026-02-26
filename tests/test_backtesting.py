"""Tests for the backtesting framework."""

from datetime import datetime, timezone

import pytest

from bot.backtesting.data_loader import MarketHistory, PriceTick
from bot.backtesting.engine import BacktestResult, BacktestTrade, run_backtest
from bot.backtesting.fees import net_profit, polymarket_fee

# ---------------------------------------------------------------------------
# Fee model tests
# ---------------------------------------------------------------------------


def test_polymarket_fee_at_half():
    """At p=0.5 the uncertainty factor is maximized (0.25)."""
    fee = polymarket_fee(quantity=100, price=0.5, fee_rate=0.02, exponent=1.0)
    # 100 * 0.5 * 0.02 * (0.5 * 0.5)^1 = 100 * 0.5 * 0.02 * 0.25 = 0.25
    assert fee == pytest.approx(0.25, abs=1e-6)


def test_polymarket_fee_at_extreme():
    """Near 0 or 1, fees are minimal."""
    fee_low = polymarket_fee(quantity=100, price=0.05, fee_rate=0.02, exponent=1.0)
    fee_high = polymarket_fee(quantity=100, price=0.95, fee_rate=0.02, exponent=1.0)
    # Both should be very small
    assert fee_low < 0.01
    assert fee_high < 0.10
    # Symmetric: p=0.05 and p=0.95 have same uncertainty factor
    assert polymarket_fee(100, 0.05, 0.02, 1.0) / 0.05 == pytest.approx(
        polymarket_fee(100, 0.95, 0.02, 1.0) / 0.95, abs=1e-4
    )


def test_polymarket_fee_boundary_zero():
    """Price at 0 or 1 returns zero fee."""
    assert polymarket_fee(100, 0.0) == 0.0
    assert polymarket_fee(100, 1.0) == 0.0
    assert polymarket_fee(100, -0.1) == 0.0
    assert polymarket_fee(100, 1.5) == 0.0


def test_polymarket_fee_sports_vs_crypto():
    """Sports (exponent=2) has lower fees than crypto (exponent=1) at same price."""
    crypto_fee = polymarket_fee(100, 0.5, fee_rate=0.02, exponent=1.0)
    sports_fee = polymarket_fee(100, 0.5, fee_rate=0.02, exponent=2.0)
    # Sports uses (0.25)^2 = 0.0625 vs crypto (0.25)^1 = 0.25
    assert sports_fee < crypto_fee
    assert sports_fee == pytest.approx(0.0625, abs=1e-6)


def test_net_profit_positive_trade():
    """Buy at 0.60, sell at 0.80 should yield positive net profit."""
    profit = net_profit(
        entry_price=0.60,
        exit_price=0.80,
        quantity=10,
        fee_rate=0.02,
        exponent=1.0,
    )
    gross = (0.80 - 0.60) * 10  # 2.0
    assert profit < gross  # Fees reduce profit
    assert profit > 0  # Still profitable


def test_net_profit_negative_trade():
    """Buy at 0.80, sell at 0.60 should yield negative net profit."""
    profit = net_profit(
        entry_price=0.80,
        exit_price=0.60,
        quantity=10,
        fee_rate=0.02,
        exponent=1.0,
    )
    assert profit < 0


# ---------------------------------------------------------------------------
# BacktestResult tests
# ---------------------------------------------------------------------------


def _make_trade(pnl_net: float, pnl_gross: float = 0.0, fees: float = 0.0) -> BacktestTrade:
    """Helper to create a BacktestTrade with given P&L."""
    return BacktestTrade(
        entry_time=datetime(2026, 1, 1, tzinfo=timezone.utc),
        exit_time=datetime(2026, 1, 2, tzinfo=timezone.utc),
        entry_price=0.70,
        exit_price=0.80,
        size=10,
        side="BUY",
        outcome="Yes",
        pnl_gross=pnl_gross,
        fees=fees,
        pnl_net=pnl_net,
        exit_reason="take_profit",
    )


def test_backtest_result_properties():
    """Test win_rate, roi, drawdown calculations."""
    result = BacktestResult(
        strategy_name="test",
        market_slug="test-market",
        question="Test?",
        initial_balance=100.0,
        trades=[
            _make_trade(pnl_net=5.0, pnl_gross=5.5, fees=0.5),
            _make_trade(pnl_net=-3.0, pnl_gross=-2.5, fees=0.5),
            _make_trade(pnl_net=2.0, pnl_gross=2.5, fees=0.5),
        ],
    )
    assert result.total_trades == 3
    assert result.winning_trades == 2
    assert result.losing_trades == 1
    assert result.win_rate == pytest.approx(2 / 3, abs=1e-6)
    assert result.total_pnl == pytest.approx(4.0, abs=1e-6)
    assert result.total_fees == pytest.approx(1.5, abs=1e-6)
    assert result.roi == pytest.approx(0.04, abs=1e-6)


def test_backtest_result_empty():
    """Empty result has sensible defaults."""
    result = BacktestResult(
        strategy_name="test",
        market_slug="empty",
        question="?",
    )
    assert result.total_trades == 0
    assert result.win_rate == 0.0
    assert result.total_pnl == 0.0
    assert result.max_drawdown == 0.0
    assert result.sharpe_ratio == 0.0
    assert result.roi == 0.0


def test_backtest_result_max_drawdown():
    """Drawdown should capture the worst peak-to-trough."""
    result = BacktestResult(
        strategy_name="test",
        market_slug="dd",
        question="?",
        initial_balance=100.0,
        trades=[
            _make_trade(pnl_net=10.0),   # equity 110
            _make_trade(pnl_net=-20.0),  # equity 90, dd = 20/110 = 18.18%
            _make_trade(pnl_net=5.0),    # equity 95
        ],
    )
    assert result.max_drawdown == pytest.approx(20.0 / 110.0, abs=1e-4)


def test_backtest_result_summary_format():
    """Summary should contain key fields."""
    result = BacktestResult(
        strategy_name="sports_favorite",
        market_slug="test-market",
        question="Will Team X win?",
        initial_balance=100.0,
        final_balance=105.0,
        trades=[_make_trade(pnl_net=5.0, pnl_gross=5.5, fees=0.5)],
    )
    summary = result.summary()
    assert "sports_favorite" in summary
    assert "test-market" in summary
    assert "Will Team X win?" in summary
    assert "1W" in summary
    assert "Win Rate" in summary
    assert "ROI" in summary
    assert "Sharpe" in summary


def test_backtest_result_to_dict():
    """to_dict should produce a serializable dict with all fields."""
    result = BacktestResult(
        strategy_name="test",
        market_slug="slug",
        question="Q?",
        initial_balance=100.0,
        final_balance=105.0,
        trades=[_make_trade(pnl_net=5.0, pnl_gross=5.5, fees=0.5)],
    )
    d = result.to_dict()
    assert d["strategy_name"] == "test"
    assert d["total_trades"] == 1
    assert d["winning_trades"] == 1
    assert len(d["trades"]) == 1
    assert "entry_time" in d["trades"][0]


# ---------------------------------------------------------------------------
# Backtest engine tests
# ---------------------------------------------------------------------------


def _make_ticks(prices: list[float], interval_seconds: int = 60) -> list[PriceTick]:
    """Create a list of PriceTick from price sequence."""
    base = datetime(2026, 1, 1, 12, 0, 0, tzinfo=timezone.utc)
    ticks = []
    for i, price in enumerate(prices):
        from datetime import timedelta
        ts = base + timedelta(seconds=i * interval_seconds)
        ticks.append(PriceTick(timestamp=ts, price=price, size=1.0, side="BUY"))
    return ticks


def _make_history(
    prices: list[float],
    resolution: float | None = None,
) -> MarketHistory:
    """Create a MarketHistory from price sequence."""
    ticks = _make_ticks(prices)
    return MarketHistory(
        slug="test-market",
        condition_id="0x123",
        token_id="tok123",
        question="Test market?",
        ticks=ticks,
        start_time=ticks[0].timestamp,
        end_time=ticks[-1].timestamp,
        resolution=resolution,
    )


@pytest.mark.asyncio
async def test_run_backtest_simple():
    """One entry, one exit via take-profit."""
    # Price goes: 0.70, 0.72, 0.75, 0.85 (take profit at 15%)
    history = _make_history([0.70, 0.72, 0.75, 0.85])

    def entry_cond(price, _idx, _ticks):
        return 0.65 <= price <= 0.75

    def exit_cond(entry_price, current_price, _hold):
        if current_price >= entry_price * 1.15:
            return "take_profit"
        return None

    result = await run_backtest(
        strategy_name="test",
        market_history=history,
        entry_condition=entry_cond,
        exit_condition=exit_cond,
        trade_size=5.0,
        initial_balance=100.0,
    )

    assert result.total_trades == 1
    assert result.trades[0].exit_reason == "take_profit"
    assert result.trades[0].pnl_net > 0
    assert result.final_balance > result.initial_balance


@pytest.mark.asyncio
async def test_run_backtest_with_resolution():
    """Position held to settlement resolves at 1.0."""
    # Price stays flat, never hits TP/SL
    history = _make_history([0.70, 0.71, 0.72, 0.73], resolution=1.0)

    def entry_cond(price, _idx, _ticks):
        return price <= 0.71

    def exit_cond(_entry, _current, _hold):
        return None  # Never exit manually

    result = await run_backtest(
        strategy_name="test",
        market_history=history,
        entry_condition=entry_cond,
        exit_condition=exit_cond,
        trade_size=5.0,
        initial_balance=100.0,
    )

    assert result.total_trades == 1
    assert result.trades[0].exit_reason == "resolution"
    assert result.trades[0].exit_price == 1.0
    assert result.trades[0].pnl_net > 0


@pytest.mark.asyncio
async def test_run_backtest_no_entry():
    """Price never meets entry condition -- no trades."""
    history = _make_history([0.50, 0.51, 0.52, 0.53])

    def entry_cond(price, _idx, _ticks):
        return price >= 0.90  # Never true

    def exit_cond(_entry, _current, _hold):
        return None

    result = await run_backtest(
        strategy_name="test",
        market_history=history,
        entry_condition=entry_cond,
        exit_condition=exit_cond,
        trade_size=5.0,
        initial_balance=100.0,
    )

    assert result.total_trades == 0
    assert result.final_balance == 100.0


@pytest.mark.asyncio
async def test_run_backtest_stop_loss():
    """Entry followed by stop-loss exit."""
    history = _make_history([0.70, 0.65, 0.50])

    def entry_cond(price, _idx, _ticks):
        return price >= 0.70

    def exit_cond(entry_price, current_price, _hold):
        if current_price <= entry_price * 0.75:
            return "stop_loss"
        return None

    result = await run_backtest(
        strategy_name="test",
        market_history=history,
        entry_condition=entry_cond,
        exit_condition=exit_cond,
        trade_size=5.0,
        initial_balance=100.0,
    )

    assert result.total_trades == 1
    assert result.trades[0].exit_reason == "stop_loss"
    assert result.trades[0].pnl_net < 0


@pytest.mark.asyncio
async def test_run_backtest_multiple_trades():
    """Multiple entry/exit cycles."""
    # Entry at 0.70, TP at 0.85, re-enter at 0.70, TP at 0.85
    history = _make_history([0.70, 0.85, 0.60, 0.70, 0.85])

    def entry_cond(price, _idx, _ticks):
        return 0.68 <= price <= 0.72

    def exit_cond(entry_price, current_price, _hold):
        if current_price >= entry_price * 1.15:
            return "take_profit"
        return None

    result = await run_backtest(
        strategy_name="test",
        market_history=history,
        entry_condition=entry_cond,
        exit_condition=exit_cond,
        trade_size=5.0,
        initial_balance=100.0,
    )

    assert result.total_trades == 2
    assert all(t.exit_reason == "take_profit" for t in result.trades)
    assert result.final_balance > result.initial_balance


@pytest.mark.asyncio
async def test_run_backtest_insufficient_balance():
    """Cannot enter when balance is too low."""
    history = _make_history([0.70, 0.85])

    def entry_cond(price, _idx, _ticks):
        return True

    def exit_cond(_entry, _current, _hold):
        return None

    result = await run_backtest(
        strategy_name="test",
        market_history=history,
        entry_condition=entry_cond,
        exit_condition=exit_cond,
        trade_size=5.0,
        initial_balance=1.0,  # Too low for $5 trade
    )

    assert result.total_trades == 0
