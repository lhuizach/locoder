from __future__ import annotations

import shutil
import sys
import time
from pathlib import Path

import pytest

from locoder.tools import registry, search_tools


@pytest.mark.skipif(shutil.which("rg") is None, reason="ripgrep not installed")
def test_ripgrep_pattern_starting_with_dash(ctx, tmp_path: Path):
    (tmp_path / "a.txt").write_text("value --verbose here\n", encoding="utf-8")

    result = registry.dispatch("search_files", {"pattern": "--verbose"}, ctx)

    assert not result.is_error, result.output
    assert "a.txt:1:" in result.output


def test_python_search_truncates_long_lines(ctx, tmp_path: Path, monkeypatch):
    monkeypatch.setattr(search_tools.shutil, "which", lambda name: None)
    (tmp_path / "min.js").write_text("needle" + "x" * 10_000 + "\n", encoding="utf-8")

    result = registry.dispatch("search_files", {"pattern": "needle"}, ctx)

    assert "line truncated" in result.output
    assert len(result.output) < 1_000


def _python_command(script: Path, code: str) -> str:
    script.write_text(code, encoding="utf-8")
    return f'"{sys.executable}" "{script}"'


def test_shell_output_with_non_ascii_bytes_does_not_crash(ctx, tmp_path: Path):
    # Bytes that aren't valid UTF-8 (and include ones cp1252 can't map).
    command = _python_command(
        tmp_path / "emit.py", "import sys\nsys.stdout.buffer.write(bytes([0x81, 0x8d, 0xff]) + b'ok')\n"
    )

    result = registry.dispatch("run_command", {"command": command}, ctx)

    assert "exit code: 0" in result.output, result.output
    assert "ok" in result.output


def test_shell_timeout_kills_child_processes(ctx, tmp_path: Path):
    marker = tmp_path / "marker.txt"
    # The shell starts python, which starts a grandchild that would write
    # the marker after 3s if it survived the timeout.
    child = f"import time; time.sleep(3); open(r'{marker}', 'w').write('alive')"
    command = _python_command(
        tmp_path / "parent.py",
        f"import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', {child!r}]).wait()\n",
    )

    started = time.monotonic()
    result = registry.dispatch("run_command", {"command": command, "timeout": 1}, ctx)

    assert result.is_error
    assert "timed out" in result.output
    assert time.monotonic() - started < 3
    time.sleep(3)
    assert not marker.exists(), "grandchild survived the timeout"
