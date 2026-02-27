"""Tests for MarketAnalyzer deduplication, stop-loss, and quality filter logic."""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.agent.market_analyzer import MarketAnalyzer, normalize_category
from bot.data.market_cache import MarketCache
from bot.polymarket.types import GammaMarket, OrderBook, OrderBookEntry, OrderSide, TradeSignal


def _signal(
    market_id: str = "mkt1",
    question: str = "Will X happen?",
    edge: float = 0.05,
    confidence: float = 0.80,
) -> TradeSignal:
    return TradeSignal(
        strategy="time_decay",
        market_id=market_id,
        token_id="tok1",
        question=question,
        side=OrderSide.BUY,
        outcome="Yes",
        estimated_prob=0.92,
        market_price=0.87,
        edge=edge,
        size_usd=1.0,
        confidence=confidence,
    )


class TestQuestionGroupKey:
    def test_same_pattern_different_names(self):
        q1 = "Will Albert Littell be the Democratic nominee for Senate in Mississippi?"
        q2 = "Will Scott Colom be the Democratic nominee for Senate in Mississippi?"
        assert MarketAnalyzer._question_group_key(q1) == MarketAnalyzer._question_group_key(q2)

    def test_different_patterns_differ(self):
        q1 = "Will Albert Littell be the Democratic nominee for Senate in Mississippi?"
        q2 = "Will Bitcoin hit $100k by March?"
        assert MarketAnalyzer._question_group_key(q1) != MarketAnalyzer._question_group_key(q2)

    def test_case_insensitive(self):
        q1 = "Will X Be The Winner?"
        q2 = "Will Y be the winner?"
        assert MarketAnalyzer._question_group_key(q1) == MarketAnalyzer._question_group_key(q2)


class TestDeduplicateCorrelated:
    def test_keeps_best_signal_per_group(self):
        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        signals = [
            _signal(
                market_id="mkt1",
                question="Will Albert Littell be the Democratic nominee for Senate in Mississippi?",
                edge=0.03,
                confidence=0.80,
            ),
            _signal(
                market_id="mkt2",
                question="Will Scott Colom be the Democratic nominee for Senate in Mississippi?",
                edge=0.05,
                confidence=0.85,
            ),
        ]
        result = analyzer._deduplicate_correlated(signals)
        assert len(result) == 1
        assert result[0].market_id == "mkt2"  # higher edge*confidence

    def test_different_groups_kept(self):
        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        signals = [
            _signal(market_id="mkt1", question="Will X win the election?"),
            _signal(market_id="mkt2", question="Will Bitcoin hit $100k?"),
        ]
        result = analyzer._deduplicate_correlated(signals)
        assert len(result) == 2

    def test_empty_signals(self):
        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        assert analyzer._deduplicate_correlated([]) == []

    def test_single_signal_kept(self):
        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        signals = [_signal(market_id="mkt1", question="Will X happen?")]
        result = analyzer._deduplicate_correlated(signals)
        assert len(result) == 1

    def test_cross_strategy_signals_both_kept(self):
        """Different strategies for the same market should NOT be deduped."""
        analyzer = MarketAnalyzer.__new__(MarketAnalyzer)
        td_signal = TradeSignal(
            strategy="time_decay",
            market_id="mkt1",
            token_id="tok1",
            question="Will Albert Littell be the Democratic nominee for Senate in Mississippi?",
            side=OrderSide.BUY,
            outcome="Yes",
            estimated_prob=0.95,
            market_price=0.92,
            edge=0.03,
            size_usd=1.0,
            confidence=0.90,
        )
        vb_signal = TradeSignal(
            strategy="value_betting",
            market_id="mkt1",
            token_id="tok1",
            question="Will Albert Littell be the Democratic nominee for Senate in Mississippi?",
            side=OrderSide.BUY,
            outcome="Yes",
            estimated_prob=0.10,
            market_price=0.08,
            edge=0.08,
            size_usd=1.0,
            confidence=0.70,
        )
        result = analyzer._deduplicate_correlated([td_signal, vb_signal])
        # Both strategies should survive — risk manager decides viability
        assert len(result) == 2
        strategies = {s.strategy for s in result}
        assert strategies == {"time_decay", "value_betting"}


def _position(
    market_id: str = "mkt1",
    strategy: str = "time_decay",
    avg_price: float = 0.95,
    current_price: float = 0.93,
):
    return SimpleNamespace(
        market_id=market_id,
        strategy=strategy,
        avg_price=avg_price,
        current_price=current_price,
    )


class TestCheckStopLoss:
    def setup_method(self):
        self.analyzer = MarketAnalyzer.__new__(MarketAnalyzer)

    def test_near_worthless_triggers_exit(self):
        pos = _position(current_price=0.05, avg_price=0.90)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=True)
        assert reason is not None
        assert "near_worthless" in reason

    def test_40pct_loss_triggers_exit(self):
        pos = _position(avg_price=0.50, current_price=0.25)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=True)
        assert reason is not None
        assert "stop_loss" in reason

    def test_39pct_loss_no_exit(self):
        pos = _position(avg_price=0.50, current_price=0.31)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=True)
        assert reason is None

    def test_unmatched_strategy_below_default_threshold(self):
        pos = _position(strategy="external", avg_price=0.95, current_price=0.60)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=False)
        assert reason is not None
        assert "unmatched_strategy" in reason

    def test_unmatched_strategy_above_threshold_no_exit(self):
        pos = _position(strategy="external", avg_price=0.95, current_price=0.80)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=False)
        assert reason is None

    def test_matched_strategy_no_stop_loss_when_healthy(self):
        pos = _position(avg_price=0.95, current_price=0.93)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=True)
        assert reason is None

    def test_real_case_external_58pct_loss(self):
        """Real scenario: position bought at $0.396, now $0.165 (58% loss)."""
        pos = _position(strategy="external", avg_price=0.396, current_price=0.165)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=False)
        assert reason is not None
        assert "stop_loss" in reason


class TestNormalizeCategory:
    def test_politics_variants(self):
        assert normalize_category("Politics") == "Politics"
        assert normalize_category("Republican Primary") == "Politics"
        assert normalize_category("Democratic Primary") == "Politics"
        assert normalize_category("U.S. Elections") == "Politics"
        assert normalize_category("Governor") == "Politics"
        assert normalize_category("presidential") == "Politics"

    def test_case_insensitive(self):
        assert normalize_category("POLITICS") == "Politics"
        assert normalize_category("republican primary") == "Politics"

    def test_non_political_passed_through(self):
        assert normalize_category("Sports") == "Sports"
        assert normalize_category("Crypto") == "Crypto"
        assert normalize_category("Entertainment") == "Entertainment"

    def test_empty_returns_other(self):
        assert normalize_category("") == "Other"
        assert normalize_category(None) == "Other"


def _make_gamma_market(
    market_id: str = "0xabc",
    question: str = "Will X happen?",
    outcomes: list[str] | None = None,
    neg_risk: bool = False,
    best_bid: float | None = None,
    best_ask: float | None = None,
    volume_24h: float = 0.0,
    category: str = "Sports",
) -> GammaMarket:
    if outcomes is None:
        outcomes = ["Yes", "No"]
    return GammaMarket(
        id=market_id,
        conditionId=market_id,
        question=question,
        endDateIso="2026-03-01T12:00:00Z",
        outcomes=outcomes,
        outcomePrices='["0.92","0.08"]',
        clobTokenIds='["tok1","tok2"]',
        acceptingOrders=True,
        negRisk=neg_risk,
        bestBid=best_bid,
        bestAsk=best_ask,
        volume24hr=volume_24h,
        groupItemTitle=category,
    )


class TestQualityFilterConstants:
    """Test that quality filter thresholds are properly set."""

    def test_neg_risk_excluded(self):
        """Markets with negRisk=True should be filtered out."""
        m = _make_gamma_market(neg_risk=True)
        assert m.neg_risk is True

    def test_min_bid_ratio_set(self):
        assert MarketAnalyzer.MIN_BID_RATIO == 0.50

    def test_min_volume_24h_set(self):
        assert MarketAnalyzer.MIN_VOLUME_24H == 50.0

    def test_max_spread_set(self):
        assert MarketAnalyzer.MAX_SPREAD == 0.04

    def test_gamma_market_new_fields(self):
        """GammaMarket should expose neg_risk, best_bid_price, best_ask_price, volume_24h."""
        m = _make_gamma_market(
            neg_risk=True,
            best_bid=0.91,
            best_ask=0.93,
            volume_24h=500.0,
        )
        assert m.neg_risk is True
        assert m.best_bid_price == 0.91
        assert m.best_ask_price == 0.93
        assert m.volume_24h == 500.0

    def test_gamma_market_defaults(self):
        """New fields should default correctly."""
        m = GammaMarket(id="0x1", question="Test?")
        assert m.neg_risk is False
        assert m.best_bid_price is None
        assert m.best_ask_price is None
        assert m.volume_24h == 0.0


# ---------------------------------------------------------------------------
# Quality Filter Order Book Cache (H1-H3)
# ---------------------------------------------------------------------------


class TestQualityFilterCache:
    @pytest.mark.asyncio
    async def test_quality_filter_uses_cached_order_book(self):
        """Quality filter should check cache before calling CLOB API."""
        cache = MarketCache(default_ttl=60)
        cached_book = OrderBook(
            market="test",
            bids=[OrderBookEntry(price=0.90, size=100)],
            asks=[OrderBookEntry(price=0.92, size=100)],
        )
        cache.set_order_book("tok1", cached_book, ttl=10)

        mock_clob = AsyncMock()

        analyzer = MarketAnalyzer(
            gamma_client=MagicMock(),
            cache=cache,
            strategies=[],
            clob_client=mock_clob,
        )

        # Market with no Gamma bid/ask → triggers order book check
        market = _make_gamma_market(
            market_id="0xtest",
            best_bid=None,
            best_ask=None,
            volume_24h=200.0,
        )

        result = await analyzer._filter_quality([market])

        # Should have used cached book, NOT called CLOB
        mock_clob.get_order_book.assert_not_called()
        assert len(result) == 1
