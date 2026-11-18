# Install locoder on Windows:
#   irm https://raw.githubusercontent.com/lhuizach/Locoder/master/install.ps1 | iex
$ErrorActionPreference = 'Stop'
$src = if ($env:LOCODER_SRC) { $env:LOCODER_SRC } else { 'git+https://github.com/lhuizach/Locoder.git' }

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
  Write-Host '==> Installing uv'
  irm https://astral.sh/uv/install.ps1 | iex
  $env:Path = "$HOME\.local\bin;$env:Path"
}
Write-Host '==> Installing locoder'
uv tool install --force --python 3.12 $src
uv tool update-shell | Out-Null
Write-Host 'Done. Open a new terminal and run: locoder'
