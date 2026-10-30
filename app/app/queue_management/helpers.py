from app.common.db import get_db_connection
from app.queue_management.rule_engine import (
    build_operation_queue_insert_payload,
    get_operation_queue_insert_columns,
    get_operation_queue_insert_values,
    should_create_operation_queue_row,
)
from app.queue_management.operation_queue_service import (
    get_operation_queue_grid_data,
    get_operation_queue_summary_counts,
)
from app.common.file_helpers import parse_attachment_json


def _safe_upper(value):
    if value is None:
        return ""

    return str(value).strip().upper()


def _mark_channel_message_for_manual_review(payload, operation_queue_id=None):
    """
    Preserve the original fallback path for messages that cannot be converted
    into an operation_queue_master row.

    Parsed business messages with Passport Number and missing agent_shrtnm no
    longer use this function. They are inserted into operation_queue_master with
    parse_status MANUAL_REVIEW and channel_message_log is marked PARSED.
    """
    payload = payload or {}
    channel_message_log_id = payload.get("channel_message_log_id")

    parse_remarks = (
        payload.get("manual_review_reason")
        or payload.get("parse_remarks")
        or (
            "WhatsApp sender could not be mapped to a valid Agency. "
            "Manual review is required for Agent assignment."
        )
    )

    if not channel_message_log_id:
        return {
            "success": False,
            "updated": False,
            "message": (
                "channel_message_log_id is missing. "
                "Manual Review status could not be saved."
            ),
        }

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE channel_message_log
            SET
                parse_status = 'MANUAL_REVIEW',
                parsed_flag = '0',
                requires_manual_review = '1',
                linked_queue_id = %s,
                channel_thread_id = NULL,
                classifier_status = 'CLASSIFIED',
                classifier_reason = %s,
                thread_resolution_status = 'NO_THREAD_FOUND',
                remarks = %s,
                updated_on = NOW()
            WHERE id = %s
            """,
            (operation_queue_id, parse_remarks, parse_remarks, channel_message_log_id),
        )

        affected_rows = cursor.rowcount
        conn.commit()

        if affected_rows == 0:
            return {
                "success": False,
                "updated": False,
                "message": "No channel_message_log record was found for the supplied ID.",
                "channel_message_log_id": channel_message_log_id,
            }

        return {
            "success": True,
            "updated": True,
            "message": "Record moved to Manual Review successfully.",
            "channel_message_log_id": channel_message_log_id,
        }

    except Exception as exc:
        if conn:
            conn.rollback()
        return {
            "success": False,
            "updated": False,
            "message": str(exc),
            "channel_message_log_id": channel_message_log_id,
        }

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _mark_channel_message_parsed(
    channel_message_log_id,
    operation_queue_id,
    channel_thread_id,
    remarks,
    requires_manual_review=False,
):
    if not channel_message_log_id:
        return

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE channel_message_log
            SET
                parse_status = 'PARSED',
                parsed_flag = '1',
                requires_manual_review = %s,
                junk_flag = '0',
                linked_queue_id = %s,
                channel_thread_id = COALESCE(%s, channel_thread_id),
                classifier_status = 'CLASSIFIED',
                classifier_reason = CASE
                    WHEN %s = 1 THEN COALESCE(%s, classifier_reason)
                    ELSE classifier_reason
                END,
                thread_resolution_status = CASE
                    WHEN %s IS NOT NULL AND TRIM(%s) <> ''
                        THEN 'EXISTING_THREAD'
                    ELSE thread_resolution_status
                END,
                remarks = COALESCE(%s, remarks),
                updated_on = NOW()
            WHERE id = %s
            """,
            (
                1 if requires_manual_review else 0,
                operation_queue_id,
                channel_thread_id,
                1 if requires_manual_review else 0,
                remarks,
                channel_thread_id,
                channel_thread_id,
                remarks,
                channel_message_log_id,
            ),
        )
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def insert_into_operation_queue_master(raw_message_text, channel_meta=None):
    payload = build_operation_queue_insert_payload(
        raw_message_text=raw_message_text,
        channel_meta=channel_meta or {},
    )

    parse_status = _safe_upper(payload.get("parse_status"))

    if parse_status == "MANUAL_REVIEW":
        manual_review_reason = (
            payload.get("manual_review_reason")
            or payload.get("classifier_reason")
            or payload.get("parse_remarks")
            or (
                "WhatsApp sender could not be mapped to a valid Agency. "
                "Manual review is required for Agent assignment."
            )
        )
        if str(manual_review_reason).strip().upper() in {"MANUAL REVIEW", "MANUAL_REVIEW"}:
            manual_review_reason = (
                "WhatsApp sender could not be mapped to a valid Agency. "
                "Manual review is required for Agent assignment."
            )
        payload["manual_review_reason"] = manual_review_reason
        payload["parse_remarks"] = manual_review_reason
        payload["channel_thread_id"] = None
        payload["channel_message_id"] = None
        payload["assigned_to"] = None
        payload["assigned_by"] = None
        payload["assigned_at"] = None

    if not should_create_operation_queue_row(payload):
        return {
            "success": False,
            "skipped": True,
            "manual_review": False,
            "queue_created": False,
            "inserted_id": None,
            "reason": "parse_status_failed",
            "message": (
                f"Parse status is {parse_status or 'FAILED'}. "
                "No row created in operation_queue_master."
            ),
            "payload": payload,
        }

    columns = get_operation_queue_insert_columns()
    values = get_operation_queue_insert_values(payload)
    placeholders = ", ".join(["%s"] * len(columns))
    column_sql = ", ".join(columns)

    query = f"""
        INSERT INTO operation_queue_master
        ({column_sql})
        VALUES
        ({placeholders})
    """

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(query, values)
        inserted_id = cursor.lastrowid
        conn.commit()

        if parse_status == "MANUAL_REVIEW":
            _mark_channel_message_for_manual_review(
                payload,
                operation_queue_id=inserted_id,
            )
        else:
            _mark_channel_message_parsed(
                channel_message_log_id=payload.get("channel_message_log_id"),
                operation_queue_id=inserted_id,
                channel_thread_id=payload.get("channel_thread_id"),
                remarks=(
                    payload.get("parse_remarks")
                    or "Operation queue row created from parsed message."
                ),
                requires_manual_review=False,
            )

        return {
            "success": True,
            "skipped": False,
            "manual_review": parse_status == "MANUAL_REVIEW",
            "queue_created": True,
            "inserted_id": inserted_id,
            "operation_queue_master_id": inserted_id,
            "channel_thread_id": payload.get("channel_thread_id"),
            "parse_status": parse_status,
            "payload": payload,
        }

    except Exception as exc:
        conn.rollback()
        return {
            "success": False,
            "skipped": False,
            "manual_review": parse_status == "MANUAL_REVIEW",
            "queue_created": False,
            "inserted_id": None,
            "error": str(exc),
            "payload": payload,
        }

    finally:
        cursor.close()
        conn.close()


def insert_operation_queue_attachments(
    operation_queue_id,
    channel_thread_id,
    channel_message_log_id,
    channel_message_id,
    attachment_json,
):
    attachments = parse_attachment_json(attachment_json)

    if not attachments:
        return {"success": True, "inserted_count": 0}

    if not operation_queue_id:
        return {
            "success": False,
            "skipped": True,
            "inserted_count": 0,
            "message": (
                "operation_queue_id is required for "
                "Operation Queue attachment insertion."
            ),
        }

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        sql = """
            INSERT INTO operation_queue_attachment
            (
                operation_queue_id,
                channel_thread_id,
                channel_message_log_id,
                channel_message_id,
                file_name,
                file_ext,
                mime_type,
                file_size,
                file_path,
                remarks
            )
            VALUES
            (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s
            )
        """

        inserted_count = 0

        for item in attachments:
            cursor.execute(
                sql,
                (
                    operation_queue_id,
                    channel_thread_id,
                    channel_message_log_id,
                    channel_message_id,
                    item.get("file_name"),
                    item.get("file_ext"),
                    item.get("mime_type"),
                    item.get("file_size"),
                    item.get("file_path"),
                    item.get("remarks"),
                ),
            )
            inserted_count += 1

        conn.commit()
        return {"success": True, "inserted_count": inserted_count}

    except Exception as exc:
        conn.rollback()
        return {
            "success": False,
            "inserted_count": 0,
            "error": str(exc),
        }

    finally:
        cursor.close()
        conn.close()


def fetch_operation_queue_grid_data():
    return get_operation_queue_grid_data()


def fetch_operation_queue_summary_counts():
    return get_operation_queue_summary_counts()
