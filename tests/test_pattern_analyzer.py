"""Tests for PatternAnalyzer — historical pattern matching."""

from datetime import datetime, timedelta, timezone
from unittest.mock import AsyncMock, patch

import pytest

from bot.research.pattern_analyzer import (
    PatternAnalyzer,
    _extract_pattern_type,
    _jaccard,
    _tokenize,
)

# ---------- Pattern extraction tests ----------


class TestExtractPatternType:
    def test_price_target_reach(self):
        assert _extract_pattern_type("Will BTC reach $100,000?") == "price_target"

    def test_price_target_above(self):
        assert _extract_pattern_type("Will ETH be above $5,000 by March?") == "price_target"

    def test_price_target_hit(self):
        assert _extract_pattern_type("Will AAPL hit $200?") == "price_target"

    def test_price_target_exceed(self):
        assert _extract_pattern_type("Will GDP exceed $25 trillion?") == "price_target"

    def test_win_outcome(self):
        assert _extract_pattern_type("Will the Lakers win the championship?") == "win_outcome"

    def test_win_outcome_simple(self):
        assert _extract_pattern_type("Will Trump win the election?") == "win_outcome"

    def test_percentage(self):
        assert _extract_pattern_type("Will inflation be above 3.5%?") == "percentage"

    def test_percentage_below(self):
        assert _extract_pattern_type("Will unemployment fall below 4%?") == "percentage"

    def test_deadline_event_before(self):
        assert _extract_pattern_type("Will X happen before 2026?") == "deadline_event"

    def test_deadline_event_by(self):
        assert _extract_pattern_type("Will peace deal be reached by March 15?") == "deadline_event"

    def test_binary_event_fallback(self):
        assert _extract_pattern_type("Will there be a government shutdown?") == "binary_event"

    def test_binary_event_generic(self):
        assert _extract_pattern_type("Will the Fed cut rates?") == "binary_event"


# ---------- Tokenization tests ----------


class TestTokenize:
    def test_basic_tokenize(self):
        tokens = _tokenize("Will Bitcoin reach $100,000?")
        assert "bitcoin" in tokens
        assert "reach" in tokens
        assert "100" in tokens
        # Stop words removed
        assert "will" not in tokens

    def test_stop_words_removed(self):
        tokens = _tokenize("Will the market resolve to yes?")
        assert "the" not in tokens
        assert "will" not in tokens
        assert "market" not in tokens
        assert "resolve" not in tokens

    def test_short_words_removed(self):
        tokens = _tokenize("Is it ok to go?")
        assert "is" not in tokens
        assert "it" not in tokens
        assert "ok" not in tokens
        assert "to" not in tokens

    def test_empty_string(self):
        assert _tokenize("") == frozenset()


# ---------- Jaccard similarity tests ----------


class TestJaccard:
    def test_identical(self):
        a = frozenset({"bitcoin", "reach", "100000"})
        assert _jaccard(a, a) == 1.0

    def test_disjoint(self):
        a = frozenset({"bitcoin", "price"})
        b = frozenset({"election", "trump"})
        assert _jaccard(a, b) == 0.0

    def test_partial_overlap(self):
        a = frozenset({"bitcoin", "reach", "100000"})
        b = frozenset({"bitcoin", "reach", "50000"})
        # 2 / 4 = 0.5
        assert _jaccard(a, b) == 0.5

    def test_empty_sets(self):
        assert _jaccard(frozenset(), frozenset()) == 0.0
        assert _jaccard(frozenset({"a"}), frozenset()) == 0.0


# ---------- PatternAnalyzer.compute_base_rate tests ----------


def _make_trade(question: str, pnl: float, exit_reason: str = "max-age"):
    """Create a mock trade object with required fields."""
    from unittest.mock import MagicMock

    trade = MagicMock()
    trade.question = question
    trade.pnl = pnl
    trade.exit_reason = exit_reason
    trade.status = "filled"
    trade.created_at = datetime.now(timezone.utc) - timedelta(days=5)
    return trade


class TestComputeBaseRate:
    @pytest.mark.asyncio
    async def test_returns_none_with_insufficient_data(self):
        """Should return None when fewer than 5 similar trades exist."""
        trades = [
            _make_trade("Will BTC reach $100k?", pnl=0.5),
            _make_trade("Will BTC reach $90k?", pnl=-0.3),
        ]
        with patch(
            "bot.research.pattern_analyzer.async_session"
        ) as mock_session:
            mock_ctx = AsyncMock()
            mock_repo = AsyncMock()
            mock_repo.get_resolved_with_questions.return_value = trades
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "bot.research.pattern_analyzer.TradeRepository",
                return_value=mock_repo,
            ):
                analyzer = PatternAnalyzer()
                result = await analyzer.compute_base_rate("Will ETH reach $5000?")
                assert result is None

    @pytest.mark.asyncio
    async def test_returns_base_rate_with_enough_data(self):
        """Should return win rate when 5+ similar trades found."""
        # 5 price_target trades with similar keywords
        trades = [
            _make_trade("Will BTC reach $100k this year?", pnl=0.5),
            _make_trade("Will BTC reach $90k this quarter?", pnl=0.3),
            _make_trade("Will BTC reach $80k soon?", pnl=-0.2),
            _make_trade("Will BTC reach $120k this year?", pnl=0.4),
            _make_trade("Will BTC reach $75k this year?", pnl=-0.1),
        ]
        with patch(
            "bot.research.pattern_analyzer.async_session"
        ) as mock_session:
            mock_ctx = AsyncMock()
            mock_repo = AsyncMock()
            mock_repo.get_resolved_with_questions.return_value = trades
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "bot.research.pattern_analyzer.TradeRepository",
                return_value=mock_repo,
            ):
                analyzer = PatternAnalyzer()
                result = await analyzer.compute_base_rate(
                    "Will BTC reach $150k this year?"
                )
                assert result is not None
                # 3 wins out of 5 = 0.6
                assert result == pytest.approx(0.6, abs=0.01)

    @pytest.mark.asyncio
    async def test_cache_hit(self):
        """Second call should use cache, not hit DB again."""
        trades = [
            _make_trade("Will BTC reach $100k this year?", pnl=0.5),
            _make_trade("Will BTC reach $90k this quarter?", pnl=0.3),
            _make_trade("Will BTC reach $80k soon?", pnl=-0.2),
            _make_trade("Will BTC reach $120k this year?", pnl=0.4),
            _make_trade("Will BTC reach $75k this year?", pnl=-0.1),
        ]
        with patch(
            "bot.research.pattern_analyzer.async_session"
        ) as mock_session:
            mock_ctx = AsyncMock()
            mock_repo = AsyncMock()
            mock_repo.get_resolved_with_questions.return_value = trades
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "bot.research.pattern_analyzer.TradeRepository",
                return_value=mock_repo,
            ):
                analyzer = PatternAnalyzer()
                q = "Will BTC reach $150k this year?"
                r1 = await analyzer.compute_base_rate(q)
                r2 = await analyzer.compute_base_rate(q)
                assert r1 == r2
                # DB should only be hit once
                assert mock_repo.get_resolved_with_questions.call_count == 1

    @pytest.mark.asyncio
    async def test_different_pattern_types_not_matched(self):
        """win_outcome trades should not match price_target questions."""
        trades = [
            _make_trade("Will Lakers win the title?", pnl=0.5),
            _make_trade("Will Celtics win the title?", pnl=0.3),
            _make_trade("Will Warriors win the title?", pnl=-0.2),
            _make_trade("Will Nuggets win the title?", pnl=0.4),
            _make_trade("Will Heat win the title?", pnl=-0.1),
        ]
        with patch(
            "bot.research.pattern_analyzer.async_session"
        ) as mock_session:
            mock_ctx = AsyncMock()
            mock_repo = AsyncMock()
            mock_repo.get_resolved_with_questions.return_value = trades
            mock_session.return_value.__aenter__ = AsyncMock(return_value=mock_ctx)
            mock_session.return_value.__aexit__ = AsyncMock(return_value=False)

            with patch(
                "bot.research.pattern_analyzer.TradeRepository",
                return_value=mock_repo,
            ):
                analyzer = PatternAnalyzer()
                # This is a price_target question, should not match win_outcome trades
                result = await analyzer.compute_base_rate(
                    "Will BTC reach $200k?"
                )
                assert result is None


class TestRefreshPatterns:
    @pytest.mark.asyncio
    async def test_clears_cache(self):
        analyzer = PatternAnalyzer()
        analyzer._pattern_cache = {"test": 0.5}
        analyzer._cache_timestamps = {"test": 100.0}
        await analyzer.refresh_patterns()
        assert analyzer._pattern_cache == {}
        assert analyzer._cache_timestamps == {}
