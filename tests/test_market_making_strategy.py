"""Tests for MarketMakingStrategy.

Covers:
- __init__ (lines 27-30)
- scan() (lines 32-42)
- _evaluate_market() (lines 44-97)
- should_exit() (line 99-102)
"""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.agent.strategies.market_making import (
    MAX_SPREAD,
    MIN_SPREAD,
    MarketMakingStrategy,
)
from bot.polymarket.types import GammaMarket, OrderBook, OrderBookEntry, OrderSide, TradeSignal


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_strategy() -> MarketMakingStrategy:
    return MarketMakingStrategy(
        clob_client=MagicMock(),
        gamma_client=MagicMock(),
        cache=MagicMock(),
    )


def _make_market(
    market_id: str = "mkt1",
    question: str = "Will Bitcoin go up or down in 5 min?",
    clob_token_ids: str = '["token_yes","token_no"]',
    yes_price: float = 0.48,
    no_price: float = 0.52,
    hours_to_resolution: float = 48.0,
) -> GammaMarket:
    end = datetime.now(timezone.utc) + timedelta(hours=hours_to_resolution)
    return GammaMarket(
        id=market_id,
        question=question,
        endDateIso=end.strftime("%Y-%m-%dT%H:%M:%SZ"),
        outcomes=["Yes", "No"],
        outcomePrices=f"[{yes_price},{no_price}]",
        clobTokenIds=clob_token_ids,
    )


def _make_order_book(
    best_bid: float = 0.46,
    best_ask: float = 0.54,
    bid_size: float = 100.0,
    ask_size: float = 100.0,
) -> OrderBook:
    """Create an order book with a controllable spread."""
    return OrderBook(
        market="mkt1",
        bids=[OrderBookEntry(price=best_bid, size=bid_size)],
        asks=[OrderBookEntry(price=best_ask, size=ask_size)],
    )


# ---------------------------------------------------------------------------
# __init__ and class attributes
# ---------------------------------------------------------------------------


class TestInit:
    def test_strategy_name(self):
        strategy = _make_strategy()
        assert strategy.name == "market_making"

    def test_min_spread_initialized(self):
        strategy = _make_strategy()
        assert strategy.MIN_SPREAD == MIN_SPREAD

    def test_max_spread_initialized(self):
        strategy = _make_strategy()
        assert strategy.MAX_SPREAD == MAX_SPREAD

    def test_min_spread_value(self):
        strategy = _make_strategy()
        assert strategy.MIN_SPREAD == pytest.approx(0.03)

    def test_max_spread_value(self):
        strategy = _make_strategy()
        assert strategy.MAX_SPREAD == pytest.approx(0.15)



# ---------------------------------------------------------------------------
# _evaluate_market — early-exit guards
# ---------------------------------------------------------------------------


class TestEvaluateMarketGuards:
    async def test_no_token_ids_returns_none(self):
        strategy = _make_strategy()
        market = _make_market(clob_token_ids="[]")
        result = await strategy._evaluate_market(market)
        assert result is None

    async def test_order_book_exception_returns_none(self):
        strategy = _make_strategy()
        strategy.get_order_book = AsyncMock(side_effect=RuntimeError("API error"))
        market = _make_market()
        result = await strategy._evaluate_market(market)
        assert result is None

    async def test_spread_none_returns_none(self):
        """If order book has no bids, spread is None."""
        strategy = _make_strategy()
        empty_book = OrderBook(
            market="mkt1",
            bids=[],
            asks=[OrderBookEntry(price=0.54, size=100.0)],
        )
        strategy.get_order_book = AsyncMock(return_value=empty_book)
        market = _make_market()
        result = await strategy._evaluate_market(market)
        assert result is None

    async def test_mid_none_returns_none(self):
        """If order book has no asks, mid_price is None."""
        strategy = _make_strategy()
        empty_book = OrderBook(
            market="mkt1",
            bids=[OrderBookEntry(price=0.46, size=100.0)],
            asks=[],
        )
        strategy.get_order_book = AsyncMock(return_value=empty_book)
        market = _make_market()
        result = await strategy._evaluate_market(market)
        assert result is None

    async def test_spread_below_min_returns_none(self):
        """Spread < MIN_SPREAD (0.03) is too tight to make market."""
        strategy = _make_strategy()
        # spread = 0.49 - 0.48 = 0.01 < 0.03
        book = _make_order_book(best_bid=0.48, best_ask=0.49)
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market()
        result = await strategy._evaluate_market(market)
        assert result is None

    async def test_spread_just_above_min_returns_signal(self):
        """Spread > MIN_SPREAD and < MAX_SPREAD → valid market making opportunity."""
        strategy = _make_strategy()
        # spread = 0.50 - 0.44 = 0.06 > 0.03 (MIN_SPREAD), < 0.15 (MAX_SPREAD)
        # mid = 0.47, buy_price = 0.44 + 0.01 = 0.45 < 0.47 → valid
        book = _make_order_book(best_bid=0.44, best_ask=0.50)
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market()
        result = await strategy._evaluate_market(market)
        assert result is not None

    async def test_spread_above_max_returns_none(self):
        """Spread > MAX_SPREAD (0.15) is too wide — don't make market."""
        strategy = _make_strategy()
        # spread = 0.70 - 0.30 = 0.40 > 0.15
        book = _make_order_book(best_bid=0.30, best_ask=0.70)
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market()
        result = await strategy._evaluate_market(market)
        assert result is None

    async def test_spread_at_max_boundary_returns_none(self):
        """Spread == MAX_SPREAD (0.15) is too wide (not < MAX_SPREAD)."""
        strategy = _make_strategy()
        # spread = 0.50 - 0.35 = 0.15
        book = _make_order_book(best_bid=0.35, best_ask=0.50)
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market()
        result = await strategy._evaluate_market(market)
        assert result is None

    async def test_buy_price_at_or_above_mid_returns_none(self):
        """If buy_price >= mid, the order would cross the spread — skip."""
        strategy = _make_strategy()
        # best_bid=0.46, buy_price=0.47, spread=0.54-0.46=0.08
        # mid = (0.46+0.54)/2 = 0.50, buy_price=0.46+0.01=0.47 < 0.50 → signal!
        # Let's make buy_price >= mid by setting best_bid very close to best_ask
        # best_bid=0.44, best_ask=0.46, spread=0.02 < MIN_SPREAD → returns None anyway
        # Instead: best_bid=0.48, buy_price=0.49, mid=(0.48+0.51)/2=0.495
        # buy_price=0.49 < 0.495 → still valid
        # To force buy_price >= mid: best_bid=0.49, best_ask=0.52
        # spread=0.03 >= MIN_SPREAD, mid=0.505, buy_price=0.49+0.01=0.50 < 0.505 → valid
        # Hard to force buy_price >= mid with typical values since we add 0.01.
        # Use: best_bid=0.50, best_ask=0.53, spread=0.03, mid=0.515, buy_price=0.51 < 0.515 → valid
        # Try: best_bid=0.51, best_ask=0.54, spread=0.03, mid=0.525, buy_price=0.52 < 0.525 → valid
        # To guarantee buy_price >= mid: need best_bid + 0.01 >= (best_bid + best_ask) / 2
        # → 0.02 >= best_ask - best_bid → spread <= 0.02 < MIN_SPREAD → guard fires first
        # This means the buy_price >= mid guard (line 66-67) requires a crafted scenario.
        # We can monkey-patch best_bid to be None-equivalent by giving book.best_bid = None:
        # Actually: buy_price = round((book.best_bid or 0) + 0.01, 2)
        # If best_bid is None → buy_price = 0.01 which will be < any reasonable mid.
        # To reach the guard we need buy_price >= mid explicitly.
        # Simplest: mock a book where best_bid property returns a high value
        mock_book = MagicMock()
        mock_book.spread = 0.05  # within bounds
        mock_book.mid_price = 0.48
        mock_book.best_bid = 0.50  # buy_price = 0.51 >= mid 0.48 → guard fires
        mock_book.best_ask = 0.53
        strategy.get_order_book = AsyncMock(return_value=mock_book)
        market = _make_market()
        result = await strategy._evaluate_market(market)
        assert result is None


# ---------------------------------------------------------------------------
# _evaluate_market — happy path (valid signal)
# ---------------------------------------------------------------------------


class TestEvaluateMarketHappyPath:
    async def _run_valid(
        self,
        best_bid: float = 0.46,
        best_ask: float = 0.54,
        market_id: str = "mkt1",
    ) -> TradeSignal | None:
        strategy = _make_strategy()
        book = _make_order_book(best_bid=best_bid, best_ask=best_ask)
        strategy.get_order_book = AsyncMock(return_value=book)
        market = _make_market(market_id=market_id)
        return await strategy._evaluate_market(market)

    async def test_valid_book_returns_signal(self):
        signal = await self._run_valid()
        assert signal is not None

    async def test_signal_strategy_name(self):
        signal = await self._run_valid()
        assert signal is not None
        assert signal.strategy == "market_making"

    async def test_signal_side_is_buy(self):
        signal = await self._run_valid()
        assert signal is not None
        assert signal.side == OrderSide.BUY

    async def test_signal_outcome_is_yes(self):
        signal = await self._run_valid()
        assert signal is not None
        assert signal.outcome == "Yes"

    async def test_signal_token_id(self):
        signal = await self._run_valid()
        assert signal is not None
        assert signal.token_id == "token_yes"

    async def test_signal_market_id(self):
        signal = await self._run_valid(market_id="my_market")
        assert signal is not None
        assert signal.market_id == "my_market"

    async def test_signal_confidence_is_055(self):
        signal = await self._run_valid()
        assert signal is not None
        assert signal.confidence == pytest.approx(0.55)

    async def test_signal_edge_is_positive(self):
        signal = await self._run_valid()
        assert signal is not None
        assert signal.edge > 0

    async def test_signal_estimated_prob_is_mid(self):
        """estimated_prob should be set to mid_price."""
        # best_bid=0.46, best_ask=0.54 → mid=0.50
        signal = await self._run_valid(best_bid=0.46, best_ask=0.54)
        assert signal is not None
        assert signal.estimated_prob == pytest.approx(0.50)

    async def test_signal_market_price_is_buy_price(self):
        """market_price should be buy_price = best_bid + 0.01."""
        # best_bid=0.46 → buy_price=0.47
        signal = await self._run_valid(best_bid=0.46, best_ask=0.54)
        assert signal is not None
        assert signal.market_price == pytest.approx(0.47)

    async def test_signal_size_usd_is_zero(self):
        signal = await self._run_valid()
        assert signal is not None
        assert signal.size_usd == 0.0

    async def test_metadata_contains_spread(self):
        signal = await self._run_valid(best_bid=0.46, best_ask=0.54)
        assert signal is not None
        assert "spread" in signal.metadata
        assert signal.metadata["spread"] == pytest.approx(0.08)

    async def test_metadata_contains_mid_price(self):
        signal = await self._run_valid(best_bid=0.46, best_ask=0.54)
        assert signal is not None
        assert "mid_price" in signal.metadata
        assert signal.metadata["mid_price"] == pytest.approx(0.50)

    async def test_metadata_contains_best_bid(self):
        signal = await self._run_valid(best_bid=0.46, best_ask=0.54)
        assert signal is not None
        assert signal.metadata["best_bid"] == pytest.approx(0.46)

    async def test_metadata_contains_best_ask(self):
        signal = await self._run_valid(best_bid=0.46, best_ask=0.54)
        assert signal is not None
        assert signal.metadata["best_ask"] == pytest.approx(0.54)

    async def test_edge_formula(self):
        """edge = (spread / 2) / buy_price."""
        # best_bid=0.46, best_ask=0.54 → spread=0.08, buy_price=0.47
        # edge = 0.04 / 0.47 ≈ 0.0851
        signal = await self._run_valid(best_bid=0.46, best_ask=0.54)
        assert signal is not None
        expected_edge = 0.04 / 0.47
        assert signal.edge == pytest.approx(expected_edge, rel=1e-4)

    async def test_reasoning_contains_key_values(self):
        signal = await self._run_valid()
        assert signal is not None
        assert "Market making" in signal.reasoning
        assert "spread=" in signal.reasoning


# ---------------------------------------------------------------------------
# scan()
# ---------------------------------------------------------------------------


class TestScan:
    async def test_scan_returns_list(self):
        strategy = _make_strategy()
        strategy.get_order_book = AsyncMock(side_effect=RuntimeError("no book"))
        result = await strategy.scan([_make_market()])
        assert isinstance(result, list)

    async def test_scan_empty_markets_returns_empty(self):
        strategy = _make_strategy()
        result = await strategy.scan([])
        assert result == []

    async def test_scan_valid_market_returns_signal(self):
        strategy = _make_strategy()
        book = _make_order_book(best_bid=0.46, best_ask=0.54)
        strategy.get_order_book = AsyncMock(return_value=book)
        result = await strategy.scan([_make_market()])
        assert len(result) == 1

    async def test_scan_multiple_valid_markets(self):
        strategy = _make_strategy()
        book = _make_order_book(best_bid=0.46, best_ask=0.54)
        strategy.get_order_book = AsyncMock(return_value=book)
        markets = [
            _make_market(market_id="m1"),
            _make_market(market_id="m2"),
            _make_market(market_id="m3"),
        ]
        result = await strategy.scan(markets)
        assert len(result) == 3

    async def test_scan_skips_invalid_markets(self):
        strategy = _make_strategy()
        book = _make_order_book(best_bid=0.46, best_ask=0.54)
        strategy.get_order_book = AsyncMock(return_value=book)
        markets = [
            _make_market(market_id="valid"),
            _make_market(market_id="no_tokens", clob_token_ids="[]"),
        ]
        result = await strategy.scan(markets)
        assert len(result) == 1
        assert result[0].market_id == "valid"

    async def test_scan_all_invalid_returns_empty(self):
        strategy = _make_strategy()
        strategy.get_order_book = AsyncMock(side_effect=RuntimeError("no book"))
        markets = [_make_market(clob_token_ids="[]"), _make_market(clob_token_ids="[]")]
        result = await strategy.scan(markets)
        assert result == []

    async def test_scan_tight_spread_excluded(self):
        """Spread < MIN_SPREAD → no signal."""
        strategy = _make_strategy()
        # spread = 0.49 - 0.48 = 0.01 < 0.03
        book = _make_order_book(best_bid=0.48, best_ask=0.49)
        strategy.get_order_book = AsyncMock(return_value=book)
        result = await strategy.scan([_make_market()])
        assert result == []

    async def test_scan_wide_spread_excluded(self):
        """Spread > MAX_SPREAD → no signal."""
        strategy = _make_strategy()
        # spread = 0.70 - 0.20 = 0.50 > 0.15
        book = _make_order_book(best_bid=0.20, best_ask=0.70)
        strategy.get_order_book = AsyncMock(return_value=book)
        result = await strategy.scan([_make_market()])
        assert result == []


# ---------------------------------------------------------------------------
# should_exit() — always returns False (line 102)
# ---------------------------------------------------------------------------


class TestShouldExit:
    async def test_always_returns_false_low_price(self):
        strategy = _make_strategy()
        assert await strategy.should_exit("mkt1", 0.01) is False

    async def test_always_returns_false_mid_price(self):
        strategy = _make_strategy()
        assert await strategy.should_exit("mkt1", 0.50) is False

    async def test_always_returns_false_high_price(self):
        strategy = _make_strategy()
        assert await strategy.should_exit("mkt1", 0.99) is False

    async def test_always_returns_false_with_kwargs(self):
        """should_exit accepts **kwargs without raising."""
        strategy = _make_strategy()
        result = await strategy.should_exit(
            "mkt1", 0.50, avg_price=0.46, created_at="2025-01-01"
        )
        assert result is False

    async def test_returns_bool(self):
        strategy = _make_strategy()
        result = await strategy.should_exit("mkt1", 0.50)
        assert isinstance(result, bool)
