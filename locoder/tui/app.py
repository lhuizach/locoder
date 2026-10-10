"""The main Textual application: composes the layout, and bridges the
synchronous Agent loop (always run in a worker thread) to the UI.
"""

from __future__ import annotations

from pathlib import Path

from textual.app import App, ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import OptionList

from locoder.agent import MODE_COLORS, MODE_ORDER, Agent
from locoder.config import AppConfig
from locoder.context import ContextManager
from locoder.providers.base import ProviderError
from locoder.providers.registry import create_provider
from locoder.session import SessionStore
from locoder.tui.chat_input import ChatInput
from locoder.tui.commands import build_file_index, expand_file_mentions, find_command
from locoder.tui.modals import ConfirmModal, HelpModal, SelectModal, SettingsModal, conversation_options
from locoder.tui.tui_ui import TuiUI
from locoder.tui.widgets import ChatLog, Sidebar, StatusBar, TopBar

CSS_PATH = str(Path(__file__).parent / "app.tcss")


class LocoderApp(App):
    CSS_PATH = CSS_PATH
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
        self.agent.tool_ctx.notify = self.tui_ui.info
        self._busy = False
        self._model_warmed_up = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="body"):
            with Vertical(id="main-column"):
                yield ChatLog(id="chatlog")
                yield OptionList(id="slash-popup")
                yield OptionList(id="file-popup")
                yield TopBar(id="topbar")
                yield ChatInput(
                    history_file=self.session.locoder_dir / "tui_history",
                    file_index=[],
                    id="chat-input",
                    placeholder="Type a message - / for commands, @ to mention a file",
                )
            yield Sidebar(id="sidebar")
        yield StatusBar(id="statusbar")

    @property
    def chat_log(self) -> ChatLog:
        return self.query_one("#chatlog", ChatLog)

    @property
    def status_bar(self) -> StatusBar:
        return self.query_one("#statusbar", StatusBar)

    @property
    def top_bar(self) -> TopBar:
        return self.query_one("#topbar", TopBar)

    @property
    def sidebar(self) -> Sidebar:
        return self.query_one("#sidebar", Sidebar)

    @property
    def chat_input(self) -> ChatInput:
        return self.query_one("#chat-input", ChatInput)

    def on_mount(self) -> None:
        self.chat_log.set_model_name(self.agent.provider.model)
        self.chat_log.set_mode_name(self.agent.mode.value.capitalize())
        self._refresh_top_bar()
        self._refresh_status_bar()
        self._check_backend()
        self.chat_input.focus()
        self.run_worker(self._index_files, thread=True)

    def _index_files(self) -> None:
        index = build_file_index(self.agent.sandbox.root)
        self.call_from_thread(setattr, self.chat_input, "file_index", index)

    def _refresh_top_bar(self) -> None:
        mode_name = self.agent.mode.value.capitalize()
        accent = MODE_COLORS[self.agent.mode]
        self.top_bar.set_info(
            provider=self.config.provider,
            model=self.agent.provider.model,
            base_url=self.agent.provider.base_url,
            yolo=self.agent.yolo,
            project_root=str(self.project_root),
            mode_name=mode_name,
            accent=accent,
        )
        self.sidebar.set_model_info(
            provider=self.config.provider,
            model=self.agent.provider.model,
            yolo=self.agent.yolo,
            mode_name=mode_name,
            accent=accent,
        )
        self.chat_log.set_accent(accent)
        self.status_bar.set_accent(accent)
        self.chat_input.styles.border_left = ("thick", accent)
        self.query_one("#slash-popup").styles.border = ("solid", accent)
        self.query_one("#file-popup").styles.border = ("solid", accent)

    def _refresh_status_bar(self) -> None:
        used, limit = self.agent.token_usage()
        self.status_bar.set_usage(used=used, limit=limit)
        self.sidebar.set_usage(used=used, limit=limit)

    def _check_backend(self) -> None:
        def worker() -> None:
            try:
                self.agent.provider.list_models()
            except ProviderError as exc:
                self.call_from_thread(
                    self.chat_log.add_warn,
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
            self.chat_log.add_warn("interrupt requested - stopping...")
        else:
            self.chat_input.value = ""

    def _cycle_mode(self) -> None:
        idx = MODE_ORDER.index(self.agent.mode)
        new_mode = MODE_ORDER[(idx + 1) % len(MODE_ORDER)]
        self.agent.set_mode(new_mode)
        mode_name = new_mode.value.capitalize()
        self.chat_log.set_mode_name(mode_name)
        self._refresh_top_bar()
        self.chat_log.add_system(f"mode: {mode_name}")

    def _dispatch_command_text(self, text: str) -> None:
        parts = text[1:].split(maxsplit=1)
        name = parts[0] if parts else ""
        arg = parts[1].strip() if len(parts) > 1 else ""
        self._dispatch_command(name, arg)

    def _dispatch_command(self, name: str, arg: str) -> None:
        command = find_command(name)
        if command is None:
            self.chat_log.add_warn(f"unknown command: /{name} (try /help)")
            return

        if command.name == "help":
            self.push_screen(HelpModal())
        elif command.name == "clear":
            self.agent.clear()
            self.chat_log.remove_children()
            self.chat_log.add_system("conversation cleared; started a new session")
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
            self._refresh_top_bar()
            self.chat_log.add_system(f"yolo mode {'enabled' if self.agent.yolo else 'disabled'}")
        elif command.name == "tokens":
            used, limit = self.agent.token_usage()
            self.chat_log.add_system(f"context usage: ~{used:,} / {limit:,} tokens")
        elif command.name == "exit":
            self.exit()

    def _send_user_message(self, text: str) -> None:
        if self._busy:
            self.chat_log.add_warn("still working on the previous message - please wait (esc to cancel it)")
            return
        self._busy = True
        label = "initializing..." if not self._model_warmed_up else "thinking..."
        self.status_bar.set_busy(True, label)
        self.chat_log.add_user_message(text)
        self.chat_log.start_thinking(label)
        expanded = expand_file_mentions(text, self.agent.sandbox)
        self.run_worker(lambda: self._run_turn(expanded), thread=True, exclusive=True, group="agent-turn")

    def _run_turn(self, expanded_text: str) -> None:
        try:
            self.agent.run_turn(expanded_text)
        finally:
            self.call_from_thread(self._on_turn_done)

    def _on_turn_done(self) -> None:
        self._busy = False
        self._model_warmed_up = True
        self.status_bar.set_busy(False)
        self._refresh_status_bar()

    # -- confirm bridge (invoked via call_from_thread from TuiUI.confirm) -----

    async def request_confirm(self, title: str, preview: str, lang: str) -> bool:
        result = await self.push_screen_wait(ConfirmModal(title, preview, lang))
        return bool(result)

    # -- model picker -----------------------------------------------------------

    def _open_model_picker(self) -> None:
        self.chat_log.add_info("loading models...")

        def worker() -> None:
            try:
                models = self.agent.provider.list_models()
            except ProviderError as exc:
                self.call_from_thread(self.chat_log.add_error, f"could not list models: {exc}")
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
        self._refresh_top_bar()
        self.chat_log.add_system(f"switched model to '{model_name}'")

    # -- settings ---------------------------------------------------------------

    def _open_settings(self) -> None:
        async def handle_result() -> None:
            new_config = await self.push_screen_wait(SettingsModal(self.config))
            if new_config is None:
                return
            try:
                new_provider = create_provider(**new_config.provider_kwargs())
            except ValueError as exc:
                self.chat_log.add_error(str(exc))
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
            self._refresh_top_bar()
            self._refresh_status_bar()
            self.chat_log.add_system("settings updated and saved to .locoder/config.yaml")

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

    def _resume_conversation(self, path: Path) -> None:
        messages = self.session.resume(path)
        self.agent.messages = messages
        self.chat_log.remove_children()
        for m in messages:
            if m.role == "user":
                self.chat_log.add_user_message(m.content)
            elif m.role == "assistant" and m.content:
                self.chat_log.replay_assistant_message(m.content)
        self.chat_log.add_system(f"resumed conversation from {path.name}")
        self._refresh_status_bar()
