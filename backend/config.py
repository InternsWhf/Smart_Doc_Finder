# ---------------------------
# backend/config.py
# ---------------------------
from pathlib import Path

"""
Configuration for Smart Document Finder (single-user setup).

- Searches only your mapped/shared drive roots set in ALLOWED_ROOTS
- Filename-only, case-insensitive matching is handled by the backend
- No SQLite index by default (set USE_INDEX=True after building db.sqlite)
"""

# Shared-folder roots to search (use *raw* strings for Windows/UNC paths)
# You can add multiple roots if needed, e.g.:
# ALLOWED_ROOTS = [r"S:\Export Logistics", r"\\WHF-BH-EX01\Another Share"]
ALLOWED_ROOTS = [
    r"\\192.168.2.14\shared drive\Shaheen\Export Shipment Details\Unit 1"
]

# Fixed parent-folder order to render in the single results table
# (Make sure this list matches the UI exactly. Last item is POD.)
PARENT_ORDER = [
    "CIPL",
    "DOCK AUDIT REPORT",
    "MTC PACKAGE",
    "PDIR REPORT",
    "PHOTOGRAPH",
    "BL",
    "POD",
]

# Pagination defaults (used by /search; coverage rows are always 7)
DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200

# Search mode
# False → direct filesystem walk (simple to start)
# True  → use prebuilt SQLite index (faster on very large folders)
USE_INDEX = False

# SQLite DB path (used only if USE_INDEX=True)
DB_PATH = Path(__file__).parent / "db.sqlite"

# CORS for frontend (keep "*" if serving frontend from same backend or LAN)
CORS_ALLOW_ORIGINS = ["*"]

# ---------------------------
# Multi-EXP Missing Report (NEW)
# ---------------------------
# Hard cap on how many EXP codes a user can queue in one report
MULTI_EXP_LIMIT = 30

# Where to drop generated reports (the API also supports temp files)
REPORTS_OUTPUT_DIR = Path(__file__).parent / "reports"

# Default naming for generated .docx reports
DOCX_REPORT_BASENAME = "Missing_Folders_Report"
DOCX_REPORT_AUTHOR = "Smart Document Finder"
