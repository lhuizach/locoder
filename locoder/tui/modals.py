"""Modal screens: confirmation prompts, model picker, settings editor, and
the conversation browser. All are plain ModalScreen subclasses driven
through ``App.push_screen_wait`` so callers can just ``await`` a result.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from rich.syntax import Syntax
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Select, Static, Switch
from textual.widgets.option_list import Option

from locoder.config import AppConfig
from locoder.providers.registry import PROVIDER_KINDS
from locoder.session import SessionSummary
from locoder.tui.commands import SLASH_COMMANDS


class ConfirmModal(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "decline", show=False),
        Binding("n", "decline", show=False),
        Binding("y", "accept", show=False),
    ]

    def __init__(self, title: str, preview: str, lang: str = "diff") -> None:
        super().__init__()
        self._title = title
        self._preview = preview
        self._lang = lang

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(self._title, id="confirm-title")
            with VerticalScroll(id="confirm-preview-box"):
                if self._lang == "diff":
                    yield Static(Syntax(self._preview, "diff", theme="ansi_dark", word_wrap=True))
                else:
                    yield Static(Text(self._preview))
            with Horizontal(id="confirm-buttons"):
                yield Button("Proceed (y)", id="yes", variant="success")
                yield Button("Cancel (n)", id="no", variant="error")

    def on_mount(self) -> None:
        self.query_one("#no", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def action_accept(self) -> None:
        self.dismiss(True)

    def action_decline(self) -> None:
        self.dismiss(False)


class SelectModal(ModalScreen[Optional[str]]):
    """Generic searchable pick-one-from-a-list modal (models, conversations)."""

    BINDINGS = [
        Binding("escape", "cancel", show=False),
        Binding("up", "move_up", show=False),
        Binding("down", "move_down", show=False),
    ]

    def __init__(self, title: str, options: list[tuple[str, str]], *, empty_message: str = "(nothing to show)") -> None:
        super().__init__()
        self._title = title
        self._options = options
        self._empty_message = empty_message

    def compose(self) -> ComposeResult:
        with Vertical(id="select-box"):
            yield Static(self._title, id="select-title")
            yield Input(placeholder="type to filter...", id="select-filter")
            yield OptionList(id="select-list")

    def on_mount(self) -> None:
        self._populate("")
        self.query_one("#select-filter", Input).focus()

    def _populate(self, fragment: str) -> None:
        option_list = self.query_one("#select-list", OptionList)
        option_list.clear_options()
        frag = fragment.lower()
        filtered = [(label, value) for label, value in self._options if frag in label.lower()]
        if not filtered:
            option_list.add_option(Option(self._empty_message, id=None, disabled=True))
            return
        option_list.add_options(Option(label, id=value) for label, value in filtered)
        option_list.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self._populate(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self._select_highlighted()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        self.dismiss(event.option.id)

    def _select_highlighted(self) -> None:
        option_list = self.query_one("#select-list", OptionList)
        if option_list.highlighted is None or option_list.option_count == 0:
            self.dismiss(None)
            return
        option = option_list.get_option_at_index(option_list.highlighted)
        self.dismiss(option.id)

    def action_move_up(self) -> None:
        self.query_one("#select-list", OptionList).action_cursor_up()

    def action_move_down(self) -> None:
        self.query_one("#select-list", OptionList).action_cursor_down()

    def action_cancel(self) -> None:
        self.dismiss(None)


class SettingsModal(ModalScreen[Optional[AppConfig]]):
    BINDINGS = [Binding("escape", "cancel", show=False)]

    def __init__(self, config: AppConfig) -> None:
        super().__init__()
        self._config = config

    def compose(self) -> ComposeResult:
        with Vertical(id="settings-box"):
            yield Static("Settings", id="settings-title")
            yield Label("Provider")
            yield Select(
                [(k, k) for k in sorted(PROVIDER_KINDS)],
                value=self._config.provider if self._config.provider in PROVIDER_KINDS else "ollama",
                allow_blank=False,
                id="f-provider",
            )
            yield Label("Model")
            yield Input(value=self._config.model, id="f-model")
            yield Label("Base URL")
            yield Input(value=self._config.base_url, id="f-base-url")
            yield Label("Temperature")
            yield Input(value=str(self._config.temperature), id="f-temperature")
            yield Label("Context window (num_ctx)")
            yield Input(value=str(self._config.num_ctx), id="f-num-ctx")
            with Horizontal(classes="settings-row"):
                yield Label("YOLO mode (skip confirmations)")
                yield Switch(value=self._config.yolo, id="f-yolo")
            with Horizontal(id="settings-buttons"):
                yield Button("Save", id="save", variant="success")
                yield Button("Cancel", id="cancel", variant="error")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        title = self.query_one("#settings-title", Static)
        try:
            temperature = float(self.query_one("#f-temperature", Input).value)
            num_ctx = int(self.query_one("#f-num-ctx", Input).value)
        except ValueError:
            title.update("Settings  [red](temperature/num_ctx must be numbers)[/]")
            return

        model = self.query_one("#f-model", Input).value.strip()
        base_url = self.query_one("#f-base-url", Input).value.strip()
        if not model or not base_url:
            title.update("Settings  [red](model and base URL can't be empty)[/]")
            return

        new_config = replace(
            self._config,
            provider=self.query_one("#f-provider", Select).value,
            model=model,
            base_url=base_url,
            temperature=temperature,
            num_ctx=num_ctx,
            yolo=self.query_one("#f-yolo", Switch).value,
        )
        self.dismiss(new_config)

    def action_cancel(self) -> None:
        self.dismiss(None)


class HelpModal(ModalScreen[None]):
    BINDINGS = [
        Binding("escape", "close", show=False),
        Binding("enter", "close", show=False),
    ]

    def compose(self) -> ComposeResult:
        lines = ["[bold]Slash commands[/]  (type / to see this menu inline)"]
        for c in SLASH_COMMANDS:
            aliases = f"  (aka /{', /'.join(c.aliases)})" if c.aliases else ""
            lines.append(f"  /{c.name}{aliases} - {c.description}")
        lines.append("")
        lines.append("[bold]Typing[/]")
        lines.append("  @fragment   mention a project file - its contents get attached to your message")
        lines.append("  /fragment   open the command menu")
        lines.append("  up/down     navigate the open menu, or your message history")
        lines.append("  tab/enter   accept the highlighted menu item")
        lines.append("  escape      close a menu")
        lines.append("")
        lines.append("[dim]press escape or enter to close this[/]")
        with Vertical(id="help-box"):
            yield Static("\n".join(lines))

    def action_close(self) -> None:
        self.dismiss(None)


def conversation_options(summaries: list[SessionSummary]) -> list[tuple[str, str]]:
    options = []
    for s in summaries:
        when = s.updated_at.split("T")[0] if s.updated_at else "?"
        label = f"[{when}] {s.title}  ({s.message_count} messages)"
        options.append((label, str(s.path)))
    return options
