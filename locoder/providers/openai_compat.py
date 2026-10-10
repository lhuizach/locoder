"""Backend for any server exposing an OpenAI-compatible /v1/chat/completions
endpoint: llama.cpp's server, vLLM, LM Studio, etc.

Handles the SSE ``data: {...}`` streaming format and reassembles tool-call
argument fragments, which OpenAI-style APIs stream incrementally by index
rather than delivering as one complete object like Ollama does.
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


class OpenAICompatProvider(ModelProvider):
    name = "openai_compat"

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    def _wire_message(self, message: Message) -> dict[str, Any]:
        d: dict[str, Any] = {"role": message.role, "content": message.content}
        if message.tool_calls:
            d["tool_calls"] = [tc.to_openai() for tc in message.tool_calls]
        if message.role == "tool" and message.tool_call_id:
            d["tool_call_id"] = message.tool_call_id
        if message.name:
            d["name"] = message.name
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
            "temperature": self.temperature,
            "stream": stream,
        }
        if tools:
            payload["tools"] = tools
        if stream:
            payload["stream_options"] = {"include_usage": True}

        try:
            resp = requests.post(
                f"{self.base_url}/chat/completions",
                json=payload,
                headers=self._headers(),
                stream=stream,
                timeout=self.timeout,
            )
            resp.raise_for_status()
        except requests.RequestException as exc:
            yield StreamEvent(type="error", error=ProviderError(f"request to {self.base_url} failed: {exc}"))
            return

        content_parts: list[str] = []
        pending_calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, int] = {}

        # The inner try/finally ensures the connection is released even if
        # this generator is closed early (e.g. the user interrupts the
        # turn), since GeneratorExit can be raised at any of the yields below.
        try:
            try:
                if stream:
                    for raw_line in resp.iter_lines(decode_unicode=True):
                        if not raw_line or not raw_line.startswith("data:"):
                            continue
                        data = raw_line[len("data:") :].strip()
                        if data == "[DONE]":
                            break
                        chunk = json.loads(data)
                        if chunk.get("usage"):
                            usage = {
                                "prompt_tokens": chunk["usage"].get("prompt_tokens", 0),
                                "completion_tokens": chunk["usage"].get("completion_tokens", 0),
                            }
                        choices = chunk.get("choices") or []
                        if not choices:
                            continue
                        delta = choices[0].get("delta") or {}
                        text = delta.get("content") or ""
                        if text:
                            content_parts.append(text)
                            yield StreamEvent(type="token", token=text)
                        for tc_delta in delta.get("tool_calls") or []:
                            self._accumulate_tool_call(pending_calls, tc_delta)
                else:
                    body = resp.json()
                    choice = (body.get("choices") or [{}])[0]
                    msg = choice.get("message") or {}
                    content_parts.append(msg.get("content") or "")
                    for i, raw_tc in enumerate(msg.get("tool_calls") or []):
                        self._accumulate_tool_call(pending_calls, {**raw_tc, "index": i})
                    if body.get("usage"):
                        usage = {
                            "prompt_tokens": body["usage"].get("prompt_tokens", 0),
                            "completion_tokens": body["usage"].get("completion_tokens", 0),
                        }
            except (json.JSONDecodeError, requests.RequestException) as exc:
                yield StreamEvent(type="error", error=ProviderError(f"stream error: {exc}"))
                return
        finally:
            resp.close()

        tool_calls = self._finalize_tool_calls(pending_calls)
        for tc in tool_calls:
            yield StreamEvent(type="tool_call", tool_call=tc)

        yield StreamEvent(
            type="done",
            message=Message(role="assistant", content="".join(content_parts), tool_calls=tool_calls),
            usage=usage,
        )

    @staticmethod
    def _accumulate_tool_call(pending: dict[int, dict[str, Any]], tc_delta: dict[str, Any]) -> None:
        idx = tc_delta.get("index", 0)
        slot = pending.setdefault(idx, {"id": None, "name": "", "arguments": ""})
        if tc_delta.get("id"):
            slot["id"] = tc_delta["id"]
        fn = tc_delta.get("function") or {}
        if fn.get("name"):
            slot["name"] += fn["name"]
        if fn.get("arguments"):
            slot["arguments"] += fn["arguments"]

    @staticmethod
    def _finalize_tool_calls(pending: dict[int, dict[str, Any]]) -> list[ToolCall]:
        result = []
        for idx in sorted(pending):
            slot = pending[idx]
            try:
                args = json.loads(slot["arguments"]) if slot["arguments"] else {}
            except json.JSONDecodeError:
                args = {"_raw": slot["arguments"]}
            result.append(ToolCall(name=slot["name"], arguments=args, id=slot["id"] or new_tool_call_id()))
        return result

    def list_models(self) -> list[str]:
        try:
            resp = requests.get(f"{self.base_url}/models", headers=self._headers(), timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            raise ProviderError(f"could not list models from {self.base_url}: {exc}") from exc
        data = resp.json()
        return [m["id"] for m in data.get("data", [])]
