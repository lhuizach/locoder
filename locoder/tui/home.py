"""The home screen shown while a conversation is empty: a horizontal lockup
of the logo beside a pixel "locoder" wordmark, inside a slowly turning aura
of shaded letters (ASCII-art style), the prompt box below it
(laid out by app.tcss), a one-line note on where the model runs, and a
"continue where you left off" panel of recent conversations, files and
projects.

The images are drawn with half-block characters, so each text cell holds
two square "pixels" and the shapes keep their proportions in any terminal.
"""

from __future__ import annotations

import math
import time
from functools import lru_cache
from pathlib import Path
from typing import Optional

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


# -- the animated logo ----------------------------------------------------------

# Letters from light to dense, the way ASCII-art portraits are shaded.
AURA_RAMP = ".,:;clodxkO0KXN"
# Dark gold through gold to near-white.
AURA_COLORS = ("#3d311a", "#5e4b24", "#80662f", "#a8853f", "#d7a84a", "#e8c272", "#f5dea6", "#fff5dc")
TWINKLE_COLOR = "#fffaf0"
AURA_FPS = 10
LOCKUP_GAP = 3  # columns between the logo and the wordmark

_HALF_BLOCKS = {(True, True): "█", (True, False): "▀", (False, True): "▄", (False, False): " "}


def _half_blocks(rows: list[str]) -> list[str]:
    """A bitmap as half-block characters: two pixel rows per text row."""
    if len(rows) % 2:
        rows = rows + ["." * len(rows[0])]
    return ["".join(_HALF_BLOCKS[(t == "#", b == "#")] for t, b in zip(top, bottom))
            for top, bottom in zip(rows[0::2], rows[1::2])]


@lru_cache(maxsize=8)
def lockup_cells(accent: str) -> tuple[list[list[Optional[tuple[str, str]]]], int, int]:
    """The logo and wordmark as a grid of (character, colour) cells, None for blank."""
    logo = _half_blocks(LOGO)
    word = _half_blocks(wordmark_bitmap("locoder"))
    width = len(logo[0]) + LOCKUP_GAP + len(word[0])
    height = max(len(logo), len(word))
    grid: list[list[Optional[tuple[str, str]]]] = [[None] * width for _ in range(height)]
    for y, line in enumerate(logo):
        for x, ch in enumerate(line):
            if ch != " ":
                grid[y][x] = (ch, accent)
    top, left = (height - len(word)) // 2, len(logo[0]) + LOCKUP_GAP
    for y, line in enumerate(word):
        for x, ch in enumerate(line):
            if ch != " ":
                grid[top + y][left + x] = (ch, WORDMARK_COLOR)
    return grid, width, height


def aura_frame(width: int, height: int, t: float, accent: str) -> Optional[Text]:
    """One frame of the home logo: the lockup, with ASCII-art "aura" arms
    turning slowly around it and the odd twinkle. None if there isn't room
    for any aura (the caller then shows the plain lockup)."""
    cells, lw, lh = lockup_cells(accent)
    if width < lw + 4 or height < lh + 2:
        return None
    ox = (width - lw) // 2
    # A little below the middle, so it sits near the prompt box.
    oy = max(1, min(height - lh - 1, round((height - lh) * 0.62)))
    cx, cy = ox + lw / 2, oy + lh / 2
    rx, ry = lw / 2 + 4, lh / 2 + 1.5  # a clear oval around the lockup
    tick = int(t * 4)
    text = Text(no_wrap=True, overflow="crop", end="")
    for y in range(height):
        run, run_style = "", None
        for x in range(width):
            ly, lx = y - oy, x - ox
            cell = cells[ly][lx] if 0 <= ly < lh and 0 <= lx < lw else None
            if cell:
                ch, style = cell
            else:
                dx, dy = (x + 0.5 - cx) / rx, (y + 0.5 - cy) / ry
                # A rounded rectangle rather than an oval, so the lockup's corners stay clear.
                d = (dx ** 4 + dy ** 4) ** 0.25
                ch, style = " ", None
                if d >= 1:
                    # Fades out with distance from the lockup, and softly at the edges of the area.
                    edge = min(1.0, (min(x, width - 1 - x) + 1) / 8, (min(y, height - 1 - y) + 1) / 3)
                    fade = math.exp(-(d - 1) * 1.15) * edge
                    arms = 0.5 + 0.5 * math.sin(3 * math.atan2(dy, dx) + 2.6 * d - 1.6 * t)
                    ripple = 0.5 + 0.5 * math.sin(0.33 * x - 0.9 * t + math.sin(0.5 * y + 0.6 * t))
                    v = fade * (0.65 * arms + 0.35 * ripple)
                    if fade > 0.25 and ((x * 73856093) ^ (y * 19349663) ^ (tick * 83492791)) % 251 == 0:
                        ch, style = "*", TWINKLE_COLOR
                    elif v >= 0.12:
                        ch = AURA_RAMP[min(len(AURA_RAMP) - 1, int(v * len(AURA_RAMP)))]
                        style = AURA_COLORS[min(len(AURA_COLORS) - 1, int(v * len(AURA_COLORS)))]
            if style != run_style and run:
                text.append(run, run_style)
                run = ""
            run_style = style
            run += ch
        text.append(run, run_style)
        if y < height - 1:
            text.append("\n")
    return text


class HomeLogo(Static):
    """The logo, in the current mode's colour, beside the wordmark - with an
    animated aura of shaded letters around them filling the free space above
    the prompt. Too little room (a small terminal) shows the plain lockup.
    The animation only runs while the home screen is showing.
    """

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._accent = WORDMARK_COLOR
        self._started = time.monotonic()
        self._plain = False

    def on_mount(self) -> None:
        self._draw()
        self.set_interval(1 / AURA_FPS, self._tick)

    def on_resize(self) -> None:
        self._draw()

    def set_accent(self, accent: str) -> None:
        if accent != self._accent:
            self._accent = accent
            self._plain = False
            self._draw()

    def _tick(self) -> None:
        if self.display and self.screen.has_class("home") and not self._plain:
            self._draw()

    def _draw(self) -> None:
        frame = aura_frame(self.size.width, self.size.height, time.monotonic() - self._started, self._accent)
        if frame is not None:
            self._plain = False
            self.update(frame)
            return
        if self._plain:
            return
        self._plain = True
        lockup = Table.grid(padding=(0, LOCKUP_GAP))
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
