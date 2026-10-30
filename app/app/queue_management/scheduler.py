from datetime import datetime, timedelta
import json
import os
import traceback
from pathlib import Path

from flask import current_app, has_app_context

from app.common.db import get_db_connection
from app.common.file_helpers import parse_attachment_json
from app.common.logger import get_logger
from app.queue_management.operation_queue_service import (
    insert_into_operation_queue_master,
)
from app.whatsapp.submission_ack_service import (
    process_pending_submission_acknowledgements,
)
from app.whatsapp.message_classifier_service import (
    classify_submission_window,
)
from app.whatsapp.consent_service import (
    process_whatsapp_session_timeouts,
)
from app.alert_management.alert_scheduler import (
    run_alert_engine_job as run_alert_engine_job_without_context,
)
from app.system_monitoring.scheduler_monitoring_service import (
    is_job_enabled,
    refresh_scheduler_heartbeat,
    register_scheduler_jobs,
    run_monitored_job,
)

try:
    from apscheduler.schedulers.background import BackgroundScheduler
except Exception:
    BackgroundScheduler = None


_scheduler_instance = None
_scheduler_app = None
scheduler_logger = get_logger("scheduler")
whatsapp_parser_logger = get_logger("whatsapp_parser")
sla_engine_logger = get_logger("sla_engine")
alert_engine_job_logger = get_logger("alert_engine_job")


WHATSAPP_ATTACHMENT_GROUPING_SECONDS = 60
WHATSAPP_SUBMISSION_WINDOW_SECONDS = int(
    os.getenv("WHATSAPP_SUBMISSION_WINDOW_SECONDS", "60")
)


# ============================================================
# WHATSAPP SUBMISSION COLLECTION WINDOW
# ============================================================

def _reset_stale_whatsapp_submission_windows(stale_minutes=10):
    """Recover a window claimed as PROCESSING before classification completed."""
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE whatsapp_submission_tracking t
            INNER JOIN channel_message_log c
                ON c.id = t.channel_message_log_id
            SET
                t.session_status = 'OPEN',
                t.updated_on = NOW()
            WHERE t.session_status = 'PROCESSING'
              AND t.parser_outcome = 'PENDING'
              AND UPPER(TRIM(COALESCE(c.parse_status, 'PENDING'))) = 'PENDING'
              AND COALESCE(t.updated_on, t.created_on) < DATE_SUB(NOW(), INTERVAL %s MINUTE)
            """,
            (int(stale_minutes),),
        )
        changed = int(cursor.rowcount or 0)
        conn.commit()
        return changed
    except Exception:
        if conn:
            conn.rollback()
        whatsapp_parser_logger.exception(
            "WHATSAPP_SUBMISSION_STALE_WINDOW_RESET_FAILED"
        )
        return 0
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _fetch_ready_whatsapp_submission_windows(cursor, batch_size=50):
    """Return OPEN roots whose sliding quiet window has elapsed."""
    cursor.execute(
        """
        SELECT
            t.id AS tracking_id,
            t.channel_message_log_id AS root_message_log_id,
            t.sender_mobile,
            t.submission_received_on,
            t.last_message_received_on
        FROM whatsapp_submission_tracking t
        INNER JOIN channel_message_log c
            ON c.id = t.channel_message_log_id
        WHERE t.session_status = 'OPEN'
          AND t.parser_outcome = 'PENDING'
          AND UPPER(TRIM(COALESCE(c.channel_code, ''))) = 'WHATSAPP'
          AND UPPER(TRIM(COALESCE(c.parse_status, 'PENDING'))) = 'PENDING'
          AND TIMESTAMPDIFF(
                SECOND,
                COALESCE(t.last_message_received_on, t.submission_received_on),
                NOW()
              ) >= %s
        ORDER BY COALESCE(t.last_message_received_on, t.submission_received_on) ASC,
                 t.id ASC
        LIMIT %s
        """,
        (
            int(WHATSAPP_SUBMISSION_WINDOW_SECONDS),
            int(batch_size or 50),
        ),
    )
    return cursor.fetchall() or []


def _resolve_whatsapp_txt_attachment_path(file_path):
    """Resolve only TXT files stored inside the private WhatsApp upload root."""
    if not has_app_context():
        return None

    raw = str(file_path or "").strip()
    if not raw:
        return None

    root = (Path(current_app.instance_path) / "uploads" / "whatsapp_docs").resolve()
    normalized = raw.replace("\\", "/")
    lower = normalized.lower()

    relative = None
    for marker in (
        "private/whatsapp_docs/",
        "uploads/whatsapp_docs/",
        "whatsapp_docs/",
    ):
        idx = lower.rfind(marker)
        if idx >= 0:
            relative = normalized[idx + len(marker):].lstrip("/")
            break

    candidate = (root / relative).resolve() if relative is not None else Path(raw).expanduser().resolve()
    try:
        candidate.relative_to(root)
    except ValueError:
        return None
    return candidate if candidate.is_file() else None


def _read_whatsapp_txt_attachment(attachment, max_bytes=262144):
    """Read a returned prescribed-format TXT safely as patient submission text."""
    attachment = attachment or {}
    file_name = str(attachment.get("file_name") or attachment.get("filename") or "").strip()
    file_ext = str(attachment.get("file_ext") or "").strip().lower().lstrip(".")
    mime_type = str(attachment.get("mime_type") or "").split(";", 1)[0].strip().lower()

    if not file_ext and "." in file_name:
        file_ext = file_name.rsplit(".", 1)[-1].lower()
    if file_ext != "txt" and mime_type != "text/plain":
        return ""

    path = _resolve_whatsapp_txt_attachment_path(attachment.get("file_path"))
    if not path:
        return ""

    try:
        size = path.stat().st_size
        if size > int(max_bytes):
            whatsapp_parser_logger.warning(
                "WHATSAPP_TXT_ATTACHMENT_TOO_LARGE path=%s size=%s", path, size
            )
            return ""
        data = path.read_bytes()
        if b"\x00" in data:
            return ""
        try:
            text = data.decode("utf-8-sig")
        except UnicodeDecodeError:
            return ""
        return text.replace("\r\n", "\n").replace("\r", "\n").strip()
    except Exception as exc:
        whatsapp_parser_logger.warning(
            "WHATSAPP_TXT_ATTACHMENT_READ_FAILED path=%s error=%s", path, exc
        )
        return ""


def _build_whatsapp_submission_window_content(cursor, root_message_log_id):
    """Combine all text and media metadata from one linked_triage submission."""
    cursor.execute(
        """
        SELECT
            id,
            raw_message_text,
            attachment_count,
            attachment_json,
            received_on
        FROM channel_message_log
        WHERE id = %s
           OR linked_triage_id = %s
        ORDER BY received_on ASC, id ASC
        """,
        (root_message_log_id, root_message_log_id),
    )
    rows = cursor.fetchall() or []

    placeholder_texts = {
        "[Document message received]",
        "[Image message received]",
        "[Video message received]",
        "[Audio message received]",
    }

    meaningful_texts = []
    fallback_texts = []
    attachments = []
    seen_attachment_keys = set()

    for item in rows:
        raw_text = str(item.get("raw_message_text") or "").strip()
        if raw_text:
            fallback_texts.append(raw_text)
            if raw_text not in placeholder_texts:
                meaningful_texts.append(raw_text)

        for attachment in parse_attachment_json(item.get("attachment_json")) or []:
            attachment = attachment or {}
            key = (
                str(attachment.get("media_id") or ""),
                str(attachment.get("file_path") or ""),
                str(attachment.get("file_name") or attachment.get("filename") or ""),
            )
            if key in seen_attachment_keys:
                continue
            seen_attachment_keys.add(key)
            attachments.append(attachment)

            # A filled prescribed-format TXT sent back as a WhatsApp document
            # is patient-detail text. Keep it as an attachment for audit/storage
            # and also feed its plain text into the existing parser body.
            txt_content = _read_whatsapp_txt_attachment(attachment)
            if txt_content:
                meaningful_texts.append(txt_content)

    combined_text = "\n".join(meaningful_texts).strip()
    if not combined_text:
        combined_text = "\n".join(fallback_texts).strip()

    return {
        "rows": rows,
        "raw_message_text": combined_text,
        "attachment_count": len(attachments),
        "attachment_json": (
            json.dumps(attachments, ensure_ascii=False)
            if attachments
            else None
        ),
    }


def _classify_ready_whatsapp_submission_windows(batch_size=50):
    """
    Claim each elapsed OPEN window as PROCESSING, aggregate all messages/files,
    then classify the ROOT exactly once.

    Changing OPEN -> PROCESSING is the hard collection boundary: a WhatsApp
    event arriving after this point starts a new submission window.
    """
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        candidates = _fetch_ready_whatsapp_submission_windows(
            cursor,
            batch_size=batch_size,
        )
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    summary = {
        "candidates": len(candidates or []),
        "claimed": 0,
        "classified": 0,
        "skipped_reopened": 0,
        "failed": 0,
    }

    for candidate in candidates or []:
        claim_conn = None
        claim_cursor = None
        try:
            claim_conn = get_db_connection()
            claim_cursor = claim_conn.cursor(dictionary=True)

            claim_cursor.execute(
                """
                SELECT *
                FROM whatsapp_submission_tracking
                WHERE id = %s
                LIMIT 1
                FOR UPDATE
                """,
                (candidate.get("tracking_id"),),
            )
            tracking = claim_cursor.fetchone() or {}

            if (
                str(tracking.get("session_status") or "").strip().upper() != "OPEN"
                or str(tracking.get("parser_outcome") or "").strip().upper() != "PENDING"
            ):
                claim_conn.rollback()
                summary["skipped_reopened"] += 1
                continue

            # Recheck the quiet period while holding the tracking-row lock. A
            # concurrent webhook may have extended last_message_received_on.
            claim_cursor.execute(
                """
                SELECT TIMESTAMPDIFF(
                           SECOND,
                           COALESCE(last_message_received_on, submission_received_on),
                           NOW()
                       ) AS quiet_seconds
                FROM whatsapp_submission_tracking
                WHERE id = %s
                """,
                (candidate.get("tracking_id"),),
            )
            quiet_row = claim_cursor.fetchone() or {}
            quiet_seconds = int(quiet_row.get("quiet_seconds") or 0)
            if quiet_seconds < int(WHATSAPP_SUBMISSION_WINDOW_SECONDS):
                claim_conn.rollback()
                summary["skipped_reopened"] += 1
                continue

            claim_cursor.execute(
                """
                UPDATE whatsapp_submission_tracking
                SET
                    session_status = 'PROCESSING',
                    updated_on = NOW()
                WHERE id = %s
                  AND session_status = 'OPEN'
                  AND parser_outcome = 'PENDING'
                """,
                (candidate.get("tracking_id"),),
            )
            if claim_cursor.rowcount != 1:
                claim_conn.rollback()
                summary["skipped_reopened"] += 1
                continue

            summary["claimed"] += 1

            root_id = int(candidate.get("root_message_log_id"))
            group_content = _build_whatsapp_submission_window_content(
                claim_cursor,
                root_id,
            )

            root_result = classify_submission_window(
                cursor=claim_cursor,
                root_message_log_id=root_id,
                sender_mobile=candidate.get("sender_mobile"),
                combined_message_body=group_content.get("raw_message_text") or "",
                attachment_count=group_content.get("attachment_count") or 0,
                channel_thread_id=None,
            ) or {}

            claim_conn.commit()
            summary["classified"] += 1

            whatsapp_parser_logger.info(
                "WHATSAPP_SUBMISSION_WINDOW_CLASSIFIED tracking_id=%s root_id=%s parse_status=%s message_type=%s group_rows=%s attachments=%s",
                candidate.get("tracking_id"),
                root_id,
                root_result.get("parse_status"),
                root_result.get("message_type"),
                len(group_content.get("rows") or []),
                group_content.get("attachment_count") or 0,
            )

        except Exception as exc:
            if claim_conn:
                claim_conn.rollback()
            summary["failed"] += 1
            whatsapp_parser_logger.exception(
                "WHATSAPP_SUBMISSION_WINDOW_CLASSIFY_FAILED tracking_id=%s root_id=%s error=%s",
                candidate.get("tracking_id"),
                candidate.get("root_message_log_id"),
                exc,
            )
        finally:
            if claim_cursor:
                claim_cursor.close()
            if claim_conn:
                claim_conn.close()

    return summary


# ============================================================
# EXISTING QUEUE PARSER LOGIC
# ============================================================

def _fetch_pending_channel_messages(cursor, batch_size=50):
    sql = """
        SELECT
            id,
            channel_code,
            channel_message_id,
            channel_thread_id,
            sender_id,
            sender_name,
            sender_mobile,
            sender_email,
            subject_line,
            raw_message_text,
            raw_payload_json,
            attachment_count,
            attachment_json,
            received_on,
            parsed_flag,
            parse_status,
            message_type,
            spam_flag,
            junk_flag,
            duplicate_flag,
            linked_triage_id,
            linked_queue_id,
            remarks,
            created_on,
            updated_on
        FROM channel_message_log
        WHERE COALESCE(parsed_flag, 0) = 0
          AND (
                COALESCE(parse_status, 'PENDING') IN ('PENDING', 'READY_FOR_PARSE', 'LINKED')
                OR (
                    UPPER(TRIM(COALESCE(parse_status, ''))) = 'MANUAL_REVIEW'
                    AND UPPER(TRIM(COALESCE(message_type, ''))) = 'ATTACHMENT_ONLY'
                    AND COALESCE(channel_thread_id, '') = ''
                    AND EXISTS (
                        SELECT 1
                        FROM channel_message_log structured_msg
                        WHERE structured_msg.id <> channel_message_log.id
                          AND UPPER(TRIM(COALESCE(structured_msg.channel_code, ''))) = 'WHATSAPP'
                          AND UPPER(TRIM(COALESCE(structured_msg.message_type, ''))) = 'STRUCTURED_REQUEST'
                          AND COALESCE(structured_msg.channel_thread_id, '') <> ''
                          AND structured_msg.linked_queue_id IS NOT NULL
                          AND REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(structured_msg.sender_mobile, ''), '+', ''), '-', ''), ' ', ''), CHAR(9), '')
                              = REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(channel_message_log.sender_mobile, ''), '+', ''), '-', ''), ' ', ''), CHAR(9), '')
                          AND ABS(TIMESTAMPDIFF(
                                  SECOND,
                                  structured_msg.received_on,
                                  channel_message_log.received_on
                              )) <= %s
                    )
                )
              )
          AND COALESCE(junk_flag, 0) = 0
          AND COALESCE(duplicate_flag, 0) = 0
          AND NOT (
                UPPER(TRIM(COALESCE(channel_code, ''))) = 'WHATSAPP'
                AND UPPER(TRIM(COALESCE(parse_status, 'PENDING'))) = 'PENDING'
                AND EXISTS (
                    SELECT 1
                    FROM whatsapp_submission_tracking wst
                    WHERE wst.parser_outcome = 'PENDING'
                      AND wst.session_status IN ('OPEN', 'PROCESSING')
                      AND (
                            wst.channel_message_log_id = channel_message_log.id
                            OR wst.channel_message_log_id = channel_message_log.linked_triage_id
                          )
                )
              )
        ORDER BY
            CASE
                WHEN UPPER(TRIM(COALESCE(message_type, ''))) = 'STRUCTURED_REQUEST' THEN 0
                WHEN UPPER(TRIM(COALESCE(message_type, ''))) = 'ATTACHMENT_ONLY' THEN 1
                ELSE 2
            END ASC,
            received_on ASC,
            id ASC
        LIMIT %s
    """
    cursor.execute(
        sql,
        (
            int(WHATSAPP_ATTACHMENT_GROUPING_SECONDS),
            batch_size,
        ),
    )
    return cursor.fetchall() or []


def _reset_stale_processing_messages(cursor, stale_minutes=10):
    """
    Reset rows left in PARSING by a killed Flask/debug session.

    This is intentionally conservative: only unparsed rows older than
    stale_minutes are reset, so a currently running parser job is not touched.
    """
    cursor.execute(
        """
        UPDATE channel_message_log
        SET parse_status = 'PENDING',
            remarks = 'Auto reset: stale PARSING row from previous parser run',
            updated_on = NOW()
        WHERE COALESCE(parsed_flag, 0) = 0
          AND UPPER(COALESCE(parse_status, '')) = 'PARSING'
          AND COALESCE(updated_on, created_on, received_on, NOW() - INTERVAL 1 DAY) < DATE_SUB(NOW(), INTERVAL %s MINUTE)
        """,
        (stale_minutes,),
    )
    return int(cursor.rowcount or 0)


def _fetch_queue_context_by_id(cursor, operation_queue_id):
    if not operation_queue_id:
        return {}

    cursor.execute(
        """
        SELECT
            id,
            channel_thread_id,
            channel_message_id,
            wrkflw_status,
            assigned_to
        FROM operation_queue_master
        WHERE id = %s
        LIMIT 1
        """,
        (operation_queue_id,),
    )
    return cursor.fetchone() or {}


def _attachment_already_exists(
    cursor,
    channel_message_log_id,
    channel_message_id,
    file_name,
    file_path,
    linked_queue_id=None,
):
    """
    Check duplicate using the same logical key as the database unique index.

    IMPORTANT:
    The DB error is on uq_opq_attachment_msglog_msg_file_path, which is
    channel_message_log_id + channel_message_id + file_path.
    file_name is intentionally not part of the blocking check because the same
    WhatsApp media may reach here with a slightly different generated filename
    while still having the same message/log/file_path combination.
    """
    # A Manual Review root may already contain an attachment collected from a
    # child log row.  The database unique key includes the source log id, so it
    # cannot by itself prevent the same physical file from appearing twice
    # under one queue record.  Check the queue/file path first.
    if linked_queue_id and file_path:
        cursor.execute(
            """
            SELECT id
            FROM operation_queue_attachment
            WHERE operation_queue_id = %s
              AND COALESCE(file_path, '') = COALESCE(%s, '')
            LIMIT 1
            """,
            (linked_queue_id, file_path),
        )
        if cursor.fetchone() is not None:
            return True

    sql = """
        SELECT id
        FROM operation_queue_attachment
        WHERE channel_message_log_id = %s
          AND channel_message_id = %s
          AND COALESCE(file_path, '') = COALESCE(%s, '')
        LIMIT 1
    """
    cursor.execute(
        sql,
        (
            channel_message_log_id,
            channel_message_id,
            file_path,
        ),
    )
    return cursor.fetchone() is not None


def _safe_attachment_value(item, *keys):
    """
    Return first non-empty attachment value.
    Supports different keys returned by webhook/media service.
    """

    for key in keys:
        value = item.get(key)
        if value is not None and str(value).strip() != "":
            return value

    return None


def _normalise_attachment_item(item):
    """
    Normalise attachment_json item before inserting into operation_queue_attachment.
    This ensures file_path is captured even if media_service returns path/local_path/saved_path.
    """

    if not item:
        return {}

    file_name = _safe_attachment_value(
        item,
        "file_name",
        "filename",
        "name",
        "original_filename",
    )

    file_path = _safe_attachment_value(
        item,
        "file_path",
        "path",
        "local_path",
        "saved_path",
        "relative_path",
        "url",
    )

    file_ext = _safe_attachment_value(
        item,
        "file_ext",
        "extension",
        "ext",
    )

    if not file_ext and file_name and "." in str(file_name):
        file_ext = str(file_name).rsplit(".", 1)[-1].lower()

    return {
        "file_name": file_name,
        "file_ext": file_ext,
        "mime_type": _safe_attachment_value(item, "mime_type", "content_type"),
        "file_size": _safe_attachment_value(item, "file_size", "size", "size_bytes"),
        "file_path": file_path,
        "remarks": item.get("remarks") or "Copied from channel_message_log attachment_json",
    }


def _insert_operation_queue_attachments(cursor, row, linked_queue_id, channel_thread_id=None):
    attachments = parse_attachment_json(row.get("attachment_json"))

    if not attachments:
        return 0

    if not channel_thread_id:
        channel_thread_id = row.get("channel_thread_id")

    if not channel_thread_id and linked_queue_id:
        queue_context = _fetch_queue_context_by_id(cursor, linked_queue_id)
        channel_thread_id = queue_context.get("channel_thread_id")

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
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
        ON DUPLICATE KEY UPDATE
            operation_queue_id = COALESCE(operation_queue_id, VALUES(operation_queue_id)),
            channel_thread_id = COALESCE(channel_thread_id, VALUES(channel_thread_id)),
            file_name = COALESCE(file_name, VALUES(file_name)),
            file_ext = COALESCE(file_ext, VALUES(file_ext)),
            mime_type = COALESCE(mime_type, VALUES(mime_type)),
            file_size = COALESCE(file_size, VALUES(file_size)),
            remarks = COALESCE(remarks, VALUES(remarks)),
            updated_on = NOW()
    """

    inserted_count = 0

    for item in attachments:
        normalized_item = _normalise_attachment_item(item)

        file_name = normalized_item.get("file_name")
        file_path = normalized_item.get("file_path")

        if not file_name and not file_path:
            whatsapp_parser_logger.warning(
                "QUEUE_ATTACHMENT_SKIPPED_NO_FILE_INFO channel_message_log_id=%s channel_message_id=%s item=%s",
                row.get("id"),
                row.get("channel_message_id"),
                item,
            )
            continue

        if _attachment_already_exists(
            cursor=cursor,
            channel_message_log_id=row.get("id"),
            channel_message_id=row.get("channel_message_id"),
            file_name=file_name,
            file_path=file_path,
            linked_queue_id=linked_queue_id,
        ):
            continue

        cursor.execute(
            sql,
            (
                linked_queue_id,
                channel_thread_id,
                row.get("id"),
                row.get("channel_message_id"),
                file_name,
                normalized_item.get("file_ext"),
                normalized_item.get("mime_type"),
                normalized_item.get("file_size"),
                file_path,
                normalized_item.get("remarks"),
            ),
        )

        if cursor.rowcount == 1:
            inserted_count += 1
            log_action = "QUEUE_ATTACHMENT_INSERTED"
        else:
            log_action = "QUEUE_ATTACHMENT_DUPLICATE_IGNORED"

        whatsapp_parser_logger.info(
            "%s channel_message_log_id=%s operation_queue_id=%s channel_thread_id=%s file_name=%s file_path=%s",
            log_action,
            row.get("id"),
            linked_queue_id,
            channel_thread_id,
            file_name,
            file_path,
        )

    return inserted_count


def _update_channel_message_log_success(
    cursor,
    channel_message_log_id,
    linked_queue_id,
    channel_thread_id,
    remarks,
    parse_status="PARSED",
):
    sql = """
        UPDATE channel_message_log
        SET
            parsed_flag = 1,
            parse_status = %s,
            linked_queue_id = %s,
            channel_thread_id = %s,
            remarks = %s,
            updated_on = NOW()
        WHERE id = %s
    """
    cursor.execute(
        sql,
        (
            parse_status or "PARSED",
            linked_queue_id,
            channel_thread_id,
            remarks,
            channel_message_log_id,
        ),
    )


def _update_channel_message_log_manual_review(
    cursor,
    channel_message_log_id,
    remarks,
    linked_queue_id=None,
):
    reason = str(
        remarks
        or "WhatsApp sender could not be mapped to a valid Agency. Manual review is required for Agent assignment."
    )[:500]

    sql = """
        UPDATE channel_message_log
        SET
            parsed_flag = 0,
            parse_status = 'MANUAL_REVIEW',
            requires_manual_review = 1,
            junk_flag = 0,
            linked_queue_id = %s,
            channel_thread_id = NULL,
            classifier_status = 'CLASSIFIED',
            classifier_reason = %s,
            thread_resolution_status = 'NO_THREAD_FOUND',
            remarks = %s,
            updated_on = NOW()
        WHERE id = %s
    """
    cursor.execute(
        sql,
        (
            linked_queue_id,
            reason,
            reason,
            channel_message_log_id,
        ),
    )


def _update_channel_message_log_failed(cursor, channel_message_log_id, error_message):
    sql = """
        UPDATE channel_message_log
        SET
            parse_status = 'FAILED',
            remarks = %s,
            updated_on = NOW()
        WHERE id = %s
    """
    cursor.execute(sql, (str(error_message)[:500], channel_message_log_id))


def _update_channel_message_log_junk(cursor, channel_message_log_id, remarks):
    sql = """
        UPDATE channel_message_log
        SET
            parsed_flag = 1,
            parse_status = 'JUNK',
            junk_flag = 1,
            remarks = %s,
            updated_on = NOW()
        WHERE id = %s
    """
    cursor.execute(sql, (str(remarks or "Follow-up message marked as junk.")[:500], channel_message_log_id))


def _update_channel_message_log_skipped_failed_parse(
    cursor,
    channel_message_log_id,
    channel_thread_id,
    remarks,
):
    sql = """
        UPDATE channel_message_log
        SET
            parsed_flag = 1,
            parse_status = 'FAILED',
            linked_queue_id = NULL,
            channel_thread_id = %s,
            remarks = %s,
            updated_on = NOW()
        WHERE id = %s
    """
    cursor.execute(
        sql,
        (
            channel_thread_id,
            remarks or "Parsing failed. No row created in operation_queue_master.",
            channel_message_log_id,
        ),
    )


def _update_channel_message_log_skipped_no_change(
    cursor,
    channel_message_log_id,
    channel_thread_id,
    remarks,
):
    sql = """
        UPDATE channel_message_log
        SET
            parsed_flag = 1,
            parse_status = 'PARSED',
            linked_queue_id = NULL,
            channel_thread_id = %s,
            remarks = %s,
            updated_on = NOW()
        WHERE id = %s
    """
    cursor.execute(
        sql,
        (
            channel_thread_id,
            remarks or "No configured field change detected and no attachment change detected. Insert skipped.",
            channel_message_log_id,
        ),
    )


def _process_linked_channel_message(conn, row):
    cursor = None
    try:
        cursor = conn.cursor(dictionary=True)

        linked_queue_id = row.get("linked_queue_id")
        if not linked_queue_id:
            raise Exception("LINKED message does not have linked_queue_id.")

        queue_context = _fetch_queue_context_by_id(cursor, linked_queue_id)
        channel_thread_id = row.get("channel_thread_id") or queue_context.get("channel_thread_id")

        if not channel_thread_id:
            raise Exception("Unable to resolve channel_thread_id for LINKED message.")

        inserted_attachment_count = _insert_operation_queue_attachments(
            cursor=cursor,
            row=row,
            linked_queue_id=linked_queue_id,
            channel_thread_id=channel_thread_id,
        )

        _update_channel_message_log_success(
            cursor=cursor,
            channel_message_log_id=row.get("id"),
            linked_queue_id=linked_queue_id,
            channel_thread_id=channel_thread_id,
            remarks="Linked message processed successfully.",
            parse_status="LINKED",
        )

        conn.commit()

        whatsapp_parser_logger.info(
            "QUEUE_PARSER_LINKED_MESSAGE_PROCESSED channel_message_log_id=%s linked_queue_id=%s attachments_inserted=%s channel_thread_id=%s",
            row.get("id"),
            linked_queue_id,
            inserted_attachment_count,
            channel_thread_id,
        )
        return 1

    except Exception as e:
        conn.rollback()

        try:
            fail_cursor = conn.cursor()
            _update_channel_message_log_failed(fail_cursor, row.get("id"), e)
            conn.commit()
            fail_cursor.close()
        except Exception:
            conn.rollback()

        whatsapp_parser_logger.exception(
            "QUEUE_PARSER_LINKED_MESSAGE_FAILED channel_message_log_id=%s error=%s",
            row.get("id"),
            e,
        )
        return 0

    finally:
        if cursor:
            cursor.close()


def _update_channel_message_log_status_safely(channel_message_log_id, parse_status, remarks, parsed_flag=None, junk_flag=None, linked_queue_id=None, channel_thread_id=None):
    """
    Open a short independent transaction only for channel_message_log status updates.
    This prevents the scheduler from holding locks while operation_queue_service.py
    performs queue insert / attachment insert / channel log update in its own transaction.
    """
    if not channel_message_log_id:
        return 0

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        set_parts = ["parse_status = %s", "remarks = %s", "updated_on = NOW()"]
        values = [parse_status, str(remarks or "")[:500]]

        if parsed_flag is not None:
            set_parts.append("parsed_flag = %s")
            values.append(parsed_flag)
        if junk_flag is not None:
            set_parts.append("junk_flag = %s")
            values.append(junk_flag)
        if linked_queue_id is not None:
            set_parts.append("linked_queue_id = %s")
            values.append(linked_queue_id)
        if channel_thread_id is not None:
            set_parts.append("channel_thread_id = %s")
            values.append(channel_thread_id)

        values.append(channel_message_log_id)
        cursor.execute(
            f"""
            UPDATE channel_message_log
            SET {", ".join(set_parts)}
            WHERE id = %s
            """,
            tuple(values),
        )
        conn.commit()
        return int(cursor.rowcount or 0)

    except Exception as e:
        if conn:
            conn.rollback()
        whatsapp_parser_logger.exception(
            "QUEUE_PARSER_SAFE_STATUS_UPDATE_FAILED channel_message_log_id=%s error=%s",
            channel_message_log_id,
            e,
        )
        return 0

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()



def _find_recent_structured_parent_for_attachment(
    cursor,
    row,
    window_seconds=WHATSAPP_ATTACHMENT_GROUPING_SECONDS,
):
    """
    Resolve the preceding structured WhatsApp request for an attachment-only
    message when Meta has delivered multiple media items as separate message
    events.

    Existing queue/thread resolution remains unchanged. This helper is used only
    for ATTACHMENT_ONLY rows that were intentionally left PENDING by the
    classifier because a structured request from the same sender arrived within
    the configured 60-second window.
    """
    row = row or {}

    row_id = row.get("id")
    sender_mobile = row.get("sender_mobile")
    received_on = row.get("received_on")

    if not row_id or not sender_mobile or not received_on:
        return {}

    cursor.execute(
        """
        SELECT
            previous_msg.id AS previous_message_log_id,
            previous_msg.channel_message_id AS previous_channel_message_id,
            previous_msg.channel_thread_id AS message_thread_id,
            previous_msg.linked_queue_id AS message_linked_queue_id,
            previous_msg.parse_status AS previous_parse_status,
            previous_msg.message_type AS previous_message_type,
            previous_msg.received_on AS previous_received_on,
            oq.id AS operation_queue_id,
            oq.channel_thread_id AS queue_thread_id,
            oq.wrkflw_status AS queue_workflow_status
        FROM channel_message_log previous_msg
        LEFT JOIN operation_queue_master oq
            ON (
                oq.id = previous_msg.linked_queue_id
                OR oq.channel_message_log_id = previous_msg.id
            )
        WHERE previous_msg.id <> %s
          AND UPPER(TRIM(COALESCE(previous_msg.channel_code, ''))) = 'WHATSAPP'
          AND REPLACE(
                  REPLACE(
                      REPLACE(
                          REPLACE(COALESCE(previous_msg.sender_mobile, ''), '+', ''),
                          '-',
                          ''
                      ),
                      ' ',
                      ''
                  ),
                  CHAR(9),
                  ''
              )
              = REPLACE(
                  REPLACE(
                      REPLACE(
                          REPLACE(COALESCE(%s, ''), '+', ''),
                          '-',
                          ''
                      ),
                      ' ',
                      ''
                  ),
                  CHAR(9),
                  ''
              )
          AND ABS(TIMESTAMPDIFF(
                  SECOND,
                  previous_msg.received_on,
                  %s
              )) <= %s
          AND UPPER(TRIM(COALESCE(previous_msg.message_type, '')))
              = 'STRUCTURED_REQUEST'
          AND COALESCE(previous_msg.junk_flag, 0) = 0
          AND COALESCE(previous_msg.duplicate_flag, 0) = 0
        ORDER BY
            ABS(TIMESTAMPDIFF(
                SECOND,
                previous_msg.received_on,
                %s
            )) ASC,
            previous_msg.id ASC,
            oq.id DESC
        LIMIT 1
        """,
        (
            row_id,
            sender_mobile,
            received_on,
            int(window_seconds or WHATSAPP_ATTACHMENT_GROUPING_SECONDS),
            received_on,
        ),
    )

    parent = cursor.fetchone() or {}

    if not parent:
        return {}

    linked_queue_id = (
        parent.get("operation_queue_id")
        or parent.get("message_linked_queue_id")
    )
    channel_thread_id = (
        parent.get("queue_thread_id")
        or parent.get("message_thread_id")
    )

    return {
        **parent,
        "linked_queue_id": linked_queue_id,
        "channel_thread_id": channel_thread_id,
    }


def _attachment_grouping_window_is_open(
    row,
    window_seconds=WHATSAPP_ATTACHMENT_GROUPING_SECONDS,
):
    """Return True while a matching structured event can still arrive."""
    row = row or {}
    received_on = row.get("received_on")
    if not received_on:
        return True

    if isinstance(received_on, str):
        parsed_value = None
        for date_format in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
            "%Y-%m-%dT%H:%M:%S.%f",
            "%Y-%m-%dT%H:%M:%S",
        ):
            try:
                parsed_value = datetime.strptime(received_on, date_format)
                break
            except Exception:
                continue
        received_on = parsed_value

    if not isinstance(received_on, datetime):
        return True

    return datetime.now() <= received_on + timedelta(
        seconds=int(window_seconds or WHATSAPP_ATTACHMENT_GROUPING_SECONDS)
    )


def _prepare_pending_attachment_for_linked_processing(row):
    """
    Convert a waiting ATTACHMENT_ONLY row to LINKED once its preceding
    structured request has created an operation queue row.

    Return values:
    - {"status": "LINKED", "row": linked_row}
    - {"status": "MANUAL_GROUPED", "row": grouped_row}
    - {"status": "WAIT"}
    - {"status": "MANUAL_REVIEW"}
    - {"status": "NOT_APPLICABLE"}

    MANUAL_GROUPED means the attachment-only channel_message_log row was
    grouped to a preceding Manual Review parent through linked_triage_id.
    channel_thread_id intentionally remains NULL until Manual Review is resolved.
    """
    row = dict(row or {})

    parse_status = str(row.get("parse_status") or "").strip().upper()
    message_type = str(row.get("message_type") or "").strip().upper()

    if message_type != "ATTACHMENT_ONLY" or parse_status not in {
        "PENDING",
        "MANUAL_REVIEW",
    }:
        return {"status": "NOT_APPLICABLE", "row": row}

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        parent = _find_recent_structured_parent_for_attachment(
            cursor=cursor,
            row=row,
            window_seconds=WHATSAPP_ATTACHMENT_GROUPING_SECONDS,
        )

        if not parent:
            # Do not send an attachment to Manual Review while the configured
            # 60-second window is still open.  Meta may deliver the structured
            # message after the attachment event.
            if _attachment_grouping_window_is_open(
                row,
                WHATSAPP_ATTACHMENT_GROUPING_SECONDS,
            ):
                return {"status": "WAIT", "row": row}
            return {"status": "MANUAL_REVIEW", "row": row}

        linked_queue_id = parent.get("linked_queue_id")
        channel_thread_id = parent.get("channel_thread_id")
        parent_parse_status = str(
            parent.get("previous_parse_status") or ""
        ).strip().upper()

        if linked_queue_id and channel_thread_id:
            cursor.execute(
                """
                UPDATE channel_message_log
                SET
                    parse_status = 'LINKED',
                    linked_triage_id = COALESCE(linked_triage_id, %s),
                    linked_queue_id = %s,
                    channel_thread_id = %s,
                    classifier_status = 'CLASSIFIED',
                    classifier_reason = %s,
                    thread_resolution_status = 'EXISTING_THREAD',
                    requires_manual_review = 0,
                    junk_flag = 0,
                    remarks = %s,
                    updated_on = NOW()
                WHERE id = %s
                """,
                (
                    parent.get("previous_message_log_id"),
                    linked_queue_id,
                    channel_thread_id,
                    (
                        "Attachment-only message linked to the preceding "
                        "structured WhatsApp request received within 60 seconds"
                    ),
                    (
                        "Linked to preceding structured WhatsApp request "
                        "within 60-second grouping window."
                    ),
                    row.get("id"),
                ),
            )
            conn.commit()

            linked_row = dict(row)
            linked_row["parse_status"] = "LINKED"
            linked_row["linked_triage_id"] = parent.get(
                "previous_message_log_id"
            )
            linked_row["linked_queue_id"] = linked_queue_id
            linked_row["channel_thread_id"] = channel_thread_id

            return {"status": "LINKED", "row": linked_row}

        if parent_parse_status == "MANUAL_REVIEW":
            parent_message_log_id = parent.get("previous_message_log_id")

            if not parent_message_log_id:
                return {"status": "MANUAL_REVIEW", "row": row}

            cursor.execute(
                """
                UPDATE channel_message_log
                SET
                    parsed_flag = 1,
                    parse_status = 'LINKED',
                    linked_triage_id = %s,
                    linked_queue_id = %s,
                    channel_thread_id = NULL,
                    classifier_status = 'CLASSIFIED',
                    classifier_reason = %s,
                    thread_resolution_status = 'MANUAL_LINKED',
                    requires_manual_review = 0,
                    junk_flag = 0,
                    remarks = %s,
                    updated_on = NOW()
                WHERE id = %s
                """,
                (
                    parent_message_log_id,
                    linked_queue_id,
                    (
                        "Attachment-only message grouped with the preceding "
                        "Manual Review request received within 60 seconds"
                    ),
                    (
                        "Grouped with preceding Manual Review request through "
                        "linked_triage_id within 60-second grouping window."
                    ),
                    row.get("id"),
                ),
            )

            # Keep one Manual Review record while making every attachment in
            # the linked group visible under that record.  No thread id is
            # assigned at this stage.
            if linked_queue_id:
                grouped_row_for_attachment = dict(row)
                grouped_row_for_attachment["linked_queue_id"] = linked_queue_id
                grouped_row_for_attachment["channel_thread_id"] = None
                _insert_operation_queue_attachments(
                    cursor=cursor,
                    row=grouped_row_for_attachment,
                    linked_queue_id=linked_queue_id,
                    channel_thread_id=None,
                )

            conn.commit()

            grouped_row = dict(row)
            grouped_row["parsed_flag"] = 1
            grouped_row["parse_status"] = "LINKED"
            grouped_row["linked_triage_id"] = parent_message_log_id
            grouped_row["linked_queue_id"] = linked_queue_id
            grouped_row["channel_thread_id"] = None
            grouped_row["thread_resolution_status"] = "MANUAL_LINKED"
            grouped_row["requires_manual_review"] = 0

            return {"status": "MANUAL_GROUPED", "row": grouped_row}

        if parent_parse_status in {
            "PENDING",
            "READY_FOR_PARSE",
            "PARSING",
            "",
        }:
            return {"status": "WAIT", "row": row}

        return {"status": "MANUAL_REVIEW", "row": row}

    except Exception as exc:
        if conn:
            conn.rollback()

        whatsapp_parser_logger.exception(
            "QUEUE_ATTACHMENT_PARENT_RESOLUTION_FAILED "
            "channel_message_log_id=%s error=%s",
            row.get("id"),
            exc,
        )
        return {"status": "WAIT", "row": row}

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _restore_waiting_attachment_to_pending(row):
    row = row or {}

    _update_channel_message_log_status_safely(
        channel_message_log_id=row.get("id"),
        parse_status="PENDING",
        remarks=(
            "Waiting for preceding structured WhatsApp request to finish "
            "queue processing within the 60-second grouping flow."
        ),
        parsed_flag=0,
    )


def _move_unresolved_attachment_to_manual_review(row):
    row = row or {}

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor()

        _update_channel_message_log_manual_review(
            cursor=cursor,
            channel_message_log_id=row.get("id"),
            remarks=(
                "Attachment received but no valid preceding structured "
                "WhatsApp request or existing queue/thread was found "
                "within the 60-second grouping window."
            ),
            linked_queue_id=None,
        )
        conn.commit()

    except Exception as exc:
        if conn:
            conn.rollback()

        whatsapp_parser_logger.exception(
            "QUEUE_ATTACHMENT_MANUAL_REVIEW_UPDATE_FAILED "
            "channel_message_log_id=%s error=%s",
            row.get("id"),
            exc,
        )

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _claim_channel_message_for_processing(row):
    """
    Claim exactly one channel_message_log row in a very short transaction.

    Why this fixes lock timeout:
    - The scheduler commits the PARSING status immediately.
    - It does not keep this transaction open while operation_queue_service.py runs.
    - Another scheduler pass cannot pick the same row at the same time.
    """
    row = row or {}
    row_id = row.get("id")
    if not row_id:
        return False

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE channel_message_log
            SET parse_status = 'PARSING',
                remarks = 'Picked by queue parser scheduler',
                updated_on = NOW()
            WHERE id = %s
              AND COALESCE(parsed_flag, 0) = 0
              AND (
                    COALESCE(parse_status, 'PENDING') IN ('PENDING', 'READY_FOR_PARSE', 'LINKED')
                    OR (
                        UPPER(TRIM(COALESCE(parse_status, ''))) = 'MANUAL_REVIEW'
                        AND UPPER(TRIM(COALESCE(message_type, ''))) = 'ATTACHMENT_ONLY'
                    )
                  )
              AND COALESCE(junk_flag, 0) = 0
              AND COALESCE(duplicate_flag, 0) = 0
            """,
            (row_id,),
        )
        claimed = int(cursor.rowcount or 0) == 1
        conn.commit()
        return claimed

    except Exception as e:
        if conn:
            conn.rollback()
        whatsapp_parser_logger.exception(
            "QUEUE_PARSER_CLAIM_FAILED channel_message_log_id=%s error=%s",
            row_id,
            e,
        )
        return False

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()



def _load_whatsapp_group_payload_for_parser(row):
    """
    Return combined text/media for a WhatsApp submission root.

    Non-WhatsApp rows keep their original behavior unchanged.
    """
    row = row or {}
    if str(row.get("channel_code") or "").strip().upper() != "WHATSAPP":
        return {
            "raw_message_text": row.get("raw_message_text") or "",
            "attachment_count": int(row.get("attachment_count") or 0),
            "attachment_json": row.get("attachment_json"),
        }

    root_id = row.get("linked_triage_id") or row.get("id")
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        return _build_whatsapp_submission_window_content(cursor, root_id)
    except Exception:
        whatsapp_parser_logger.exception(
            "WHATSAPP_SUBMISSION_GROUP_LOAD_FAILED root_id=%s",
            root_id,
        )
        return {
            "raw_message_text": row.get("raw_message_text") or "",
            "attachment_count": int(row.get("attachment_count") or 0),
            "attachment_json": row.get("attachment_json"),
        }
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def _process_one_channel_message(row):
    """
    Process one WhatsApp channel message without holding an outer scheduler transaction.

    Important design rule:
    operation_queue_service.py owns normal queue insert, attachment insert and
    channel_message_log success update. The scheduler only claims the row before
    processing and records failure/junk statuses when the service returns before
    doing DB updates. This removes the old duplicate flow that caused lock waits.
    """
    row = row or {}

    if not _claim_channel_message_for_processing(row):
        whatsapp_parser_logger.info(
            "QUEUE_PARSER_MESSAGE_SKIPPED_ALREADY_CLAIMED channel_message_log_id=%s",
            row.get("id"),
        )
        return 0

    try:
        parse_status = str(row.get("parse_status") or "").strip().upper()
        message_type = str(row.get("message_type") or "").strip().upper()

        if message_type == "ATTACHMENT_ONLY" and parse_status in {
            "PENDING",
            "MANUAL_REVIEW",
        }:
            preparation = _prepare_pending_attachment_for_linked_processing(row)
            preparation_status = preparation.get("status")
            prepared_row = preparation.get("row") or row

            if preparation_status == "LINKED":
                conn = None
                try:
                    conn = get_db_connection()
                    return _process_linked_channel_message(conn, prepared_row)
                finally:
                    if conn:
                        conn.close()

            if preparation_status == "MANUAL_GROUPED":
                whatsapp_parser_logger.info(
                    "QUEUE_ATTACHMENT_GROUPED_WITH_MANUAL_REVIEW_PARENT "
                    "channel_message_log_id=%s linked_triage_id=%s",
                    row.get("id"),
                    prepared_row.get("linked_triage_id"),
                )
                return 1

            if preparation_status == "WAIT":
                _restore_waiting_attachment_to_pending(row)
                whatsapp_parser_logger.info(
                    "QUEUE_ATTACHMENT_WAITING_FOR_PARENT "
                    "channel_message_log_id=%s",
                    row.get("id"),
                )
                return 0

            if preparation_status == "MANUAL_REVIEW":
                _move_unresolved_attachment_to_manual_review(row)
                whatsapp_parser_logger.info(
                    "QUEUE_ATTACHMENT_ROUTED_TO_MANUAL_REVIEW "
                    "channel_message_log_id=%s",
                    row.get("id"),
                )
                return 0

        # Keep existing LINKED non-followup flow because it only attaches a
        # message to an already-created operation queue row. Use a fresh short
        # connection so no scheduler-level transaction remains open.
        if parse_status == "LINKED" and message_type != "FOLLOWUP_TEXT":
            conn = None
            try:
                conn = get_db_connection()
                return _process_linked_channel_message(conn, row)
            finally:
                if conn:
                    conn.close()

        group_payload = _load_whatsapp_group_payload_for_parser(row)

        channel_meta = {
            "channel_message_log_id": row.get("id"),
            "channel_code": row.get("channel_code"),
            "channel_message_id": row.get("channel_message_id"),
            "channel_thread_id": row.get("channel_thread_id"),
            "sender_mobile": row.get("sender_mobile"),
            "sender_email": row.get("sender_email"),
            "sender_name": row.get("sender_name"),
            "sender_id": row.get("sender_id"),
            "subject_line": row.get("subject_line"),
            "attachment_count": group_payload.get("attachment_count") or 0,
            "attachment_json": group_payload.get("attachment_json"),
            "message_type": row.get("message_type"),
            "linked_queue_id": row.get("linked_queue_id"),
        }

        result = insert_into_operation_queue_master(
            raw_message_text=group_payload.get("raw_message_text") or "",
            channel_meta=channel_meta,
        ) or {}

        if result.get("manual_review"):
            payload = result.get("payload") or {}
            manual_review_remarks = (
                payload.get("manual_review_reason")
                or payload.get("classifier_reason")
                or payload.get("parse_remarks")
                or result.get("message")
                or "WhatsApp sender could not be mapped to a valid Agency. Manual review is required for Agent assignment."
            )

            if str(manual_review_remarks).strip().upper() in {"MANUAL REVIEW", "MANUAL_REVIEW"}:
                manual_review_remarks = (
                    "WhatsApp sender could not be mapped to a valid Agency. "
                    "Manual review is required for Agent assignment."
                )

            operation_queue_id = (
                result.get("operation_queue_master_id")
                or result.get("inserted_id")
                or result.get("linked_queue_id")
            )

            manual_conn = None
            manual_cursor = None
            try:
                manual_conn = get_db_connection()
                manual_cursor = manual_conn.cursor()
                _update_channel_message_log_manual_review(
                    cursor=manual_cursor,
                    channel_message_log_id=row.get("id"),
                    remarks=manual_review_remarks,
                    linked_queue_id=operation_queue_id,
                )
                manual_conn.commit()
            except Exception:
                if manual_conn:
                    manual_conn.rollback()
                raise
            finally:
                if manual_cursor:
                    manual_cursor.close()
                if manual_conn:
                    manual_conn.close()

            whatsapp_parser_logger.info(
                "QUEUE_PARSER_MESSAGE_ROUTED_TO_MANUAL_REVIEW channel_message_log_id=%s operation_queue_master_id=%s",
                row.get("id"),
                operation_queue_id,
            )
            return 1

        if result.get("success"):
            whatsapp_parser_logger.info(
                "QUEUE_PARSER_MESSAGE_PROCESSED_BY_SERVICE channel_message_log_id=%s operation_queue_master_id=%s channel_thread_id=%s",
                row.get("id"),
                result.get("operation_queue_master_id") or result.get("linked_queue_id"),
                result.get("channel_thread_id"),
            )
            return 1

        reason = str(result.get("reason") or "").strip()

        if reason == "parse_status_failed":
            payload = result.get("payload") or {}
            _update_channel_message_log_status_safely(
                channel_message_log_id=row.get("id"),
                parse_status="FAILED",
                remarks=payload.get("parse_remarks") or result.get("message") or "Parsing failed. No row created in operation_queue_master.",
                parsed_flag=1,
                linked_queue_id=None,
                channel_thread_id=payload.get("channel_thread_id"),
            )
            return 0

        if reason == "followup_junk":
            _update_channel_message_log_status_safely(
                channel_message_log_id=row.get("id"),
                parse_status="JUNK",
                remarks=result.get("message") or "Follow-up message marked as junk.",
                parsed_flag=1,
                junk_flag=1,
                channel_thread_id=result.get("channel_thread_id") or row.get("channel_thread_id"),
            )

            # Do not send the JUNK closure here. The WA submission tracking
            # service sends the terminal negative outcome with the correct
            # WA Ref # and can defer older outcomes while another chat is open.

            return 0

        _update_channel_message_log_status_safely(
            channel_message_log_id=row.get("id"),
            parse_status="FAILED",
            remarks=result.get("error") or result.get("message") or "Unknown queue insert error.",
            parsed_flag=0,
            channel_thread_id=result.get("channel_thread_id") or row.get("channel_thread_id"),
        )
        return 0

    except Exception as e:
        _update_channel_message_log_status_safely(
            channel_message_log_id=row.get("id"),
            parse_status="FAILED",
            remarks=e,
            parsed_flag=0,
            channel_thread_id=row.get("channel_thread_id"),
        )
        whatsapp_parser_logger.exception(
            "QUEUE_PARSER_MESSAGE_FAILED channel_message_log_id=%s error=%s",
            row.get("id"),
            e,
        )
        return 0

def _run_submission_acknowledgement_after_parser():
    """
    Evaluate WhatsApp submission outcomes after the existing queue parser has
    finished its current batch.

    The tracking service evaluates the completed submission window.
    SUCCESS sends Patient + original Date/Time + channel_thread_id.
    MANUAL_REVIEW/JUNK send one WA Ref for the whole window.
    """
    try:
        summary = process_pending_submission_acknowledgements() or {}

        whatsapp_parser_logger.info(
            "WHATSAPP_SUBMISSION_TRACKING_POST_PARSER_COMPLETED "
            "success_detected=%s junk_detected=%s current_sent=%s "
            "processing_closed=%s deferred_sent=%s deferred_open=%s",
            summary.get("success_detected", 0),
            summary.get("junk_detected", 0),
            summary.get("current_sent", 0),
            summary.get("processing_sessions_closed", 0),
            summary.get("deferred_sent", 0),
            summary.get("deferred_open_conversation", 0),
        )

        return summary

    except Exception as e:
        whatsapp_parser_logger.exception(
            "WHATSAPP_SUBMISSION_TRACKING_POST_PARSER_FAILED error=%s",
            e,
        )
        return {}


def _run_queue_parser_job_inner():
    conn = None
    cursor = None

    try:
        whatsapp_parser_logger.info("QUEUE_PARSER_JOB_TRIGGERED")

        # Fetch candidates only, then close immediately.
        # Do not hold this connection while processing rows.
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        reset_count = _reset_stale_processing_messages(cursor, stale_minutes=10)
        if reset_count:
            conn.commit()
            whatsapp_parser_logger.warning(
                "QUEUE_PARSER_STALE_PARSING_RESET count=%s",
                reset_count,
            )

        # Do this before fetching queue rows. Fresh WhatsApp PENDING events stay
        # protected from individual parsing until the quiet window has closed.
        cursor.close()
        conn.close()
        cursor = None
        conn = None

        stale_window_count = _reset_stale_whatsapp_submission_windows(
            stale_minutes=10,
        )
        window_summary = _classify_ready_whatsapp_submission_windows(
            batch_size=20,
        ) or {}

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        pending_rows = _fetch_pending_channel_messages(cursor, batch_size=5)
        whatsapp_parser_logger.info(
            "QUEUE_PARSER_PENDING_ROWS count=%s",
            len(pending_rows),
        )

        cursor.close()
        conn.close()
        cursor = None
        conn = None

        processed_count = 0
        for row in pending_rows:
            processed_count += _process_one_channel_message(row)

        # Post-parser acknowledgement is intentionally separate from queue
        # creation/Manual Review/thread resolution.  It only evaluates settled
        # submissions after the current parser batch has completed.
        _run_submission_acknowledgement_after_parser()

        whatsapp_parser_logger.info(
            "QUEUE_PARSER_JOB_COMPLETED processed_count=%s",
            processed_count,
        )

        return {
            "monitoring_status": "SUCCESS",
            "records_read": len(pending_rows),
            "records_processed": processed_count,
            "records_success": processed_count,
            "records_failed": 0,
            "records_skipped": max(len(pending_rows) - processed_count, 0),
            "remarks": (
                "Queue Parser completed. "
                f"pending={len(pending_rows)}, processed={processed_count}, "
                f"stale_reset={reset_count}, stale_window_reset={stale_window_count}, "
                f"windows_classified={window_summary.get('classified', 0)}."
            ),
        }

    except Exception as e:
        whatsapp_parser_logger.exception("QUEUE_PARSER_JOB_FAILED error=%s", e)
        return {
            "monitoring_status": "FAILED",
            "records_read": 0,
            "records_processed": 0,
            "records_success": 0,
            "records_failed": 0,
            "records_skipped": 0,
            "error_message": str(e),
            "stack_trace": traceback.format_exc(),
            "remarks": "Queue Parser failed.",
        }

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def run_queue_parser_job():
    global _scheduler_app, _scheduler_instance

    if _scheduler_app is None:
        whatsapp_parser_logger.error(
            "QUEUE_PARSER_JOB_FAILED Flask app reference not available"
        )
        return

    with _scheduler_app.app_context():
        return run_monitored_job(
            job_code="QUEUE_PARSER",
            job_callable=_run_queue_parser_job_inner,
            scheduler=_scheduler_instance,
            trigger_type="SCHEDULED",
        )


# ============================================================
# SLA ENGINE LOGIC
# ============================================================

def _table_exists(cursor, table_name):
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def _get_table_columns(cursor, table_name):
    try:
        cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
        rows = cursor.fetchall() or []
        return {row["Field"] for row in rows}
    except Exception:
        return set()


def _safe_str(value):
    if value is None:
        return ""
    return str(value).strip()


def _norm_upper(value):
    return _safe_str(value).upper()


def _safe_int(value, default=0):
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(value)
    except Exception:
        return default


def _parse_dt(value):
    if value is None:
        return None

    if isinstance(value, datetime):
        return value

    for fmt in (
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
        "%d-%m-%Y %H:%M:%S",
        "%d-%m-%Y %H:%M",
        "%d-%m-%Y",
    ):
        try:
            return datetime.strptime(str(value), fmt)
        except Exception:
            continue

    return None



def _status_is_closed(status_value):
    # SLA Stop Basis = CLOSED means exactly workflow status CLOSED.
    return _norm_upper(status_value) == "CLOSED"



def _derive_start_time(queue_row, rule_row, existing_log=None):
    existing_log = existing_log or {}

    # Once an SLA has started, its start time must never move.
    existing_start = (
        existing_log.get("start_time")
        or existing_log.get("sla_start_on")
    )
    parsed_existing = _parse_dt(existing_start)
    if parsed_existing:
        return parsed_existing

    start_event = _norm_upper(rule_row.get("start_event"))

    if start_event in {"ASSIGNED", "ASSIGNED_AT"}:
        candidate_columns = [
            "first_assigned_at",
            "assigned_at",
            "created_on",
            "updated_on",
        ]
    elif start_event in {"IN PROGRESS", "IN_PROGRESS", "IN_PROGRESS_AT"}:
        candidate_columns = [
            "in_progress_at",
            "updated_on",
            "assigned_at",
            "created_on",
        ]
    elif start_event == "UPDATED_ON":
        candidate_columns = ["updated_on", "assigned_at", "created_on"]
    else:
        candidate_columns = ["created_on", "assigned_at", "updated_on"]

    for col in candidate_columns:
        if col in queue_row and queue_row.get(col):
            parsed = _parse_dt(queue_row.get(col))
            if parsed:
                return parsed

    return None


def _rule_applies_to_queue(rule_row, queue_row):
    def matches(rule_value, queue_value):
        rule_text = _safe_str(rule_value)
        queue_text = _safe_str(queue_value)

        if not rule_text or _norm_upper(rule_text) == "ALL":
            return True
        return _norm_upper(rule_text) == _norm_upper(queue_text)

    if not matches(rule_row.get("module_name"), "Operation Queue"):
        return False

    if not matches(rule_row.get("priority_status"), queue_row.get("priority_status")):
        return False

    if not matches(rule_row.get("intent_remarks"), queue_row.get("intent_remarks")):
        return False

    if not matches(rule_row.get("hospital_shrtnm"), queue_row.get("hospital_shrtnm")):
        return False

    if not matches(rule_row.get("ivac_center"), queue_row.get("ivac_center")):
        return False

    if not matches(rule_row.get("agent_shrtnm"), queue_row.get("agent_shrtnm")):
        return False

    if not matches(rule_row.get("applicable_country"), queue_row.get("patient_country")):
        return False

    return True


def _fetch_active_sla_rules(cursor):
    sql = """
        SELECT
            id,
            rule_code,
            rule_name,
            module_name,
            intent_remarks,
            priority_status,
            trigger_event,
            hospital_shrtnm,
            ivac_center,
            agent_shrtnm,
            applicable_country,
            start_event,
            stop_basis,
            target_duration_mins,
            near_breach_mins,
            calendar_type,
            pause_on_hold_flag,
            resume_on_reopen_flag,
            auto_close_on_workflow_closed_flag,
            condition_expression,
            remarks,
            active_flag
        FROM sla_rule_master
        WHERE UPPER(COALESCE(active_flag, '')) = 'ACTIVE'
        ORDER BY id ASC
    """
    cursor.execute(sql)
    return cursor.fetchall() or []


def _fetch_active_escalations(cursor):
    sql = """
        SELECT
            id,
            sla_rule_id,
            escalation_level,
            escalation_basis,
            trigger_after_value,
            trigger_after_unit,
            recipient_type,
            recipient_value,
            action_type,
            notification_channel,
            escalation_message_template,
            active_flag,
            remarks
        FROM sla_rule_escalation
        WHERE UPPER(COALESCE(active_flag, '')) = 'ACTIVE'
        ORDER BY sla_rule_id ASC, escalation_level ASC
    """
    cursor.execute(sql)
    rows = cursor.fetchall() or []
    escalation_map = {}

    for row in rows:
        escalation_map.setdefault(row.get("sla_rule_id"), []).append(row)

    return escalation_map



def _fetch_operation_queue_rows(cursor):
    sql = """
        SELECT
            oq.id,
            oq.channel_thread_id,
            oq.channel_message_id,
            oq.patpassport_no,
            oq.patient_country,
            oq.hospital_shrtnm,
            oq.ivac_center,
            oq.agent_shrtnm,
            oq.intent_remarks,
            oq.wrkflw_status,
            oq.priority_status,
            oq.assigned_to,
            oq.assigned_by,
            oq.assigned_at,
            oq.created_on,
            oq.updated_on,
            first_assign.first_assigned_at
        FROM operation_queue_master oq
        INNER JOIN (
            SELECT channel_thread_id, MAX(id) AS max_id
            FROM operation_queue_master
            WHERE COALESCE(channel_thread_id, '') <> ''
            GROUP BY channel_thread_id
        ) latest
            ON latest.channel_thread_id = oq.channel_thread_id
           AND latest.max_id = oq.id
        LEFT JOIN (
            SELECT
                channel_thread_id,
                MIN(assigned_at) AS first_assigned_at
            FROM operation_queue_master
            WHERE COALESCE(channel_thread_id, '') <> ''
              AND assigned_at IS NOT NULL
            GROUP BY channel_thread_id
        ) first_assign
            ON first_assign.channel_thread_id = oq.channel_thread_id
        WHERE COALESCE(oq.channel_thread_id, '') <> ''
        ORDER BY oq.id ASC
    """
    cursor.execute(sql)
    return cursor.fetchall() or []



def _fetch_existing_sla_logs(cursor, table_columns):
    sql = """
        SELECT
            l.id,
            l.sla_rule_id,
            l.rule_code,
            l.operation_queue_id,
            l.channel_thread_id,
            l.status,
            l.sla_start_on,
            l.sla_due_on,
            l.sla_stop_on,
            l.elapsed_mins,
            l.breached_on,
            l.current_escalation_level,
            l.escalated_to,
            l.remarks,
            l.created_on,
            l.updated_on,
            oq_start.wrkflw_status AS initial_wrkflw_status
        FROM operation_queue_sla_log l
        LEFT JOIN operation_queue_master oq_start
            ON oq_start.id = l.operation_queue_id
    """
    cursor.execute(sql)
    rows = cursor.fetchall() or []

    log_map = {}

    for row in rows:
        # Internal aliases used by the SLA engine.
        row["start_time"] = row.get("sla_start_on")
        row["target_time"] = row.get("sla_due_on")
        row["stop_time"] = row.get("sla_stop_on")

        rule_code = _safe_str(row.get("rule_code"))
        channel_thread_id = _safe_str(row.get("channel_thread_id"))

        if not rule_code or not channel_thread_id:
            continue

        key = (rule_code, channel_thread_id)

        existing = log_map.get(key)
        if existing is None:
            log_map[key] = row
            continue

        existing_updated = (
            _parse_dt(existing.get("updated_on"))
            or _parse_dt(existing.get("created_on"))
            or datetime.min
        )
        current_updated = (
            _parse_dt(row.get("updated_on"))
            or _parse_dt(row.get("created_on"))
            or datetime.min
        )

        if current_updated >= existing_updated:
            log_map[key] = row

    return log_map


def _build_sla_log_key(rule_row, queue_row):
    return (
        _safe_str(rule_row.get("rule_code")),
        _safe_str(queue_row.get("channel_thread_id")),
    )


def _calculate_elapsed_minutes(start_time, now_dt):
    if not start_time or not now_dt:
        return 0

    delta = now_dt - start_time
    mins = int(delta.total_seconds() // 60)
    return mins if mins >= 0 else 0


def _calculate_target_time(start_time, target_duration_mins):
    if not start_time:
        return None
    return start_time + timedelta(minutes=_safe_int(target_duration_mins, 0))


def _calculate_near_breach_time(target_time, near_breach_mins):
    if not target_time:
        return None
    return target_time - timedelta(minutes=_safe_int(near_breach_mins, 0))



def _get_initial_workflow_status(existing_log, queue_row, rule_row=None):
    existing_value = _safe_str((existing_log or {}).get("initial_wrkflw_status"))
    if existing_value:
        return existing_value

    start_event = _norm_upper((rule_row or {}).get("start_event"))

    if start_event in {"ASSIGNED", "ASSIGNED_AT"}:
        return "ASSIGNED"

    if start_event in {"IN PROGRESS", "IN_PROGRESS", "IN_PROGRESS_AT"}:
        return "IN PROGRESS"

    return _safe_str(queue_row.get("wrkflw_status"))



def _should_stop_sla(rule_row, queue_row, existing_log=None):
    stop_basis = _norm_upper(rule_row.get("stop_basis"))
    current_status = _norm_upper(queue_row.get("wrkflw_status"))

    if stop_basis == "CLOSED":
        return current_status == "CLOSED"

    if stop_basis in {"STATUS CHANGE", "STATUS_CHANGE"}:
        initial_status = _norm_upper(
            _get_initial_workflow_status(
                existing_log,
                queue_row,
                rule_row=rule_row,
            )
        )
        return bool(
            initial_status
            and current_status
            and initial_status != current_status
        )

    return False


def _resolve_recipient(queue_row, escalation_row):
    recipient_type = _norm_upper(escalation_row.get("recipient_type"))
    recipient_value = _safe_str(escalation_row.get("recipient_value"))

    if recipient_type == "ASSIGNED TO":
        return _safe_str(queue_row.get("assigned_to"))

    if recipient_value:
        return recipient_value

    return ""


def _compute_escalation_due(start_time, target_time, escalation_row):
    basis = _norm_upper(escalation_row.get("escalation_basis"))
    trigger_value = _safe_int(escalation_row.get("trigger_after_value"), 0)
    trigger_unit = _norm_upper(escalation_row.get("trigger_after_unit"))

    if not start_time:
        return None

    if basis == "% OF SLA" and target_time:
        total_minutes = int((target_time - start_time).total_seconds() // 60)
        due_minutes = int((total_minutes * trigger_value) / 100) if total_minutes > 0 else 0
        return start_time + timedelta(minutes=due_minutes)

    if basis == "AFTER BREACH" and target_time:
        if trigger_unit == "HOURS":
            return target_time + timedelta(hours=trigger_value)
        return target_time + timedelta(minutes=trigger_value)

    if basis == "FIXED TIME":
        if trigger_unit == "HOURS":
            return start_time + timedelta(hours=trigger_value)
        return start_time + timedelta(minutes=trigger_value)

    return target_time



def _resolve_sla_stop_time(queue_row, start_time, now_dt):
    """
    Use the workflow-event timestamp rather than scheduler execution time.

    A cloned follow-up row gets created_on at the status transition.
    An in-place status update is represented by updated_on.
    """
    created_on = _parse_dt(queue_row.get("created_on"))
    updated_on = _parse_dt(queue_row.get("updated_on"))

    if created_on and start_time and created_on > start_time:
        return created_on

    if updated_on and start_time and updated_on >= start_time:
        return updated_on

    return updated_on or created_on or now_dt



def _prepare_log_payload(rule_row, queue_row, now_dt, existing_log=None):
    existing_log = existing_log or {}

    start_time = _derive_start_time(
        queue_row,
        rule_row,
        existing_log=existing_log,
    )

    target_duration_mins = _safe_int(
        rule_row.get("target_duration_mins"),
        0,
    )
    near_breach_mins = _safe_int(
        rule_row.get("near_breach_mins"),
        0,
    )

    existing_target = _parse_dt(
        existing_log.get("target_time")
        or existing_log.get("sla_due_on")
    )

    target_time = (
        existing_target
        or _calculate_target_time(start_time, target_duration_mins)
    )

    near_breach_time = _calculate_near_breach_time(
        target_time,
        near_breach_mins,
    )

    existing_stop = _parse_dt(
        existing_log.get("stop_time")
        or existing_log.get("sla_stop_on")
    )

    existing_breached_on = _parse_dt(
        existing_log.get("breached_on")
    )

    initial_status = _get_initial_workflow_status(
        existing_log,
        queue_row,
        rule_row=rule_row,
    )

    # Terminal SLA: freeze everything permanently.
    if existing_stop:
        return {
            "start_time": start_time,
            "target_time": target_time,
            "near_breach_time": near_breach_time,
            "stop_time": existing_stop,
            "elapsed_mins": _safe_int(
                existing_log.get("elapsed_mins"),
                0,
            ),
            "status": _safe_str(existing_log.get("status")) or "MET",
            "remarks": _safe_str(existing_log.get("remarks")),
            "initial_wrkflw_status": initial_status,
            "breached_on": existing_breached_on,
        }

    calculated_elapsed_mins = _calculate_elapsed_minutes(
        start_time,
        now_dt,
    )

    stop_sla = _should_stop_sla(
        rule_row,
        queue_row,
        existing_log=existing_log,
    )

    stop_time = None
    breached_on = existing_breached_on

    if stop_sla:
        stop_time = _resolve_sla_stop_time(
            queue_row,
            start_time,
            now_dt,
        )

        elapsed_mins = _calculate_elapsed_minutes(
            start_time,
            stop_time,
        )

        if target_time and stop_time >= target_time:
            status = "BREACHED"
            breached_on = breached_on or target_time
            remarks = "SLA stopped after target breach."
        else:
            status = "MET"
            remarks = "SLA stopped within configured target."
    else:
        elapsed_mins = calculated_elapsed_mins

        if target_time and now_dt >= target_time:
            status = "BREACHED"
            breached_on = breached_on or target_time
            remarks = "SLA target breached."
        elif near_breach_time and now_dt >= near_breach_time:
            status = "NEAR BREACH"
            remarks = "SLA is nearing breach."
        else:
            status = "RUNNING"
            remarks = "SLA is running."

    return {
        "start_time": start_time,
        "target_time": target_time,
        "near_breach_time": near_breach_time,
        "stop_time": stop_time,
        "elapsed_mins": elapsed_mins,
        "status": status,
        "remarks": remarks,
        "initial_wrkflw_status": initial_status,
        "breached_on": breached_on,
    }



def _insert_sla_log(cursor, table_columns, rule_row, queue_row, log_payload):
    insert_data = {}

    possible_values = {
        "sla_rule_id": rule_row.get("id"),
        "rule_code": rule_row.get("rule_code"),
        "operation_queue_id": queue_row.get("id"),
        "channel_thread_id": queue_row.get("channel_thread_id"),
        "sla_start_on": log_payload.get("start_time"),
        "sla_due_on": log_payload.get("target_time"),
        "sla_stop_on": log_payload.get("stop_time"),
        "elapsed_mins": log_payload.get("elapsed_mins"),
        "breached_on": log_payload.get("breached_on"),
        "status": log_payload.get("status"),
        "remarks": log_payload.get("remarks"),
        "created_on": datetime.now(),
        "updated_on": datetime.now(),
    }

    for col, value in possible_values.items():
        if col in table_columns:
            insert_data[col] = value

    if not insert_data:
        return None

    cols = list(insert_data.keys())
    placeholders = ", ".join(["%s"] * len(cols))

    sql = f"""
        INSERT INTO operation_queue_sla_log
        ({", ".join(cols)})
        VALUES
        ({placeholders})
    """

    cursor.execute(
        sql,
        tuple(insert_data[col] for col in cols),
    )

    return cursor.lastrowid



def _update_sla_log(cursor, table_columns, log_row, queue_row, log_payload):
    set_parts = []
    values = []

    # operation_queue_id intentionally remains the SLA-origin row.
    # Do not move it to every follow-up workflow row.
    update_candidates = {
        "status": log_payload.get("status"),
        "sla_start_on": log_payload.get("start_time"),
        "sla_due_on": log_payload.get("target_time"),
        "sla_stop_on": log_payload.get("stop_time"),
        "elapsed_mins": log_payload.get("elapsed_mins"),
        "breached_on": log_payload.get("breached_on"),
        "remarks": log_payload.get("remarks"),
        "updated_on": datetime.now(),
    }

    for col, value in update_candidates.items():
        if col in table_columns:
            set_parts.append(f"{col} = %s")
            values.append(value)

    if not set_parts or "id" not in log_row:
        return

    sql = f"""
        UPDATE operation_queue_sla_log
        SET {", ".join(set_parts)}
        WHERE id = %s
    """

    values.append(log_row.get("id"))
    cursor.execute(sql, tuple(values))


def _apply_escalations(cursor, table_columns, rule_row, queue_row, log_row, log_payload, escalation_rows, now_dt):
    if log_payload.get("stop_time"):
        return

    if log_payload.get("status") not in {"NEAR BREACH", "BREACHED"}:
        return

    highest_triggered_level = 0
    escalated_to_values = []

    for escalation in escalation_rows or []:
        due_dt = _compute_escalation_due(
            start_time=log_payload.get("start_time"),
            target_time=log_payload.get("target_time"),
            escalation_row=escalation,
        )
        if due_dt is None or now_dt < due_dt:
            continue

        level = _safe_int(escalation.get("escalation_level"), 0)
        if level > highest_triggered_level:
            highest_triggered_level = level

        recipient = _resolve_recipient(queue_row, escalation)
        if recipient:
            escalated_to_values.append(recipient)

        sla_engine_logger.info(
            "SLA_ESCALATION_TRIGGERED rule_code=%s thread=%s level=%s recipient_type=%s recipient=%s action=%s channel=%s",
            rule_row.get("rule_code"),
            queue_row.get("channel_thread_id"),
            level,
            escalation.get("recipient_type"),
            recipient or "-",
            escalation.get("action_type") or "-",
            escalation.get("notification_channel") or "-",
        )

    if not log_row or "id" not in log_row:
        return

    set_parts = []
    values = []

    if "current_escalation_level" in table_columns and highest_triggered_level > 0:
        set_parts.append("current_escalation_level = %s")
        values.append(highest_triggered_level)

    if "escalated_to" in table_columns and escalated_to_values:
        set_parts.append("escalated_to = %s")
        values.append(", ".join(sorted(set([v for v in escalated_to_values if v]))))

    if "updated_on" in table_columns:
        set_parts.append("updated_on = %s")
        values.append(datetime.now())

    if not set_parts:
        return

    sql = f"""
        UPDATE operation_queue_sla_log
        SET {", ".join(set_parts)}
        WHERE id = %s
    """
    values.append(log_row.get("id"))
    cursor.execute(sql, tuple(values))


def _run_sla_engine_inner():
    conn = None
    cursor = None

    try:
        sla_engine_logger.info("SLA_ENGINE_JOB_TRIGGERED")

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        required_tables = {
            "sla_rule_master",
            "sla_rule_escalation",
            "operation_queue_master",
            "operation_queue_sla_log",
        }

        existing_tables = {tbl for tbl in required_tables if _table_exists(cursor, tbl)}
        missing_tables = required_tables - existing_tables

        if missing_tables:
            missing_text = ", ".join(sorted(missing_tables))
            sla_engine_logger.warning(
                "SLA_ENGINE_SKIPPED missing_tables=%s",
                missing_text,
            )
            return {
                "monitoring_status": "PARTIAL",
                "records_read": 0,
                "records_processed": 0,
                "records_success": 0,
                "records_failed": 0,
                "records_skipped": 0,
                "remarks": f"SLA Engine skipped because tables are missing: {missing_text}.",
            }

        log_table_columns = _get_table_columns(cursor, "operation_queue_sla_log")
        rule_rows = _fetch_active_sla_rules(cursor)
        escalation_map = _fetch_active_escalations(cursor)
        queue_rows = _fetch_operation_queue_rows(cursor)
        existing_log_map = _fetch_existing_sla_logs(cursor, log_table_columns)

        sla_engine_logger.info(
            "SLA_ENGINE_COUNTS active_rules=%s latest_queue_rows=%s existing_sla_logs=%s",
            len(rule_rows),
            len(queue_rows),
            len(existing_log_map),
        )

        now_dt = datetime.now()
        inserted_logs = 0
        updated_logs = 0
        matched_records = 0

        for rule_row in rule_rows:
            rule_code = _safe_str(rule_row.get("rule_code"))
            if not rule_code:
                continue

            rule_escalations = escalation_map.get(rule_row.get("id"), [])

            for queue_row in queue_rows:
                log_key = _build_sla_log_key(rule_row, queue_row)
                existing_log = existing_log_map.get(log_key)

                if existing_log and _parse_dt(
                    existing_log.get("stop_time")
                    or existing_log.get("sla_stop_on")
                ):
                    continue

                rule_matches_now = _rule_applies_to_queue(rule_row, queue_row)
                must_continue_existing_sla = existing_log is not None

                if not rule_matches_now and not must_continue_existing_sla:
                    continue

                matched_records += 1

                log_payload = _prepare_log_payload(
                    rule_row=rule_row,
                    queue_row=queue_row,
                    now_dt=now_dt,
                    existing_log=existing_log,
                )

                start_time = log_payload.get("start_time")
                if start_time is None:
                    sla_engine_logger.info(
                        "SLA_ENGINE_RECORD_SKIPPED rule_code=%s thread=%s reason=%s",
                        rule_code,
                        queue_row.get("channel_thread_id"),
                        "Start time not available",
                    )
                    continue

                if existing_log:
                    _update_sla_log(
                        cursor=cursor,
                        table_columns=log_table_columns,
                        log_row=existing_log,
                        queue_row=queue_row,
                        log_payload=log_payload,
                    )
                    updated_logs += 1

                    refreshed_log = dict(existing_log)
                    refreshed_log.update({
                        "operation_queue_id": queue_row.get("id"),
                        "channel_message_id": queue_row.get("channel_message_id"),
                        "status": log_payload.get("status"),
                        "start_time": existing_log.get("start_time") or log_payload.get("start_time"),
                        "target_time": log_payload.get("target_time"),
                        "near_breach_time": log_payload.get("near_breach_time"),
                        "elapsed_mins": log_payload.get("elapsed_mins"),
                        "wrkflw_status": queue_row.get("wrkflw_status"),
                        "assigned_to": queue_row.get("assigned_to"),
                        "initial_wrkflw_status": existing_log.get("initial_wrkflw_status") or log_payload.get("initial_wrkflw_status"),
                    })

                    _apply_escalations(
                        cursor=cursor,
                        table_columns=log_table_columns,
                        rule_row=rule_row,
                        queue_row=queue_row,
                        log_row=refreshed_log,
                        log_payload=log_payload,
                        escalation_rows=rule_escalations,
                        now_dt=now_dt,
                    )
                else:
                    new_log_id = _insert_sla_log(
                        cursor=cursor,
                        table_columns=log_table_columns,
                        rule_row=rule_row,
                        queue_row=queue_row,
                        log_payload=log_payload,
                    )
                    inserted_logs += 1

                    new_log = {
                        "id": new_log_id,
                        "rule_code": rule_row.get("rule_code"),
                        "channel_thread_id": queue_row.get("channel_thread_id"),
                        "operation_queue_id": queue_row.get("id"),
                        "start_time": log_payload.get("start_time"),
                        "initial_wrkflw_status": log_payload.get("initial_wrkflw_status"),
                    }

                    existing_log_map[log_key] = new_log

                    _apply_escalations(
                        cursor=cursor,
                        table_columns=log_table_columns,
                        rule_row=rule_row,
                        queue_row=queue_row,
                        log_row=new_log,
                        log_payload=log_payload,
                        escalation_rows=rule_escalations,
                        now_dt=now_dt,
                    )

        conn.commit()

        sla_engine_logger.info(
            "SLA_ENGINE_JOB_COMPLETED matched=%s inserted=%s updated=%s",
            matched_records,
            inserted_logs,
            updated_logs,
        )

        successful_updates = inserted_logs + updated_logs
        return {
            "monitoring_status": "SUCCESS",
            "records_read": len(queue_rows),
            "records_processed": matched_records,
            "records_success": successful_updates,
            "records_failed": 0,
            "records_skipped": max(matched_records - successful_updates, 0),
            "remarks": (
                "SLA Engine completed. "
                f"active_rules={len(rule_rows)}, queue_rows={len(queue_rows)}, "
                f"matched={matched_records}, inserted={inserted_logs}, "
                f"updated={updated_logs}."
            ),
        }

    except Exception as e:
        if conn:
            conn.rollback()
        sla_engine_logger.exception("SLA_ENGINE_JOB_FAILED error=%s", e)
        return {
            "monitoring_status": "FAILED",
            "records_read": 0,
            "records_processed": 0,
            "records_success": 0,
            "records_failed": 0,
            "records_skipped": 0,
            "error_message": str(e),
            "stack_trace": traceback.format_exc(),
            "remarks": "SLA Engine failed.",
        }

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def run_sla_engine_job():
    global _scheduler_app, _scheduler_instance

    if _scheduler_app is None:
        sla_engine_logger.error(
            "SLA_ENGINE_JOB_FAILED Flask app reference not available"
        )
        return

    with _scheduler_app.app_context():
        return run_monitored_job(
            job_code="SLA_ENGINE",
            job_callable=_run_sla_engine_inner,
            scheduler=_scheduler_instance,
            trigger_type="SCHEDULED",
        )


# ============================================================
# ALERT ENGINE JOB
# ============================================================

def run_alert_engine_job():
    global _scheduler_app, _scheduler_instance

    if _scheduler_app is None:
        alert_engine_job_logger.error(
            "ALERT_ENGINE_JOB_FAILED Flask app reference not available"
        )
        return

    with _scheduler_app.app_context():
        return run_monitored_job(
            job_code="ALERT_ENGINE",
            job_callable=run_alert_engine_job_without_context,
            scheduler=_scheduler_instance,
            trigger_type="SCHEDULED",
        )


# ============================================================
# WHATSAPP SESSION TIMEOUT JOB
# ============================================================

def run_whatsapp_session_timeout_job():
    """Run the approved 2-minute / 5-minute WhatsApp timeout rules."""

    global _scheduler_app

    if _scheduler_app is None:
        scheduler_logger.error(
            "WHATSAPP_SESSION_TIMEOUT_JOB_SKIPPED Flask app reference not available"
        )
        return {}

    with _scheduler_app.app_context():
        try:
            privacy_summary = process_whatsapp_session_timeouts() or {}

            # Run WA submission tracking here as well as post-parser so delayed
            # Manual Review -> SUCCESS notifications can be delivered even when
            # the queue parser has no pending rows.
            tracking_summary = process_pending_submission_acknowledgements() or {}

            return {
                "privacy": privacy_summary,
                "submission_tracking": tracking_summary,
            }
        except Exception as exc:
            scheduler_logger.exception(
                "WHATSAPP_SESSION_TIMEOUT_JOB_FAILED error=%s",
                exc,
            )
            return {}


# ============================================================
# MASTER SCHEDULER
# ============================================================

def run_scheduler_heartbeat_job():
    global _scheduler_app, _scheduler_instance

    if _scheduler_app is None or _scheduler_instance is None:
        scheduler_logger.error(
            "SCHEDULER_HEARTBEAT_JOB_SKIPPED scheduler/app reference not available"
        )
        return

    with _scheduler_app.app_context():
        refresh_scheduler_heartbeat(
            _scheduler_instance,
            scheduler_status="RUNNING",
            remarks="TERIQEN background scheduler heartbeat is healthy.",
        )


def start_queue_parser_scheduler(app=None):
    global _scheduler_instance, _scheduler_app

    if BackgroundScheduler is None:
        scheduler_logger.warning(
            "APScheduler not installed. Queue parser scheduler not started."
        )
        return None

    if app is not None:
        _scheduler_app = app

    if _scheduler_instance and _scheduler_instance.running:
        scheduler_logger.info("Queue parser scheduler already running.")
        if _scheduler_app is not None:
            with _scheduler_app.app_context():
                register_scheduler_jobs(_scheduler_instance)
        return _scheduler_instance

    # Register/synchronize the job master before adding APScheduler jobs.
    # Monitoring failures are non-blocking inside the monitoring service.
    enabled_jobs = {
        "QUEUE_PARSER": True,
        "SLA_ENGINE": True,
        "ALERT_ENGINE": True,
    }

    if _scheduler_app is not None:
        with _scheduler_app.app_context():
            register_scheduler_jobs()
            enabled_jobs = {
                "QUEUE_PARSER": is_job_enabled("QUEUE_PARSER"),
                "SLA_ENGINE": is_job_enabled("SLA_ENGINE"),
                "ALERT_ENGINE": is_job_enabled("ALERT_ENGINE"),
            }

    scheduler = BackgroundScheduler()

    if enabled_jobs["QUEUE_PARSER"]:
        scheduler.add_job(
            func=run_queue_parser_job,
            trigger="interval",
            seconds=60,
            id="queue_parser_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    else:
        scheduler_logger.warning("Queue Parser scheduler is disabled in scheduler_job_master.")

    if enabled_jobs["SLA_ENGINE"]:
        scheduler.add_job(
            func=run_sla_engine_job,
            trigger="interval",
            seconds=60,
            id="sla_engine_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    else:
        scheduler_logger.warning("SLA Engine scheduler is disabled in scheduler_job_master.")

    if enabled_jobs["ALERT_ENGINE"]:
        scheduler.add_job(
            func=run_alert_engine_job,
            trigger="interval",
            minutes=1,
            id="alert_engine_job",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
        )
    else:
        scheduler_logger.warning("Alert Engine scheduler is disabled in scheduler_job_master.")

    # WhatsApp session timeout engine. This is a technical workflow job and is
    # intentionally independent of QUEUE_PARSER enable/disable status so idle
    # consent/submission sessions still close cleanly.
    scheduler.add_job(
        func=run_whatsapp_session_timeout_job,
        trigger="interval",
        seconds=60,
        id="whatsapp_session_timeout_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Technical heartbeat. It is intentionally NOT registered in scheduler_job_master
    # and is excluded from total_jobs/active_jobs displayed in System Monitoring.
    scheduler.add_job(
        func=run_scheduler_heartbeat_job,
        trigger="interval",
        seconds=60,
        id="system_monitoring_heartbeat_job",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    # Assign before start so monitored jobs can access the live scheduler instance.
    _scheduler_instance = scheduler
    scheduler.start()

    if _scheduler_app is not None:
        with _scheduler_app.app_context():
            register_scheduler_jobs(scheduler)
            refresh_scheduler_heartbeat(
                scheduler,
                scheduler_status="RUNNING",
                remarks="TERIQEN background scheduler started successfully.",
            )

    scheduler_logger.info("Queue parser scheduler started. Running every 1 minute.")
    scheduler_logger.info("SLA engine scheduler started. Running every 1 minute.")
    scheduler_logger.info("Alert engine scheduler started. Running every 1 minute.")
    scheduler_logger.info("WhatsApp session timeout scheduler started. Running every 1 minute.")
    return scheduler
