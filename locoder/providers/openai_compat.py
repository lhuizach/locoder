"""Backend for any server exposing an OpenAI-compatible /v1/chat/completions
endpoint: llama.cpp's server, vLLM, LM Studio, etc.

Handles the SSE ``data: {...}`` streaming format and reassembles tool-call
argument fragments, which OpenAI-style APIs stream incrementally by index
rather than delivering as one complete object like Ollama does.
"""

from __future__ import annotations

import json
import time
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

# Hosted APIs (Groq especially) answer "slow down" with HTTP 429. Wait and
# retry a few times rather than failing the whole turn.
MAX_RATE_LIMIT_RETRIES = 3
MAX_RATE_LIMIT_WAIT = 30.0


class ThinkSplitter:
    """Separates ``<think>...</think>`` reasoning from answer text in a token
    stream. Some servers (Groq's default "raw" format, llama.cpp/vLLM without
    a reasoning parser) put the model's reasoning inline in ``content``; left
    there it gets shown as the answer and re-sent every turn.
    """

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self) -> None:
        self._buf = ""
        self._thinking = False

    def feed(self, text: str) -> list[tuple[str, str]]:
        """Returns (kind, text) pieces, kind being "token" or "thinking"."""
        self._buf += text
        out: list[tuple[str, str]] = []
        while True:
            tag = self.CLOSE if self._thinking else self.OPEN
            i = self._buf.find(tag)
            if i < 0:
                break
            self._emit(out, self._buf[:i])
            self._buf = self._buf[i + len(tag):]
            self._thinking = not self._thinking
        # Hold back a tail that could be the start of a tag split across chunks.
        keep = next((n for n in range(min(len(tag) - 1, len(self._buf)), 0, -1)
                     if tag.startswith(self._buf[-n:])), 0)
        self._emit(out, self._buf[:len(self._buf) - keep])
        self._buf = self._buf[len(self._buf) - keep:]
        return out

    def flush(self) -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        self._emit(out, self._buf)
        self._buf = ""
        return out

    def _emit(self, out: list[tuple[str, str]], text: str) -> None:
        if text:
            out.append(("thinking" if self._thinking else "token", text))


class OpenAICompatProvider(ModelProvider):
    name = "openai_compat"

    def extra_payload(self) -> dict[str, Any]:
        """Backend-specific request fields; subclasses override."""
        return {}

    def _post(self, payload: dict[str, Any], stream: bool) -> requests.Response:
        """POST the chat request, waiting out rate limits. Raises ProviderError
        with the server's own error message on failure.
        """
        url = f"{self.base_url}/chat/completions"
        for attempt in range(MAX_RATE_LIMIT_RETRIES + 1):
            try:
                resp = requests.post(url, json=payload, headers=self._headers(), stream=stream,
                                     timeout=self.chat_timeout())
            except requests.exceptions.ReadTimeout as exc:
                raise ProviderError(self.silence_message()) from exc
            except requests.RequestException as exc:
                raise ProviderError(f"request to {self.base_url} failed: {exc}") from exc
            if resp.status_code == 429 and attempt < MAX_RATE_LIMIT_RETRIES:
                wait = _retry_after(resp, default=2.0 ** (attempt + 1))
                if wait <= MAX_RATE_LIMIT_WAIT:
                    resp.close()
                    time.sleep(wait)
                    continue
            if resp.status_code >= 400:
                detail = _error_detail(resp)
                resp.close()
                hint = " (rate limited - wait a bit and try again)" if resp.status_code == 429 else ""
                raise ProviderError(f"{self.base_url} returned HTTP {resp.status_code}{hint}: {detail}")
            return resp
        raise AssertionError("unreachable")

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
        payload.update(self.extra_payload())

        try:
            resp = self._post(payload, stream)
        except ProviderError as exc:
            yield StreamEvent(type="error", error=exc)
            return

        splitter = ThinkSplitter()
        content_parts: list[str] = []
        pending_calls: dict[int, dict[str, Any]] = {}
        usage: dict[str, int] = {}

        # The inner try/finally ensures the connection is released even if
        # this generator is closed early (e.g. the user interrupts the
        # turn), since GeneratorExit can be raised at any of the yields below.
        try:
            try:
                if stream:
                    # Server-sent events are UTF-8; without this, requests guesses
                    # Latin-1 (or gives raw bytes) when the server names no charset.
                    resp.encoding = "utf-8"
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
                        # Reasoning sent as its own field (Groq "parsed",
                        # vLLM/llama.cpp reasoning parsers).
                        reasoning = delta.get("reasoning") or delta.get("reasoning_content") or ""
                        if reasoning:
                            yield StreamEvent(type="thinking", token=reasoning)
                        for kind, piece in splitter.feed(delta.get("content") or ""):
                            if kind == "token":
                                content_parts.append(piece)
                            yield StreamEvent(type=kind, token=piece)
                        for tc_delta in delta.get("tool_calls") or []:
                            self._accumulate_tool_call(pending_calls, tc_delta)
                    for kind, piece in splitter.flush():
                        if kind == "token":
                            content_parts.append(piece)
                        yield StreamEvent(type=kind, token=piece)
                else:
                    body = resp.json()
                    choice = (body.get("choices") or [{}])[0]
                    msg = choice.get("message") or {}
                    pieces = splitter.feed(msg.get("content") or "") + splitter.flush()
                    content_parts.extend(piece for kind, piece in pieces if kind == "token")
                    for i, raw_tc in enumerate(msg.get("tool_calls") or []):
                        self._accumulate_tool_call(pending_calls, {**raw_tc, "index": i})
                    if body.get("usage"):
                        usage = {
                            "prompt_tokens": body["usage"].get("prompt_tokens", 0),
                            "completion_tokens": body["usage"].get("completion_tokens", 0),
                        }
            except requests.exceptions.ReadTimeout:
                yield StreamEvent(type="error", error=ProviderError(self.silence_message()))
                return
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


def _retry_after(resp: requests.Response, *, default: float) -> float:
    """Seconds the server asked us to wait, from the Retry-After header."""
    try:
        return max(0.0, float(resp.headers.get("retry-after", "")))
    except ValueError:
        return default


def _error_detail(resp: requests.Response) -> str:
    """The server's own explanation (e.g. Groq's "Request too large ... TPM"),
    which is far more useful than the bare status code.
    """
    try:
        body = resp.json()
        err = body.get("error") if isinstance(body, dict) else None
        if isinstance(err, dict) and err.get("message"):
            return str(err["message"])
        if isinstance(err, str):
            return err
    except ValueError:
        pass
    return (resp.text or resp.reason or "").strip()[:500]
