# One-shot installer for Windows PowerShell
#
#   powershell -ExecutionPolicy Bypass -File scripts\install.ps1
#
# Uses pipx when available (global `novel` command); otherwise creates a local .venv.
#
# IMPORTANT: keep this file ASCII-only. PowerShell 5.1 reads BOM-less UTF-8 as ANSI;
# non-ASCII bytes can swallow the following newline and break the script silently.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

# Some Windows setups have a TEMP dir Python cannot use; pip then hangs for minutes.
$tmp = Join-Path $root ".tmp"
New-Item -ItemType Directory -Force -Path $tmp | Out-Null
$env:TEMP = $tmp
$env:TMP = $tmp

$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) {
    Write-Host "ERROR: python not found. Install Python 3.10+ from https://www.python.org/downloads/" -ForegroundColor Red
    exit 1
}

$ok = & python -c "import sys; print(1 if sys.version_info >= (3, 10) else 0)"
if ($ok -ne "1") {
    $version = & python -c "import sys; print('%d.%d' % sys.version_info[:2])"
    Write-Host "ERROR: Python $version found, but 3.10+ is required." -ForegroundColor Red
    exit 1
}
Write-Host "Python OK"

if (Get-Command pipx -ErrorAction SilentlyContinue) {
    Write-Host "pipx detected - installing as a global command ..."
    pipx install . --force
    Write-Host ""
    Write-Host "Done. Verify with: novel --help" -ForegroundColor Green
} else {
    Write-Host "pipx not found - creating a local .venv ..."
    if (-not (Test-Path ".venv")) { python -m venv .venv }
    & .\.venv\Scripts\python.exe -m pip install --quiet --upgrade pip
    & .\.venv\Scripts\python.exe -m pip install --quiet .
    Write-Host ""
    Write-Host "Done. Verify with: .\.venv\Scripts\novel.exe --help" -ForegroundColor Green
    Write-Host "For a global command later: python -m pip install --user pipx"
}
