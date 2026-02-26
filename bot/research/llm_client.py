"""Multi-model LLM abstraction layer.

Design-only module — provides a provider-agnostic interface for LLM calls.
Currently wraps Anthropic (Claude Haiku). OpenAI placeholder included for
future GPT-5.4/Codex support when bankroll justifies multi-model costs.

NOT wired into the trading system yet. Existing code continues using
direct Anthropic calls in llm_debate.py and llm_sentiment.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@dataclass(frozen=True)
class LlmResponse:
    """Unified response from any LLM provider."""

    text: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    model: str
    provider: str  # "anthropic", "openai", etc.


@runtime_checkable
class LlmProvider(Protocol):
    """Protocol for LLM providers — implement for each vendor."""

    @property
    def name(self) -> str:
        """Provider name (e.g., 'anthropic', 'openai')."""
        ...

    async def complete(
        self,
        system: str,
        user_message: str,
        *,
        max_tokens: int = 300,
        timeout: float = 15.0,
    ) -> LlmResponse:
        """Send a completion request and return a unified response."""
        ...


class AnthropicProvider:
    """Claude (Haiku) provider via the Anthropic SDK."""

    # Pricing per 1M tokens (Haiku 4.5, as of 2026-03)
    INPUT_COST_PER_M = 0.80
    OUTPUT_COST_PER_M = 4.00

    def __init__(self, api_key: str, model: str = "claude-haiku-4-5-20251001"):
        self._api_key = api_key
        self._model = model

    @property
    def name(self) -> str:
        return "anthropic"

    async def complete(
        self,
        system: str,
        user_message: str,
        *,
        max_tokens: int = 300,
        timeout: float = 15.0,
    ) -> LlmResponse:
        from anthropic import AsyncAnthropic

        client = AsyncAnthropic(api_key=self._api_key, timeout=timeout)
        resp = await client.messages.create(
            model=self._model,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": user_message}],
        )
        text = resp.content[0].text.strip()
        input_tokens = resp.usage.input_tokens
        output_tokens = resp.usage.output_tokens
        cost = (
            input_tokens * self.INPUT_COST_PER_M
            + output_tokens * self.OUTPUT_COST_PER_M
        ) / 1_000_000

        return LlmResponse(
            text=text,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
            model=self._model,
            provider=self.name,
        )


class OpenAIProvider:
    """OpenAI provider placeholder — implement when bankroll supports it.

    Expected models: gpt-4o-mini (cheap), gpt-5.4 (powerful).
    """

    # Placeholder pricing (update when integrating)
    INPUT_COST_PER_M = 0.15  # gpt-4o-mini
    OUTPUT_COST_PER_M = 0.60

    def __init__(self, api_key: str, model: str = "gpt-4o-mini"):
        self._api_key = api_key
        self._model = model

    @property
    def name(self) -> str:
        return "openai"

    async def complete(
        self,
        system: str,
        user_message: str,
        *,
        max_tokens: int = 300,
        timeout: float = 15.0,
    ) -> LlmResponse:
        raise NotImplementedError(
            "OpenAI provider not yet implemented. "
            "Install openai package and add API key when ready."
        )
