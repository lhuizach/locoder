"""Provider-agnostic chat primitives.

Every backend (Ollama, OpenAI-compatible servers, ...) speaks this same
vocabulary of Message / ToolCall / StreamEvent so the agent loop never has to
know which backend it's talking to.
"""

from __future__ import annotations

import json
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Iterator, Literal, Optional


def new_tool_call_id() -> str:
    return f"call_{uuid.uuid4().hex[:24]}"


@dataclass
class ToolCall:
    name: str
    arguments: dict[str, Any]
    id: str = field(default_factory=new_tool_call_id)

    def to_openai(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "type": "function",
            "function": {
                "name": self.name,
                "arguments": json.dumps(self.arguments),
            },
        }

    def to_ollama(self) -> dict[str, Any]:
        # Ollama keeps ``arguments`` as a native JSON object rather than a
        # JSON-encoded string (unlike the OpenAI wire format).
        return {"function": {"name": self.name, "arguments": self.arguments}}

    def to_dict(self) -> dict[str, Any]:
        return {"id": self.id, "name": self.name, "arguments": self.arguments}

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "ToolCall":
        return cls(name=data["name"], arguments=data.get("arguments", {}), id=data.get("id") or new_tool_call_id())


Role = Literal["system", "user", "assistant", "tool"]


@dataclass
class Message:
    role: Role
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    # Only set on role="tool" messages: which call this is answering, and
    # (for providers, like Ollama, that want it) the tool's name.
    tool_call_id: Optional[str] = None
    name: Optional[str] = None

    def to_dict(self) -> dict[str, Any]:
        d: dict[str, Any] = {"role": self.role, "content": self.content}
        if self.tool_calls:
            d["tool_calls"] = [tc.to_dict() for tc in self.tool_calls]
        if self.tool_call_id:
            d["tool_call_id"] = self.tool_call_id
        if self.name:
            d["name"] = self.name
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Message":
        return cls(
            role=data["role"],
            content=data.get("content", ""),
            tool_calls=[ToolCall.from_dict(tc) for tc in data.get("tool_calls", [])],
            tool_call_id=data.get("tool_call_id"),
            name=data.get("name"),
        )


EventType = Literal["token", "thinking", "tool_call", "done", "error"]


@dataclass
class StreamEvent:
    """One increment of a streamed chat response.

    - "token": a chunk of assistant text to display immediately (``token``).
    - "thinking": a chunk of the model's reasoning/thinking trace (``token``),
      distinct from its final answer. Only reasoning-capable models on
      backends that expose it (currently: Ollama's ``message.thinking``)
      ever emit this; it is not included in the final assembled Message.
    - "tool_call": a fully-parsed tool call became available (``tool_call``).
      Emitted as soon as we know about it, ahead of "done", so the UI can show
      a "calling tool..." indicator without waiting for the stream to close.
    - "done": the stream is finished; ``message`` holds the fully assembled
      assistant Message (content + tool_calls) and ``usage`` has token counts
      if the backend reported them.
    - "error": something went wrong talking to the backend; ``error`` holds
      the exception.
    """

    type: EventType
    token: str = ""
    tool_call: Optional[ToolCall] = None
    message: Optional[Message] = None
    usage: Optional[dict[str, int]] = None
    error: Optional[BaseException] = None


class ProviderError(RuntimeError):
    """Raised when a backend cannot be reached or returns something we can't parse."""


class ModelProvider(ABC):
    """Common interface implemented by every model backend."""

    name: str = "provider"

    # A local model can sit silent for minutes before its first word - reading
    # a long prompt, thinking, or running partly on the CPU - so waiting for
    # the next chunk of a streamed answer is allowed far longer than `timeout`
    # (which still covers connecting and the quick calls).
    STREAM_PATIENCE = 900.0

    def __init__(
        self,
        *,
        model: str,
        base_url: str,
        temperature: float = 0.2,
        num_ctx: int = 8192,
        api_key: Optional[str] = None,
        timeout: float = 120.0,
        think: bool = True,
    ) -> None:
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.temperature = temperature
        self.num_ctx = num_ctx
        self.api_key = api_key
        self.timeout = timeout
        self.think = think

    def with_think(self, think: bool) -> "ModelProvider":
        """Return a copy of this provider with thinking switched on or off."""
        clone = self.__class__.__new__(self.__class__)
        clone.__dict__.update(self.__dict__)
        clone.think = think
        return clone

    def chat_timeout(self) -> tuple[float, float]:
        """(connect, read) timeouts for a chat request."""
        return (min(self.timeout, 15.0), max(self.timeout, self.STREAM_PATIENCE))

    def silence_message(self) -> str:
        minutes = max(self.timeout, self.STREAM_PATIENCE) / 60
        return (f"the model said nothing for {minutes:.0f} minutes - it may be overloaded, or the context "
                "window (num_ctx) is too big for your GPU and part of the model runs slowly on the CPU. "
                "Try a smaller num_ctx (32768 is plenty for most work)")

    @abstractmethod
    def chat(
        self,
        messages: list[Message],
        tools: Optional[list[dict[str, Any]]] = None,
        *,
        stream: bool = True,
    ) -> Iterator[StreamEvent]:
        """Send a conversation + tool definitions, yield StreamEvents.

        Implementations must always terminate the stream with exactly one
        event of type "done" (or "error").
        """
        raise NotImplementedError

    @abstractmethod
    def list_models(self) -> list[str]:
        """Return model names currently available on this backend."""
        raise NotImplementedError

    def with_model(self, model: str) -> "ModelProvider":
        """Return a copy of this provider pointed at a different model."""
        clone = self.__class__.__new__(self.__class__)
        clone.__dict__.update(self.__dict__)
        clone.model = model
        return clone
