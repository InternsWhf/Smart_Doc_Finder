# ---------------------------
# backend/config.py
# ---------------------------
from pathlib import Path

"""
Configuration for Smart Document Finder (single-user setup).

- Searches only your mapped drive path (set in ALLOWED_ROOTS)
- Filename-only, case-insensitive matching is handled by the backend
- No SQLite index by default (set USE_INDEX=True after building db.sqlite)
"""

# Shared folder roots to search (your mapped drive)
ALLOWED_ROOTS = [
    r"S:\Export Logistics"   # use a *raw* string for Windows paths
]

# Pagination defaults
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
