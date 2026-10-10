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
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

import yaml

DEFAULT_MODEL = "qwen3.5:4b"

# Keys a project's .locoder/config.yaml is not allowed to set.
GLOBAL_ONLY_KEYS = ("yolo", "allowed_paths")

# Keys the settings screen edits, and so writes back to the project file.
PROJECT_SETTINGS_KEYS = ("provider", "model", "base_url", "temperature", "num_ctx")

CONFIG_TEMPLATE = f"""\
# locoder configuration
# provider: one of ollama, openai, llamacpp, vllm, lmstudio
provider: ollama
model: {DEFAULT_MODEL}
base_url: http://localhost:11434
temperature: 0.2
# context window size (tokens) of the selected model - check the model's max
# with `ollama show <model>` before raising this; bigger uses more VRAM/RAM
num_ctx: 32768
# fraction of num_ctx at which old turns get summarized/truncated
context_compaction_threshold: 0.85
# api_key: only needed for hosted OpenAI-compatible endpoints
api_key: null
timeout: 120
# yolo and allowed_paths are ignored here for safety (a cloned repo could ship
# this file); set them in ~/.locoder/config.yaml or pass --yolo instead.
"""

_KEY_COMMENTS = {
    "provider": "provider: one of ollama, openai, llamacpp, vllm, lmstudio",
    "num_ctx": "context window size (tokens) of the selected model",
    "context_compaction_threshold": "fraction of num_ctx at which old turns get summarized/truncated",
    "api_key": "api_key: only needed for hosted OpenAI-compatible endpoints",
}


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

        project_data = _read_yaml(project_path)
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
        }
        for env_var, key in env_map.items():
            if os.environ.get(env_var):
                data[key] = os.environ[env_var]

        known = cls._field_names()
        config = cls(**{k: v for k, v in data.items() if k in known})
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
        }

    def save(self, project_root: Path) -> Path:
        """Write the settings-screen fields into the project config, keeping
        whatever else that file already had. Values that came from the global
        config or environment (an API key, say) are not copied into it, and
        global-only keys are never written there.
        """
        config_path = project_root / ".locoder" / "config.yaml"
        known = self._field_names()
        data = {
            k: v for k, v in _read_yaml(config_path).items() if k in known and k not in GLOBAL_ONLY_KEYS
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
