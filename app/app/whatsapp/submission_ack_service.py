import logging
import os
import re
from datetime import datetime
from zoneinfo import ZoneInfo

from app.common.db import get_db_connection
from app.whatsapp.outbound_service import (
    extract_outbound_message_id,
    send_text,
)


logger = logging.getLogger("whatsapp_submission_ack")

INDIA_TZ = ZoneInfo("Asia/Kolkata")
CATEGORY_AGENCY = "MEDICAL_TOURISM_AGENCY"
STATE_NEW = "NEW"
LANGUAGE_ENGLISH = "ENGLISH"
LANGUAGE_BENGALI = "BENGALI"

TRACKING_TABLE = "whatsapp_submission_tracking"
DEFAULT_SUBMISSION_WINDOW_SECONDS = 60
DEFAULT_BATCH_SIZE = 200

OUTCOME_PENDING = "PENDING"
OUTCOME_SUCCESS = "SUCCESS"
OUTCOME_MANUAL_REVIEW = "MANUAL_REVIEW"
OUTCOME_JUNK = "JUNK"

NOTIFICATION_NOT_READY = "NOT_READY"
NOTIFICATION_PENDING = "PENDING"
NOTIFICATION_SENDING = "SENDING"
NOTIFICATION_SENT = "SENT"
NOTIFICATION_FAILED = "FAILED"

SESSION_OPEN = "OPEN"
SESSION_PROCESSING = "PROCESSING"
SESSION_CLOSED = "CLOSED"

CLOSE_MESSAGE_NOT_SENT = "NOT_SENT"
CLOSE_MESSAGE_PENDING = "PENDING"
CLOSE_MESSAGE_SENDING = "SENDING"
CLOSE_MESSAGE_SENT = "SENT"
CLOSE_MESSAGE_FAILED = "FAILED"


# ============================================================
# BASIC HELPERS
# ============================================================

def _safe_str(value):
    return "" if value is None else str(value).strip()


def _now_india_naive():
    return datetime.now(INDIA_TZ).replace(tzinfo=None)


def _parse_datetime(value):
    if isinstance(value, datetime):
        return value

    text = _safe_str(value)
    if not text:
        return None

    for fmt in (
        "%Y-%m-%d %H:%M:%S.%f",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%dT%H:%M:%S.%f",
        "%Y-%m-%dT%H:%M:%S",
    ):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue

    return None


def _submission_window_seconds():
    raw = _safe_str(
        os.getenv(
            "WHATSAPP_SUBMISSION_WINDOW_SECONDS",
            DEFAULT_SUBMISSION_WINDOW_SECONDS,
        )
    )
    try:
        value = int(raw)
    except Exception:
        value = DEFAULT_SUBMISSION_WINDOW_SECONDS

    # Keep accidental values within a practical range.
    return max(30, min(value, 600))


def build_wa_submission_id(channel_message_log_id, received_on=None):
    """
    Stable temporary WhatsApp submission reference.

    One tracking/root row owns one WA Ref. Child WhatsApp messages in the same
    submission window use linked_triage_id and never generate another WA Ref.
    """
    root_id = int(channel_message_log_id)
    received_dt = _parse_datetime(received_on) or _now_india_naive()
    return f"WA-{received_dt.strftime('%Y%m%d')}-{root_id:06d}"


def _table_exists(cursor, table_name):
    cursor.execute(
        """
        SELECT COUNT(*) AS cnt
        FROM information_schema.tables
        WHERE table_schema = DATABASE()
          AND table_name = %s
        """,
        (table_name,),
    )
    row = cursor.fetchone() or {}
    return int(row.get("cnt") or 0) > 0


def _assert_tracking_table(cursor):
    if not _table_exists(cursor, TRACKING_TABLE):
        raise RuntimeError(
            "whatsapp_submission_tracking is missing. "
            "Create the WhatsApp submission tracking table first."
        )


def _format_submission_datetime(value):
    dt = _parse_datetime(value)
    if not dt:
        return "-"
    return dt.strftime("%d %b %Y, %I:%M %p")


def _normalize_language(value):
    value = _safe_str(value).upper()
    return LANGUAGE_BENGALI if value in {"BN", "BENGALI", "BANGLA", "বাংলা"} else LANGUAGE_ENGLISH


def _get_preferred_language(sender_mobile):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT preferred_language
            FROM whatsapp_conversation_state
            WHERE sender_mobile = %s
              AND user_category = %s
            ORDER BY updated_on DESC, id DESC
            LIMIT 1
            """,
            (sender_mobile, CATEGORY_AGENCY),
        )
        row = cursor.fetchone() or {}
        return _normalize_language(row.get("preferred_language"))
    except Exception:
        logger.exception("WHATSAPP_LANGUAGE_LOOKUP_FAILED mobile=%s", sender_mobile)
        return LANGUAGE_ENGLISH
    finally:
        cursor.close()
        conn.close()


# ============================================================
# USER-FACING MESSAGE BUILDERS
# ============================================================

def _build_processing_wait_text(language=LANGUAGE_ENGLISH):
    """Immediate acknowledgement for every accepted patient-data event."""
    language = _normalize_language(language)
    if language == LANGUAGE_BENGALI:
        return (
            "📥 রোগীর তথ্য পাওয়া গেছে।\n"
            "⏳ আমরা রেকর্ডগুলো যাচাই করছি। অনুগ্রহ করে অপেক্ষা করুন।"
        )
    return (
        "📥 Patient information received.\n"
        "⏳ We are processing the records. Please wait."
    )


def send_processing_wait_message(sender_mobile):
    """
    Send the immediate processing/wait message after EACH accepted patient-data
    WhatsApp event. This message deliberately contains no WA Ref and no thread
    reference because the final business outcome is not known yet.
    """
    sender_mobile = _safe_str(sender_mobile)
    if not sender_mobile:
        return {"sent": False, "reason": "SENDER_MOBILE_REQUIRED"}

    try:
        language = _get_preferred_language(sender_mobile)
        message_text = _build_processing_wait_text(language=language)
        meta_response = send_text(
            sender_mobile,
            message_text,
            preview_url=False,
        )
        meta_message_id = extract_outbound_message_id(meta_response)
        if not meta_message_id:
            raise RuntimeError(
                "Meta accepted no auditable processing/wait message id."
            )

        logger.info(
            "WHATSAPP_PROCESSING_WAIT_SENT mobile=%s language=%s message_id=%s",
            sender_mobile,
            language,
            meta_message_id,
        )
        return {
            "sent": True,
            "message_id": meta_message_id,
            "language": language,
        }

    except Exception as exc:
        logger.exception(
            "WHATSAPP_PROCESSING_WAIT_SEND_FAILED mobile=%s error=%s",
            sender_mobile,
            exc,
        )
        return {
            "sent": False,
            "reason": "SEND_FAILED",
            "error": str(exc),
        }

def _build_manual_review_text(wa_submission_id, language=LANGUAGE_ENGLISH):
    language = _normalize_language(language)
    if language == LANGUAGE_BENGALI:
        lines = ["🔎 আপনার সাবমিশনটির জন্য অতিরিক্ত পর্যালোচনার প্রয়োজন রয়েছে।"]
        if _safe_str(wa_submission_id):
            lines.append(f"🆔 WA Ref #: {wa_submission_id}")
        lines.extend([
            "",
            "আমাদের টিম তথ্যগুলো যাচাই করে প্রয়োজনীয় ব্যবস্থা গ্রহণ করবে।",
            "🔒 এই WhatsApp সাবমিশন উইন্ডোটি এখন বন্ধ করা হয়েছে।",
            "",
            "🙏 ক্যাডুপিয়াস হেলথঅ্যাসিস্ট বেছে নেওয়ার জন্য ধন্যবাদ।",
        ])
        return "\n".join(lines)

    lines = ["📋 Patient submission is under Manual Review."]
    if _safe_str(wa_submission_id):
        lines.append(f"🆔 WA Ref #: {wa_submission_id}")
    lines.extend([
        "",
        "Our team will review the information and take the necessary action.",
        "🔒 This WhatsApp submission window is now closed.",
        "",
        "🙏 Thank you for choosing Cadupius Healthassist.",
    ])
    return "\n".join(lines)

def _build_processing_error_text(language=LANGUAGE_ENGLISH):
    """Terminal non-duplicate processing error. Never expose a WA Ref here."""
    language = _normalize_language(language)
    if language == LANGUAGE_BENGALI:
        return (
            "⚠️ আমরা আপনার পাঠানো রোগীর তথ্যটি যাচাই করতে পারিনি।\n"
            "অনুগ্রহ করে তথ্যগুলো পরীক্ষা করুন এবং নির্ধারিত ফরমেট ব্যবহার করে পুনরায় পাঠান।"
        )
    return (
        "⚠️ We could not process the patient information you sent.\n"
        "Please check the details and resend them using the prescribed format."
    )


def _extract_passport_from_submission_text(raw_message_text):
    """
    Extract the labelled Patient Passport Number only for user-facing duplicate
    closure text. This helper does not make the duplicate business decision.
    """
    text = _safe_str(raw_message_text).upper()
    if not text:
        return ""

    patterns = (
        r"(?:^|[\r\n])\s*PATIENT\s+PASSPORT\s*(?:NO|NUMBER|#)?\s*[:\-\t ]*\s*([A-Z0-9]{4,20})\b",
        r"(?:^|[\r\n])\s*PASSPORT\s*(?:NO|NUMBER|#)?\s*[:\-\t ]*\s*([A-Z0-9]{4,20})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.MULTILINE)
        if match:
            return re.sub(r"[^A-Z0-9]", "", _safe_str(match.group(1)).upper())

    return ""


def _fetch_duplicate_closure_context(channel_message_log_id):
    """
    Read duplicate outcome context for the user-facing closure message.

    IMPORTANT BUSINESS BOUNDARY:
    - duplicate_flag is already decided upstream from patient_wrkflw +
      wrkflwrule_master.
    - operation_queue_master is NOT used here to decide whether a record is a
      duplicate.
    - operation_queue_master is queried only AFTER duplicate_flag = 1 so the
      existing channel_thread_id can be shown to the Agency as "Reference #".
    """
    if not channel_message_log_id:
        return {
            "duplicate_flag": 0,
            "passport_no": "",
            "existing_thread_id": "",
        }

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT
                duplicate_flag,
                raw_message_text,
                classifier_reason,
                remarks
            FROM channel_message_log
            WHERE id = %s
            LIMIT 1
            """,
            (channel_message_log_id,),
        )
        log_row = cursor.fetchone() or {}
        duplicate_flag = int(log_row.get("duplicate_flag") or 0)
        passport_no = _extract_passport_from_submission_text(
            log_row.get("raw_message_text")
        )

        existing_thread_id = ""
        existing_workflow_status = ""

        # Display-context lookup only. This does NOT participate in the duplicate
        # business decision. duplicate_flag has already been established upstream.
        if duplicate_flag == 1 and passport_no:
            cursor.execute(
                """
                SELECT wrkflw_status
                FROM patient_wrkflw
                WHERE UPPER(TRIM(COALESCE(patpassport_no, ''))) = %s
                ORDER BY
                    entered_date DESC,
                    COALESCE(ld_seqno, 0) DESC,
                    ld_internalno DESC
                LIMIT 1
                """,
                (passport_no,),
            )
            workflow_row = cursor.fetchone() or {}
            existing_workflow_status = _safe_str(
                workflow_row.get("wrkflw_status")
            ).upper()

        # Reference lookup only. This does NOT participate in the duplicate
        # business decision. By the time this branch runs, duplicate_flag = 1
        # has already been established from patient_wrkflw / Workflow Rule Master.
        if duplicate_flag == 1 and passport_no:
            cursor.execute(
                """
                SELECT channel_thread_id
                FROM operation_queue_master
                WHERE UPPER(TRIM(COALESCE(patpassport_no, ''))) = %s
                  AND TRIM(COALESCE(channel_thread_id, '')) <> ''
                ORDER BY id DESC
                LIMIT 1
                """,
                (passport_no,),
            )
            queue_row = cursor.fetchone() or {}
            existing_thread_id = _safe_str(
                queue_row.get("channel_thread_id")
            )

        return {
            "duplicate_flag": duplicate_flag,
            "passport_no": passport_no,
            "existing_thread_id": existing_thread_id,
            "existing_workflow_status": existing_workflow_status,
            "classifier_reason": _safe_str(log_row.get("classifier_reason")),
            "remarks": _safe_str(log_row.get("remarks")),
        }
    finally:
        cursor.close()
        conn.close()

def _build_duplicate_closure_text(
    passport_no="",
    existing_workflow_status="",
    existing_thread_id="",
    language=LANGUAGE_ENGLISH,
):
    """Duplicate error. Never expose the temporary WA Ref."""
    passport_no = _safe_str(passport_no).upper()
    existing_workflow_status = _safe_str(existing_workflow_status).upper()
    existing_thread_id = _safe_str(existing_thread_id)
    language = _normalize_language(language)

    if language == LANGUAGE_BENGALI:
        lines = ["⚠️ ডুপ্লিকেট রোগীর রেকর্ড শনাক্ত করা হয়েছে।"]
        if passport_no:
            lines.append(f"👤 Passport #: {passport_no}")
        if existing_workflow_status:
            lines.append(f"📌 বিদ্যমান কেসের স্ট্যাটাস: {existing_workflow_status}")
        lines.extend([
            "",
            "এই Passport Number-এর জন্য একটি রোগীর কেস ইতিমধ্যেই বিদ্যমান রয়েছে।",
            "এই সাবমিশন থেকে নতুন কোনো রোগীর কেস তৈরি করা হয়নি।",
        ])
        if existing_thread_id:
            lines.extend(["", f"🆔 Ref #: {existing_thread_id}"])
        lines.extend([
            "",
            "🔒 এই WhatsApp সাবমিশন উইন্ডোটি এখন বন্ধ করা হয়েছে।",
            "",
            "🙏 ক্যাডুপিয়াস হেলথঅ্যাসিস্ট বেছে নেওয়ার জন্য ধন্যবাদ।",
        ])
        return "\n".join(lines)

    lines = ["⚠️ Duplicate patient record detected."]
    if passport_no:
        lines.append(f"👤 Passport #: {passport_no}")
    if existing_workflow_status:
        lines.append(f"📌 Existing Case Status: {existing_workflow_status}")
    lines.extend([
        "",
        "An existing patient case already exists for this Passport Number.",
        "No new patient case has been created from this submission.",
    ])
    if existing_thread_id:
        lines.extend(["", f"🆔 Ref #: {existing_thread_id}"])
    lines.extend([
        "",
        "🔒 This WhatsApp submission window is now closed.",
        "",
        "🙏 Thank you for choosing Cadupius Healthassist.",
    ])
    return "\n".join(lines)

def _build_success_text(
    patient_name,
    submission_received_on,
    channel_thread_id,
    wa_submission_id="",
    language=LANGUAGE_ENGLISH,
):
    """
    Normal success message.

    The business reference shown to the Agency is channel_thread_id only.
    wa_submission_id is retained in the signature for backward compatibility
    but is intentionally never displayed for a successful parsed record.
    """
    language = _normalize_language(language)
    patient_name = _safe_str(patient_name)
    channel_thread_id = _safe_str(channel_thread_id)

    if language == LANGUAGE_BENGALI:
        lines = ["✅ রোগীর তথ্য সফলভাবে যাচাই করা হয়েছে।"]
        if patient_name:
            lines.append(f"👤 রোগী: {patient_name}")
        lines.append(f"🆔 Ref #: {channel_thread_id}")
        lines.append(
            f"🗓️ জমা দেওয়ার সময়: {_format_submission_datetime(submission_received_on)}"
        )
        lines.extend([
            "",
            "🔒 এই WhatsApp সাবমিশন উইন্ডোটি এখন বন্ধ করা হয়েছে।",
            "",
            "🙏 ক্যাডুপিয়াস হেলথঅ্যাসিস্ট বেছে নেওয়ার জন্য ধন্যবাদ।",
        ])
        return "\n".join(lines)

    lines = ["✅ Patient information processed successfully."]
    if patient_name:
        lines.append(f"👤 Patient: {patient_name}")
    lines.append(f"🆔 Ref #: {channel_thread_id}")
    lines.append(f"🗓️ Submitted: {_format_submission_datetime(submission_received_on)}")
    lines.extend([
        "",
        "🔒 This WhatsApp submission window is now closed.",
        "",
        "🙏 Thank you for choosing Cadupius Healthassist.",
    ])
    return "\n".join(lines)

def _fetch_patient_name_for_thread(cursor, channel_thread_id):
    channel_thread_id = _safe_str(channel_thread_id)
    if not channel_thread_id:
        return ""

    try:
        cursor.execute(
            """
            SELECT
                patgiven_nm,
                patsur_nm
            FROM operation_queue_master
            WHERE channel_thread_id = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (channel_thread_id,),
        )
        row = cursor.fetchone() or {}
        parts = [
            _safe_str(row.get("patgiven_nm")),
            _safe_str(row.get("patsur_nm")),
        ]
        return " ".join([part for part in parts if part]).strip()
    except Exception:
        logger.exception(
            "WHATSAPP_SUCCESS_PATIENT_NAME_LOOKUP_FAILED thread=%s",
            channel_thread_id,
        )
        return ""


# ============================================================
# SUBMISSION WINDOW REGISTRATION
# ============================================================

def register_whatsapp_submission(
    sender_mobile,
    channel_message_log_id,
    received_on=None,
):
    """
    Register or extend one WhatsApp submission collection window.

    IMPORTANT:
    - This function DOES NOT send any acknowledgement.
    - First message creates the tracking/root row.
    - Later messages while the submission is OPEN are linked to the same root
      through channel_message_log.linked_triage_id.
    - last_message_received_on is moved forward for every new message.
    - The scheduler waits for the quiet window before classification/parsing.
    """

    if not channel_message_log_id:
        return {"sent": False, "reason": "CHANNEL_MESSAGE_LOG_ID_REQUIRED"}

    sender_mobile = _safe_str(sender_mobile)
    if not sender_mobile:
        return {"sent": False, "reason": "SENDER_MOBILE_REQUIRED"}

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        _assert_tracking_table(cursor)

        cursor.execute(
            """
            SELECT
                id,
                sender_mobile,
                received_on,
                linked_triage_id
            FROM channel_message_log
            WHERE id = %s
              AND UPPER(TRIM(COALESCE(channel_code, ''))) = 'WHATSAPP'
            LIMIT 1
            FOR UPDATE
            """,
            (channel_message_log_id,),
        )
        message_row = cursor.fetchone()

        if not message_row:
            conn.rollback()
            return {"sent": False, "reason": "CHANNEL_MESSAGE_LOG_NOT_FOUND"}

        root_sender = _safe_str(message_row.get("sender_mobile"))
        if root_sender and root_sender != sender_mobile:
            conn.rollback()
            return {"sent": False, "reason": "SENDER_MISMATCH"}

        message_received_on = (
            message_row.get("received_on")
            or received_on
            or _now_india_naive()
        )

        # Only an OPEN/PENDING submission is still collecting WhatsApp events.
        # Once the scheduler claims it as PROCESSING, a later inbound message
        # starts a new submission window instead of modifying the closed window.
        cursor.execute(
            f"""
            SELECT *
            FROM {TRACKING_TABLE}
            WHERE sender_mobile = %s
              AND session_status = %s
              AND parser_outcome = %s
            ORDER BY id DESC
            LIMIT 1
            FOR UPDATE
            """,
            (sender_mobile, SESSION_OPEN, OUTCOME_PENDING),
        )
        tracking_row = cursor.fetchone()

        if tracking_row:
            root_id = int(tracking_row.get("channel_message_log_id"))

            if int(channel_message_log_id) != root_id:
                cursor.execute(
                    """
                    UPDATE channel_message_log
                    SET
                        linked_triage_id = %s,
                        parsed_flag = 0,
                        parse_status = 'PENDING',
                        linked_queue_id = NULL,
                        channel_thread_id = NULL,
                        requires_manual_review = 0,
                        junk_flag = 0,
                        remarks = 'Grouped into open WhatsApp submission window.',
                        updated_on = NOW()
                    WHERE id = %s
                    """,
                    (root_id, channel_message_log_id),
                )

            cursor.execute(
                f"""
                UPDATE {TRACKING_TABLE}
                SET
                    last_message_received_on = CASE
                        WHEN last_message_received_on IS NULL THEN %s
                        WHEN last_message_received_on < %s THEN %s
                        ELSE last_message_received_on
                    END,
                    updated_on = NOW()
                WHERE id = %s
                """,
                (
                    message_received_on,
                    message_received_on,
                    message_received_on,
                    tracking_row.get("id"),
                ),
            )

            conn.commit()

            logger.info(
                "WHATSAPP_SUBMISSION_WINDOW_EXTENDED tracking_id=%s root_id=%s child_id=%s mobile=%s",
                tracking_row.get("id"),
                root_id,
                channel_message_log_id,
                sender_mobile,
            )

            return {
                "sent": False,
                "reason": "WINDOW_EXTENDED",
                "tracking_id": tracking_row.get("id"),
                "wa_submission_id": tracking_row.get("wa_submission_id"),
                "channel_message_log_id": root_id,
            }

        # No collection window is currently OPEN. This message becomes the root
        # of a new submission. No user-facing WA Ref is sent yet.
        root_id = int(channel_message_log_id)
        wa_submission_id = build_wa_submission_id(
            channel_message_log_id=root_id,
            received_on=message_received_on,
        )

        cursor.execute(
            f"""
            INSERT INTO {TRACKING_TABLE}
            (
                channel_message_log_id,
                sender_mobile,
                wa_submission_id,
                submission_received_on,
                last_message_received_on,
                session_status,
                parser_outcome,
                notification_status,
                processing_message_status,
                session_close_message_status,
                created_on,
                updated_on
            )
            VALUES
            (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                NOW(), NOW()
            )
            """,
            (
                root_id,
                sender_mobile,
                wa_submission_id,
                message_received_on,
                message_received_on,
                SESSION_OPEN,
                OUTCOME_PENDING,
                NOTIFICATION_NOT_READY,
                "NOT_SENT",
                CLOSE_MESSAGE_NOT_SENT,
            ),
        )
        tracking_id = cursor.lastrowid

        # The root deliberately keeps linked_triage_id NULL.
        cursor.execute(
            """
            UPDATE channel_message_log
            SET
                linked_triage_id = NULL,
                parsed_flag = 0,
                parse_status = 'PENDING',
                linked_queue_id = NULL,
                channel_thread_id = NULL,
                requires_manual_review = 0,
                junk_flag = 0,
                remarks = 'Root of WhatsApp submission collection window.',
                updated_on = NOW()
            WHERE id = %s
            """,
            (root_id,),
        )

        conn.commit()

        logger.info(
            "WHATSAPP_SUBMISSION_WINDOW_OPENED tracking_id=%s root_id=%s mobile=%s wa_ref=%s",
            tracking_id,
            root_id,
            sender_mobile,
            wa_submission_id,
        )

        return {
            "sent": False,
            "reason": "WINDOW_OPENED",
            "tracking_id": tracking_id,
            "wa_submission_id": wa_submission_id,
            "channel_message_log_id": root_id,
        }

    except Exception:
        conn.rollback()
        logger.exception(
            "WHATSAPP_SUBMISSION_WINDOW_REGISTER_FAILED mobile=%s channel_message_log_id=%s",
            sender_mobile,
            channel_message_log_id,
        )
        raise
    finally:
        cursor.close()
        conn.close()


# ============================================================
# OUTCOME DETECTION
# ============================================================

def _detect_terminal_outcomes(batch_size=DEFAULT_BATCH_SIZE):
    """
    Detect one GROUP outcome from the root channel_message_log row.

    MANUAL_REVIEW is deliberately not final forever: if the same root is later
    promoted to PARSED + channel_thread_id by Manual Review re-processing, this
    function upgrades MANUAL_REVIEW -> SUCCESS and schedules the final patient
    acknowledgement without repeating the WA Ref.
    """
    summary = {
        "success_detected": 0,
        "manual_review_detected": 0,
        "junk_detected": 0,
        "duplicate_detected": 0,
    }

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        _assert_tracking_table(cursor)

        cursor.execute(
            f"""
            SELECT
                t.id AS tracking_id,
                t.parser_outcome,
                t.notification_status,
                t.session_close_message_status,
                c.id AS channel_message_log_id,
                c.parse_status,
                c.junk_flag,
                c.duplicate_flag,
                c.channel_thread_id,
                c.updated_on
            FROM {TRACKING_TABLE} t
            INNER JOIN channel_message_log c
                ON c.id = t.channel_message_log_id
            WHERE t.parser_outcome IN (%s, %s)
            ORDER BY c.updated_on ASC, t.id ASC
            LIMIT %s
            """,
            (
                OUTCOME_PENDING,
                OUTCOME_MANUAL_REVIEW,
                int(batch_size or DEFAULT_BATCH_SIZE),
            ),
        )
        candidates = cursor.fetchall() or []

        for row in candidates:
            current_outcome = _safe_str(row.get("parser_outcome")).upper()
            parse_status = _safe_str(row.get("parse_status")).upper()
            junk_flag = int(row.get("junk_flag") or 0)
            duplicate_flag = int(row.get("duplicate_flag") or 0)
            channel_thread_id = _safe_str(row.get("channel_thread_id"))

            outcome = None

            # A blocked same-passport record is a terminal negative outcome.
            # Reuse OUTCOME_JUNK internally so no new DB enum/schema value is needed;
            # duplicate_flag remains the business discriminator and selects the
            # dedicated WhatsApp closure message.
            if current_outcome == OUTCOME_PENDING and duplicate_flag == 1:
                outcome = OUTCOME_JUNK
            # Existing success/manual-review behavior remains unchanged.
            elif parse_status == "PARSED" and channel_thread_id:
                outcome = OUTCOME_SUCCESS
            elif current_outcome == OUTCOME_PENDING:
                if junk_flag == 1 or parse_status == "JUNK":
                    outcome = OUTCOME_JUNK
                elif parse_status in {"MANUAL_REVIEW", "FAILED"}:
                    outcome = OUTCOME_MANUAL_REVIEW

            if not outcome or outcome == current_outcome:
                continue

            notification_status = row.get("notification_status")
            close_message_status = row.get("session_close_message_status")

            if outcome == OUTCOME_SUCCESS:
                if _safe_str(notification_status).upper() in {
                    "",
                    NOTIFICATION_NOT_READY,
                }:
                    notification_status = NOTIFICATION_PENDING
            else:
                if _safe_str(close_message_status).upper() in {
                    "",
                    CLOSE_MESSAGE_NOT_SENT,
                }:
                    close_message_status = CLOSE_MESSAGE_PENDING

            cursor.execute(
                f"""
                UPDATE {TRACKING_TABLE}
                SET
                    parser_outcome = %s,
                    parser_outcome_detected_on = NOW(),
                    final_channel_thread_id = %s,
                    notification_status = %s,
                    session_close_message_status = %s,
                    updated_on = NOW()
                WHERE id = %s
                  AND parser_outcome = %s
                """,
                (
                    outcome,
                    channel_thread_id if outcome == OUTCOME_SUCCESS else None,
                    notification_status,
                    close_message_status,
                    row.get("tracking_id"),
                    current_outcome,
                ),
            )

            if cursor.rowcount != 1:
                continue

            if outcome == OUTCOME_SUCCESS:
                summary["success_detected"] += 1
            elif outcome == OUTCOME_MANUAL_REVIEW:
                summary["manual_review_detected"] += 1
            elif outcome == OUTCOME_JUNK:
                summary["junk_detected"] += 1
                if duplicate_flag == 1:
                    summary["duplicate_detected"] += 1

            logger.info(
                "WHATSAPP_SUBMISSION_OUTCOME_DETECTED tracking_id=%s root_id=%s old=%s new=%s thread=%s",
                row.get("tracking_id"),
                row.get("channel_message_log_id"),
                current_outcome,
                outcome,
                channel_thread_id or "-",
            )

        conn.commit()
        return summary

    except Exception:
        conn.rollback()
        logger.exception("WHATSAPP_SUBMISSION_OUTCOME_DETECTION_FAILED")
        raise
    finally:
        cursor.close()
        conn.close()


# ============================================================
# CONVERSATION / SESSION HELPERS
# ============================================================

def _sender_has_open_conversation(sender_mobile):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        _assert_tracking_table(cursor)

        cursor.execute(
            f"""
            SELECT id
            FROM {TRACKING_TABLE}
            WHERE sender_mobile = %s
              AND session_status IN (%s, %s)
            ORDER BY id DESC
            LIMIT 1
            """,
            (sender_mobile, SESSION_OPEN, SESSION_PROCESSING),
        )
        if cursor.fetchone():
            return True

        cursor.execute(
            """
            SELECT conversation_state
            FROM whatsapp_conversation_state
            WHERE sender_mobile = %s
              AND user_category = %s
            ORDER BY updated_on DESC, id DESC
            LIMIT 1
            """,
            (sender_mobile, CATEGORY_AGENCY),
        )
        state = cursor.fetchone() or {}
        return _safe_str(state.get("conversation_state")).upper() not in {
            "",
            STATE_NEW,
        }
    finally:
        cursor.close()
        conn.close()


def _reset_conversation_after_current_submission(sender_mobile):
    """Reset to NEW only when no newer submission is OPEN/PROCESSING."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        _assert_tracking_table(cursor)

        cursor.execute(
            f"""
            SELECT id
            FROM {TRACKING_TABLE}
            WHERE sender_mobile = %s
              AND session_status IN (%s, %s)
            ORDER BY id DESC
            LIMIT 1
            """,
            (sender_mobile, SESSION_OPEN, SESSION_PROCESSING),
        )
        if cursor.fetchone():
            return False

        cursor.execute(
            """
            UPDATE whatsapp_conversation_state
            SET
                conversation_state = %s,
                consent_required_flag = 0,
                language_selected_flag = 0,
                language_selected_on = NULL,
                processing_message_status = 'NOT_SENT',
                processing_message_id = NULL,
                processing_message_sent_on = NULL,
                updated_on = NOW()
            WHERE sender_mobile = %s
              AND user_category = %s
            ORDER BY updated_on DESC, id DESC
            LIMIT 1
            """,
            (STATE_NEW, sender_mobile, CATEGORY_AGENCY),
        )
        updated = cursor.rowcount == 1
        conn.commit()

        logger.info(
            "WHATSAPP_SUBMISSION_CONVERSATION_RESET mobile=%s reset=%s",
            sender_mobile,
            int(updated),
        )
        return updated

    except Exception:
        conn.rollback()
        logger.exception(
            "WHATSAPP_SUBMISSION_CONVERSATION_RESET_FAILED mobile=%s",
            sender_mobile,
        )
        return False
    finally:
        cursor.close()
        conn.close()


def _close_tracking_session(tracking_id, close_reason):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            UPDATE {TRACKING_TABLE}
            SET
                session_status = %s,
                session_closed_on = COALESCE(session_closed_on, NOW()),
                session_close_reason = %s,
                updated_on = NOW()
            WHERE id = %s
              AND session_status IN (%s, %s)
            """,
            (
                SESSION_CLOSED,
                close_reason,
                tracking_id,
                SESSION_OPEN,
                SESSION_PROCESSING,
            ),
        )
        changed = cursor.rowcount == 1
        conn.commit()
        return changed
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


# ============================================================
# MANUAL REVIEW / JUNK : ONE WA REF ONLY
# ============================================================

def _claim_wa_ref_notification(tracking_id, retry_failed=False):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        allowed = [CLOSE_MESSAGE_PENDING]
        if retry_failed:
            allowed.append(CLOSE_MESSAGE_FAILED)
        placeholders = ", ".join(["%s"] * len(allowed))

        cursor.execute(
            f"SELECT * FROM {TRACKING_TABLE} WHERE id = %s LIMIT 1 FOR UPDATE",
            (tracking_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            return None

        current = _safe_str(row.get("session_close_message_status")).upper()
        if current in {CLOSE_MESSAGE_SENT, CLOSE_MESSAGE_SENDING}:
            conn.commit()
            return None
        if current not in allowed:
            conn.commit()
            return None

        cursor.execute(
            f"""
            UPDATE {TRACKING_TABLE}
            SET
                session_close_message_status = %s,
                session_close_message_last_error = NULL,
                updated_on = NOW()
            WHERE id = %s
              AND session_close_message_status IN ({placeholders})
            """,
            (CLOSE_MESSAGE_SENDING, tracking_id, *allowed),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return None

        conn.commit()
        row["session_close_message_status"] = CLOSE_MESSAGE_SENDING
        return row
    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _mark_wa_ref_sent(tracking_id, meta_message_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            UPDATE {TRACKING_TABLE}
            SET
                session_close_message_status = %s,
                session_close_message_id = %s,
                session_close_message_sent_on = NOW(),
                session_close_message_last_error = NULL,
                updated_on = NOW()
            WHERE id = %s
              AND session_close_message_status = %s
            """,
            (
                CLOSE_MESSAGE_SENT,
                meta_message_id,
                tracking_id,
                CLOSE_MESSAGE_SENDING,
            ),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        cursor.close()
        conn.close()


def _mark_wa_ref_failed(tracking_id, error):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            UPDATE {TRACKING_TABLE}
            SET
                session_close_message_status = %s,
                session_close_message_last_error = %s,
                updated_on = NOW()
            WHERE id = %s
              AND session_close_message_status = %s
            """,
            (
                CLOSE_MESSAGE_FAILED,
                _safe_str(error)[:500],
                tracking_id,
                CLOSE_MESSAGE_SENDING,
            ),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def _send_wa_ref_notification(tracking_id, retry_failed=False):
    row = _claim_wa_ref_notification(tracking_id, retry_failed=retry_failed)
    if not row:
        return {"sent": False, "reason": "NOT_CLAIMED"}

    try:
        language = _get_preferred_language(row.get("sender_mobile"))
        closure_context = _fetch_duplicate_closure_context(
            row.get("channel_message_log_id")
        )

        if int(closure_context.get("duplicate_flag") or 0) == 1:
            message_text = _build_duplicate_closure_text(
                passport_no=closure_context.get("passport_no"),
                existing_workflow_status=closure_context.get(
                    "existing_workflow_status"
                ),
                existing_thread_id=closure_context.get("existing_thread_id"),
                language=language,
            )
        elif _safe_str(row.get("parser_outcome")).upper() == OUTCOME_MANUAL_REVIEW:
            # WA Ref is exposed ONLY for Manual Review / Manual Queue cases.
            message_text = _build_manual_review_text(
                row.get("wa_submission_id"),
                language=language,
            )
        else:
            # Generic parser/JUNK failure: error message only, no WA Ref.
            message_text = _build_processing_error_text(
                language=language,
            )

        meta_response = send_text(
            row.get("sender_mobile"),
            message_text,
            preview_url=False,
        )
        meta_message_id = extract_outbound_message_id(meta_response)
        if not meta_message_id:
            raise RuntimeError("Meta accepted no auditable closure message id.")

        _mark_wa_ref_sent(row.get("id"), meta_message_id)
        logger.info(
            "WHATSAPP_WA_REF_SENT tracking_id=%s mobile=%s wa_ref=%s outcome=%s message_id=%s",
            row.get("id"),
            row.get("sender_mobile"),
            row.get("wa_submission_id"),
            row.get("parser_outcome"),
            meta_message_id,
        )
        return {"sent": True, "message_id": meta_message_id, "row": row}

    except Exception as exc:
        _mark_wa_ref_failed(row.get("id"), exc)
        logger.exception(
            "WHATSAPP_WA_REF_SEND_FAILED tracking_id=%s mobile=%s wa_ref=%s",
            row.get("id"),
            row.get("sender_mobile"),
            row.get("wa_submission_id"),
        )
        return {
            "sent": False,
            "reason": "SEND_FAILED",
            "error": str(exc),
            "row": row,
        }


# ============================================================
# SUCCESS : PATIENT + ORIGINAL DATE/TIME + THREAD ID
# ============================================================

def _claim_success_notification(tracking_id, retry_failed=False):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        allowed = [NOTIFICATION_PENDING]
        if retry_failed:
            allowed.append(NOTIFICATION_FAILED)
        placeholders = ", ".join(["%s"] * len(allowed))

        cursor.execute(
            f"SELECT * FROM {TRACKING_TABLE} WHERE id = %s LIMIT 1 FOR UPDATE",
            (tracking_id,),
        )
        row = cursor.fetchone()
        if not row:
            conn.rollback()
            return None

        current = _safe_str(row.get("notification_status")).upper()
        if current in {NOTIFICATION_SENT, NOTIFICATION_SENDING}:
            conn.commit()
            return None
        if current not in allowed:
            conn.commit()
            return None

        cursor.execute(
            f"""
            UPDATE {TRACKING_TABLE}
            SET
                notification_status = %s,
                notification_attempt_count = COALESCE(notification_attempt_count, 0) + 1,
                notification_last_error = NULL,
                updated_on = NOW()
            WHERE id = %s
              AND notification_status IN ({placeholders})
            """,
            (NOTIFICATION_SENDING, tracking_id, *allowed),
        )
        if cursor.rowcount != 1:
            conn.rollback()
            return None

        # Use the same transaction to resolve the patient display name.
        patient_name = _fetch_patient_name_for_thread(
            cursor,
            row.get("final_channel_thread_id"),
        )
        conn.commit()
        row["notification_status"] = NOTIFICATION_SENDING
        row["patient_name"] = patient_name
        return row

    except Exception:
        conn.rollback()
        raise
    finally:
        cursor.close()
        conn.close()


def _mark_success_sent(tracking_id, meta_message_id):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            UPDATE {TRACKING_TABLE}
            SET
                notification_status = %s,
                notification_message_id = %s,
                notification_sent_on = NOW(),
                notification_last_error = NULL,
                updated_on = NOW()
            WHERE id = %s
              AND notification_status = %s
            """,
            (
                NOTIFICATION_SENT,
                meta_message_id,
                tracking_id,
                NOTIFICATION_SENDING,
            ),
        )
        conn.commit()
        return cursor.rowcount == 1
    finally:
        cursor.close()
        conn.close()


def _mark_success_failed(tracking_id, error):
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            UPDATE {TRACKING_TABLE}
            SET
                notification_status = %s,
                notification_last_error = %s,
                updated_on = NOW()
            WHERE id = %s
              AND notification_status = %s
            """,
            (
                NOTIFICATION_FAILED,
                _safe_str(error)[:500],
                tracking_id,
                NOTIFICATION_SENDING,
            ),
        )
        conn.commit()
    finally:
        cursor.close()
        conn.close()


def _send_success_notification(tracking_id, retry_failed=False):
    row = _claim_success_notification(
        tracking_id=tracking_id,
        retry_failed=retry_failed,
    )
    if not row:
        return {"sent": False, "reason": "NOT_CLAIMED"}

    thread_id = _safe_str(row.get("final_channel_thread_id"))
    if not thread_id:
        _mark_success_failed(tracking_id, "SUCCESS has no channel_thread_id")
        return {
            "sent": False,
            "reason": "MISSING_THREAD_ID",
            "row": row,
        }

    try:
        language = _get_preferred_language(row.get("sender_mobile"))
        message = _build_success_text(
            patient_name=row.get("patient_name"),
            submission_received_on=row.get("submission_received_on"),
            channel_thread_id=thread_id,
            wa_submission_id=row.get("wa_submission_id"),
            language=language,
        )
        meta_response = send_text(
            row.get("sender_mobile"),
            message,
            preview_url=False,
        )
        meta_message_id = extract_outbound_message_id(meta_response)
        if not meta_message_id:
            raise RuntimeError(
                "Meta accepted no auditable success notification id."
            )

        _mark_success_sent(row.get("id"), meta_message_id)
        logger.info(
            "WHATSAPP_SUCCESS_NOTIFICATION_SENT tracking_id=%s mobile=%s thread=%s message_id=%s",
            row.get("id"),
            row.get("sender_mobile"),
            thread_id,
            meta_message_id,
        )
        return {"sent": True, "message_id": meta_message_id, "row": row}

    except Exception as exc:
        _mark_success_failed(row.get("id"), exc)
        logger.exception(
            "WHATSAPP_SUCCESS_NOTIFICATION_SEND_FAILED tracking_id=%s mobile=%s thread=%s",
            row.get("id"),
            row.get("sender_mobile"),
            thread_id,
        )
        return {
            "sent": False,
            "reason": "SEND_FAILED",
            "error": str(exc),
            "row": row,
        }


# ============================================================
# DELIVERY ORCHESTRATION
# ============================================================

def _deliver_manual_review_and_junk(batch_size=DEFAULT_BATCH_SIZE, retry_failed=False):
    summary = {"sent": 0, "failed": 0, "closed": 0}

    statuses = [CLOSE_MESSAGE_PENDING]
    if retry_failed:
        statuses.append(CLOSE_MESSAGE_FAILED)
    placeholders = ", ".join(["%s"] * len(statuses))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        _assert_tracking_table(cursor)
        cursor.execute(
            f"""
            SELECT *
            FROM {TRACKING_TABLE}
            WHERE parser_outcome IN (%s, %s)
              AND session_close_message_status IN ({placeholders})
            ORDER BY parser_outcome_detected_on ASC, id ASC
            LIMIT %s
            """,
            (
                OUTCOME_MANUAL_REVIEW,
                OUTCOME_JUNK,
                *statuses,
                int(batch_size or DEFAULT_BATCH_SIZE),
            ),
        )
        candidates = cursor.fetchall() or []
    finally:
        cursor.close()
        conn.close()

    for row in candidates:
        result = _send_wa_ref_notification(
            row.get("id"),
            retry_failed=retry_failed,
        )

        if result.get("sent"):
            summary["sent"] += 1
        elif result.get("reason") == "SEND_FAILED":
            summary["failed"] += 1

        # The collection session ends once the group has a settled MANUAL/JUNK
        # outcome. A failed Meta send is retriable from the CLOSED row.
        closed = _close_tracking_session(
            row.get("id"),
            _safe_str(row.get("parser_outcome")) or "MANUAL_REVIEW",
        )
        if closed:
            summary["closed"] += 1
            _reset_conversation_after_current_submission(row.get("sender_mobile"))

    return summary


def _deliver_current_successes(batch_size=DEFAULT_BATCH_SIZE, retry_failed=False):
    summary = {"sent": 0, "failed": 0, "closed": 0}

    statuses = [NOTIFICATION_PENDING]
    if retry_failed:
        statuses.append(NOTIFICATION_FAILED)
    placeholders = ", ".join(["%s"] * len(statuses))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        _assert_tracking_table(cursor)
        cursor.execute(
            f"""
            SELECT *
            FROM {TRACKING_TABLE}
            WHERE session_status IN (%s, %s)
              AND parser_outcome = %s
              AND notification_status IN ({placeholders})
            ORDER BY parser_outcome_detected_on ASC, id ASC
            LIMIT %s
            """,
            (
                SESSION_OPEN,
                SESSION_PROCESSING,
                OUTCOME_SUCCESS,
                *statuses,
                int(batch_size or DEFAULT_BATCH_SIZE),
            ),
        )
        candidates = cursor.fetchall() or []
    finally:
        cursor.close()
        conn.close()

    for row in candidates:
        result = _send_success_notification(
            row.get("id"),
            retry_failed=retry_failed,
        )
        if result.get("sent"):
            summary["sent"] += 1
        elif result.get("reason") == "SEND_FAILED":
            summary["failed"] += 1

        closed = _close_tracking_session(row.get("id"), OUTCOME_SUCCESS)
        if closed:
            summary["closed"] += 1
            _reset_conversation_after_current_submission(row.get("sender_mobile"))

    return summary


def _deliver_deferred_successes(batch_size=DEFAULT_BATCH_SIZE, retry_failed=False):
    """
    A MANUAL_REVIEW may become SUCCESS after its original session was closed.
    Send Patient + original Date/Time + Thread ID only when the Agency is not in
    another active WhatsApp conversation. The old WA Ref is never repeated.
    """
    summary = {
        "sent": 0,
        "failed": 0,
        "deferred_open_conversation": 0,
    }

    statuses = [NOTIFICATION_PENDING]
    if retry_failed:
        statuses.append(NOTIFICATION_FAILED)
    placeholders = ", ".join(["%s"] * len(statuses))

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        _assert_tracking_table(cursor)
        cursor.execute(
            f"""
            SELECT *
            FROM {TRACKING_TABLE}
            WHERE session_status = %s
              AND parser_outcome = %s
              AND notification_status IN ({placeholders})
            ORDER BY parser_outcome_detected_on ASC, id ASC
            LIMIT %s
            """,
            (
                SESSION_CLOSED,
                OUTCOME_SUCCESS,
                *statuses,
                int(batch_size or DEFAULT_BATCH_SIZE),
            ),
        )
        candidates = cursor.fetchall() or []
    finally:
        cursor.close()
        conn.close()

    for row in candidates:
        sender_mobile = row.get("sender_mobile")
        if _sender_has_open_conversation(sender_mobile):
            summary["deferred_open_conversation"] += 1
            logger.info(
                "WHATSAPP_MANUAL_SUCCESS_DEFERRED_OPEN_CONVERSATION tracking_id=%s mobile=%s thread=%s",
                row.get("id"),
                sender_mobile,
                row.get("final_channel_thread_id") or "-",
            )
            continue

        result = _send_success_notification(
            row.get("id"),
            retry_failed=retry_failed,
        )
        if result.get("sent"):
            summary["sent"] += 1
        elif result.get("reason") == "SEND_FAILED":
            summary["failed"] += 1

    return summary


# ============================================================
# SCHEDULER ENTRY POINT
# ============================================================

def process_pending_submission_acknowledgements(
    batch_size=DEFAULT_BATCH_SIZE,
    retry_failed=False,
):
    """
    Final WhatsApp acknowledgement rules:

    SUCCESS
        -> Patient Name + original Submission Date/Time + channel_thread_id.
        -> No WA Ref is sent.

    MANUAL_REVIEW
        -> One WA Ref for the entire submission window.

    JUNK / PROCESSING ERROR
        -> Error message only. No WA Ref.

    DUPLICATE
        -> Duplicate error message. No WA Ref.
        -> Existing channel_thread_id may be shown as Ref # when available.

    MANUAL_REVIEW -> later SUCCESS
        -> Patient Name + original Submission Date/Time + channel_thread_id.
        -> The earlier WA Ref is NOT repeated.
    """
    summary = {
        "success_detected": 0,
        "manual_review_detected": 0,
        "junk_detected": 0,
        "wa_ref_sent": 0,
        "wa_ref_failed": 0,
        "current_sent": 0,
        "current_failed": 0,
        "processing_sessions_closed": 0,
        "processing_timeout_messages_sent": 0,
        "processing_timeout_message_failures": 0,
        "deferred_sent": 0,
        "deferred_failed": 0,
        "deferred_open_conversation": 0,
    }

    detected = _detect_terminal_outcomes(batch_size=batch_size)
    summary.update(detected)

    negative = _deliver_manual_review_and_junk(
        batch_size=batch_size,
        retry_failed=retry_failed,
    )
    summary["wa_ref_sent"] = negative.get("sent", 0)
    summary["wa_ref_failed"] = negative.get("failed", 0)
    summary["processing_sessions_closed"] += negative.get("closed", 0)

    current = _deliver_current_successes(
        batch_size=batch_size,
        retry_failed=retry_failed,
    )
    summary["current_sent"] = current.get("sent", 0)
    summary["current_failed"] = current.get("failed", 0)
    summary["processing_sessions_closed"] += current.get("closed", 0)

    deferred = _deliver_deferred_successes(
        batch_size=batch_size,
        retry_failed=retry_failed,
    )
    summary["deferred_sent"] = deferred.get("sent", 0)
    summary["deferred_failed"] = deferred.get("failed", 0)
    summary["deferred_open_conversation"] = deferred.get(
        "deferred_open_conversation",
        0,
    )

    logger.info(
        "WHATSAPP_SUBMISSION_ACK_JOB_COMPLETED "
        "success=%s manual=%s junk=%s wa_ref_sent=%s current_success=%s "
        "closed=%s deferred_success=%s deferred_open=%s",
        summary.get("success_detected", 0),
        summary.get("manual_review_detected", 0),
        summary.get("junk_detected", 0),
        summary.get("wa_ref_sent", 0),
        summary.get("current_sent", 0),
        summary.get("processing_sessions_closed", 0),
        summary.get("deferred_sent", 0),
        summary.get("deferred_open_conversation", 0),
    )

    return summary