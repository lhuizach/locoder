"""The message input box: a single-line Input with two popup behaviors
layered on top -- a slash-command menu triggered by '/', and a fuzzy file
picker triggered by '@' -- plus simple persistent up/down history.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Optional

from rich.text import Text
from textual.binding import Binding
from textual.message import Message
from textual.widgets import Input, OptionList
from textual.widgets.option_list import Option

from locoder.display import FG, MUTED
from locoder.tui.commands import current_token, filter_commands, filter_files

SLASH_TRIGGER_RE = re.compile(r"^/(\S*)$")


class ChatInput(Input):
    BINDINGS = [
        Binding("up", "history_up", show=False),
        Binding("down", "history_down", show=False),
        Binding("escape", "dismiss_popups", show=False),
        Binding("tab", "accept_popup", show=False),
    ]

    class CommandSelected(Message):
        def __init__(self, command_name: str) -> None:
            self.command_name = command_name
            super().__init__()

    class CycleMode(Message):
        pass

    class InterruptRequested(Message):
        pass

    class DownFromEmpty(Message):
        """Down arrow with nothing typed and no history being browsed."""

    def __init__(self, *, history_file: Path, file_index: list[str], **kwargs) -> None:
        super().__init__(**kwargs)
        self.history_file = history_file
        self.file_index = file_index
        self.history: list[str] = []
        self.history_index: Optional[int] = None
        self._draft = ""
        self._slash_popup: Optional[OptionList] = None
        self._file_popup: Optional[OptionList] = None
        self._active: Optional[OptionList] = None
        self._load_history()

    def _load_history(self) -> None:
        if self.history_file.exists():
            self.history = [
                line for line in self.history_file.read_text(encoding="utf-8").splitlines() if line.strip()
            ]

    def remember(self, text: str) -> None:
        self.history.append(text)
        self.history_index = None
        self._draft = ""
        try:
            self.history_file.parent.mkdir(parents=True, exist_ok=True)
            with self.history_file.open("a", encoding="utf-8") as fh:
                fh.write(text + "\n")
        except OSError:
            pass

    def on_mount(self) -> None:
        self._slash_popup = self.screen.query_one("#slash-popup", OptionList)
        self._file_popup = self.screen.query_one("#file-popup", OptionList)

    # -- popup visibility -----------------------------------------------------

    def watch_value(self, value: str) -> None:
        self._refresh_popups(value, self.cursor_position)

    def _refresh_popups(self, value: str, cursor: int) -> None:
        if self._slash_popup is None or self._file_popup is None:
            return

        slash_match = SLASH_TRIGGER_RE.match(value)
        if slash_match:
            self._show_slash_popup(slash_match.group(1))
            return
        self._hide_popup(self._slash_popup)

        token = current_token(value, cursor)
        if token and token[1].startswith("@"):
            self._show_file_popup(token[1][1:])
        else:
            self._hide_popup(self._file_popup)

    def _show_slash_popup(self, fragment: str) -> None:
        matches = filter_commands(fragment)
        if not matches:
            self._hide_popup(self._slash_popup)
            return
        self._slash_popup.clear_options()
        # Command names in a column, descriptions dimmed beside them.
        width = max(len(c.name) for c in matches) + 4
        self._slash_popup.add_options(
            Option(Text.assemble((f"/{c.name}".ljust(width), FG), (c.description, MUTED)), id=c.name)
            for c in matches
        )
        self._slash_popup.highlighted = 0
        self._slash_popup.display = True
        self._active = self._slash_popup
        self._hide_popup(self._file_popup)

    def _show_file_popup(self, fragment: str) -> None:
        matches = filter_files(self.file_index, fragment)
        if not matches:
            self._hide_popup(self._file_popup)
            return
        self._file_popup.clear_options()
        self._file_popup.add_options(Option(p, id=p) for p in matches)
        self._file_popup.highlighted = 0
        self._file_popup.display = True
        self._active = self._file_popup

    def _hide_popup(self, popup: OptionList) -> None:
        popup.display = False
        if self._active is popup:
            self._active = None

    # -- key handling -----------------------------------------------------------

    def action_history_up(self) -> None:
        if self._active is not None:
            self._active.action_cursor_up()
        else:
            self._navigate_history(-1)

    def action_history_down(self) -> None:
        if self._active is not None:
            self._active.action_cursor_down()
        elif self.history_index is None and not self.value:
            self.post_message(self.DownFromEmpty())
        else:
            self._navigate_history(1)

    def action_dismiss_popups(self) -> None:
        if self._active is not None:
            self._hide_popup(self._active)
        else:
            self.post_message(self.InterruptRequested())

    def action_accept_popup(self) -> None:
        if self._active is not None:
            self._accept_active_popup()
        else:
            self.post_message(self.CycleMode())

    async def action_submit(self) -> None:
        if self._active is not None:
            self._accept_active_popup()
        else:
            await super().action_submit()

    def _accept_active_popup(self) -> None:
        popup = self._active
        if popup is None or popup.highlighted is None or popup.option_count == 0:
            return
        option = popup.get_option_at_index(popup.highlighted)
        if popup is self._slash_popup:
            self._hide_popup(popup)
            self.value = ""
            self.post_message(self.CommandSelected(option.id or ""))
        else:
            self._accept_file_option(option.id or "")

    def _accept_file_option(self, rel_path: str) -> None:
        token = current_token(self.value, self.cursor_position)
        if token is None:
            self._hide_popup(self._file_popup)
            return
        start, _ = token
        new_value = self.value[:start] + "@" + rel_path + " " + self.value[self.cursor_position :]
        self.value = new_value
        self.cursor_position = start + len(rel_path) + 2
        self._hide_popup(self._file_popup)

    def _navigate_history(self, direction: int) -> None:
        if not self.history:
            return
        if self.history_index is None:
            self._draft = self.value
            self.history_index = len(self.history)
        new_index = max(0, min(len(self.history), self.history_index + direction))
        if new_index == self.history_index:
            return
        self.history_index = new_index
        self.value = self._draft if new_index == len(self.history) else self.history[new_index]
        self.cursor_position = len(self.value)
