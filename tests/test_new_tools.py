from __future__ import annotations

import sys
import time
from pathlib import Path

import pytest

from locoder.display import tool_summary, tool_target
from locoder.sandbox import Sandbox
from locoder.tools import registry, web_tools
from locoder.tools.base import ToolContext, ToolResult


def run(ctx, tool, **args):
    return registry.dispatch(tool, args, ctx)


# -- read_file with several paths ---------------------------------------------------

def test_read_several_files_at_once(ctx, tmp_path: Path):
    (tmp_path / "a.py").write_text("alpha\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("beta\n", encoding="utf-8")

    result = run(ctx, "read_file", paths=["a.py", "b.py", "missing.py"])

    assert not result.is_error, result.output
    assert "=== a.py ===" in result.output and "alpha" in result.output
    assert "=== b.py ===" in result.output and "beta" in result.output
    assert "could not read it" in result.output  # one missing file doesn't sink the rest


def test_read_file_needs_a_path(ctx):
    assert "give 'path'" in run(ctx, "read_file").output


# -- edit_file: replace_all and several edits ---------------------------------------

def test_replace_all(ctx, tmp_path: Path):
    target = tmp_path / "a.txt"
    target.write_text("x = 1\ny = x\nz = x\n", encoding="utf-8")

    result = run(ctx, "edit_file", path="a.txt", old_string="x", new_string="w", replace_all=True)

    assert not result.is_error, result.output
    assert target.read_text(encoding="utf-8") == "w = 1\ny = w\nz = w\n"
    assert "3 replacements" in result.output


def test_not_unique_mentions_replace_all(ctx, tmp_path: Path):
    (tmp_path / "a.txt").write_text("x\nx\n", encoding="utf-8")
    result = run(ctx, "edit_file", path="a.txt", old_string="x", new_string="y")
    assert result.is_error and "replace_all" in result.output


def test_several_edits_apply_in_order(ctx, tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("def f():\n    return 1\n\nprint(f())\n", encoding="utf-8")

    result = run(ctx, "edit_file", path="a.py", edits=[
        {"old_string": "def f():", "new_string": "def g():"},
        {"old_string": "print(f())", "new_string": "print(g())"},
    ])

    assert not result.is_error, result.output
    assert target.read_text(encoding="utf-8") == "def g():\n    return 1\n\nprint(g())\n"


def test_failed_edit_in_a_list_writes_nothing(ctx, tmp_path: Path):
    target = tmp_path / "a.py"
    target.write_text("one\ntwo\n", encoding="utf-8")

    result = run(ctx, "edit_file", path="a.py", edits=[
        {"old_string": "one", "new_string": "ONE"},
        {"old_string": "three", "new_string": "THREE"},
    ])

    assert result.is_error and "edit 2 of 2" in result.output
    assert target.read_text(encoding="utf-8") == "one\ntwo\n"


# -- find_files ---------------------------------------------------------------------

def test_find_files_by_name_and_path(ctx, tmp_path: Path):
    (tmp_path / "src" / "ui").mkdir(parents=True)
    (tmp_path / "src" / "app.ts").write_text("", encoding="utf-8")
    (tmp_path / "src" / "ui" / "Button.TEST.ts").write_text("", encoding="utf-8")
    (tmp_path / "node_modules" / "x").mkdir(parents=True)
    (tmp_path / "node_modules" / "x" / "skip.ts").write_text("", encoding="utf-8")

    by_name = run(ctx, "find_files", pattern="*.test.ts")
    assert by_name.output.splitlines() == ["src/ui/Button.TEST.ts"]  # case doesn't matter

    by_path = run(ctx, "find_files", pattern="src/**/*.ts")
    assert by_path.output.splitlines() == ["src/app.ts", "src/ui/Button.TEST.ts"]  # node_modules skipped

    folders = run(ctx, "find_files", pattern="ui")
    assert folders.output == "src/ui/"
    assert run(ctx, "find_files", pattern="*.rs").output == "no files found"


# -- move_path / delete_path --------------------------------------------------------

def test_move_renames_and_moves_into_folders(ctx, tmp_path: Path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    (tmp_path / "docs").mkdir()

    assert not run(ctx, "move_path", source="a.txt", destination="b.txt").is_error
    assert (tmp_path / "b.txt").exists() and not (tmp_path / "a.txt").exists()

    assert not run(ctx, "move_path", source="b.txt", destination="docs").is_error
    assert (tmp_path / "docs" / "b.txt").read_text(encoding="utf-8") == "hi"


def test_move_into_a_new_folder_with_a_trailing_slash(ctx, tmp_path: Path):
    (tmp_path / "a.txt").write_text("hi", encoding="utf-8")
    result = run(ctx, "move_path", source="a.txt", destination="docs/")
    assert not result.is_error, result.output
    assert (tmp_path / "docs" / "a.txt").read_text(encoding="utf-8") == "hi"


def test_move_refuses_to_overwrite(ctx, tmp_path: Path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "b.txt").write_text("b", encoding="utf-8")
    result = run(ctx, "move_path", source="a.txt", destination="b.txt")
    assert result.is_error and "already exists" in result.output
    assert (tmp_path / "b.txt").read_text(encoding="utf-8") == "b"


def test_delete_file_and_folder(ctx, tmp_path: Path):
    (tmp_path / "a.txt").write_text("a", encoding="utf-8")
    (tmp_path / "build" / "sub").mkdir(parents=True)
    (tmp_path / "build" / "sub" / "x.js").write_text("x", encoding="utf-8")

    assert not run(ctx, "delete_path", path="a.txt").is_error
    assert not (tmp_path / "a.txt").exists()
    result = run(ctx, "delete_path", path="build")
    assert not result.is_error and "everything in it" in result.output
    assert not (tmp_path / "build").exists()


def test_delete_refuses_the_project_root(ctx):
    result = run(ctx, "delete_path", path=".")
    assert result.is_error and "whole project" in result.output


def test_deleting_a_big_folder_is_flagged_as_risky(tmp_path: Path):
    seen = []
    ctx = ToolContext(sandbox=Sandbox(tmp_path), yolo=True,
                      confirm=lambda title, preview, reasons=(): seen.append(list(reasons)) or False)
    (tmp_path / "data").mkdir()
    for i in range(5):
        (tmp_path / "data" / f"{i}.csv").write_text("", encoding="utf-8")

    result = run(ctx, "delete_path", path="data")

    assert result.is_error and "declined" in result.output
    assert any("holds 5 files" in r for r in seen[0])
    assert (tmp_path / "data").exists()


# -- background processes -----------------------------------------------------------

PY = f'"{sys.executable}"'


def test_background_process_lifecycle(ctx, tmp_path: Path):
    script = tmp_path / "server.py"
    script.write_text("import time\nprint('listening on 8000', flush=True)\n"
                      "for i in range(300):\n    print('tick', i, flush=True)\n    time.sleep(0.2)\n", encoding="utf-8")

    started = run(ctx, "start_process", command=f"{PY} server.py", name="dev server")
    try:
        assert not started.is_error, started.output
        assert "process 1 (dev server): running" in started.output
        assert "listening on 8000" in started.output

        later = run(ctx, "read_process", id=1, wait_seconds=3)
        assert "running" in later.output and "tick" in later.output
        assert "listening on 8000" not in later.output  # only what's new

        listing = run(ctx, "read_process")
        assert "process 1 (dev server)" in listing.output
    finally:
        stopped = run(ctx, "stop_process", id=1)
    assert "stopped process 1" in stopped.output
    assert ctx.processes.processes[1].proc.poll() is not None


def test_process_that_finishes_at_once(ctx):
    result = run(ctx, "start_process", command=f'{PY} -c "print(42)"')
    assert "exited with code 0" in result.output and "42" in result.output
    assert "run_command is simpler" in result.output


def test_unknown_process_id(ctx):
    result = run(ctx, "read_process", id=7)
    assert result.is_error and "no background process" in result.output


# -- todos / ask / notify -----------------------------------------------------------

def test_update_todos_renders_and_shows(tmp_path: Path):
    shown = []
    ctx = ToolContext(sandbox=Sandbox(tmp_path), yolo=True, confirm=lambda *a, **k: True, show_todos=shown.append)

    result = run(ctx, "update_todos", todos=[
        {"text": "Read the code", "status": "completed"},
        {"text": "Fix the bug", "status": "in progress"},
        "Run the tests",
    ])

    assert not result.is_error, result.output
    assert result.output.splitlines() == ["[x] Read the code", "[>] Fix the bug", "[ ] Run the tests", "(1 of 3 done)"]
    assert shown[-1][1] == {"text": "Fix the bug", "status": "in_progress"}


def test_update_todos_rejects_unknown_status(ctx):
    result = run(ctx, "update_todos", todos=[{"text": "x", "status": "maybe"}])
    assert result.is_error and "status must be one of" in result.output


def test_ask_user_with_and_without_an_answer(tmp_path: Path):
    asked = []
    ctx = ToolContext(sandbox=Sandbox(tmp_path), yolo=True, confirm=lambda *a, **k: True,
                      ask=lambda q, opts: asked.append((q, opts)) or "the blue one")
    assert run(ctx, "ask_user", question="Which theme?", options=["blue", "red"]).output == "The user answered: the blue one"
    assert asked == [("Which theme?", ["blue", "red"])]

    nobody = ToolContext(sandbox=Sandbox(tmp_path), yolo=True, confirm=lambda *a, **k: True)
    assert "didn't answer" in run(nobody, "ask_user", question="Which theme?").output


def test_notify_user_is_limited_to_one_a_minute(tmp_path: Path):
    sent = []
    ctx = ToolContext(sandbox=Sandbox(tmp_path), yolo=True, confirm=lambda *a, **k: True,
                      notify_user=lambda t, m: sent.append((t, m)))
    assert not run(ctx, "notify_user", title="Tests passing", message="All 40 tests pass now.").is_error
    second = run(ctx, "notify_user", title="Again", message="Too soon.")
    assert second.is_error and "at most one a minute" in second.output
    assert sent == [("Tests passing", "All 40 tests pass now.")]

    ctx.last_notify = time.monotonic() - 61
    assert not run(ctx, "notify_user", title="Done", message="Finished.").is_error


# -- web ----------------------------------------------------------------------------

DUCK_HTML = """
<div class="result"><a rel="nofollow" class="result__a"
   href="//duckduckgo.com/l/?uddg=https%3A%2F%2Fdocs.python.org%2F3%2Flibrary%2Fpathlib.html&amp;rut=abc">pathlib &mdash; Object-oriented paths</a>
 <a class="result__snippet" href="#">Source code: <b>Lib/pathlib</b>.py</a></div>
<div class="result"><a class="result__a" href="https://duckduckgo.com/y.js?ad=1">An ad</a></div>
"""


class FakeResponse:
    def __init__(self, text="", status=200, headers=None, content=b""):
        self.text, self.status_code = text, status
        self.headers = headers or {}

    def raise_for_status(self):
        pass


def test_web_tools_refuse_when_off(ctx):
    for name, args in (("web_search", {"query": "x"}), ("web_fetch", {"url": "https://example.com"})):
        result = registry.dispatch(name, args, ctx)
        assert result.is_error and "web access is off" in result.output


def test_duckduckgo_results_are_parsed_and_marked_untrusted(ctx, monkeypatch):
    ctx.web_enabled = True
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.setattr(web_tools.requests, "post", lambda *a, **k: FakeResponse(DUCK_HTML))

    result = run(ctx, "web_search", query="python pathlib")

    assert not result.is_error, result.output
    assert result.output.startswith(web_tools.UNTRUSTED)
    assert "1. pathlib — Object-oriented paths" in result.output
    assert "https://docs.python.org/3/library/pathlib.html" in result.output
    assert "Lib/pathlib .py" in result.output or "Lib/pathlib.py" in result.output
    assert "An ad" not in result.output


def test_duckduckgo_bot_check_falls_back_to_wikipedia(ctx, monkeypatch):
    ctx.web_enabled = True
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    monkeypatch.delenv("TAVILY_API_KEY", raising=False)
    monkeypatch.setattr(web_tools.requests, "post", lambda *a, **k: FakeResponse("<html>anomaly</html>", status=202))
    wiki = {"query": {"search": [{"title": "Python (programming language)",
                                  "snippet": "<span class=\"searchmatch\">Python</span> is a language"}]}}
    monkeypatch.setattr(web_tools.requests, "get", lambda *a, **k: JsonResponse(wiki))
    result = run(ctx, "web_search", query="python")
    assert not result.is_error, result.output
    assert "(from Wikipedia)" in result.output and "tavily_api_key" in result.output
    assert "https://en.wikipedia.org/wiki/Python_%28programming_language%29" in result.output
    assert "Python is a language" in result.output


class JsonResponse(FakeResponse):
    def __init__(self, data, status=200):
        super().__init__(status=status)
        self.data = data

    def json(self):
        return self.data


def test_tavily_is_used_when_there_is_a_key(ctx, monkeypatch):
    ctx.web_enabled = True
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    ctx.tavily_api_key = "tvly-test"
    sent = {}

    def fake_post(url, **kwargs):
        sent.update(url=url, **kwargs)
        return JsonResponse({"results": [{"title": "Pathlib docs", "url": "https://docs.python.org/3/library/pathlib.html",
                                          "content": "Object-oriented filesystem paths"}]})

    monkeypatch.setattr(web_tools.requests, "post", fake_post)
    result = run(ctx, "web_search", query="python pathlib", count=3)
    assert not result.is_error, result.output
    assert sent["url"] == "https://api.tavily.com/search"
    assert sent["headers"]["Authorization"] == "Bearer tvly-test" and sent["json"]["max_results"] == 3
    assert "(from Tavily)" in result.output and "Object-oriented filesystem paths" in result.output


def test_tavily_bad_key_and_used_up_plan_say_so(ctx, monkeypatch):
    ctx.web_enabled = True
    monkeypatch.delenv("BRAVE_API_KEY", raising=False)
    ctx.tavily_api_key = "tvly-wrong"
    for status, words in ((401, "rejected the API key"), (432, "run out of searches")):
        monkeypatch.setattr(web_tools.requests, "post", lambda *a, s=status, **k: JsonResponse({}, status=s))
        result = run(ctx, "web_search", query="x")
        assert result.is_error and words in result.output


def test_brave_wins_over_tavily(ctx, monkeypatch):
    ctx.web_enabled = True
    ctx.brave_api_key, ctx.tavily_api_key = "brave-key", "tvly-key"
    used = []
    monkeypatch.setattr(web_tools, "_search_brave", lambda q, c, k: used.append("brave") or [])
    monkeypatch.setattr(web_tools, "_search_tavily", lambda q, c, k: used.append("tavily") or [])
    run(ctx, "web_search", query="x")
    assert used == ["brave"]


def test_fetch_refuses_local_addresses(ctx):
    ctx.web_enabled = True
    for url in ("http://localhost:8000/", "http://127.0.0.1/", "http://192.168.1.1/admin", "file:///etc/passwd"):
        result = run(ctx, "web_fetch", url=url)
        assert result.is_error, url


def test_html_becomes_readable_text():
    parser = web_tools._TextParser()
    parser.feed("<html><head><title>Docs</title><style>p{}</style></head><body><h1>Intro</h1>"
                "<p>Hello   <b>world</b></p><script>evil()</script><ul><li>one</li><li>two</li></ul></body></html>")
    assert parser.title == "Docs"
    text = parser.text()
    assert "# Intro" in text and "Hello world" in text and "- one" in text
    assert "evil" not in text and "p{}" not in text


# -- the agent only offers what's switched on ---------------------------------------

def _names(agent):
    return {s["function"]["name"] for s in agent._tool_schemas()}


def test_web_tools_only_offered_when_on(tmp_path: Path):
    from tests.test_agent_loop import RecordingUI, ScriptedProvider, _agent

    agent = _agent(tmp_path, ScriptedProvider([]), RecordingUI())
    assert "web_search" not in _names(agent) and "find_files" in _names(agent)
    agent.set_web(True)
    assert {"web_search", "web_fetch"} <= _names(agent)


def test_read_only_modes_keep_the_harmless_new_tools(tmp_path: Path):
    from locoder.agent import AgentMode
    from tests.test_agent_loop import RecordingUI, ScriptedProvider, _agent

    agent = _agent(tmp_path, ScriptedProvider([]), RecordingUI())
    agent.set_mode(AgentMode.PLAN)
    names = _names(agent)
    assert {"find_files", "update_todos", "ask_user", "read_process"} <= names
    assert not {"delete_path", "move_path", "start_process"} & names


# -- one-line display of every new tool ---------------------------------------------

@pytest.mark.parametrize("name,args,output,expected_target,expected_summary", [
    ("find_files", {"pattern": "*.py"}, "a.py\nb.py", "*.py", "2 found"),
    ("move_path", {"source": "a", "destination": "b"}, "moved", "a -> b", "moved"),
    ("delete_path", {"path": "build"}, "deleted", "build", "deleted"),
    ("start_process", {"command": "npm run dev"}, "process 1 (npm): running\nok", "npm run dev", "running"),
    ("read_process", {"id": 2}, "process 2 (x): exited with code 1\n", "process 2", "exited with code 1"),
    ("stop_process", {"id": 2}, "stopped process 2 (x)", "process 2", "stopped"),
    ("update_todos", {"todos": [1, 2]}, "[x] a\n[ ] b\n(1 of 2 done)", "2 steps", "1 of 2 done"),
    ("ask_user", {"question": "Which?"}, "The user answered: A", "Which?", "answered"),
    ("notify_user", {"title": "Done"}, "notification sent: Done", "Done", "sent"),
    ("read_file", {"paths": ["a", "b"]}, "=== a ===\n1\n\n=== b ===\n2", "a, b", "2 files"),
    ("edit_file", {"path": "a", "edits": [{}, {}]}, "applied", "a", "2 edits"),
])
def test_display_lines(name, args, output, expected_target, expected_summary):
    assert tool_target(name, args) == expected_target
    assert tool_summary(name, args, ToolResult(output=output)) == expected_summary
