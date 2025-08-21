import os
import sqlite3
from pathlib import Path

def build_index(db_path, roots):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()
    cur.execute("DROP TABLE IF EXISTS files")
    cur.execute("""
        CREATE TABLE files (
            file_name TEXT,
            parent_folder TEXT,
            full_path TEXT
        )
    """)


    for root in roots:
        for dirpath, _, filenames in os.walk(root):
            for fname in filenames:
                full_path = os.path.join(dirpath, fname)
                parent_folder = os.path.basename(os.path.dirname(full_path))
                cur.execute(
                    "INSERT INTO files (file_name, parent_folder, full_path) VALUES (?, ?, ?)",
                    (fname, parent_folder, full_path)
                )

    conn.commit()
    conn.close()

def search_index(db_path, query, year=None, month=None, company=None, page=1, page_size=50):
    conn = sqlite3.connect(db_path)
    cur = conn.cursor()

    filters = ["LOWER(file_name) LIKE ?"]
    params = [f"%{query.lower()}%"]

    if year:
        filters.append("LOWER(file_name) LIKE ?")
        params.append(f"%{year.lower()}%")
    if month:
        filters.append("LOWER(file_name) LIKE ?")
        params.append(f"%{month.lower()}%")
    if company:
        filters.append("LOWER(file_name) LIKE ?")
        params.append(f"%{company.lower()}%")

    where_clause = " AND ".join(filters)
    count_sql = f"SELECT COUNT(*) FROM files WHERE {where_clause}"
    total = cur.execute(count_sql, params).fetchone()[0]

    offset = (page - 1) * page_size
    sql = f"""
        SELECT file_name, parent_folder, full_path
        FROM files
        WHERE {where_clause}
        ORDER BY file_name ASC
        LIMIT ? OFFSET ?
    """
    items = [
        {"file_name": r[0], "parent_folder": r[1], "full_path": r[2]}
        for r in cur.execute(sql, params + [page_size, offset]).fetchall()
    ]

    conn.close()
    return {
        "count": total,
        "page": page,
        "total_pages": max(1, -(-total // page_size)),
        "items": items
    }
