"""What makes a file change or a shell command risky.

Small models have a weak sense of danger, so locoder judges it for them:
anything these checks flag is always shown to you for approval - in yolo
mode too, which only skips the questions for everyday, low-risk actions.

Each check returns plain-language reasons ("it deletes files recursively"),
shown on the approval prompt; an empty list means nothing stood out. These
are heuristics: they catch the common ways things go wrong, not every
possible one, so yolo mode is still for projects you can afford to lose
changes in.
"""

from __future__ import annotations

import difflib
import os
import re
import shlex
from pathlib import Path, PurePath
from typing import Iterable, Optional

LARGE_FILE_LINES = 1000
# Removing at least this share of a file's lines (in a file at least
# MOSTLY_REMOVED_MIN_LINES long) counts as wiping most of it.
MOSTLY_REMOVED_SHARE = 0.5
MOSTLY_REMOVED_MIN_LINES = 50
MANY_FILES = 3  # deleting more than this many named files at once

SENSITIVE_NAMES = {"id_rsa", "id_ed25519", "id_ecdsa", "id_dsa", ".npmrc", ".pypirc", ".netrc", ".git-credentials"}
SENSITIVE_SUFFIXES = {".pem", ".key", ".pfx", ".p12", ".keystore", ".jks"}
PROTECTED_DIRS = {".git": "git's own data", ".locoder": "locoder's own settings", ".ssh": "your SSH keys"}
SCRIPT_SUFFIXES = {".py", ".js", ".mjs", ".cjs", ".ts", ".sh", ".bash", ".ps1", ".bat", ".cmd", ".rb", ".pl"}


def _lines(text: Optional[str]) -> int:
    return len(text.splitlines()) if text else 0


def _removed_lines(old: str, new: str) -> int:
    matcher = difflib.SequenceMatcher(a=old.splitlines(), b=new.splitlines(), autojunk=False)
    return sum(i2 - i1 for tag, i1, i2, _, _ in matcher.get_opcodes() if tag in ("delete", "replace"))


def _inside(path: PurePath, root: PurePath) -> bool:
    try:
        Path(path).resolve().relative_to(Path(root).resolve())
        return True
    except ValueError:
        return False


# -- sending files to the phone ----------------------------------------------------

MAX_SEND_BYTES = 8_000_000


def sendable_problem(root: Path, path: Path) -> Optional[str]:
    """Why ``path`` can't be sent to the phone (None: it can). Secrets and
    locoder's own data never leave the PC this way."""
    name = path.name
    if not _inside(path, root):
        return "that file is outside the project folder"
    if not path.is_file():
        return f"'{name}' is not a file"
    if name == ".env" or name.startswith(".env.") or name in SENSITIVE_NAMES or path.suffix.lower() in SENSITIVE_SUFFIXES:
        return f"'{name}' may hold passwords or keys, so it isn't sent"
    try:
        parts = Path(path).resolve().relative_to(Path(root).resolve()).parts
    except ValueError:
        parts = ()
    if any(part in PROTECTED_DIRS for part in parts[:-1]):
        return "files inside .git, .locoder and .ssh aren't sent"
    try:
        size = path.stat().st_size
    except OSError as exc:
        return f"can't read '{name}': {exc}"
    if size > MAX_SEND_BYTES:
        return f"'{name}' is {size / 1e6:.1f} MB; the limit is {MAX_SEND_BYTES // 1_000_000} MB"
    return None


# -- file changes ------------------------------------------------------------------


def file_change_risks(root: Path, path: Path, old: Optional[str], new: str) -> list[str]:
    """Why writing ``new`` over ``old`` (None: a new file) at ``path`` is risky."""
    reasons = []
    if not _inside(path, root):
        reasons.append("it's outside the project folder")
    name = path.name
    if name == ".env" or name.startswith(".env.") or name in SENSITIVE_NAMES or path.suffix.lower() in SENSITIVE_SUFFIXES:
        reasons.append(f"{name} may hold passwords or keys")
    try:
        parts = Path(path).resolve().relative_to(Path(root).resolve()).parts
    except ValueError:
        parts = Path(path).parts
    for part in parts[:-1]:
        if part in PROTECTED_DIRS:
            reasons.append(f"it changes {part}/ ({PROTECTED_DIRS[part]})")
            break

    old_lines, new_lines = _lines(old), _lines(new)
    if old is not None and old_lines > LARGE_FILE_LINES:
        reasons.append(f"{name} is a big file ({old_lines:,} lines)")
    elif old is None and new_lines > LARGE_FILE_LINES:
        reasons.append(f"it writes a {new_lines:,}-line file")
    if old is not None and old_lines >= MOSTLY_REMOVED_MIN_LINES:
        removed = _removed_lines(old, new)
        if removed >= old_lines * MOSTLY_REMOVED_SHARE:
            reasons.append(f"it removes or rewrites {removed:,} of {name}'s {old_lines:,} lines")
    return reasons


# -- shell commands ----------------------------------------------------------------

_I = re.IGNORECASE
_DELETE_CMD = r"(?:\brm\b|\bdel\b|\berase\b|\brd\b|\brmdir\b|\bremove-item\b|\bunlink\b)"

# (pattern, reason): checked against the whole command.
COMMAND_RULES: list[tuple[re.Pattern, str]] = [
    # deleting a lot / for good
    (re.compile(r"\brm\s+(?:-\w*\s+)*-\w*[rR]\w*\b|\brm\s+--recursive\b", _I), "it deletes folders and everything in them"),
    (re.compile(r"\b(?:rd|rmdir)\s+(?:/\w\s+)*/s\b|\bdel\s+(?:/\w\s+)*/s\b|\bremove-item\b[^|;&]*-r(?:ecurse)?\b", _I),
     "it deletes folders and everything in them"),
    (re.compile(_DELETE_CMD + r"[^|;&\n]*[*?]", _I), "it deletes files by wildcard"),
    (re.compile(r"\bfind\b[^|;&]*\s-delete\b|\bxargs\s+(?:-\S+\s+)*rm\b", _I), "it deletes every file a search finds"),
    (re.compile(r"\bgit\s+clean\s+-\w*f", _I), "it deletes all untracked files"),
    (re.compile(r"\bgit\s+(?:reset\s+--hard|checkout\s+(?:-f\s+)?--?\s*\.|restore\s+(?:--\S+\s+)*\.|stash\s+(?:drop|clear))", _I),
     "it throws away uncommitted work"),
    (re.compile(r"\bgit\s+(?:branch\s+-D|push\b[^|;&]*(?:--force|\s-f\b)|filter-branch|rebase\b)", _I),
     "it rewrites or deletes git history"),
    (re.compile(r"\bgit\s+push\b", _I), "it sends commits to a remote server"),
    (re.compile(r"\b(?:drop\s+(?:table|database|schema)|truncate\s+table|delete\s+from\s+\w+\s*(?:;|$))", _I),
     "it deletes database data"),
    (re.compile(r"\b(?:mkfs\w*|diskpart|fdisk|parted)\b|\bdd\s+if=|\bformat(?:\.com)?\s+[a-z]:", _I), "it can wipe a disk"),
    (re.compile(r":\(\)\s*\{\s*:\|:&\s*\};"), "it's a fork bomb"),
    (re.compile(r"\b(?:shutdown|reboot|restart-computer|stop-computer|logoff|poweroff|halt)\b", _I),
     "it shuts down or restarts your PC"),
    # the rest of your PC
    (re.compile(r"\b(?:sudo|runas|doas)\b|-verb\s+runas\b", _I), "it asks for admin rights"),
    (re.compile(r"\b(?:pip3?|uv\s+pip|python\d*(?:\.exe)?\s+-m\s+pip)\s+(?:install|uninstall)\b", _I),
     "it installs or removes Python packages, which changes your Python outside this folder"),
    (re.compile(r"\b(?:npm|pnpm|yarn)\s+(?:i|install|add|remove|uninstall|rm)\b[^|;&]*(?:\s-g\b|--global\b)|\byarn\s+global\b", _I),
     "it installs or removes programs for your whole PC"),
    (re.compile(r"\b(?:winget|choco|scoop|apt|apt-get|brew|yum|dnf|pacman|snap|msiexec)\s+(?:install|uninstall|remove|upgrade|-i|/i|/x)\b", _I),
     "it installs or removes programs on your PC"),
    (re.compile(r"\b(?:reg(?:\.exe)?\s+(?:add|delete|import)|regedit|set-itemproperty\s+[^|;&]*hk|new-itemproperty)\b", _I),
     "it changes the Windows registry"),
    (re.compile(r"\bsetx\b|\[environment\]::setenvironmentvariable", _I), "it changes environment variables for your whole account"),
    (re.compile(r"\b(?:sc(?:\.exe)?\s+(?:config|delete|create|stop)|net\s+(?:stop|start|user|localgroup)|netsh|schtasks\s+/(?:create|delete|change)"
                r"|set-executionpolicy|bcdedit|set-mppreference|add-mppreference|systemctl|launchctl|crontab)\b", _I),
     "it changes system settings, services or scheduled tasks"),
    (re.compile(r"\b(?:taskkill|stop-process|pkill|killall)\b|\bkill\s+-9\b", _I), "it stops other programs"),
    (re.compile(r"\b(?:chmod|chown)\s+-R\b|\b(?:icacls|takeown|cacls)\b|\battrib\b[^|;&]*[+-][rhs]\b", _I),
     "it changes file permissions"),
    (re.compile(r"\b(?:curl|wget|iwr|irm|invoke-webrequest|invoke-restmethod)\b[^|;&]*\|\s*(?:sudo\s+)?(?:sh|bash|zsh|iex|invoke-expression|python\d*|node|pwsh|powershell)\b", _I),
     "it downloads code from the internet and runs it"),
    (re.compile(r"\b(?:powershell|pwsh)(?:\.exe)?\b[^|;&]*\s-e(?:nc|ncodedcommand)?\s+[A-Za-z0-9+/=]{16,}", _I), "it runs a hidden (encoded) command"),
    (re.compile(r"\b(?:ssh|scp|rsync|sftp|ftp)\b", _I), "it connects to another computer"),
]

# Code that deletes files or runs commands, for `python -c ...` and for
# scripts locoder wrote and is about to run.
CODE_RULES: list[tuple[re.Pattern, str]] = [
    (re.compile(r"\b(?:shutil\.rmtree|os\.(?:remove|unlink|rmdir|removedirs)|\.unlink\(|\.rmdir\(|send2trash)\b"
                r"|\bfs\.(?:rm|rmdir|unlink)(?:Sync)?\b|\bfs\.promises\.(?:rm|unlink)\b", _I), "deletes files"),
    (re.compile(r"\b(?:subprocess\.|os\.system|os\.popen|child_process|execSync|spawnSync)\b"), "runs other commands"),
]

_WIN_ABS = re.compile(r"(?<![\w/])([A-Za-z]:[\\/][^\s\"'|;&<>]*)")
_UNC = re.compile(r"(?<![\w])\\\\[\w.$-]+\\")
_UNIX_ROOTS = re.compile(r"(?<![\w:/.~$-])/(?:etc|usr|bin|sbin|var|home|root|opt|boot|lib|sys|proc|Users|Library|System|Applications|mnt|srv)\b")
_HOME = re.compile(r"(?<![\w])~(?:[\\/]|$|\s)|\$HOME\b|\$env:\w+|%(?:userprofile|appdata|localappdata|homepath|homedrive|windir|systemroot|programfiles|programdata|temp|tmp)%", _I)
_PARENT = re.compile(r"(?:^|[\s\"'=(:,])\.\.(?:[\\/]|$|[\s\"')])")


def _command_words(command: str) -> list[str]:
    try:
        return shlex.split(command, posix=False)
    except ValueError:
        return command.split()


_DELETE_WORDS = {"rm", "del", "erase", "rd", "rmdir", "remove-item", "ri", "unlink"}
_WRAPPERS = [
    re.compile(r"\b(?:powershell|pwsh)(?:\.exe)?\b.*?\s-c(?:ommand)?\s+(.+)$", _I | re.S),
    re.compile(r"\bcmd(?:\.exe)?\s+/[ck]\s+(.+)$", _I | re.S),
]
_DOTNET_DELETE = re.compile(r"\b(?:File|Directory)\]::Delete\(\s*['\"]([^'\"]+)['\"]", _I)


def _unwrap(command: str) -> list[str]:
    """The command, plus whatever it hands to ``powershell -Command`` / ``cmd /c``."""
    found = [command]
    for pattern in _WRAPPERS:
        match = pattern.search(command)
        if match:
            inner = match.group(1).strip()
            if len(inner) >= 2 and inner[0] in "\"'" and inner[-1] == inner[0]:
                inner = inner[1:-1]
            found.append(inner.replace("\\'", "'").replace('\\"', '"'))
    return found


def _file_lines(path: Path) -> int:
    try:
        with path.open("rb") as handle:
            return sum(chunk.count(b"\n") for chunk in iter(lambda: handle.read(1 << 20), b""))
    except OSError:
        return 0


def _delete_reasons(command: str, cwd: Path) -> list[str]:
    """What a delete command takes with it: several files, a big file, a
    folder full of files, or whatever gets piped into it. Looks inside
    ``powershell -Command "..."`` too."""
    reasons: list[str] = []
    for text in _unwrap(command):
        for statement in re.split(r"&&|\|\||[;&\n]", text):
            for position, part in enumerate(statement.split("|")):
                words = _command_words(part.strip())
                if not words or words[0].strip("\"'").lower() not in _DELETE_WORDS:
                    continue
                targets = [w.strip("\"'") for w in words[1:] if not w.startswith(("-", "/"))]
                if not targets and position > 0:
                    reasons.append("it deletes everything piped into it")
                if len(targets) > MANY_FILES:
                    reasons.append(f"it deletes {len(targets)} files at once")
                for target in targets:
                    reasons += _target_reason(target, cwd)
        for target in _DOTNET_DELETE.findall(text):
            reasons += _target_reason(target, cwd)
    return reasons


def _target_reason(target: str, cwd: Path) -> list[str]:
    try:
        path = (cwd / target).resolve() if not Path(target).is_absolute() else Path(target).resolve()
        if path.is_file():
            lines = _file_lines(path)
            if lines > LARGE_FILE_LINES:
                return [f"it deletes {path.name}, a {lines:,}-line file"]
        elif path.is_dir():
            count = 0
            for _, _, names in os.walk(path):
                count += len(names)
                if count > 500:
                    break
            if count > MANY_FILES:
                return [f"it deletes the folder {path.name}, which holds {count}{'+' if count > 500 else ''} files"]
    except (OSError, ValueError):
        pass
    return []


_WRITES = re.compile(r"set-content|add-content|out-file|\btee\b|>\s*[^\s&|]|writealltext|\.write\(|writelines", _I)
_BIG_NUMBERS = [
    re.compile(r"\b\d+\.\.(\d{4,})\b"),  # PowerShell 1..1100
    re.compile(r"\bseq\s+(?:\d+\s+)*(\d{4,})\b", _I),
    re.compile(r"\brange\(\s*(?:\d+\s*,\s*)?(\d{4,})\s*\)", _I),
    re.compile(r"\bfor\s+/l\b[^)]*,\s*(\d{4,})\s*\)", _I),
    re.compile(r"[\"']\s*\*\s*(\d{4,})\b"),  # "a\n" * 1100
]


def _big_generation(command: str) -> Optional[str]:
    """A command that writes a very large file (a loop or range over thousands of lines)."""
    if not _WRITES.search(command):
        return None
    for pattern in _BIG_NUMBERS:
        for match in pattern.finditer(command):
            if int(match.group(1)) > LARGE_FILE_LINES:
                return f"it generates a very large file (about {int(match.group(1)):,} lines)"
    return None


def broad_root_warning(root: Path) -> Optional[str]:
    """A warning when locoder runs somewhere so broad that "inside the project" means almost everything."""
    root = Path(root).resolve()
    if root == Path.home().resolve() or root.parent == root:
        what = "your whole home folder" if root != root.parent else "a whole drive"
        return (f"locoder is running in {root}, so its project folder is {what} - the safety checks treat "
                "everything in it as part of the project. Run it from a project's own folder instead.")
    return None


def _outside_reasons(command: str, root: Path, cwd: Path) -> list[str]:
    reasons = []
    for match in _WIN_ABS.finditer(command):
        if not _inside(PurePath(match.group(1).rstrip("\\/.")), root):
            reasons.append(f"it touches {match.group(1)}, outside the project folder")
            break
    if _UNC.search(command) or _UNIX_ROOTS.search(command):
        reasons.append("it touches system or other folders outside the project")
    if _HOME.search(command):
        reasons.append("it touches your home folder or system folders")
    if _PARENT.search(command) and not _inside(cwd.parent, root):
        reasons.append("it goes up out of the project folder (..)")
    return reasons


def code_risks(text: str) -> list[str]:
    return [reason for pattern, reason in CODE_RULES if pattern.search(text)]


def command_risks(command: str, root: Path, cwd: Optional[Path] = None,
                  written: Iterable[Path] = ()) -> list[str]:
    """Why running ``command`` (in ``cwd``, inside project ``root``) is risky.

    ``written`` are files locoder wrote this session: running one of those
    that deletes files or runs commands is flagged too, since otherwise a
    script would be an easy way around these checks.
    """
    cwd = cwd or root
    reasons: list[str] = []
    for pattern, reason in COMMAND_RULES:
        if pattern.search(command) and reason not in reasons:
            reasons.append(reason)
    if re.search(r"(?:^|[\s\\/\"'])\.?venv[\\/]", command):
        # pip from the project's own virtualenv installs inside this folder.
        reasons = [r for r in reasons if "Python packages" not in r]
    reasons += _delete_reasons(command, cwd)
    big = _big_generation(command)
    if big:
        reasons.append(big)
    reasons += _outside_reasons(command, root, cwd)
    if re.search(r"\b(?:python\d*|py|node|deno|bun)(?:\.exe)?\s+(?:-\w+\s+)*-[ce]\s", command, _I):
        reasons += [f"its inline code {r}" for r in code_risks(command)]

    written_paths = {Path(p).resolve() for p in written}
    for word in _command_words(command):
        word = word.strip("\"'")
        if not word or PurePath(word).suffix.lower() not in SCRIPT_SUFFIXES:
            continue
        candidate = (cwd / word).resolve()
        if candidate in written_paths and candidate.is_file():
            try:
                text = candidate.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            inner = code_risks(text)
            if candidate.suffix.lower() in {".sh", ".bash", ".ps1", ".bat", ".cmd"}:
                inner += [r.removeprefix("it ") for line in text.splitlines() for r in command_risks(line, root, cwd)]
            if inner:
                reasons.append(f"it runs {word}, which locoder wrote and which {inner[0]}")
    return list(dict.fromkeys(reasons))
