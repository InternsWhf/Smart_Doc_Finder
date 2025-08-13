# ---------------------------
# backend/search.py
# ---------------------------
import os
from pathlib import Path
from typing import Dict, List, Optional

# We use full month names only (no numeric mapping, no abbreviations).
_FULL_MONTHS = {
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
}

def _norm_month(month: Optional[str]) -> Optional[str]:
    """
    Normalize month from dropdown.
    - Keep only full month names (case-insensitive).
    - Treat 'Any' / '' / None as None.
    """
    if not month:
        return None
    m = month.strip().lower()
    if m in ("any", ""):
        return None
    return m if m in _FULL_MONTHS else None


# ---------- MONTH HELPERS (contains match, text only) ----------
def _dir_contains_month(dir_parts: List[str], month: Optional[str]) -> bool:
    """
    True if ANY directory segment contains the month text (case-insensitive).
    No number mapping or abbreviations — pure substring on that segment.
    """
    if not month:
        return True
    m = month.strip().lower()
    for seg in dir_parts:
        if m in (seg or "").lower():
            return True
    return False

def _month_ok_for_file(full_path: str, month: Optional[str], year: Optional[str]) -> bool:
    """
    For FILE rows:
      - If month is selected, at least one DIRECTORY segment must CONTAIN the month text.
      - We do not map month to numbers; we only use the string.
    """
    if not month:
        return True
    p = Path(full_path)
    dirs = [seg for seg in p.parts[:-1]]  # directories only
    return _dir_contains_month(dirs, month)

def _month_ok_for_folder(full_path: str, month: Optional[str], year: Optional[str]) -> bool:
    """
    For FOLDER rows:
      - If month is selected, at least one DIRECTORY segment must CONTAIN the month text.
    """
    if not month:
        return True
    dirs = list(Path(full_path).parts)  # entire folder path
    return _dir_contains_month(dirs, month)


def _passes_filters_file(
    file_name: str,
    full_path: str,
    query: str,
    company: Optional[str],
    year: Optional[str],
    month: Optional[str],
) -> bool:
    """
    Files:
      - query: filename-only, case-insensitive, substring
      - company/year: anywhere in full path (string match)
      - month: some directory segment must CONTAIN the month text (no number mapping)
    """
    name_l = (file_name or "").lower()
    path_l = (full_path or "").lower()

    if query and query.lower() not in name_l:
        return False
    if company and company.lower() not in path_l:
        return False

    # Enforce year substring only when no month filter is applied
    if year and not month and str(year).lower() not in path_l:
        return False

    if not _month_ok_for_file(full_path, month, year):
        return False
    return True


def _passes_filters_folder(
    folder_name: str,
    full_path: str,
    query: str,
    company: Optional[str],
    year: Optional[str],
    month: Optional[str],
) -> bool:
    """
    Folders:
      - query: must match the folder name (if provided)
      - company/year: anywhere in full path
      - month: some directory segment must CONTAIN the month text (no number mapping)
    """
    fname_l = (folder_name or "").lower()
    path_l = (full_path or "").lower()

    if query and query.lower() not in fname_l:
        return False
    if company and company.lower() not in path_l:
        return False

    # Enforce year substring only when no month filter is applied
    if year and not month and str(year).lower() not in path_l:
        return False

    if not _month_ok_for_folder(full_path, month, year):
        return False
    return True


def _enumerate_effective_roots(roots: List[str], year: Optional[str], month: Optional[str]) -> List[str]:
    """
    STRICT hierarchy traversal (month logic = 'contains' only):
      - If no year: search all provided roots as-is.
      - If year only: search the <root>/<year> directory.
      - If month + year: search ONLY inside <root>/<year>/<*> where the folder name
        CONTAINS the month text (e.g., '6. June-2025', 'June-2025', 'June 2025').
      - If month only (no year): search any subdir of <root> whose name CONTAINS the month text.
    """
    effective: List[str] = []

    # No year, no month → all roots
    if not year and not month:
        return [r for r in roots if os.path.exists(r)]

    # Year only → <root>/<year>
    if year and not month:
        for r in roots:
            ydir = os.path.join(r, str(year))
            if os.path.isdir(ydir):
                effective.append(ydir)
        return effective

    # Month only → any <root>/<*> where name CONTAINS month text
    if month and not year:
        m = month.strip().lower()
        for r in roots:
            if not os.path.isdir(r):
                continue
            try:
                for name in os.listdir(r):
                    full = os.path.join(r, name)
                    if os.path.isdir(full) and (m in name.lower()):
                        effective.append(full)
            except PermissionError:
                continue
        return effective

    # Month + Year → only month folders under <root>/<year> whose name CONTAINS month text
    if month and year:
        m = month.strip().lower()
        y = str(year)
        for r in roots:
            ydir = os.path.join(r, y)
            if not os.path.isdir(ydir):
                continue
            try:
                for name in os.listdir(ydir):
                    full = os.path.join(ydir, name)
                    if os.path.isdir(full) and (m in name.lower()):
                        effective.append(full)
            except PermissionError:
                continue
        return effective

    return effective


# ---------- main search ----------
def walk_search(
    query: str,
    roots: List[str],
    year: Optional[str],
    month: Optional[str],
    company: Optional[str],
    page: int,
    page_size: int,
) -> Dict:
    """
    Walk filesystem and return paginated matches.

    STRICT HIERARCHY:
      - If year is selected, traverse only <root>/<year>.
      - If month is also selected, traverse only the folder(s) inside that year whose
        name CONTAINS the month text. Show only files/folders from there.
      - Month is TEXT ONLY (full name); no abbreviations and no numeric mapping.
      - Folders are included even if query is empty, so 'EXP-192' shows up simply by picking Year+Month.
    """
    # Normalize month per rules
    month = _norm_month(month)

    matches: List[Dict] = []
    q = (query or "").strip()

    # Determine traversal roots
    roots_to_walk = _enumerate_effective_roots(roots, year, month)
    if not roots_to_walk:
        return {
            "count": 0,
            "page": page,
            "page_size": page_size,
            "total_pages": 1,
            "items": [],
        }

    for root in roots_to_walk:
        if not os.path.exists(root):
            continue

        for dirpath, dirnames, filenames in os.walk(root):
            # --- folder hits (ALWAYS evaluate filters; do NOT require q to be non-empty) ---
            folder_name = Path(dirpath).name
            if _passes_filters_folder(folder_name, dirpath, q, company, year, month):
                matches.append({
                    "kind": "folder",
                    "file_name": folder_name,
                    "parent_folder": Path(dirpath).parent.name,
                    "full_path": dirpath,
                })

            # --- file hits (filename must contain query if provided) ---
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                if _passes_filters_file(fname, full, q, company, year, month):
                    matches.append({
                        "kind": "file",
                        "file_name": fname,
                        "parent_folder": Path(dirpath).name,
                        "full_path": full,
                    })

    # Sort: folders first, then files
    matches.sort(key=lambda r: (r.get("kind") != "folder", r["file_name"].lower()))

    total = len(matches)
    start = max(0, (page - 1) * page_size)
    end = min(total, start + page_size)
    items = matches[start:end]

    return {
        "count": total,
        "page": page,
        "page_size": page_size,
        "total_pages": max(1, (total + page_size - 1) // page_size),
        "items": items,
    }
