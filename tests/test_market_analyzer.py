"""Tests for MarketAnalyzer deduplication logic."""

from bot.agent.market_analyzer import MarketAnalyzer
from bot.polymarket.types import OrderSide, TradeSignal


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
