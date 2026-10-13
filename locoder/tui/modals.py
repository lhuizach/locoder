"""Modal screens: confirmation prompts, model picker, settings editor, and
the conversation browser. All are plain ModalScreen subclasses driven
through ``App.push_screen_wait`` so callers can just ``await`` a result.

They share the app's look: a rounded outline over a dimmed screen, the
title sitting in the top border in the current mode's colour, and flat
compact controls.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Optional

from rich.syntax import Syntax
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, OptionList, Select, Static, Switch
from textual.widgets.option_list import Option

from locoder.config import AppConfig
from locoder.display import ERROR, FG, MUTED, SECONDARY
from locoder.providers.registry import PROVIDER_KINDS
from locoder.session import SessionSummary
from locoder.tui.commands import SLASH_COMMANDS

DESTRUCTIVE_MARK = "[DESTRUCTIVE - double check this]"


def _accent(screen: ModalScreen) -> str:
    return getattr(screen.app, "accent", FG)


def _title(text: str, color: str) -> Text:
    return Text(f" {text} ", style=f"bold {color}")


def _key_hint(*pairs: tuple[str, str]) -> Text:
    hint = Text()
    for i, (key, action) in enumerate(pairs):
        if i:
            hint.append("   ")
        hint.append(key, style=SECONDARY)
        hint.append(f" {action}", style=MUTED)
    return hint


class ConfirmModal(ModalScreen[bool]):
    BINDINGS = [
        Binding("escape", "decline", show=False),
        Binding("n", "decline", show=False),
        Binding("y", "accept", show=False),
    ]

    def __init__(self, title: str, preview: str, lang: str = "diff", *, reasons: Optional[list[str]] = None) -> None:
        super().__init__()
        self._reasons = reasons or []
        self._destructive = bool(self._reasons) or DESTRUCTIVE_MARK in title
        self._title = title.replace(DESTRUCTIVE_MARK, "").strip()
        self._preview = preview
        self._lang = lang

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            if self._destructive:
                warning = Text("careful - read this before you approve:", style=f"bold {ERROR}")
                for reason in self._reasons or ["this command looks destructive"]:
                    warning.append(f"\n  • {reason}", style=ERROR)
                yield Static(warning, id="confirm-warning")
            with VerticalScroll(id="confirm-preview-box"):
                if self._lang == "diff":
                    yield Static(Syntax(self._preview, "diff", theme="ansi_dark", word_wrap=True,
                                        background_color="default"))
                else:
                    yield Static(Text(self._preview, style=FG))
            with Horizontal(id="confirm-buttons"):
                yield Static(_key_hint(("y", "proceed"), ("n", "cancel"), ("esc", "cancel")), id="confirm-hint")
                yield Button("proceed", id="yes", compact=True)
                yield Button("cancel", id="no", compact=True)

    def on_mount(self) -> None:
        box = self.query_one("#confirm-box")
        color = ERROR if self._destructive else _accent(self)
        box.border_title = _title(self._title, color)
        box.styles.border = ("round", color)
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
            yield Input(placeholder="type to filter...", id="select-filter", compact=True)
            yield OptionList(id="select-list", compact=True)
            yield Static(_key_hint(("↑↓", "move"), ("enter", "choose"), ("esc", "close")), id="select-hint")

    def on_mount(self) -> None:
        box = self.query_one("#select-box")
        box.border_title = _title(self._title.lower(), _accent(self))
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
            yield Label("provider", classes="field-label")
            yield Select(
                [(k, k) for k in sorted(PROVIDER_KINDS)],
                value=self._config.provider if self._config.provider in PROVIDER_KINDS else "ollama",
                allow_blank=False,
                id="f-provider",
                compact=True,
            )
            yield Label("model", classes="field-label")
            yield Input(value=self._config.model, id="f-model", compact=True)
            yield Label("base url", classes="field-label")
            yield Input(value=self._config.base_url, id="f-base-url", compact=True)
            with Horizontal(classes="settings-pair"):
                with Vertical():
                    yield Label("temperature", classes="field-label")
                    yield Input(value=str(self._config.temperature), id="f-temperature", compact=True)
                with Vertical():
                    yield Label("context window", classes="field-label")
                    yield Input(value=str(self._config.num_ctx), id="f-num-ctx", compact=True)
            with Horizontal(classes="settings-row"):
                yield Label(Text.assemble(("yolo", FG), ("  skip approvals except risky ones, this session only", MUTED)))
                yield Switch(value=self._config.yolo, id="f-yolo")
            yield Static("", id="settings-error")
            with Horizontal(id="settings-buttons"):
                yield Static(_key_hint(("esc", "cancel")), id="settings-hint")
                yield Button("save", id="save", compact=True)
                yield Button("cancel", id="cancel", compact=True)

    def on_mount(self) -> None:
        self.query_one("#settings-box").border_title = _title("settings", _accent(self))
        self.query_one("#f-model", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()

    def _error(self, message: str) -> None:
        self.query_one("#settings-error", Static).update(Text(message, style=ERROR))

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "cancel":
            self.dismiss(None)
            return
        try:
            temperature = float(self.query_one("#f-temperature", Input).value)
            num_ctx = int(self.query_one("#f-num-ctx", Input).value)
        except ValueError:
            self._error("temperature and context window must be numbers")
            return

        model = self.query_one("#f-model", Input).value.strip()
        base_url = self.query_one("#f-base-url", Input).value.strip()
        if not model or not base_url:
            self._error("model and base url can't be empty")
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
        with Vertical(id="help-box"):
            yield Static(id="help-body")

    def on_mount(self) -> None:
        accent = _accent(self)
        self.query_one("#help-box").border_title = _title("help", accent)

        commands = Table.grid(padding=(0, 2))
        commands.add_column(no_wrap=True)
        commands.add_column()
        for c in SLASH_COMMANDS:
            aliases = f"  /{', /'.join(c.aliases)}" if c.aliases else ""
            commands.add_row(Text.assemble((f"/{c.name}", accent), (aliases, MUTED)), Text(c.description, style=SECONDARY))

        keys = Table.grid(padding=(0, 2))
        keys.add_column(no_wrap=True)
        keys.add_column()
        for key, what in [
            ("@file", "attach a project file's contents to your message"),
            ("/", "open the command menu"),
            ("↑ ↓", "move in the open menu, or through your message history"),
            ("tab", "accept the menu item - or, with no menu open, switch mode"),
            ("esc", "close a menu, or stop the current answer"),
            ("ctrl+b", "show or hide the sidebar"),
        ]:
            keys.add_row(Text(key, style=FG), Text(what, style=SECONDARY))

        body = Table.grid()
        body.add_row(Text("commands", style=MUTED))
        body.add_row(commands)
        body.add_row(Text(""))
        body.add_row(Text("keys", style=MUTED))
        body.add_row(keys)
        body.add_row(Text(""))
        body.add_row(_key_hint(("esc", "close")))
        self.query_one("#help-body", Static).update(body)

    def action_close(self) -> None:
        self.dismiss(None)


def conversation_options(summaries: list[SessionSummary]) -> list[tuple[str, str]]:
    options = []
    for s in summaries:
        when = s.updated_at.split("T")[0] if s.updated_at else "?"
        label = f"{when}   {s.title}   ({s.message_count} messages)"
        options.append((label, str(s.path)))
    return options
