"""Tests for bot.agent.strategies.news_sniping — NewsSniperStrategy."""

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from bot.agent.strategies.news_sniping import NewsSniperStrategy
from bot.polymarket.types import GammaMarket, OrderSide
from bot.research.news_sniper import SnipeCandidate


@pytest.fixture
def mock_deps():
    clob = MagicMock()
    gamma = MagicMock()
    cache = MagicMock()
    return clob, gamma, cache


@pytest.fixture
def mock_sniper():
    sniper = MagicMock()
    sniper.get_candidates.return_value = []
    return sniper


@pytest.fixture
def strategy(mock_deps, mock_sniper):
    clob, gamma, cache = mock_deps
    return NewsSniperStrategy(
        clob, gamma, cache, news_sniper=mock_sniper,
    )


def _make_market(market_id="m1", question="Will X happen?", yes_price=0.5):
    return GammaMarket(
        id=market_id,
        question=question,
        outcomePrices='[0.50, 0.50]',
        clobTokenIds='["tok_yes", "tok_no"]',
    )


def _make_candidate(
    market_id="m1", sentiment=0.6, overlap=0.65, yes_price=0.5,
):
    return SnipeCandidate(
        market_id=market_id,
        question="Will X happen?",
        headline="Breaking: X is happening right now!",
        source="Reuters",
        sentiment=sentiment,
        keyword_overlap=overlap,
        yes_price=yes_price,
    )


class TestCandidateToSignal:
    def test_positive_sentiment_buys_yes(self, strategy):
        market = _make_market()
        candidate = _make_candidate(sentiment=0.7, overlap=0.6)
        signal = strategy._candidate_to_signal(candidate, market)

        assert signal is not None
        assert signal.side == OrderSide.BUY
        assert signal.outcome == "Yes"
        assert signal.token_id == "tok_yes"
        assert signal.strategy == "news_sniping"

    def test_negative_sentiment_buys_no(self, strategy):
        market = _make_market()
        candidate = _make_candidate(sentiment=-0.7, overlap=0.6)
        signal = strategy._candidate_to_signal(candidate, market)

        assert signal is not None
        assert signal.side == OrderSide.BUY
        assert signal.outcome == "No"
        assert signal.token_id == "tok_no"

    def test_edge_calculation(self, strategy):
        market = _make_market()
        candidate = _make_candidate(sentiment=0.8, overlap=0.7)
        signal = strategy._candidate_to_signal(candidate, market)

        assert signal is not None
        expected_edge = min(0.8 * 0.7 * 0.15, 0.10)
        assert abs(signal.edge - expected_edge) < 0.001

    def test_edge_capped_at_max(self, strategy):
        market = _make_market()
        candidate = _make_candidate(sentiment=1.0, overlap=1.0)
        signal = strategy._candidate_to_signal(candidate, market)

        assert signal is not None
        assert signal.edge <= 0.10

    def test_low_edge_filtered(self, strategy):
        market = _make_market()
        # Very low sentiment * overlap -> edge below MIN_EDGE
        candidate = _make_candidate(sentiment=0.31, overlap=0.50)
        strategy.MIN_EDGE = 0.05  # Higher bar
        signal = strategy._candidate_to_signal(candidate, market)

        assert signal is None

    def test_no_token_ids_returns_none(self, strategy):
        market = GammaMarket(id="m1", question="Q?", clobTokenIds="")
        candidate = _make_candidate()
        signal = strategy._candidate_to_signal(candidate, market)
        assert signal is None

    def test_reasoning_contains_headline(self, strategy):
        market = _make_market()
        candidate = _make_candidate()
        signal = strategy._candidate_to_signal(candidate, market)

        assert signal is not None
        assert "Breaking" in signal.reasoning

    def test_metadata_includes_source(self, strategy):
        market = _make_market()
        candidate = _make_candidate()
        signal = strategy._candidate_to_signal(candidate, market)

        assert signal is not None
        assert signal.metadata["source"] == "Reuters"


class TestScan:
    @pytest.mark.asyncio
    async def test_scan_returns_signals(self, strategy, mock_sniper):
        candidate = _make_candidate(sentiment=0.7, overlap=0.65)
        mock_sniper.get_candidates.return_value = [candidate]
        market = _make_market()

        signals = await strategy.scan([market])
        assert len(signals) >= 1
        assert signals[0].strategy == "news_sniping"

    @pytest.mark.asyncio
    async def test_scan_no_sniper(self, mock_deps):
        clob, gamma, cache = mock_deps
        strat = NewsSniperStrategy(clob, gamma, cache, news_sniper=None)
        signals = await strat.scan([_make_market()])
        assert signals == []

    @pytest.mark.asyncio
    async def test_scan_caps_signals(self, strategy, mock_sniper):
        # Generate many candidates
        candidates = [
            _make_candidate(market_id=f"m{i}", sentiment=0.7, overlap=0.65)
            for i in range(10)
        ]
        mock_sniper.get_candidates.return_value = candidates

        markets = [_make_market(market_id=f"m{i}") for i in range(10)]
        signals = await strategy.scan(markets)

        assert len(signals) <= strategy.MAX_SIGNALS_PER_CYCLE


class TestShouldExit:
    @pytest.mark.asyncio
    async def test_stop_loss(self, strategy):
        result = await strategy.should_exit(
            "m1", current_price=0.40,
            avg_price=0.50,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        assert result == "stop_loss"

    @pytest.mark.asyncio
    async def test_take_profit_after_hold(self, strategy):
        result = await strategy.should_exit(
            "m1", current_price=0.55,
            avg_price=0.50,
            created_at=datetime.now(timezone.utc) - timedelta(hours=2),
        )
        assert result == "take_profit"

    @pytest.mark.asyncio
    async def test_no_take_profit_before_hold(self, strategy):
        result = await strategy.should_exit(
            "m1", current_price=0.55,
            avg_price=0.50,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_max_hold_time(self, strategy):
        result = await strategy.should_exit(
            "m1", current_price=0.505,
            avg_price=0.50,
            created_at=datetime.now(timezone.utc) - timedelta(hours=25),
        )
        assert result == "max_hold_time"

    @pytest.mark.asyncio
    async def test_no_exit_normal(self, strategy):
        result = await strategy.should_exit(
            "m1", current_price=0.51,
            avg_price=0.50,
            created_at=datetime.now(timezone.utc) - timedelta(minutes=30),
        )
        assert result is False
