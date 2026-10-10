"""Confines file-system tool operations to the project root (plus any
explicitly allow-listed directories) so a runaway or malicious tool call
can't read or write arbitrary paths on the machine.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Optional

# Directories skipped when walking the project (search, @-mentions, suggestions).
IGNORED_DIRS = {".git", ".locoder", "__pycache__", "node_modules", ".venv", "venv", ".mypy_cache"}

# Bound on how many files a "did you mean" lookup scans, so a typo in a huge
# tree can't stall a tool call.
MAX_SUGGESTION_SCAN = 20_000


class SandboxViolation(Exception):
    """Raised when a path resolves outside every allowed root."""


class Sandbox:
    def __init__(self, root: Path, allowed_paths: Optional[list[str]] = None):
        self.root = root.resolve()
        self.allowed_roots = [self.root] + [
            Path(p).expanduser().resolve() for p in (allowed_paths or [])
        ]

    def resolve(self, path: str, *, must_exist: bool = False) -> Path:
        raw = Path(path).expanduser()
        candidate = raw if raw.is_absolute() else (self.root / raw)
        candidate = candidate.resolve()

        if not self._is_allowed(candidate):
            raise SandboxViolation(
                f"'{path}' resolves to {candidate}, which is outside the sandbox root "
                f"({self.root}). Refusing. Add it to allowed_paths in ~/.locoder/config.yaml "
                "if this is intentional."
            )
        if must_exist and not candidate.exists():
            message = f"no such file or directory: '{path}' (paths are relative to the project root)"
            suggestions = self.suggest(candidate.name)
            if suggestions:
                message += f". Did you mean: {', '.join(suggestions)}?"
            raise FileNotFoundError(message)
        return candidate

    def suggest(self, name: str, limit: int = 5) -> list[str]:
        """Project paths whose final component is ``name`` (case-insensitive)."""
        if not name:
            return []
        target = name.lower()
        found: list[str] = []
        scanned = 0
        for dirpath, dirnames, filenames in os.walk(self.root):
            dirnames[:] = sorted(d for d in dirnames if d not in IGNORED_DIRS)
            for entry in dirnames + filenames:
                scanned += 1
                if entry.lower() == target:
                    found.append(self.display_path(Path(dirpath) / entry))
                    if len(found) >= limit:
                        return found
            if scanned >= MAX_SUGGESTION_SCAN:
                break
        return found

    def _is_allowed(self, candidate: Path) -> bool:
        for allowed_root in self.allowed_roots:
            if candidate == allowed_root or allowed_root in candidate.parents:
                return True
        return False

    def display_path(self, path: Path) -> str:
        """Root-relative path with forward slashes on every OS. Backslashes
        would have to be escaped in tool-call JSON, which small models fumble.
        """
        try:
            return path.relative_to(self.root).as_posix()
        except ValueError:
            return path.as_posix()
