"""Tests for LLM debate gate and position reviewer."""

from unittest.mock import AsyncMock, MagicMock, patch

from bot.research.llm_debate import (
    LlmCostTracker,
    _is_debatable_rejection,
    _parse_challenger,
    _parse_proposer,
    _parse_reviewer,
    _parse_risk_analyst,
    _parse_risk_proposer,
    debate_risk_rejection,
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


class TestIsDebatableRejection:
    def test_hard_rejections_not_debatable(self):
        assert not _is_debatable_rejection("Trading is paused")
        assert not _is_debatable_rejection("Daily loss limit exceeded")
        assert not _is_debatable_rejection("Max drawdown reached")
        assert not _is_debatable_rejection("Duplicate position exists")

    def test_debatable_rejections(self):
        assert _is_debatable_rejection("Edge too low: 2.1% < 3.0%")
        assert _is_debatable_rejection("Category exposure limit")
        assert _is_debatable_rejection("Win prob too low")
        assert _is_debatable_rejection("Max positions reached")
        assert _is_debatable_rejection("Max deployed capital: 95%")

    def test_unknown_rejection_not_debatable(self):
        assert not _is_debatable_rejection("Some unknown reason")

    def test_case_insensitive_hard_rejection(self):
        assert not _is_debatable_rejection("daily loss limit exceeded")
        assert not _is_debatable_rejection("TRADING IS PAUSED by admin")

    def test_case_insensitive_debatable(self):
        assert _is_debatable_rejection("EDGE TOO LOW: 2.1% < 3.0%")


class TestParseRiskProposer:
    def test_full_parse(self):
        text = (
            "REBUTTAL: Edge is only 0.5% below threshold and resolution is in 12h\n"
            "PROPOSED_FIX: reduce size to 60%\n"
            "CONVICTION: 0.75"
        )
        rebuttal, fix, conviction = _parse_risk_proposer(text)
        assert "below threshold" in rebuttal
        assert "60%" in fix
        assert conviction == 0.75

    def test_low_conviction(self):
        text = "REBUTTAL: Weak argument\nPROPOSED_FIX: none\nCONVICTION: 0.2"
        _, _, conviction = _parse_risk_proposer(text)
        assert conviction == 0.2

    def test_malformed_defaults(self):
        text = "I agree with the rejection"
        rebuttal, fix, conviction = _parse_risk_proposer(text)
        assert rebuttal == text
        assert fix == ""
        assert conviction == 0.5

    def test_conviction_clamped(self):
        text = "CONVICTION: 1.5"
        _, _, conviction = _parse_risk_proposer(text)
        assert conviction == 1.0


class TestParseRiskAnalyst:
    def test_concede(self):
        text = (
            "VERDICT: CONCEDE\n"
            "SIZE_ADJUSTMENT: 0.7\n"
            "REASONING: Edge is close enough with reduced size"
        )
        verdict, adj, reasoning = _parse_risk_analyst(text)
        assert verdict == "CONCEDE"
        assert adj == 0.7
        assert "close enough" in reasoning

    def test_maintain(self):
        text = (
            "VERDICT: MAINTAIN\n"
            "SIZE_ADJUSTMENT: 1.0\n"
            "REASONING: Edge is far below threshold"
        )
        verdict, adj, reasoning = _parse_risk_analyst(text)
        assert verdict == "MAINTAIN"
        assert adj == 1.0

    def test_malformed_defaults_maintain(self):
        text = "The rejection should stand"
        verdict, adj, reasoning = _parse_risk_analyst(text)
        assert verdict == "MAINTAIN"
        assert adj == 1.0

    def test_size_adjustment_clamped(self):
        text = "SIZE_ADJUSTMENT: 0.3"
        _, adj, _ = _parse_risk_analyst(text)
        assert adj == 0.5


class TestDebateRiskRejection:
    async def test_hard_rejection_returns_none(self):
        result = await debate_risk_rejection(
            question="Q?", strategy="test",
            rejection_reason="Trading is paused",
            edge=0.05, price=0.5, estimated_prob=0.55, size_usd=2.0,
        )
        assert result is None

    async def test_budget_exhausted_returns_none(self):
        with patch("bot.research.llm_debate.cost_tracker") as mock_tracker:
            mock_tracker.is_over_budget = True
            result = await debate_risk_rejection(
                question="Q?", strategy="test",
                rejection_reason="Edge too low: 2% < 3%",
                edge=0.02, price=0.5, estimated_prob=0.52, size_usd=2.0,
            )
            assert result is None

    async def test_low_conviction_skips_analyst(self):
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="REBUTTAL: Weak case\nPROPOSED_FIX: none\nCONVICTION: 0.2"
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 30

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=prop_resp)

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            result = await debate_risk_rejection(
                question="Will X?", strategy="value_betting",
                rejection_reason="Edge too low: 2% < 3%",
                edge=0.02, price=0.5, estimated_prob=0.52, size_usd=2.0,
            )

        assert result is not None
        assert not result.override
        assert result.analyst_verdict == "skipped"
        assert mock_client.messages.create.call_count == 1

    async def test_full_debate_override(self):
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text=(
                "REBUTTAL: Edge is 2.8%, only 0.2% under threshold\n"
                "PROPOSED_FIX: reduce size to 70%\n"
                "CONVICTION: 0.8"
            )
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 40

        analyst_resp = MagicMock()
        analyst_resp.content = [MagicMock(
            text=(
                "VERDICT: CONCEDE\n"
                "SIZE_ADJUSTMENT: 0.7\n"
                "REASONING: Edge is close, reduced size mitigates risk"
            )
        )]
        analyst_resp.usage.input_tokens = 300
        analyst_resp.usage.output_tokens = 40

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[prop_resp, analyst_resp]
        )

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            result = await debate_risk_rejection(
                question="Will BTC hit $100k?", strategy="value_betting",
                rejection_reason="Edge too low: 2.8% < 3.0%",
                edge=0.028, price=0.5, estimated_prob=0.528, size_usd=3.0,
            )

        assert result is not None
        assert result.override
        assert result.analyst_verdict == "CONCEDE"
        assert result.adjusted_size_pct == 0.7
        assert mock_client.messages.create.call_count == 2

    async def test_full_debate_maintain(self):
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="REBUTTAL: Should reconsider\nPROPOSED_FIX: lower threshold\nCONVICTION: 0.6"
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 30

        analyst_resp = MagicMock()
        analyst_resp.content = [MagicMock(
            text="VERDICT: MAINTAIN\nSIZE_ADJUSTMENT: 1.0\nREASONING: Edge too far below"
        )]
        analyst_resp.usage.input_tokens = 300
        analyst_resp.usage.output_tokens = 30

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[prop_resp, analyst_resp]
        )

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            result = await debate_risk_rejection(
                question="Will X?", strategy="value_betting",
                rejection_reason="Edge too low: 1% < 3%",
                edge=0.01, price=0.5, estimated_prob=0.51, size_usd=2.0,
            )

        assert result is not None
        assert not result.override
        assert result.analyst_verdict == "MAINTAIN"
        assert result.adjusted_size_pct == 0.0
