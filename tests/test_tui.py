"""Headless checks of the full-screen app (Textual's run_test pilot)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, Iterator, Optional

import pytest

from locoder.agent import Agent
from locoder.config import AppConfig
from locoder.context import ContextManager
from locoder.providers.base import Message, ModelProvider, StreamEvent
from locoder.recent import RecentItem
from locoder.sandbox import Sandbox
from locoder.session import SessionStore
from locoder.tui.app import LocoderApp, SwitchProject
from locoder.tui.home import HomeRecent, RecentList
from locoder.ui import UI


class QuietProvider(ModelProvider):
    def __init__(self) -> None:
        super().__init__(model="test-model", base_url="http://localhost:11434")

    def chat(self, messages: list[Message], tools: Optional[list[dict[str, Any]]] = None, *, stream: bool = True) -> Iterator[StreamEvent]:
        yield StreamEvent(type="done", message=Message(role="assistant", content="ok"))

    def list_models(self) -> list[str]:
        return ["test-model"]


def _app(tmp_path: Path) -> LocoderApp:
    (tmp_path / "notes.md").write_text("hello", encoding="utf-8")
    session = SessionStore(tmp_path)
    session.save([Message(role="system", content="p"), Message(role="user", content="earlier question")])
    session.start_new()
    agent = Agent(QuietProvider(), UI(), Sandbox(tmp_path), ContextManager(num_ctx=8192), session)
    return LocoderApp(agent, session, AppConfig(), tmp_path)


def _run(coro) -> None:
    asyncio.run(coro)


@pytest.fixture(autouse=True)
def no_global_projects(monkeypatch, tmp_path: Path):
    # Keep the real ~/.locoder/recent_projects.json out of these tests.
    import locoder.tui.app as app_module

    monkeypatch.setattr(app_module, "recent_projects", lambda current: [
        RecentItem("project", "~/other", "opened 2d ago", str(tmp_path)),
    ])


def test_home_shows_recent_items_and_keyboard_navigation(tmp_path: Path):
    async def scenario():
        app = _app(tmp_path)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause(0.5)
            recent = app.query_one(HomeRecent)
            assert recent.display and not recent.has_class("-empty")
            assert recent._tab == "conversations"

            await pilot.press("down")  # from the empty input into the list
            assert isinstance(app.focused, RecentList)
            await pilot.press("right")
            assert recent._tab == "files"
            await pilot.press("enter")  # attach the file
            await pilot.pause(0.1)
            assert app.chat_input.value == "@notes.md "
            assert app.focused is app.chat_input

    _run(scenario())


def test_picking_a_project_exits_with_switch_result(tmp_path: Path):
    async def scenario():
        app = _app(tmp_path)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause(0.5)
            app.query_one(HomeRecent).select_tab("projects")
            app.query_one(HomeRecent).focus_list()
            await pilot.press("enter")
            await pilot.pause(0.1)
        assert app.return_value == SwitchProject(tmp_path)

    _run(scenario())


def test_sidebar_collapses_with_animation_and_comes_back(tmp_path: Path):
    async def scenario():
        app = _app(tmp_path)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause(0.3)
            app._show_home(False)  # the sidebar only shows outside the home screen
            await pilot.pause(0.2)
            full = app.sidebar.region.width
            assert full > 20

            # Called directly: pilot.press() waits for animations to finish,
            # which would hide the in-between widths.
            app.action_toggle_sidebar()
            await pilot.pause(0.08)
            mid = app.sidebar.region.width
            assert 0 < mid < full, "should still be narrowing"
            await pilot.pause(0.4)
            assert app.screen.has_class("sidebar-hidden")
            assert app.sidebar.region.width == 0

            await pilot.press("ctrl+b")  # the real key brings it back
            await pilot.pause(0.1)
            assert not app.screen.has_class("sidebar-hidden")
            assert app.sidebar.region.width == full
            assert app.sidebar.styles.opacity == 1.0

    _run(scenario())
