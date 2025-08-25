# ---------------------------
# backend/search.py
# ---------------------------
import os
import re
from pathlib import Path
from typing import Dict, List, Optional, Tuple

# We only treat full month names (no numeric mapping/abbrs)
_FULL_MONTHS = {
    "january", "february", "march", "april", "may", "june",
    "july", "august", "september", "october", "november", "december",
}

def _norm_month(month: Optional[str]) -> Optional[str]:
    """
    Normalize month from dropdown:
      - Keep only full month names (case-insensitive).
      - Treat 'Any' / '' / None as None.
    """
    if not month:
        return None
    m = month.strip().lower()
    if m in ("any", ""):
        return None
    return m if m in _FULL_MONTHS else None


# ---------- EXP CODE HELPERS (robust) ----------

_EXP_RX = re.compile(r"^\s*(?:exp[-\s]*)?(\d+)\s*$", re.IGNORECASE)

def _norm_exp_code(s: str) -> Optional[str]:
    """
    Accepts inputs like: '192', 'EXP-192', 'exp192', 'Exp 192' and
    normalizes them to canonical 'EXP-<digits>' (uppercased).
    Returns None if it can't parse a number.
    """
    if not s:
        return None
    m = _EXP_RX.match(str(s))
    if not m:
        return None
    return f"EXP-{m.group(1)}".upper()


# ---------- MONTH HELPERS (text 'contains' only) ----------

def _dir_contains_month(dir_parts: List[str], month: Optional[str]) -> bool:
    """
    True if ANY directory segment contains the month text (case-insensitive).
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
      - If month selected, at least one DIRECTORY segment must CONTAIN the month text.
    """
    if not month:
        return True
    p = Path(full_path)
    return _dir_contains_month([seg for seg in p.parts[:-1]], month)

def _month_ok_for_folder(full_path: str, month: Optional[str], year: Optional[str]) -> bool:
    """
    For FOLDER rows:
      - If month selected, at least one DIRECTORY segment must CONTAIN the month text.
    """
    if not month:
        return True
    return _dir_contains_month(list(Path(full_path).parts), month)


# ---------- FILTER CHECKS ----------

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
      - month: some directory segment must CONTAIN the month text
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


# ---------- ROOT ENUMERATION (scope traversal) ----------

def _enumerate_effective_roots(roots: List[str], year: Optional[str], month: Optional[str]) -> List[str]:
    """
    STRICT hierarchy traversal (month logic = 'contains' only):
      - If no year & no month: search all provided roots as-is.
      - If year only: search <root>/<year>.
      - If month only: search <root>/<*> where folder name CONTAINS month text.
      - If year + month: search only month folders under <root>/<year> where folder name CONTAINS month text.
    """
    effective: List[str] = []

    if not year and not month:
        return [r for r in roots if os.path.exists(r)]

    if year and not month:
        for r in roots:
            ydir = os.path.join(r, str(year))
            if os.path.isdir(ydir):
                effective.append(ydir)
        return effective

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


# ---------- PUBLIC: baseline search (used by /search) ----------

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
    Walk filesystem and return paginated file/folder matches (baseline list).
    """
    month = _norm_month(month)

    matches: List[Dict] = []
    q = (query or "").strip()

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
            # folders: include when folder name matches query (useful for “EXP-xxx” folders)
            folder_name = Path(dirpath).name
            if q and q.lower() in (folder_name.lower()):
                if _month_ok_for_folder(dirpath, month, year):
                    matches.append({
                        "kind": "folder",
                        "file_name": folder_name,
                        "parent_folder": Path(dirpath).parent.name,
                        "full_path": dirpath,
                    })

            # files
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                if _passes_filters_file(fname, full, q, company, year, month):
                    matches.append({
                        "kind": "file",
                        "file_name": fname,
                        "parent_folder": Path(dirpath).name,
                        "full_path": full,
                    })

    # Sort: folders first, then files by name
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


# ---------- COVERAGE (7 parent rows for single-table UI) ----------

def _find_parent_in_path(path_str: str, parent_names_lower: Dict[str, str]) -> Optional[str]:
    """
    Return the canonical parent name if one of the path segments exactly matches it (case-insensitive).
    parent_names_lower: map lower_name -> canonical_name
    """
    for seg in Path(path_str).parts:
        canon = parent_names_lower.get(seg.lower())
        if canon:
            return canon
    return None


def coverage_rows(
    parents_order: List[str],
    roots: List[str],
    query: str,
    year: Optional[str],
    month: Optional[str],
    company: Optional[str],
    max_items_per_parent: int = 2000,
) -> Dict:
    """
    Returns exactly 7 parent rows in the given order:
      {
        "rows": [
          { "parent": "CIPL", "present": true/false, "found": true/false, "count": N, "items": [...] },
          ...
        ]
      }

    - present=True if any folder with that name exists in the scanned scope
    - found=True if any file/folder under that parent matches the query+filters
    - count is the number of matching items (files/folders) under that parent within the current scope
    - items lists the concrete matches (up to max_items_per_parent)
    """
    month = _norm_month(month)
    q = (query or "").strip()

    # Prepare result skeleton in requested order
    rows: Dict[str, Dict] = {}
    for p in parents_order:
        rows[p] = {
            "parent": p,
            "present": False,
            "found": False,
            "count": 0,
            "items": [],  # filled only when found
        }

    parent_names_lower = {p.lower(): p for p in parents_order}

    # Determine traversal scope
    roots_to_walk = _enumerate_effective_roots(roots, year, month)
    if not roots_to_walk:
        return {"rows": list(rows.values())}

    # Walk and compute presence + matches
    for root in roots_to_walk:
        if not os.path.exists(root):
            continue

        for dirpath, dirnames, filenames in os.walk(root):
            # mark "present" if a directory with parent name exists at this node
            for d in dirnames:
                canon = parent_names_lower.get(d.lower())
                if canon:
                    rows[canon]["present"] = True

            # check if this directory itself is a known parent (for presence)
            canon_self = parent_names_lower.get(Path(dirpath).name.lower())
            if canon_self:
                rows[canon_self]["present"] = True

            # folder-name match contributes to 'found' if folder is under a known parent
            if q:
                folder_name = Path(dirpath).name
                if q.lower() in folder_name.lower():
                    # attribute this match to the parent found in the path (if any)
                    parent_for_dir = _find_parent_in_path(dirpath, parent_names_lower)
                    if parent_for_dir:
                        if _month_ok_for_folder(dirpath, month, year):
                            # company/year checks happen on full path string
                            pl = dirpath.lower()
                            if (not company or company.lower() in pl) and (not year or month or str(year).lower() in pl):
                                rows[parent_for_dir]["found"] = True
                                rows[parent_for_dir]["count"] += 1
                                if len(rows[parent_for_dir]["items"]) < max_items_per_parent:
                                    rows[parent_for_dir]["items"].append({
                                        "kind": "folder",
                                        "file_name": folder_name,
                                        "parent_folder": Path(dirpath).parent.name,
                                        "full_path": dirpath,
                                    })

            # file matches
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                if _passes_filters_file(fname, full, q, company, year, month):
                    parent_for_file = _find_parent_in_path(full, parent_names_lower)
                    if parent_for_file:
                        rows[parent_for_file]["found"] = True
                        rows[parent_for_file]["count"] += 1
                        if len(rows[parent_for_file]["items"]) < max_items_per_parent:
                            rows[parent_for_file]["items"].append({
                                "kind": "file",
                                "file_name": fname,
                                "parent_folder": Path(dirpath).name,
                                "full_path": full,
                            })

    # Ensure items are sorted (folders first, then files by name)
    for p in parents_order:
        items = rows[p]["items"]
        items.sort(key=lambda r: (r.get("kind") != "folder", r["file_name"].lower()))

    # Return in fixed order
    return {"rows": [rows[p] for p in parents_order]}


def coverage_files_for_parent(
    parents_order: List[str],
    roots: List[str],
    query: str,
    parent: str,
    year: Optional[str],
    month: Optional[str],
    company: Optional[str],
    max_items: int = 5000,
) -> Dict:
    """
    Lazily returns the concrete items for a single parent row.
    Used when the UI expands "View files" on that row.
    """
    parent_lower_map = {p.lower(): p for p in parents_order}
    parent_canon = parent_lower_map.get((parent or "").lower())
    if not parent_canon:
        return {"parent": parent, "items": []}

    month = _norm_month(month)
    q = (query or "").strip()

    items: List[Dict] = []
    roots_to_walk = _enumerate_effective_roots(roots, year, month)
    if not roots_to_walk:
        return {"parent": parent_canon, "items": []}

    for root in roots_to_walk:
        if not os.path.exists(root):
            continue
        for dirpath, dirnames, filenames in os.walk(root):
            # Folder hits under this parent
            if q:
                folder_name = Path(dirpath).name
                if q.lower() in folder_name.lower():
                    # Is this path under our target parent?
                    canon = _find_parent_in_path(dirpath, parent_lower_map)
                    if canon == parent_canon:
                        if _month_ok_for_folder(dirpath, month, year):
                            pl = dirpath.lower()
                            if (not company or company.lower() in pl) and (not year or month or str(year).lower() in pl):
                                items.append({
                                    "kind": "folder",
                                    "file_name": folder_name,
                                    "parent_folder": Path(dirpath).parent.name,
                                    "full_path": dirpath,
                                })
                                if len(items) >= max_items:
                                    break

            # File hits
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                if _passes_filters_file(fname, full, q, company, year, month):
                    canon = _find_parent_in_path(full, parent_lower_map)
                    if canon == parent_canon:
                        items.append({
                            "kind": "file",
                            "file_name": fname,
                            "parent_folder": Path(dirpath).name,
                            "full_path": full,
                        })
                        if len(items) >= max_items:
                            break

            if len(items) >= max_items:
                break
        if len(items) >= max_items:
            break

    items.sort(key=lambda r: (r.get("kind") != "folder", r["file_name"].lower()))
    return {"parent": parent_canon, "items": items}


# ---------- MONTHLY COVERAGE (NEW for /monthly-coverage) ----------

def monthly_coverage(
    parents_order: List[str],
    roots: List[str],
    year: Optional[str],
    month: Optional[str],
    company: Optional[str],
    max_items_per_parent: int = 5000,
) -> Dict:
    """
    Enumerate ALL documents for a given Year/Month scope and group them by the
    fixed parent folders. Output shape:

    {
      "period": {"year": "<year or ''>", "month": "<month or ''>"},
      "available": [
        { "parent": "CIPL", "count": N, "items": [ {kind:"file", file_name, parent_folder, full_path}, ... ] },
        ...
      ],
      "missing": ["PDIR REPORT", "BL", ...]   # parents with zero items
    }

    Notes:
    - Scope selection follows _enumerate_effective_roots (year and/or month).
    - Company filter is a substring check on the full path (like elsewhere).
    - We list FILES only (documents), as that’s what users usually open/preview.
    - Items per parent are capped by max_items_per_parent for safety.
    """
    m = _norm_month(month)
    parent_lower = {p.lower(): p for p in parents_order}

    # Build results skeleton
    grouped: Dict[str, List[Dict]] = {p: [] for p in parents_order}

    roots_to_walk = _enumerate_effective_roots(roots, year, m)
    if not roots_to_walk:
        # Everything is missing if nothing to traverse
        return {
            "period": {"year": str(year) if year else "", "month": m or ""},
            "available": [],
            "missing": list(parents_order),
        }

    for root in roots_to_walk:
        if not os.path.exists(root):
            continue

        for dirpath, dirnames, filenames in os.walk(root):
            for fname in filenames:
                full = os.path.join(dirpath, fname)
                # company filter (substring on full path)
                if company and company.lower() not in full.lower():
                    continue

                # month filter (defensive; scope should already constrain)
                if not _month_ok_for_file(full, m, year):
                    continue

                canon_parent = _find_parent_in_path(full, parent_lower)
                if not canon_parent:
                    continue  # skip files that are not under a known parent folder

                bucket = grouped[canon_parent]
                if len(bucket) >= max_items_per_parent:
                    continue
                bucket.append({
                    "kind": "file",
                    "file_name": fname,
                    "parent_folder": Path(dirpath).name,
                    "full_path": full,
                })

    # Sort items within each parent by file name
    for p in parents_order:
        grouped[p].sort(key=lambda r: r["file_name"].lower())

    available = [
        {"parent": p, "count": len(grouped[p]), "items": grouped[p]}
        for p in parents_order if len(grouped[p]) > 0
    ]
    missing = [p for p in parents_order if len(grouped[p]) == 0]

    return {
        "period": {"year": str(year) if year else "", "month": m or ""},
        "available": available,
        "missing": missing,
    }


# ---------- MULTI-EXP MISSING REPORT (NEW) ----------

def multi_exp_missing(
    parents_order: List[str],
    roots: List[str],
    exp_codes: List[str],
    year: Optional[str] = None,
    month: Optional[str] = None,
    company: Optional[str] = None,
    limit: int = 30,
) -> Dict:
    """
    For up to `limit` EXPs, compute which of the fixed parent folders are missing.

    Input:
      - exp_codes: list like ['EXP-123','124','exp 125', ...] (we normalize)
      - parents_order: the canonical order of parent folders (e.g., ["CIPL","DOCK AUDIT REPORT",...,"POD"])
      - roots/year/month/company: same filter semantics as other functions

    Output:
      {
        "parents_order": [...],
        "count": N,                 # number of EXPs processed
        "limit": 30,
        "items": [
          { "exp": "EXP-123", "missing": ["CIPL","DOCK AUDIT REPORT"], "found": ["MTC PACKAGE", ...] },
          ...
        ],
        "summary": {
          "missing_counts": { "CIPL": 2, "POD": 0, ... }  # across all EXPs
        },
        "invalid": ["EXP-xyz"]      # inputs that could not be parsed
      }
    """
    m = _norm_month(month)

    # Normalize and de-duplicate while preserving order
    normalized: List[str] = []
    invalid: List[str] = []
    seen = set()
    for raw in exp_codes or []:
        norm = _norm_exp_code(raw)
        if not norm:
            invalid.append(str(raw))
            continue
        if norm in seen:
            continue
        seen.add(norm)
        normalized.append(norm)

    # Enforce limit
    if len(normalized) > (limit or 30):
        normalized = normalized[: (limit or 30)]

    results: List[Dict] = []
    missing_counts = {p: 0 for p in parents_order}

    for exp in normalized:
        # Reuse coverage_rows logic for a single EXP
        rows = coverage_rows(
            parents_order=parents_order,
            roots=roots,
            query=exp,
            year=year,
            month=m,
            company=company,
            max_items_per_parent=1,  # we only care about found/missing flags
        ).get("rows", [])

        missing = [r["parent"] for r in rows if not r.get("found", False)]
        found   = [r["parent"] for r in rows if r.get("found", False)]

        # Tally summary
        for p in missing:
            missing_counts[p] = missing_counts.get(p, 0) + 1

        results.append({
            "exp": exp,
            "missing": missing,
            "found": found,
        })

    return {
        "parents_order": list(parents_order),
        "count": len(results),
        "limit": limit or 30,
        "items": results,
        "summary": {"missing_counts": missing_counts},
        "invalid": invalid,
    }
