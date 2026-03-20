"""Tests for bot.agent.strategies.copy_trading — CopyTradingStrategy."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from bot.agent.strategies.copy_trading import CopyTradingStrategy
from bot.polymarket.types import GammaMarket, OrderSide
from bot.research.whale_tracker import WhaleTrade


@pytest.fixture
def mock_deps():
    clob = MagicMock()
    gamma = MagicMock()
    cache = MagicMock()
    return clob, gamma, cache


@pytest.fixture
def mock_whale_tracker():
    tracker = MagicMock()
    tracker.get_whale_trades.return_value = []
    return tracker


@pytest.fixture
def strategy(mock_deps, mock_whale_tracker):
    clob, gamma, cache = mock_deps
    return CopyTradingStrategy(
        clob, gamma, cache,
        whale_tracker=mock_whale_tracker,
        bankroll_fn=lambda: 30.0,
    )


def _make_market(market_id="m1", question="Will X happen?"):
    return GammaMarket(
        id=market_id,
        question=question,
        outcomePrices='[0.50, 0.50]',
        clobTokenIds='["tok_yes", "tok_no"]',
    )


def _make_market_inverted(market_id="m2", question="Will X happen?"):
    """Market where outcomes array is ['No', 'Yes'] (inverted ordering)."""
    return GammaMarket(
        id=market_id,
        question=question,
        outcomes='["No", "Yes"]',
        outcomePrices='[0.73, 0.27]',   # No=0.73, Yes=0.27
        clobTokenIds='["tok_no_first", "tok_yes_second"]',
    )


def _make_whale_trade(
    market_id="m1", side="BUY", outcome="Yes", win_rate=0.70,
    size=100.0, price=0.6,
):
    return WhaleTrade(
        proxy_address="0xwhale",
        username="toptrader",
        market_id=market_id,
        question="Will X happen?",
        outcome=outcome,
        side=side,
        size=size,
        price=price,
        win_rate=win_rate,
        trade_id="t1",
    )


class TestComputeCopySize:
    def test_proportional_scaling(self, strategy):
        # bankroll=30, whale_est=10000, whale_notional=60 (100*0.6)
        size = strategy._compute_copy_size(100.0, 0.6)
        expected = 60.0 * (30.0 / 10000.0)  # 0.18
        assert size == 1.0  # Capped at MIN_COPY_USD

    def test_large_whale_trade(self, strategy):
        # Large whale trade should cap at MAX_COPY_USD
        size = strategy._compute_copy_size(10000.0, 0.8)
        assert size <= 5.0

    def test_zero_price(self, strategy):
        size = strategy._compute_copy_size(100.0, 0.0)
        assert size == 0.0


class TestComputeEdge:
    def test_base_edge(self, strategy):
        edge = strategy._compute_edge(0.55)
        assert abs(edge - 0.03) < 0.001

    def test_win_rate_bonus(self, strategy):
        edge = strategy._compute_edge(0.75)
        expected = 0.03 + (0.75 - 0.55) * 0.10
        assert abs(edge - expected) < 0.001

    def test_below_threshold(self, strategy):
        edge = strategy._compute_edge(0.50)
        assert abs(edge - 0.03) < 0.001  # No bonus below threshold


class TestWhaleTradeToSignal:
    def test_buy_yes(self, strategy):
        market = _make_market()
        trade = _make_whale_trade(outcome="Yes")
        signal = strategy._whale_trade_to_signal(trade, market)

        assert signal is not None
        assert signal.side == OrderSide.BUY
        assert signal.outcome == "Yes"
        assert signal.token_id == "tok_yes"
        assert signal.strategy == "copy_trading"

    def test_buy_no(self, strategy):
        market = _make_market()
        trade = _make_whale_trade(outcome="No")
        signal = strategy._whale_trade_to_signal(trade, market)

        assert signal is not None
        assert signal.outcome == "No"
        assert signal.token_id == "tok_no"

    def test_sell_filtered(self, strategy):
        market = _make_market()
        trade = _make_whale_trade(side="SELL")
        signal = strategy._whale_trade_to_signal(trade, market)
        assert signal is None

    def test_no_token_ids(self, strategy):
        market = GammaMarket(id="m1", question="Q?", clobTokenIds="")
        trade = _make_whale_trade()
        signal = strategy._whale_trade_to_signal(trade, market)
        assert signal is None

    def test_metadata_includes_wallet(self, strategy):
        market = _make_market()
        trade = _make_whale_trade()
        signal = strategy._whale_trade_to_signal(trade, market)

        assert signal is not None
        assert signal.metadata["source_wallet"] == "0xwhale"
        assert signal.metadata["whale_username"] == "toptrader"

    def test_reasoning_contains_whale_info(self, strategy):
        market = _make_market()
        trade = _make_whale_trade()
        signal = strategy._whale_trade_to_signal(trade, market)

        assert signal is not None
        assert "toptrader" in signal.reasoning
        assert "70%" in signal.reasoning

    def test_inverted_outcomes_yes(self, strategy):
        """When outcomes=['No','Yes'], Yes trade maps to token_ids[1]."""
        market = _make_market_inverted()
        trade = _make_whale_trade(market_id="m2", outcome="Yes", price=0.27)
        signal = strategy._whale_trade_to_signal(trade, market)

        assert signal is not None
        assert signal.outcome == "Yes"
        assert signal.token_id == "tok_yes_second"  # index 1 in inverted market

    def test_inverted_outcomes_no(self, strategy):
        """When outcomes=['No','Yes'], No trade maps to token_ids[0]."""
        market = _make_market_inverted()
        trade = _make_whale_trade(market_id="m2", outcome="No", price=0.73)
        signal = strategy._whale_trade_to_signal(trade, market)

        assert signal is not None
        assert signal.outcome == "No"
        assert signal.token_id == "tok_no_first"  # index 0 in inverted market


class TestScan:
    @pytest.mark.asyncio
    async def test_scan_returns_signals(self, strategy, mock_whale_tracker):
        trade = _make_whale_trade()
        mock_whale_tracker.get_whale_trades.return_value = [trade]
        market = _make_market()

        signals = await strategy.scan([market])
        assert len(signals) == 1
        assert signals[0].strategy == "copy_trading"

    @pytest.mark.asyncio
    async def test_scan_no_tracker(self, mock_deps):
        clob, gamma, cache = mock_deps
        strat = CopyTradingStrategy(clob, gamma, cache, whale_tracker=None)
        signals = await strat.scan([_make_market()])
        assert signals == []

    @pytest.mark.asyncio
    async def test_scan_caps_signals(self, strategy, mock_whale_tracker):
        trades = [
            _make_whale_trade(market_id=f"m{i}")
            for i in range(10)
        ]
        mock_whale_tracker.get_whale_trades.return_value = trades
        markets = [_make_market(market_id=f"m{i}") for i in range(10)]

        signals = await strategy.scan(markets)
        assert len(signals) <= strategy.MAX_COPY_SIGNALS_PER_CYCLE

    @pytest.mark.asyncio
    async def test_scan_dedup_markets(self, strategy, mock_whale_tracker):
        # Two whale trades for same market
        trades = [
            _make_whale_trade(market_id="m1"),
            WhaleTrade(
                proxy_address="0xother", username="w2", market_id="m1",
                question="Q?", outcome="Yes", side="BUY",
                size=200, price=0.5, win_rate=0.8, trade_id="t2",
            ),
        ]
        mock_whale_tracker.get_whale_trades.return_value = trades
        market = _make_market()

        signals = await strategy.scan([market])
        assert len(signals) == 1  # Only one per market


class TestShouldExit:
    @pytest.mark.asyncio
    async def test_stop_loss(self, strategy):
        result = await strategy.should_exit(
            "m1", current_price=0.40,
            avg_price=0.50,
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert result == "stop_loss"

    @pytest.mark.asyncio
    async def test_take_profit_after_hold(self, strategy):
        result = await strategy.should_exit(
            "m1", current_price=0.56,
            avg_price=0.50,
            created_at=datetime.now(timezone.utc) - timedelta(hours=3),
        )
        assert result == "take_profit"

    @pytest.mark.asyncio
    async def test_no_take_profit_before_hold(self, strategy):
        result = await strategy.should_exit(
            "m1", current_price=0.56,
            avg_price=0.50,
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_max_hold_time(self, strategy):
        result = await strategy.should_exit(
            "m1", current_price=0.51,
            avg_price=0.50,
            created_at=datetime.now(timezone.utc) - timedelta(hours=73),
        )
        assert result == "max_hold_time"

    @pytest.mark.asyncio
    async def test_no_exit_normal(self, strategy):
        result = await strategy.should_exit(
            "m1", current_price=0.51,
            avg_price=0.50,
            created_at=datetime.now(timezone.utc) - timedelta(hours=1),
        )
        assert result is False
