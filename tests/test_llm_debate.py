"""Tests for LLM debate gate and position reviewer."""

from unittest.mock import AsyncMock, MagicMock, patch

from bot.research.llm_debate import (
    LlmCostTracker,
    _parse_challenger,
    _parse_proposer,
    _parse_reviewer,
    debate_signal,
    review_position,
)

_ANTHROPIC_PATCH = "anthropic.AsyncAnthropic"


class TestParseProposer:
    def test_buy_verdict(self):
        text = "VERDICT: BUY\nCONFIDENCE: 0.8\nREASONING: Strong edge"
        verdict, conf, reasoning = _parse_proposer(text)
        assert verdict == "BUY"
        assert conf == 0.8
        assert reasoning == "Strong edge"

    def test_pass_verdict(self):
        text = "VERDICT: PASS\nCONFIDENCE: 0.3\nREASONING: Too risky"
        verdict, conf, reasoning = _parse_proposer(text)
        assert verdict == "PASS"
        assert conf == 0.3

    def test_malformed_defaults_to_pass(self):
        text = "I think we should buy"
        verdict, conf, reasoning = _parse_proposer(text)
        assert verdict == "PASS"
        assert conf == 0.5


class TestParseChallenger:
    def test_approve(self):
        text = "VERDICT: APPROVE\nRISK_LEVEL: LOW\nOBJECTIONS: None"
        verdict, risk, obj = _parse_challenger(text)
        assert verdict == "APPROVE"
        assert risk == "LOW"
        assert obj == "None"

    def test_reject_high_risk(self):
        text = "VERDICT: REJECT\nRISK_LEVEL: HIGH\nOBJECTIONS: Edge is noise"
        verdict, risk, obj = _parse_challenger(text)
        assert verdict == "REJECT"
        assert risk == "HIGH"
        assert obj == "Edge is noise"


class TestParseReviewer:
    def test_hold(self):
        text = "VERDICT: HOLD\nURGENCY: LOW\nREASONING: Thesis intact"
        verdict, urgency, reasoning = _parse_reviewer(text)
        assert verdict == "HOLD"
        assert urgency == "LOW"

    def test_exit_high(self):
        text = "VERDICT: EXIT\nURGENCY: HIGH\nREASONING: Price collapsed"
        verdict, urgency, reasoning = _parse_reviewer(text)
        assert verdict == "EXIT"
        assert urgency == "HIGH"
        assert reasoning == "Price collapsed"

    def test_reduce(self):
        text = "VERDICT: REDUCE\nURGENCY: MEDIUM\nREASONING: Take partial profits"
        verdict, urgency, reasoning = _parse_reviewer(text)
        assert verdict == "REDUCE"
        assert urgency == "MEDIUM"
        assert reasoning == "Take partial profits"

    def test_increase(self):
        text = "VERDICT: INCREASE\nURGENCY: LOW\nREASONING: Price dipped, thesis stronger"
        verdict, urgency, reasoning = _parse_reviewer(text)
        assert verdict == "INCREASE"
        assert urgency == "LOW"


class TestLlmCostTracker:
    def test_tracks_daily_cost(self):
        tracker = LlmCostTracker(daily_budget=5.0)
        tracker.add(1.0)
        tracker.add(0.5)
        assert tracker.today_cost == 1.5
        assert tracker.budget_remaining == 3.5
        assert not tracker.is_over_budget

    def test_over_budget(self):
        tracker = LlmCostTracker(daily_budget=1.0)
        tracker.add(1.5)
        assert tracker.is_over_budget
        assert tracker.budget_remaining == 0.0


class TestDebateSignal:
    async def test_budget_exhausted_returns_none(self):
        with patch("bot.research.llm_debate.cost_tracker") as mock_tracker:
            mock_tracker.is_over_budget = True
            result = await debate_signal(
                question="Q?", strategy="test", edge=0.05,
                price=0.5, estimated_prob=0.55, confidence=0.7,
                reasoning="test",
            )
            assert result is None

    async def test_missing_api_key_returns_none(self):
        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = ""
            result = await debate_signal(
                question="Q?", strategy="test", edge=0.05,
                price=0.5, estimated_prob=0.55, confidence=0.7,
                reasoning="test",
            )
            assert result is None

    async def test_proposer_pass_skips_challenger(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(
            text="VERDICT: PASS\nCONFIDENCE: 0.3\nREASONING: Weak edge"
        )]
        mock_response.usage.input_tokens = 200
        mock_response.usage.output_tokens = 30

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            result = await debate_signal(
                question="Will X?", strategy="value_betting", edge=0.05,
                price=0.5, estimated_prob=0.55, confidence=0.7,
                reasoning="Orderbook imbalance",
            )

        assert result is not None
        assert not result.approved
        assert result.proposer_verdict == "PASS"
        assert result.challenger_verdict == "skipped"
        # Challenger should NOT have been called
        assert mock_client.messages.create.call_count == 1

    async def test_full_debate_approve(self):
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.85\nREASONING: Strong signal"
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 30

        chal_resp = MagicMock()
        chal_resp.content = [MagicMock(
            text="VERDICT: APPROVE\nRISK_LEVEL: LOW\nOBJECTIONS: None"
        )]
        chal_resp.usage.input_tokens = 300
        chal_resp.usage.output_tokens = 30

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[prop_resp, chal_resp]
        )

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            result = await debate_signal(
                question="Will BTC reach $100k?", strategy="value_betting",
                edge=0.08, price=0.45, estimated_prob=0.53, confidence=0.8,
                reasoning="Strong imbalance",
            )

        assert result is not None
        assert result.approved
        assert result.proposer_verdict == "BUY"
        assert result.challenger_verdict == "APPROVE"
        assert mock_client.messages.create.call_count == 2

    async def test_full_debate_reject(self):
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.6\nREASONING: Moderate edge"
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 30

        chal_resp = MagicMock()
        chal_resp.content = [MagicMock(
            text="VERDICT: REJECT\nRISK_LEVEL: HIGH\nOBJECTIONS: Edge is noise"
        )]
        chal_resp.usage.input_tokens = 300
        chal_resp.usage.output_tokens = 30

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[prop_resp, chal_resp]
        )

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            result = await debate_signal(
                question="Will X?", strategy="test", edge=0.03,
                price=0.5, estimated_prob=0.53, confidence=0.6,
                reasoning="Weak signal",
            )

        assert result is not None
        assert not result.approved
        assert result.challenger_verdict == "REJECT"


class TestReviewPosition:
    async def test_hold_recommendation(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(
            text="VERDICT: HOLD\nURGENCY: LOW\nREASONING: Thesis intact"
        )]
        mock_response.usage.input_tokens = 200
        mock_response.usage.output_tokens = 20

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            result = await review_position(
                question="Will BTC hit $100k?", strategy="value_betting",
                entry_price=0.45, current_price=0.50, size=10.0,
                age_hours=12.0, unrealized_pnl=0.50,
            )

        assert result is not None
        assert result.verdict == "HOLD"
        assert not result.should_exit
        assert result.urgency == "LOW"

    async def test_exit_recommendation(self):
        mock_response = MagicMock()
        mock_response.content = [MagicMock(
            text="VERDICT: EXIT\nURGENCY: HIGH\nREASONING: Price collapsed"
        )]
        mock_response.usage.input_tokens = 200
        mock_response.usage.output_tokens = 20

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=mock_response)

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            result = await review_position(
                question="Will X happen?", strategy="time_decay",
                entry_price=0.60, current_price=0.40, size=8.0,
                age_hours=48.0, unrealized_pnl=-1.60,
            )

        assert result is not None
        assert result.verdict == "EXIT"
        assert result.should_exit
        assert result.urgency == "HIGH"
