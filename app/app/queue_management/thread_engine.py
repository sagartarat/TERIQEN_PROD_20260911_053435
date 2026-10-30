import re
from datetime import datetime
from app.common.db import get_db_connection


CLOSED_STATUSES = (
    "CLOSED",
    "COMPLETED",
    "CONVERTED",
    "CANCELLED",
    "REJECTED",
)


def _safe_thread_part(value):
    if not value:
        return "UNKNOWN"
    value = str(value).strip().upper()
    value = re.sub(r"[^A-Z0-9]+", "", value)
    return value if value else "UNKNOWN"


def _normalize_passport(value):
    if not value:
        return None
    value = str(value).strip().upper()
    value = re.sub(r"\s+", "", value)
    value = re.sub(r"[^A-Z0-9]", "", value)
    return value if value else None


def generate_channel_thread_id(passport_no):
    passport_part = _safe_thread_part(passport_no)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"THR_{passport_part}_{timestamp}"


def find_existing_open_thread_context(passport_no):
    normalized_passport = _normalize_passport(passport_no)
    if not normalized_passport:
        return None

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        closed_placeholders = ", ".join(["%s"] * len(CLOSED_STATUSES))

        sql = f"""
            SELECT
                id,
                channel_thread_id,
                assigned_to,
                wrkflw_status,
                converted_flag
            FROM operation_queue_master
            WHERE UPPER(
                    REPLACE(
                        REPLACE(
                            REPLACE(
                                TRIM(COALESCE(patpassport_no, '')),
                                ' ',
                                ''
                            ),
                            '-',
                            ''
                        ),
                        '/',
                        ''
                    )
                  ) = %s
              AND channel_thread_id IS NOT NULL
              AND TRIM(channel_thread_id) <> ''
              AND (
                    wrkflw_status IS NULL
                    OR UPPER(TRIM(wrkflw_status)) NOT IN ({closed_placeholders})
                  )
            ORDER BY id DESC
            LIMIT 1
        """

        params = [normalized_passport] + list(CLOSED_STATUSES)
        cursor.execute(sql, tuple(params))
        row = cursor.fetchone()

        if row:
            return {
                "id": row.get("id"),
                "channel_thread_id": row.get("channel_thread_id"),
                "assigned_to": row.get("assigned_to"),
                "wrkflw_status": row.get("wrkflw_status"),
                "converted_flag": row.get("converted_flag"),
            }

        return None

    except Exception as e:
        print("find_existing_open_thread_context error:", e)
        return None

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def find_latest_thread_id_any_status(passport_no):
    normalized_passport = _normalize_passport(passport_no)
    if not normalized_passport:
        return None

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        sql = """
            SELECT channel_thread_id
            FROM operation_queue_master
            WHERE UPPER(
                    REPLACE(
                        REPLACE(
                            REPLACE(
                                TRIM(COALESCE(patpassport_no, '')),
                                ' ',
                                ''
                            ),
                            '-',
                            ''
                        ),
                        '/',
                        ''
                    )
                  ) = %s
              AND channel_thread_id IS NOT NULL
              AND TRIM(channel_thread_id) <> ''
            ORDER BY id DESC
            LIMIT 1
        """

        cursor.execute(sql, (normalized_passport,))
        row = cursor.fetchone()

        if row:
            return row.get("channel_thread_id")

        return None

    except Exception as e:
        print("find_latest_thread_id_any_status error:", e)
        return None

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def resolve_thread_context(passport_no):
    normalized_passport = _normalize_passport(passport_no)

    if not normalized_passport:
        return {
            "channel_thread_id": generate_channel_thread_id(passport_no),
            "assigned_to": None,
            "wrkflw_status": None,
            "continuity_applied": False,
            "source": "NEW_NO_PASSPORT",
        }

    existing_open = find_existing_open_thread_context(normalized_passport)
    if existing_open:
        return {
            "channel_thread_id": existing_open.get("channel_thread_id"),
            "assigned_to": existing_open.get("assigned_to"),
            "wrkflw_status": existing_open.get("wrkflw_status"),
            "continuity_applied": True,
            "source": "EXISTING_OPEN_THREAD",
        }

    return {
        "channel_thread_id": generate_channel_thread_id(normalized_passport),
        "assigned_to": None,
        "wrkflw_status": None,
        "continuity_applied": False,
        "source": "NEW_THREAD_AFTER_CLOSED_OR_NEW_CASE",
    }


def resolve_channel_thread_id(passport_no):
    context = resolve_thread_context(passport_no)
    return context.get("channel_thread_id")