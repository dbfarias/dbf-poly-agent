"""Tests for PriceDivergenceStrategy."""

import json
from collections import deque
from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

import pytest

from bot.agent.strategies.price_divergence import (
    MAX_EDGE,
    MAX_HOLD_HOURS_CRYPTO,
    MAX_HOLD_HOURS_OTHER,
    MIN_EDGE,
    PRICE_HISTORY_MAXLEN,
    PriceDivergenceStrategy,
    _extract_price_threshold,
)
from bot.polymarket.types import GammaMarket
from bot.research.cache import ResearchCache
from bot.research.types import ResearchResult


def _make_market(
    market_id="m1",
    question="Will BTC be above $100,000?",
    yes_price=0.60,
    no_price=0.40,
    best_bid=0.59,
    best_ask=0.61,
    token_ids=None,
    volume_24h=500.0,
    end_date_iso="2026-03-10T00:00:00Z",
) -> GammaMarket:
    if token_ids is None:
        token_ids = ["tok_yes", "tok_no"]
    return GammaMarket(
        id=market_id,
        question=question,
        outcomePrices=json.dumps([str(yes_price), str(no_price)]),
        bestBid=best_bid,
        bestAsk=best_ask,
        clobTokenIds=json.dumps(token_ids),
        volume24hr=volume_24h,
        endDateIso=end_date_iso,
        outcomes='["Yes","No"]',
    )


def _make_research(
    market_id="m1",
    sentiment=0.65,
    confidence=0.80,
    crypto_prices=None,
) -> ResearchResult:
    return ResearchResult(
        market_id=market_id,
        keywords=("bitcoin",),
        news_items=(),
        sentiment_score=sentiment,
        confidence=confidence,
        research_multiplier=1.0,
        updated_at=datetime.now(timezone.utc),
        crypto_prices=crypto_prices or (),
    )


def _make_strategy(research_cache=None) -> PriceDivergenceStrategy:
    clob = MagicMock()
    gamma = MagicMock()
    cache = MagicMock()
    return PriceDivergenceStrategy(
        clob, gamma, cache, research_cache=research_cache,
    )


class TestInit:
    def test_name(self):
        strategy = _make_strategy()
        assert strategy.name == "price_divergence"

class TestPriceHistory:
    def test_adds_entries(self):
        strategy = _make_strategy()
        market = _make_market(best_bid=0.50)
        strategy._update_price_history([market])

        assert "m1" in strategy._price_history
        assert list(strategy._price_history["m1"]) == [0.50]

    def test_evicts_stale(self):
        strategy = _make_strategy()
        # Seed with a market
        strategy._price_history["old_market"] = deque([0.5], maxlen=PRICE_HISTORY_MAXLEN)

        # Update with a different market — "old_market" should be evicted
        market = _make_market(market_id="new_market", best_bid=0.60)
        strategy._update_price_history([market])

        assert "old_market" not in strategy._price_history
        assert "new_market" in strategy._price_history


class TestExtractPriceThreshold:
    def test_plain_number(self):
        assert _extract_price_threshold("above $100,000?") == 100_000.0

    def test_k_suffix(self):
        assert _extract_price_threshold("above $100k?") == 100_000.0

    def test_k_suffix_upper(self):
        assert _extract_price_threshold("above $100K?") == 100_000.0

    def test_decimal(self):
        assert _extract_price_threshold("above $3,400.50?") == 3400.50

    def test_no_match(self):
        assert _extract_price_threshold("no dollar amount here") is None

    def test_m_suffix(self):
        assert _extract_price_threshold("market cap $2M") == 2_000_000.0


class TestCryptoDivergence:
    def test_bullish_signal(self):
        """BTC at $105k, contract asks 'above $100k?' priced at 0.55 → BUY YES.

        distance_pct = +5%, estimated_prob = 0.5 + min(0.20, 0.25) = 0.70
        edge = 0.70 - 0.55 = 0.15
        """
        rc = ResearchCache()
        research = _make_research(
            crypto_prices=(("bitcoin", 105_000.0), ("ethereum", 3400.0)),
        )
        rc.set("m1", research)

        strategy = _make_strategy(research_cache=rc)
        market = _make_market(yes_price=0.55, no_price=0.45)

        signal = strategy._detect_crypto_divergence(market)

        assert signal is not None
        assert signal.outcome == "Yes"
        assert signal.edge > MIN_EDGE
        assert signal.metadata["divergence_type"] == "crypto"
        assert signal.metadata["coin_id"] == "bitcoin"

    def test_no_signal_when_aligned(self):
        """BTC at $100.5k, 'above $100k?' priced at 0.55 — edge too small."""
        rc = ResearchCache()
        research = _make_research(
            crypto_prices=(("bitcoin", 100_500.0),),
        )
        rc.set("m1", research)

        strategy = _make_strategy(research_cache=rc)
        # Price already close to estimated prob → no edge
        market = _make_market(yes_price=0.55, no_price=0.45)

        signal = strategy._detect_crypto_divergence(market)

        # The estimated_prob would be ~0.55, close to yes_price 0.55 → edge ~0
        assert signal is None

    def test_bearish_signal(self):
        """BTC at $99k, contract asks 'above $100k?' priced at 0.52 → BUY NO.

        distance_pct = -0.01, estimated_prob = 0.40
        edge = (1 - 0.40) - 0.48 = 0.12 (within MIN_EDGE..MAX_EDGE)
        """
        rc = ResearchCache()
        research = _make_research(
            crypto_prices=(("bitcoin", 99_000.0),),
        )
        rc.set("m1", research)

        strategy = _make_strategy(research_cache=rc)
        market = _make_market(yes_price=0.52, no_price=0.48)

        signal = strategy._detect_crypto_divergence(market)

        assert signal is not None
        assert signal.outcome == "No"
        assert MIN_EDGE < signal.edge < MAX_EDGE

    def test_no_research_returns_none(self):
        strategy = _make_strategy(research_cache=None)
        market = _make_market()
        assert strategy._detect_crypto_divergence(market) is None


class TestSentimentDivergence:
    def test_divergence_detected(self):
        """Positive sentiment + falling price → BUY YES."""
        rc = ResearchCache()
        research = _make_research(sentiment=0.95, confidence=0.95)
        rc.set("m1", research)

        strategy = _make_strategy(research_cache=rc)
        # Seed sharply falling price history → strong negative trend
        strategy._price_history["m1"] = deque(
            [0.80, 0.68, 0.55, 0.42, 0.30], maxlen=PRICE_HISTORY_MAXLEN
        )

        market = _make_market(
            question="Will event X happen?",
            yes_price=0.45,
            no_price=0.55,
        )

        signal = strategy._detect_sentiment_divergence(market)

        assert signal is not None
        assert signal.outcome == "Yes"
        assert signal.metadata["divergence_type"] == "sentiment"

    def test_no_divergence_when_aligned(self):
        """Positive sentiment + rising price → same direction → no signal."""
        rc = ResearchCache()
        research = _make_research(sentiment=0.65, confidence=0.80)
        rc.set("m1", research)

        strategy = _make_strategy(research_cache=rc)
        # Rising prices → positive trend → same direction as positive sentiment
        strategy._price_history["m1"] = deque(
            [0.50, 0.52, 0.54, 0.56, 0.58], maxlen=PRICE_HISTORY_MAXLEN
        )

        market = _make_market(
            question="Will event X happen?",
            yes_price=0.58,
            no_price=0.42,
        )

        signal = strategy._detect_sentiment_divergence(market)
        assert signal is None

    def test_no_research_returns_none(self):
        strategy = _make_strategy(research_cache=ResearchCache())
        market = _make_market(question="Will event X happen?")
        assert strategy._detect_sentiment_divergence(market) is None


class TestScan:
    @pytest.mark.asyncio
    async def test_returns_sorted_signals(self):
        """Signals should be sorted by edge (descending).

        m1: BTC at $103k, threshold $100k → est_prob=0.65, edge=0.10
        m2: BTC at $102k, threshold $100k → est_prob=0.60, edge=0.05
        """
        rc = ResearchCache()
        r1 = _make_research(
            market_id="m1",
            crypto_prices=(("bitcoin", 103_000.0),),
        )
        r2 = _make_research(
            market_id="m2",
            crypto_prices=(("bitcoin", 102_000.0),),
        )
        rc.set("m1", r1)
        rc.set("m2", r2)

        strategy = _make_strategy(research_cache=rc)

        m1 = _make_market(
            market_id="m1", question="BTC above $100k?",
            yes_price=0.55, best_bid=0.54, best_ask=0.55,
        )
        m2 = _make_market(
            market_id="m2", question="BTC above $100k?",
            yes_price=0.55, best_bid=0.54, best_ask=0.55,
        )

        signals = await strategy.scan([m1, m2])

        assert len(signals) >= 1
        # Should be sorted by edge descending
        for i in range(len(signals) - 1):
            assert signals[i].edge >= signals[i + 1].edge

    @pytest.mark.asyncio
    async def test_filters_invalid_markets(self):
        """Markets without token IDs or with extreme prices should be filtered."""
        strategy = _make_strategy(research_cache=ResearchCache())

        # No token IDs — use empty clob_token_ids string
        m1 = GammaMarket(
            id="m1",
            question="test",
            clobTokenIds="",
            bestBid=0.50,
            bestAsk=0.52,
            outcomePrices='["0.50","0.50"]',
            outcomes='["Yes","No"]',
        )
        # Price too high (> MAX_PRICE 0.90)
        m2 = _make_market(market_id="m2", yes_price=0.95, no_price=0.05)

        signals = await strategy.scan([m1, m2])
        assert len(signals) == 0


class TestWordBoundaryMatching:
    """Tests for word-boundary matching that prevents 'MegaETH' → 'eth'."""

    def test_megaeth_not_crypto(self):
        strategy = _make_strategy()
        assert strategy._is_crypto_market("Will MegaETH launch this month?") is False

    def test_eth_is_crypto(self):
        strategy = _make_strategy()
        assert strategy._is_crypto_market("Will ETH be above $3,400?") is True

    def test_bitcoin_is_crypto(self):
        strategy = _make_strategy()
        assert strategy._is_crypto_market("Will bitcoin reach $100k?") is True

    def test_embedded_keyword_not_crypto(self):
        strategy = _make_strategy()
        assert strategy._is_crypto_market("Will MyBitcoinToken moon?") is False

    def test_parse_rejects_megaeth(self):
        """_extract_crypto_target must not match 'MegaETH' as ethereum."""
        strategy = _make_strategy()
        coin_id, threshold = strategy._extract_crypto_target(
            "Will MegaETH reach $5?"
        )
        assert coin_id is None

    def test_parse_matches_eth(self):
        strategy = _make_strategy()
        coin_id, threshold = strategy._extract_crypto_target(
            "Will ETH be above $3,400?"
        )
        assert coin_id == "ethereum"
        assert threshold == 3400.0


class TestMaxEdgeRejection:
    """Signals with edge > MAX_EDGE (15%) are rejected as likely parse errors."""

    def test_absurd_edge_rejected(self):
        """BTC at $50k, threshold $100k → huge divergence → rejected."""
        rc = ResearchCache()
        research = _make_research(
            crypto_prices=(("bitcoin", 50_000.0),),
        )
        rc.set("m1", research)

        strategy = _make_strategy(research_cache=rc)
        market = _make_market(yes_price=0.60, no_price=0.40)

        signal = strategy._detect_crypto_divergence(market)
        assert signal is None

    def test_edge_within_range_accepted(self):
        """BTC at $104k, threshold $100k → moderate edge → accepted."""
        rc = ResearchCache()
        research = _make_research(
            crypto_prices=(("bitcoin", 104_000.0),),
        )
        rc.set("m1", research)

        strategy = _make_strategy(research_cache=rc)
        market = _make_market(yes_price=0.55, no_price=0.45)

        signal = strategy._detect_crypto_divergence(market)
        assert signal is not None
        assert signal.edge < MAX_EDGE


class TestExtractPriceThresholdExtended:
    """Tests for B/T suffix support in _extract_price_threshold."""

    def test_b_suffix_lower(self):
        assert _extract_price_threshold("market cap $2b") == 2_000_000_000.0

    def test_b_suffix_upper(self):
        assert _extract_price_threshold("market cap $2B") == 2_000_000_000.0

    def test_t_suffix(self):
        assert _extract_price_threshold("market cap $1T") == 1_000_000_000_000.0

    def test_decimal_with_b(self):
        assert _extract_price_threshold("above $1.5B") == 1_500_000_000.0


class TestShouldExit:
    @pytest.mark.asyncio
    async def test_take_profit_triggers(self):
        strategy = _make_strategy()
        # 2%+ above avg → take profit
        result = await strategy.should_exit("m1", current_price=0.613, avg_price=0.60)
        assert result is True

    @pytest.mark.asyncio
    async def test_stop_loss_triggers(self):
        strategy = _make_strategy()
        # 1.5%+ below avg price → stop loss
        result = await strategy.should_exit("m1", current_price=0.591, avg_price=0.60)
        assert result is True

    @pytest.mark.asyncio
    async def test_no_exit_within_range(self):
        strategy = _make_strategy()
        # Price within TP/SL range
        result = await strategy.should_exit(
            "m1", current_price=0.601, avg_price=0.60
        )
        assert result is False

    @pytest.mark.asyncio
    async def test_time_expiry_crypto(self):
        strategy = _make_strategy()
        created = datetime.now(timezone.utc) - timedelta(hours=MAX_HOLD_HOURS_CRYPTO + 1)
        result = await strategy.should_exit(
            "m1",
            current_price=0.60,
            avg_price=0.60,
            created_at=created,
            question="Will BTC be above $100k?",
        )
        assert result is True

    @pytest.mark.asyncio
    async def test_time_expiry_non_crypto(self):
        strategy = _make_strategy()
        # Held for 25 hours (> MAX_HOLD_HOURS_OTHER=24)
        created = datetime.now(timezone.utc) - timedelta(hours=MAX_HOLD_HOURS_OTHER + 1)
        result = await strategy.should_exit(
            "m1",
            current_price=0.60,
            avg_price=0.60,
            created_at=created,
            question="Will event X happen?",
        )
        assert result is True


class TestCryptoOnlyEvaluation:
    """_evaluate_market only produces signals for crypto markets (no sentiment fallback)."""

    def test_non_crypto_returns_none(self):
        """Non-crypto markets return None (no sentiment divergence fallback)."""
        rc = ResearchCache()
        research = _make_research(sentiment=0.90, confidence=0.80)
        rc.set("m1", research)

        strategy = _make_strategy(research_cache=rc)
        # Seed price history for sentiment divergence (would have triggered before)
        strategy._price_history["m1"] = deque(
            [0.65, 0.60, 0.55, 0.50, 0.45], maxlen=PRICE_HISTORY_MAXLEN,
        )

        market = _make_market(
            question="Will event X happen?",
            yes_price=0.45,
            no_price=0.55,
        )
        now = datetime.now(timezone.utc)

        result = strategy._evaluate_market(market, now)
        assert result is None

    def test_crypto_market_evaluated(self):
        """Crypto markets are still evaluated normally."""
        rc = ResearchCache()
        research = _make_research(
            sentiment=0.65,
            confidence=0.80,
            crypto_prices=(("bitcoin", 105000.0),),
        )
        rc.set("m1", research)

        strategy = _make_strategy(research_cache=rc)
        market = _make_market(
            question="Will BTC be above $100,000?",
            yes_price=0.60,
            no_price=0.40,
            best_bid=0.595,
            best_ask=0.605,
        )
        now = datetime.now(timezone.utc)

        result = strategy._evaluate_market(market, now)
        # Should produce a signal (crypto divergence: actual $105k vs threshold $100k)
        assert result is not None
        assert result.metadata["divergence_type"] == "crypto"
