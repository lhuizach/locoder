"""What ``locoder serve`` does, without the HTTP: the open project, its chats,
the one task that may be running, and the stream of events phones follow.

One task at a time: a local model only answers one request at once anyway,
and it keeps "what is my PC doing right now" simple. Events carry a rising
``seq`` so a phone that drops off (screen off, Wi-Fi blip) can ask for
everything after the last one it saw.
"""

from __future__ import annotations

import base64
import mimetypes
import platform
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable, Optional

import requests

from locoder import __version__
from locoder.agent import Agent, AgentMode
from locoder.config import AppConfig, ensure_project_config
from locoder.context import ContextManager
from locoder.providers.base import Message, ProviderError
from locoder.providers.registry import create_provider
from locoder.recent import recent_files, recent_projects, record_project, relative_time
from locoder.safety import sendable_problem
from locoder.sandbox import Sandbox, SandboxViolation
from locoder.serve.transcript import to_entries
from locoder.serve.web_ui import ConfirmRequest, QuestionRequest, WebUI, preview_text
from locoder.session import TITLE_PREVIEW_LEN, SessionStore
from locoder.tools.process_tools import ProcessManager

KEEP_EVENTS = 5_000
TITLE_LEN = 80


class HubError(Exception):
    """A request the hub refuses; ``status`` is the HTTP status to answer with."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class Run:
    chat: str
    user_text: str
    base_count: int  # messages in the chat before this run's user message
    agent: Agent
    ui: WebUI
    started: float = field(default_factory=time.time)
    events: list[dict[str, Any]] = field(default_factory=list)
    stopped: bool = False


class Hub:
    name = "locoder"  # in messages to the phone (lochat's hub is "lochat")

    def __init__(self, project_root: Path, overrides: Optional[dict[str, Any]] = None,
                 *, provider_factory: Callable[..., Any] = create_provider) -> None:
        self.overrides = overrides or {}
        self.provider_factory = provider_factory
        self.mode = AgentMode.BUILD
        self.computer = platform.node() or "this PC"
        self._lock = threading.RLock()
        self._events_changed = threading.Condition(self._lock)
        self._events: deque[dict[str, Any]] = deque(maxlen=KEEP_EVENTS)
        self._seq = 0
        self.run: Optional[Run] = None
        # The encrypted tunnel's public address, when there is one (tunnel.py).
        self.remote: Optional[str] = None
        self.pending: dict[str, ConfirmRequest] = {}
        self.questions: dict[str, QuestionRequest] = {}
        # Background processes (dev servers) outlive one task: each message
        # gets a new Agent, but they all share this.
        self.processes = ProcessManager()
        self._open_project(project_root)
        self.yolo = self.config.yolo
        self.web = self.config.web

    # -- project ---------------------------------------------------------------

    def _open_project(self, root: Path) -> None:
        root = root.resolve()
        ensure_project_config(root)
        record_project(root)
        config = AppConfig.load(root, **self.overrides)
        provider = self.provider_factory(**config.provider_kwargs())
        self.root = root
        self.config = config
        self.provider = provider
        self.sandbox = Sandbox(root, allowed_paths=config.allowed_paths)
        self.store = SessionStore(root)

    def switch_project(self, path: str) -> dict[str, Any]:
        allowed = {p.value.lower(): p.value for p in recent_projects(self.root, limit=50)}
        target = allowed.get(str(Path(path)).lower())
        if target is None:
            raise HubError(404, "that folder isn't one of your recent projects")
        with self._lock:
            if self.run:
                raise HubError(409, "wait for the running task to finish (or stop it) first")
            yolo = self.yolo
            self.processes.stop_all()  # they belong to the project being left
            self._open_project(Path(target))
            self.yolo = yolo
        self.publish("project", project=self.project_info())
        return self.project_info()

    def project_info(self) -> dict[str, Any]:
        home = Path.home()
        try:
            label = "~/" + self.root.relative_to(home).as_posix()
        except ValueError:
            label = self.root.as_posix()
        return {"path": str(self.root), "label": label, "name": self.root.name}

    def projects(self) -> list[dict[str, Any]]:
        current = {**self.project_info(), "detail": "open now", "current": True}
        others = [{"path": p.value, "label": p.label, "name": Path(p.value).name, "detail": p.detail, "current": False}
                  for p in recent_projects(self.root, limit=12)]
        return [current, *others]

    def recent_files(self) -> list[str]:
        return [item.value for item in recent_files(self.root, limit=15)]

    # -- events ----------------------------------------------------------------

    def publish(self, type_: str, **data: Any) -> dict[str, Any]:
        with self._lock:
            self._seq += 1
            event = {"seq": self._seq, "type": type_, "t": round(time.time(), 3), **data}
            self._events.append(event)
            if self.run and data.get("chat") == self.run.chat:
                self.run.events.append(event)
            self._events_changed.notify_all()
            return event

    @property
    def last_seq(self) -> int:
        return self._seq

    def events_after(self, seq: int, timeout: float = 0.0) -> Optional[list[dict[str, Any]]]:
        """Events newer than ``seq``, waiting up to ``timeout`` for one. None
        means some of them are no longer kept, and the phone should reload."""
        with self._lock:
            if timeout and self._seq <= seq:
                self._events_changed.wait_for(lambda: self._seq > seq, timeout)
            if self._events and seq < self._events[0]["seq"] - 1:
                return None
            return [e for e in self._events if e["seq"] > seq]

    # -- chats -----------------------------------------------------------------

    def chats(self) -> list[dict[str, Any]]:
        running = self.run.chat if self.run else None
        out = []
        for s in self.store.summarize_all():
            if s.message_count <= 1 and s.session_id != running:
                continue  # never got a message
            try:
                when = datetime.fromisoformat(s.updated_at).timestamp()
            except ValueError:
                when = 0.0
            out.append({
                "id": s.path.stem,
                "title": self._title(s.path) if s.session_id == running else s.title,
                "updated": when,
                "ago": relative_time(when) if when else "",
                "messages": s.message_count - 1,
                "running": s.path.stem == running,
            })
        return out

    def _title(self, path: Path) -> str:
        title = SessionStore._summarize(path).title
        run = self.run
        if run and run.chat == path.stem and title == "(empty conversation)":
            # A new chat's first message isn't saved until the model answers.
            text = " ".join(run.user_text.split())
            title = text[:TITLE_PREVIEW_LEN] + ("..." if len(text) > TITLE_PREVIEW_LEN else "")
        return title

    def _path(self, chat: str) -> Path:
        path = self.store.chat_path(chat)
        if path is None:
            raise HubError(404, "no such chat")
        return path

    def chat(self, chat: str) -> dict[str, Any]:
        path = self._path(chat)
        with self._lock:
            messages = SessionStore.load(path)
            live = None
            run = self.run
            if run and run.chat == chat:
                # Draw what was there before this run, then replay the run.
                messages = messages[: run.base_count] + [Message(role="user", content=run.user_text)]
                pending = [p.public() for p in self.pending.values() if p.chat == chat]
                live = {"events": list(run.events), "started": run.started, "pending": pending}
            seq = self._seq
        title = self._title(path)
        used = ContextManager(num_ctx=self.config.num_ctx).total_tokens(messages)
        return {
            "id": chat,
            "title": title,
            "entries": to_entries(messages),
            "live": live,
            "seq": seq,
            "usage": {"used": used, "limit": self.config.num_ctx},
        }

    def rename(self, chat: str, title: str) -> dict[str, Any]:
        title = " ".join(title.split())[:TITLE_LEN]
        if not title:
            raise HubError(400, "the title can't be empty")
        self.store.rename(self._path(chat), title)
        self.publish("chats")
        return {"id": chat, "title": title}

    def delete(self, chat: str) -> None:
        path = self._path(chat)
        if self.run and self.run.chat == chat:
            raise HubError(409, "stop this chat's task before deleting it")
        self.store.delete(path)
        self.publish("chats")

    # -- running a task --------------------------------------------------------

    def send(self, chat: Optional[str], text: str, files: Optional[list[str]] = None) -> dict[str, Any]:
        text = text.strip()
        if not text:
            raise HubError(400, "empty message")
        if files:
            text += "\n\nFiles to look at: " + ", ".join(str(f) for f in files)

        with self._lock:
            if self.run:
                raise HubError(409, f"{self.name} is already working on something - wait or stop it first")
            store = SessionStore(self.root)
            messages = store.resume(self._path(chat)) if chat else None
            chat_id = store.session_id
            ui = WebUI(self, chat_id)
            agent = self._new_agent(ui, store)
            if messages:
                agent.messages = messages
            store.save(agent.messages)
            run = self.run = Run(chat=chat_id, user_text=text, base_count=len(agent.messages), agent=agent, ui=ui)

        self.publish("run_start", chat=chat_id, text=text, mode=self.mode.value, model=self.provider.model)
        self.publish("chats")
        threading.Thread(target=self._work, args=(run,), name=f"locoder-run-{chat_id}", daemon=True).start()
        return {"id": chat_id}

    def _new_agent(self, ui: WebUI, store: SessionStore) -> Agent:
        ctx_manager = ContextManager(num_ctx=self.config.num_ctx,
                                     threshold=self.config.context_compaction_threshold)
        agent = Agent(self.provider, ui, self.sandbox, ctx_manager, store, yolo=self.yolo, web=self.web,
                      brave_api_key=self.config.brave_api_key,
                      tavily_api_key=self.config.tavily_api_key, processes=self.processes)
        agent.set_mode(self.mode)
        return agent

    def _work(self, run: Run) -> None:
        try:
            run.agent.run_turn(run.user_text)
        except Exception as exc:  # noqa: BLE001 - reported to the phone, the server keeps going
            run.ui.error(f"unexpected error: {exc}")
        finally:
            # run_turn doesn't save when the model errors; keep the user's message anyway.
            run.agent.session.save(run.agent.messages)
            used, limit = run.agent.token_usage()
            answer = next((m.content for m in reversed(run.agent.messages[run.base_count:])
                           if m.role == "assistant" and m.content.strip()), "")
            status = "stopped" if run.stopped else "error" if run.ui.failed else "done"
            title = SessionStore._summarize(run.agent.session.path).title
            with self._lock:
                self.run = None
                for request in [p for p in self.pending.values() if p.chat == run.chat]:
                    self._answer(request, False)
                for question in [q for q in self.questions.values() if q.chat == run.chat]:
                    self._reply(question, None)
            self.publish("usage", chat=run.chat, used=used, limit=limit)
            self.publish("run_end", chat=run.chat, status=status, title=title, preview=preview_text(answer),
                         seconds=round(time.time() - run.started, 1), model=run.agent.provider.model)
            self.publish("chats")

    def ask(self, request: ConfirmRequest) -> bool:
        """Called on the agent's thread: show the request, wait for an answer."""
        with self._lock:
            if self.run is None or self.run.stopped:
                return False
            request.chat_title = self._title(self.run.agent.session.path)
            self.pending[request.id] = request
        self.publish("confirm", chat=request.chat, request=request.public())
        request.answered.wait()
        return request.approved

    def answer(self, request_id: str, approve: bool) -> None:
        with self._lock:
            request = self.pending.get(request_id)
            if request is None:
                raise HubError(404, "that request was already answered")
            self._answer(request, approve)

    def _answer(self, request: ConfirmRequest, approve: bool) -> None:
        self.pending.pop(request.id, None)
        request.approved = approve
        request.answered.set()
        self.publish("confirm_done", chat=request.chat, id=request.id, approved=approve)

    def ask_question(self, question: QuestionRequest) -> Optional[str]:
        """Called on the agent's thread: show the model's question, wait for an answer (None: skipped)."""
        with self._lock:
            if self.run is None or self.run.stopped:
                return None
            question.chat_title = self._title(self.run.agent.session.path)
            self.questions[question.id] = question
        self.publish("question", chat=question.chat, question=question.public())
        question.answered.wait()
        return question.answer

    def reply(self, question_id: str, answer: Optional[str]) -> None:
        with self._lock:
            question = self.questions.get(question_id)
            if question is None:
                raise HubError(404, "that question was already answered")
            self._reply(question, (answer or "").strip() or None)

    def _reply(self, question: QuestionRequest, answer: Optional[str]) -> None:
        self.questions.pop(question.id, None)
        question.answer = answer
        question.answered.set()
        self.publish("question_done", chat=question.chat, id=question.id, answer=answer)

    def stop(self, chat: str) -> None:
        with self._lock:
            run = self.run
            if run is None or run.chat != chat:
                return
            run.stopped = True
            run.agent.request_cancel()
            for request in [p for p in self.pending.values() if p.chat == chat]:
                self._answer(request, False)
            for question in [q for q in self.questions.values() if q.chat == chat]:
                self._reply(question, None)

    # -- session settings ------------------------------------------------------

    def state(self) -> dict[str, Any]:
        with self._lock:
            run = self.run
            return {
                "computer": self.computer,
                "version": __version__,
                "project": self.project_info(),
                "mode": self.mode.value,
                "model": self.provider.model,
                "provider": self.config.provider,
                "base_url": self.config.base_url,
                "temperature": self.config.temperature,
                "num_ctx": self.config.num_ctx,
                "think": self.config.think,
                "yolo": self.yolo,
                "web": self.web,
                "running": run.chat if run else None,
                "pending": [p.public() for p in self.pending.values()],
                "questions": [q.public() for q in self.questions.values()],
                "todos": list(run.agent.tool_ctx.todos) if run else [],
                "seq": self._seq,
                "remote": self.remote,
            }

    def update(self, changes: dict[str, Any]) -> dict[str, Any]:
        save = False
        with self._lock:
            if "mode" in changes:
                try:
                    self.mode = AgentMode(str(changes["mode"]))
                except ValueError:
                    raise HubError(400, "mode must be build, ask or plan") from None
            if "yolo" in changes:
                self.yolo = bool(changes["yolo"])
            if "web" in changes:
                self.web = bool(changes["web"])  # applies from the next message; not saved
            if "model" in changes:
                model = str(changes["model"]).strip()
                if not model:
                    raise HubError(400, "no model given")
                self.config.model = model
                save = True
            if "think" in changes:
                self.config.think = bool(changes["think"])
                save = True
            if "temperature" in changes:
                try:
                    self.config.temperature = min(2.0, max(0.0, float(changes["temperature"])))
                except (TypeError, ValueError):
                    raise HubError(400, "temperature must be a number") from None
                save = True
            if save:
                # Applies from the next message; a running task keeps its model.
                self.provider = self.provider_factory(**self.config.provider_kwargs())
                self.config.save(self.root)
        state = self.state()
        self.publish("state", state={k: state[k] for k in ("mode", "model", "temperature", "yolo", "think", "web")})
        return state

    # -- files for the phone -----------------------------------------------------

    def read_file(self, path: str) -> dict[str, Any]:
        """A project file for the phone to download (what send_file offered).
        Same checks as the tool: inside the project, no secrets, a size limit."""
        if not path:
            raise HubError(400, "no file given")
        try:
            target = self.sandbox.resolve(path, must_exist=True)
        except (SandboxViolation, OSError) as exc:
            raise HubError(404, str(exc)) from None
        problem = sendable_problem(self.root, target)
        if problem:
            raise HubError(403, problem)
        mime = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
        return {
            "name": target.name,
            "size": target.stat().st_size,
            "mime": mime,
            "data": base64.b64encode(target.read_bytes()).decode("ascii"),
        }

    # -- the model in memory (Ollama) -------------------------------------------

    def _ollama(self) -> str:
        if self.config.provider.lower() != "ollama":
            raise HubError(400, "only Ollama models can be unloaded from here")
        return self.config.base_url.rstrip("/")

    def model_memory(self) -> dict[str, Any]:
        """Which models Ollama has loaded right now (it unloads them by itself
        a few minutes after the last use) and how much memory they hold."""
        try:
            resp = requests.get(f"{self._ollama()}/api/ps", timeout=5)
            resp.raise_for_status()
            loaded = resp.json().get("models") or []
        except requests.RequestException as exc:
            raise HubError(502, f"can't reach Ollama: {exc}") from None
        out = []
        for m in loaded:
            try:
                expires = datetime.fromisoformat(str(m.get("expires_at", "")).replace("Z", "+00:00"))
                unloads_in = max(0, int(expires.timestamp() - time.time()))
            except ValueError:
                unloads_in = None
            out.append({
                "name": m.get("name", ""),
                "vram_gb": round((m.get("size_vram") or 0) / 1e9, 1),
                "ram_gb": round(max(0, (m.get("size") or 0) - (m.get("size_vram") or 0)) / 1e9, 1),
                "unloads_in": unloads_in,
            })
        return {"loaded": out}

    def unload(self) -> dict[str, Any]:
        """Frees the memory now instead of waiting for Ollama's timeout."""
        if self.run:
            raise HubError(409, "a task is running - stop it first")
        base = self._ollama()
        for m in self.model_memory()["loaded"]:
            try:
                # keep_alive 0 is Ollama's way of saying "unload it now".
                requests.post(f"{base}/api/generate", json={"model": m["name"], "keep_alive": 0},
                              timeout=30).raise_for_status()
            except requests.RequestException as exc:
                raise HubError(502, f"Ollama didn't unload {m['name']}: {exc}") from None
        self.publish("memory")
        return self.model_memory()

    def models(self) -> list[str]:
        try:
            return self.provider.list_models()
        except ProviderError as exc:
            raise HubError(502, str(exc)) from None
