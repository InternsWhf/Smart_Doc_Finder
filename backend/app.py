# ---------------------------
# backend/app.py
# ---------------------------
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .config import (
    ALLOWED_ROOTS,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    USE_INDEX,
    DB_PATH,
    CORS_ALLOW_ORIGINS,
)
from .search import walk_search
from .indexer import search_index
from .mime_types import guess_type

# NEW: for shell-open endpoints
import os, platform, subprocess
from pydantic import BaseModel

APP_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = APP_DIR.parent / "frontend"

app = FastAPI(title="Smart Document Finder", version="1.0.0")

# ---- CORS ----
app.add_middleware(
    CORSMiddleware,
    allow_origins=CORS_ALLOW_ORIGINS or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# ---- Health ----
@app.get("/health")
def health():
    return {"status": "ok"}

# ---- Helpers ----
def _path_allowed(p: Path) -> bool:
    """Ensure the requested path is within one of the allowed roots."""
    try:
        rp = p.resolve(strict=False)
    except Exception:
        return False
    s = str(rp).lower()
    return any(s.startswith(root.lower()) for root in ALLOWED_ROOTS)

# ---- Search endpoint ----
@app.get("/search")
def search(
    query: str = Query(..., min_length=1, description="Filename-only, case-insensitive substring"),
    year: Optional[str] = Query(None, description="Year text appearing anywhere in the path"),
    month: Optional[str] = Query(None, description="Month text/number appearing anywhere in the path"),
    company: Optional[str] = Query(None, description="Company text appearing anywhere in the path"),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
    """
    Returns paginated matches with fields:
      - kind: "file" or "folder"
      - file_name
      - parent_folder
      - full_path

    Matching rules:
    - query MUST match the filename (for files) or the folder name (for folders)
    - year/month/company are matched against the FULL PATH (folders + filename), case-insensitive
    """
    if USE_INDEX:
        res = search_index(
            db_path=str(DB_PATH),
            query=query,
            year=year,
            month=month,
            company=company,
            page=page,
            page_size=page_size,
        )
        total = res.get("count", 0)
        total_pages = res.get("total_pages", 1)
        items = res.get("items", [])
    else:
        res = walk_search(
            query=query,
            roots=ALLOWED_ROOTS,
            year=year,
            month=month,
            company=company,
            page=page,
            page_size=page_size,
        )
        total = res.get("count", 0)
        total_pages = res.get("total_pages", 1)
        items = res.get("items", [])

    return JSONResponse({
        "count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": total_pages,
        "items": items,
    })

# ---- Preview endpoint ----
@app.get("/preview")
def preview(path: str):
    """
    Streams the file to the browser. PDFs/images render inline. Office docs typically download/open via local apps.
    """
    p = Path(path)
    if not _path_allowed(p):
        raise HTTPException(status_code=403, detail="Path not allowed")
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found")

    media_type = guess_type(str(p))
    headers = {"Content-Disposition": f'inline; filename="{p.name}"'}
    return FileResponse(str(p), media_type=media_type, headers=headers)

# ---- Folder browse endpoint (list immediate children) ----
@app.get("/browse")
def browse(path: str, query: Optional[str] = None):
    """
    List immediate children (files and subfolders) of a folder path.
    Optional 'query' filters children by substring match on their names (case-insensitive).
    """
    p = Path(path)
    if not _path_allowed(p):
        raise HTTPException(status_code=403, detail="Path not allowed")
    if not p.exists() or not p.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")

    ql = (query or "").lower()
    items = []
    try:
        for child in p.iterdir():
            name = child.name
            if ql and ql not in name.lower():
                continue
            items.append({
                "kind": "folder" if child.is_dir() else "file",
                "file_name": name,
                "parent_folder": p.name,
                "full_path": str(child),
            })
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied when listing folder")

    return {"count": len(items), "items": items}

# ---- Windows shell-open endpoints (avoid file:// browser block) ----
class ShellRequest(BaseModel):
    path: str

@app.post("/shell/open")
def shell_open(req: ShellRequest):
    """
    Open a FILE with its default application on Windows.
    """
    if platform.system().lower() != "windows":
        raise HTTPException(status_code=501, detail="Open supported only on Windows")
    p = Path(req.path)
    if not _path_allowed(p):
        raise HTTPException(status_code=403, detail="Path not allowed")
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        os.startfile(str(p))  # launches default app
        return {"status": "launched", "target": str(p)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open: {e}")

@app.post("/shell/open-folder")
def shell_open_folder(req: ShellRequest):
    """
    Open a FOLDER in Explorer (or select a file in its folder).
    """
    if platform.system().lower() != "windows":
        raise HTTPException(status_code=501, detail="Open supported only on Windows")
    p = Path(req.path)
    if not _path_allowed(p):
        raise HTTPException(status_code=403, detail="Path not allowed")

    target = p if p.is_dir() else p.parent
    if not target.exists():
        raise HTTPException(status_code=404, detail="Folder not found")

    try:
        # If it's a file, select it; if it's a folder, open it.
        if p.is_file():
            subprocess.run(["explorer", f"/select,{str(p)}"], check=False)
        else:
            subprocess.run(["explorer", str(target)], check=False)
        return {"status": "launched", "target": str(target)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open folder: {e}")

# ---- Serve the static frontend at ROOT (/) ----
# IMPORTANT: mount AFTER defining API routes so exact paths (/search, /preview, /browse, /shell/*, /health) win.
if FRONTEND_DIR.exists():
    print("Serving frontend from:", FRONTEND_DIR.resolve())
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")
