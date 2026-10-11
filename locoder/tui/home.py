"""The home screen shown while a conversation is empty: the logo, a pixel
"locoder" wordmark and, below them (laid out by app.tcss), the input box.

Both images are drawn with half-block characters, so each text cell holds
two square "pixels" and the shapes keep their proportions in any terminal.
"""

from __future__ import annotations

from pathlib import Path

from rich.align import Align
from rich.console import Group
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from locoder import __version__

LOGO_COLOR = "#e8e8e8"
WORDMARK_DIM = "#6a6a6a"
WORDMARK_BRIGHT = "#e8e8e8"

# 20 x 19: a bar across the top with two square holes on the left, and an
# arm down the right side.
LOGO = [
    "####################",
    "#.##################",
    "####################",
    "#.##################",
    "####################",
] + ["...............#####"] * 14

# 7 rows: two for ascenders (l, d), five for the x-height.
GLYPHS = {
    "l": ["#.", "#.", "#.", "#.", "#.", "#.", "##"],
    "o": ["....", "....", "####", "#..#", "#..#", "#..#", "####"],
    "c": ["....", "....", "####", "#...", "#...", "#...", "####"],
    "d": ["...#", "...#", "####", "#..#", "#..#", "#..#", "####"],
    "e": ["....", "....", "####", "#..#", "####", "#...", "####"],
    "r": ["....", "....", "####", "#...", "#...", "#...", "#..."],
}


def pixels_to_text(rows: list[str], style_for_column) -> Text:
    """Render a '#'/'.' bitmap two rows per line with half blocks."""
    rows = rows + ["." * len(rows[0])] if len(rows) % 2 else rows
    text = Text(no_wrap=True)
    for y in range(0, len(rows), 2):
        top, bottom = rows[y], rows[y + 1]
        for x, (t, b) in enumerate(zip(top, bottom)):
            char = {(True, True): "█", (True, False): "▀", (False, True): "▄"}.get(
                (t == "#", b == "#"), " ")
            text.append(char, style=style_for_column(x))
        if y + 2 < len(rows):
            text.append("\n")
    return text


def wordmark_bitmap(word: str) -> tuple[list[str], int]:
    """The word's bitmap, plus the column where the bright part ("coder") starts."""
    rows = ["" for _ in range(7)]
    split = 0
    for i, ch in enumerate(word):
        if i == 2:
            split = len(rows[0])
        for r in range(7):
            rows[r] += GLYPHS[ch][r] + ("." if i < len(word) - 1 else "")
    return rows, split


class HomeLogo(Static):
    """The logo above the wordmark; just the wordmark when space is short."""

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._compact = False

    def on_mount(self) -> None:
        self._draw()

    def set_compact(self, compact: bool) -> None:
        if compact != self._compact:
            self._compact = compact
            self._draw()

    def _draw(self) -> None:
        rows, split = wordmark_bitmap("locoder")
        # Centred as whole blocks: centring line by line would trim each
        # line's trailing blanks and knock the rows out of alignment.
        word = Align.center(pixels_to_text(rows, lambda x: WORDMARK_DIM if x < split else WORDMARK_BRIGHT))
        if self._compact:
            self.update(word)
            return
        logo = Align.center(pixels_to_text(LOGO, lambda x: LOGO_COLOR))
        self.update(Group(logo, Text(""), word))


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
        grid.add_row(Text(path, style="#6a6a6a"), Text(__version__, style="#6a6a6a"))
        self.update(grid)
