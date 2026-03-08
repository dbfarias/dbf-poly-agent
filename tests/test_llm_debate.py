"""Tests for LLM debate gate and position reviewer."""

import time
from unittest.mock import AsyncMock, MagicMock, patch

from bot.research.llm_debate import (
    ConsensusResult,
    DebateContext,
    DebateResult,
    LlmCostTracker,
    _debate_cache,
    _debate_cache_key,
    _format_challenger_prompt,
    _format_proposer_prompt,
    _get_cached_debate,
    _is_debatable_rejection,
    _parse_challenger,
    _parse_consensus_persona,
    _parse_counter_proposer,
    _parse_post_mortem,
    _parse_proposer,
    _parse_reviewer,
    _parse_risk_analyst,
    _parse_risk_proposer,
    clear_debate_cache,
    debate_risk_rejection,
    debate_signal,
    debate_with_consensus,
    review_position,
)

_ANTHROPIC_PATCH = "anthropic.AsyncAnthropic"


class TestParseProposer:
    def test_buy_verdict(self):
        text = "VERDICT: BUY\nCONFIDENCE: 0.8\nEDGE_VALID: YES\nREASONING: Strong edge"
        verdict, conf, reasoning, edge_valid = _parse_proposer(text)
        assert verdict == "BUY"
        assert conf == 0.8
        assert reasoning == "Strong edge"
        assert edge_valid is True

    def test_pass_verdict(self):
        text = "VERDICT: PASS\nCONFIDENCE: 0.3\nEDGE_VALID: YES\nREASONING: Too risky"
        verdict, conf, reasoning, edge_valid = _parse_proposer(text)
        assert verdict == "PASS"
        assert conf == 0.3
        assert edge_valid is True

    def test_malformed_defaults_to_pass(self):
        text = "I think we should buy"
        verdict, conf, reasoning, edge_valid = _parse_proposer(text)
        assert verdict == "PASS"
        assert conf == 0.5
        assert edge_valid is True

    def test_edge_invalid_deprecated_always_true(self):
        """EDGE_VALID: NO is deprecated — edges are algo-computed, always valid."""
        text = "VERDICT: BUY\nCONFIDENCE: 0.7\nEDGE_VALID: NO\nREASONING: Edge seems fabricated"
        verdict, conf, reasoning, edge_valid = _parse_proposer(text)
        assert verdict == "BUY"
        assert conf == 0.7
        assert edge_valid is True  # Always True now (deprecated)

    def test_edge_valid_missing_defaults_true(self):
        text = "VERDICT: BUY\nCONFIDENCE: 0.8\nREASONING: Good signal"
        verdict, conf, reasoning, edge_valid = _parse_proposer(text)
        assert verdict == "BUY"
        assert edge_valid is True


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


class TestParsePostMortem:
    def test_good_outcome(self):
        text = (
            "OUTCOME_QUALITY: GOOD\n"
            "KEY_LESSON: Timing was right\n"
            "STRATEGY_FIT: GOOD_FIT\n"
            "ANALYSIS: Entry was well-timed and thesis played out"
        )
        quality, lesson, fit, analysis = _parse_post_mortem(text)
        assert quality == "GOOD"
        assert lesson == "Timing was right"
        assert fit == "GOOD_FIT"
        assert "Entry was well-timed" in analysis

    def test_bad_outcome(self):
        text = (
            "OUTCOME_QUALITY: BAD\n"
            "KEY_LESSON: Should have exited earlier\n"
            "STRATEGY_FIT: POOR_FIT\n"
            "ANALYSIS: Sports market was a coin flip"
        )
        quality, lesson, fit, analysis = _parse_post_mortem(text)
        assert quality == "BAD"
        assert fit == "POOR_FIT"

    def test_malformed_defaults(self):
        text = "Some analysis without proper format"
        quality, lesson, fit, analysis = _parse_post_mortem(text)
        assert quality == "NEUTRAL"
        assert lesson == ""
        assert fit == "NEUTRAL"


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
    def setup_method(self):
        clear_debate_cache()

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
            mock_settings.use_llm_consensus = False
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
            mock_settings.use_llm_consensus = False
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
            mock_settings.use_multi_round_debate = False
            mock_settings.use_llm_consensus = False
            result = await debate_signal(
                question="Will X?", strategy="test", edge=0.03,
                price=0.5, estimated_prob=0.53, confidence=0.6,
                reasoning="Weak signal",
            )

        assert result is not None
        assert not result.approved
        assert result.challenger_verdict == "REJECT"
        assert result.counter_rebuttal == ""
        assert result.final_verdict == ""


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


class TestParseCounterProposer:
    def test_full_parse(self):
        text = "COUNTER: The edge is sufficient because resolution is in 12h\nCONVICTION: 0.85"
        counter, conviction = _parse_counter_proposer(text)
        assert counter == "The edge is sufficient because resolution is in 12h"
        assert conviction == 0.85

    def test_low_conviction(self):
        text = "COUNTER: Maybe not worth it\nCONVICTION: 0.2"
        counter, conviction = _parse_counter_proposer(text)
        assert conviction == 0.2

    def test_malformed_defaults(self):
        text = "Some random response without format"
        counter, conviction = _parse_counter_proposer(text)
        assert counter == text
        assert conviction == 0.5

    def test_conviction_clamped(self):
        text = "COUNTER: Strong case\nCONVICTION: 1.5"
        _, conviction = _parse_counter_proposer(text)
        assert conviction == 1.0


class TestMultiRoundDebate:
    def setup_method(self):
        clear_debate_cache()

    async def test_single_round_reject_no_counter(self):
        """When multi-round is off, reject stays rejected without counter."""
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.7\nREASONING: Good edge"
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 30

        chal_resp = MagicMock()
        chal_resp.content = [MagicMock(
            text="VERDICT: REJECT\nRISK_LEVEL: HIGH\nOBJECTIONS: Too risky"
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
            mock_settings.use_multi_round_debate = False
            mock_settings.use_llm_consensus = False
            result = await debate_signal(
                question="Will X?", strategy="test", edge=0.05,
                price=0.5, estimated_prob=0.55, confidence=0.7,
                reasoning="test",
            )

        assert result is not None
        assert not result.approved
        assert result.counter_rebuttal == ""
        assert result.final_verdict == ""
        assert mock_client.messages.create.call_count == 2

    async def test_multi_round_counter_approve(self):
        """Multi-round: challenger rejects, proposer counters, challenger approves."""
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.8\nREASONING: Strong signal"
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 30

        chal_resp = MagicMock()
        chal_resp.content = [MagicMock(
            text="VERDICT: REJECT\nRISK_LEVEL: MEDIUM\nOBJECTIONS: Edge seems thin"
        )]
        chal_resp.usage.input_tokens = 300
        chal_resp.usage.output_tokens = 30

        counter_resp = MagicMock()
        counter_resp.content = [MagicMock(
            text="COUNTER: Resolution in 6h, reduced exposure\nCONVICTION: 0.75"
        )]
        counter_resp.usage.input_tokens = 250
        counter_resp.usage.output_tokens = 30

        final_resp = MagicMock()
        final_resp.content = [MagicMock(
            text="VERDICT: APPROVE\nRISK_LEVEL: LOW\nOBJECTIONS: Counter is valid"
        )]
        final_resp.usage.input_tokens = 350
        final_resp.usage.output_tokens = 30

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[prop_resp, chal_resp, counter_resp, final_resp]
        )

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            mock_settings.use_multi_round_debate = True
            mock_settings.use_llm_consensus = False
            result = await debate_signal(
                question="Will BTC reach $100k?", strategy="value_betting",
                edge=0.05, price=0.5, estimated_prob=0.55, confidence=0.8,
                reasoning="Strong edge",
            )

        assert result is not None
        assert result.approved
        assert result.counter_rebuttal == "Resolution in 6h, reduced exposure"
        assert result.counter_conviction == 0.75
        assert result.final_verdict == "APPROVE"
        assert mock_client.messages.create.call_count == 4

    async def test_multi_round_counter_still_rejected(self):
        """Multi-round: challenger rejects, proposer counters, challenger still rejects."""
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.6\nREASONING: Moderate edge"
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 30

        chal_resp = MagicMock()
        chal_resp.content = [MagicMock(
            text="VERDICT: REJECT\nRISK_LEVEL: HIGH\nOBJECTIONS: Fundamentally flawed"
        )]
        chal_resp.usage.input_tokens = 300
        chal_resp.usage.output_tokens = 30

        counter_resp = MagicMock()
        counter_resp.content = [MagicMock(
            text="COUNTER: Data supports the thesis\nCONVICTION: 0.6"
        )]
        counter_resp.usage.input_tokens = 250
        counter_resp.usage.output_tokens = 30

        final_resp = MagicMock()
        final_resp.content = [MagicMock(
            text="VERDICT: REJECT\nRISK_LEVEL: HIGH\nOBJECTIONS: Still too risky"
        )]
        final_resp.usage.input_tokens = 350
        final_resp.usage.output_tokens = 30

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[prop_resp, chal_resp, counter_resp, final_resp]
        )

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            mock_settings.use_multi_round_debate = True
            mock_settings.use_llm_consensus = False
            result = await debate_signal(
                question="Will X?", strategy="test", edge=0.03,
                price=0.5, estimated_prob=0.53, confidence=0.6,
                reasoning="Weak signal",
            )

        assert result is not None
        assert not result.approved
        assert result.final_verdict == "REJECT"
        assert mock_client.messages.create.call_count == 4

    async def test_multi_round_low_conviction_skips_final(self):
        """Multi-round: counter has low conviction, skips final challenger call."""
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.7\nREASONING: Some edge"
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 30

        chal_resp = MagicMock()
        chal_resp.content = [MagicMock(
            text="VERDICT: REJECT\nRISK_LEVEL: HIGH\nOBJECTIONS: Bad trade"
        )]
        chal_resp.usage.input_tokens = 300
        chal_resp.usage.output_tokens = 30

        counter_resp = MagicMock()
        counter_resp.content = [MagicMock(
            text="COUNTER: Maybe they're right\nCONVICTION: 0.2"
        )]
        counter_resp.usage.input_tokens = 250
        counter_resp.usage.output_tokens = 30

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[prop_resp, chal_resp, counter_resp]
        )

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            mock_settings.use_multi_round_debate = True
            mock_settings.use_llm_consensus = False
            result = await debate_signal(
                question="Will X?", strategy="test", edge=0.03,
                price=0.5, estimated_prob=0.53, confidence=0.6,
                reasoning="Weak signal",
            )

        assert result is not None
        assert not result.approved
        assert result.counter_rebuttal == "Maybe they're right"
        assert result.counter_conviction == 0.2
        # Final verdict empty = low conviction skipped final call
        assert result.final_verdict == ""
        # Only 3 calls: proposer, challenger, counter (no final)
        assert mock_client.messages.create.call_count == 3

    async def test_multi_round_not_triggered_on_approve(self):
        """Multi-round doesn't trigger if challenger approves (no need to counter)."""
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.9\nREASONING: Great trade"
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
            mock_settings.use_multi_round_debate = True
            mock_settings.use_llm_consensus = False
            result = await debate_signal(
                question="Will X?", strategy="test", edge=0.08,
                price=0.5, estimated_prob=0.58, confidence=0.9,
                reasoning="Strong",
            )

        assert result is not None
        assert result.approved
        assert result.counter_rebuttal == ""
        assert mock_client.messages.create.call_count == 2

    async def test_multi_round_counter_api_error_fallback(self):
        """When counter-proposer API fails, challenger's REJECT stands."""
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.7\nREASONING: Good edge"
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 30

        chal_resp = MagicMock()
        chal_resp.content = [MagicMock(
            text="VERDICT: REJECT\nRISK_LEVEL: HIGH\nOBJECTIONS: Too risky"
        )]
        chal_resp.usage.input_tokens = 300
        chal_resp.usage.output_tokens = 30

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[prop_resp, chal_resp, Exception("API timeout")]
        )

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            mock_settings.use_multi_round_debate = True
            mock_settings.use_llm_consensus = False
            result = await debate_signal(
                question="Will X?", strategy="test", edge=0.05,
                price=0.5, estimated_prob=0.55, confidence=0.7,
                reasoning="test",
            )

        assert result is not None
        assert not result.approved
        assert result.counter_rebuttal == ""
        assert result.final_verdict == ""
        assert mock_client.messages.create.call_count == 3

    async def test_multi_round_final_challenger_api_error(self):
        """When final challenger API fails, REJECT is the default."""
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.8\nREASONING: Strong signal"
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 30

        chal_resp = MagicMock()
        chal_resp.content = [MagicMock(
            text="VERDICT: REJECT\nRISK_LEVEL: HIGH\nOBJECTIONS: Serious flaw"
        )]
        chal_resp.usage.input_tokens = 300
        chal_resp.usage.output_tokens = 30

        counter_resp = MagicMock()
        counter_resp.content = [MagicMock(
            text="COUNTER: Short resolution mitigates risk\nCONVICTION: 0.8"
        )]
        counter_resp.usage.input_tokens = 250
        counter_resp.usage.output_tokens = 30

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[prop_resp, chal_resp, counter_resp, Exception("API error")]
        )

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            mock_settings.use_multi_round_debate = True
            mock_settings.use_llm_consensus = False
            result = await debate_signal(
                question="Will X?", strategy="test", edge=0.05,
                price=0.5, estimated_prob=0.55, confidence=0.8,
                reasoning="test",
            )

        assert result is not None
        assert not result.approved
        assert result.counter_rebuttal == "Short resolution mitigates risk"
        assert result.final_verdict == "REJECT"
        assert "rejection stands" in result.final_reasoning
        assert mock_client.messages.create.call_count == 4


class TestDebateCache:
    """Tests for the debate result cache."""

    def setup_method(self):
        clear_debate_cache()

    def teardown_method(self):
        clear_debate_cache()

    def test_cache_key_normalizes(self):
        k1 = _debate_cache_key("Will BTC hit $1M?", "value_betting", 0.5, 0.05)
        k2 = _debate_cache_key("  Will BTC hit $1M?  ", "value_betting", 0.5, 0.05)
        assert k1 == k2

    def test_cache_key_differs_by_strategy(self):
        k1 = _debate_cache_key("Q?", "value_betting", 0.5, 0.05)
        k2 = _debate_cache_key("Q?", "time_decay", 0.5, 0.05)
        assert k1 != k2

    def test_cache_key_differs_by_price(self):
        """Different prices produce different cache keys (re-debate on price change)."""
        k1 = _debate_cache_key("Q?", "test", 0.50, 0.05)
        k2 = _debate_cache_key("Q?", "test", 0.55, 0.05)
        assert k1 != k2

    def test_cache_key_same_within_bucket(self):
        """Tiny price changes within bucket share the same key."""
        k1 = _debate_cache_key("Q?", "test", 0.501, 0.05)
        k2 = _debate_cache_key("Q?", "test", 0.504, 0.05)
        assert k1 == k2

    def test_get_cached_returns_none_when_empty(self):
        assert _get_cached_debate("Q?", "test") is None

    def test_cache_stores_and_retrieves(self):
        result = DebateResult(
            approved=True,
            proposer_verdict="BUY",
            proposer_confidence=0.8,
            proposer_reasoning="Good edge",
            challenger_verdict="APPROVE",
            challenger_risk="LOW",
            challenger_objections="None",
            total_cost_usd=0.001,
            elapsed_s=1.5,
        )
        key = _debate_cache_key("Q?", "test", 0.5, 0.05)
        _debate_cache[key] = (result, time.monotonic())

        cached = _get_cached_debate("Q?", "test", price=0.5, edge=0.05)
        assert cached is not None
        assert cached.approved is True
        assert cached.total_cost_usd == 0.0  # Cost zeroed on cache hit
        assert cached.elapsed_s == 0.0

    def test_cache_expires_after_ttl(self):
        result = DebateResult(
            approved=False,
            proposer_verdict="PASS",
            proposer_confidence=0.3,
            proposer_reasoning="Weak",
            challenger_verdict="skipped",
            challenger_risk="N/A",
            challenger_objections="",
            total_cost_usd=0.001,
            elapsed_s=0.5,
        )
        key = _debate_cache_key("Q?", "test", 0.5, 0.05)
        # Backdate the timestamp so it's expired
        _debate_cache[key] = (result, time.monotonic() - 99999)

        assert _get_cached_debate("Q?", "test", price=0.5, edge=0.05) is None
        # Expired entry should be cleaned up
        assert key not in _debate_cache

    def test_clear_debate_cache(self):
        result = DebateResult(
            approved=True,
            proposer_verdict="BUY",
            proposer_confidence=0.8,
            proposer_reasoning="Good",
            challenger_verdict="APPROVE",
            challenger_risk="LOW",
            challenger_objections="None",
            total_cost_usd=0.001,
            elapsed_s=1.0,
        )
        key = _debate_cache_key("Q?", "test", 0.5, 0.05)
        _debate_cache[key] = (result, time.monotonic())
        assert len(_debate_cache) == 1

        clear_debate_cache()
        assert len(_debate_cache) == 0

    async def test_debate_signal_uses_cache(self):
        """Second call to debate_signal for same market returns cached result."""
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.85\nREASONING: Strong signal",
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 30

        chal_resp = MagicMock()
        chal_resp.content = [MagicMock(
            text="VERDICT: APPROVE\nRISK: LOW\nOBJECTIONS: Looks fine",
        )]
        chal_resp.usage.input_tokens = 300
        chal_resp.usage.output_tokens = 40

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[prop_resp, chal_resp],
        )

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            mock_settings.use_multi_round_debate = False
            mock_settings.use_llm_consensus = False

            # First call — should hit the API
            result1 = await debate_signal(
                question="Will BTC hit $1M?",
                strategy="value_betting",
                edge=0.05, price=0.5, estimated_prob=0.55,
                confidence=0.7, reasoning="test",
            )

        assert result1 is not None
        assert result1.approved is True
        assert result1.total_cost_usd > 0
        assert mock_client.messages.create.call_count == 2

        # Second call — should return cached result (no API calls)
        with patch("bot.research.llm_debate.cost_tracker") as mock_tracker:
            mock_tracker.is_over_budget = False
            result2 = await debate_signal(
                question="Will BTC hit $1M?",
                strategy="value_betting",
                edge=0.05, price=0.5, estimated_prob=0.55,
                confidence=0.7, reasoning="test",
            )

        assert result2 is not None
        assert result2.approved is True
        assert result2.total_cost_usd == 0.0  # No cost — cached
        # API was NOT called again
        assert mock_client.messages.create.call_count == 2


class TestConsensusResult:
    """Tests for the ConsensusResult dataclass."""

    def test_dataclass_fields(self):
        result = ConsensusResult(
            approved=True,
            verdicts=["BUY", "BUY", "PASS"],
            confidences=[0.8, 0.7, 0.3],
            avg_confidence=0.6,
            total_cost_usd=0.003,
        )
        assert result.approved is True
        assert len(result.verdicts) == 3
        assert result.avg_confidence == 0.6

    def test_frozen(self):
        result = ConsensusResult(
            approved=False,
            verdicts=["PASS", "PASS", "BUY"],
            confidences=[0.2, 0.3, 0.8],
            avg_confidence=0.433,
            total_cost_usd=0.002,
        )
        import pytest
        with pytest.raises(AttributeError):
            result.approved = True  # type: ignore[misc]


class TestParseConsensusPersona:
    def test_buy_verdict(self):
        text = "VERDICT: BUY\nCONFIDENCE: 0.85\nREASONING: Strong edge"
        verdict, conf = _parse_consensus_persona(text)
        assert verdict == "BUY"
        assert conf == 0.85

    def test_pass_verdict(self):
        text = "VERDICT: PASS\nCONFIDENCE: 0.2\nREASONING: Too risky"
        verdict, conf = _parse_consensus_persona(text)
        assert verdict == "PASS"
        assert conf == 0.2

    def test_malformed_defaults(self):
        text = "I think we should pass on this"
        verdict, conf = _parse_consensus_persona(text)
        assert verdict == "PASS"
        assert conf == 0.5

    def test_confidence_clamped(self):
        text = "VERDICT: BUY\nCONFIDENCE: 1.5"
        _, conf = _parse_consensus_persona(text)
        assert conf == 1.0


class TestDebateWithConsensus:
    """Tests for the debate_with_consensus function."""

    async def test_budget_exhausted_returns_none(self):
        with patch("bot.research.llm_debate.cost_tracker") as mock_tracker:
            mock_tracker.is_over_budget = True
            result = await debate_with_consensus(
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
            result = await debate_with_consensus(
                question="Q?", strategy="test", edge=0.05,
                price=0.5, estimated_prob=0.55, confidence=0.7,
                reasoning="test",
            )
            assert result is None

    async def test_majority_buy_approves(self):
        # 3 personas: conservative=PASS, aggressive=BUY, balanced=BUY
        conservative_resp = MagicMock()
        conservative_resp.content = [MagicMock(
            text="VERDICT: PASS\nCONFIDENCE: 0.3\nREASONING: Edge too thin"
        )]
        conservative_resp.usage.input_tokens = 200
        conservative_resp.usage.output_tokens = 30

        aggressive_resp = MagicMock()
        aggressive_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.9\nREASONING: Great edge"
        )]
        aggressive_resp.usage.input_tokens = 200
        aggressive_resp.usage.output_tokens = 30

        balanced_resp = MagicMock()
        balanced_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.7\nREASONING: Data supports"
        )]
        balanced_resp.usage.input_tokens = 200
        balanced_resp.usage.output_tokens = 30

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[conservative_resp, aggressive_resp, balanced_resp]
        )

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            result = await debate_with_consensus(
                question="Will BTC hit $100k?", strategy="value_betting",
                edge=0.05, price=0.5, estimated_prob=0.55, confidence=0.8,
                reasoning="Strong imbalance",
            )

        assert result is not None
        assert result.approved is True
        assert result.verdicts == ["PASS", "BUY", "BUY"]
        assert len(result.confidences) == 3
        assert result.avg_confidence > 0
        assert result.total_cost_usd > 0
        assert mock_client.messages.create.call_count == 3

    async def test_majority_pass_rejects(self):
        # All 3 return PASS
        resp = MagicMock()
        resp.content = [MagicMock(
            text="VERDICT: PASS\nCONFIDENCE: 0.2\nREASONING: Bad signal"
        )]
        resp.usage.input_tokens = 200
        resp.usage.output_tokens = 30

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=resp)

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            result = await debate_with_consensus(
                question="Will X?", strategy="test", edge=0.01,
                price=0.5, estimated_prob=0.51, confidence=0.3,
                reasoning="Weak",
            )

        assert result is not None
        assert result.approved is False
        assert result.verdicts == ["PASS", "PASS", "PASS"]

    async def test_persona_error_defaults_to_pass(self):
        # First persona succeeds with BUY, other two throw errors
        good_resp = MagicMock()
        good_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.9\nREASONING: Strong"
        )]
        good_resp.usage.input_tokens = 200
        good_resp.usage.output_tokens = 30

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(
            side_effect=[good_resp, Exception("API error"), Exception("Timeout")]
        )

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            result = await debate_with_consensus(
                question="Will X?", strategy="test", edge=0.05,
                price=0.5, estimated_prob=0.55, confidence=0.7,
                reasoning="test",
            )

        assert result is not None
        assert result.approved is False  # Only 1 BUY, 2 errors (PASS)
        assert result.verdicts == ["BUY", "PASS", "PASS"]
        assert result.confidences[1] == 0.0  # Error persona gets 0 confidence
        assert result.confidences[2] == 0.0


class TestGetMarketHistory:
    async def test_empty_question_returns_empty(self):
        from bot.research.llm_debate import _get_market_history
        result = await _get_market_history("")
        assert result == ""

    async def test_import_error_returns_empty(self):
        from bot.research.llm_debate import _get_market_history
        with patch.dict("sys.modules", {"bot.data.database": None}):
            result = await _get_market_history("Will BTC hit $100k?")
            # Should not crash, returns empty
            assert isinstance(result, str)


class TestWhaleSummaryIntegration:
    """Tests that whale_summary parameter is accepted and doesn't break debate."""

    def setup_method(self):
        clear_debate_cache()

    async def test_whale_summary_in_debate_signal(self):
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.85\nREASONING: Whales are buying"
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
            mock_settings.use_llm_consensus = False
            mock_settings.use_multi_round_debate = False
            result = await debate_signal(
                question="Will BTC hit $100k?", strategy="value_betting",
                edge=0.08, price=0.45, estimated_prob=0.53, confidence=0.8,
                reasoning="Strong imbalance",
                whale_summary="3 whale orders, $5,000 total, net bias: BUY",
            )

        assert result is not None
        assert result.approved is True
        # Verify whale summary was included in the prompt
        call_args = mock_client.messages.create.call_args_list[0]
        proposer_msg = call_args.kwargs["messages"][0]["content"]
        assert "WHALE ACTIVITY" in proposer_msg
        assert "3 whale orders" in proposer_msg

    async def test_empty_whale_summary_not_injected(self):
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: PASS\nCONFIDENCE: 0.3\nREASONING: Weak"
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
            mock_settings.use_llm_consensus = False
            result = await debate_signal(
                question="Will X?", strategy="test", edge=0.05,
                price=0.5, estimated_prob=0.55, confidence=0.7,
                reasoning="test",
                whale_summary="",
            )

        assert result is not None
        call_args = mock_client.messages.create.call_args_list[0]
        proposer_msg = call_args.kwargs["messages"][0]["content"]
        assert "WHALE ACTIVITY" not in proposer_msg


class TestConsensusIntegrationWithDebateSignal:
    """Test that consensus mode integrates correctly with debate_signal."""

    def setup_method(self):
        clear_debate_cache()

    async def test_consensus_approved_triggers_challenger(self):
        """When consensus approves, challenger still reviews."""
        # Consensus: 2 BUY, 1 PASS = approved
        buy_resp = MagicMock()
        buy_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.8\nREASONING: Good"
        )]
        buy_resp.usage.input_tokens = 200
        buy_resp.usage.output_tokens = 30

        pass_resp = MagicMock()
        pass_resp.content = [MagicMock(
            text="VERDICT: PASS\nCONFIDENCE: 0.3\nREASONING: Weak"
        )]
        pass_resp.usage.input_tokens = 200
        pass_resp.usage.output_tokens = 30

        chal_resp = MagicMock()
        chal_resp.content = [MagicMock(
            text="VERDICT: APPROVE\nRISK_LEVEL: LOW\nOBJECTIONS: None"
        )]
        chal_resp.usage.input_tokens = 300
        chal_resp.usage.output_tokens = 30

        mock_client = AsyncMock()
        # 3 consensus + 1 challenger = 4 calls
        mock_client.messages.create = AsyncMock(
            side_effect=[buy_resp, buy_resp, pass_resp, chal_resp]
        )

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            mock_settings.use_llm_consensus = True
            mock_settings.use_multi_round_debate = False
            result = await debate_signal(
                question="Will BTC hit $100k?", strategy="value_betting",
                edge=0.08, price=0.45, estimated_prob=0.53, confidence=0.8,
                reasoning="Strong",
            )

        assert result is not None
        assert result.approved is True
        assert "Consensus" in result.proposer_reasoning
        assert result.challenger_verdict == "APPROVE"
        # 3 consensus calls + 1 challenger call = 4
        assert mock_client.messages.create.call_count == 4

    async def test_consensus_rejected_skips_challenger(self):
        """When consensus rejects (majority PASS), skip challenger."""
        pass_resp = MagicMock()
        pass_resp.content = [MagicMock(
            text="VERDICT: PASS\nCONFIDENCE: 0.2\nREASONING: Bad"
        )]
        pass_resp.usage.input_tokens = 200
        pass_resp.usage.output_tokens = 30

        mock_client = AsyncMock()
        mock_client.messages.create = AsyncMock(return_value=pass_resp)

        with (
            patch("bot.research.llm_debate.cost_tracker") as mock_tracker,
            patch("bot.research.llm_debate.settings") as mock_settings,
            patch(_ANTHROPIC_PATCH, return_value=mock_client),
        ):
            mock_tracker.is_over_budget = False
            mock_settings.anthropic_api_key = "sk-test"
            mock_settings.use_llm_consensus = True
            mock_settings.use_multi_round_debate = False
            result = await debate_signal(
                question="Will X?", strategy="test", edge=0.01,
                price=0.5, estimated_prob=0.51, confidence=0.3,
                reasoning="Weak",
            )

        assert result is not None
        assert result.approved is False
        assert result.challenger_verdict == "skipped"
        # Only 3 consensus calls, no challenger
        assert mock_client.messages.create.call_count == 3


class TestMediumRiskOverride:
    """Test risk override: confident proposer overrides MEDIUM/HIGH rejections."""

    def setup_method(self):
        clear_debate_cache()

    async def test_medium_risk_override_approves(self):
        """Proposer BUY conf 0.8 + Challenger REJECT MEDIUM → approved."""
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.8\nREASONING: Strong signal"
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 30

        chal_resp = MagicMock()
        chal_resp.content = [MagicMock(
            text="VERDICT: REJECT\nRISK_LEVEL: MEDIUM\nOBJECTIONS: Minor concerns"
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
            mock_settings.use_llm_consensus = False
            mock_settings.use_multi_round_debate = False
            mock_settings.daily_target_pct = 1.0
            result = await debate_signal(
                question="Will BTC hit $100k?", strategy="value_betting",
                edge=0.08, price=0.45, estimated_prob=0.53, confidence=0.8,
                reasoning="Strong orderbook imbalance",
            )

        assert result is not None
        assert result.approved is True
        assert result.proposer_verdict == "BUY"
        assert result.challenger_verdict == "REJECT"
        assert result.challenger_risk == "MEDIUM"

    async def test_high_risk_low_conf_still_rejects(self):
        """Proposer BUY conf 0.8 + Challenger REJECT HIGH → rejected (conf < 0.85)."""
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.8\nREASONING: Strong signal"
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 30

        chal_resp = MagicMock()
        chal_resp.content = [MagicMock(
            text="VERDICT: REJECT\nRISK_LEVEL: HIGH\nOBJECTIONS: Fundamental flaw"
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
            mock_settings.use_llm_consensus = False
            mock_settings.use_multi_round_debate = False
            mock_settings.daily_target_pct = 1.0
            result = await debate_signal(
                question="Will X happen?", strategy="value_betting",
                edge=0.08, price=0.45, estimated_prob=0.53, confidence=0.8,
                reasoning="Strong signal",
            )

        assert result is not None
        assert result.approved is False

    async def test_low_confidence_medium_still_rejects(self):
        """Proposer BUY conf 0.5 + Challenger REJECT MEDIUM → rejected (too low conf)."""
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.5\nREASONING: Marginal signal"
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 30

        chal_resp = MagicMock()
        chal_resp.content = [MagicMock(
            text="VERDICT: REJECT\nRISK_LEVEL: MEDIUM\nOBJECTIONS: Some concerns"
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
            mock_settings.use_llm_consensus = False
            mock_settings.use_multi_round_debate = False
            mock_settings.daily_target_pct = 1.0
            result = await debate_signal(
                question="Will Y?", strategy="time_decay",
                edge=0.04, price=0.50, estimated_prob=0.54, confidence=0.5,
                reasoning="Weak edge",
            )

        assert result is not None
        assert result.approved is False


    async def test_high_risk_high_conf_overrides(self):
        """Proposer BUY conf 0.9 + Challenger REJECT HIGH → approved (conf >= 0.85)."""
        prop_resp = MagicMock()
        prop_resp.content = [MagicMock(
            text="VERDICT: BUY\nCONFIDENCE: 0.9\nREASONING: Very strong signal"
        )]
        prop_resp.usage.input_tokens = 200
        prop_resp.usage.output_tokens = 30

        chal_resp = MagicMock()
        chal_resp.content = [MagicMock(
            text="VERDICT: REJECT\nRISK_LEVEL: HIGH\nOBJECTIONS: Some concerns"
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
            mock_settings.use_llm_consensus = False
            mock_settings.use_multi_round_debate = False
            mock_settings.daily_target_pct = 1.0
            result = await debate_signal(
                question="Will Z happen soon?", strategy="value_betting",
                edge=0.10, price=0.40, estimated_prob=0.50, confidence=0.9,
                reasoning="Very strong signal",
            )

        assert result is not None
        assert result.approved is True


class TestContextEnrichment:
    """Test that DebateContext is properly included in prompts."""

    def test_proposer_prompt_includes_context(self):
        ctx = DebateContext(
            strategy_win_rate=0.65,
            strategy_total_trades=20,
            edge_multiplier=0.85,
            daily_progress=0.5,
            urgency_multiplier=1.3,
            market_category="Crypto",
            research_confidence=0.8,
            news_headlines=("BTC hits new high", "ETH upgrade coming"),
            crypto_prices=(("bitcoin", 102000.0), ("ethereum", 3500.0)),
            is_volume_anomaly=True,
            historical_base_rate=0.7,
        )
        prompt = _format_proposer_prompt(
            question="Will BTC hit $110k?", strategy="value_betting",
            edge=0.05, price=0.45, estimated_prob=0.50, confidence=0.7,
            reasoning="Orderbook imbalance", sentiment_score=0.3,
            hours_to_resolution=48.0, context=ctx,
        )
        assert "BOT TRACK RECORD" in prompt
        assert "65%" in prompt
        assert "20 trades" in prompt
        assert "Daily progress" in prompt
        assert "BEHIND" in prompt
        assert "Crypto" in prompt
        assert "VOLUME ANOMALY" in prompt
        assert "BTC hits new high" in prompt
        assert "bitcoin: $102,000" in prompt

    def test_challenger_prompt_includes_context(self):
        ctx = DebateContext(
            strategy_win_rate=0.75,
            strategy_total_trades=10,
            edge_multiplier=1.1,
        )
        prompt = _format_challenger_prompt(
            question="Will X happen?", strategy="time_decay",
            edge=0.03, price=0.50, estimated_prob=0.53,
            proposer_reasoning="Good edge",
            sentiment_score=None, hours_to_resolution=24.0,
            resolution_condition="price exceeds $100",
            resolution_source="CoinGecko",
            context=ctx,
        )
        assert "BOT TRACK RECORD" in prompt
        assert "Resolution:" in prompt
        assert "CoinGecko" in prompt

    def test_prompt_without_context_unchanged(self):
        """Without context, prompts should not contain track record."""
        prompt = _format_proposer_prompt(
            question="Will X?", strategy="test",
            edge=0.05, price=0.50, estimated_prob=0.55, confidence=0.7,
            reasoning="Test", sentiment_score=None,
            hours_to_resolution=None, context=None,
        )
        assert "BOT TRACK RECORD" not in prompt
