# ---------------------------
# backend/mime_types.py
# ---------------------------
import mimetypes

# Extra/normalized mappings to improve preview behavior
EXTRA_TYPES = {
    ".pdf":  "application/pdf",
    ".txt":  "text/plain",
    ".csv":  "text/csv",
    ".json": "application/json",
    ".xml":  "application/xml",

    ".doc":  "application/msword",
    ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    ".xls":  "application/vnd.ms-excel",
    ".xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    ".ppt":  "application/vnd.ms-powerpoint",
    ".pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",

    ".jpg":  "image/jpeg",
    ".jpeg": "image/jpeg",
    ".png":  "image/png",
    ".gif":  "image/gif",
    ".bmp":  "image/bmp",
    ".tif":  "image/tiff",
    ".tiff": "image/tiff",
}

def guess_type(path: str) -> str:
    """
    Return a best-effort MIME type for the given file path.
    PDFs and common image types will render inline in most browsers.
    Office documents typically download or open in the local app.
    """
    if not path:
        return "application/octet-stream"

    ext = ""
    dot = path.rfind(".")
    if dot != -1:
        ext = path[dot:].lower()

    # Prefer our explicit map first
    if ext in EXTRA_TYPES:
        return EXTRA_TYPES[ext]

    # Fallback to Python's mimetypes
    mt, _ = mimetypes.guess_type(path)
    return mt or "application/octet-stream"
