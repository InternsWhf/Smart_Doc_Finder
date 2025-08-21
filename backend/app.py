# ---------------------------
# backend/app.py
# ---------------------------
import os
import platform
import subprocess
import urllib.parse
import tempfile
import shutil
from pathlib import Path
from typing import Optional, List, Dict, Iterator
from datetime import datetime

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

from .config import (
    ALLOWED_ROOTS,
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    USE_INDEX,
    DB_PATH,
    CORS_ALLOW_ORIGINS,
    PARENT_ORDER,
)
from .search import walk_search, coverage_rows
from .search import monthly_coverage  # <-- ADD: import for monthly data
from .indexer import search_index  # optional
from .mime_types import guess_type

APP_DIR = Path(__file__).resolve().parent
FRONTEND_DIR = APP_DIR.parent / "frontend"

app = FastAPI(title="Smart Document Finder", version="2.1.2")

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
    """
    Ensure the requested path is within one of the allowed roots.

    Uses normcase + abspath + commonpath so it works across:
      - different path casing
      - forward/back slashes
      - drive-letter paths vs UNC paths (best-effort)
    """
    try:
        rp = os.path.normcase(os.path.abspath(str(p)))
    except Exception:
        return False

    for root in ALLOWED_ROOTS:
        try:
            base = os.path.normcase(os.path.abspath(root))
            if os.path.commonpath([rp, base]) == base:
                return True
        except Exception:
            # different drives / malformed inputs can raise ValueError
            continue
    return False


def _dedup_key(path_str: str) -> str:
    """
    Build a robust identity key for a path.
    Prefer (st_dev, st_ino) when available; fall back to normalized absolute path.
    This prevents duplicates when the same file is reached via different notations
    (e.g., drive letter vs UNC, or different casing).
    """
    p = Path(path_str)
    try:
        st = p.stat()
        return f"{st.st_dev}:{st.st_ino}"
    except Exception:
        try:
            return os.path.normcase(os.path.abspath(str(p)))
        except Exception:
            return str(p).lower()


def _fmt_size(n: Optional[int]) -> Optional[str]:
    if n is None:
        return None
    try:
        n = int(n)
    except Exception:
        return None
    units = ["B", "KB", "MB", "GB", "TB"]
    i = 0
    f = float(n)
    while f >= 1024 and i < len(units) - 1:
        f /= 1024.0
        i += 1
    return f"{f:.1f} {units[i]}"


def _fmt_mtime(ts: Optional[float]) -> Optional[str]:
    try:
        return datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return None


def _has_children(dir_path: Path) -> bool:
    try:
        with os.scandir(dir_path) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        return True
                except PermissionError:
                    continue
    except PermissionError:
        return False
    return False


# =========================================================
#  A) BASELINE SEARCH
# =========================================================
@app.get("/search")
def search(
    query: str = Query(..., min_length=1, description="Filename-only, case-insensitive substring"),
    year: Optional[str] = Query(None),
    month: Optional[str] = Query(None),
    company: Optional[str] = Query(None),
    page: int = Query(1, ge=1),
    page_size: int = Query(DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
):
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


# =========================================================
#  B) COVERAGE ROWS (single table: exactly 7 parents)
# =========================================================
@app.get("/coverage-rows")
def coverage_rows_endpoint(
    query: str = Query(..., min_length=1),
    year: Optional[str] = Query(None),
    month: Optional[str] = Query(None),
    company: Optional[str] = Query(None),
):
    res = coverage_rows(
        parents_order=PARENT_ORDER,
        roots=ALLOWED_ROOTS,
        query=query,
        year=year,
        month=month,
        company=company,
    )
    return JSONResponse(res)


@app.get("/coverage-files")
def coverage_files_endpoint(
    parent: str = Query(..., description="One of the fixed parent names (e.g., 'CIPL')"),
    query: str = Query(..., min_length=1),
    year: Optional[str] = Query(None),
    month: Optional[str] = Query(None),
    company: Optional[str] = Query(None),
):
    """
    For a given parent (e.g., 'PDIR REPORT'), return *files* for the child table:
      - If a search hit is a FILE, include it directly.
      - If a search hit is a FOLDER (e.g., 'EXP-192'), enumerate files inside it
        so Preview works. (Expands up to max_depth and returns files.)
    """
    res = walk_search(
        query=query or "",
        roots=ALLOWED_ROOTS,
        year=year,
        month=month,
        company=company,
        page=1,
        page_size=10_000,
    )

    wanted = (parent or "").strip().lower()
    items_out: List[Dict] = []

    for item in res.get("items", []):
        kind = (item.get("kind") or "file").lower()
        full_path = Path(item.get("full_path", ""))

        parent_name = full_path.parent.name.lower()

        # Only keep entries belonging to the requested parent
        if parent_name != wanted:
            if wanted not in [p.lower() for p in full_path.parts]:
                continue

        if kind == "file":
            items_out.append({
                "kind": "file",
                "file_name": item.get("file_name") or full_path.name,
                "parent_folder": full_path.parent.name,
                "full_path": str(full_path),
            })
        else:
            if not _path_allowed(full_path):
                continue
            items_out.extend(_list_files_in_folder(full_path, max_depth=2, limit=500))

    # De-duplicate by robust file identity (device+inode) or normalized path
    seen = set()
    deduped = []
    for it in items_out:
        key = _dedup_key(it["full_path"])
        if key in seen:
            continue
        seen.add(key)
        deduped.append(it)

    return {"items": deduped}


# =========================================================
#  B2) MONTHLY COVERAGE (Available vs Missing by parent)  <-- ADD: new endpoint
# =========================================================
@app.get("/monthly-coverage")
def monthly_coverage_endpoint(
    year: str = Query(..., description="Year (e.g., 2025)"),
    month: str = Query(..., description="Full month name (e.g., August)"),
    company: Optional[str] = Query(None),
):
    """
    Returns grouped results for a selected Year+Month:
      {
        "period": {"year":"2025","month":"august"},
        "available": [ { "parent":"CIPL","count":N,"items":[...] }, ... ],
        "missing":   [ "DOCK AUDIT REPORT", "BL", ... ]
      }
    """
    res = monthly_coverage(
        parents_order=PARENT_ORDER,
        roots=ALLOWED_ROOTS,
        year=year,
        month=month,
        company=company,
        max_items_per_parent=5000,
    )
    return JSONResponse(res)


# =========================================================
#  C) PREVIEW / BASIC BROWSE
# =========================================================
@app.get("/preview")
def preview(path: str):
    p = Path(path)
    if not _path_allowed(p):
        raise HTTPException(status_code=403, detail="Path not allowed")
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found")

    media_type = guess_type(str(p))
    headers = {"Content-Disposition": f'inline; filename="{p.name}"'}
    return FileResponse(str(p), media_type=media_type, headers=headers)


@app.get("/browse")
def browse(path: str, query: Optional[str] = None):
    """
    JSON API for programmatic listing (immediate children).
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


# =========================================================
#  D) RICH BROWSER ENDPOINTS (tree + details)
# =========================================================
@app.get("/browse-children")
def browse_children(path: str = Query(...)):
    """
    Return immediate *folders* of a path, with has_children flag for each.
    """
    base = Path(path)
    if not _path_allowed(base):
        raise HTTPException(status_code=403, detail="Path not allowed")
    if not base.exists() or not base.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")

    out = []
    try:
        with os.scandir(base) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        fp = Path(entry.path)
                        out.append({
                            "name": entry.name,
                            "full_path": str(fp),
                            "has_children": _has_children(fp),
                        })
                except PermissionError:
                    continue
    except PermissionError:
        raise HTTPException(status_code=403, detail="Permission denied when listing folder")

    out.sort(key=lambda r: r["name"].lower())
    return {"items": out}


def _iter_items(root: Path, include_subfolders: bool) -> Iterator[Path]:
    if not include_subfolders:
        try:
            with os.scandir(root) as it:
                for entry in it:
                    yield Path(entry.path)
        except PermissionError:
            return
        return

    # deep
    for dirpath, dirnames, filenames in os.walk(root):
        pdir = Path(dirpath)
        for d in dirnames:
            yield pdir / d
        for f in filenames:
            yield pdir / f


@app.get("/browse-list")
def browse_list(
    path: str = Query(...),
    q: Optional[str] = Query(None),
    include_subfolders: bool = Query(False),
    offset: int = Query(0, ge=0),
    limit: int = Query(1000, ge=1, le=5000),
):
    """
    Return folders+files for the details pane (optionally recursive), with soft pagination.
    """
    base = Path(path)
    if not _path_allowed(base):
        raise HTTPException(status_code=403, detail="Path not allowed")
    if not base.exists() or not base.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")

    ql = (q or "").lower()
    rows: List[Dict] = []

    # Collect up to offset+limit+1 to determine has_more
    max_collect = offset + limit + 1
    count_collected = 0

    def push(p: Path):
        nonlocal count_collected
        name = p.name
        if ql and ql not in name.lower():
            return
        count_collected += 1
        if count_collected <= offset:
            return
        if len(rows) >= limit + 1:
            return

        try:
            st = p.stat()
        except PermissionError:
            st = None

        rows.append({
            "name": name,
            "full_path": str(p),
            "kind": "folder" if p.is_dir() else "file",
            "size": _fmt_size(st.st_size) if st and p.is_file() else None,
            "modified": _fmt_mtime(st.st_mtime) if st else None,
        })

    collected = 0
    for child in _iter_items(base, include_subfolders):
        push(child)
        collected += 1
        if collected >= max_collect:
            break

    # Sort: folders first, then name
    rows.sort(key=lambda r: (r["kind"] != "folder", (r["name"] or "").lower()))

    has_more = len(rows) > limit
    if has_more:
        rows = rows[:limit]
    next_offset = (offset + limit) if has_more else None

    return {"items": rows, "has_more": has_more, "next_offset": next_offset}


# ---- Server-rendered folder UI (two-pane, with sidebar toggle) ----
@app.get("/browse-ui", response_class=HTMLResponse)
def browse_ui(path: str = Query(..., description="Absolute path under one of ALLOWED_ROOTS")):
    """
    Two-pane browser (tree + details). Uses /browse-children and /browse-list.
    Raw HTML string (not an f-string); we inject values via .replace() to avoid
    collisions with JS template literals.
    """
    p = Path(path)
    if not _path_allowed(p):
        raise HTTPException(status_code=403, detail="Path not allowed")
    if not p.exists():
        raise HTTPException(status_code=404, detail="Not found")
    if p.is_file():
        media_type = guess_type(str(p))
        headers = {"Content-Disposition": f'inline; filename="{p.name}"'}
        return FileResponse(str(p), media_type=media_type, headers=headers)

    allowed_roots_encoded = urllib.parse.quote("|".join(ALLOWED_ROOTS))
    initial_path_encoded = urllib.parse.quote(str(p))

    # RAW HTML (not an f-string) — values injected via .replace below
    html = r"""<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <title>Browse</title>
  <meta name="viewport" content="width=device-width,initial-scale=1" />
  <style>
    :root {
      --bg:#0b1020; --bg-2:#0e1630; --card:#111a33; --border:#1e2a44;
      --text:#e8eef7; --muted:#9fb3cc; --accent:#22d3ee; --accent-strong:#06b6d4;
      --success:#10b981; --danger:#ef4444;
    }
    *{box-sizing:border-box} html,body{height:100%}
    body{
      margin:0; background:linear-gradient(180deg,var(--bg),var(--bg-2)); color:var(--text);
      font-family:ui-sans-serif,-apple-system,Segoe UI,Roboto,Helvetica,Arial;
    }

    /* Layout with collapsible sidebar */
    .wrap{display:grid; grid-template-columns:0 1fr; height:100vh; transition:grid-template-columns .2s ease}
    .wrap.sidebar-open{grid-template-columns:320px 1fr}
    .sidebar{border-right:1px solid var(--border); background:rgba(15,23,42,.5); padding:12px; overflow:auto; transition:padding .2s ease, border-color .2s ease, opacity .2s ease}
    .wrap:not(.sidebar-open) .sidebar{padding:0; border-color:transparent; opacity:.0; pointer-events:none}
    .main{display:flex; flex-direction:column; min-width:0}

    /* Top bar with right-aligned controls and a toggle button */
    .topbar{
      display:flex; align-items:center; justify-content:space-between;
      padding:12px 16px; border-bottom:1px solid var(--border);
      background:linear-gradient(180deg,rgba(20,28,52,.75),rgba(16,24,43,.65));
      position:sticky; top:0; z-index:20;
    }
    .crumbs a{color:#cfe2ff; text-decoration:none} .crumbs a:hover{text-decoration:underline}
    .controls{display:flex; gap:10px; align-items:center}
    .input,.checkbox{background:linear-gradient(180deg,#0c1736,#0c1530); border:1px solid #203258; color:var(--text); border-radius:10px; padding:8px 10px}
    .input:focus{outline:none; border-color:var(--accent-strong); box-shadow:0 0 0 3px rgba(34,211,238,.2)}
    .btn{padding:8px 10px; border-radius:10px; border:1px solid #203258; background:#0f1f3d; color:var(--text); cursor:pointer}
    .btn:hover{background:#162a52}

    /* Small icon button for sidebar toggle (top-right) */
    .iconbtn{width:36px; height:36px; display:inline-grid; place-items:center; border-radius:10px; border:1px solid #203258; background:rgba(255,255,255,.06); cursor:pointer}
    .iconbtn:hover{background:rgba(255,255,255,.10)}
    .iconbtn svg{width:22px; height:22px}

    .tree ul{list-style:none; margin:0; padding-left:16px} .tree li{margin:3px 0}
    .node{display:flex; align-items:center; gap:6px; cursor:pointer}
    .twisty{width:12px; height:12px; display:inline-block; border-left:6px solid #8fb3ff; border-top:6px solid transparent; border-bottom:6px solid transparent; transform:rotate(0deg)}
    .node.expanded .twisty{transform:rotate(90deg)}
    .node .name{color:#d7e7ff; text-decoration:none} .node .name.active{color:#a5f3fc; font-weight:700}
    .icon{width:16px; height:16px; vertical-align:-2px}
    .content{padding:10px 16px; overflow:auto}
    table{width:100%; border-collapse:separate; border-spacing:0; border:1px solid var(--border); border-radius:12px; overflow:hidden}
    thead th{text-align:left; padding:10px; background:linear-gradient(180deg,#0f1c3e,#0c1733); color:#d7e7ff; border-bottom:1px solid var(--border)}
    tbody td{padding:10px; border-bottom:1px solid var(--border)}
    tbody tr:nth-child(odd) td{background:#0b1530} tbody tr:nth-child(even) td{background:#0d1a3c}
    tbody tr:hover td{background:#12224a}
    .muted{color:var(--muted)} .empty{color:#cbd5e1; padding:14px; border:1px dashed var(--border); border-radius:12px; background:linear-gradient(180deg,#0e1a38,#0c1633)}
    .openlink{color:var(--accent); text-decoration:none} .openlink:hover{text-decoration:underline}
    .rowicon{width:18px; height:18px; margin-right:6px; vertical-align:-3px}
    .grid-row{display:flex; gap:12px; align-items:center} .nowrap{white-space:nowrap}
  </style>
</head>
<body>
  <div class="wrap">
    <aside class="sidebar">
      <div class="tree" id="tree"></div>
    </aside>
    <main class="main">
      <div class="topbar">
        <div class="crumbs" id="crumbs">Loading…</div>
        <div class="controls">
          <button id="btnSidebar" class="iconbtn" title="Open sidebar" aria-label="Open sidebar">
            <!-- panel icon -->
            <svg viewBox="0 0 24 24" fill="#cfe2ff">
              <rect x="3" y="4" width="18" height="16" rx="2" ry="2" fill="none" stroke="#8fb3ff" stroke-width="2"></rect>
              <rect x="3" y="4" width="6" height="16" rx="2" ry="2" fill="#8fb3ff" opacity=".35"></rect>
            </svg>
          </button>
          <input class="input" id="search" placeholder="Search in this folder…" />
          <label class="grid-row">
            <input type="checkbox" id="deep" class="checkbox" />
            <span class="muted">Include subfolders</span>
          </label>
        </div>
      </div>
      <div class="content">
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th class="nowrap">Type</th>
              <th class="nowrap">Size</th>
              <th class="nowrap">Modified</th>
              <th class="nowrap">Open</th>
            </tr>
          </thead>
          <tbody id="rows">
            <tr><td colspan="5" class="muted">Loading…</td></tr>
          </tbody>
        </table>
        <div id="moreWrap" style="padding:10px 0;display:none;">
          <button class="btn" id="btnMore">Load more</button>
          <span class="muted" id="moreInfo"></span>
        </div>
      </div>
    </main>
  </div>

  <script>
  const API_ROOTS = decodeURIComponent("__ALLOWED_ROOTS__").split("|").filter(Boolean);
  const INITIAL_PATH = decodeURIComponent("__INITIAL_PATH__");
  const $ = sel => document.querySelector(sel);
  const rowsEl = $("#rows");
  const treeEl = $("#tree");
  const crumbsEl = $("#crumbs");
  const searchEl = $("#search");
  const deepEl = $("#deep");
  const moreWrap = $("#moreWrap");
  const btnMore = $("#btnMore");
  const moreInfo = $("#moreInfo");
  const btnSidebar = $("#btnSidebar");
  const wrapEl = document.querySelector(".wrap");

  let state = { currentPath: INITIAL_PATH, q: "", deep: false, nextOffset: 0, loading: false };

  // sidebar toggle (hidden by default, persisted)
  function setSidebar(open){
    if(open){ wrapEl.classList.add("sidebar-open"); btnSidebar.title = "Close sidebar"; btnSidebar.setAttribute("aria-label","Close sidebar"); }
    else{ wrapEl.classList.remove("sidebar-open"); btnSidebar.title = "Open sidebar"; btnSidebar.setAttribute("aria-label","Open sidebar"); }
    try{ localStorage.setItem("sidebarOpen", open ? "1":"0"); }catch(e){}
  }
  btnSidebar.addEventListener("click", () => {
    const open = !wrapEl.classList.contains("sidebar-open");
    setSidebar(open);
  });
  // initialize collapsed unless previously opened
  let savedOpen = null;
  try{ savedOpen = localStorage.getItem("sidebarOpen"); }catch(e){}
  setSidebar(savedOpen === "1");

  const icons = {
    folder: '<svg class="icon" viewBox="0 0 24 24" fill="#fbbf24"><path d="M10 4l2 2h6a2 2 0 012 2v1H4V6a2 2 0 012-2h4z"></path><path d="M4 9h16v7a2 2 0 01-2 2H6a2 2 0 01-2-2V9z" fill="#f59e0b"></path></svg>',
    pdf:    '<svg class="icon" viewBox="0 0 24 24" fill="#ef4444"><path d="M6 2h9l5 5v15a2 2 0 01-2 2H6a2 2 0 01-2-2V4a2 2 0 012-2z"></path><text x="7" y="17" fill="#fff" font-size="8" font-weight="700">PDF</text></svg>',
    img:    '<svg class="icon" viewBox="0 0 24 24" fill="#10b981"><path d="M4 4h16v16H4z"></path><path d="M7 14l3-3 3 3 3-4 3 5v2H4z" fill="#34d399"></path></svg>',
    xls:    '<svg class="icon" viewBox="0 0 24 24" fill="#22c55e"><path d="M6 2h9l5 5v15a2 2 0 01-2 2H6a2 2 0 01-2-2V4a2 2 0 012-2z"></path><text x="7" y="17" fill="#fff" font-size="8" font-weight="700">XLS</text></svg>',
    doc:    '<svg class="icon" viewBox="0 0 24 24" fill="#3b82f6"><path d="M6 2h9l5 5v15a2 2 0 01-2 2H6a2 2 0 01-2-2V4a2 2 0 012-2z"></path><text x="7" y="17" fill="#fff" font-size="8" font-weight="700">DOC</text></svg>',
    ppt:    '<svg class="icon" viewBox="0 0 24 24" fill="#f97316"><path d="M6 2h9l5 5v15a2 2 0 01-2 2H6a2 2 0 01-2-2V4a2 2 0 012-2z"></path><text x="7" y="17" fill="#fff" font-size="8" font-weight="700">PPT</text></svg>',
    zip:    '<svg class="icon" viewBox="0 0 24 24" fill="#a78bfa"><path d="M6 2h12a2 2 0 012 2v16a2 2 0 01-2 2H6a2 2 0 01-2-2V4a2 2 0 012-2z"></path><path d="M10 4h4v4h-4zM10 8h4v4h-4z" fill="#c4b5fd"></path></svg>',
    txt:    '<svg class="icon" viewBox="0 0 24 24" fill="#94a3b8"><path d="M6 2h12a2 2 0 012 2v16a2 2 0 01-2 2H6a2 2 0 01-2-2V4a2 2 0 012-2z"></path></svg>',
    file:   '<svg class="icon" viewBox="0 0 24 24" fill="#9ca3af"><path d="M6 2h9l5 5v15a2 2 0 01-2 2H6a2 2 0 01-2-2V4a2 2 0 012-2z"></path></svg>',
    extlink:'<svg class="rowicon" viewBox="0 0 24 24" fill="#60a5fa"><path d="M14 3h7v7h-2V6.41l-9.29 9.3-1.42-1.42L17.59 5H14V3z"/><path d="M5 5h5V3H5a2 2 0 00-2 2v14c0 1.1.9 2 2 2h14a2 2 0 002-2v-5h-2v5H5V5z"/></svg>'
  };

  function fileTypeIcon(name) {
    const n = name.toLowerCase();
    if (n.endsWith(".pdf")) return icons.pdf;
    if (/(\.png|\.jpg|\.jpeg|\.gif|\.webp|\.bmp|\.tif|\.tiff)$/.test(n)) return icons.img;
    if (/(\.xls|\.xlsx|\.csv)$/.test(n)) return icons.xls;
    if (/(\.doc|\.docx)$/.test(n)) return icons.doc;
    if (/(\.ppt|\.pptx)$/.test(n)) return icons.ppt;
    if (/(\.zip|\.rar|\.7z|\.tar|\.gz)$/.test(n)) return icons.zip;
    if (/(\.txt|\.log|\.md)$/.test(n)) return icons.txt;
    return icons.file;
  }

  function nodeTemplate(name, fullPath, expanded=false, isActive=false, hasTwisty=false) {
    return `
      <div class="node ${expanded ? "expanded" : ""}" data-path="${escapeHtml(fullPath)}">
        ${hasTwisty ? '<span class="twisty"></span>' : '<span style="display:inline-block;width:12px;"></span>'}
        ${icons.folder}
        <a class="name ${isActive ? 'active' : ''}" href="#">${escapeHtml(name)}</a>
      </div>
      <ul class="kids" style="display:${expanded ? 'block' : 'none'}"></ul>
    `;
  }

  function renderTreeRoots() {
    treeEl.innerHTML = "";
    API_ROOTS.forEach(root => {
      const wrap = document.createElement("div");
      wrap.className = "tree-root";
      wrap.innerHTML = nodeTemplate(root.split(/[\\\/]/).pop() || root, root, false, false, true);
      treeEl.appendChild(wrap);
    });
    expandToPath(state.currentPath);
  }

  async function expand(nodeEl) {
    const kids = nodeEl.nextElementSibling;
    if (kids.getAttribute("data-loaded") === "1") {
      const open = kids.style.display !== "none";
      kids.style.display = open ? "none" : "block";
      nodeEl.classList.toggle("expanded", !open);
      return;
    }
    const path = nodeEl.getAttribute("data-path");
    try {
      const res = await fetch(`/browse-children?` + new URLSearchParams({ path }));
      if (!res.ok) throw new Error("children " + res.status);
      const data = await res.json();
      kids.innerHTML = "";
      (data.items || []).forEach(item => {
        const li = document.createElement("li");
        li.innerHTML = nodeTemplate(item.name, item.full_path, false, false, item.has_children);
        kids.appendChild(li);
      });
      kids.setAttribute("data-loaded", "1");
      kids.style.display = "block";
      nodeEl.classList.add("expanded");
    } catch(e) { alert("Could not load subfolders."); }
  }

  async function expandToPath(targetPath) {
    const roots = [...treeEl.querySelectorAll(".tree-root > .node")];
    let rootNode = roots.find(n => targetPath.toLowerCase().startsWith(n.getAttribute("data-path").toLowerCase()));
    if (!rootNode) rootNode = roots[0];
    await expand(rootNode);

    const base = rootNode.getAttribute("data-path");
    const segs = targetPath.substring(base.length).replace(/^[/\\]/, "").split(/[\\\/]/).filter(Boolean);
    let cur = rootNode;
    let curPath = base;
    for (const seg of segs) {
      curPath += (curPath.endsWith("/") || curPath.endsWith("\\") ? "" : "\\") + seg;
      const kids = cur.nextElementSibling;
      let nextNode = [...kids.querySelectorAll(".node")].find(n => n.getAttribute("data-path").toLowerCase() === curPath.toLowerCase());
      if (!nextNode) {
        await expand(cur);
        nextNode = [...kids.querySelectorAll(".node")].find(n => n.getAttribute("data-path").toLowerCase() === curPath.toLowerCase());
      }
      if (!nextNode) break;
      await expand(nextNode);
      cur = nextNode;
    }
    treeEl.querySelectorAll(".name.active").forEach(a => a.classList.remove("active"));
    const active = [...treeEl.querySelectorAll(".node")].find(n => n.getAttribute("data-path").toLowerCase() === targetPath.toLowerCase());
    if (active) active.querySelector(".name").classList.add("active");
  }

  async function loadList(reset=true) {
    if (state.loading) return;
    state.loading = true;
    if (reset) { rowsEl.innerHTML = `<tr><td colspan="5" class="muted">Loading…</td></tr>`; state.nextOffset = 0; }

    try {
      const params = new URLSearchParams({
        path: state.currentPath,
        include_subfolders: String(state.deep),
        offset: String(state.nextOffset || 0),
        limit: "1000",
      });
      if (state.q) params.set("q", state.q);

      const res = await fetch(`/browse-list?` + params.toString());
      if (!res.ok) throw new Error("list " + res.status);
      const data = await res.json();
      const items = data.items || [];
      if (reset) rowsEl.innerHTML = "";
      if (!items.length && (state.nextOffset || 0) === 0) {
        rowsEl.innerHTML = `<tr><td colspan="5"><div class="empty">Empty folder</div></td></tr>`;
      } else {
        items.forEach(addRow);
      }

      state.nextOffset = data.has_more ? (data.next_offset || 0) : null;
      moreWrap.style.display = data.has_more ? "block" : "none";
      moreInfo.textContent = data.has_more ? "Showing first 1000 items…" : "";
    } catch(e) {
      rowsEl.innerHTML = `<tr><td colspan="5" class="muted">Error loading.</td></tr>`;
    } finally { state.loading = false; }
  }

  function addRow(it) {
    const isFolder = it.kind === "folder";
    const icon = isFolder ? `${icons.folder}` : fileTypeIcon(it.name);
    const type = isFolder ? "Folder" : (it.name.split(".").pop().toUpperCase());
    const openCell = isFolder
      ? `<a class="openlink" href="/browse-ui?path=${encodeURIComponent(it.full_path)}">Open</a>`
      : `<a class="openlink" href="/preview?path=${encodeURIComponent(it.full_path)}">Open</a> <a class="openlink" href="/preview?path=${encodeURIComponent(it.full_path)}" target="_blank" title="Open in new tab">${icons.extlink}</a>`;

    const tr = document.createElement("tr");
    tr.innerHTML = `
      <td class="grid-row"><span>${icon}</span><span>${escapeHtml(it.name)}</span></td>
      <td class="nowrap">${type || "—"}</td>
      <td class="nowrap">${it.size || "—"}</td>
      <td class="nowrap">${it.modified || "—"}</td>
      <td class="nowrap">${openCell}</td>
    `;
    rowsEl.appendChild(tr);
  }

  function renderCrumbs(path) {
    const parts = path.split(/[/\\]+/).filter(Boolean);
    let acc = (path.startsWith("\\\\") ? "\\\\" + parts.shift() + "\\\\" + parts.shift() : (parts.shift() || ""));
    let html = "";
    let baseBuilt = acc;
    if (path.startsWith("\\\\") && acc) {
      html += `<a href="/browse-ui?path=${encodeURIComponent("\\\\" + baseBuilt)}">${escapeHtml("\\\\" + baseBuilt)}</a>`;
    } else {
      html += `<a href="/browse-ui?path=${encodeURIComponent(acc + (acc.includes(":") ? "\\\\" : ""))}">${escapeHtml(acc)}</a>`;
    }
    parts.forEach(part => {
      baseBuilt = baseBuilt + (baseBuilt.endsWith("\\\\") ? "" : "\\\\") + part;
      html += " / " + `<a href="/browse-ui?path=${encodeURIComponent(baseBuilt)}">${escapeHtml(part)}</a>`;
    });
    crumbsEl.innerHTML = html;
  }

  function escapeHtml(s){ return String(s).replace(/[&<>"']/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[c])); }

  treeEl.addEventListener("click", async (e) => {
    const node = e.target.closest(".node");
    if (!node) return;

    if (e.target.classList.contains("twisty")) { await expand(node); return; }
    if (e.target.classList.contains("name")) {
      e.preventDefault();
      state.currentPath = node.getAttribute("data-path");
      renderCrumbs(state.currentPath);
      searchEl.value = ""; state.q = ""; deepEl.checked = false; state.deep = false;
      await loadList(true);
    }
  });

  searchEl.addEventListener("input", () => { state.q = searchEl.value.trim(); loadList(true); });
  deepEl.addEventListener("change", () => { state.deep = deepEl.checked; loadList(true); });
  btnMore.addEventListener("click", () => { if (state.nextOffset != null) loadList(false); });

  renderTreeRoots();
  renderCrumbs(state.currentPath);
  loadList(true);
  </script>
</body>
</html>
"""
    html = html.replace("__ALLOWED_ROOTS__", allowed_roots_encoded).replace("__INITIAL_PATH__", initial_path_encoded)
    return HTMLResponse(html)


# ---- Optional zip (kept; not shown in UI now) ----
@app.get("/zip-folder")
def zip_folder(path: str = Query(..., description="Absolute folder path under allowed roots")):
    folder = Path(path)
    if not _path_allowed(folder):
        raise HTTPException(status_code=403, detail="Path not allowed")
    if not folder.exists() or not folder.is_dir():
        raise HTTPException(status_code=404, detail="Folder not found")

    tmpdir = Path(tempfile.mkdtemp())
    zip_base = tmpdir / folder.name
    shutil.make_archive(str(zip_base), "zip", folder)
    return FileResponse(str(zip_base) + ".zip", filename=f"{folder.name}.zip")


# ---- Windows shell-open endpoints (compat) ----
class ShellRequest(BaseModel):
    path: str


@app.post("/shell/open")
def shell_open(req: ShellRequest):
    if platform.system().lower() != "windows":
        raise HTTPException(status_code=501, detail="Open supported only on Windows")
    p = Path(req.path)
    if not _path_allowed(p):
        raise HTTPException(status_code=403, detail="Path not allowed")
    if not p.exists():
        raise HTTPException(status_code=404, detail="File not found")
    try:
        os.startfile(str(p))
        return {"status": "launched", "target": str(p)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open: {e}")


@app.post("/shell/open-folder")
def shell_open_folder(req: ShellRequest):
    if platform.system().lower() != "windows":
        raise HTTPException(status_code=501, detail="Open supported only on Windows")
    p = Path(req.path)
    if not _path_allowed(p):
        raise HTTPException(status_code=403, detail="Path not allowed")

    target = p if p.is_dir() else p.parent
    if not target.exists():
        raise HTTPException(status_code=404, detail="Folder not found")

    try:
        if p.is_file():
            subprocess.run(["explorer", f"/select,{str(p)}"], check=False)
        else:
            subprocess.run(["explorer", str(target)], check=False)
        return {"status": "launched", "target": str(target)}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Failed to open folder: {e}")


# ---- Static frontend at ROOT (/) ----
if FRONTEND_DIR.exists():
    print("Serving frontend from:", FRONTEND_DIR.resolve())
    app.mount("/", StaticFiles(directory=FRONTEND_DIR, html=True), name="frontend")


# ---- Helper: list files inside a folder (used by /coverage-files) ----
def _list_files_in_folder(folder_path: Path, max_depth: int = 2, limit: int = 500):
    out = []
    if not folder_path.exists() or not folder_path.is_dir():
        return out

    base_parts = Path(folder_path).resolve().parts
    for dirpath, _dirnames, filenames in os.walk(folder_path):
        depth = len(Path(dirpath).resolve().parts) - len(base_parts)
        if depth > max_depth:
            continue
        for fname in filenames:
            full = Path(dirpath) / fname
            out.append({
                "kind": "file",
                "file_name": fname,
                "parent_folder": Path(dirpath).name,
                "full_path": str(full),
            })
            if len(out) >= limit:
                return out
    return out
