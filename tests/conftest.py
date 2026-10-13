from __future__ import annotations

from pathlib import Path

import pytest

from locoder.sandbox import Sandbox
from locoder.tools.base import ToolContext


@pytest.fixture
def ctx(tmp_path: Path) -> ToolContext:
    """A tool context rooted at a temp dir that auto-approves every confirmation."""
    return ToolContext(sandbox=Sandbox(tmp_path), yolo=True, confirm=lambda title, preview, reasons=(): True)
