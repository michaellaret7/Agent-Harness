"""Per-call LLM usage telemetry.

`Usage` is the wire shape carried by the `on_usage` Sink event. One
instance per LLM call; the TUI accumulates them into per-turn and
per-session totals for the status bar.

Providers disagree on which fields they populate. Reliable across all
three configured providers (vLLM, OpenAI, Anthropic-via-shim) are only
`prompt_tokens` and `completion_tokens`. `reasoning_tokens` shows up on
thinking models. `cached_tokens` shows up on OpenAI and Anthropic-shim
prompt caching. `cache_write_tokens` is read from both the OpenAI shape
(`prompt_tokens_details.cache_write_tokens`) and Anthropic's top-level
`cache_creation_input_tokens` — whichever the provider supplies. `cost`
is only populated by gateways like OpenRouter; vLLM/OpenAI/Anthropic
direct will return 0. `from_response` defaults every field to 0 so a
missing field is indistinguishable from a real zero, which is the right
call for accumulation.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class Usage:
    prompt_tokens: int = 0
    completion_tokens: int = 0
    reasoning_tokens: int = 0
    cached_tokens: int = 0
    cache_write_tokens: int = 0
    cost: float = 0.0

    @classmethod
    def zero(cls) -> 'Usage':
        return cls()

    @classmethod
    def from_response(cls, usage: Any) -> 'Usage':
        """Parse a CompletionUsage object. Tolerates missing nested fields."""
        if usage is None:
            return cls.zero()

        completion_details = getattr(usage, 'completion_tokens_details', None)
        prompt_details = getattr(usage, 'prompt_tokens_details', None)

        # cache_write tokens: OpenAI puts them under prompt_tokens_details,
        # Anthropic's OpenAI-shim surfaces them as top-level
        # `cache_creation_input_tokens`. Take whichever the provider gives.
        cache_write = (
            getattr(prompt_details, 'cache_write_tokens', 0)
            or getattr(usage, 'cache_creation_input_tokens', 0)
            or 0
        )

        return cls(
            prompt_tokens=getattr(usage, 'prompt_tokens', 0) or 0,
            completion_tokens=getattr(usage, 'completion_tokens', 0) or 0,
            reasoning_tokens=getattr(completion_details, 'reasoning_tokens', 0) or 0,
            cached_tokens=getattr(prompt_details, 'cached_tokens', 0) or 0,
            cache_write_tokens=cache_write,
            cost=float(getattr(usage, 'cost', 0.0) or 0.0),
        )

    def __add__(self, other: 'Usage') -> 'Usage':
        return Usage(
            prompt_tokens=self.prompt_tokens + other.prompt_tokens,
            completion_tokens=self.completion_tokens + other.completion_tokens,
            reasoning_tokens=self.reasoning_tokens + other.reasoning_tokens,
            cached_tokens=self.cached_tokens + other.cached_tokens,
            cache_write_tokens=self.cache_write_tokens + other.cache_write_tokens,
            cost=self.cost + other.cost,
        )
