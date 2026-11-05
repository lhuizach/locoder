"""Loads and merges locoder configuration.

Precedence (highest wins): CLI flags > environment variables >
project ``.locoder/config.yaml`` > global ``~/.locoder/config.yaml`` > defaults.

Settings that loosen safety (``yolo``, ``allowed_paths``) are only honored
from the global config or CLI flags - never from the project file, since that
file can arrive with a cloned repo.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

import yaml

DEFAULT_MODEL = "qwen3.5:4b"

# Keys a project's .locoder/config.yaml is not allowed to set.
GLOBAL_ONLY_KEYS = ("yolo", "allowed_paths")

# Where the full-screen app puts the conversation (/layout): beside the
# sidebar on the left, a narrower band in the middle, or mirrored on the right.
CHAT_LAYOUTS = ("left", "center", "right")

# Keys the settings screen edits, and so writes back to the project file.
PROJECT_SETTINGS_KEYS = ("provider", "model", "base_url", "temperature", "num_ctx", "think")

# Everything is commented out: a value set here would hide the same setting
# in ~/.locoder/config.yaml (a starter file that set base_url: localhost
# stopped a VM from reaching Ollama on its host, for one). Uncomment only
# what this project needs to do differently.
CONFIG_TEMPLATE = f"""\
# locoder settings for this project. Anything left commented out comes from
# ~/.locoder/config.yaml, or locoder's defaults (shown here).
# provider: one of ollama, groq, openai, llamacpp, vllm, lmstudio
# provider: ollama
# model: {DEFAULT_MODEL}
# base_url: http://localhost:11434
# temperature: 0.2
# context window size (tokens) of the selected model - check the model's max
# with `ollama show <model>` before raising this; bigger uses more VRAM/RAM
# num_ctx: 32768
# fraction of num_ctx at which old turns get summarized/truncated
# context_compaction_threshold: 0.85
# api_key: only needed for hosted OpenAI-compatible endpoints
# timeout: 120
# think: false skips the model's long "thinking" step - much faster answers,
# a bit less careful on hard problems (only affects models that think, on Ollama)
# think: true
# web: true lets the model search the web and read pages (off by default:
# queries go to the internet). Toggle it for a session with /web.
# web: false
# brave_api_key / tavily_api_key: optional; put them in ~/.locoder/config.yaml,
# not here. Without one, search uses DuckDuckGo, with Wikipedia as backup.
# yolo and allowed_paths are ignored here for safety (a cloned repo could ship
# this file); set them in ~/.locoder/config.yaml or pass --yolo instead.
"""

_KEY_COMMENTS = {
    "provider": "provider: one of ollama, groq, openai, llamacpp, vllm, lmstudio",
    "num_ctx": "context window size (tokens) of the selected model",
    "context_compaction_threshold": "fraction of num_ctx at which old turns get summarized/truncated",
    "api_key": "api_key: only needed for hosted OpenAI-compatible endpoints",
}


def save_global_setting(key: str, value: Any, *, global_config_path: Optional[Path] = None) -> Path:
    """Set one key in ~/.locoder/config.yaml, leaving every other line - and
    comment, and secret - exactly as it was."""
    path = global_config_path or Path.home() / ".locoder" / "config.yaml"
    new_line = f"{key}: {json.dumps(value)}"
    lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
    pattern = re.compile(rf"^{re.escape(key)}\s*:")
    for i, line in enumerate(lines):
        if pattern.match(line):
            lines[i] = new_line
            break
    else:
        lines.append(new_line)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return path


def _same_file(a: Path, b: Path) -> bool:
    try:
        return a.resolve() == b.resolve()
    except OSError:
        return False


def _read_yaml(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    loaded = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    return loaded if isinstance(loaded, dict) else {}


@dataclass
class AppConfig:
    provider: str = "ollama"
    model: str = DEFAULT_MODEL
    base_url: str = "http://localhost:11434"
    temperature: float = 0.2
    num_ctx: int = 32768
    context_compaction_threshold: float = 0.85
    api_key: Optional[str] = None
    timeout: float = 120.0
    # False asks a thinking model (Ollama's qwen3 etc.) to skip its long
    # "thinking" step: far faster answers, a bit less careful on hard problems.
    think: bool = True
    # Web search / fetch tools. Off by default: queries leave the machine.
    web: bool = False
    # Brave Search API key; web search falls back to DuckDuckGo without one.
    brave_api_key: Optional[str] = None
    # Tavily API key: used for web search when there's no Brave key.
    tavily_api_key: Optional[str] = None
    chat_layout: str = "left"
    yolo: bool = False
    allowed_paths: list[str] = field(default_factory=list)
    # Not a config key: problems noticed while loading, for the UI to show.
    warnings: list[str] = field(default_factory=list, compare=False)

    @classmethod
    def _field_names(cls) -> set[str]:
        return {f.name for f in fields(cls)} - {"warnings"}

    @classmethod
    def load(
        cls,
        project_root: Path,
        *,
        model_override: Optional[str] = None,
        base_url_override: Optional[str] = None,
        provider_override: Optional[str] = None,
        num_ctx_override: Optional[int] = None,
        yolo_override: bool = False,
        global_config_path: Optional[Path] = None,
    ) -> "AppConfig":
        global_path = global_config_path or Path.home() / ".locoder" / "config.yaml"
        project_path = project_root / ".locoder" / "config.yaml"
        warnings: list[str] = []

        data: dict[str, Any] = dict(_read_yaml(global_path))

        # Run from the home folder, the "project" file is the global one: it
        # was just read as global, so don't read it again as an untrusted
        # project file (that would wrongly ignore yolo/allowed_paths in it).
        project_data = {} if _same_file(project_path, global_path) else _read_yaml(project_path)
        blocked = [k for k in GLOBAL_ONLY_KEYS if k in project_data]
        for key in blocked:
            project_data.pop(key)
        if blocked:
            warnings.append(
                f"ignored {', '.join(blocked)} in {project_path} - for safety these can only be "
                f"set in {global_path} or with --yolo"
            )
        data.update(project_data)

        env_map = {
            "LOCODER_PROVIDER": "provider",
            "LOCODER_MODEL": "model",
            "LOCODER_BASE_URL": "base_url",
            "LOCODER_API_KEY": "api_key",
            "BRAVE_API_KEY": "brave_api_key",
            "TAVILY_API_KEY": "tavily_api_key",
        }
        for env_var, key in env_map.items():
            if os.environ.get(env_var):
                data[key] = os.environ[env_var]

        known = cls._field_names()
        config = cls(**{k: v for k, v in data.items() if k in known})
        if config.chat_layout not in CHAT_LAYOUTS:
            warnings.append(f"chat_layout {config.chat_layout!r} isn't one of {', '.join(CHAT_LAYOUTS)}; using left")
            config.chat_layout = "left"
        config.warnings = warnings

        if provider_override:
            config.provider = provider_override
        if model_override:
            config.model = model_override
        if base_url_override:
            config.base_url = base_url_override
        if num_ctx_override:
            config.num_ctx = num_ctx_override
        if yolo_override:
            config.yolo = True

        return config

    def provider_kwargs(self) -> dict[str, Any]:
        return {
            "kind": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "temperature": self.temperature,
            "num_ctx": self.num_ctx,
            "api_key": self.api_key,
            "timeout": self.timeout,
            "think": self.think,
        }

    def save(self, project_root: Path, *, global_config_path: Optional[Path] = None) -> Path:
        """Write the settings-screen fields into the project config, keeping
        whatever else that file already had. Values that came from the global
        config or environment (an API key, say) are not copied into it, and
        global-only keys are never written there - unless the project file is
        the global file (run from the home folder), where they're kept as they are.
        """
        config_path = project_root / ".locoder" / "config.yaml"
        global_path = global_config_path or Path.home() / ".locoder" / "config.yaml"
        keep_global_only = _same_file(config_path, global_path)
        known = self._field_names()
        data = {
            k: v for k, v in _read_yaml(config_path).items()
            if k in known and (keep_global_only or k not in GLOBAL_ONLY_KEYS)
        }
        for key in PROJECT_SETTINGS_KEYS:
            data[key] = getattr(self, key)

        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(_render_yaml(data), encoding="utf-8")
        return config_path


def _render_yaml(data: dict[str, Any]) -> str:
    lines = ["# locoder configuration"]
    order = [f.name for f in fields(AppConfig)]
    for key in sorted(data, key=lambda k: order.index(k) if k in order else len(order)):
        if key in _KEY_COMMENTS:
            lines.append(f"# {_KEY_COMMENTS[key]}")
        # JSON scalars/lists are valid YAML and quote strings safely.
        lines.append(f"{key}: {json.dumps(data[key])}")
    lines.append("# yolo and allowed_paths are only honored in ~/.locoder/config.yaml (or --yolo)")
    return "\n".join(lines) + "\n"


def ensure_project_config(project_root: Path) -> Path:
    """Write a starter config.yaml the first time locoder runs in a project."""
    config_path = project_root / ".locoder" / "config.yaml"
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    return config_path
