from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import yaml

from locoder.agent import Agent, AgentMode
from locoder.config import DEFAULT_MODEL, AppConfig, ensure_project_config
from locoder.context import SUMMARY_PREFIX, ContextManager
from locoder.providers.base import Message
from locoder.sandbox import Sandbox
from locoder.session import SessionStore


def _write(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_default_model(tmp_path: Path):
    ensure_project_config(tmp_path)
    config = AppConfig.load(tmp_path, global_config_path=tmp_path / "no-global.yaml")
    assert DEFAULT_MODEL == "qwen3.5:4b"
    assert config.model == "qwen3.5:4b"


def test_project_config_cannot_enable_yolo_or_widen_sandbox(tmp_path: Path, monkeypatch):
    for var in ("LOCODER_PROVIDER", "LOCODER_MODEL", "LOCODER_BASE_URL", "LOCODER_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    _write(tmp_path / ".locoder" / "config.yaml", {"model": "m", "yolo": True, "allowed_paths": ["~"]})

    config = AppConfig.load(tmp_path, global_config_path=tmp_path / "no-global.yaml")

    assert config.model == "m"
    assert config.yolo is False
    assert config.allowed_paths == []
    assert config.warnings and "yolo" in config.warnings[0]


def test_global_config_can_enable_yolo(tmp_path: Path):
    global_path = tmp_path / "global.yaml"
    _write(global_path, {"yolo": True, "allowed_paths": ["/tmp"]})

    config = AppConfig.load(tmp_path, global_config_path=global_path)

    assert config.yolo is True
    assert config.allowed_paths == ["/tmp"]
    assert config.warnings == []


def test_save_does_not_leak_env_api_key_or_global_only_keys(tmp_path: Path, monkeypatch):
    monkeypatch.setenv("LOCODER_API_KEY", "sk-secret")
    project_path = tmp_path / ".locoder" / "config.yaml"
    _write(project_path, {"timeout": 300})
    config = AppConfig.load(tmp_path, global_config_path=tmp_path / "no-global.yaml")
    config.model = "new-model"
    config.yolo = True

    config.save(tmp_path)

    saved = yaml.safe_load(project_path.read_text(encoding="utf-8"))
    assert saved["model"] == "new-model"
    assert saved["timeout"] == 300
    assert "api_key" not in saved
    assert "yolo" not in saved
    assert "allowed_paths" not in saved


def test_effective_messages_have_a_single_leading_system_message(tmp_path: Path):
    agent = Agent(
        provider=None,  # type: ignore[arg-type]
        ui=SimpleNamespace(info=lambda msg: None),  # type: ignore[arg-type]
        sandbox=Sandbox(tmp_path),
        ctx_manager=ContextManager(num_ctx=1000),
        session=SessionStore(tmp_path),
    )
    agent.messages += [
        Message(role="system", content=f"{SUMMARY_PREFIX}\nfacts"),
        Message(role="user", content="hi"),
    ]
    agent.set_mode(AgentMode.PLAN)

    effective = agent._effective_messages()

    assert [m.role for m in effective] == ["system", "user"]
    assert "PLAN mode" in effective[0].content
    assert "facts" in effective[0].content
