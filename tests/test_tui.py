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
            await pilot.pause(0.5)  # longer than the 0.22s animation, even on a slow run
            assert not app.screen.has_class("sidebar-hidden")
            assert app.sidebar.region.width == full
            assert app.sidebar.styles.opacity == 1.0

    _run(scenario())


def test_approval_dialog_lists_why_an_action_is_risky(tmp_path: Path):
    from textual.widgets import Static

    from locoder.tui.modals import ConfirmModal

    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.run_test(size=(110, 40)) as pilot:
            answers: list[bool] = []
            app.push_screen(ConfirmModal("Run shell command", "$ rm -rf build", "text",
                                         reasons=["it deletes folders and everything in them"]), answers.append)
            await pilot.pause()
            warning = app.screen.query_one("#confirm-warning", Static)
            assert "it deletes folders and everything in them" in str(warning.render())
            await pilot.press("n")
            assert answers == [False]

    _run(scenario())


def test_question_dialog_takes_an_option_or_typed_answer(tmp_path: Path):
    from locoder.tui.modals import AskModal

    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.run_test(size=(110, 40)) as pilot:
            answers: list = []
            app.push_screen(AskModal("Which colour?", ["blue", "red"]), answers.append)
            await pilot.pause()
            await pilot.press("down", "enter")  # options start focused: pick the second
            await pilot.pause()

            app.push_screen(AskModal("Name it?", []), answers.append)
            await pilot.pause()
            await pilot.press(*"lumen", "enter")
            await pilot.pause()

            app.push_screen(AskModal("Anything?", ["x"]), answers.append)
            await pilot.pause()
            await pilot.press("escape")
            await pilot.pause()
            assert answers == ["red", "lumen", None]

    _run(scenario())


def test_sidebar_shows_the_plan_and_web_switch(tmp_path: Path):
    async def scenario() -> None:
        app = _app(tmp_path)
        async with app.run_test(size=(110, 40)) as pilot:
            await pilot.pause(0.2)
            app._show_home(False)
            assert "web off" in " ".join(t.plain for t in app.sidebar.lines)

            app.sidebar.set_todos([{"text": "Read the code", "status": "done"},
                                   {"text": "Fix the bug", "status": "in_progress"}])
            app._dispatch_command("web", "")
            await pilot.pause()
            shown = " ".join(t.plain for t in app.sidebar.lines)
            assert "1/2 done" in shown and "Fix the bug" in shown
            assert "web on" in shown and app.agent.web

    _run(scenario())


def test_layout_moves_the_conversation_and_is_remembered(tmp_path: Path):
    settings = tmp_path / "global.yaml"
    settings.write_text("model: m\n# keep this comment\n", encoding="utf-8")

    async def scenario():
        app = _app(tmp_path)
        app.settings_file = settings
        async with app.run_test(size=(150, 34)) as pilot:
            await pilot.pause(0.3)
            app._show_home(False)
            await pilot.pause(0.2)
            left = (app.chat_log.region, app.sidebar.region)
            assert left[0].x < left[1].x  # conversation left, sidebar right

            app._dispatch_command("layout", "center")
            await pilot.pause(0.3)
            chat, column = app.chat_log.region, app.query_one("#main-column").region
            assert app.screen.has_class("layout-center") and chat.width < 100
            # in the middle of the column, give or take the box's margins
            assert abs((chat.x + chat.width / 2) - (column.x + column.width / 2)) <= 3

            app.action_toggle_sidebar()  # the column widens: the band stays in the middle
            await pilot.pause(0.6)
            chat, column = app.chat_log.region, app.query_one("#main-column").region
            assert abs((chat.x + chat.width / 2) - (column.x + column.width / 2)) <= 3
            app.action_toggle_sidebar()
            await pilot.pause(0.6)

            app._dispatch_command("layout", "right")
            await pilot.pause(0.3)
            assert app.sidebar.region.x < app.chat_log.region.x  # mirrored

            app._dispatch_command("layout", "")  # steps on: right -> left
            await pilot.pause(0.3)
            assert app.chat_layout == "left" and app.chat_log.region.x < app.sidebar.region.x

            app._dispatch_command("layout", "sideways")
            assert app.chat_layout == "left"

    _run(scenario())
    text = settings.read_text(encoding="utf-8")
    assert "# keep this comment" in text and "model: m" in text and text.count("chat_layout") == 1
    assert 'chat_layout: "left"' in text
