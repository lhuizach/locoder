"""Entry point: argument parsing + the interactive REPL."""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path
from typing import Optional

from prompt_toolkit import PromptSession
from prompt_toolkit.history import FileHistory

from locoder import __version__
from locoder.agent import Agent
from locoder.config import AppConfig, ensure_project_config
from locoder.context import ContextManager
from locoder.providers.base import ModelProvider, ProviderError
from locoder.providers.registry import create_provider
from locoder.recent import record_project
from locoder.sandbox import Sandbox
from locoder.session import SessionStore
from locoder.ui import UI


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="locoder", description="A local-first CLI coding agent.")
    parser.add_argument("--model", help="override the model name from config")
    parser.add_argument("--provider", help="override provider kind: ollama, groq, openai, llamacpp, vllm, lmstudio")
    parser.add_argument("--base-url", help="override the backend base URL")
    parser.add_argument("--num-ctx", type=int, help="override the context window size (tokens)")
    parser.add_argument("--yolo", action="store_true", help="skip confirmation prompts for writes, edits, and commands")
    parser.add_argument(
        "--plain",
        action="store_true",
        help="use a plain line-based REPL instead of the full-screen TUI (for unsupported terminals or scripting)",
    )
    parser.add_argument("--version", action="version", version=f"locoder {__version__}")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    project_root = Path.cwd()
    while True:
        result = _run(args, project_root)
        # Picking another project on the home screen restarts locoder there.
        if isinstance(result, Path):
            os.chdir(result)
            project_root = Path.cwd()
            continue
        return result


def _run(args: argparse.Namespace, project_root: Path) -> int | Path:
    """Run locoder in ``project_root``: an exit code, or the project to switch to."""
    ensure_project_config(project_root)
    record_project(project_root)
    config = AppConfig.load(
        project_root,
        model_override=args.model,
        base_url_override=args.base_url,
        provider_override=args.provider,
        num_ctx_override=args.num_ctx,
        yolo_override=args.yolo,
    )

    ui = UI()

    try:
        provider = create_provider(**config.provider_kwargs())
    except ValueError as exc:
        ui.error(str(exc))
        return 1

    sandbox = Sandbox(project_root, allowed_paths=config.allowed_paths)
    ctx_manager = ContextManager(num_ctx=config.num_ctx, threshold=config.context_compaction_threshold)
    session = SessionStore(project_root)
    agent = Agent(provider, ui, sandbox, ctx_manager, session, yolo=config.yolo)

    if args.plain:
        ui.banner(
            model=config.model,
            base_url=config.base_url,
            provider=config.provider,
            yolo=config.yolo,
            project_root=str(project_root),
        )
        for warning in config.warnings:
            ui.warn(warning)
        _check_backend_reachable(provider, ui)
        repl(agent, ui, session)
        return 0

    from locoder.tui.app import LocoderApp, SwitchProject

    try:
        result = LocoderApp(agent, session, config, project_root).run()
    except Exception as exc:  # noqa: BLE001 - fall back with actionable guidance
        print(f"locoder: the full-screen UI failed to start ({exc})")
        print("Try running from Windows Terminal, cmd.exe, or PowerShell, or use 'locoder --plain'.")
        return 1
    if isinstance(result, SwitchProject) and result.path.is_dir():
        return result.path
    return 0


def _check_backend_reachable(provider: ModelProvider, ui: UI) -> None:
    try:
        provider.list_models()
    except ProviderError as exc:
        ui.warn(f"could not reach the model backend yet ({exc}); will retry when you send a message")


def repl(agent: Agent, ui: UI, session: SessionStore) -> None:
    session.history_file.parent.mkdir(parents=True, exist_ok=True)
    prompt_session: Optional[PromptSession] = None
    try:
        prompt_session = PromptSession(history=FileHistory(str(session.history_file)))
    except Exception:
        # prompt_toolkit needs a real Win32 console screen buffer; that's not
        # available under some terminals (plain Git Bash/mintty without
        # winpty, fully redirected output, etc). Fall back to plain input()
        # rather than crashing - you lose arrow-key history, not the agent.
        ui.warn("this terminal doesn't support advanced input editing; falling back to plain input (no history)")

    while True:
        try:
            user_input = prompt_session.prompt(ui.prompt) if prompt_session else input(ui.prompt)
        except EOFError:
            ui.system("goodbye!")
            break
        except KeyboardInterrupt:
            continue

        user_input = user_input.strip()
        if not user_input:
            continue

        if user_input.startswith("/"):
            if _handle_slash_command(user_input, agent, ui):
                break
            continue

        try:
            agent.run_turn(user_input)
        except KeyboardInterrupt:
            ui.warn("interrupted")
        except Exception as exc:  # noqa: BLE001 - keep the REPL alive on unexpected errors
            ui.error(f"unexpected error: {exc}")


def _handle_slash_command(raw: str, agent: Agent, ui: UI) -> bool:
    """Returns True if the REPL should exit."""
    parts = raw[1:].split(maxsplit=1)
    cmd = parts[0].lower() if parts else ""
    arg = parts[1].strip() if len(parts) > 1 else ""

    if cmd in ("exit", "quit"):
        ui.system("goodbye!")
        return True
    if cmd == "clear":
        agent.clear()
        ui.system("conversation cleared; started a new session")
        return False
    if cmd == "help":
        ui.help()
        return False
    if cmd == "model":
        if not arg:
            ui.system(f"current model: {agent.provider.model}")
            return False
        agent.set_provider(agent.provider.with_model(arg))
        ui.system(f"switched model to '{arg}'")
        return False
    if cmd == "yolo":
        agent.set_yolo(not agent.yolo)
        ui.system(f"yolo mode {'enabled' if agent.yolo else 'disabled'}")
        return False
    if cmd == "tokens":
        used, limit = agent.token_usage()
        ui.token_usage(used=used, limit=limit)
        return False

    ui.warn(f"unknown command: /{cmd} (try /help)")
    return False


if __name__ == "__main__":
    sys.exit(main())
