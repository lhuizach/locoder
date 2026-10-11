"""The home screen shown while a conversation is empty: a horizontal lockup
of the logo beside a pixel "locoder" wordmark, the prompt box below it
(laid out by app.tcss), a one-line note on where the model runs, and a
"continue where you left off" panel of recent conversations, files and
projects.

The images are drawn with half-block characters, so each text cell holds
two square "pixels" and the shapes keep their proportions in any terminal.
"""

from __future__ import annotations

from pathlib import Path

from rich.align import Align
from rich.table import Table
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.message import Message
from textual.widgets import OptionList, Static
from textual.widgets.option_list import Option

from locoder import __version__
from locoder.display import FG as WORDMARK_COLOR
from locoder.display import LOGO, pixels_to_text, where_it_runs, wordmark_bitmap
from locoder.display import MUTED as DIM
from locoder.recent import RecentItem

__all__ = ["HomeFooter", "HomeLogo", "HomeRecent", "where_it_runs"]

RECENT_TABS = ("conversations", "files", "projects")
EMPTY_TAB = {
    "conversations": "no conversations in this project yet",
    "files": "no files here yet",
    "projects": "other folders you open locoder in will show up here",
}


class HomeLogo(Static):
    """The logo, in the current mode's colour, beside the wordmark."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._accent = WORDMARK_COLOR

    def on_mount(self) -> None:
        self._draw()

    def set_accent(self, accent: str) -> None:
        if accent != self._accent:
            self._accent = accent
            self._draw()

    def _draw(self) -> None:
        lockup = Table.grid(padding=(0, 3))
        lockup.add_column(vertical="middle")
        lockup.add_column(vertical="middle")
        lockup.add_row(pixels_to_text(LOGO, self._accent), pixels_to_text(wordmark_bitmap("locoder"), WORDMARK_COLOR))
        # Centred as a block: centring line by line would trim each line's
        # trailing blanks and knock the pixel rows out of alignment.
        self.update(Align.center(lockup))


class RecentTab(Static):
    """One clickable tab label."""

    def __init__(self, name: str, **kwargs) -> None:
        super().__init__(name, **kwargs)
        self.tab = name

    def on_click(self) -> None:
        recent = self.screen.query_one(HomeRecent)
        recent.select_tab(self.tab)
        recent.focus_list()


class RecentList(OptionList):
    """The items of the open tab. Left/right switch tabs; up from the first
    item hands focus back to the prompt.
    """

    BINDINGS = [
        Binding("left", "tab(-1)", show=False),
        Binding("right", "tab(1)", show=False),
    ]

    class LeaveTop(Message):
        pass

    def action_tab(self, step: int) -> None:
        self.screen.query_one(HomeRecent).step_tab(step)

    def action_cursor_up(self) -> None:
        if self.highlighted in (None, 0):
            self.post_message(self.LeaveTop())
        else:
            super().action_cursor_up()


class HomeRecent(Vertical):
    """"continue where you left off": tabs of recent conversations, recently
    edited files and other projects. Picking an item posts ``Chosen``.
    """

    class Chosen(Message):
        def __init__(self, item: RecentItem) -> None:
            super().__init__()
            self.item = item

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._items: dict[str, list[RecentItem]] = {tab: [] for tab in RECENT_TABS}
        self._tab = RECENT_TABS[0]
        self._user_picked_tab = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="recent-tabs"):
            yield Static(Text("continue where you left off", style=DIM), id="recent-title")
            for tab in RECENT_TABS:
                yield RecentTab(tab, classes="recent-tab", id=f"recent-tab-{tab}")
        yield RecentList(id="recent-list", compact=True)

    @property
    def has_items(self) -> bool:
        return any(self._items.values())

    def set_items(self, items: dict[str, list[RecentItem]]) -> None:
        self._items = {tab: items.get(tab, []) for tab in RECENT_TABS}
        self._user_picked_tab = False
        self._show_best_tab()

    def update_tab(self, tab: str, items: list[RecentItem]) -> None:
        """Fill one tab later (the file scan) without yanking the user's tab."""
        self._items[tab] = items
        if self._user_picked_tab:
            self.set_class(not self.has_items, "-empty")
            if tab == self._tab:
                self.select_tab(tab, by_user=True)
        else:
            self._show_best_tab()

    def _show_best_tab(self) -> None:
        self.set_class(not self.has_items, "-empty")
        # Open the first tab that has something in it.
        self.select_tab(next((t for t in RECENT_TABS if self._items[t]), RECENT_TABS[0]), by_user=False)

    def step_tab(self, step: int) -> None:
        i = RECENT_TABS.index(self._tab)
        self.select_tab(RECENT_TABS[(i + step) % len(RECENT_TABS)])

    def select_tab(self, tab: str, *, by_user: bool = True) -> None:
        self._tab = tab
        self._user_picked_tab = self._user_picked_tab or by_user
        for name in RECENT_TABS:
            self.query_one(f"#recent-tab-{name}").set_class(name == tab, "-active")
        option_list = self.query_one(RecentList)
        option_list.clear_options()
        items = self._items[tab]
        if not items:
            option_list.add_option(Option(Text(EMPTY_TAB[tab], style=DIM), disabled=True))
            return
        for i, item in enumerate(items):
            row = Table.grid(expand=True)
            row.add_column(no_wrap=True, overflow="ellipsis", ratio=1)
            row.add_column(justify="right", no_wrap=True)
            row.add_row(Text(item.label, style=WORDMARK_COLOR), Text(item.detail, style=DIM))
            option_list.add_option(Option(row, id=str(i)))
        option_list.highlighted = 0

    def focus_list(self) -> bool:
        """Move focus into the list; False if there's nothing to pick."""
        if not self._items[self._tab]:
            return False
        self.query_one(RecentList).focus()
        return True

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        event.stop()
        if event.option.id is not None:
            self.post_message(self.Chosen(self._items[self._tab][int(event.option.id)]))


class HomeFooter(Static):
    """Project folder on the left, version on the right."""

    def show(self, project_root: Path) -> None:
        path = str(project_root)
        home = str(Path.home())
        if path.lower().startswith(home.lower()):
            path = "~" + path[len(home):]
        grid = Table.grid(expand=True)
        grid.add_column(justify="left")
        grid.add_column(justify="right")
        grid.add_row(Text(path, style=DIM), Text(f"v{__version__}", style=DIM))
        self.update(grid)
