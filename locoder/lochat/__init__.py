"""lochat: chat with the AI on your PC from your phone - no coding.

``lochat serve [--tunnel]`` is ``locoder serve`` with three differences:

- Plain chat. The model has no file or command tools at all, only web search
  when you switch Web on in the app. Its "folder" is an empty one, so even a
  model that names a file tool by mistake can't reach anything.
- Its own place: chats in ~/.lochat/.locoder/sessions, paired phones in
  ~/.lochat/devices.json, and port 8766 - it can run next to locoder serve.
- Its own page (lochat/web/index.html) and app (the "lochat" build of
  mobile/android).

Pairing, the pairing lock, end-to-end encryption and the Cloudflare tunnel
are locoder serve's, unchanged.
"""

from __future__ import annotations

import re
import sys
from datetime import date
from pathlib import Path
from typing import Any, Optional

from locoder.agent import Agent
from locoder.config import AppConfig
from locoder.context import ContextManager
from locoder.providers.base import Message
from locoder.sandbox import Sandbox
from locoder.serve import ServeApp, serve_main
from locoder.serve.hub import Hub, HubError, Run
from locoder.serve.web_ui import WebUI
from locoder.session import SessionStore
from locoder.tools.web_tools import WEB_TOOLS

LOCHAT_DIR = Path.home() / ".lochat"
WEB_DIR = Path(__file__).parent / "web"

CHAT_PROMPT = """\
You are lochat, a friendly and helpful AI assistant. You run privately on the \
user's own PC and they're talking to you from their phone.

Answer conversationally and to the point: short answers for simple questions, \
more detail when it's asked for or really needed. Use plain language, and \
markdown (bold, lists, code blocks) only when it makes the answer easier to \
read on a phone screen. If you don't know something, say so instead of \
guessing."""

WEB_NOTE = ("You can search the web (web_search) and read pages (web_fetch). Use them for anything "
            "recent or that you're unsure of, and say where the information came from.")
NO_WEB_NOTE = ("You can't look anything up right now. If a question needs current information, say "
               "that the user can switch on Web (the globe button) for you to search.")


TITLE_PROMPT = """\
Give a short title (2 to 5 words) to a chat that starts with the message \
below. Reply with the title only: no quotes, no full stop, no "Title:".

Message: {text}"""
TITLE_MAX = 60


def clean_title(text: str) -> str:
    """The model's reply as a title: one line, no thinking, quotes or markdown."""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    line = next((ln.strip() for ln in text.splitlines() if ln.strip()), "")
    line = re.sub(r"^(title|chat title)\s*:\s*", "", line, flags=re.I)
    line = line.strip(" \t\"'`*#_.").strip()
    return line[:TITLE_MAX].rstrip()


class ChatAgent(Agent):
    """The agent loop with no tools but web search (and that only when Web is on)."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.messages = [Message(role="system", content=CHAT_PROMPT)]

    def set_mode(self, mode: Any) -> None:
        pass  # build / ask / plan are coding modes; lochat has none

    def _tool_schemas(self) -> list[dict]:
        if not self.tool_ctx.web_enabled:
            return []
        return [s for s in super()._tool_schemas() if s["function"]["name"] in WEB_TOOLS]

    def _refuse_tool(self, name: str) -> Optional[str]:
        if name in WEB_TOOLS and self.tool_ctx.web_enabled:
            return None
        if name in WEB_TOOLS:
            return "web search is switched off - the user can turn it on with the globe button"
        return f"'{name}' isn't available: lochat is chat only (plus web search when it's on)"

    def _effective_messages(self) -> list[Message]:
        system = [m.content.strip() for m in self.messages if m.role == "system"]
        system.insert(1, f"Today is {date.today():%A %d %B %Y}. " + (WEB_NOTE if self.web else NO_WEB_NOTE))
        rest = [m for m in self.messages if m.role != "system"]
        return [Message(role="system", content="\n\n".join(system)), *rest]


class LochatHub(Hub):
    name = "lochat"

    def _open_project(self, root: Path) -> None:
        # Not a project: lochat's own folder, whatever folder it was started in.
        root = root.resolve()
        workspace = root / "workspace"  # empty, and the only place a tool could ever look
        workspace.mkdir(parents=True, exist_ok=True)
        config = AppConfig.load(root, **self.overrides)
        self.root = root
        self.config = config
        self.provider = self.provider_factory(**config.provider_kwargs())
        self.sandbox = Sandbox(workspace)
        self.store = SessionStore(root)

    def project_info(self) -> dict[str, Any]:
        return {"path": str(self.root), "label": "your chats", "name": "lochat"}

    def projects(self) -> list[dict[str, Any]]:
        return [{**self.project_info(), "detail": "open now", "current": True}]

    def switch_project(self, path: str) -> dict[str, Any]:
        raise HubError(404, "lochat has no projects")

    def recent_files(self) -> list[str]:
        return []

    def read_file(self, path: str) -> dict[str, Any]:
        raise HubError(404, "lochat doesn't share files")

    def _work(self, run: Run) -> None:
        # A new chat gets a name from the model first, before the answer -
        # rather than being called after its first message.
        if run.base_count <= 1 and not run.stopped:
            title = self._make_title(run.user_text)
            if title and not run.stopped:
                session = run.agent.session  # through the chat's own store, so later saves keep it
                session.rename(session.path, title)
                self.publish("title", chat=run.chat, title=title)
                self.publish("chats")
        super()._work(run)

    def _make_title(self, text: str) -> Optional[str]:
        """A few words naming the chat, or None (no model, odd reply): then
        the first message stays its name, as before."""
        try:
            # Thinking off: a title isn't worth waiting for.
            provider = self.provider_factory(**{**self.config.provider_kwargs(), "think": False})
            reply = None
            for event in provider.chat([Message(role="user", content=TITLE_PROMPT.format(text=text[:1500]))]):
                if event.type == "done":
                    reply = event.message
        except Exception:  # noqa: BLE001 - a missing title never stops the answer
            return None
        title = clean_title(reply.content if reply else "")
        return title or None

    def _new_agent(self, ui: WebUI, store: SessionStore) -> Agent:
        ctx_manager = ContextManager(num_ctx=self.config.num_ctx,
                                     threshold=self.config.context_compaction_threshold)
        return ChatAgent(self.provider, ui, self.sandbox, ctx_manager, store, yolo=False, web=self.web,
                         brave_api_key=self.config.brave_api_key, tavily_api_key=self.config.tavily_api_key)


def _make_hub(cwd: Path, overrides: dict[str, Any]) -> LochatHub:
    return LochatHub(LOCHAT_DIR, overrides)


LOCHAT = ServeApp(
    name="lochat",
    description="Chat with the AI on your PC from your phone.",
    port=8766,
    accent="#a78bfa",
    devices_file=LOCHAT_DIR / "devices.json",
    web_dir=WEB_DIR,
    make_hub=_make_hub,
    serves_folder=False,
    discovery=False,
)

USAGE = """\
lochat - chat with the AI on your PC from your phone

  lochat serve              on your home Wi-Fi
  lochat serve --tunnel     also from anywhere, end-to-end encrypted through Cloudflare
  lochat serve --help       every option
"""


def main(argv: Optional[list[str]] = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] in ("-h", "--help", "help"):
        print(USAGE, end="")
        return 0
    if argv[0] == "serve":
        return serve_main(argv[1:], LOCHAT)
    print(f"lochat: unknown command {argv[0]!r}\n\n{USAGE}", end="", file=sys.stderr)
    return 2
