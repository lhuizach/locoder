"""Factory that turns config into a concrete ModelProvider instance.

Ollama gets its own backend because it has a native /api/chat endpoint with
richer tool-calling behavior. Everything else (llama.cpp server, vLLM,
LM Studio, or plain OpenAI) speaks the same /v1/chat/completions dialect, so
they all share OpenAICompatProvider.
"""

from __future__ import annotations

from locoder.providers.base import ModelProvider
from locoder.providers.ollama import OllamaProvider
from locoder.providers.openai_compat import OpenAICompatProvider

PROVIDER_KINDS = {
    "ollama": OllamaProvider,
    "openai": OpenAICompatProvider,
    "openai_compat": OpenAICompatProvider,
    "llamacpp": OpenAICompatProvider,
    "llama.cpp": OpenAICompatProvider,
    "vllm": OpenAICompatProvider,
    "lmstudio": OpenAICompatProvider,
}


def create_provider(
    *,
    kind: str,
    model: str,
    base_url: str,
    temperature: float = 0.2,
    num_ctx: int = 8192,
    api_key: str | None = None,
    timeout: float = 120.0,
) -> ModelProvider:
    cls = PROVIDER_KINDS.get(kind.lower())
    if cls is None:
        raise ValueError(f"unknown provider kind '{kind}'; expected one of {sorted(PROVIDER_KINDS)}")
    return cls(
        model=model,
        base_url=base_url,
        temperature=temperature,
        num_ctx=num_ctx,
        api_key=api_key,
        timeout=timeout,
    )
