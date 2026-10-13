"""The main Textual application: composes the layout, and bridges the
synchronous Agent loop (always run in a worker thread) to the UI.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from rich.text import Text
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.color import Color
from textual.containers import Horizontal, Vertical
from textual.widgets import OptionList, Static

from locoder.agent import MODE_COLORS, MODE_ORDER, Agent
from locoder.config import AppConfig
from locoder.context import ContextManager
from locoder.providers.base import Message, ProviderError
from locoder.providers.registry import create_provider
from locoder.session import SessionStore
from locoder.tools.base import ToolResult
from locoder.tui.chat_input import ChatInput
from locoder.tui.commands import build_file_index, expand_file_mentions, find_command
from locoder.recent import RecentItem, recent_conversations, recent_files, recent_projects
from locoder.tui.home import HomeFooter, HomeLogo, HomeRecent, RecentList, where_it_runs
from locoder.tui.modals import ConfirmModal, HelpModal, SelectModal, SettingsModal, conversation_options
from locoder.tui.tui_ui import TuiUI
from locoder.tui.widgets import ChatLog, Sidebar, StatusBar

CSS_PATH = str(Path(__file__).parent / "app.tcss")
ATTACHED_FILE_MARKER = "\n\n### Attached file: "
# Matches #sidebar { width } in app.tcss; the collapse animation runs to and from it.
SIDEBAR_WIDTH = 34
SIDEBAR_ANIMATION = 0.22

# Saved tool messages don't record success/failure; these openings are how
# the tools and dispatcher phrase failures.
_FAILURE_PREFIXES = (
    "not found:", "permission denied:", "unexpected error", "unknown tool", "user declined",
    "old_string", "command timed out", "search timed out", "ripgrep failed", "invalid regex",
)


@dataclass(frozen=True)
class SwitchProject:
    """Returned from LocoderApp.run() when the user picks another project on
    the home screen; the CLI then starts locoder again in that folder.
    """

    path: Path


def _looks_failed(output: str) -> bool:
    if output.startswith("exit code: "):
        return not output.startswith("exit code: 0\n") and output.strip() != "exit code: 0"
    return output.startswith(_FAILURE_PREFIXES) or "outside the sandbox root" in output


class LocoderApp(App):
    CSS_PATH = CSS_PATH
    BINDINGS = [Binding("ctrl+b", "toggle_sidebar", "toggle sidebar", show=False)]
    TITLE = "locoder"

    def __init__(self, agent: Agent, session: SessionStore, config: AppConfig, project_root: Path) -> None:
        super().__init__()
        self.agent = agent
        self.session = session
        self.config = config
        self.project_root = project_root
        self.tui_ui = TuiUI(self)
        # Route the agent's output through Textual widgets instead of the
        # rich console. `tool_ctx.confirm` stays bound to Agent._confirm,
        # which reads self.ui dynamically and already handles the yolo
        # short-circuit - overwriting it here directly would bypass yolo.
        self.agent.ui = self.tui_ui
        self.agent.tool_ctx.notify = self.tui_ui.notice
        self._busy = False
        self._model_warmed_up = False
        self._home = False
        self._home_notes: list[Text] = []
        self._sidebar_hidden = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            with Vertical(id="main-column"):
                yield HomeLogo(id="home-logo")
                yield ChatLog(id="chatlog")
                yield OptionList(id="slash-popup")
                yield OptionList(id="file-popup")
                with Vertical(id="prompt-box"):
                    yield ChatInput(
                        history_file=self.session.locoder_dir / "tui_history",
                        file_index=[],
                        id="chat-input",
                        placeholder='Ask anything... "fix the failing tests"',
                    )
                yield Static(id="home-notice")
                yield HomeRecent(id="home-recent")
                yield Static(id="home-spacer")
            yield Sidebar(id="sidebar")
        yield StatusBar(id="statusbar")
        yield HomeFooter(id="home-footer")

    @property
    def accent(self) -> str:
        """The current mode's colour; dialogs use it for their titles and highlights."""
        return MODE_COLORS[self.agent.mode]

    @property
    def chat_log(self) -> ChatLog:
        return self.query_one("#chatlog", ChatLog)

    @property
    def status_bar(self) -> StatusBar:
        return self.query_one("#statusbar", StatusBar)

    @property
    def sidebar(self) -> Sidebar:
        return self.query_one("#sidebar", Sidebar)

    @property
    def chat_input(self) -> ChatInput:
        return self.query_one("#chat-input", ChatInput)

    def on_mount(self) -> None:
        self.chat_log.set_model_name(self.agent.provider.model)
        self.chat_log.set_mode_name(self.agent.mode.value.capitalize())
        self._refresh_mode_display()
        self._refresh_status_bar()
        self.query_one(HomeFooter).show(self.project_root)
        self.status_bar.set_project(str(self.project_root))
        self._show_home(True)
        for warning in self.config.warnings:
            self._warn(warning)
        self._check_backend()
        self.chat_input.focus()
        self.run_worker(self._index_files, thread=True)

    def _index_files(self) -> None:
        index = build_file_index(self.agent.sandbox.root)
        self.call_from_thread(setattr, self.chat_input, "file_index", index)

    def _refresh_mode_display(self) -> None:
        mode_name = self.agent.mode.value.capitalize()
        accent = MODE_COLORS[self.agent.mode]
        # The prompt box carries the mode in its top border and the model in
        # its bottom border; the logo takes the mode's colour.
        box = self.query_one("#prompt-box")
        box.styles.border = ("round", Color.parse(accent).with_alpha(0.55))
        box.border_title = Text(f" {mode_name} ", style=f"bold {accent}")
        subtitle = Text.assemble(" ", (self.agent.provider.model, "#c8c8c8"), (" · ", "#555555"),
                                 (self.config.provider, "#808080"))
        if self.agent.yolo:
            subtitle.append_text(Text.assemble((" · ", "#555555"), ("yolo", f"bold {accent}")))
        subtitle.append(" ")
        box.border_subtitle = subtitle
        self.query_one(HomeLogo).set_accent(accent)
        self._render_home_notice()
        self.sidebar.set_model_info(
            provider=self.config.provider,
            model=self.agent.provider.model,
            yolo=self.agent.yolo,
            mode_name=mode_name,
            accent=accent,
        )
        self.chat_log.set_accent(accent)
        self.status_bar.set_accent(accent)
        for popup in ("#slash-popup", "#file-popup"):
            self.query_one(popup).styles.border = ("round", Color.parse(accent).with_alpha(0.55))

    def _refresh_status_bar(self) -> None:
        used, limit = self.agent.token_usage()
        self.status_bar.set_usage(used=used, limit=limit)
        self.sidebar.set_usage(used=used, limit=limit)

    # -- home screen --------------------------------------------------------------

    def _show_home(self, on: bool) -> None:
        """The home screen is shown while the conversation is empty."""
        self._home = on
        self._home_notes = []
        self._render_home_notice()
        self.screen.set_class(on, "home")
        if on:
            self._load_recent()
        self.chat_input.focus()

    def _load_recent(self) -> None:
        """Fill "continue where you left off" (file scan off the UI thread)."""
        recent = self.query_one(HomeRecent)
        recent.set_items({
            "conversations": recent_conversations(self.session),
            "projects": recent_projects(self.project_root),
        })

        def scan() -> None:
            files = recent_files(self.project_root)
            self.call_from_thread(self._set_recent_files, files)

        self.run_worker(scan, thread=True, group="recent-files", exclusive=True)

    def _set_recent_files(self, files: list[RecentItem]) -> None:
        if self._home:
            self.query_one(HomeRecent).update_tab("files", files)

    def on_chat_input_down_from_empty(self, message: ChatInput.DownFromEmpty) -> None:
        if self._home:
            self.query_one(HomeRecent).focus_list()

    def on_recent_list_leave_top(self, message: RecentList.LeaveTop) -> None:
        self.chat_input.focus()

    def on_home_recent_chosen(self, message: HomeRecent.Chosen) -> None:
        item = message.item
        if item.kind == "conversation":
            self._resume_conversation(Path(item.value))
        elif item.kind == "file":
            # Attach it: @mentions are expanded into the file's contents on send.
            self.chat_input.value = f"@{item.value} "
            self.chat_input.cursor_position = len(self.chat_input.value)
            self.chat_input.focus()
        elif item.kind == "project":
            self.exit(SwitchProject(Path(item.value)))

    def action_toggle_sidebar(self) -> None:
        """Ctrl+B or /sidebar: the sidebar narrows and fades out (or grows
        and fades in). The hidden state is a class on the screen, so the home
        screen - which never shows the sidebar - keeps hiding it either way.
        """
        self._sidebar_hidden = not self._sidebar_hidden
        hidden = self._sidebar_hidden
        self.status_bar.set_sidebar_hidden(hidden)
        sidebar = self.sidebar
        if self._home:
            self.screen.set_class(hidden, "sidebar-hidden")
            return

        def finished() -> None:
            # Only if nothing toggled it back while it was moving.
            if self._sidebar_hidden:
                self.screen.add_class("sidebar-hidden")

        if hidden:
            sidebar.styles.animate("width", 0, duration=SIDEBAR_ANIMATION, easing="out_cubic", on_complete=finished)
            sidebar.styles.animate("opacity", 0.0, duration=SIDEBAR_ANIMATION, easing="out_cubic")
        else:
            sidebar.styles.width = 0
            sidebar.styles.opacity = 0.0
            self.screen.remove_class("sidebar-hidden")
            sidebar.styles.animate("width", SIDEBAR_WIDTH, duration=SIDEBAR_ANIMATION, easing="out_cubic")
            sidebar.styles.animate("opacity", 1.0, duration=SIDEBAR_ANIMATION, easing="out_cubic")

    def _render_home_notice(self) -> None:
        """Under the box: where the model runs, then any recent status lines."""
        lines = [where_it_runs(self.agent.provider.base_url), *self._home_notes]
        self.query_one("#home-notice", Static).update(Text("\n").join(lines))

    def _say(self, kind: str, msg: str) -> None:
        """A status line: into the transcript, or under the box on the home screen."""
        if not self._home:
            getattr(self.chat_log, f"add_{kind}")(msg)
            return
        if kind == "system" and msg.startswith("mode: "):
            return  # the box already shows the mode
        style = {"warn": "yellow", "error": "bold red", "info": "dim"}.get(kind, "#808080")
        prefix = {"warn": "warning: ", "error": "error: "}.get(kind, "")
        self._home_notes = (self._home_notes + [Text(prefix + msg, style=style)])[-3:]
        self._render_home_notice()

    def _warn(self, msg: str) -> None:
        self._say("warn", msg)

    def _check_backend(self) -> None:
        def worker() -> None:
            try:
                self.agent.provider.list_models()
            except ProviderError as exc:
                self.call_from_thread(
                    self._warn,
                    f"could not reach the model backend yet ({exc}); will retry when you send a message",
                )

        self.run_worker(worker, thread=True)

    # -- input handling -------------------------------------------------------

    def on_input_submitted(self, event) -> None:  # noqa: ANN001 - Input.Submitted
        if event.input is not self.chat_input:
            return
        event.stop()
        text = event.value.strip()
        self.chat_input.value = ""
        if not text:
            return
        self.chat_input.remember(text)
        if text.startswith("/"):
            self._dispatch_command_text(text)
        else:
            self._send_user_message(text)

    def on_chat_input_command_selected(self, message: ChatInput.CommandSelected) -> None:
        self._dispatch_command(message.command_name, "")

    def on_chat_input_cycle_mode(self, message: ChatInput.CycleMode) -> None:
        self._cycle_mode()

    def on_chat_input_interrupt_requested(self, message: ChatInput.InterruptRequested) -> None:
        if self._busy:
            self.agent.request_cancel()
            self._say("warn", "interrupt requested - stopping...")
        else:
            self.chat_input.value = ""

    def _cycle_mode(self) -> None:
        idx = MODE_ORDER.index(self.agent.mode)
        new_mode = MODE_ORDER[(idx + 1) % len(MODE_ORDER)]
        self.agent.set_mode(new_mode)
        mode_name = new_mode.value.capitalize()
        self.chat_log.set_mode_name(mode_name)
        self._refresh_mode_display()
        self._say("system", f"mode: {mode_name}")

    def _dispatch_command_text(self, text: str) -> None:
        parts = text[1:].split(maxsplit=1)
        name = parts[0] if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""
        self._dispatch_command(name, arg)

    def _dispatch_command(self, name: str, arg: str) -> None:
        command = find_command(name)
        if command is None:
            self._say("warn", f"unknown command: /{name} (try /help)")
            return

        if command.name == "help":
            self.push_screen(HelpModal())
        elif command.name == "clear":
            self.agent.clear()
            self.chat_log.remove_children()
            self._show_home(True)
            self._refresh_status_bar()
        elif command.name == "model":
            if arg:
                self._switch_model(arg)
            else:
                self._open_model_picker()
        elif command.name == "settings":
            self._open_settings()
        elif command.name == "conversations":
            self._open_conversations()
        elif command.name == "yolo":
            self.agent.set_yolo(not self.agent.yolo)
            self._refresh_mode_display()
            self._say("system", f"yolo mode {'enabled' if self.agent.yolo else 'disabled'}")
        elif command.name == "sidebar":
            self.action_toggle_sidebar()
        elif command.name == "tokens":
            used, limit = self.agent.token_usage()
            self._say("system", f"context usage: ~{used:,} / {limit:,} tokens")
        elif command.name == "exit":
            self.exit()

    def _send_user_message(self, text: str) -> None:
        if self._busy:
            self._say("warn", "still working on the previous message - please wait (esc to cancel it)")
            return
        self._busy = True
        self._show_home(False)
        label = "loading model..." if not self._model_warmed_up else "thinking..."
        self.status_bar.set_busy(True, label)
        self.chat_log.add_user_message(text)
        self.chat_log.start_thinking("Loading model" if not self._model_warmed_up else "Thinking")
        expanded = expand_file_mentions(text, self.agent.sandbox)
        self.run_worker(lambda: self._run_turn(expanded), thread=True, exclusive=True, group="agent-turn")

    def _run_turn(self, expanded_text: str) -> None:
        try:
            self.agent.run_turn(expanded_text)
        finally:
            self.call_from_thread(self._on_turn_done)

    def _on_turn_done(self) -> None:
        self.chat_log.set_model_name(self.agent.provider.model)
        self.chat_log.finish_turn()
        self._busy = False
        self._model_warmed_up = True
        self.status_bar.set_busy(False)
        self._refresh_status_bar()

    # -- confirm bridge (invoked via call_from_thread from TuiUI.confirm) -----

    async def request_confirm(self, title: str, preview: str, lang: str, reasons: list[str] | None = None) -> bool:
        result = await self.push_screen_wait(ConfirmModal(title, preview, lang, reasons=reasons))
        return bool(result)

    # -- model picker -----------------------------------------------------------

    def _open_model_picker(self) -> None:
        self._say("info", "loading models...")

        def worker() -> None:
            try:
                models = self.agent.provider.list_models()
            except ProviderError as exc:
                self.call_from_thread(self._say, "error", f"could not list models: {exc}")
                return
            self.call_from_thread(self._show_model_picker, models)

        self.run_worker(worker, thread=True)

    def _show_model_picker(self, models: list[str]) -> None:
        options = [(m, m) for m in models]

        async def handle_result() -> None:
            chosen = await self.push_screen_wait(
                SelectModal("Select a model", options, empty_message="(no models found on this backend)")
            )
            if chosen:
                self._switch_model(chosen)

        self.run_worker(handle_result())

    def _switch_model(self, model_name: str) -> None:
        self.agent.set_provider(self.agent.provider.with_model(model_name))
        self.config.model = model_name
        self.chat_log.set_model_name(model_name)
        self._model_warmed_up = False
        self._refresh_mode_display()
        self._say("system", f"switched model to '{model_name}'")

    # -- settings ---------------------------------------------------------------

    def _open_settings(self) -> None:
        async def handle_result() -> None:
            new_config = await self.push_screen_wait(SettingsModal(self.config))
            if new_config is None:
                return
            try:
                new_provider = create_provider(**new_config.provider_kwargs())
            except ValueError as exc:
                self._say("error", str(exc))
                return
            self.config = new_config
            self.agent.set_provider(new_provider)
            self.agent.ctx_manager = ContextManager(
                num_ctx=new_config.num_ctx, threshold=new_config.context_compaction_threshold
            )
            self.agent.set_yolo(new_config.yolo)
            new_config.save(self.project_root)
            self.chat_log.set_model_name(new_config.model)
            self._model_warmed_up = False
            self._refresh_mode_display()
            self._refresh_status_bar()
            self._say("system", 
                "settings updated and saved to .locoder/config.yaml "
                "(yolo applies to this session only - set it in ~/.locoder/config.yaml to keep it)"
            )

        self.run_worker(handle_result())

    # -- conversations ------------------------------------------------------------

    def _open_conversations(self) -> None:
        summaries = self.session.summarize_all()
        options = conversation_options(summaries)

        async def handle_result() -> None:
            chosen = await self.push_screen_wait(
                SelectModal("Conversations", options, empty_message="(no saved conversations yet)")
            )
            if chosen:
                self._resume_conversation(Path(chosen))

        self.run_worker(handle_result())

    def _replay(self, messages: list[Message]) -> None:
        """Redraw a saved conversation in the same compact turn layout as a
        live one: tool calls as single lines, narration dimmed, answer last.
        """
        results = {m.tool_call_id: m.content for m in messages if m.role == "tool" and m.tool_call_id}
        log = self.chat_log
        for i, m in enumerate(messages):
            if m.role == "user":
                log.finish_turn(footer="")
                # Hide the file contents an @mention attached.
                log.add_user_message(m.content.split(ATTACHED_FILE_MARKER, 1)[0])
            elif m.role == "assistant":
                if m.content.strip():
                    log.replay_text(m.content, narration=bool(m.tool_calls))
                for tc in m.tool_calls:
                    output = results.get(tc.id)
                    result = None if output is None else ToolResult(output=output, is_error=_looks_failed(output))
                    log.replay_tool(tc.name, tc.arguments, result)
        log.finish_turn(footer="")

    def _resume_conversation(self, path: Path) -> None:
        messages = self.session.resume(path)
        self.agent.messages = messages
        self.chat_log.remove_children()
        self._show_home(False)
        self._replay(messages)
        self._say("system", f"resumed conversation from {path.name}")
        self._refresh_status_bar()
