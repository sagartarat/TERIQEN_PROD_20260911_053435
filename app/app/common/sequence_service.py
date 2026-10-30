# app/common/sequence_service.py

from typing import Optional
from mysql.connector.connection import MySQLConnection


def ensure_sequence_exists(
    conn: MySQLConnection,
    sequence_name: str,
    start_with: int = 1,
) -> None:
    """
    Ensures a sequence row exists inside sequence_master.

    Expected table:
        sequence_master(
            sequence_name VARCHAR(...) PRIMARY KEY,
            current_value BIGINT NOT NULL
        )
    """
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT sequence_name
            FROM sequence_master
            WHERE sequence_name = %s
            LIMIT 1
            """,
            (sequence_name,),
        )
        row = cursor.fetchone()

        if not row:
            cursor.execute(
                """
                INSERT INTO sequence_master (
                    sequence_name,
                    current_value
                ) VALUES (%s, %s)
                """,
                (sequence_name, int(start_with) - 1),
            )
    finally:
        cursor.close()


def get_next_sequence_value(
    conn: MySQLConnection,
    sequence_name: str,
    start_with: int = 1,
) -> int:
    """
    Safely gets the next numeric value using row lock.

    IMPORTANT:
    - Must be called inside the same DB transaction/connection
    - Caller controls commit/rollback
    """
    ensure_sequence_exists(conn, sequence_name, start_with=start_with)

    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT current_value
            FROM sequence_master
            WHERE sequence_name = %s
            FOR UPDATE
            """,
            (sequence_name,),
        )
        row = cursor.fetchone()
        current_value = int(row["current_value"]) if row and row.get("current_value") is not None else 0
        next_value = current_value + 1

        cursor.execute(
            """
            UPDATE sequence_master
            SET current_value = %s
            WHERE sequence_name = %s
            """,
            (next_value, sequence_name),
        )
        return next_value
    finally:
        cursor.close()


def get_next_sequence_text(
    conn: MySQLConnection,
    sequence_name: str,
    prefix: str = "",
    pad_length: int = 0,
    start_with: int = 1,
    suffix: str = "",
) -> str:
    """
    Example:
        get_next_sequence_text(conn, "lead_seq", prefix="LD", pad_length=6)
        -> LD000001
    """
    next_value = get_next_sequence_value(
        conn=conn,
        sequence_name=sequence_name,
        start_with=start_with,
    )

    if pad_length > 0:
        return f"{prefix}{str(next_value).zfill(pad_length)}{suffix}"

    return f"{prefix}{next_value}{suffix}"


def peek_sequence_value(
    conn: MySQLConnection,
    sequence_name: str,
) -> Optional[int]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT current_value
            FROM sequence_master
            WHERE sequence_name = %s
            LIMIT 1
            """,
            (sequence_name,),
        )
        row = cursor.fetchone()

        if not row:
            return None

        return int(row["current_value"])
    finally:
        cursor.close()