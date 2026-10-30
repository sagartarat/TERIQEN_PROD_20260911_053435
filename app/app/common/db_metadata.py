def table_exists(conn, table_name):
    cursor = conn.cursor()
    try:
        cursor.execute("SHOW TABLES LIKE %s", (table_name,))
        return cursor.fetchone() is not None
    finally:
        cursor.close()


def get_table_columns(conn, table_name):
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(f"SHOW COLUMNS FROM {table_name}")
        rows = cursor.fetchall() or []
        return {
            (row.get("Field") or "").strip()
            for row in rows
            if (row.get("Field") or "").strip()
        }
    finally:
        cursor.close()


def get_table_schema_rows(conn, table_name):
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(f"SHOW COLUMNS FROM {table_name}")
        return cursor.fetchall() or []
    finally:
        cursor.close()


def first_existing_column(columns, candidates):
    column_set = set(columns or [])
    for candidate in candidates or []:
        if candidate in column_set:
            return candidate
    return None
