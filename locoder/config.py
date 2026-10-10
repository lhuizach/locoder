"""Loads and merges locoder configuration.

Precedence (highest wins): CLI flags > environment variables >
project ``.locoder/config.yaml`` > global ``~/.locoder/config.yaml`` > defaults.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field, fields
from pathlib import Path
from typing import Any, Optional

import yaml

CONFIG_TEMPLATE = """\
# locoder configuration
# provider: one of ollama, openai, llamacpp, vllm, lmstudio
provider: ollama
model: qwen2.5-coder:latest
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
# extra directories (outside the project root) the agent may read/write
allowed_paths: []
"""


@dataclass
class AppConfig:
    provider: str = "ollama"
    model: str = "qwen2.5-coder:latest"
    base_url: str = "http://localhost:11434"
    temperature: float = 0.2
    num_ctx: int = 32768
    context_compaction_threshold: float = 0.85
    api_key: Optional[str] = None
    timeout: float = 120.0
    yolo: bool = False
    allowed_paths: list[str] = field(default_factory=list)

    @classmethod
    def _field_names(cls) -> set[str]:
        return {f.name for f in fields(cls)}

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
    ) -> "AppConfig":
        data: dict[str, Any] = {}

        for candidate in (Path.home() / ".locoder" / "config.yaml", project_root / ".locoder" / "config.yaml"):
            if candidate.exists():
                loaded = yaml.safe_load(candidate.read_text(encoding="utf-8")) or {}
                data.update(loaded)

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

    def to_yaml_text(self) -> str:
        return (
            "# locoder configuration\n"
            "# provider: one of ollama, openai, llamacpp, vllm, lmstudio\n"
            f"provider: {self.provider}\n"
            f"model: {self.model}\n"
            f"base_url: {self.base_url}\n"
            f"temperature: {self.temperature}\n"
            "# context window size (tokens) of the selected model\n"
            f"num_ctx: {self.num_ctx}\n"
            "# fraction of num_ctx at which old turns get summarized/truncated\n"
            f"context_compaction_threshold: {self.context_compaction_threshold}\n"
            "# api_key: only needed for hosted OpenAI-compatible endpoints\n"
            f"api_key: {self.api_key if self.api_key else 'null'}\n"
            f"timeout: {self.timeout}\n"
            "# skip confirmation prompts for writes/edits/commands\n"
            f"yolo: {'true' if self.yolo else 'false'}\n"
            "# extra directories (outside the project root) the agent may read/write\n"
            f"allowed_paths: {self.allowed_paths!r}\n"
        )

    def save(self, project_root: Path) -> Path:
        config_path = project_root / ".locoder" / "config.yaml"
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(self.to_yaml_text(), encoding="utf-8")
        return config_path


def ensure_project_config(project_root: Path) -> Path:
    """Write a starter config.yaml the first time locoder runs in a project."""
    config_path = project_root / ".locoder" / "config.yaml"
    if not config_path.exists():
        config_path.parent.mkdir(parents=True, exist_ok=True)
        config_path.write_text(CONFIG_TEMPLATE, encoding="utf-8")
    return config_path
