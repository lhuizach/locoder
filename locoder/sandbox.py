"""Confines file-system tool operations to the project root (plus any
explicitly allow-listed directories) so a runaway or malicious tool call
can't read or write arbitrary paths on the machine.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


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
                f"({self.root}). Refusing. Add it to allowed_paths in .locoder/config.yaml "
                "if this is intentional."
            )
        if must_exist and not candidate.exists():
            raise FileNotFoundError(f"no such file or directory: {candidate}")
        return candidate

    def _is_allowed(self, candidate: Path) -> bool:
        for allowed_root in self.allowed_roots:
            if candidate == allowed_root or allowed_root in candidate.parents:
                return True
        return False

    def display_path(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)
