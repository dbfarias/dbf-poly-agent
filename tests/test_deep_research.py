"""Tests for deep research context and debate extra_context integration."""

from datetime import datetime, timezone

from bot.research.types import ResearchResult


class TestResearchResultConvergence:
    def test_convergence_score_default(self) -> None:
        """convergence_score defaults to 0.0."""
        result = ResearchResult(
            market_id="test",
            keywords=("test",),
            news_items=(),
            sentiment_score=0.0,
            confidence=0.5,
            research_multiplier=1.0,
            updated_at=datetime.now(timezone.utc),
        )
        assert result.convergence_score == 0.0

    def test_convergence_score_set(self) -> None:
        result = ResearchResult(
            market_id="test",
            keywords=("test",),
            news_items=(),
            sentiment_score=0.0,
            confidence=0.5,
            research_multiplier=1.0,
            updated_at=datetime.now(timezone.utc),
            convergence_score=0.85,
        )
        assert result.convergence_score == 0.85


class TestDebateExtraContext:
    def test_debate_signal_accepts_extra_context(self) -> None:
        """Verify debate_signal function signature accepts extra_context."""
        import inspect

        from bot.research.llm_debate import debate_signal

        sig = inspect.signature(debate_signal)
        assert "extra_context" in sig.parameters
        param = sig.parameters["extra_context"]
        assert param.default == ""
