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

## From your phone

```
locoder serve
```

runs locoder in the current folder behind a small server on your local
network and prints a pairing QR code. Then either:

- scan it with your phone's camera: it opens locoder in the browser,
  already paired; or
- in the Android app (`mobile/android`, `gradlew assembleDebug`) tap
  **scan QR code** - or pick your PC from the ones it finds on the Wi-Fi and
  type the 6-digit code shown next to the QR.

The code works once; the phone gets its own key. If the QR points at the
wrong network (VPN or VM adapters), pass `--address <your-pc-ip>`. Chats, approvals, mode,
model and project switching all work from the phone, and the chats are the
same `.locoder/sessions` the CLI uses. The Android app notifies you when a
task needs your OK (with Approve / Decline right on the notification) or
finishes. Nothing leaves your network; for use away from home put both
devices on Tailscale. `locoder serve --unpair-all` forgets every phone.

On Windows, allow Python on private networks when the firewall asks.

**Files to your phone:** ask the model to "send me todo.html" and it shows a
file card in the chat with **save** (into the phone's Downloads folder) and,
for HTML files, **open**, which tries the page right in the app (in a sandbox
with no network, and without access to the app itself). Files up to 8 MB;
secrets (`.env`, keys) and anything in `.git`/`.locoder` are never sent.

## Safety

locoder asks before it writes a file or runs a command. Yolo mode (`--yolo`,
`/yolo`, or the switch in the phone app) skips those questions for everyday
actions only: anything risky still asks, with the reasons shown, because
small models don't reliably recognise a dangerous action. Risky means
(see `locoder/safety.py`):

- files: changing a file over 1,000 lines, writing a new one that big,
  removing or rewriting most of a file, `.env`/key files, `.git/`,
  `.locoder/`, anything outside the project folder
- commands: deleting folders recursively, by wildcard, or more than three
  files at once; throwing away git work or history, `git push`; touching
  paths outside the project (absolute paths, `..`, your home folder);
  installing packages or programs outside the project; registry, services,
  environment variables, permissions, admin rights, stopping programs,
  shutting down; downloading and running code; connecting to other machines
- running a script locoder itself wrote that deletes files or runs commands

These are heuristics: they catch the common ways things go wrong, not every
possible one.

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
- `locoder/serve/` - `locoder serve`: pairing, the HTTP API + event stream, and the phone web app (`web/`)
- `mobile/android/` - the Android app (a WebView around `locoder/serve/web` plus notifications)
