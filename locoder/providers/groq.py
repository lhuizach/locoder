"""Backend for Groq's hosted API (https://console.groq.com).

Groq speaks the OpenAI chat-completions dialect, so this is
OpenAICompatProvider plus Groq's defaults: its base URL, the API key from
``GROQ_API_KEY``, and asking reasoning models to return their thinking in a
separate field instead of inline ``<think>`` tags.
"""

from __future__ import annotations

import os
from typing import Any, Optional

from locoder.providers.openai_compat import OpenAICompatProvider

GROQ_BASE_URL = "https://api.groq.com/openai/v1"

# Groq models that accept reasoning_format; others reject the field outright
# (gpt-oss uses include_reasoning instead, non-reasoning models have neither).
_REASONING_FORMAT_PREFIXES = ("qwen/", "qwen-", "deepseek")


class GroqProvider(OpenAICompatProvider):
    name = "groq"

    def __init__(self, *, base_url: str, api_key: Optional[str] = None, **kwargs: Any) -> None:
        # The config's base_url defaults to Ollama's local address, which is
        # never what someone choosing Groq means.
        if not base_url or "localhost:11434" in base_url or "127.0.0.1:11434" in base_url:
            base_url = GROQ_BASE_URL
        super().__init__(base_url=base_url, api_key=api_key or os.environ.get("GROQ_API_KEY"), **kwargs)

    def extra_payload(self) -> dict[str, Any]:
        if self.model.lower().startswith(_REASONING_FORMAT_PREFIXES):
            return {"reasoning_format": "parsed"}
        return {}
