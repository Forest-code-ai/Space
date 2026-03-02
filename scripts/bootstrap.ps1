$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
Set-Location $projectRoot

if (-not (Test-Path .\.venv)) {
  py -3 -m venv .venv
}

& .\.venv\Scripts\python.exe -m pip install --upgrade pip

if (Test-Path .\requirements.txt) {
  & .\.venv\Scripts\python.exe -m pip install -r requirements.txt
}

Write-Host "Bootstrap complete. Next:"
Write-Host "  . ./.venv/Scripts/Activate.ps1"
Write-Host "  python -m vibec"
