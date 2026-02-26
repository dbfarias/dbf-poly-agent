"""Tests for daily market report generation."""

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from bot.research.market_report import generate_daily_report


@dataclass(frozen=True)
class _FakeResearchResult:
    market_id: str
    sentiment_score: float
    confidence: float
    market_category: str = ""


@dataclass
class _FakePosition:
    market_id: str
    category: str = "unknown"


class _FakeStrategyStats:
    def __init__(self, strategy, category, total_trades, winning_trades, total_pnl):
        self.strategy = strategy
        self.category = category
        self.total_trades = total_trades
        self.winning_trades = winning_trades
        self.total_pnl = total_pnl


def _make_mocks(
    equity=100.0,
    day_start=95.0,
    positions=None,
    research_results=None,
    stats=None,
):
    """Build mock objects for generate_daily_report."""
    portfolio = MagicMock()
    portfolio.get_overview.return_value = {
        "total_equity": equity,
        "day_start_equity": day_start,
    }
    portfolio.positions = positions or []

    research_cache = MagicMock()
    research_cache.get_all.return_value = research_results or []

    learner = MagicMock()
    learner._stats = stats or {}
    learner._paused_strategies = {}

    research_engine = MagicMock()
    market_cache = MagicMock()
    market_cache.get_market.return_value = None
    research_engine.market_cache = market_cache

    return research_cache, portfolio, learner, research_engine


class TestReportGeneration:
    """Test report content and structure."""

    @pytest.mark.asyncio
    async def test_basic_report_has_all_sections(self):
        rc, portfolio, learner, re_ = _make_mocks()
        report = await generate_daily_report(rc, portfolio, learner, re_)

        assert "<b>Portfolio Summary</b>" in report
        assert "<b>Top 5 Markets</b>" in report
        assert "<b>Strategy Health</b>" in report
        assert "<b>Risk Alerts</b>" in report

    @pytest.mark.asyncio
    async def test_portfolio_summary_values(self):
        rc, portfolio, learner, re_ = _make_mocks(equity=105.0, day_start=100.0)
        report = await generate_daily_report(rc, portfolio, learner, re_)

        assert "$105.00" in report
        assert "$+5.00" in report
        assert "+5.0%" in report

    @pytest.mark.asyncio
    async def test_top_markets_sorted_by_sentiment(self):
        results = [
            _FakeResearchResult("m1", 0.1, 0.5, "politics"),
            _FakeResearchResult("m2", -0.8, 0.9, "crypto"),
            _FakeResearchResult("m3", 0.5, 0.7, "economics"),
        ]
        rc, portfolio, learner, re_ = _make_mocks(research_results=results)
        report = await generate_daily_report(rc, portfolio, learner, re_)

        # m2 (|0.8|) should appear before m3 (|0.5|) before m1 (|0.1|)
        idx_m2 = report.find("-0.80")
        idx_m3 = report.find("+0.50")
        assert idx_m2 < idx_m3

    @pytest.mark.asyncio
    async def test_strategy_health_shows_stats(self):
        stats = {
            ("value_betting", "crypto"): _FakeStrategyStats(
                "value_betting", "crypto", 10, 6, 2.50,
            ),
            ("arbitrage", "politics"): _FakeStrategyStats(
                "arbitrage", "politics", 5, 3, 1.00,
            ),
        }
        rc, portfolio, learner, re_ = _make_mocks(stats=stats)
        report = await generate_daily_report(rc, portfolio, learner, re_)

        assert "value_betting" in report
        assert "arbitrage" in report
        assert "60%" in report  # 6/10 win rate
        assert "$+2.50" in report

    @pytest.mark.asyncio
    async def test_risk_alert_category_concentration(self):
        positions = [
            _FakePosition("m1", "crypto"),
            _FakePosition("m2", "crypto"),
            _FakePosition("m3", "crypto"),
            _FakePosition("m4", "crypto"),
        ]
        rc, portfolio, learner, re_ = _make_mocks(positions=positions)
        report = await generate_daily_report(rc, portfolio, learner, re_)

        assert "crypto" in report
        assert "4 positions" in report

    @pytest.mark.asyncio
    async def test_risk_alert_daily_pnl_below_threshold(self):
        rc, portfolio, learner, re_ = _make_mocks(equity=95.0, day_start=100.0)
        report = await generate_daily_report(rc, portfolio, learner, re_)

        assert "-1% threshold" in report

    @pytest.mark.asyncio
    async def test_no_risk_alerts_when_healthy(self):
        rc, portfolio, learner, re_ = _make_mocks(equity=101.0, day_start=100.0)
        report = await generate_daily_report(rc, portfolio, learner, re_)

        assert "No alerts." in report


class TestHTMLFormatting:
    """Test Telegram HTML formatting."""

    @pytest.mark.asyncio
    async def test_uses_html_bold_tags(self):
        rc, portfolio, learner, re_ = _make_mocks()
        report = await generate_daily_report(rc, portfolio, learner, re_)

        assert "<b>" in report
        assert "</b>" in report

    @pytest.mark.asyncio
    async def test_uses_code_tags_for_numbers(self):
        rc, portfolio, learner, re_ = _make_mocks()
        report = await generate_daily_report(rc, portfolio, learner, re_)

        assert "<code>" in report

    @pytest.mark.asyncio
    async def test_report_under_4000_chars(self):
        """Reports should always fit within Telegram's limit."""
        # Create many results to stress the length
        results = [
            _FakeResearchResult(f"m{i}", 0.5, 0.8, "other")
            for i in range(50)
        ]
        rc, portfolio, learner, re_ = _make_mocks(research_results=results)
        report = await generate_daily_report(rc, portfolio, learner, re_)

        assert len(report) <= 4000


class TestEdgeCases:
    """Test edge cases and error handling."""

    @pytest.mark.asyncio
    async def test_empty_research_cache(self):
        rc, portfolio, learner, re_ = _make_mocks(research_results=[])
        report = await generate_daily_report(rc, portfolio, learner, re_)

        assert "No research data yet." in report

    @pytest.mark.asyncio
    async def test_empty_strategy_stats(self):
        rc, portfolio, learner, re_ = _make_mocks(stats={})
        report = await generate_daily_report(rc, portfolio, learner, re_)

        assert "No strategy data yet." in report

    @pytest.mark.asyncio
    async def test_zero_day_start_equity(self):
        """Should not divide by zero when day_start is 0."""
        rc, portfolio, learner, re_ = _make_mocks(equity=0.0, day_start=0.0)
        report = await generate_daily_report(rc, portfolio, learner, re_)

        assert "Portfolio Summary" in report
