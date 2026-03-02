# vibe

Minimal starter project under `C:\Forest OS\_projects\Space\vibe`.

## What this is
A tiny, runnable Python project scaffold (no deps by default) so you can start coding immediately. If you meant a Vite/React or Next.js scaffold instead, tell me and I’ll switch templates.

## Quickstart (PowerShell)
From this folder:

```powershell
./scripts/bootstrap.ps1
. ./.venv/Scripts/Activate.ps1
python -m vibe
```

Then open `http://localhost:8000`.

## Layout
- `vibe/` — Python package (entrypoint is `python -m vibe`)
- `static/` — static site content (served at `/` and `/static/*`)
- `scripts/bootstrap.ps1` — creates `.venv` and installs deps
- `requirements.txt` — add pip dependencies here
