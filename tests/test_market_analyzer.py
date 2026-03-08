"""Tests for MarketAnalyzer deduplication, stop-loss, and quality filter logic."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from bot.agent.market_analyzer import MarketAnalyzer, classify_market_type, normalize_category
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
    created_at: datetime | None = None,
    question: str = "Will X happen?",
):
    return SimpleNamespace(
        market_id=market_id,
        strategy=strategy,
        avg_price=avg_price,
        current_price=current_price,
        created_at=created_at,
        question=question,
    )


class TestCheckStopLoss:
    def setup_method(self):
        self.analyzer = MarketAnalyzer.__new__(MarketAnalyzer)

    def test_near_worthless_triggers_exit(self):
        pos = _position(current_price=0.05, avg_price=0.90)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=True)
        assert reason is not None
        assert "near_worthless" in reason

    def test_15pct_loss_triggers_exit(self):
        pos = _position(avg_price=0.50, current_price=0.42)  # 16% loss
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=True)
        assert reason is not None
        assert "stop_loss" in reason

    def test_14pct_loss_no_exit(self):
        pos = _position(avg_price=0.50, current_price=0.44)  # 12% loss
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=True)
        assert reason is None

    def test_unmatched_strategy_below_default_threshold(self):
        pos = _position(strategy="external", avg_price=0.80, current_price=0.65)  # ~19% loss, hits stop_loss
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=False)
        assert reason is not None

    def test_unmatched_strategy_above_threshold_no_exit(self):
        pos = _position(strategy="external", avg_price=0.80, current_price=0.75)  # ~6% loss
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=False)
        assert reason is None

    def test_matched_strategy_no_stop_loss_when_healthy(self):
        pos = _position(avg_price=0.95, current_price=0.92)  # ~3% loss, below 15%
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=True)
        assert reason is None

    def test_real_case_external_58pct_loss(self):
        """Real scenario: position bought at $0.396, now $0.165 (58% loss)."""
        pos = _position(strategy="external", avg_price=0.396, current_price=0.165)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=False)
        assert reason is not None
        assert "stop_loss" in reason

    # --- Max position age (72h) ---

    def test_max_age_triggers_exit_after_3_days(self):
        old_time = datetime.now(timezone.utc) - timedelta(hours=73)
        pos = _position(avg_price=0.90, current_price=0.88, created_at=old_time)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=True)
        assert reason is not None
        assert "max_age" in reason

    def test_no_max_age_exit_within_3_days(self):
        recent_time = datetime.now(timezone.utc) - timedelta(hours=71)
        pos = _position(avg_price=0.90, current_price=0.88, created_at=recent_time)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=True)
        assert reason is None

    def test_no_max_age_exit_without_created_at(self):
        pos = _position(avg_price=0.90, current_price=0.88, created_at=None)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=True)
        assert reason is None

    # --- Take profit (universal at $0.95) ---

    def test_take_profit_at_95_after_6h(self):
        old_time = datetime.now(timezone.utc) - timedelta(hours=8)
        pos = _position(avg_price=0.90, current_price=0.95, created_at=old_time)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=True)
        assert reason is not None
        assert "take_profit" in reason

    def test_no_take_profit_below_95(self):
        old_time = datetime.now(timezone.utc) - timedelta(hours=8)
        pos = _position(avg_price=0.90, current_price=0.94, created_at=old_time)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=True)
        assert reason is None

    def test_no_take_profit_within_6h(self):
        recent_time = datetime.now(timezone.utc) - timedelta(hours=3)
        pos = _position(avg_price=0.90, current_price=0.96, created_at=recent_time)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=True)
        assert reason is None

    def test_no_take_profit_without_created_at(self):
        pos = _position(avg_price=0.90, current_price=0.96, created_at=None)
        reason = self.analyzer._check_stop_loss(pos, strategy_matched=True)
        assert reason is None

    # --- Updated constants ---

    def test_max_position_age_is_72h(self):
        assert MarketAnalyzer.MAX_POSITION_AGE_HOURS == 72.0

    def test_take_profit_price_is_095(self):
        assert MarketAnalyzer.TAKE_PROFIT_PRICE == 0.95


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
        assert MarketAnalyzer.MIN_VOLUME_24H == 150.0

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


class TestScanMarketsShortTermMerge:
    """Test that scan_markets merges short-term markets with active markets."""

    @pytest.mark.asyncio
    async def test_short_term_markets_merged_and_deduped(self):
        """Short-term markets should be merged without duplicates."""
        cache = MarketCache(default_ttl=60)
        gamma = AsyncMock()

        # Active markets return one market
        active_market = _make_gamma_market(market_id="0xactive")
        gamma.get_active_markets.return_value = [active_market]

        # Short-term returns one new + one duplicate
        short_new = _make_gamma_market(market_id="0xshort")
        short_dup = _make_gamma_market(market_id="0xactive")  # duplicate
        gamma.get_short_term_markets.return_value = [short_new, short_dup]

        mock_strategy = AsyncMock()
        mock_strategy.name = "value_betting"
        mock_strategy.is_enabled_for_tier.return_value = True
        mock_strategy.scan.return_value = []

        analyzer = MarketAnalyzer(gamma, cache, [mock_strategy])

        from bot.config import CapitalTier
        await analyzer.scan_markets(CapitalTier.TIER1)

        # Strategy.scan should have been called with merged list (2 unique markets)
        call_args = mock_strategy.scan.call_args
        markets_passed = call_args[0][0]
        market_ids = [m.id for m in markets_passed]
        assert "0xactive" in market_ids
        assert "0xshort" in market_ids
        # No duplicates
        assert len(market_ids) == len(set(market_ids))

    @pytest.mark.asyncio
    async def test_short_term_failure_does_not_break_scan(self):
        """If short-term fetch fails, scan should still work with active markets."""
        cache = MarketCache(default_ttl=60)
        gamma = AsyncMock()

        active_market = _make_gamma_market(market_id="0xactive")
        gamma.get_active_markets.return_value = [active_market]
        gamma.get_short_term_markets.side_effect = RuntimeError("API down")

        mock_strategy = AsyncMock()
        mock_strategy.name = "time_decay"
        mock_strategy.is_enabled_for_tier.return_value = True
        mock_strategy.scan.return_value = []

        analyzer = MarketAnalyzer(gamma, cache, [mock_strategy])

        from bot.config import CapitalTier
        # Should not raise
        signals = await analyzer.scan_markets(CapitalTier.TIER1)
        assert isinstance(signals, list)


class TestCheckExitsQuestionKwarg:
    """Verify check_exits passes question kwarg to strategy.should_exit."""

    @pytest.mark.asyncio
    async def test_check_exits_passes_question_to_should_exit(self):
        mock_strategy = AsyncMock()
        mock_strategy.name = "price_divergence"
        mock_strategy.should_exit.return_value = False

        cache = MarketCache(default_ttl=60)
        analyzer = MarketAnalyzer(
            gamma_client=MagicMock(),
            cache=cache,
            strategies=[mock_strategy],
        )

        created = datetime.now(timezone.utc) - timedelta(hours=10)
        pos = _position(
            strategy="price_divergence",
            avg_price=0.80,
            current_price=0.82,
            created_at=created,
            question="Will Bitcoin hit $100k?",
        )

        from bot.config import CapitalTier
        await analyzer.check_exits([pos], CapitalTier.TIER1)

        mock_strategy.should_exit.assert_called_once()
        call_kwargs = mock_strategy.should_exit.call_args
        assert call_kwargs.kwargs.get("question") == "Will Bitcoin hit $100k?"


class TestCheckExitsReturnFormat:
    """Verify check_exits returns list[tuple[str, str]] with exit reasons."""

    @pytest.mark.asyncio
    async def test_strategy_exit_returns_tuple_with_name_exit(self):
        """Strategy returning True → (market_id, '{name}_exit')."""
        mock_strategy = AsyncMock()
        mock_strategy.name = "time_decay"
        mock_strategy.should_exit.return_value = True

        cache = MarketCache(default_ttl=60)
        analyzer = MarketAnalyzer(
            gamma_client=MagicMock(), cache=cache, strategies=[mock_strategy],
        )

        pos = _position(strategy="time_decay", avg_price=0.80, current_price=0.85)

        from bot.config import CapitalTier
        exits = await analyzer.check_exits([pos], CapitalTier.TIER1)

        assert len(exits) == 1
        market_id, reason = exits[0]
        assert market_id == "mkt1"
        assert reason == "time_decay_exit"

    @pytest.mark.asyncio
    async def test_strategy_exit_returns_string_reason(self):
        """Strategy returning a string → that string used as reason."""
        mock_strategy = AsyncMock()
        mock_strategy.name = "value_betting"
        mock_strategy.should_exit.return_value = "vb_take_profit_3pct"

        cache = MarketCache(default_ttl=60)
        analyzer = MarketAnalyzer(
            gamma_client=MagicMock(), cache=cache, strategies=[mock_strategy],
        )

        pos = _position(strategy="value_betting", avg_price=0.80, current_price=0.85)

        from bot.config import CapitalTier
        exits = await analyzer.check_exits([pos], CapitalTier.TIER1)

        assert len(exits) == 1
        _, reason = exits[0]
        assert reason == "vb_take_profit_3pct"

    @pytest.mark.asyncio
    async def test_stop_loss_exit_returns_reason_from_check_stop_loss(self):
        """Stop-loss exit → reason string from _check_stop_loss."""
        mock_strategy = AsyncMock()
        mock_strategy.name = "time_decay"
        mock_strategy.should_exit.return_value = False

        cache = MarketCache(default_ttl=60)
        analyzer = MarketAnalyzer(
            gamma_client=MagicMock(), cache=cache, strategies=[mock_strategy],
        )

        # Price dropped 20% → triggers 15% stop-loss
        created = datetime.now(timezone.utc) - timedelta(hours=2)
        pos = _position(
            strategy="time_decay", avg_price=0.80, current_price=0.60,
            created_at=created,
        )

        from bot.config import CapitalTier
        exits = await analyzer.check_exits([pos], CapitalTier.TIER1)

        assert len(exits) == 1
        _, reason = exits[0]
        assert "stop_loss" in reason

    @pytest.mark.asyncio
    async def test_no_exit_returns_empty_list(self):
        """No exit conditions met → empty list."""
        mock_strategy = AsyncMock()
        mock_strategy.name = "time_decay"
        mock_strategy.should_exit.return_value = False

        cache = MarketCache(default_ttl=60)
        analyzer = MarketAnalyzer(
            gamma_client=MagicMock(), cache=cache, strategies=[mock_strategy],
        )

        created = datetime.now(timezone.utc) - timedelta(hours=2)
        pos = _position(
            strategy="time_decay", avg_price=0.80, current_price=0.79,
            created_at=created,
        )

        from bot.config import CapitalTier
        exits = await analyzer.check_exits([pos], CapitalTier.TIER1)

        assert exits == []


class TestDisabledStrategies:
    @pytest.mark.asyncio
    async def test_disabled_strategy_skipped_in_scan(self):
        """Disabled strategies should not produce signals."""
        mock_strategy = AsyncMock()
        mock_strategy.name = "time_decay"
        mock_strategy.is_enabled_for_tier.return_value = True
        mock_strategy.scan.return_value = [_signal()]

        cache = MarketCache(default_ttl=60)
        gamma = AsyncMock()
        gamma.get_active_markets.return_value = []

        analyzer = MarketAnalyzer(gamma, cache, [mock_strategy])
        analyzer.disabled_strategies = {"time_decay"}

        from bot.config import CapitalTier
        signals = await analyzer.scan_markets(CapitalTier.TIER1)

        # Strategy was disabled → scan should NOT be called
        mock_strategy.scan.assert_not_called()
        assert signals == []

    @pytest.mark.asyncio
    async def test_enabled_strategy_runs_normally(self):
        """Non-disabled strategies should produce signals."""
        mock_strategy = AsyncMock()
        mock_strategy.name = "time_decay"
        mock_strategy.is_enabled_for_tier.return_value = True
        mock_strategy.scan.return_value = [_signal()]

        cache = MarketCache(default_ttl=60)
        gamma = AsyncMock()
        gamma.get_active_markets.return_value = []

        analyzer = MarketAnalyzer(gamma, cache, [mock_strategy])
        analyzer.disabled_strategies = set()  # nothing disabled

        from bot.config import CapitalTier
        signals = await analyzer.scan_markets(CapitalTier.TIER1)

        mock_strategy.scan.assert_called_once()
        assert len(signals) == 1


# ---------------------------------------------------------------------------
# _merge_markets helper
# ---------------------------------------------------------------------------


class TestMergeMarkets:
    def test_adds_new_markets(self):
        m1 = _make_gamma_market(market_id="0x1")
        m2 = _make_gamma_market(market_id="0x2")
        markets = [m1]
        existing_ids = {m1.id}

        added = MarketAnalyzer._merge_markets(markets, existing_ids, [m2])

        assert added == 1
        assert len(markets) == 2
        assert "0x2" in existing_ids

    def test_skips_duplicates(self):
        m1 = _make_gamma_market(market_id="0x1")
        dup = _make_gamma_market(market_id="0x1")
        markets = [m1]
        existing_ids = {m1.id}

        added = MarketAnalyzer._merge_markets(markets, existing_ids, [dup])

        assert added == 0
        assert len(markets) == 1

    def test_mixed_new_and_dup(self):
        m1 = _make_gamma_market(market_id="0x1")
        m2 = _make_gamma_market(market_id="0x2")
        dup = _make_gamma_market(market_id="0x1")
        markets = [m1]
        existing_ids = {m1.id}

        added = MarketAnalyzer._merge_markets(markets, existing_ids, [m2, dup])

        assert added == 1
        assert len(markets) == 2

    def test_empty_new_markets(self):
        m1 = _make_gamma_market(market_id="0x1")
        markets = [m1]
        existing_ids = {m1.id}

        added = MarketAnalyzer._merge_markets(markets, existing_ids, [])

        assert added == 0
        assert len(markets) == 1


# ---------------------------------------------------------------------------
# scan_markets: new/trending/breaking market source merging
# ---------------------------------------------------------------------------


class TestScanMarketsNewSources:
    @pytest.mark.asyncio
    async def test_all_sources_merged_without_duplicates(self):
        """All 5 sources should be merged; duplicates across sources removed."""
        cache = MarketCache(default_ttl=60)
        gamma = AsyncMock()

        active = _make_gamma_market(market_id="0xactive")
        short = _make_gamma_market(market_id="0xshort")
        new = _make_gamma_market(market_id="0xnew")
        trending = _make_gamma_market(market_id="0xtrend")
        breaking = _make_gamma_market(market_id="0xbreak")
        dup_active = _make_gamma_market(market_id="0xactive")  # duplicate

        gamma.get_active_markets.return_value = [active]
        gamma.get_short_term_markets.return_value = [short]
        gamma.get_new_markets.return_value = [new, dup_active]
        gamma.get_trending_markets.return_value = [trending]
        gamma.get_breaking_markets.return_value = [breaking]

        mock_strategy = AsyncMock()
        mock_strategy.name = "value_betting"
        mock_strategy.is_enabled_for_tier.return_value = True
        mock_strategy.scan.return_value = []

        analyzer = MarketAnalyzer(gamma, cache, [mock_strategy])

        from bot.config import CapitalTier
        await analyzer.scan_markets(CapitalTier.TIER1)

        markets_passed = mock_strategy.scan.call_args[0][0]
        ids = [m.id for m in markets_passed]
        assert len(ids) == len(set(ids)), "Duplicates found"
        assert set(ids) == {"0xactive", "0xshort", "0xnew", "0xtrend", "0xbreak"}

    @pytest.mark.asyncio
    async def test_new_markets_failure_does_not_break_scan(self):
        """If get_new_markets fails, scan still works with other sources."""
        cache = MarketCache(default_ttl=60)
        gamma = AsyncMock()

        gamma.get_active_markets.return_value = [_make_gamma_market(market_id="0x1")]
        gamma.get_short_term_markets.return_value = []
        gamma.get_new_markets.side_effect = RuntimeError("API down")
        gamma.get_trending_markets.return_value = []
        gamma.get_breaking_markets.return_value = []

        mock_strategy = AsyncMock()
        mock_strategy.name = "time_decay"
        mock_strategy.is_enabled_for_tier.return_value = True
        mock_strategy.scan.return_value = []

        analyzer = MarketAnalyzer(gamma, cache, [mock_strategy])

        from bot.config import CapitalTier
        signals = await analyzer.scan_markets(CapitalTier.TIER1)
        assert isinstance(signals, list)

    @pytest.mark.asyncio
    async def test_trending_failure_does_not_break_scan(self):
        """If get_trending_markets fails, scan still works."""
        cache = MarketCache(default_ttl=60)
        gamma = AsyncMock()

        gamma.get_active_markets.return_value = [_make_gamma_market(market_id="0x1")]
        gamma.get_short_term_markets.return_value = []
        gamma.get_new_markets.return_value = []
        gamma.get_trending_markets.side_effect = RuntimeError("timeout")
        gamma.get_breaking_markets.return_value = []

        mock_strategy = AsyncMock()
        mock_strategy.name = "time_decay"
        mock_strategy.is_enabled_for_tier.return_value = True
        mock_strategy.scan.return_value = []

        analyzer = MarketAnalyzer(gamma, cache, [mock_strategy])

        from bot.config import CapitalTier
        signals = await analyzer.scan_markets(CapitalTier.TIER1)
        assert isinstance(signals, list)

    @pytest.mark.asyncio
    async def test_breaking_failure_does_not_break_scan(self):
        """If get_breaking_markets fails, scan still works."""
        cache = MarketCache(default_ttl=60)
        gamma = AsyncMock()

        gamma.get_active_markets.return_value = [_make_gamma_market(market_id="0x1")]
        gamma.get_short_term_markets.return_value = []
        gamma.get_new_markets.return_value = []
        gamma.get_trending_markets.return_value = []
        gamma.get_breaking_markets.side_effect = RuntimeError("network")

        mock_strategy = AsyncMock()
        mock_strategy.name = "time_decay"
        mock_strategy.is_enabled_for_tier.return_value = True
        mock_strategy.scan.return_value = []

        analyzer = MarketAnalyzer(gamma, cache, [mock_strategy])

        from bot.config import CapitalTier
        signals = await analyzer.scan_markets(CapitalTier.TIER1)
        assert isinstance(signals, list)


class TestClassifyMarketType:
    """Tests for classify_market_type — sports, crypto, other detection."""

    def test_crypto_market(self):
        assert classify_market_type("Will Bitcoin be above $100k?") == "crypto"

    def test_crypto_eth(self):
        assert classify_market_type("Will ETH exceed $5,000?") == "crypto"

    def test_other_market(self):
        assert classify_market_type("Will Congress pass the budget?") == "other"

    # --- Expanded sports keyword coverage ---

    def test_nba_team_raptors(self):
        assert classify_market_type("Raptors O/U 220.5?") == "sports"

    def test_nba_team_nuggets(self):
        assert classify_market_type("Nuggets Spread -3.5?") == "sports"

    def test_nba_team_pelicans(self):
        assert classify_market_type("Pelicans Spread +5.5?") == "sports"

    def test_college_big_east(self):
        assert classify_market_type("UConn Big East tournament winner?") == "sports"

    def test_college_ncaa(self):
        assert classify_market_type("Who wins the NCAA championship?") == "sports"

    def test_college_march_madness(self):
        assert classify_market_type("March Madness Final Four winner?") == "sports"

    def test_college_uconn(self):
        assert classify_market_type("Will UConn win the national title?") == "sports"

    def test_tennis_antalya(self):
        assert classify_market_type("Antalya 2: Djokovic vs Nadal") == "sports"

    def test_tennis_wimbledon(self):
        assert classify_market_type("Who wins Wimbledon 2026?") == "sports"

    def test_tennis_atp(self):
        assert classify_market_type("ATP finals winner?") == "sports"

    def test_soccer_pumas_unam(self):
        assert classify_market_type("Pumas UNAM win on 2026-03-07?") == "sports"

    def test_mls_inter_miami(self):
        assert classify_market_type("Inter Miami win MLS Cup?") == "sports"

    def test_mls_la_galaxy(self):
        assert classify_market_type("Will LA Galaxy make playoffs?") == "sports"

    def test_betting_term_handicap(self):
        assert classify_market_type("Asian handicap Chelsea +1.5?") == "sports"

    def test_betting_term_point_spread(self):
        assert classify_market_type("Point spread for game?") == "sports"

    def test_championship_pattern(self):
        assert classify_market_type("Who wins the 2026 championship?") == "sports"

    def test_playoff_pattern(self):
        assert classify_market_type("Will they make the playoff?") == "sports"

    def test_semifinals_pattern(self):
        assert classify_market_type("Semifinals matchup predictions?") == "sports"

    def test_win_on_date_pattern(self):
        assert classify_market_type("Will Real Madrid win on 2026-03-10?") == "sports"

    def test_vs_pattern_catches_matchup(self):
        """'X vs Y' pattern catches sports matchups."""
        assert classify_market_type("Antalya 2: Player A vs Player B") == "sports"

    def test_vs_dot_pattern(self):
        assert classify_market_type("ATP: Federer vs. Nadal") == "sports"

    def test_vs_pattern_not_false_positive_crypto(self):
        """Crypto question should still classify as crypto first."""
        result = classify_market_type("Will Bitcoin beat $100k?")
        assert result == "crypto"

    def test_quarterback_pattern(self):
        assert classify_market_type("Most passing yards by a quarterback?") == "sports"

    def test_slam_dunk(self):
        assert classify_market_type("Most slam dunk contest wins?") == "sports"

    def test_college_duke(self):
        assert classify_market_type("Will Duke win March Madness?") == "sports"

    def test_santos_laguna(self):
        assert classify_market_type("Santos Laguna vs Toluca") == "sports"

    def test_grand_slam(self):
        assert classify_market_type("Grand Slam winner 2026?") == "sports"

    def test_politics_not_sports(self):
        """Political markets should not be classified as sports."""
        assert classify_market_type("Will Biden win the 2026 midterms?") == "other"

    def test_economics_not_sports(self):
        assert classify_market_type("Will inflation exceed 5%?") == "other"
