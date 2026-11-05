"""Slash-command table, file-mention indexing, and @-mention expansion.

Kept free of any Textual imports so it's trivial to unit test.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

from locoder.sandbox import Sandbox, SandboxViolation
from locoder.tools.search_tools import IGNORED_DIRS

MAX_FILE_INDEX = 5000
MAX_FILE_MATCHES = 30
MAX_MENTION_BYTES = 50_000


@dataclass(frozen=True)
class SlashCommand:
    name: str
    aliases: tuple[str, ...]
    description: str
    opens_modal: bool = False


SLASH_COMMANDS: list[SlashCommand] = [
    SlashCommand("help", (), "show available commands", opens_modal=True),
    SlashCommand("clear", ("new",), "start a new conversation"),
    SlashCommand("model", (), "switch the active model", opens_modal=True),
    SlashCommand("settings", (), "edit provider, model, temperature, etc", opens_modal=True),
    SlashCommand("conversations", ("sessions",), "browse and resume past conversations", opens_modal=True),
    SlashCommand("yolo", (), "skip approvals except for risky actions"),
    SlashCommand("think", (), "turn the model's thinking on or off (off is much faster)"),
    SlashCommand("web", (), "let the model search the web and read pages, or stop it"),
    SlashCommand("layout", (), "conversation on the left, in the middle or on the right (/layout center)"),
    SlashCommand("sidebar", (), "show or hide the sidebar (ctrl+b)"),
    SlashCommand("tokens", (), "show context/token usage"),
    SlashCommand("exit", ("quit",), "leave locoder"),
]

_BY_NAME: dict[str, SlashCommand] = {}
for _cmd in SLASH_COMMANDS:
    _BY_NAME[_cmd.name] = _cmd
    for _alias in _cmd.aliases:
        _BY_NAME[_alias] = _cmd


def find_command(name: str) -> SlashCommand | None:
    return _BY_NAME.get(name.lower())


def filter_commands(fragment: str) -> list[SlashCommand]:
    if not fragment:
        return SLASH_COMMANDS
    frag = fragment.lower()
    matches = [c for c in SLASH_COMMANDS if c.name.startswith(frag) or any(a.startswith(frag) for a in c.aliases)]
    return matches


def build_file_index(root: Path, limit: int = MAX_FILE_INDEX) -> list[str]:
    paths: list[str] = []
    for entry in root.rglob("*"):
        try:
            rel_parts = entry.relative_to(root).parts
        except ValueError:
            continue
        if any(part in IGNORED_DIRS for part in rel_parts):
            continue
        if entry.is_file():
            paths.append("/".join(rel_parts))
            if len(paths) >= limit:
                break
    paths.sort()
    return paths


def filter_files(paths: list[str], fragment: str, limit: int = MAX_FILE_MATCHES) -> list[str]:
    if not fragment:
        return paths[:limit]
    frag = fragment.lower()
    starts_with = [p for p in paths if p.lower().startswith(frag) or p.lower().rsplit("/", 1)[-1].startswith(frag)]
    contains = [p for p in paths if frag in p.lower() and p not in starts_with]
    return (starts_with + contains)[:limit]


MENTION_RE = re.compile(r"@(\S+)")


def expand_file_mentions(text: str, sandbox: Sandbox) -> str:
    """Replace @path mentions with the literal text plus attached file
    contents, so the model actually sees what the user pointed at.
    """
    seen: set[str] = set()
    blocks: list[str] = []
    for match in MENTION_RE.finditer(text):
        rel = match.group(1)
        if rel in seen:
            continue
        seen.add(rel)
        try:
            path = sandbox.resolve(rel, must_exist=True)
        except (SandboxViolation, FileNotFoundError):
            continue
        if not path.is_file():
            continue
        try:
            content = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if len(content) > MAX_MENTION_BYTES:
            content = content[:MAX_MENTION_BYTES] + "\n... [truncated]"
        blocks.append(f"### Attached file: {rel}\n```\n{content}\n```")

    if not blocks:
        return text
    return text + "\n\n" + "\n\n".join(blocks)


WHITESPACE = re.compile(r"\s")


def current_token(value: str, cursor: int) -> tuple[int, str] | None:
    """Return (start_index, token) for the whitespace-delimited token ending
    exactly at `cursor`, or None if the cursor isn't inside/after a token.
    """
    if cursor <= 0 or cursor > len(value):
        return None
    start = cursor
    while start > 0 and not WHITESPACE.match(value[start - 1]):
        start -= 1
    token = value[start:cursor]
    return (start, token) if token else None
