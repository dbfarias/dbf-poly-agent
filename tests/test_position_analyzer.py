"""Tests for LLM-based position exit analyzer."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from bot.utils.position_analyzer import analyze_position_for_exit


def _make_mock_anthropic(response_text: str):
    """Create a mock anthropic module with AsyncAnthropic that returns given text."""
    mock_content = MagicMock()
    mock_content.text = response_text

    mock_response = MagicMock()
    mock_response.content = [mock_content]

    mock_client = AsyncMock()
    mock_client.messages.create = AsyncMock(return_value=mock_response)

    mock_cls = MagicMock(return_value=mock_client)
    return mock_cls, mock_client


class TestAnalyzePositionForExit:

    @pytest.mark.asyncio
    async def test_no_api_key_returns_hold(self):
        with patch("bot.utils.position_analyzer.settings") as mock_settings:
            mock_settings.anthropic_api_key = ""
            verdict, confidence, reason = await analyze_position_for_exit(
                question="Will Bitcoin reach $100k?",
                outcome="Yes",
                avg_price=0.50,
                current_price=0.55,
                size=10,
                unrealized_pnl=0.50,
            )
            assert verdict == "HOLD"
            assert confidence == "Low"
            assert reason == ""

    @pytest.mark.asyncio
    async def test_exit_verdict_parsed(self):
        mock_cls, mock_client = _make_mock_anthropic(
            "VERDICT: EXIT\n"
            "CONFIDENCE: High\n"
            "REASON: Price momentum reversed, cut losses early."
        )

        with patch("bot.utils.position_analyzer.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            with patch.dict("sys.modules", {"anthropic": MagicMock(AsyncAnthropic=mock_cls)}):
                verdict, confidence, reason = await analyze_position_for_exit(
                    question="Will BTC hit $90k by March?",
                    outcome="Yes",
                    avg_price=0.70,
                    current_price=0.45,
                    size=20,
                    unrealized_pnl=-5.0,
                )
                assert verdict == "EXIT"
                assert confidence == "High"
                assert "momentum" in reason.lower()

    @pytest.mark.asyncio
    async def test_hold_verdict_parsed(self):
        mock_cls, _ = _make_mock_anthropic(
            "VERDICT: HOLD\n"
            "CONFIDENCE: Medium\n"
            "REASON: Market still has time to resolve favorably."
        )

        with patch("bot.utils.position_analyzer.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            with patch.dict("sys.modules", {"anthropic": MagicMock(AsyncAnthropic=mock_cls)}):
                verdict, confidence, reason = await analyze_position_for_exit(
                    question="Will CPI exceed 3%?",
                    outcome="Yes",
                    avg_price=0.55,
                    current_price=0.60,
                    size=15,
                    unrealized_pnl=0.75,
                    days_to_expiry=14.0,
                )
                assert verdict == "HOLD"
                assert confidence == "Medium"

    @pytest.mark.asyncio
    async def test_api_exception_returns_hold(self):
        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(side_effect=Exception("API timeout"))
        mock_cls = MagicMock(return_value=mock_client)

        with patch("bot.utils.position_analyzer.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            with patch.dict("sys.modules", {"anthropic": MagicMock(AsyncAnthropic=mock_cls)}):
                verdict, confidence, reason = await analyze_position_for_exit(
                    question="Test market",
                    outcome="Yes",
                    avg_price=0.50,
                    current_price=0.40,
                    size=10,
                    unrealized_pnl=-1.0,
                )
                assert verdict == "HOLD"
                assert confidence == "Low"
                assert reason == ""

    @pytest.mark.asyncio
    async def test_malformed_response_defaults(self):
        mock_cls, _ = _make_mock_anthropic(
            "I'm not sure what to do with this position."
        )

        with patch("bot.utils.position_analyzer.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            with patch.dict("sys.modules", {"anthropic": MagicMock(AsyncAnthropic=mock_cls)}):
                verdict, confidence, reason = await analyze_position_for_exit(
                    question="Test",
                    outcome="Yes",
                    avg_price=0.50,
                    current_price=0.50,
                    size=10,
                    unrealized_pnl=0.0,
                )
                assert verdict == "HOLD"
                assert confidence == "Low"
                assert reason == ""

    @pytest.mark.asyncio
    async def test_pnl_percentage_calculation(self):
        """Ensure PnL percentage is computed correctly in the prompt."""
        mock_cls, mock_client = _make_mock_anthropic(
            "VERDICT: HOLD\nCONFIDENCE: Low\nREASON: Flat."
        )

        with patch("bot.utils.position_analyzer.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            with patch.dict("sys.modules", {"anthropic": MagicMock(AsyncAnthropic=mock_cls)}):
                await analyze_position_for_exit(
                    question="Test",
                    outcome="Yes",
                    avg_price=0.50,
                    current_price=0.60,
                    size=10,
                    unrealized_pnl=1.0,
                )
                mock_client.messages.create.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_zero_cost_basis(self):
        """Zero cost basis should not crash (division by zero)."""
        mock_cls, _ = _make_mock_anthropic(
            "VERDICT: HOLD\nCONFIDENCE: Low\nREASON: No cost."
        )

        with patch("bot.utils.position_analyzer.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            with patch.dict("sys.modules", {"anthropic": MagicMock(AsyncAnthropic=mock_cls)}):
                verdict, confidence, reason = await analyze_position_for_exit(
                    question="Test",
                    outcome="Yes",
                    avg_price=0.0,
                    current_price=0.50,
                    size=10,
                    unrealized_pnl=0.0,
                )
                assert verdict == "HOLD"

    @pytest.mark.asyncio
    async def test_days_to_expiry_none(self):
        """days_to_expiry=None should show 'unknown' in prompt, not crash."""
        mock_cls, _ = _make_mock_anthropic(
            "VERDICT: EXIT\nCONFIDENCE: Low\nREASON: Unclear."
        )

        with patch("bot.utils.position_analyzer.settings") as mock_settings:
            mock_settings.anthropic_api_key = "test-key"
            with patch.dict("sys.modules", {"anthropic": MagicMock(AsyncAnthropic=mock_cls)}):
                verdict, _, _ = await analyze_position_for_exit(
                    question="Test",
                    outcome="Yes",
                    avg_price=0.50,
                    current_price=0.40,
                    size=10,
                    unrealized_pnl=-1.0,
                    days_to_expiry=None,
                )
                assert verdict == "EXIT"
