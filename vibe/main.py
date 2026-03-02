from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent
STATIC_DIR = PROJECT_ROOT / "static"
INDEX_HTML = STATIC_DIR / "index.html"

app = FastAPI()

app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/", include_in_schema=False)
def read_root() -> FileResponse:
    return FileResponse(path=str(INDEX_HTML), media_type="text/html")
