"""The home screen shown while a conversation is empty: a horizontal lockup
of the logo beside a pixel "locoder" wordmark, the prompt box below it
(laid out by app.tcss), and a one-line note on where the model runs.

Both images are drawn with half-block characters, so each text cell holds
two square "pixels" and the shapes keep their proportions in any terminal.
"""

from __future__ import annotations

from pathlib import Path

from rich.align import Align
from rich.table import Table
from rich.text import Text
from textual.widgets import Static

from locoder import __version__
from locoder.display import FG as WORDMARK_COLOR
from locoder.display import LOGO, pixels_to_text, where_it_runs, wordmark_bitmap
from locoder.display import MUTED as DIM

__all__ = ["HomeFooter", "HomeLogo", "where_it_runs"]

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
