"""Backend for Ollama's native /api/chat endpoint.

Uses Ollama's built-in tool-calling support (the model decides when to call a
tool; Ollama parses the arguments into a JSON object for us) rather than
hand-rolled prompt-based JSON parsing.
"""

from __future__ import annotations

import json
from typing import Any, Iterator, Optional

import requests

from locoder.providers.base import (
    Message,
    ModelProvider,
    ProviderError,
    StreamEvent,
    ToolCall,
    new_tool_call_id,
)


class OllamaProvider(ModelProvider):
    name = "ollama"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _wire_message(self, message: Message) -> dict[str, Any]:
        d: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            d["tool_calls"] = [tc.to_ollama() for tc in message.tool_calls]
        if message.role == "tool":
            if message.name:
                d["name"] = message.name
            if message.tool_call_id:
                d["tool_call_id"] = message.tool_call_id
        return d

    def chat(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
        *,
        stream: bool = True,
    ) -> Iterator[StreamEvent]:
        payload: dict[str, Any] = {
            "model": self.model,
            "messages": [self._wire_message(m) for m in messages],
            "stream": stream,
            "options": {
                "temperature": self.temperature,
                "num_ctx": self.num_ctx,
            },
        }
        if tools:
            payload["tools"] = tools

        try:
            resp = requests.post(
                f"{self.base_url}/api/chat",
                json=payload,
                headers=self._headers(),
                stream=stream,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            yield StreamEvent(type="error", error=ProviderError(f"ollama request failed: {exc}"))
            return

        content_parts: list[str] = []
        tool_calls: list[ToolCall] = []
        usage: dict[str, int] = {}

        # The inner try/finally ensures the connection is released even if
        # this generator is closed early (e.g. the user interrupts the
        # turn), since GeneratorExit can be raised at any of the yields above.
        try:
            try:
                if stream:
                    for line in resp.iter_lines(decode_unicode=True):
                        if not line:
                            continue
                        chunk = json.loads(line)
                        yield from self._consume_chunk(chunk, content_parts, tool_calls)
                        if chunk.get("done"):
                            usage = {
                                "prompt_tokens": chunk.get("prompt_eval_count", 0),
                                "completion_tokens": chunk.get("eval_count", 0),
                            }
                else:
                    chunk = resp.json()
                    yield from self._consume_chunk(chunk, content_parts, tool_calls)
                    usage = {
                        "prompt_tokens": chunk.get("prompt_eval_count", 0),
                        "completion_tokens": chunk.get("eval_count", 0),
                    }
            except (json.JSONDecodeError, requests.RequestException) as exc:
                yield StreamEvent(type="error", error=ProviderError(f"ollama stream error: {exc}"))
                return
        finally:
            resp.close()

        yield StreamEvent(
            type="done",
            message=Message(role="assistant", content="".join(content_parts), tool_calls=tool_calls),
            usage=usage,
        )

    def _consume_chunk(
        self,
        chunk: dict[str, Any],
        content_parts: list[str],
        tool_calls: list[ToolCall],
    ) -> Iterator[StreamEvent]:
        message = chunk.get("message") or {}
        thinking = message.get("thinking") or ""
        if thinking:
            yield StreamEvent(type="thinking", token=thinking)
        text = message.get("content") or ""
        if text:
            content_parts.append(text)
            yield StreamEvent(type="token", token=text)
        for raw_tc in message.get("tool_calls") or []:
            fn = raw_tc.get("function", {})
            args = fn.get("arguments", {})
            if isinstance(args, str):
                try:
                    args = json.loads(args) if args else {}
                except json.JSONDecodeError:
                    args = {"_raw": args}
            tc = ToolCall(name=fn.get("name", ""), arguments=args, id=raw_tc.get("id") or new_tool_call_id())
            tool_calls.append(tc)
            yield StreamEvent(type="tool_call", tool_call=tc)

    def list_models(self) -> list[str]:
        try:
            resp = requests.get(f"{self.base_url}/api/tags", headers=self._headers(), timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ProviderError(f"could not list ollama models: {exc}") from exc
        data = resp.json()
        return [m["name"] for m in data.get("models", [])]
