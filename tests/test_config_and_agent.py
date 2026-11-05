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


def test_starter_project_config_doesnt_hide_global_settings(tmp_path: Path, monkeypatch):
    # e.g. locoder in a VM using the host's Ollama: base_url set globally
    monkeypatch.delenv("LOCODER_BASE_URL", raising=False)
    global_path = tmp_path / "global.yaml"
    _write(global_path, {"base_url": "http://10.0.2.2:11434", "temperature": 0.7})
    project = tmp_path / "project"
    ensure_project_config(project)

    config = AppConfig.load(project, global_config_path=global_path)

    assert config.base_url == "http://10.0.2.2:11434"
    assert config.temperature == 0.7
    assert not config.warnings


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


def test_home_folder_project_config_is_the_global_one(tmp_path: Path):
    """Run from the home folder, the project's config file *is* the global one:
    no false "ignored yolo" warning, and saving settings keeps yolo/allowed_paths."""
    from locoder.config import AppConfig

    home = tmp_path
    global_file = home / ".locoder" / "config.yaml"
    global_file.parent.mkdir(parents=True)
    global_file.write_text("yolo: true\nallowed_paths: ['D:/shared']\nmodel: m1\n", encoding="utf-8")

    config = AppConfig.load(home, global_config_path=global_file)
    assert config.warnings == []
    assert config.yolo is True and config.allowed_paths == ["D:/shared"] and config.model == "m1"

    config.model = "m2"
    config.save(home, global_config_path=global_file)
    saved = AppConfig.load(home, global_config_path=global_file)
    assert saved.model == "m2" and saved.yolo is True and saved.allowed_paths == ["D:/shared"]


def test_a_real_project_still_cannot_turn_on_yolo(tmp_path: Path):
    from locoder.config import AppConfig

    global_file = tmp_path / "home" / ".locoder" / "config.yaml"
    project = tmp_path / "project"
    (project / ".locoder").mkdir(parents=True)
    (project / ".locoder" / "config.yaml").write_text("yolo: true\n", encoding="utf-8")
    config = AppConfig.load(project, global_config_path=global_file)
    assert config.yolo is False and "ignored yolo" in config.warnings[0]


def test_save_global_setting_changes_only_its_line(tmp_path: Path):
    from locoder.config import AppConfig, save_global_setting

    path = tmp_path / ".locoder" / "config.yaml"
    path.parent.mkdir()
    path.write_text("# my settings\nmodel: m\nchat_layout: left\nsome_key: secret\n", encoding="utf-8")
    save_global_setting("chat_layout", "center", global_config_path=path)
    assert path.read_text(encoding="utf-8") == '# my settings\nmodel: m\nchat_layout: "center"\nsome_key: secret\n'
    assert AppConfig.load(tmp_path / "project", global_config_path=path).chat_layout == "center"

    path.write_text("chat_layout: sideways\n", encoding="utf-8")
    config = AppConfig.load(tmp_path / "project", global_config_path=path)
    assert config.chat_layout == "left" and "isn't one of" in config.warnings[0]
