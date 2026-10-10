from __future__ import annotations

from pathlib import Path

from locoder.tools import registry


def _edit(ctx, path: str, old: str, new: str):
    return registry.dispatch("edit_file", {"path": path, "old_string": old, "new_string": new}, ctx)


def test_edit_keeps_lf_line_endings(ctx, tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_bytes(b"one\ntwo\nthree\n")

    result = _edit(ctx, "a.py", "two", "TWO")

    assert not result.is_error, result.output
    assert target.read_bytes() == b"one\nTWO\nthree\n"


def test_edit_keeps_crlf_line_endings_and_matches_lf_old_string(ctx, tmp_path: Path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"one\r\ntwo\r\nthree\r\n")

    # The model sends LF-only strings spanning a line break.
    result = _edit(ctx, "a.txt", "one\ntwo", "one\nTWO\nextra")

    assert not result.is_error, result.output
    assert target.read_bytes() == b"one\r\nTWO\r\nextra\r\nthree\r\n"


def test_write_overwrite_keeps_existing_line_endings(ctx, tmp_path: Path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"old\r\n")

    result = registry.dispatch("write_file", {"path": "a.txt", "content": "new\nlines\n"}, ctx)

    assert not result.is_error, result.output
    assert target.read_bytes() == b"new\r\nlines\r\n"


def test_write_new_file_uses_lf_on_every_platform(ctx, tmp_path: Path):
    result = registry.dispatch("write_file", {"path": "sub/new.py", "content": "a\nb\n"}, ctx)

    assert not result.is_error, result.output
    assert (tmp_path / "sub" / "new.py").read_bytes() == b"a\nb\n"


def test_edit_rejects_ambiguous_match(ctx, tmp_path: Path):
    (tmp_path / "a.txt").write_text("x\nx\n", encoding="utf-8")

    result = _edit(ctx, "a.txt", "x", "y")

    assert result.is_error
    assert "not unique" in result.output


def test_declined_edit_leaves_file_alone(ctx, tmp_path: Path):
    target = tmp_path / "a.txt"
    target.write_bytes(b"keep\n")
    ctx.confirm = lambda title, preview: False

    result = _edit(ctx, "a.txt", "keep", "lose")

    assert result.is_error
    assert target.read_bytes() == b"keep\n"


def test_large_tool_output_is_capped(ctx, tmp_path: Path):
    (tmp_path / "big.txt").write_text("x" * 100 + "\n" * 1 + ("line\n" * 20_000), encoding="utf-8")

    result = registry.dispatch("read_file", {"path": "big.txt"}, ctx)

    assert len(result.output) < registry.MAX_TOOL_OUTPUT_CHARS + 500
    assert "output truncated" in result.output
    assert "start_line/end_line" in result.output


def test_missing_file_error_suggests_matching_paths(ctx, tmp_path: Path):
    (tmp_path / "pkg" / "sub").mkdir(parents=True)
    (tmp_path / "pkg" / "sub" / "Sandbox.py").write_text("x\n", encoding="utf-8")
    (tmp_path / ".venv").mkdir()
    (tmp_path / ".venv" / "sandbox.py").write_text("x\n", encoding="utf-8")

    result = registry.dispatch("read_file", {"path": "sandbox.py"}, ctx)

    assert result.is_error
    assert "Did you mean: pkg/sub/Sandbox.py?" in result.output
    assert ".venv" not in result.output


def test_paths_are_shown_with_forward_slashes(ctx, tmp_path: Path):
    (tmp_path / "a" / "b").mkdir(parents=True)
    (tmp_path / "a" / "b" / "c.txt").write_text("needle\n", encoding="utf-8")

    result = registry.dispatch("search_files", {"pattern": "needle"}, ctx)

    assert "a/b/c.txt:1:needle" in result.output


def test_sandbox_blocks_paths_outside_root(ctx, tmp_path: Path):
    result = registry.dispatch("read_file", {"path": "../outside.txt"}, ctx)

    assert result.is_error
    assert "outside the sandbox" in result.output
