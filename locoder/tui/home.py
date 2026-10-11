"""The home screen shown while a conversation is empty: a horizontal lockup
of the logo beside a pixel "locoder" wordmark, the prompt box below it
(laid out by app.tcss), and a one-line note on where the model runs.

Both images are drawn with half-block characters, so each text cell holds
two square "pixels" and the shapes keep their proportions in any terminal.
"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import urlparse

from rich.align import Align
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from locoder import __version__

WORDMARK_COLOR = "#e8e8e8"
DIM = "#6a6a6a"

# 14 x 14, a compact cut of the logo: a bar across the top with two square
# holes on the left, and an arm down the right side as thick as the bar.
LOGO = [
    "##############",
    "#.############",
    "##############",
    "#.############",
    "##############",
] + [".........#####"] * 9

# 7 rows: two for ascenders (l, d), five for the x-height.
GLYPHS = {
    "l": ["#.", "#.", "#.", "#.", "#.", "#.", "##"],
    "o": ["....", "....", "####", "#..#", "#..#", "#..#", "####"],
    "c": ["....", "....", "####", "#...", "#...", "#...", "####"],
    "d": ["...#", "...#", "####", "#..#", "#..#", "#..#", "####"],
    "e": ["....", "....", "####", "#..#", "####", "#...", "####"],
    "r": ["....", "....", "####", "#...", "#...", "#...", "#..."],
}

LOCAL_HOSTS = {"localhost", "127.0.0.1", "::1", "0.0.0.0"}


def pixels_to_text(rows: list[str], style: str) -> Text:
    """Render a '#'/'.' bitmap two rows per line with half blocks."""
    rows = rows + ["." * len(rows[0])] if len(rows) % 2 else rows
    text = Text(no_wrap=True, style=style)
    for y in range(0, len(rows), 2):
        top, bottom = rows[y], rows[y + 1]
        for t, b in zip(top, bottom):
            text.append({(True, True): "█", (True, False): "▀", (False, True): "▄"}.get(
                (t == "#", b == "#"), " "))
        if y + 2 < len(rows):
            text.append("\n")
    return text


def wordmark_bitmap(word: str) -> list[str]:
    rows = ["" for _ in range(7)]
    for i, ch in enumerate(word):
        for r in range(7):
            rows[r] += GLYPHS[ch][r] + ("." if i < len(word) - 1 else "")
    return rows


def where_it_runs(base_url: str) -> Text:
    """One line under the prompt box: local, or which host the code goes to."""
    host = urlparse(base_url).hostname or base_url
    if host in LOCAL_HOSTS:
        return Text.assemble(("● ", "#5fb85f"), ("runs locally — your code stays on this machine", DIM))
    return Text.assemble(("● ", "#d7a84a"), (f"connected to {host} — prompts and files are sent there", DIM))


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
