# locoder

A local-first CLI coding agent that talks to [Ollama](https://ollama.com) (or any
OpenAI-compatible server: llama.cpp, vLLM, LM Studio) as its LLM backend.

## Install

```bash
pip install -e .
```

## Quick start

```bash
ollama pull qwen3.5:4b              # default model; any tool-calling capable model works
locoder                            # starts an interactive session in the current directory
locoder --model llama3.1           # override the model for this run
locoder --yolo                     # skip confirmation prompts for writes/edits/commands
```

The first run writes a starter config to `.locoder/config.yaml` in the current
project. Edit it to change the default provider, model, base URL, temperature,
or context window size.

## Groq (hosted)

Set your key once (from https://console.groq.com/keys), then pick the `groq`
provider. The base URL is filled in for you.

```bash
setx GROQ_API_KEY "gsk_..."                              # Windows; new terminals pick it up
locoder --provider groq --model qwen/qwen3.8-27b
```

This sends your prompts and the code locoder reads to Groq's servers. Rate
limits (HTTP 429) are retried automatically for waits of up to 30 seconds.

`yolo` and `allowed_paths` are only read from the global `~/.locoder/config.yaml`
(or `--yolo`); a project's config can't set them, since that file may come with
a cloned repo.

## Slash commands

- `/help` - list slash commands
- `/clear` - clear the conversation and start a new session
- `/model <name>` - switch the active model
- `/yolo` - toggle confirmation prompts on/off
- `/tokens` - show approximate context/token usage
- `/exit`, `/quit` - leave locoder

## Project layout

- `locoder/cli.py` - entry point and REPL
- `locoder/agent.py` - the core tool-calling loop
- `locoder/providers/` - model backends (Ollama, Groq, OpenAI-compatible)
- `locoder/tools/` - file, search, and shell command tools
- `locoder/config.py` - config file loading
- `locoder/ui.py` - rich-based terminal rendering
- `locoder/context.py` - token accounting and history compaction
- `locoder/session.py` - per-project `.locoder/` session persistence
- `locoder/sandbox.py` - path containment for file tools
