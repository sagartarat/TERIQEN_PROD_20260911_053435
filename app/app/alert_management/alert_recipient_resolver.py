"""
Resolve configured Alert Rule recipients into actual application users.
"""

from __future__ import annotations

from typing import Any

from app.common.db import get_db_connection


def _close(connection=None, cursor=None) -> None:
    if cursor is not None:
        try:
            cursor.close()
        except Exception:
            pass

    if connection is not None:
        try:
            connection.close()
        except Exception:
            pass


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _get_users_by_codes(
    user_codes: list[str],
) -> list[dict[str, Any]]:
    clean_codes = sorted({
        _safe_text(code)
        for code in user_codes
        if _safe_text(code)
    })

    if not clean_codes:
        return []

    connection = cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        placeholders = ", ".join(["%s"] * len(clean_codes))

        cursor.execute(
            f"""
            SELECT
                user_code,
                user_name,
                user_email,
                user_conctno,
                user_type,
                user_status
            FROM user_master
            WHERE user_code IN ({placeholders})
              AND user_status = 'Active'
            """,
            tuple(clean_codes),
        )

        return cursor.fetchall() or []

    finally:
        _close(connection, cursor)


def _resolve_assigned_user_code(
    source_record: dict[str, Any],
) -> str:
    """
    Resolve the action owner without changing existing module behaviour.

    Priority:
    1. assigned_user alias supplied by the alert source query
    2. assigned_to used by operation_queue_master
    3. entered_by used by patient_wrkflw
    """
    source_record = source_record or {}

    return _safe_text(
        source_record.get("assigned_user")
        or source_record.get("assigned_to")
        or source_record.get("entered_by")
    )


def resolve_recipients(
    recipient_config: dict[str, Any],
    source_record: dict[str, Any],
) -> list[dict[str, Any]]:
    recipient_config = recipient_config or {}
    source_record = source_record or {}

    recipient_type = _safe_text(
        recipient_config.get("recipient_type")
    ).upper()

    recipient_value = _safe_text(
        recipient_config.get("recipient_value")
    )

    connection = cursor = None

    try:
        if recipient_type == "ASSIGNED_USER":
            assigned_user_code = _resolve_assigned_user_code(
                source_record
            )

            return _get_users_by_codes([
                assigned_user_code
            ])

        if recipient_type == "SPECIFIC_USER":
            return _get_users_by_codes([
                recipient_value
            ])

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if recipient_type == "ROLE":
            cursor.execute(
                """
                SELECT DISTINCT
                    u.user_code,
                    u.user_name,
                    u.user_email,
                    u.user_conctno,
                    u.user_type,
                    u.user_status
                FROM user_role_mapping urm
                INNER JOIN user_master u
                    ON u.user_code = urm.user_code
                WHERE TRIM(urm.role_code) = %s
                  AND u.user_status = 'Active'
                ORDER BY u.user_name, u.user_code
                """,
                (recipient_value,),
            )

            return cursor.fetchall() or []

        if recipient_type == "ADMINISTRATOR":
            cursor.execute(
                """
                SELECT
                    user_code,
                    user_name,
                    user_email,
                    user_conctno,
                    user_type,
                    user_status
                FROM user_master
                WHERE user_status = 'Active'
                  AND UPPER(user_type) = 'ADMIN'
                ORDER BY user_name, user_code
                """
            )

            return cursor.fetchall() or []

        if recipient_type == "TEAM_LEADER":
            # No team-leader mapping table has been connected yet.
            # Keep the existing behaviour and do not guess recipients.
            return []

        return []

    finally:
        _close(connection, cursor)
