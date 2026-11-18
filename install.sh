#!/usr/bin/env sh
# Install locoder:  curl -fsSL https://raw.githubusercontent.com/lhuizach/Locoder/master/install.sh | sh
# Works on Linux, macOS and Windows (Git Bash). Re-run it to update.
set -eu

SRC="${LOCODER_SRC:-git+https://github.com/lhuizach/Locoder.git}"

say() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

# uv gives us an isolated install and fetches a stable Python (3.12) if the
# system one is missing or too new for prebuilt dependency wheels.
if ! command -v uv >/dev/null 2>&1; then
  say "Installing uv"
  if command -v curl >/dev/null 2>&1; then
    curl -LsSf https://astral.sh/uv/install.sh | sh || die "could not install uv"
  else
    die "curl is required"
  fi
  PATH="$HOME/.local/bin:$HOME/.cargo/bin:$PATH"; export PATH
  command -v uv >/dev/null 2>&1 || die "uv installed but not on PATH; open a new terminal and re-run"
fi

say "Installing locoder"
uv tool install --force --python 3.12 "$SRC"
uv tool update-shell >/dev/null 2>&1 || true

say "Done. Open a new terminal and run:  locoder"
