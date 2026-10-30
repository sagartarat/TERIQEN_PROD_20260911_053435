import re
import json
import smtplib
from datetime import datetime, date
from email.message import EmailMessage
from urllib.parse import quote

import mysql.connector
from flask import current_app

from app.common.db import get_db_connection
from app.queue_management.rule_engine import (
    build_operation_queue_insert_payload,
    should_create_operation_queue_row,
)


OPERATION_QUEUE_EDITABLE_FIELDS = [
    "ivac_center",
    "hospital_shrtnm",
    "hospital_dept",
    "doctor_nm",
    "intent_remarks",
    "priority_status",
    "vil_date",
    "appointment_dt",
    "assigned_to",
    "parse_remarks",
]


PARSE_REMARKS_MAP = {
    "PENDING": "PENDING",
    "READY_FOR_PARSE": "READY FOR PARSE",
    "PARSING": "PARSING",
    "PARSED": "SUCCESS",
    "PARTIAL": "PARTIAL",
    "MANUAL_REVIEW": "MANUAL REVIEW",
    "LINKED": "FOLLOW-UP",
    "FAILED": "REJECTED",
    "JUNK": "DUPLICATE",
}


def _has_media_download_failure(channel_meta=None):
    """
    Detect WhatsApp media download failures from attachment metadata only.
    This helper does not change attachment insertion/downloading flow; it only
    converts the business-facing Operation Remarks to ATTACHMENT FAILED.
    """
    channel_meta = channel_meta or {}
    raw_attachment_json = channel_meta.get("attachment_json")

    if raw_attachment_json in (None, "", "None"):
        return False

    try:
        if isinstance(raw_attachment_json, list):
            attachments = raw_attachment_json
        elif isinstance(raw_attachment_json, str) and raw_attachment_json.strip():
            attachments = json.loads(raw_attachment_json)
        else:
            attachments = []
    except Exception:
        attachments = []

    for item in attachments or []:
        item = item or {}
        remarks_text = _safe_str(item.get("remarks")).upper()
        file_path = _safe_str(item.get("file_path"))

        if remarks_text and any(token in remarks_text for token in ("FAILED", "ERROR", "401", "AUTHENTICATION")):
            return True

        if _safe_str(item.get("media_id")) and not file_path and remarks_text:
            return True

    return False


def _get_operation_remarks(parse_status=None, thread_resolution_status=None, channel_meta=None):
    """
    Business-facing Operation Remarks shown in Operation Queue.
    Existing parse_status, thread resolution, insert/update and attachment flows
    are not changed by this helper.
    """
    if _has_media_download_failure(channel_meta):
        return "ATTACHMENT FAILED"

    thread_resolution_status = _safe_str(thread_resolution_status).upper()
    if thread_resolution_status == "EXISTING_THREAD":
        return "FOLLOW-UP"

    parse_status = _safe_str(parse_status).upper()
    return PARSE_REMARKS_MAP.get(parse_status, "PENDING")




def _generate_new_channel_thread_id(passport_no=None):
    """
    Generate a fresh channel_thread_id for a new WhatsApp request.

    Required old project format:
    THR-A000001-20260516153000
    """
    passport_no = _safe_str(passport_no).upper()
    passport_part = re.sub(r"[^A-Z0-9]", "", passport_no) if passport_no else "UNKNOWN"
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    return f"THR-{passport_part}-{timestamp}"


def resolve_thread_context(passport_no):
    """
    Resolve continuity only against the latest OPEN operation queue record.

    Critical rule:
    - If the latest row for the same passport is CLOSED, do not reuse its
      channel_thread_id, assigned_to, or wrkflw_status.
    - Return a fresh channel_thread_id so operation_queue_master creates a
      completely separate new record.
    """
    passport_no = _safe_str(passport_no)
    if not passport_no:
        return {
            "channel_thread_id": _generate_new_channel_thread_id(None),
            "assigned_to": None,
            "wrkflw_status": None,
            "new_thread": True,
        }

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                id,
                channel_thread_id,
                assigned_to,
                wrkflw_status,
                parse_status
            FROM operation_queue_master
            WHERE UPPER(TRIM(IFNULL(patpassport_no, ''))) = UPPER(TRIM(%s))
            ORDER BY id DESC
            LIMIT 1
            """,
            (passport_no,),
        )

        latest_row = cursor.fetchone() or {}
        latest_status = _safe_str(latest_row.get("wrkflw_status")).upper()
        latest_parse_status = _safe_str(latest_row.get("parse_status")).upper()
        latest_thread_id = _safe_str(latest_row.get("channel_thread_id"))

        # An unresolved MANUAL_REVIEW row is only a temporary review record.
        # It must not be treated as an existing business thread after the user
        # corrects the message and sends it back to the parser.
        #
        # A record is considered an existing thread only when it has a real
        # channel_thread_id and is still open.
        if (
            not latest_row
            or latest_status == "CLOSED"
            or latest_parse_status == "MANUAL_REVIEW"
            or not latest_thread_id
        ):
            return {
                "channel_thread_id": _generate_new_channel_thread_id(passport_no),
                "assigned_to": None,
                "wrkflw_status": None,
                "new_thread": True,
                "closed_previous_queue_id": latest_row.get("id"),
            }

        return {
            "channel_thread_id": latest_row.get("channel_thread_id"),
            "assigned_to": latest_row.get("assigned_to"),
            "wrkflw_status": latest_row.get("wrkflw_status"),
            "new_thread": False,
            "operation_queue_id": latest_row.get("id"),
        }

    except Exception:
        # Fail safe: never reuse an old thread if continuity lookup fails.
        return {
            "channel_thread_id": _generate_new_channel_thread_id(passport_no),
            "assigned_to": None,
            "wrkflw_status": None,
            "new_thread": True,
        }

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def _safe_str(value):
    if value is None:
        return ""
    return str(value).strip()


def _normalize_department_csv(value):
    """
    Normalize one or more department values into the existing comma-separated
    storage format without changing the one-request/one-queue-row design.

    Accepted separators: comma, semicolon and pipe. Values are trimmed,
    upper-cased and de-duplicated while preserving their original order.
    """
    raw = _safe_str(value)
    if not raw:
        return ""

    normalized = raw.replace("|", ",").replace(";", ",")
    seen = set()
    departments = []

    for item in normalized.split(","):
        department = _safe_str(item).upper()
        if not department or department in seen:
            continue
        seen.add(department)
        departments.append(department)

    return ",".join(departments)


def _debug_compare_log(*parts):
    return None


def _debug_value_repr(value):
    if value is None:
        return "None"

    try:
        text = str(value)
    except Exception:
        return repr(value)

    return repr(text)


def normalize_patient_gender(val, default_if_blank=None):
    val = (val or "").strip().upper()

    if not val:
        return default_if_blank

    if val in ("M", "MALE"):
        return "Male"
    if val in ("F", "FEMALE"):
        return "Female"
    if val in ("O", "OTHER", "OTHERS"):
        return "Others"

    return default_if_blank



def _normalize_db_date(value):
    if value in (None, ""):
        return None

    try:
        if isinstance(value, datetime):
            return value.date()

        if isinstance(value, date):
            return value

        value = str(value).strip()
        if not value:
            return None

        # Common DB-safe format
        return datetime.strptime(value[:10], "%Y-%m-%d").date()
    except Exception:
        return None


def _calc_age_for_workflow(dob_value):
    dob_value = _normalize_db_date(dob_value)
    if not dob_value:
        return None

    try:
        today = datetime.today().date()
        return today.year - dob_value.year - (
            (today.month, today.day) < (dob_value.month, dob_value.day)
        )
    except Exception:
        return None


def _build_attender_payload(row, prefix):
    row = row or {}
    prefix = _safe_str(prefix)

    dob_value = _normalize_db_date(row.get(f"{prefix}_dob"))
    pass_expiry_value = _normalize_db_date(row.get(f"{prefix}pass_expirydt"))

    return {
        "passport_no": row.get(f"{prefix}passport_no"),
        "relation": row.get(f"{prefix}pat_relation"),
        "givennm": row.get(f"{prefix}_givennm"),
        "surnm": row.get(f"{prefix}_surnm"),
        "dob": dob_value,
        "country": row.get(f"{prefix}_country"),
        "gender": normalize_patient_gender(row.get(f"{prefix}_gender"), default_if_blank=None),
        "contno": row.get(f"{prefix}_contno"),
        "age": _calc_age_for_workflow(dob_value),
        "pass_expirydt": pass_expiry_value,
        "address": row.get(f"{prefix}_address"),
        "email": row.get(f"{prefix}_email"),
    }


def _generate_workflow_internal_numbers(conn, row, wrkflw_status):
    from app.lead.helpers import (
        get_country_code,
        build_ld_internalno,
        build_vil_internalno,
    )

    cursor = conn.cursor(dictionary=True)
    try:
        existing_ld_internalno = _safe_str((row or {}).get("ld_internalno"))
        existing_vil_internalno = _safe_str((row or {}).get("vil_internalno"))
        existing_ld_seqno = (row or {}).get("ld_seqno")

        country_code = get_country_code(conn, (row or {}).get("patient_country"))

        ld_seqno = existing_ld_seqno
        ld_internalno = existing_ld_internalno

        if not ld_seqno:
            cursor.execute("""
                SELECT COALESCE(MAX(CAST(ld_seqno AS UNSIGNED)), 0) + 1 AS next_ld_seqno
                FROM patient_wrkflw
            """)
            seq_row = cursor.fetchone() or {}
            ld_seqno = int(seq_row.get("next_ld_seqno") or 1)

        if not ld_internalno:
            ld_internalno = build_ld_internalno(country_code, ld_seqno)

        vil_internalno = existing_vil_internalno
        if _safe_str(wrkflw_status).upper() == "VIL REQUESTED":
            if not vil_internalno:
                cursor.execute("""
                    SELECT COALESCE(
                        MAX(CAST(SUBSTRING_INDEX(vil_internalno, '-', -1) AS UNSIGNED)),
                        0
                    ) + 1 AS next_vil_seq
                    FROM patient_wrkflw
                    WHERE vil_internalno IS NOT NULL
                      AND TRIM(vil_internalno) <> ''
                """)
                vil_row = cursor.fetchone() or {}
                vil_seq = int(vil_row.get("next_vil_seq") or 1)
                vil_internalno = build_vil_internalno(
                    (row or {}).get("agent_shrtnm"),
                    country_code,
                    vil_seq,
                )
        else:
            vil_internalno = None

        return {
            "ld_seqno": ld_seqno,
            "ld_internalno": ld_internalno,
            "vil_internalno": vil_internalno,
        }
    finally:
        cursor.close()




def _get_same_passport_reingestion_decision(passport_no):
    """
    Defence-in-depth duplicate decision used by Operation Queue insertion.

    IMPORTANT BUSINESS BOUNDARY
    ---------------------------
    Only patient_wrkflw can activate the same-passport duplicate rule.

    channel_message_log, operation_queue_master and Manual Review are transient
    intake/work-in-progress stages. The same passport may legitimately receive
    corrected details or additional reports while it is in those stages, so an
    Operation Queue row must NEVER be used as the source of duplicate judgement.

    When patient_wrkflw exists, wrkflwrule_master.allow_new_case_same_passport
    is the business rule:
    - 0 -> reject a genuinely new structured case;
    - 1 -> allow a fresh case/thread;
    - missing rule/status -> reject fail-closed.

    No workflow status names are hard-coded in Python.
    """
    passport_no = _safe_str(passport_no).upper()
    if not passport_no:
        return {
            "passport_no": "",
            "has_existing_workflow": False,
            "rule_found": False,
            "allow_new_case": True,
            "latest_workflow_status": "",
            "workflow_status_source": "",
            "existing_queue_id": None,
            "existing_thread_id": None,
        }

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # patient_wrkflw is the ONLY source that can activate duplicate blocking.
        cursor.execute(
            """
            SELECT
                pw.wrkflw_status AS latest_workflow_status
            FROM patient_wrkflw pw
            WHERE UPPER(TRIM(COALESCE(pw.patpassport_no, '')))
                = UPPER(TRIM(%s))
            ORDER BY
                pw.entered_date DESC,
                COALESCE(pw.ld_seqno, 0) DESC,
                pw.ld_internalno DESC
            LIMIT 1
            """,
            (passport_no,),
        )
        workflow_row = cursor.fetchone() or {}

        # No established patient workflow = no duplicate judgement. Existing
        # Operation Queue/Manual Review records remain eligible for the normal
        # continuation/comparison/attachment flow.
        if not workflow_row:
            return {
                "passport_no": passport_no,
                "has_existing_workflow": False,
                "rule_found": False,
                "allow_new_case": True,
                "latest_workflow_status": "",
                "workflow_status_source": "",
                "existing_queue_id": None,
                "existing_thread_id": None,
            }

        effective_status = _safe_str(
            workflow_row.get("latest_workflow_status")
        ).upper()

        if not effective_status:
            return {
                "passport_no": passport_no,
                "has_existing_workflow": True,
                "rule_found": False,
                "allow_new_case": False,
                "latest_workflow_status": "UNKNOWN",
                "workflow_status_source": "PATIENT_WORKFLOW",
                "existing_queue_id": None,
                "existing_thread_id": None,
            }

        try:
            cursor.execute(
                """
                SELECT
                    status_name,
                    allow_new_case_same_passport
                FROM wrkflwrule_master
                WHERE UPPER(TRIM(COALESCE(status_name, '')))
                    = UPPER(TRIM(%s))
                LIMIT 1
                """,
                (effective_status,),
            )
            rule_row = cursor.fetchone() or {}
        except Exception as exc:
            if "allow_new_case_same_passport" in str(exc):
                raise RuntimeError(
                    "Workflow Rule Master migration is missing: "
                    "wrkflwrule_master.allow_new_case_same_passport"
                ) from exc
            raise

        rule_found = bool(_safe_str(rule_row.get("status_name")))

        try:
            allow_new_case = (
                rule_found
                and int(rule_row.get("allow_new_case_same_passport") or 0) == 1
            )
        except (TypeError, ValueError):
            allow_new_case = False

        return {
            "passport_no": passport_no,
            "has_existing_workflow": True,
            "rule_found": rule_found,
            "allow_new_case": bool(allow_new_case),
            "latest_workflow_status": effective_status,
            "workflow_status_source": "PATIENT_WORKFLOW",
            "existing_queue_id": None,
            "existing_thread_id": None,
        }
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def _same_passport_duplicate_message(decision):
    decision = decision or {}
    passport_no = _safe_str(decision.get("passport_no")).upper()
    status = _safe_str(
        decision.get("latest_workflow_status")
    ).upper() or "UNKNOWN"

    if not decision.get("rule_found"):
        return (
            f"Duplicate ingestion rejected for Passport {passport_no}. "
            f"Existing workflow status: {status}. Workflow Rule Master has "
            "no same-passport re-ingestion rule; rejected fail-closed."
        )[:500]

    return (
        f"Duplicate ingestion rejected for Passport {passport_no}. "
        f"Existing workflow status: {status}. New same-passport case is not "
        "permitted by Workflow Rule Master."
    )[:500]


def _mark_same_passport_duplicate_rejected(channel_meta, decision):
    """
    Defence-in-depth queue-layer duplicate rejection.

    Normally message_classifier_service rejects the record before parser entry.
    This safety net ensures no operation_queue_master row is created even if a
    caller bypasses or regresses the classifier.
    """
    channel_meta = channel_meta or {}
    decision = decision or {}

    source_message_log_id = channel_meta.get("channel_message_log_id")
    if not source_message_log_id:
        return 0

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        root_id, _ = _resolve_channel_message_group(
            cursor,
            source_message_log_id,
        )
        root_id = root_id or source_message_log_id

        message = _same_passport_duplicate_message(decision)
        existing_queue_id = (
            decision.get("existing_queue_id")
            or channel_meta.get("linked_queue_id")
        )
        thread_status = (
            "EXISTING_THREAD"
            if existing_queue_id
            else "NO_THREAD_FOUND"
        )

        cursor.execute(
            """
            UPDATE channel_message_log
            SET
                parsed_flag = 1,
                parse_status = 'PARSED',
                classifier_status = 'CLASSIFIED',
                classifier_reason = %s,
                thread_resolution_status = %s,
                requires_manual_review = 0,
                junk_flag = 0,
                duplicate_flag = 1,
                linked_queue_id = %s,
                channel_thread_id = NULL,
                remarks = %s,
                updated_on = NOW()
            WHERE id = %s
            """,
            (
                message,
                thread_status,
                existing_queue_id,
                message,
                root_id,
            ),
        )
        updated = int(cursor.rowcount or 0)

        cursor.execute(
            """
            UPDATE channel_message_log
            SET
                parsed_flag = 1,
                parse_status = 'LINKED',
                classifier_status = 'CLASSIFIED',
                classifier_reason = %s,
                thread_resolution_status = %s,
                requires_manual_review = 0,
                junk_flag = 0,
                duplicate_flag = 1,
                linked_queue_id = %s,
                channel_thread_id = NULL,
                remarks = %s,
                updated_on = NOW()
            WHERE linked_triage_id = %s
              AND id <> %s
            """,
            (
                "Grouped with duplicate-rejected WhatsApp submission window.",
                thread_status,
                existing_queue_id,
                "Grouped with duplicate-rejected WhatsApp submission window.",
                root_id,
                root_id,
            ),
        )
        updated += int(cursor.rowcount or 0)

        conn.commit()
        return updated
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _has_active_patient_workflow_duplicate(conn, passport_no):
    """
    Backward-compatible wrapper retained for any legacy internal caller.

    It now delegates the business decision to wrkflwrule_master instead of
    hard-coding workflow statuses.
    """
    decision = _get_same_passport_reingestion_decision(passport_no)
    blocked = bool(
        decision.get("has_existing_workflow")
        and not decision.get("allow_new_case")
    )
    return (
        blocked,
        _same_passport_duplicate_message(decision) if blocked else "",
    )


def _get_latest_active_patient_workflow_row(conn, passport_no):
    """
    Backward-compatible helper retained for any future/legacy caller.

    The latest workflow is considered active when Workflow Rule Master does
    NOT allow a new case for the same passport.  Missing rule fails closed.
    No workflow status names are hard-coded.
    """
    passport_no = _safe_str(passport_no).upper()
    if not passport_no:
        return {}

    cursor = conn.cursor(dictionary=True)
    try:
        try:
            cursor.execute(
                """
                SELECT
                    pw.*,
                    wr.status_name AS _same_passport_rule_status_name,
                    wr.allow_new_case_same_passport AS _allow_new_case_same_passport
                FROM patient_wrkflw pw
                LEFT JOIN wrkflwrule_master wr
                    ON UPPER(TRIM(IFNULL(wr.status_name, '')))
                     = UPPER(TRIM(IFNULL(pw.wrkflw_status, '')))
                WHERE UPPER(TRIM(IFNULL(pw.patpassport_no, ''))) = %s
                ORDER BY pw.entered_date DESC,
                         COALESCE(pw.ld_seqno, 0) DESC,
                         pw.ld_internalno DESC
                LIMIT 1
                """,
                (passport_no,),
            )
            latest = cursor.fetchone() or {}
        except Exception as exc:
            if "allow_new_case_same_passport" in str(exc):
                raise RuntimeError(
                    "Workflow Rule Master migration is missing: "
                    "wrkflwrule_master.allow_new_case_same_passport"
                ) from exc
            raise

        if not latest:
            return {}

        rule_found = bool(
            _safe_str(latest.get("_same_passport_rule_status_name"))
        )
        try:
            allow_new_case = (
                rule_found
                and int(latest.get("_allow_new_case_same_passport") or 0) == 1
            )
        except (TypeError, ValueError):
            allow_new_case = False

        if allow_new_case:
            return {}

        latest.pop("_same_passport_rule_status_name", None)
        latest.pop("_allow_new_case_same_passport", None)
        return latest
    finally:
        cursor.close()

def _get_workflow_rule_row(cursor, status_name):
    status_name = _safe_str(status_name)
    if not status_name:
        return {}

    try:
        cursor.execute(
            """
            SELECT
                status_name,
                allow_edit,
                vil_date_required,
                appointment_date_required,
                other_checks
            FROM wrkflwrule_master
            WHERE UPPER(TRIM(IFNULL(status_name, ''))) = UPPER(TRIM(%s))
            LIMIT 1
            """,
            (status_name,),
        )
        return cursor.fetchone() or {}
    except Exception:
        return {}


def _validate_workflow_rule_requirements(cursor, queue_row, wrkflw_status):
    queue_row = queue_row or {}
    wrkflw_status = _safe_str(wrkflw_status)

    rule_row = _get_workflow_rule_row(cursor, wrkflw_status)
    if not rule_row:
        return {"success": True, "rule_row": {}}

    allow_edit = int(rule_row.get("allow_edit") or 0)
    vil_date_required = int(rule_row.get("vil_date_required") or 0)
    appointment_date_required = int(rule_row.get("appointment_date_required") or 0)

    if allow_edit != 1:
        return {"success": False, "message": f"Workflow status '{wrkflw_status}' is not editable as per wrkflwrule_master.", "rule_row": rule_row}

    if vil_date_required == 1 and not _normalize_db_date(queue_row.get("vil_date")):
        return {"success": False, "message": f"VIL Date is required for workflow status '{wrkflw_status}'.", "rule_row": rule_row}

    if appointment_date_required == 1 and not _normalize_db_date(queue_row.get("appointment_dt")):
        return {"success": False, "message": f"Appointment Date is required for workflow status '{wrkflw_status}'.", "rule_row": rule_row}

    return {"success": True, "rule_row": rule_row}


def _update_existing_patient_workflow_row(cursor, existing_row, queue_row, hospital_city, user_code, wrkflw_status):
    queue_row = queue_row or {}
    existing_row = existing_row or {}

    patient_dob = _normalize_db_date(queue_row.get("patient_dob"))
    patient_age = _calc_age_for_workflow(queue_row.get("patient_dob"))
    patpass_expirydt = _normalize_db_date(queue_row.get("patpass_expirydt"))

    attn1 = _build_attender_payload(queue_row, "attn1")
    attn2 = _build_attender_payload(queue_row, "attn2")
    attn3 = _build_attender_payload(queue_row, "attn3")

    vil_internalno = _safe_str(existing_row.get("vil_internalno")) or None
    if _safe_str(wrkflw_status).upper() == "VIL REQUESTED" and not vil_internalno:
        generated_numbers = _generate_workflow_internal_numbers(cursor.connection, existing_row, wrkflw_status)
        vil_internalno = generated_numbers.get("vil_internalno")

    cursor.execute(
        """
        UPDATE patient_wrkflw
        SET patgiven_nm = %s,
            patsur_nm = %s,
            patient_dob = %s,
            patient_country = %s,
            patient_gender = %s,
            patient_contno = %s,
            patient_age = %s,
            patpass_expirydt = %s,
            patient_address = %s,
            patient_email = %s,
            attn1passport_no = %s,
            attn1pat_relation = %s,
            attn1_givennm = %s,
            attn1_surnm = %s,
            attn1_dob = %s,
            attn1_country = %s,
            attn1_gender = %s,
            attn1_contno = %s,
            attn1_age = %s,
            attn1pass_expirydt = %s,
            attn1_address = %s,
            attn1_email = %s,
            attn2passport_no = %s,
            attn2pat_relation = %s,
            attn2_givennm = %s,
            attn2_surnm = %s,
            attn2_dob = %s,
            attn2_country = %s,
            attn2_gender = %s,
            attn2_contno = %s,
            attn2_age = %s,
            attn2pass_expirydt = %s,
            attn2_address = %s,
            attn2_email = %s,
            attn3passport_no = %s,
            attn3pat_relation = %s,
            attn3_givennm = %s,
            attn3_surnm = %s,
            attn3_dob = %s,
            attn3_country = %s,
            attn3_gender = %s,
            attn3_contno = %s,
            attn3_age = %s,
            attn3pass_expirydt = %s,
            attn3_address = %s,
            attn3_email = %s,
            hospital_shrtnm = %s,
            hospital_city = %s,
            agent_shrtnm = %s,
            hospital_dept = %s,
            ivac_center = %s,
            doctor_nm = %s,
            vil_date = %s,
            appointment_dt = %s,
            wrkflw_status = %s,
            vil_internalno = %s,
            entered_by = %s,
            entered_date = NOW()
        WHERE patpassport_no = %s
          AND ld_internalno = %s
        """,
        (
            queue_row.get("patgiven_nm"),
            queue_row.get("patsur_nm"),
            patient_dob,
            queue_row.get("patient_country"),
            normalize_patient_gender(queue_row.get("patient_gender"), default_if_blank=None),
            queue_row.get("patient_contno"),
            patient_age,
            patpass_expirydt,
            queue_row.get("patient_address"),
            queue_row.get("patient_email"),
            attn1["passport_no"], attn1["relation"], attn1["givennm"], attn1["surnm"], attn1["dob"], attn1["country"], attn1["gender"], attn1["contno"], attn1["age"], attn1["pass_expirydt"], attn1["address"], attn1["email"],
            attn2["passport_no"], attn2["relation"], attn2["givennm"], attn2["surnm"], attn2["dob"], attn2["country"], attn2["gender"], attn2["contno"], attn2["age"], attn2["pass_expirydt"], attn2["address"], attn2["email"],
            attn3["passport_no"], attn3["relation"], attn3["givennm"], attn3["surnm"], attn3["dob"], attn3["country"], attn3["gender"], attn3["contno"], attn3["age"], attn3["pass_expirydt"], attn3["address"], attn3["email"],
            queue_row.get("hospital_shrtnm"), hospital_city, queue_row.get("agent_shrtnm"), queue_row.get("hospital_dept"), queue_row.get("ivac_center"), queue_row.get("doctor_nm"), _normalize_db_date(queue_row.get("vil_date")), _normalize_db_date(queue_row.get("appointment_dt")), wrkflw_status, vil_internalno, user_code,
            existing_row.get("patpassport_no"), existing_row.get("ld_internalno"),
        ),
    )

    return {"updated": cursor.rowcount > 0, "ld_internalno": _safe_str(existing_row.get("ld_internalno")), "vil_internalno": vil_internalno}


def _normalize_whitespace(value):
    return " ".join(_safe_str(value).split())


def _normalize_key(value):
    value = _safe_str(value).lower()
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]", "", value)


def _build_browser_file_url(file_path):
    raw_path = (file_path or "").strip()
    if not raw_path:
        return ""

    normalized = raw_path.replace("\\", "/")

    if normalized.startswith("http://") or normalized.startswith("https://"):
        return normalized

    lower_path = normalized.lower()

    private_marker = "private/whatsapp_docs/"
    private_index = lower_path.rfind(private_marker)
    if private_index >= 0:
        relative = normalized[private_index + len(private_marker):].lstrip("/")
        browser_path = "/whatsapp/media/" + relative
        return quote(browser_path, safe="/:._-() ")

    static_marker = "/static/"
    static_index = lower_path.rfind(static_marker)
    if static_index >= 0:
        browser_path = normalized[static_index:]
        return quote(browser_path, safe="/:._-() ")

    if lower_path.startswith("static/"):
        browser_path = "/" + normalized.lstrip("/")
        return quote(browser_path, safe="/:._-() ")

    app_static_marker = "app/static/"
    app_static_index = lower_path.rfind(app_static_marker)
    if app_static_index >= 0:
        browser_path = "/" + normalized[app_static_index + len("app/"):]
        return quote(browser_path, safe="/:._-() ")

    uploads_marker = "/uploads/"
    uploads_index = lower_path.rfind(uploads_marker)
    if uploads_index >= 0:
        browser_path = "/static" + normalized[uploads_index:]
        return quote(browser_path, safe="/:._-() ")

    if lower_path.startswith("uploads/"):
        browser_path = "/static/" + normalized
        return quote(browser_path, safe="/:._-() ")

    return ""


def _build_followup_channel_message_id(existing_message_id, channel_thread_id, cursor):
    base_id = _safe_str(existing_message_id) or "OPQ"
    channel_thread_id = _safe_str(channel_thread_id)

    if "_FUP" in base_id:
        base_id = base_id.split("_FUP")[0]

    current_count = 0

    if cursor and channel_thread_id and base_id:
        cursor.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM operation_queue_master
            WHERE channel_thread_id = %s
              AND channel_message_id LIKE %s
            """,
            (channel_thread_id, f"{base_id}_FUP%"),
        )
        result = cursor.fetchone() or {}

        if isinstance(result, dict):
            current_count = int(result.get("cnt", 0) or 0)
        elif isinstance(result, (list, tuple)) and len(result) > 0:
            current_count = int(result[0] or 0)

    next_seq = current_count + 1
    seq_str = str(next_seq).zfill(3)

    return f"{base_id}_FUP{seq_str}"


def _normalize_attachment_item(item):
    item = item or {}

    file_name = _safe_str(item.get("file_name"))
    file_ext = _safe_str(item.get("file_ext"))
    mime_type = _safe_str(item.get("mime_type"))
    file_path = _safe_str(item.get("file_path"))
    remarks = _safe_str(item.get("remarks"))

    file_size = item.get("file_size")
    try:
        file_size = int(file_size) if file_size not in (None, "", "None") else 0
    except Exception:
        file_size = 0

    return {
        "file_name": file_name,
        "file_ext": file_ext,
        "mime_type": mime_type,
        "file_size": file_size,
        "file_path": file_path,
        "remarks": remarks,
    }


def _normalize_attachment_signature(item):
    item = _normalize_attachment_item(item)

    return (
        item.get("file_name"),
        item.get("file_ext"),
        item.get("mime_type"),
        int(item.get("file_size") or 0),
        item.get("file_path"),
    )


def _extract_incoming_attachments_from_channel_meta(channel_meta):
    channel_meta = channel_meta or {}
    raw_attachment_json = channel_meta.get("attachment_json")
    attachments = []

    try:
        if isinstance(raw_attachment_json, list):
            attachments = raw_attachment_json
        elif isinstance(raw_attachment_json, str) and raw_attachment_json.strip():
            attachments = json.loads(raw_attachment_json)
        else:
            attachments = []
    except Exception:
        attachments = []

    normalized = []
    for item in attachments or []:
        normalized.append(_normalize_attachment_item(item))

    return normalized


def _fetch_latest_row_attachments(cursor, latest_row):
    latest_row = latest_row or {}
    operation_queue_id = latest_row.get("id")

    if not operation_queue_id:
        return []

    cursor.execute(
        """
        SELECT
            file_name,
            file_ext,
            mime_type,
            file_size,
            file_path,
            remarks
        FROM operation_queue_attachment
        WHERE operation_queue_id = %s
        ORDER BY id ASC
        """,
        (operation_queue_id,),
    )

    rows = cursor.fetchall() or []
    normalized = []

    for row in rows:
        normalized.append(_normalize_attachment_item(row))

    return normalized


def _compare_attachments_with_latest_row(incoming_attachments, latest_attachments):
    incoming_attachments = incoming_attachments or []
    latest_attachments = latest_attachments or []

    incoming_set = {
        _normalize_attachment_signature(item)
        for item in incoming_attachments
    }
    latest_set = {
        _normalize_attachment_signature(item)
        for item in latest_attachments
    }

    added = sorted(list(incoming_set - latest_set))
    removed = sorted(list(latest_set - incoming_set))

    has_changes = bool(added or removed)

    _debug_compare_log("---- ATTACHMENT LEVEL COMPARE START ----")
    _debug_compare_log("incoming attachment count:", len(incoming_attachments))
    _debug_compare_log("latest attachment count:", len(latest_attachments))
    _debug_compare_log("incoming attachment signatures:", list(incoming_set))
    _debug_compare_log("latest attachment signatures:", list(latest_set))
    _debug_compare_log("attachment added:", added)
    _debug_compare_log("attachment removed:", removed)
    _debug_compare_log("attachment has_changes:", has_changes)
    _debug_compare_log("---- ATTACHMENT LEVEL COMPARE END ----")

    return {
        "has_changes": has_changes,
        "added": added,
        "removed": removed,
    }



def _attachment_already_exists(
    cursor,
    operation_queue_id,
    attachment_item,
    channel_message_log_id=None,
    channel_message_id=None,
):
    """
    Prevent duplicate attachment rows before inserting metadata copied from
    channel_message_log into operation_queue_attachment.

    Existing flow is preserved:
    - Queue creation/parsing/comparison logic is not changed.
    - Existing operation_queue_id-level duplicate protection is retained.
    - Additional protection is added for the DB unique key:
      channel_message_log_id + channel_message_id + file_path.

    This prevents MySQL 1062 duplicate key errors for
    uq_opq_attachment_msglog_msg_file_path when the same WhatsApp attachment
    is processed again by webhook retry / scheduler retry / follow-up flow.
    """
    item = _normalize_attachment_item(attachment_item)
    file_path = item.get("file_path")

    try:
        # First check the actual unique-key combination present in the DB.
        # This is the key that was causing:
        # 1062 Duplicate entry '<log_id>-<message_id>-<file_path>'
        if channel_message_log_id and channel_message_id:
            cursor.execute(
                """
                SELECT id
                FROM operation_queue_attachment
                WHERE channel_message_log_id = %s
                  AND channel_message_id = %s
                  AND IFNULL(file_path, '') = IFNULL(%s, '')
                LIMIT 1
                """,
                (
                    channel_message_log_id,
                    channel_message_id,
                    file_path,
                ),
            )
            if cursor.fetchone() is not None:
                return True

        # Keep the old duplicate protection also, so existing behavior remains.
        operation_queue_id = _safe_str(operation_queue_id)
        if operation_queue_id:
            cursor.execute(
                """
                SELECT id
                FROM operation_queue_attachment
                WHERE operation_queue_id = %s
                  AND IFNULL(file_name, '') = IFNULL(%s, '')
                  AND IFNULL(file_ext, '') = IFNULL(%s, '')
                  AND IFNULL(mime_type, '') = IFNULL(%s, '')
                  AND IFNULL(file_size, 0) = IFNULL(%s, 0)
                  AND IFNULL(file_path, '') = IFNULL(%s, '')
                LIMIT 1
                """,
                (
                    operation_queue_id,
                    item.get("file_name"),
                    item.get("file_ext"),
                    item.get("mime_type"),
                    int(item.get("file_size") or 0),
                    file_path,
                ),
            )
            if cursor.fetchone() is not None:
                return True

        return False

    except Exception:
        return False


def _insert_operation_queue_attachments(
    cursor,
    operation_queue_id,
    channel_message_log_id=None,
    channel_message_id=None,
    channel_thread_id=None,
    attachment_json=None,
):
    """
    Insert WhatsApp attachment metadata from channel_message_log.attachment_json
    into operation_queue_attachment after an operation_queue_master row is created.

    This fixes the case where channel_message_log contains attachment_json, but
    operation_queue_attachment has no row, so the document is not visible in the
    Operation Queue attachment pane.

    Safe behavior:
    - No change when attachment_json is blank.
    - No duplicate insert for the same operation_queue_id + file signature.
    - Uses table column detection, so existing environments with slightly different
      operation_queue_attachment columns will not break.
    - Inserts metadata even when file_path is blank due to Meta token download error,
      preserving the received document record and error remarks.
    """
    operation_queue_id = _safe_str(operation_queue_id)
    if not operation_queue_id:
        return 0

    raw_attachment_json = attachment_json
    if raw_attachment_json in (None, "", "None"):
        return 0

    attachments = []
    try:
        if isinstance(raw_attachment_json, list):
            attachments = raw_attachment_json
        elif isinstance(raw_attachment_json, str) and raw_attachment_json.strip():
            attachments = json.loads(raw_attachment_json)
        else:
            attachments = []
    except Exception:
        attachments = []

    if not attachments:
        return 0

    table_columns = _get_table_column_names_safe(cursor, "operation_queue_attachment")
    if not table_columns:
        return 0

    inserted_count = 0

    for raw_item in attachments:
        item = _normalize_attachment_item(raw_item)

        if not item.get("file_name") and not item.get("file_path"):
            continue

        if _attachment_already_exists(
            cursor=cursor,
            operation_queue_id=operation_queue_id,
            attachment_item=item,
            channel_message_log_id=channel_message_log_id,
            channel_message_id=channel_message_id,
        ):
            continue

        payload = {
            "operation_queue_id": operation_queue_id,
            "channel_message_log_id": channel_message_log_id,
            "channel_message_id": channel_message_id,
            "channel_thread_id": channel_thread_id,
            "file_name": item.get("file_name"),
            "file_ext": item.get("file_ext"),
            "mime_type": item.get("mime_type"),
            "file_size": int(item.get("file_size") or 0),
            "file_path": item.get("file_path"),
            "remarks": item.get("remarks"),
        }

        insert_columns = [
            col for col in payload.keys()
            if col in table_columns and col not in ("id", "created_on", "updated_on")
        ]

        if not insert_columns:
            continue

        # IMPORTANT:
        # Use ON DUPLICATE KEY UPDATE for the DB unique key:
        # uq_opq_attachment_msglog_msg_file_path
        #
        # Why this is required:
        # In live WhatsApp flow, the same attachment can be attempted again by
        # webhook retry / scheduler retry / second parser pass. A SELECT-before-
        # INSERT check is not enough under retry/concurrent processing.
        # This statement makes the insert idempotent and prevents parse_status
        # from becoming FAILED only because attachment metadata already exists.
        duplicate_update_columns = [
            col for col in insert_columns
            if col not in ("id", "channel_message_log_id", "channel_message_id", "file_path", "created_on")
        ]

        if duplicate_update_columns:
            duplicate_update_sql = ", ".join([
                f"{col} = VALUES({col})" for col in duplicate_update_columns
            ])
        else:
            duplicate_update_sql = "id = id"

        sql = f"""
            INSERT INTO operation_queue_attachment ({", ".join(insert_columns)})
            VALUES ({", ".join(["%s"] * len(insert_columns))})
            ON DUPLICATE KEY UPDATE {duplicate_update_sql}
        """

        try:
            cursor.execute(sql, [payload.get(col) for col in insert_columns])
            inserted_count += 1

        except mysql.connector.Error as e:
            error_text = str(e)

            # Final safety net. Even if MySQL raises duplicate error due to
            # another unique/index combination, never fail parsing only because
            # the same attachment already exists.
            if getattr(e, "errno", None) == 1062:
                print(
                    "DUPLICATE OPERATION QUEUE ATTACHMENT SKIPPED | "
                    f"operation_queue_id={operation_queue_id} | "
                    f"channel_message_log_id={channel_message_log_id} | "
                    f"channel_message_id={channel_message_id} | "
                    f"file_path={_safe_str(item.get('file_path'))} | "
                    f"error={error_text}",
                    flush=True,
                )
                continue

            raise

    return inserted_count



FOLLOWUP_FIELD_LABEL_MAP = {
    "patient given name": "patgiven_nm",
    "patient short name": "patsur_nm",
    "patient passport expiry date": "patpass_expirydt",
    "patient gender": "patient_gender",
    "patient dob": "patient_dob",
    "patient age": "patient_age",
    "patient contact number": "patient_contno",
    "patient address": "patient_address",
    "patient email": "patient_email",
    "patient nationality": "patient_country",
    "attender1 given name": "attn1_givennm",
    "attender1 short name": "attn1_surnm",
    "attender1 passport number": "attn1passport_no",
    "attender1 passport expiry date": "attn1pass_expirydt",
    "attender1 gender": "attn1_gender",
    "attender1 dob": "attn1_dob",
    "attender1 age": "attn1_age",
    "attender1 address": "attn1_address",
    "attender1 nationality": "attn1_country",
    "attender1 contact number": "attn1_contno",
    "attender1 relationship with patient": "attn1pat_relation",
    "attender1 relationship": "attn1pat_relation",
    "attender2 given name": "attn2_givennm",
    "attender2 short name": "attn2_surnm",
    "attender2 passport number": "attn2passport_no",
    "attender2 passport expiry date": "attn2pass_expirydt",
    "attender2 gender": "attn2_gender",
    "attender2 dob": "attn2_dob",
    "attender2 age": "attn2_age",
    "attender2 address": "attn2_address",
    "attender2 nationality": "attn2_country",
    "attender2 contact number": "attn2_contno",
    "attender2 relationship with patient": "attn2pat_relation",
    "attender2 relationship": "attn2pat_relation",
    "attender3 given name": "attn3_givennm",
    "attender3 short name": "attn3_surnm",
    "attender3 passport number": "attn3passport_no",
    "attender3 passport expiry date": "attn3pass_expirydt",
    "attender3 gender": "attn3_gender",
    "attender3 dob": "attn3_dob",
    "attender3 age": "attn3_age",
    "attender3 address": "attn3_address",
    "attender3 nationality": "attn3_country",
    "attender3 contact number": "attn3_contno",
    "attender3 relationship with patient": "attn3pat_relation",
    "attender3 relationship": "attn3pat_relation",
    "hospital name": "hospital_shrtnm",
    "department": "hospital_dept",
    "doctor's name": "doctor_nm",
    "doctor name": "doctor_nm",
    "nationality": "patient_country",
    "embassy": "ivac_center",
    "intent": "intent_remarks",
}


FOLLOWUP_DATE_COLUMNS = {
    "patient_dob",
    "patpass_expirydt",
    "attn1_dob",
    "attn1pass_expirydt",
    "attn2_dob",
    "attn2pass_expirydt",
    "attn3_dob",
    "attn3pass_expirydt",
    "vil_date",
    "appointment_dt",
}


PASSPORT_UPPERCASE_COLUMNS = {
    "patpassport_no",
    "attn1passport_no",
    "attn2passport_no",
    "attn3passport_no",
}


def _normalize_passport_column_value(column_name, value):
    """
    Keep passport number values in uppercase before saving to DB.

    This only standardizes alphabet casing for passport columns and does not
    change parser, follow-up, comparison, attachment or workflow logic.
    """
    if column_name in PASSPORT_UPPERCASE_COLUMNS and _safe_str(value):
        return _safe_str(value).upper()
    return value


def _normalize_followup_date_value(value):
    """
    Normalize date values received in FOLLOWUP_TEXT amendments before inserting
    the copied revision row into operation_queue_master.

    Existing flow is preserved: if a value cannot be parsed safely, the original
    value is returned instead of rejecting the message or breaking the insert.

    Supported examples:
    20/11/2031    -> 2031-11-20
    20-11-2031    -> 2031-11-20
    20.11.2031    -> 2031-11-20
    20TH NOV 2031 -> 2031-11-20
    2031-11-20    -> 2031-11-20
    """
    original_value = _safe_str(value)
    if not original_value:
        return None

    cleaned = original_value.upper().strip()
    cleaned = re.sub(r"(\d+)(ST|ND|RD|TH)\b", r"\1", cleaned)
    cleaned = cleaned.replace(",", " ")
    cleaned = re.sub(r"\s+", " ", cleaned).strip()

    formats = [
        "%Y-%m-%d",
        "%d/%m/%Y",
        "%d-%m-%Y",
        "%d.%m.%Y",
        "%d %m %Y",
        "%d %b %Y",
        "%d %B %Y",
        "%d/%b/%Y",
        "%d/%B/%Y",
        "%d-%b-%Y",
        "%d-%B-%Y",
    ]

    for fmt in formats:
        try:
            return datetime.strptime(cleaned, fmt).strftime("%Y-%m-%d")
        except Exception:
            pass

    return original_value


def _normalize_followup_label(label):
    label = _safe_str(label).lower()
    label = label.replace("_", " ").replace("-", " ")
    label = re.sub(r"[^a-z0-9' ]+", " ", label)
    label = re.sub(r"\s+", " ", label).strip()
    return label


def _extract_passport_no_from_followup_text(text):
    if not text:
        return ""

    text_upper = str(text).upper()
    patterns = [
        r"PATIENT\s+PASSPORT\s*(NO|NUMBER|#)?\s*[:\-]?\s*([A-Z]{1,3}[A-Z0-9]{5,14})\b",
        r"PASSPORT\s*(NO|NUMBER|#)?\s*[:\-]?\s*([A-Z]{1,3}[A-Z0-9]{5,14})\b",
        r"PASS\s*(NO|NUMBER|#)?\s*[:\-]?\s*([A-Z]{1,3}[A-Z0-9]{5,14})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text_upper)
        if match:
            return _safe_str(match.group(2)).upper()

    return ""


def _parse_followup_update_fields(raw_message_text):
    """
    Parse FOLLOWUP_TEXT amendment fields.

    IMPORTANT:
    This keeps the earlier working FOLLOWUP_TEXT insert flow unchanged.
    Only parsing of field labels is made safer.

    Supported formats:
    1. Label: Value
    2. Label - Value
    3. Label<TAB>Value
    4. Label    Value
    5. Label Value, when Label is a known field label

    Date values such as 2030-12-31 are protected from being split at '-'.
    """
    updates = {}
    approximate_date = ""

    passport_labels = {
        "patient passport number",
        "patient passport no",
        "passport number",
        "passport no",
        "passport",
    }

    special_labels = {"approximate date"}
    known_labels = set(FOLLOWUP_FIELD_LABEL_MAP.keys()) | passport_labels | special_labels
    known_labels_sorted = sorted(known_labels, key=len, reverse=True)

    def assign_value(label, value):
        nonlocal approximate_date

        label = _normalize_followup_label(label)
        value = _safe_str(value)

        if not label or value == "":
            return

        if label in passport_labels:
            return

        if label == "approximate date":
            approximate_date = value
            return

        target_column = FOLLOWUP_FIELD_LABEL_MAP.get(label)
        if target_column:
            updates[target_column] = value

    def parse_line(line):
        line = _safe_str(line)
        if not line:
            return "", ""

        # 1. Colon separator is always safe.
        match = re.match(r"^(.+?)\s*:\s*(.+)$", line)
        if match:
            return match.group(1), match.group(2)

        # 2. Tab copied from Excel/WhatsApp table.
        if "\t" in line:
            parts = [part.strip() for part in line.split("\t") if part.strip()]
            if len(parts) >= 2:
                return parts[0], " ".join(parts[1:])

        # 3. Multiple spaces between field and value.
        match = re.match(r"^(.+?)\s{2,}(.+)$", line)
        if match:
            possible_label = _normalize_followup_label(match.group(1))
            if possible_label in known_labels:
                return match.group(1), match.group(2)

        # 4. Dash separator only when dash is surrounded by spaces.
        # This avoids breaking date values such as 2030-12-31.
        match = re.match(r"^(.+?)\s+-\s+(.+)$", line)
        if match:
            return match.group(1), match.group(2)

        # 5. Known label followed by one or more spaces and then value.
        normalized_line = _normalize_followup_label(line)
        for possible_label in known_labels_sorted:
            if normalized_line.startswith(possible_label + " "):
                raw_value = line[len(possible_label):].strip()
                if raw_value:
                    return possible_label, raw_value

        return "", ""

    lines = [_safe_str(line) for line in str(raw_message_text or "").splitlines() if _safe_str(line)]

    idx = 0
    while idx < len(lines):
        label, value = parse_line(lines[idx])

        # Support format:
        # Patient Passport Expiry Date
        # 2030-12-31
        if label and value == "" and idx + 1 < len(lines):
            next_label, _ = parse_line(lines[idx + 1])
            if not next_label:
                value = lines[idx + 1]
                idx += 1

        if label and value != "":
            assign_value(label, value)

        idx += 1

    # IMPORTANT:
    # Do NOT normalize gender values for FOLLOWUP_TEXT amendments.
    # If WhatsApp says Patient Gender: M, the new operation_queue_master
    # revision row must store exactly M, not Male.
    # If WhatsApp says Patient Gender: F, it must store exactly F, not Female.
    # Existing workflow/patient conversion functions may still normalize gender
    # later where required; amendment history must preserve the received value.

    return updates, approximate_date

def _fetch_latest_open_operation_queue_row_by_passport(cursor, passport_no):
    passport_no = _safe_str(passport_no)
    if not passport_no:
        return {}

    cursor.execute(
        """
        SELECT *
        FROM operation_queue_master
        WHERE UPPER(TRIM(IFNULL(patpassport_no, ''))) = UPPER(TRIM(%s))
          AND UPPER(TRIM(IFNULL(wrkflw_status, ''))) NOT IN ('CLOSED', 'CANCEL', 'NO PAYMENT', 'NOT INTERESTED WITHOUT PAY')
        ORDER BY id DESC
        LIMIT 1
        """,
        (passport_no,),
    )
    return cursor.fetchone() or {}


def _fetch_latest_open_operation_queue_row_by_thread_id(cursor, channel_thread_id):
    """
    Fetch latest open operation queue row using channel_thread_id.

    This is required for FOLLOWUP_TEXT because follow-up WhatsApp messages
    may carry only the amended field and may not repeat the passport number.
    """
    channel_thread_id = _safe_str(channel_thread_id)
    if not channel_thread_id:
        return {}

    cursor.execute(
        """
        SELECT *
        FROM operation_queue_master
        WHERE UPPER(TRIM(IFNULL(channel_thread_id, ''))) = UPPER(TRIM(%s))
          AND UPPER(TRIM(IFNULL(wrkflw_status, ''))) NOT IN ('CLOSED', 'CANCEL', 'NO PAYMENT', 'NOT INTERESTED WITHOUT PAY')
        ORDER BY id DESC
        LIMIT 1
        """,
        (channel_thread_id,),
    )
    return cursor.fetchone() or {}


def _fetch_latest_open_operation_queue_row_by_id(cursor, operation_queue_id):
    """
    Fetch latest open operation queue row using operation_queue_master.id.

    This is required for FOLLOWUP_TEXT because channel_message_log already
    stores linked_queue_id after classification. linked_queue_id is the most
    reliable source row reference for follow-up parsing.
    """
    operation_queue_id = _safe_str(operation_queue_id)
    if not operation_queue_id:
        return {}

    cursor.execute(
        """
        SELECT *
        FROM operation_queue_master
        WHERE id = %s
          AND UPPER(TRIM(IFNULL(wrkflw_status, ''))) NOT IN ('CLOSED', 'CANCEL', 'NO PAYMENT', 'NOT INTERESTED WITHOUT PAY')
        LIMIT 1
        """,
        (operation_queue_id,),
    )
    return cursor.fetchone() or {}


def _get_table_column_names_safe(cursor, table_name):
    try:
        cursor.execute(f"SHOW COLUMNS FROM {table_name}")
        rows = cursor.fetchall() or []
        columns = set()

        for row in rows:
            field_name = row.get("Field") if isinstance(row, dict) else row[0]
            if field_name:
                columns.add(str(field_name))

        return columns
    except Exception:
        return set()


def _get_generic_table_columns(cursor, table_name):
    """
    Return column names for any table using the existing cursor.

    This helper is intentionally generic because Operation Queue save/clone
    logic needs to build dynamic INSERT statements without breaking existing
    environments where table columns may differ.
    """
    try:
        cursor.execute(f"SHOW COLUMNS FROM {table_name}")
        rows = cursor.fetchall() or []
        columns = []

        for row in rows:
            if isinstance(row, dict):
                col_name = row.get("Field")
            else:
                col_name = row[0]

            if col_name:
                columns.append(str(col_name).strip())

        return columns
    except Exception:
        return []


def _resolve_channel_message_group(cursor, channel_message_log_id):
    """
    Return the stable pre-parse group for one channel message.

    The root keeps linked_triage_id NULL.  Every attachment/message received
    from the same WhatsApp sender inside the existing grouping window points to
    that root through linked_triage_id.  channel_thread_id is deliberately not
    involved in this lookup because Manual Review groups do not have one.
    """
    message_log_id = _safe_str(channel_message_log_id)
    if not message_log_id:
        return None, []

    try:
        cursor.execute(
            """
            SELECT id, linked_triage_id
            FROM channel_message_log
            WHERE id = %s
            LIMIT 1
            """,
            (message_log_id,),
        )
        current_row = cursor.fetchone() or {}
        root_id = _safe_str(current_row.get("linked_triage_id")) or _safe_str(
            current_row.get("id")
        )
        if not root_id:
            root_id = message_log_id

        cursor.execute(
            """
            SELECT
                id,
                channel_message_id,
                channel_thread_id,
                linked_triage_id,
                linked_queue_id,
                attachment_count,
                attachment_json,
                parse_status,
                message_type,
                received_on
            FROM channel_message_log
            WHERE id = %s
               OR linked_triage_id = %s
            ORDER BY received_on ASC, id ASC
            """,
            (root_id, root_id),
        )
        return root_id, cursor.fetchall() or []
    except Exception:
        return message_log_id, []


def _find_reusable_manual_review_queue_row(
    cursor,
    channel_message_log_id,
    linked_queue_id=None,
):
    """
    Find the temporary Manual Review queue row that must be promoted in place.

    Promoting the same row prevents one MANUAL_REVIEW row and a second PARSED
    row from being left behind for the same source group.
    """
    root_id, _ = _resolve_channel_message_group(cursor, channel_message_log_id)
    if not root_id:
        return {}

    if linked_queue_id:
        cursor.execute(
            """
            SELECT *
            FROM operation_queue_master
            WHERE id = %s
              AND channel_message_log_id = %s
              AND UPPER(TRIM(COALESCE(parse_status, ''))) = 'MANUAL_REVIEW'
            LIMIT 1
            """,
            (linked_queue_id, root_id),
        )
        row = cursor.fetchone() or {}
        if row:
            return row

    cursor.execute(
        """
        SELECT *
        FROM operation_queue_master
        WHERE channel_message_log_id = %s
          AND UPPER(TRIM(COALESCE(parse_status, ''))) = 'MANUAL_REVIEW'
          AND COALESCE(channel_thread_id, '') = ''
        ORDER BY id DESC
        LIMIT 1
        """,
        (root_id,),
    )
    return cursor.fetchone() or {}


def _insert_operation_queue_group_attachments(
    cursor,
    operation_queue_id,
    channel_message_log_id,
    channel_thread_id=None,
    fallback_channel_message_id=None,
    fallback_attachment_json=None,
):
    """Insert every distinct attachment from one linked_triage group."""
    root_id, group_rows = _resolve_channel_message_group(
        cursor,
        channel_message_log_id,
    )

    if not group_rows:
        return _insert_operation_queue_attachments(
            cursor=cursor,
            operation_queue_id=operation_queue_id,
            channel_message_log_id=root_id or channel_message_log_id,
            channel_message_id=fallback_channel_message_id,
            channel_thread_id=channel_thread_id,
            attachment_json=fallback_attachment_json,
        )

    inserted_count = 0
    for group_row in group_rows:
        inserted_count += _insert_operation_queue_attachments(
            cursor=cursor,
            operation_queue_id=operation_queue_id,
            channel_message_log_id=group_row.get("id"),
            channel_message_id=group_row.get("channel_message_id"),
            channel_thread_id=channel_thread_id,
            attachment_json=group_row.get("attachment_json"),
        )

    return inserted_count


def _update_channel_message_log_after_queue_insert(
    cursor,
    channel_message_log_id,
    channel_message_id=None,
    channel_thread_id=None,
    operation_queue_id=None,
    parse_status="PARSED",
    remarks=None,
    thread_resolution_status=None,
    classifier_reason=None,
):
    """
    Update channel_message_log after operation_queue_master insert.

    Normal parsed records retain the existing success behaviour.
    MANUAL_REVIEW records remain unresolved: no channel_thread_id is stored,
    parsed_flag stays 0, and the detailed review reason is written to both
    classifier_reason and remarks while linked_queue_id points to the queue row.
    """
    channel_message_log_id = _safe_str(channel_message_log_id)
    if not channel_message_log_id:
        return 0

    group_root_id, _ = _resolve_channel_message_group(
        cursor,
        channel_message_log_id,
    )
    group_root_id = _safe_str(group_root_id) or channel_message_log_id

    columns = _get_table_column_names_safe(cursor, "channel_message_log")
    if not columns:
        return 0

    normalized_status = _safe_str(parse_status).upper() or "PARSED"
    normalized_thread_status = _safe_str(thread_resolution_status).upper()
    manual_review = normalized_status == "MANUAL_REVIEW"
    reason = _safe_str(remarks) or (
        "WhatsApp sender could not be mapped to a valid Agency. "
        "Manual review is required for Agent assignment."
    )

    set_parts = []
    values = []

    if manual_review:
        if "parsed_flag" in columns:
            set_parts.append("parsed_flag = %s")
            values.append(0)
        if "parse_status" in columns:
            set_parts.append("parse_status = %s")
            values.append("MANUAL_REVIEW")
        if "requires_manual_review" in columns:
            set_parts.append("requires_manual_review = %s")
            values.append(1)
        if "junk_flag" in columns:
            set_parts.append("junk_flag = %s")
            values.append(0)
        if "linked_queue_id" in columns and operation_queue_id:
            set_parts.append("linked_queue_id = %s")
            values.append(operation_queue_id)
        if "channel_thread_id" in columns:
            set_parts.append("channel_thread_id = NULL")
        if "classifier_status" in columns:
            set_parts.append("classifier_status = 'CLASSIFIED'")
        if "classifier_reason" in columns:
            set_parts.append("classifier_reason = %s")
            values.append(reason[:500])
        if "thread_resolution_status" in columns:
            set_parts.append("thread_resolution_status = 'NO_THREAD_FOUND'")
        if "remarks" in columns:
            set_parts.append("remarks = %s")
            values.append(reason[:500])
    else:
        if "channel_message_id" in columns and _safe_str(channel_message_id):
            set_parts.append("channel_message_id = %s")
            values.append(_safe_str(channel_message_id))
        if "channel_thread_id" in columns and _safe_str(channel_thread_id):
            set_parts.append("channel_thread_id = %s")
            values.append(_safe_str(channel_thread_id))
        if "linked_queue_id" in columns and operation_queue_id:
            set_parts.append("linked_queue_id = %s")
            values.append(operation_queue_id)
        if "parsed_flag" in columns:
            set_parts.append("parsed_flag = %s")
            values.append(1)
        if "parse_status" in columns:
            set_parts.append("parse_status = %s")
            values.append(normalized_status)
        if "requires_manual_review" in columns:
            set_parts.append("requires_manual_review = %s")
            values.append(0)
        if "junk_flag" in columns:
            set_parts.append("junk_flag = %s")
            values.append(0)
        if "classifier_status" in columns:
            set_parts.append("classifier_status = %s")
            values.append("CLASSIFIED")

        final_classifier_reason = _safe_str(classifier_reason)
        if not final_classifier_reason:
            if normalized_thread_status == "EXISTING_THREAD":
                final_classifier_reason = (
                    "Existing thread/follow-up request identified"
                )
            elif normalized_thread_status == "NEW_THREAD":
                final_classifier_reason = (
                    "New patient/request details detected and sent to parser"
                )
            else:
                final_classifier_reason = (
                    "Message successfully classified and processed by parser"
                )

        if "classifier_reason" in columns:
            set_parts.append("classifier_reason = %s")
            values.append(final_classifier_reason[:500])

        if (
            "thread_resolution_status" in columns
            and normalized_thread_status
        ):
            set_parts.append("thread_resolution_status = %s")
            values.append(normalized_thread_status)

        if "remarks" in columns and remarks:
            set_parts.append("remarks = %s")
            values.append(_safe_str(remarks)[:500])

    if "updated_on" in columns:
        set_parts.append("updated_on = NOW()")

    if not set_parts:
        return 0

    values.append(group_root_id)
    cursor.execute(
        f"""
        UPDATE channel_message_log
        SET {", ".join(set_parts)}
        WHERE id = %s
        """,
        tuple(values),
    )
    updated_count = int(cursor.rowcount or 0)

    # Child message/attachment rows retain their own channel_message_id.  They
    # only inherit group-level queue/thread/status fields from the root.
    child_set_parts = []
    child_values = []

    if manual_review:
        if "parsed_flag" in columns:
            child_set_parts.append("parsed_flag = 1")
        if "parse_status" in columns:
            child_set_parts.append("parse_status = 'LINKED'")
        if "requires_manual_review" in columns:
            child_set_parts.append("requires_manual_review = 0")
        if "linked_queue_id" in columns and operation_queue_id:
            child_set_parts.append("linked_queue_id = %s")
            child_values.append(operation_queue_id)
        if "channel_thread_id" in columns:
            child_set_parts.append("channel_thread_id = NULL")
        if "classifier_status" in columns:
            child_set_parts.append("classifier_status = 'CLASSIFIED'")
        if "thread_resolution_status" in columns:
            child_set_parts.append("thread_resolution_status = 'MANUAL_LINKED'")
        if "remarks" in columns:
            child_set_parts.append("remarks = %s")
            child_values.append(
                "Grouped with Manual Review root through linked_triage_id."
            )
    else:
        if "parsed_flag" in columns:
            child_set_parts.append("parsed_flag = 1")
        if "parse_status" in columns:
            child_set_parts.append("parse_status = 'LINKED'")
        if "requires_manual_review" in columns:
            child_set_parts.append("requires_manual_review = 0")
        if "linked_queue_id" in columns and operation_queue_id:
            child_set_parts.append("linked_queue_id = %s")
            child_values.append(operation_queue_id)
        if "channel_thread_id" in columns and _safe_str(channel_thread_id):
            child_set_parts.append("channel_thread_id = %s")
            child_values.append(_safe_str(channel_thread_id))
        if "classifier_status" in columns:
            child_set_parts.append("classifier_status = 'CLASSIFIED'")
        if "thread_resolution_status" in columns:
            child_set_parts.append("thread_resolution_status = 'EXISTING_THREAD'")
        if "remarks" in columns:
            child_set_parts.append("remarks = %s")
            child_values.append(
                "Grouped message/attachment linked to Operation Queue."
            )

    if "updated_on" in columns:
        child_set_parts.append("updated_on = NOW()")

    if child_set_parts:
        child_values.append(group_root_id)
        cursor.execute(
            f"""
            UPDATE channel_message_log
            SET {", ".join(child_set_parts)}
            WHERE linked_triage_id = %s
            """,
            tuple(child_values),
        )
        updated_count += int(cursor.rowcount or 0)

    return updated_count


def _operation_queue_master_columns_for_insert(cursor):
    cursor.execute("SHOW COLUMNS FROM operation_queue_master")
    rows = cursor.fetchall() or []
    columns = []
    for row in rows:
        field_name = row.get("Field") if isinstance(row, dict) else row[0]
        if field_name and field_name not in ("id", "created_on", "updated_on"):
            columns.append(field_name)
    return columns


def _is_meaningful_payload_value(value):
    """
    Return True only when the incoming parser value should overwrite the
    previous queue-row value. Blank parser values must not wipe old data.
    """
    if value is None:
        return False

    if isinstance(value, str):
        text = value.strip()
        if text == "":
            return False
        if text.upper() in ("NULL", "NONE"):
            return False

    return True


def _merge_payload_with_latest_operation_queue_row(latest_row, payload, channel_meta=None):
    """
    Build a complete revision row for the same channel_thread_id.

    Why this is required:
    The parser may return only amended fields in the next WhatsApp message
    e.g. patpassport_no + patient_email. If we insert that partial payload
    directly, the new operation_queue_master row loses all previous patient,
    attender, hospital, assignment and workflow values.

    Correct behavior:
    Copy the latest row of the same thread, then overwrite only non-blank
    values received in the latest payload. Technical WhatsApp/message columns
    are always refreshed from the new payload/channel_meta.
    """
    latest_row = latest_row or {}
    payload = payload or {}
    channel_meta = channel_meta or {}

    if not latest_row:
        return dict(payload)

    merged = dict(latest_row)

    for skip_col in ("id", "created_on", "updated_on"):
        merged.pop(skip_col, None)

    # Overlay only meaningful parser values so blanks from amended messages
    # do not erase existing values from the previous full row.
    #
    # IMPORTANT:
    # rule_engine/parser may put priority_status = "NORMAL" as a default even
    # when the follow-up WhatsApp message did not explicitly amend priority.
    # In that case, do NOT downgrade an existing HIGH/MEDIUM priority in the
    # copied revision row. This does not affect row creation / comparison; it
    # only protects the final value inserted in the new copied row.
    for col, val in payload.items():
        if not _is_meaningful_payload_value(val):
            continue

        if col == "priority_status":
            incoming_priority = _safe_str(val).upper()
            existing_priority = _safe_str(latest_row.get("priority_status") or merged.get("priority_status")).upper()

            if incoming_priority == "NORMAL" and existing_priority in ("HIGH", "MEDIUM"):
                continue

        if col == "hospital_dept":
            merged[col] = _normalize_department_csv(val)
        else:
            merged[col] = _normalize_passport_column_value(col, val)

    # Always retain the existing thread id when the incoming payload belongs
    # to this thread but does not explicitly carry the id.
    merged["channel_thread_id"] = (
        payload.get("channel_thread_id")
        or latest_row.get("channel_thread_id")
        or merged.get("channel_thread_id")
    )

    # Always refresh message-level identifiers for the newly received message.
    if "channel_message_log_id" in payload or channel_meta.get("channel_message_log_id") is not None:
        merged["channel_message_log_id"] = payload.get("channel_message_log_id") or channel_meta.get("channel_message_log_id")

    if "channel_message_id" in payload or channel_meta.get("channel_message_id"):
        merged["channel_message_id"] = payload.get("channel_message_id") or channel_meta.get("channel_message_id")

    if "channel_code" in payload or channel_meta.get("channel_code"):
        merged["channel_code"] = payload.get("channel_code") or channel_meta.get("channel_code") or merged.get("channel_code") or "WHATSAPP"

    # Keep the parser result of the new message. If parser did not set these,
    # keep the latest row values.
    if payload.get("parse_status"):
        merged["parse_status"] = payload.get("parse_status")
    if payload.get("parse_remarks"):
        merged["parse_remarks"] = payload.get("parse_remarks")

    return merged


def _insert_followup_revision_row(cursor, source_row, updates, approximate_date, channel_meta):
    source_row = source_row or {}
    updates = updates or {}
    channel_meta = channel_meta or {}

    payload = dict(source_row)
    payload.pop("id", None)
    payload.pop("created_on", None)
    payload.pop("updated_on", None)

    for col, val in updates.items():
        if col in payload and _safe_str(val) != "":
            if col in FOLLOWUP_DATE_COLUMNS:
                payload[col] = _normalize_followup_date_value(val)
            elif col == "hospital_dept":
                payload[col] = _normalize_department_csv(val)
            else:
                payload[col] = _normalize_passport_column_value(col, val)

    intent_value = _safe_str(payload.get("intent_remarks")).upper()
    if approximate_date:
        normalized_approximate_date = _normalize_followup_date_value(approximate_date)

        if "VIL" in intent_value:
            payload["vil_date"] = normalized_approximate_date
            payload["appointment_dt"] = normalized_approximate_date
        elif "APPOINT" in intent_value:
            payload["appointment_dt"] = normalized_approximate_date
        else:
            payload["appointment_dt"] = normalized_approximate_date

    payload["channel_message_log_id"] = channel_meta.get("channel_message_log_id")
    payload["channel_code"] = channel_meta.get("channel_code") or payload.get("channel_code") or "WHATSAPP"
    payload["channel_message_id"] = channel_meta.get("channel_message_id") or _build_followup_channel_message_id(
        existing_message_id=source_row.get("channel_message_id"),
        channel_thread_id=source_row.get("channel_thread_id"),
        cursor=cursor,
    )
    payload["channel_thread_id"] = source_row.get("channel_thread_id")
    payload["patpassport_no"] = _normalize_passport_column_value("patpassport_no", source_row.get("patpassport_no"))
    payload["parse_status"] = "PARSED"
    payload["parse_remarks"] = _get_operation_remarks(
        parse_status="LINKED",
        thread_resolution_status="EXISTING_THREAD",
        channel_meta=channel_meta,
    )

    for passport_col in PASSPORT_UPPERCASE_COLUMNS:
        if passport_col in payload:
            payload[passport_col] = _normalize_passport_column_value(passport_col, payload.get(passport_col))

    columns = _operation_queue_master_columns_for_insert(cursor)
    insert_columns = [col for col in columns if col in payload]

    sql = f"""
        INSERT INTO operation_queue_master ({", ".join(insert_columns)})
        VALUES ({", ".join(["%s"] * len(insert_columns))})
    """
    cursor.execute(sql, [payload.get(col) for col in insert_columns])
    return cursor.lastrowid, payload


def _process_followup_modification_message(raw_message_text, channel_meta):
    conn = None
    cursor = None

    try:
        channel_meta = channel_meta or {}
        passport_no = _extract_passport_no_from_followup_text(raw_message_text)
        incoming_thread_id = _safe_str(channel_meta.get("channel_thread_id"))
        linked_queue_id = channel_meta.get("linked_queue_id")

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        source_row = {}

        # Priority 1:
        # Use linked_queue_id from channel_message_log when classifier has
        # already linked this FOLLOWUP_TEXT to an existing operation queue row.
        # This is the most reliable reference and avoids missing inserts when
        # the follow-up text does not repeat the full passport/request details.
        if linked_queue_id:
            source_row = _fetch_latest_open_operation_queue_row_by_id(
                cursor,
                linked_queue_id,
            )

        # Priority 2:
        # If classifier/thread resolver already identified the thread, use that.
        # FOLLOWUP_TEXT may contain only amended fields and may not repeat passport.
        if not source_row and incoming_thread_id:
            source_row = _fetch_latest_open_operation_queue_row_by_thread_id(
                cursor,
                incoming_thread_id,
            )

        # Priority 3:
        # Fallback to passport from the follow-up text, preserving the existing flow.
        if not source_row and passport_no:
            source_row = _fetch_latest_open_operation_queue_row_by_passport(
                cursor,
                passport_no,
            )

        if not source_row:
            return {
                "success": False,
                "reason": "followup_junk",
                "message": "Follow-up message rejected as JUNK because no matching open operation queue record was found by linked_queue_id, channel_thread_id or Patient Passport Number.",
                "channel_thread_id": incoming_thread_id,
            }

        updates, approximate_date = _parse_followup_update_fields(raw_message_text)

        changed = False
        for col, val in updates.items():
            if _safe_str(source_row.get(col)) != _safe_str(val):
                changed = True
                break

        if approximate_date:
            intent_value = _safe_str(updates.get("intent_remarks") or source_row.get("intent_remarks")).upper()
            if "VIL" in intent_value:
                changed = changed or _safe_str(source_row.get("vil_date")) != _safe_str(approximate_date)
                changed = changed or _safe_str(source_row.get("appointment_dt")) != _safe_str(approximate_date)
            else:
                changed = changed or _safe_str(source_row.get("appointment_dt")) != _safe_str(approximate_date)

        incoming_attachments = _extract_incoming_attachments_from_channel_meta(channel_meta)
        latest_attachments = _fetch_latest_row_attachments(cursor, source_row)
        attachment_compare_result = _compare_attachments_with_latest_row(incoming_attachments, latest_attachments)

        # IMPORTANT:
        # For FOLLOWUP_TEXT, a successfully parsed follow-up message must create
        # a new operation_queue_master revision row even when no mapped field
        # value changed. This preserves the WhatsApp follow-up history in the
        # queue and keeps channel_message_log.linked_queue_id pointing to the
        # new revision row. Existing data is copied from the latest source row,
        # and only recognised non-blank amended fields are overwritten.
        inserted_id, payload = _insert_followup_revision_row(
            cursor=cursor,
            source_row=source_row,
            updates=updates,
            approximate_date=approximate_date,
            channel_meta=channel_meta,
        )

        _insert_operation_queue_attachments(
            cursor=cursor,
            operation_queue_id=inserted_id,
            channel_message_log_id=channel_meta.get("channel_message_log_id"),
            # The source WhatsApp message id remains available even when a
            # record requires Manual Review; only channel_thread_id stays NULL.
            channel_message_id=payload.get("channel_message_id"),
            channel_thread_id=(
                None
                if _safe_str(payload.get("parse_status")).upper() == "MANUAL_REVIEW"
                else payload.get("channel_thread_id")
            ),
            attachment_json=channel_meta.get("attachment_json"),
        )

        if not updates and not approximate_date and not attachment_compare_result.get("has_changes"):
            followup_remarks = "Follow-up text row created in operation_queue_master. No recognised amendment field was found, so previous row values were copied."
        elif not changed and not attachment_compare_result.get("has_changes"):
            followup_remarks = "Follow-up text row created in operation_queue_master. No changed value detected, so previous row values were copied."
        else:
            followup_remarks = "Follow-up modification row created in operation_queue_master."

        _update_channel_message_log_after_queue_insert(
            cursor=cursor,
            channel_message_log_id=channel_meta.get("channel_message_log_id"),
            channel_message_id=payload.get("channel_message_id"),
            channel_thread_id=payload.get("channel_thread_id"),
            operation_queue_id=inserted_id,
            parse_status="PARSED",
            remarks="FOLLOW-UP",
            thread_resolution_status="EXISTING_THREAD",
            classifier_reason="Existing thread/follow-up request identified",
        )

        conn.commit()

        return {
            "success": True,
            "operation_queue_master_id": inserted_id,
            "channel_thread_id": payload.get("channel_thread_id"),
            "assigned_to": payload.get("assigned_to"),
            "wrkflw_status": payload.get("wrkflw_status"),
            "followup_revision": True,
            "field_changed": changed,
            "attachment_changes": attachment_compare_result,
        }

    except Exception as e:
        if conn:
            conn.rollback()
        return {"success": False, "error": str(e)}

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

# ============================================================
# POOL-BASED ROUND ROBIN ASSIGNMENT
# ============================================================

def _normalize_assignment_lookup_value(value):
    value = _safe_str(value).upper()
    return value or None


def _get_agency_registered_country(cursor, agent_shrtnm=None):
    """
    Return the registered country of the Agency/Partner from partner_reg.

    Round Robin COUNTRY matching must use the Agency's registered country.
    It must never use operation_queue_master.patient_country because that
    field represents the patient's country/nationality.

    The partner country column is detected safely so this amendment remains
    compatible with existing database environments that use slightly different
    country-column names. If the Agency or a supported country column cannot be
    found, None is returned and the resolver falls back to the DEFAULT rule.
    """
    agency = _normalize_assignment_lookup_value(agent_shrtnm)
    if not agency:
        return None

    partner_columns = _get_table_column_names_safe(cursor, "partner_reg")
    if not partner_columns:
        return None

    country_column = next(
        (
            column_name
            for column_name in (
                "partner_country",
                "country",
                "agency_country",
                "registered_country",
                "country_name",
                "partner_country_name",
            )
            if column_name in partner_columns
        ),
        None,
    )

    if not country_column:
        return None

    cursor.execute(
        f"""
        SELECT {country_column} AS agency_country
        FROM partner_reg
        WHERE UPPER(TRIM(COALESCE(partner_type, ''))) = 'AGENCY'
          AND UPPER(TRIM(COALESCE(short_name, ''))) = %s
        ORDER BY short_name ASC
        LIMIT 1
        """,
        (agency,),
    )
    agency_row = cursor.fetchone() or {}
    return _normalize_assignment_lookup_value(
        agency_row.get("agency_country")
    )


def _resolve_round_robin_rule_and_member(cursor, agent_shrtnm=None, patient_country=None):
    """
    Resolve and lock the next configured Round Robin member.

    Priority is strictly:
    1. Exact AGENCY rule using agent_shrtnm.
    2. Exact COUNTRY rule using the Agency's registered country from partner_reg.
    3. DEFAULT rule.

    patient_country is retained only for backward-compatible calls and is
    intentionally not used for Round Robin assignment-rule resolution.

    No state is advanced here. The caller advances state only after the
    operation_queue_master insert succeeds, within the same transaction.
    """
    agency = _normalize_assignment_lookup_value(agent_shrtnm)
    agency_country = _get_agency_registered_country(
        cursor=cursor,
        agent_shrtnm=agency,
    )

    candidates = []
    if agency:
        candidates.append(("AGENCY", agency))
    if agency_country:
        candidates.append(("COUNTRY", agency_country))
    candidates.append(("DEFAULT", None))

    rule_row = None
    for rule_type, rule_value in candidates:
        if rule_type == "DEFAULT":
            cursor.execute(
                """
                SELECT
                    r.id AS rule_id,
                    r.rule_name,
                    r.rule_type,
                    r.rule_value,
                    r.pool_id,
                    r.priority_order,
                    p.pool_name
                FROM assignment_rule_mapping r
                INNER JOIN assignment_pool_master p
                    ON p.id = r.pool_id
                   AND p.active_flag = 1
                WHERE r.active_flag = 1
                  AND UPPER(TRIM(r.rule_type)) = 'DEFAULT'
                ORDER BY r.priority_order ASC, r.id ASC
                LIMIT 1
                """
            )
        else:
            cursor.execute(
                """
                SELECT
                    r.id AS rule_id,
                    r.rule_name,
                    r.rule_type,
                    r.rule_value,
                    r.pool_id,
                    r.priority_order,
                    p.pool_name
                FROM assignment_rule_mapping r
                INNER JOIN assignment_pool_master p
                    ON p.id = r.pool_id
                   AND p.active_flag = 1
                WHERE r.active_flag = 1
                  AND UPPER(TRIM(r.rule_type)) = %s
                  AND UPPER(TRIM(COALESCE(r.rule_value, ''))) = %s
                ORDER BY r.priority_order ASC, r.id ASC
                LIMIT 1
                """,
                (rule_type, rule_value),
            )

        candidate_rule = cursor.fetchone() or {}
        if candidate_rule:
            rule_row = candidate_rule
            break

    if not rule_row:
        return {}

    rule_id = rule_row.get("rule_id")
    pool_id = rule_row.get("pool_id")

    cursor.execute(
        """
        INSERT INTO assignment_round_robin_state
            (rule_id, pool_id, assignment_count)
        VALUES (%s, %s, 0)
        ON DUPLICATE KEY UPDATE
            pool_id = VALUES(pool_id),
            last_member_id = CASE
                WHEN pool_id <> VALUES(pool_id) THEN NULL
                ELSE last_member_id
            END,
            last_user_code = CASE
                WHEN pool_id <> VALUES(pool_id) THEN NULL
                ELSE last_user_code
            END,
            last_assigned_on = CASE
                WHEN pool_id <> VALUES(pool_id) THEN NULL
                ELSE last_assigned_on
            END,
            updated_on = NOW()
        """,
        (rule_id, pool_id),
    )

    cursor.execute(
        """
        SELECT
            id,
            rule_id,
            pool_id,
            last_member_id,
            last_user_code,
            last_assigned_on,
            assignment_count
        FROM assignment_round_robin_state
        WHERE rule_id = %s
        FOR UPDATE
        """,
        (rule_id,),
    )
    state_row = cursor.fetchone() or {}

    cursor.execute(
        """
        SELECT
            m.id AS member_id,
            m.user_code,
            m.priority_order,
            u.user_name
        FROM assignment_pool_member m
        INNER JOIN user_master u
            ON u.user_code = m.user_code
        WHERE m.pool_id = %s
          AND m.active_flag = 1
          AND UPPER(TRIM(COALESCE(u.user_status, ''))) = 'ACTIVE'
        ORDER BY m.priority_order ASC, m.id ASC
        """,
        (pool_id,),
    )
    members = cursor.fetchall() or []
    if not members:
        return {}

    last_member_id = state_row.get("last_member_id")
    next_member = None

    if last_member_id:
        last_index = next(
            (index for index, item in enumerate(members) if item.get("member_id") == last_member_id),
            None,
        )
        if last_index is not None:
            next_member = members[(last_index + 1) % len(members)]

    if not next_member:
        next_member = members[0]

    return {
        **rule_row,
        "state_id": state_row.get("id"),
        "previous_user_code": state_row.get("last_user_code"),
        "member_id": next_member.get("member_id"),
        "user_code": next_member.get("user_code"),
        "user_name": next_member.get("user_name"),
    }


def _finalize_round_robin_assignment(cursor, assignment, operation_queue_id):
    """Advance state and write assignment history in the caller's transaction."""
    assignment = assignment or {}
    if not assignment.get("state_id") or not assignment.get("user_code"):
        return 0

    assigned_on = datetime.now()

    cursor.execute(
        """
        UPDATE assignment_round_robin_state
        SET pool_id = %s,
            last_member_id = %s,
            last_user_code = %s,
            last_assigned_on = %s,
            assignment_count = assignment_count + 1,
            updated_on = NOW()
        WHERE id = %s
        """,
        (
            assignment.get("pool_id"),
            assignment.get("member_id"),
            assignment.get("user_code"),
            assigned_on,
            assignment.get("state_id"),
        ),
    )

    rule_type = _safe_str(assignment.get("rule_type")).upper() or "DEFAULT"
    rule_value = _safe_str(assignment.get("rule_value")) or "DEFAULT"
    remarks = (
        f"Assigned through configured {rule_type} rule "
        f"'{_safe_str(assignment.get('rule_name'))}' and pool "
        f"'{_safe_str(assignment.get('pool_name'))}'."
    )

    cursor.execute(
        """
        INSERT INTO assignment_round_robin_log
        (
            rule_type,
            rule_value,
            assigned_user_code,
            previous_user_code,
            reference_type,
            reference_id,
            assignment_source,
            assigned_on,
            assigned_by,
            remarks
        )
        VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
        """,
        (
            rule_type,
            rule_value,
            assignment.get("user_code"),
            assignment.get("previous_user_code"),
            "OPERATION_QUEUE",
            str(operation_queue_id),
            "ROUND_ROBIN",
            assigned_on,
            "SYSTEM",
            remarks[:500],
        ),
    )
    return 1


def insert_into_operation_queue_master(raw_message_text, channel_meta):
    conn = None
    cursor = None
    insert_cursor = None
    round_robin_assignment = {}

    try:
        channel_meta = channel_meta or {}
        if _safe_str(channel_meta.get("message_type")).upper() == "FOLLOWUP_TEXT":
            return _process_followup_modification_message(
                raw_message_text=raw_message_text,
                channel_meta=channel_meta,
            )

        payload = build_operation_queue_insert_payload(
            raw_message_text=raw_message_text,
            channel_meta=channel_meta,
        )

        if "hospital_dept" in payload:
            payload["hospital_dept"] = _normalize_department_csv(
                payload.get("hospital_dept")
            ) or None

        for passport_col in PASSPORT_UPPERCASE_COLUMNS:
            if passport_col in payload:
                payload[passport_col] = _normalize_passport_column_value(passport_col, payload.get(passport_col))

        _debug_compare_log("============================================================")
        _debug_compare_log("insert_into_operation_queue_master called")
        _debug_compare_log("incoming raw_message_text:", _debug_value_repr(raw_message_text))
        _debug_compare_log("incoming channel_meta:", channel_meta)
        _debug_compare_log("initial payload keys:", sorted(list((payload or {}).keys())))
        _debug_compare_log("initial payload patpassport_no:", _debug_value_repr((payload or {}).get("patpassport_no")))
        _debug_compare_log("initial payload channel_thread_id:", _debug_value_repr((payload or {}).get("channel_thread_id")))

        parse_status = _safe_str(payload.get("parse_status")).upper()

        # MANUAL_REVIEW can still be a successfully parsed business message.
        # Example: Patient Passport Number is available, but agent_shrtnm could
        # not be mapped from the WhatsApp sender. In that case, continue with
        # normal thread resolution and create operation_queue_master with
        # parse_status = MANUAL_REVIEW. channel_message_log is updated to PARSED
        # only after the queue row is successfully created.

        if not should_create_operation_queue_row(payload):
            _debug_compare_log("skip reason=parse_status_failed")
            return {
                "success": False,
                "skipped": True,
                "manual_review": False,
                "queue_created": False,
                "reason": "parse_status_failed",
                "message": f"Parse status is {parse_status or 'FAILED'}. No row created in operation_queue_master.",
                "payload": payload,
                "channel_thread_id": payload.get("channel_thread_id"),
                "assigned_to": payload.get("assigned_to"),
            }

        passport_no = payload.get("patpassport_no")

        same_passport_decision = {}
        if parse_status != "MANUAL_REVIEW" and _safe_str(passport_no):
            same_passport_decision = _get_same_passport_reingestion_decision(
                passport_no
            )

            if (
                same_passport_decision.get("has_existing_workflow")
                and not same_passport_decision.get("allow_new_case")
            ):
                _mark_same_passport_duplicate_rejected(
                    channel_meta=channel_meta,
                    decision=same_passport_decision,
                )
                duplicate_message = _same_passport_duplicate_message(
                    same_passport_decision
                )
                return {
                    "success": True,
                    "skipped": True,
                    "duplicate": True,
                    "queue_created": False,
                    "reason": "SAME_PASSPORT_ACTIVE_WORKFLOW",
                    "message": duplicate_message,
                    "payload": payload,
                    "linked_queue_id": same_passport_decision.get(
                        "existing_queue_id"
                    ),
                    "channel_thread_id": None,
                    "assigned_to": None,
                }

        if parse_status == "MANUAL_REVIEW":
            # Revised requirement: Manual Review remains unresolved until a user
            # completes the review. Do not generate/carry a thread.  Preserve
            # the inbound channel_message_id because it is the immutable source
            # identifier for the root WhatsApp message.
            thread_context = {
                "channel_thread_id": None,
                "assigned_to": None,
                "wrkflw_status": None,
                "new_thread": True,
                "manual_review": True,
            }
            payload["channel_thread_id"] = None
            payload["channel_message_id"] = (
                payload.get("channel_message_id")
                or channel_meta.get("channel_message_id")
            )
            payload["assigned_to"] = None
            payload["assigned_by"] = None
            payload["assigned_at"] = None
        else:
            if (
                same_passport_decision.get("has_existing_workflow")
                and same_passport_decision.get("allow_new_case")
            ):
                # Workflow Rule Master explicitly permits a new case for the
                # same passport.  Do not carry the prior queue thread, owner, or
                # queue workflow status into this new business case.
                thread_context = {
                    "channel_thread_id": _generate_new_channel_thread_id(
                        passport_no
                    ),
                    "assigned_to": None,
                    "wrkflw_status": None,
                    "new_thread": True,
                    "rule_permitted_same_passport_reingestion": True,
                    "previous_queue_id": same_passport_decision.get(
                        "existing_queue_id"
                    ),
                    "previous_workflow_status": same_passport_decision.get(
                        "latest_workflow_status"
                    ),
                }
            else:
                thread_context = resolve_thread_context(passport_no)

            if thread_context.get("new_thread"):
                # IMPORTANT:
                # Previous same-passport record is CLOSED or no previous row exists.
                # Force a fresh thread id and do not carry forward assignment/status.
                payload["channel_thread_id"] = thread_context.get("channel_thread_id")
                payload["assigned_to"] = payload.get("assigned_to")
                payload["assigned_by"] = payload.get("assigned_by")
                payload["assigned_at"] = payload.get("assigned_at")
            else:
                if not payload.get("channel_thread_id"):
                    payload["channel_thread_id"] = thread_context.get("channel_thread_id")

                if not payload.get("assigned_to") and thread_context.get("assigned_to"):
                    payload["assigned_to"] = thread_context.get("assigned_to")
                    payload["assigned_by"] = "SYSTEM_CONTINUITY"
                    payload["assigned_at"] = payload.get("assigned_at")

                if thread_context.get("wrkflw_status"):
                    payload["wrkflw_status"] = thread_context.get("wrkflw_status")

        _debug_compare_log("resolved thread_context:", thread_context)

        _debug_compare_log("payload after thread enrichment channel_thread_id:", _debug_value_repr(payload.get("channel_thread_id")))
        _debug_compare_log("payload after thread enrichment assigned_to:", _debug_value_repr(payload.get("assigned_to")))
        _debug_compare_log("payload after thread enrichment wrkflw_status:", _debug_value_repr(payload.get("wrkflw_status")))

        if parse_status == "MANUAL_REVIEW":
            manual_review_reason = _safe_str(
                payload.get("manual_review_reason")
                or payload.get("classifier_reason")
                or payload.get("parse_remarks")
            )
            if not manual_review_reason or manual_review_reason.upper() in {"MANUAL REVIEW", "MANUAL_REVIEW"}:
                manual_review_reason = (
                    "WhatsApp sender could not be mapped to a valid Agency. "
                    "Manual review is required for Agent assignment."
                )
            payload["manual_review_reason"] = manual_review_reason
            payload["parse_remarks"] = manual_review_reason
        else:
            payload["parse_remarks"] = _get_operation_remarks(
                parse_status=payload.get("parse_status"),
                thread_resolution_status=("EXISTING_THREAD" if not thread_context.get("new_thread") else "NEW_THREAD_AFTER_CLOSED_OR_NEW_CASE"),
                channel_meta=channel_meta,
            )

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        group_root_id, _ = _resolve_channel_message_group(
            cursor,
            channel_meta.get("channel_message_log_id"),
        )
        group_root_id = (
            group_root_id
            or channel_meta.get("channel_message_log_id")
        )

        # Re-parsing must be idempotent for an unresolved Manual Review record.
        # Reuse the existing temporary queue row whether the corrected message
        # remains MANUAL_REVIEW or is now ready to be promoted to Operation Queue.
        # This preserves the original channel_message_id and prevents a second
        # INSERT from violating uq_operation_queue_master_channel_message_id.
        reusable_manual_review_row = _find_reusable_manual_review_queue_row(
            cursor=cursor,
            channel_message_log_id=group_root_id,
            linked_queue_id=channel_meta.get("linked_queue_id"),
        )

        # operation_queue_master always represents the root message of the
        # linked_triage group, never an individual attachment child.
        if group_root_id:
            payload["channel_message_log_id"] = group_root_id

        latest_row = {}
        if parse_status != "MANUAL_REVIEW":
            latest_row = _fetch_latest_operation_queue_row_by_thread_id(
                cursor,
                payload.get("channel_thread_id"),
            )

        compare_config_rows = _get_compare_config_rows(cursor)

        _debug_compare_log(
            "latest_row found:",
            bool(latest_row),
            "| latest_row id:",
            _debug_value_repr((latest_row or {}).get("id")),
            "| latest_row channel_message_id:",
            _debug_value_repr((latest_row or {}).get("channel_message_id")),
            "| latest_row channel_thread_id:",
            _debug_value_repr((latest_row or {}).get("channel_thread_id")),
        )
        _debug_compare_log("compare_config_rows count:", len(compare_config_rows or []))

        if latest_row:
            field_compare_result = {
                "has_changes": True,
                "changes": [],
            }

            if compare_config_rows:
                field_compare_result = _compare_payload_with_latest_row(
                    payload=payload,
                    latest_row=latest_row,
                    compare_config_rows=compare_config_rows,
                )

            incoming_attachments = _extract_incoming_attachments_from_channel_meta(channel_meta)
            latest_attachments = _fetch_latest_row_attachments(cursor, latest_row)

            attachment_compare_result = _compare_attachments_with_latest_row(
                incoming_attachments=incoming_attachments,
                latest_attachments=latest_attachments,
            )

            _debug_compare_log("field_compare_result has_changes:", field_compare_result.get("has_changes"))
            _debug_compare_log("field_compare_result changes:", field_compare_result.get("changes"))
            _debug_compare_log("attachment_compare_result has_changes:", attachment_compare_result.get("has_changes"))
            _debug_compare_log("attachment_compare_result:", attachment_compare_result)

            if (
                (not field_compare_result.get("has_changes"))
                and (not attachment_compare_result.get("has_changes"))
                and not reusable_manual_review_row
            ):
                _debug_compare_log("NO_FIELD_CHANGE and NO_ATTACHMENT_CHANGE -> insert skipped")

                # Parsing was successful, but no new operation_queue_master row is needed.
                # Do not insert attachment rows in this case. Only link the channel log
                # back to the latest queue row so parse_status does not remain FAILED/PENDING.
                _update_channel_message_log_after_queue_insert(
                    cursor=cursor,
                    channel_message_log_id=channel_meta.get("channel_message_log_id"),
                    channel_message_id=(
                        payload.get("channel_message_id")
                        or latest_row.get("channel_message_id")
                        or channel_meta.get("channel_message_id")
                    ),
                    channel_thread_id=payload.get("channel_thread_id") or latest_row.get("channel_thread_id"),
                    operation_queue_id=latest_row.get("id"),
                    parse_status="PARSED",
                    remarks="FOLLOW-UP",
                    thread_resolution_status="EXISTING_THREAD",
                    classifier_reason="Existing thread/follow-up request identified",
                )
                conn.commit()

                return {
                    "success": True,
                    "skipped": True,
                    "reason": "NO_FIELD_CHANGE",
                    "message": "No configured field change detected and no attachment change detected. Insert skipped.",
                    "payload": payload,
                    "channel_thread_id": payload.get("channel_thread_id"),
                    "assigned_to": payload.get("assigned_to"),
                    "wrkflw_status": payload.get("wrkflw_status"),
                    "linked_queue_id": latest_row.get("id"),
                    "changes": [],
                    "attachment_changes": attachment_compare_result,
                }

        insert_cursor = conn.cursor()

        if latest_row:
            payload = _merge_payload_with_latest_operation_queue_row(
                latest_row=latest_row,
                payload=payload,
                channel_meta=channel_meta,
            )
            _debug_compare_log("payload merged with latest row for full revision insert")
            payload["parse_remarks"] = _get_operation_remarks(
                parse_status=payload.get("parse_status"),
                thread_resolution_status="EXISTING_THREAD",
                channel_meta=channel_meta,
            )

        for passport_col in PASSPORT_UPPERCASE_COLUMNS:
            if passport_col in payload:
                payload[passport_col] = _normalize_passport_column_value(passport_col, payload.get(passport_col))

        # Assign only genuinely new, successfully parsed requests that do not
        # already have an owner from passport/thread continuity. Existing-thread
        # revisions and Manual Review records retain their current behavior.
        if (
            parse_status in ("PARSED", "PARTIAL")
            and thread_context.get("new_thread")
            and not latest_row
            and not _safe_str(payload.get("assigned_to"))
        ):
            round_robin_assignment = _resolve_round_robin_rule_and_member(
                cursor=cursor,
                agent_shrtnm=payload.get("agent_shrtnm"),
                patient_country=payload.get("patient_country"),
            )

            if round_robin_assignment.get("user_code"):
                payload["assigned_to"] = round_robin_assignment.get("user_code")
                payload["assigned_by"] = "SYSTEM"
                payload["assigned_at"] = datetime.now()

        table_columns = _operation_queue_master_columns_for_insert(cursor)
        columns = [col for col in table_columns if col in payload]
        placeholders = ", ".join(["%s"] * len(columns))
        column_sql = ", ".join(columns)

        values = [payload.get(col) for col in columns]

        if reusable_manual_review_row:
            inserted_id = reusable_manual_review_row.get("id")
            update_sql = f"""
                UPDATE operation_queue_master
                SET {", ".join([f"{col} = %s" for col in columns])},
                    updated_on = NOW()
                WHERE id = %s
            """
            insert_cursor.execute(update_sql, values + [inserted_id])
            _debug_compare_log(
                "MANUAL_REVIEW row promoted in place id:",
                inserted_id,
            )
        else:
            sql = f"""
                INSERT INTO operation_queue_master ({column_sql})
                VALUES ({placeholders})
            """

            _debug_compare_log("INSERT will happen")
            _debug_compare_log("insert column count:", len(columns))
            _debug_compare_log("insert channel_thread_id:", _debug_value_repr(payload.get("channel_thread_id")))
            _debug_compare_log("insert patpassport_no:", _debug_value_repr(payload.get("patpassport_no")))
            _debug_compare_log("insert channel_message_id:", _debug_value_repr(payload.get("channel_message_id")))

            insert_cursor.execute(sql, values)
            inserted_id = insert_cursor.lastrowid

        if round_robin_assignment.get("user_code"):
            _finalize_round_robin_assignment(
                cursor=cursor,
                assignment=round_robin_assignment,
                operation_queue_id=inserted_id,
            )

        # Promote any attachments already stored under the Manual Review row,
        # then collect every distinct attachment from the complete source group.
        if payload.get("channel_thread_id"):
            cursor.execute(
                """
                UPDATE operation_queue_attachment
                SET channel_thread_id = %s,
                    updated_on = NOW()
                WHERE operation_queue_id = %s
                """,
                (payload.get("channel_thread_id"), inserted_id),
            )

        _insert_operation_queue_group_attachments(
            cursor=cursor,
            operation_queue_id=inserted_id,
            channel_message_log_id=group_root_id,
            channel_thread_id=payload.get("channel_thread_id"),
            fallback_channel_message_id=payload.get("channel_message_id"),
            fallback_attachment_json=channel_meta.get("attachment_json"),
        )

        if parse_status == "MANUAL_REVIEW":
            final_thread_resolution_status = None
            final_classifier_reason = None
            final_channel_remarks = (
                payload.get("manual_review_reason")
                or payload.get("parse_remarks")
                or (
                    "WhatsApp sender could not be mapped to a valid Agency. "
                    "Manual review is required for Agent assignment."
                )
            )
        elif thread_context.get("new_thread"):
            final_thread_resolution_status = "NEW_THREAD"
            final_classifier_reason = (
                "New patient/request details detected and sent to parser"
            )
            final_channel_remarks = (
                "Operation queue row created from WhatsApp message."
            )
        else:
            final_thread_resolution_status = "EXISTING_THREAD"
            final_classifier_reason = (
                "Existing thread/follow-up request identified"
            )
            final_channel_remarks = "FOLLOW-UP"

        _update_channel_message_log_after_queue_insert(
            cursor=cursor,
            channel_message_log_id=group_root_id,
            channel_message_id=(
                payload.get("channel_message_id")
                or channel_meta.get("channel_message_id")
            ),
            channel_thread_id=payload.get("channel_thread_id"),
            operation_queue_id=inserted_id,
            parse_status=(
                "MANUAL_REVIEW"
                if parse_status == "MANUAL_REVIEW"
                else "PARSED"
            ),
            remarks=final_channel_remarks,
            thread_resolution_status=final_thread_resolution_status,
            classifier_reason=final_classifier_reason,
        )

        conn.commit()

        _debug_compare_log("INSERT success lastrowid:", inserted_id)

        return {
            "success": True,
            "skipped": False,
            "queue_created": True,
            "manual_review_promoted": bool(reusable_manual_review_row),
            "manual_review": parse_status == "MANUAL_REVIEW",
            "parse_status": payload.get("parse_status"),
            "operation_queue_master_id": inserted_id,
            "channel_thread_id": payload.get("channel_thread_id"),
            "assigned_to": payload.get("assigned_to"),
            "wrkflw_status": payload.get("wrkflw_status"),
            "payload": payload,
        }

    except Exception as e:
        if conn:
            conn.rollback()

        _debug_compare_log("EXCEPTION in insert_into_operation_queue_master:", str(e))

        return {
            "success": False,
            "error": str(e),
        }

    finally:
        _debug_compare_log("insert_into_operation_queue_master finished")
        _debug_compare_log("============================================================")
        if insert_cursor:
            insert_cursor.close()
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _build_attachments_by_thread(cursor, thread_ids):
    attachments_by_thread = {}

    if not thread_ids:
        return attachments_by_thread

    placeholders = ", ".join(["%s"] * len(thread_ids))
    sql = f"""
        SELECT
            a.id,
            a.operation_queue_id,
            oq.channel_thread_id,
            a.channel_message_log_id,
            a.channel_message_id,
            a.file_name,
            a.file_ext,
            a.mime_type,
            a.file_size,
            a.file_path,
            a.remarks,
            a.created_on,
            a.updated_on
        FROM operation_queue_attachment a
        INNER JOIN operation_queue_master oq
            ON oq.id = a.operation_queue_id
        WHERE oq.channel_thread_id IN ({placeholders})
        ORDER BY a.created_on DESC, a.id DESC
    """

    cursor.execute(sql, thread_ids)
    attachment_rows = cursor.fetchall() or []

    for attachment in attachment_rows:
        thread_id = _safe_str(attachment.get("channel_thread_id"))
        if not thread_id:
            continue

        attachment["file_url"] = _build_browser_file_url(attachment.get("file_path"))

        if thread_id not in attachments_by_thread:
            attachments_by_thread[thread_id] = []
        attachments_by_thread[thread_id].append(attachment)

    return attachments_by_thread


def _get_table_columns(cursor):
    cursor.execute("SHOW COLUMNS FROM operation_queue_master")
    rows = cursor.fetchall() or []
    columns = []

    for row in rows:
        if isinstance(row, dict):
            col_name = row.get("Field")
        else:
            col_name = row[0]

        if col_name:
            columns.append(col_name)

    return columns


def _fetch_operation_queue_row_by_id(cursor, row_id):
    cursor.execute(
        "SELECT * FROM operation_queue_master WHERE id = %s LIMIT 1",
        (row_id,),
    )
    return cursor.fetchone()


def _fetch_latest_operation_queue_row_by_thread_id(cursor, channel_thread_id):
    channel_thread_id = _safe_str(channel_thread_id)
    if not channel_thread_id:
        return {}

    cursor.execute(
        """
        SELECT *
        FROM operation_queue_master
        WHERE channel_thread_id = %s
          AND UPPER(TRIM(IFNULL(wrkflw_status, ''))) <> 'CLOSED'
        ORDER BY id DESC
        LIMIT 1
        """,
        (channel_thread_id,),
    )
    return cursor.fetchone() or {}


def _get_compare_config_rows(cursor):
    cursor.execute(
        """
        SELECT
            field_name,
            data_type,
            compare_flag,
            amendment_flag,
            ignore_blank_flag,
            case_sensitive_flag,
            trim_spaces_flag,
            normalize_date_flag,
            same_thread_compare_flag
        FROM operation_queue_field_compare_master
        WHERE active_flag = 1
          AND compare_flag = 1
          AND amendment_flag = 1
          AND source_table = 'operation_queue_master'
        ORDER BY display_order ASC, id ASC
        """
    )
    return cursor.fetchall() or []


def _normalize_value_for_compare(value, config_row):
    config_row = config_row or {}

    data_type = _safe_str(config_row.get("data_type")).upper()
    ignore_blank_flag = int(config_row.get("ignore_blank_flag") or 0)
    case_sensitive_flag = int(config_row.get("case_sensitive_flag") or 0)
    trim_spaces_flag = int(config_row.get("trim_spaces_flag") or 0)
    normalize_date_flag = int(config_row.get("normalize_date_flag") or 0)

    if value is None:
        return None

    if data_type in ("DATE", "DATETIME") or normalize_date_flag == 1:
        normalized_date = _normalize_db_date(value)
        if normalized_date is not None:
            return str(normalized_date)

    if data_type == "NUMBER":
        try:
            return str(int(str(value).strip()))
        except Exception:
            try:
                return str(float(str(value).strip()))
            except Exception:
                pass

    if data_type == "BOOLEAN":
        str_val = _safe_str(value).upper()
        if str_val in ("1", "TRUE", "Y", "YES"):
            return "1"
        if str_val in ("0", "FALSE", "N", "NO", ""):
            return "0"

    str_val = str(value)

    if trim_spaces_flag == 1:
        str_val = " ".join(str_val.split())

    str_val = str_val.strip()

    if case_sensitive_flag == 0:
        str_val = str_val.upper()

    if ignore_blank_flag == 1 and str_val == "":
        return None

    return str_val


def _compare_payload_with_latest_row(payload, latest_row, compare_config_rows):
    changes = []

    if not payload or not latest_row:
        _debug_compare_log("compare short-circuit because payload or latest_row missing")
        return {
            "has_changes": True,
            "changes": changes,
        }

    _debug_compare_log("---- FIELD LEVEL COMPARE START ----")

    for config_row in compare_config_rows or []:
        field_name = _safe_str(config_row.get("field_name"))
        same_thread_compare_flag = int(config_row.get("same_thread_compare_flag") or 0)

        if not field_name or same_thread_compare_flag != 1:
            continue

        raw_old_value = latest_row.get(field_name)
        raw_new_value = payload.get(field_name)

        old_value = _normalize_value_for_compare(raw_old_value, config_row)
        new_value = _normalize_value_for_compare(raw_new_value, config_row)

        _debug_compare_log(
            "FIELD:",
            field_name,
            "| raw_old =", _debug_value_repr(raw_old_value),
            "| raw_new =", _debug_value_repr(raw_new_value),
            "| norm_old =", _debug_value_repr(old_value),
            "| norm_new =", _debug_value_repr(new_value),
            "| ignore_blank_flag =", int(config_row.get("ignore_blank_flag") or 0),
            "| case_sensitive_flag =", int(config_row.get("case_sensitive_flag") or 0),
            "| trim_spaces_flag =", int(config_row.get("trim_spaces_flag") or 0),
            "| normalize_date_flag =", int(config_row.get("normalize_date_flag") or 0),
        )

        if old_value != new_value:
            _debug_compare_log(
                "FIELD CHANGE DETECTED ->",
                field_name,
                "| old =", _debug_value_repr(old_value),
                "| new =", _debug_value_repr(new_value),
            )
            changes.append({
                "field_name": field_name,
                "old_value": old_value,
                "new_value": new_value,
            })

    _debug_compare_log("---- FIELD LEVEL COMPARE END ----")
    _debug_compare_log("total changes detected:", len(changes))

    return {
        "has_changes": len(changes) > 0,
        "changes": changes,
    }


def _normalize_date_or_none(value):
    value = _safe_str(value)
    return value or None



def _table_exists_by_name(cursor, table_name):
    try:
        cursor.execute("SHOW TABLES LIKE %s", (table_name,))
        return cursor.fetchone() is not None
    except Exception:
        return False


def _get_table_columns_safe(cursor, table_name):
    try:
        cursor.execute(f"SHOW COLUMNS FROM {table_name}")
        rows = cursor.fetchall() or []
        columns = set()
        for row in rows:
            if isinstance(row, dict):
                field_name = row.get("Field")
            else:
                field_name = row[0] if row else None
            if field_name:
                columns.add(str(field_name))
        return columns
    except Exception:
        return set()



def _close_active_sla_logs_for_thread(
    conn,
    channel_thread_id,
    source_status="",
    target_status="",
    stop_time=None,
    remarks="",
):
    from datetime import timedelta

    channel_thread_id = _safe_str(channel_thread_id)
    source_status = _safe_str(source_status).upper()
    target_status = _safe_str(target_status).upper()

    if not conn or not channel_thread_id:
        return 0

    if not source_status or not target_status:
        return 0

    cursor = None

    try:
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                l.id,
                l.status,
                l.sla_start_on,
                l.sla_due_on,
                l.sla_stop_on,
                l.elapsed_mins,
                l.breached_on,
                r.start_event,
                r.stop_basis,
                r.target_duration_mins
            FROM operation_queue_sla_log l
            INNER JOIN sla_rule_master r
                ON r.id = l.sla_rule_id
            WHERE l.channel_thread_id = %s
              AND l.sla_stop_on IS NULL
              AND UPPER(TRIM(l.status))
                    IN ('RUNNING', 'NEAR BREACH', 'BREACHED')
              AND UPPER(TRIM(COALESCE(r.active_flag, ''))) = 'ACTIVE'
            """,
            (channel_thread_id,),
        )

        rows = cursor.fetchall() or []
        stopped_count = 0
        event_time = stop_time or datetime.now()

        for row in rows:
            stop_basis = (
                _safe_str(row.get("stop_basis"))
                .upper()
                .replace("_", " ")
            )

            if stop_basis == "CLOSED":
                should_stop = target_status == "CLOSED"
            elif stop_basis == "STATUS CHANGE":
                should_stop = source_status != target_status
            else:
                should_stop = False

            if not should_stop:
                continue

            sla_start_on = row.get("sla_start_on")

            # Backfill start time for ASSIGNED SLA rules if an older
            # runtime row was created before SLA timestamps were persisted.
            if not sla_start_on:
                start_event = (
                    _safe_str(row.get("start_event"))
                    .upper()
                    .replace("_", " ")
                )

                if start_event in {"ASSIGNED", "ASSIGNED AT"}:
                    cursor.execute(
                        """
                        SELECT MIN(assigned_at) AS first_assigned_at
                        FROM operation_queue_master
                        WHERE channel_thread_id = %s
                          AND assigned_at IS NOT NULL
                        """,
                        (channel_thread_id,),
                    )
                    start_row = cursor.fetchone() or {}
                    sla_start_on = start_row.get("first_assigned_at")

            if not sla_start_on:
                # Scheduler can repair/start this SLA later.
                continue

            sla_due_on = row.get("sla_due_on")
            if not sla_due_on:
                sla_due_on = sla_start_on + timedelta(
                    minutes=int(row.get("target_duration_mins") or 0)
                )

            elapsed_mins = max(
                0,
                int((event_time - sla_start_on).total_seconds() // 60),
            )

            is_breached = bool(
                sla_due_on and event_time >= sla_due_on
            )

            final_status = "BREACHED" if is_breached else "MET"

            breached_on = row.get("breached_on")
            if is_breached and not breached_on:
                breached_on = sla_due_on or event_time

            final_remarks = _safe_str(remarks)
            if not final_remarks:
                final_remarks = (
                    "SLA stopped after target breach."
                    if is_breached
                    else "SLA stopped within configured target."
                )

            cursor.execute(
                """
                UPDATE operation_queue_sla_log
                SET sla_start_on = %s,
                    sla_due_on = %s,
                    sla_stop_on = %s,
                    elapsed_mins = %s,
                    breached_on = %s,
                    status = %s,
                    remarks = %s,
                    updated_on = NOW()
                WHERE id = %s
                  AND sla_stop_on IS NULL
                """,
                (
                    sla_start_on,
                    sla_due_on,
                    event_time,
                    elapsed_mins,
                    breached_on,
                    final_status,
                    final_remarks,
                    row.get("id"),
                ),
            )

            stopped_count += int(cursor.rowcount or 0)

        return stopped_count

    except Exception as e:
        try:
            print("_close_active_sla_logs_for_thread error:", e)
        except Exception:
            pass
        return 0

    finally:
        if cursor:
            cursor.close()


def _get_latest_sla_runtime_by_threads(cursor, thread_ids):
    result = {}

    if not thread_ids:
        return result

    if not _table_exists_by_name(cursor, "operation_queue_sla_log"):
        return result

    table_columns = _get_table_columns_safe(cursor, "operation_queue_sla_log")
    if not table_columns or "channel_thread_id" not in table_columns:
        return result

    select_parts = ["channel_thread_id"]

    for col in [
        "rule_code",
        "status",
        "elapsed_mins",
        "escalated_to",
        "current_escalation_level",
        "target_time",
        "near_breach_time",
        "start_time",
        "updated_on",
        "created_on",
    ]:
        if col in table_columns:
            select_parts.append(col)

    order_col = "updated_on" if "updated_on" in table_columns else "created_on" if "created_on" in table_columns else None

    placeholders = ", ".join(["%s"] * len(thread_ids))
    sql = f"""
        SELECT {", ".join(select_parts)}
        FROM operation_queue_sla_log
        WHERE channel_thread_id IN ({placeholders})
    """
    if order_col:
        sql += f" ORDER BY {order_col} DESC"

    cursor.execute(sql, tuple(thread_ids))
    rows = cursor.fetchall() or []

    for row in rows:
        thread_id = _safe_str(row.get("channel_thread_id"))
        if thread_id and thread_id not in result:
            result[thread_id] = row

    return result


def _clone_row_payload(source_row, updates, target_status, actor_code, new_channel_message_id=None):
    payload = dict(source_row or {})

    payload.pop("id", None)
    payload.pop("created_on", None)
    payload.pop("updated_on", None)

    payload["ivac_center"] = _safe_str(updates.get("ivac_center")) or payload.get("ivac_center")
    payload["hospital_shrtnm"] = _safe_str(updates.get("hospital_shrtnm")) or payload.get("hospital_shrtnm")
    incoming_department = _normalize_department_csv(updates.get("hospital_dept"))
    payload["hospital_dept"] = incoming_department or payload.get("hospital_dept")
    payload["doctor_nm"] = _safe_str(updates.get("doctor_nm")) or payload.get("doctor_nm")
    payload["intent_remarks"] = _safe_str(updates.get("intent_remarks")) or payload.get("intent_remarks")
    payload["priority_status"] = _safe_str(updates.get("priority_status")) or payload.get("priority_status")
    payload["vil_date"] = _normalize_date_or_none(updates.get("vil_date")) or payload.get("vil_date")
    payload["appointment_dt"] = _normalize_date_or_none(updates.get("appointment_dt")) or payload.get("appointment_dt")
    print(
        "OPQ_ASSIGN_DEBUG CLONE_BEFORE",
        "source_assigned_to=", repr(payload.get("assigned_to")),
        "incoming_assigned_to=", repr(updates.get("assigned_to")),
        "source_assigned_by=", repr(payload.get("assigned_by")),
        "source_assigned_at=", repr(payload.get("assigned_at")),
        "actor_code=", repr(actor_code),
        flush=True,
    )

    payload["agent_shrtnm"] = _safe_str(updates.get("agent_shrtnm")) or payload.get("agent_shrtnm")
    payload["assigned_to"] = _safe_str(updates.get("assigned_to")) or payload.get("assigned_to")
    payload["parse_remarks"] = _safe_str(updates.get("parse_remarks"))

    print(
        "OPQ_ASSIGN_DEBUG CLONE_AFTER",
        "assigned_to=", repr(payload.get("assigned_to")),
        flush=True,
    )
    payload["wrkflw_status"] = target_status

    if new_channel_message_id:
        payload["channel_message_id"] = new_channel_message_id

    if "assigned_by" in payload and payload.get("assigned_to"):
        payload["assigned_by"] = actor_code or payload.get("assigned_by")

    if "assigned_at" in payload and payload.get("assigned_to"):
        payload["assigned_at"] = datetime.now()

    if "parse_status" in payload and not payload.get("parse_status"):
        payload["parse_status"] = "PARSED"

    return payload


def create_operation_queue_followup_row(payload, target_status, actor_code=""):
    payload = payload or {}
    row_id = _safe_str(payload.get("row_id") or payload.get("id"))
    parse_remarks = _safe_str(
        payload.get("parse_remarks")
        or payload.get("operation_remarks")
        or payload.get("operational_remarks")
        or payload.get("remarks")
    )
    target_status = _safe_str(target_status).upper()

    if not row_id:
        return {"success": False, "message": "Row id is required."}

    if not parse_remarks:
        return {"success": False, "message": "Operation Remarks is mandatory."}

    if target_status not in ("IN PROGRESS", "CLOSED"):
        return {"success": False, "message": "Invalid target status."}

    payload["parse_remarks"] = parse_remarks

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        source_row = _fetch_operation_queue_row_by_id(cursor, row_id)
        if not source_row:
            return {"success": False, "message": "Selected row not found."}

        source_status = _safe_str(source_row.get("wrkflw_status")).upper()
        if source_status == "CLOSED":
            return {"success": False, "message": "Closed records cannot be modified."}

        agency_candidate = _safe_str(payload.get("agent_shrtnm"))
        if agency_candidate:
            cursor.execute("""
                SELECT short_name FROM partner_reg
                WHERE UPPER(TRIM(partner_type)) = 'AGENCY'
                  AND UPPER(TRIM(short_name)) = UPPER(TRIM(%s))
                LIMIT 1
            """, (agency_candidate,))
            agency_row = cursor.fetchone()
            if not agency_row:
                return {"success": False, "message": "Invalid Agency. Select an Agency from Partner Master."}
            payload["agent_shrtnm"] = agency_row["short_name"]

        new_channel_message_id = _build_followup_channel_message_id(
            existing_message_id=source_row.get("channel_message_id"),
            channel_thread_id=source_row.get("channel_thread_id"),
            cursor=cursor,
        )

        cloned_payload = _clone_row_payload(
            source_row=source_row,
            updates=payload,
            target_status=target_status,
            actor_code=actor_code,
            new_channel_message_id=new_channel_message_id,
        )

        insert_columns = _get_generic_table_columns(cursor, "operation_queue_master")
        insert_columns = [
            col for col in insert_columns
            if col not in ("id", "created_on", "updated_on")
        ]

        placeholders = ", ".join(["%s"] * len(insert_columns))
        column_sql = ", ".join(insert_columns)
        values = [cloned_payload.get(col) for col in insert_columns]

        sql = f"""
            INSERT INTO operation_queue_master ({column_sql})
            VALUES ({placeholders})
        """
        cursor.execute(sql, values)

        _close_active_sla_logs_for_thread(
            conn=conn,
            channel_thread_id=cloned_payload.get("channel_thread_id"),
            source_status=source_status,
            target_status=target_status,
            stop_time=datetime.now(),
            remarks=f"SLA stopped due to workflow status changed to {target_status}."
        )

        conn.commit()

        return {
            "success": True,
            "message": "Record resolved successfully." if target_status == "CLOSED" else "Record saved successfully.",
            "inserted_id": cursor.lastrowid,
            "channel_thread_id": cloned_payload.get("channel_thread_id"),
            "channel_message_id": cloned_payload.get("channel_message_id"),
            "wrkflw_status": cloned_payload.get("wrkflw_status"),
        }

    except Exception as e:
        if conn:
            conn.rollback()
        return {"success": False, "message": str(e)}

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def create_operation_queue_followup_rows(payload_rows, target_status, actor_code=""):
    payload_rows = payload_rows or []
    target_status = _safe_str(target_status).upper()

    if not payload_rows:
        return {
            "success": False,
            "message": "No rows selected.",
        }

    results = []
    success_count = 0
    failure_count = 0

    for payload in payload_rows:
        result = create_operation_queue_followup_row(
            payload=payload,
            target_status=target_status,
            actor_code=actor_code,
        )
        results.append(result)

        if result.get("success"):
            success_count += 1
        else:
            failure_count += 1

    if failure_count == 0:
        if target_status == "CLOSED":
            message = f"Resolved {success_count} row(s) successfully."
        else:
            message = f"Saved {success_count} row(s) successfully."
    else:
        message = f"Processed {success_count} row(s). Failed {failure_count} row(s)."

    return {
        "success": failure_count == 0,
        "message": message,
        "results": results,
        "success_count": success_count,
        "failure_count": failure_count,
    }


def _get_table_columns(cursor, table_name):
    try:
        cursor.execute(f"SHOW COLUMNS FROM {table_name}")
        rows = cursor.fetchall() or []
        columns = set()
        for row in rows:
            if isinstance(row, dict):
                field_name = row.get("Field")
            else:
                field_name = row[0] if row else None
            if field_name:
                columns.add(str(field_name))
        return columns
    except Exception:
        return set()


def _normalize_request_for_value(value):
    text = _safe_str(value).upper()
    if not text:
        return ""
    if "APPOINT" in text:
        return "APPOINTMENT"
    if "VIL" in text:
        return "VIL"
    return text


def _is_same_tracked_request(current_request, sent_request):
    current_norm = _normalize_request_for_value(current_request)
    sent_norm = _normalize_request_for_value(sent_request)

    if not current_norm or not sent_norm:
        return False

    tracked_values = {"VIL", "APPOINTMENT"}
    if current_norm not in tracked_values or sent_norm not in tracked_values:
        return False

    return current_norm == sent_norm


def _get_latest_processing_by_thread(cursor, channel_thread_id):
    channel_thread_id = _safe_str(channel_thread_id)
    if not channel_thread_id:
        return {}

    processing_columns = _get_table_columns(cursor, "operation_queue_processing")
    optional_fields = []

    if "template_name" in processing_columns:
        optional_fields.append("op.template_name AS template_name")
    if "template_code" in processing_columns:
        optional_fields.append("op.template_code AS template_code")

    select_optional = ""
    if optional_fields:
        select_optional = ",\n                " + ",\n                ".join(optional_fields)

    cursor.execute(
        f"""
        SELECT
            op.operation_queue_id,
            op.conversion_status,
            op.conversion_started_on,
            op.conversion_completed_on,
            op.conversion_request_id,
            op.email_status,
            op.email_sent_on,
            op.email_error,
            op.email_attempt_count{select_optional}
        FROM operation_queue_processing op
        INNER JOIN operation_queue_master oq
            ON oq.id = op.operation_queue_id
        WHERE oq.channel_thread_id = %s
        ORDER BY op.operation_queue_id DESC
        LIMIT 1
        """,
        (channel_thread_id,),
    )
    return cursor.fetchone() or {}


def get_operation_queue_record_by_id(operation_queue_id):
    operation_queue_id = _safe_str(operation_queue_id)
    if not operation_queue_id:
        return {}

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT *
            FROM operation_queue_master
            WHERE id = %s
            LIMIT 1
            """,
            (operation_queue_id,),
        )

        row = cursor.fetchone() or {}
        if not row:
            return {}

        processing_row = _get_latest_processing_by_thread(cursor, row.get("channel_thread_id"))
        if processing_row:
            row["conversion_status"] = processing_row.get("conversion_status")
            row["conversion_started_on"] = processing_row.get("conversion_started_on")
            row["conversion_completed_on"] = processing_row.get("conversion_completed_on")
            row["conversion_request_id"] = processing_row.get("conversion_request_id")
            row["email_status"] = processing_row.get("email_status")
            row["email_sent_on"] = processing_row.get("email_sent_on")
            row["email_error"] = processing_row.get("email_error")
            row["email_attempt_count"] = processing_row.get("email_attempt_count")
            if "template_name" in processing_row:
                row["template_name"] = processing_row.get("template_name")
            if "template_code" in processing_row:
                row["email_template_code"] = processing_row.get("template_code")
            if "sent_intent_remarks" in processing_row:
                row["sent_intent_remarks"] = processing_row.get("sent_intent_remarks")
        else:
            row.setdefault("email_status", "NOT_SENT")
            row.setdefault("template_name", "")
            row.setdefault("sent_intent_remarks", "")

        return row

    except Exception:
        return {}

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_latest_operation_queue_record_by_thread_id(channel_thread_id):
    channel_thread_id = _safe_str(channel_thread_id)
    if not channel_thread_id:
        return {}

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT *
            FROM operation_queue_master
            WHERE channel_thread_id = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (channel_thread_id,),
        )

        return cursor.fetchone() or {}

    except Exception:
        return {}

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_operation_queue_grid_data():
    """
    Important:
    Return ALL rows for each channel_thread_id, not only the latest row.
    Frontend groups them thread-wise and calculates:
    - Message Count
    - Old/New field change history
    - Latest row details
    """
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        sql = """
            SELECT
                oq.id,
                oq.channel_thread_id,
                oq.channel_message_id,
                oq.channel_message_log_id,
                oq.channel_code,

                oq.patpassport_no,
                oq.patgiven_nm,
                oq.patsur_nm,

                oq.patient_dob,
                oq.patient_country,
                oq.patient_gender,
                oq.patient_contno,
                oq.patient_age,
                oq.patient_address,
                oq.patient_email,
                oq.patpass_expirydt,

                oq.attn1_givennm,
                oq.attn1_surnm,
                oq.attn1passport_no,
                oq.attn1pat_relation,
                oq.attn1_country,
                oq.attn1_dob,
                oq.attn1_gender,
                oq.attn1_contno,
                oq.attn1_age,
                oq.attn1_address,
                oq.attn1_email,
                oq.attn1pass_expirydt,

                oq.attn2_givennm,
                oq.attn2_surnm,
                oq.attn2passport_no,
                oq.attn2pat_relation,
                oq.attn2_country,
                oq.attn2_dob,
                oq.attn2_gender,
                oq.attn2_contno,
                oq.attn2_age,
                oq.attn2_address,
                oq.attn2_email,
                oq.attn2pass_expirydt,

                oq.attn3_givennm,
                oq.attn3_surnm,
                oq.attn3passport_no,
                oq.attn3pat_relation,
                oq.attn3_country,
                oq.attn3_dob,
                oq.attn3_gender,
                oq.attn3_contno,
                oq.attn3_age,
                oq.attn3_address,
                oq.attn3_email,
                oq.attn3pass_expirydt,

                oq.ivac_center,
                oq.hospital_shrtnm,
                oq.hospital_dept,
                oq.doctor_nm,
                oq.intent_remarks,
                oq.vil_date,
                oq.appointment_dt,

                oq.agent_shrtnm,

                oq.assigned_to,
                oq.assigned_by,
                oq.assigned_at,
                oq.wrkflw_status,
                oq.priority_status,
                oq.parse_status,
                oq.parse_remarks,
                oq.created_on,
                oq.updated_on
            FROM operation_queue_master oq
            WHERE oq.channel_thread_id IS NOT NULL
              AND TRIM(oq.channel_thread_id) <> ''
            ORDER BY
                oq.channel_thread_id ASC,
                oq.id ASC
        """

        cursor.execute(sql)
        rows = cursor.fetchall() or []

        thread_ids = []
        seen_thread_ids = set()
        for row in rows:
            thread_id = _safe_str(row.get("channel_thread_id"))
            if thread_id and thread_id not in seen_thread_ids:
                seen_thread_ids.add(thread_id)
                thread_ids.append(thread_id)

        attachments_by_thread = _build_attachments_by_thread(cursor, thread_ids)

        processing_by_thread = {}
        for thread_id in thread_ids:
            processing_by_thread[thread_id] = _get_latest_processing_by_thread(cursor, thread_id)

        sla_runtime_by_thread = _get_latest_sla_runtime_by_threads(cursor, thread_ids)

        enriched_rows = []
        for row in rows:
            thread_id = _safe_str(row.get("channel_thread_id"))
            row["thread_attachments"] = attachments_by_thread.get(thread_id, [])

            processing_row = processing_by_thread.get(thread_id) or {}
            row["conversion_status"] = processing_row.get("conversion_status") or row.get("conversion_status") or "NOT_STARTED"
            row["email_status"] = processing_row.get("email_status") or "NOT_SENT"
            row["email_sent_on"] = processing_row.get("email_sent_on")
            row["email_error"] = processing_row.get("email_error")
            row["email_attempt_count"] = processing_row.get("email_attempt_count")
            row["template_name"] = processing_row.get("template_name") or ""
            row["email_template_code"] = processing_row.get("template_code") or ""
            row["sent_intent_remarks"] = processing_row.get("sent_intent_remarks") or ""

            sla_row = sla_runtime_by_thread.get(thread_id) or {}
            row["sla_rule_code"] = sla_row.get("rule_code") or ""
            row["sla_status"] = sla_row.get("status") or ""
            row["sla_elapsed_mins"] = sla_row.get("elapsed_mins")
            row["sla_escalated_to"] = sla_row.get("escalated_to") or ""
            row["sla_current_escalation_level"] = sla_row.get("current_escalation_level")
            row["sla_start_time"] = sla_row.get("start_time")
            row["sla_near_breach_time"] = sla_row.get("near_breach_time")
            row["sla_target_time"] = sla_row.get("target_time")

            enriched_rows.append(row)

        return enriched_rows

    except Exception:
        return []

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_operation_queue_summary_counts():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        sql = """
            SELECT
                COUNT(*) AS total_queue,
                (
                    SELECT COUNT(*)
                    FROM operation_queue_master
                    WHERE assigned_to IS NULL OR TRIM(assigned_to) = ''
                ) AS open_requests,
                (
                    SELECT COUNT(*)
                    FROM operation_queue_master
                    WHERE assigned_at IS NOT NULL
                      AND DATE(assigned_at) = CURDATE()
                ) AS assigned_today,
                (
                    SELECT COUNT(*)
                    FROM operation_queue_master
                    WHERE UPPER(TRIM(IFNULL(priority_status, ''))) = 'HIGH'
                ) AS high_priority_count,
                (
                    SELECT COUNT(DISTINCT channel_thread_id)
                    FROM operation_queue_master
                    WHERE channel_thread_id IS NOT NULL
                      AND TRIM(channel_thread_id) <> ''
                ) AS thread_groups
            FROM operation_queue_master
            LIMIT 1
        """

        cursor.execute(sql)
        result = cursor.fetchone() or {}

        latest_thread_sql = """
            SELECT oq.channel_thread_id,
                   UPPER(TRIM(IFNULL(oq.wrkflw_status, ''))) AS wrkflw_status
            FROM operation_queue_master oq
            INNER JOIN (
                SELECT channel_thread_id, MAX(id) AS max_id
                FROM operation_queue_master
                WHERE channel_thread_id IS NOT NULL
                  AND TRIM(channel_thread_id) <> ''
                GROUP BY channel_thread_id
            ) latest_oq
                ON latest_oq.channel_thread_id = oq.channel_thread_id
               AND latest_oq.max_id = oq.id
        """
        cursor.execute(latest_thread_sql)
        latest_threads = cursor.fetchall() or []

        thread_ids = []
        for item in latest_threads:
            thread_id = _safe_str(item.get("channel_thread_id"))
            if thread_id:
                thread_ids.append(thread_id)

        sla_runtime_by_thread = _get_latest_sla_runtime_by_threads(cursor, thread_ids)

        near_breach = 0
        breached = 0
        escalated = 0

        for item in latest_threads:
            thread_id = _safe_str(item.get("channel_thread_id"))
            wrkflw_status = _safe_str(item.get("wrkflw_status")).upper()
            if not thread_id or wrkflw_status == "CLOSED":
                continue

            sla_row = sla_runtime_by_thread.get(thread_id) or {}
            sla_status = _safe_str(sla_row.get("status")).upper()

            try:
                escalation_level = int(sla_row.get("current_escalation_level") or 0)
            except Exception:
                escalation_level = 0

            if sla_status == "NEAR BREACH":
                near_breach += 1
            elif sla_status == "BREACHED":
                breached += 1
            elif sla_status == "ESCALATED":
                escalated += 1

            if escalation_level > 0 and sla_status != "ESCALATED":
                escalated += 1

        return {
            "total_queue": int(result.get("total_queue", 0) or 0),
            "open_requests": int(result.get("open_requests", 0) or 0),
            "assigned_today": int(result.get("assigned_today", 0) or 0),
            "high_priority": int(result.get("high_priority_count", 0) or 0),
            "thread_groups": int(result.get("thread_groups", 0) or 0),
            "near_breach": near_breach,
            "breached": breached,
            "escalated": escalated,
        }

    except Exception:
        return {
            "total_queue": 0,
            "open_requests": 0,
            "assigned_today": 0,
            "high_priority": 0,
            "thread_groups": 0,
            "near_breach": 0,
            "breached": 0,
            "escalated": 0,
        }

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


from app.queue_management.queue_email_service import (
    get_hospital_email_by_short_name,
    get_active_email_templates,
    get_email_template_by_name,
    get_active_email_placeholder_mappings,
    get_thread_attachments,
    _build_email_template_context,
    _render_email_template_text,
    _clean_rendered_subject,
    _clean_rendered_body as _queue_email_clean_rendered_body,
    _attach_files_to_message,
    _collect_attachment_files,
    _get_mail_backend,
    _get_default_sender_email,
    _send_email_via_graph,
)


def _strip_email_line_value(value):
    """
    Normalize rendered email line values before blank-line detection.
    Keeps existing email rendering flow intact; only removes cosmetic blank fields.
    """
    text = _safe_str(value)
    if not text:
        return ""

    text = text.replace("\xa0", " ")
    text = text.replace("&nbsp;", " ")
    text = re.sub(r"<[^>]+>", "", text)
    text = re.sub(r"\{\{\s*[^{}]+\s*\}\}", "", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _operation_queue_should_keep_email_body_line(line):
    """
    Remove email body lines whose template field value is blank.

    Examples removed after rendering:
        Attender2 Given Name :
        Attender2 Passport Number -
        Attender3 Contact Number |

    Existing non-blank lines and normal paragraphs are preserved.
    """
    raw_line = _safe_str(line).rstrip()
    stripped = _strip_email_line_value(raw_line)

    if not stripped:
        return False

    if re.fullmatch(r"[\|\-:_.,/\\\s]+", stripped):
        return False

    # Field-label style lines. If right side is empty, hide the full line.
    # This covers: Label:, Label :, Label - , Label | , etc.
    field_match = re.match(r"^(.+?)\s*(:|\-|\|)\s*(.*)$", stripped)
    if field_match:
        left = _strip_email_line_value(field_match.group(1))
        right = _strip_email_line_value(field_match.group(3))
        if left and not right:
            return False

    return True


def _clean_rendered_body(text):
    """
    Operation Queue email body cleaner.

    Purpose:
    - Keep existing template rendering behavior.
    - Remove unresolved placeholders.
    - Remove only blank field lines, especially Attender1/2/3 fields with no data.
    - Preserve all lines where an actual value exists.
    """
    text = text or ""

    try:
        text = re.sub(r"\{\{\s*[^{}]+\s*\}\}", "", text)
    except Exception:
        pass

    text = text.replace("\r\n", "\n").replace("\r", "\n")

    cleaned_lines = []
    blank_streak = 0

    for raw_line in text.split("\n"):
        line = raw_line.rstrip()

        if not _operation_queue_should_keep_email_body_line(line):
            continue

        if not _safe_str(line):
            blank_streak += 1
            if blank_streak <= 1:
                cleaned_lines.append("")
            continue

        blank_streak = 0
        cleaned_lines.append(line)

    cleaned_text = "\n".join(cleaned_lines)
    cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)

    return cleaned_text.strip()


def build_operation_queue_email(
    operation_queue_id,
    template_name,
    to_email,
    cc_email="",
    bcc_email="",
    selected_attachment_ids=None,
):
    operation_queue_id = _safe_str(operation_queue_id)
    template_name = _safe_str(template_name)
    to_email = _safe_str(to_email)
    cc_email = _safe_str(cc_email)
    bcc_email = _safe_str(bcc_email)
    selected_attachment_ids = selected_attachment_ids or []

    if not operation_queue_id:
        return {
            "success": False,
            "message": "Operation queue id is required.",
        }

    if not template_name:
        return {
            "success": False,
            "message": "Email template is required.",
        }

    if not to_email:
        return {
            "success": False,
            "message": "To email is required.",
        }

    queue_row = get_operation_queue_record_by_id(operation_queue_id)
    if not queue_row:
        return {
            "success": False,
            "message": "Operation queue record not found.",
        }

    template_row = get_email_template_by_name(template_name)
    if not template_row:
        return {
            "success": False,
            "message": "Active email template not found.",
        }

    context = _build_email_template_context(queue_row)

    rendered_subject = _render_email_template_text(
        template_row.get("template_subject"),
        context,
    )
    rendered_body = _render_email_template_text(
        template_row.get("template_body"),
        context,
    )

    rendered_subject = _clean_rendered_subject(rendered_subject)
    rendered_body = _clean_rendered_body(rendered_body)

    attachment_rows = get_thread_attachments(
        channel_thread_id=queue_row.get("channel_thread_id"),
        operation_queue_id=operation_queue_id,
    )

    if selected_attachment_ids:
        selected_id_set = {str(x).strip() for x in selected_attachment_ids if _safe_str(x)}
        attachment_rows = [
            row for row in attachment_rows
            if str(row.get("id")).strip() in selected_id_set
        ]

    email_message = EmailMessage()

    from_email = _get_default_sender_email()

    if from_email:
        email_message["From"] = from_email

    email_message["To"] = to_email
    email_message["Subject"] = rendered_subject

    if cc_email:
        email_message["Cc"] = cc_email

    if bcc_email:
        email_message["Bcc"] = bcc_email

    email_message.set_content(rendered_body or "")

    html_body = (rendered_body or "").replace("\r\n", "\n").replace("\n", "<br>")
    email_message.add_alternative(html_body, subtype="html")

    attached_files, missing_files = _attach_files_to_message(email_message, attachment_rows)
    attachment_files_for_graph, missing_files_for_graph = _collect_attachment_files(attachment_rows)

    if not missing_files and missing_files_for_graph:
        missing_files = missing_files_for_graph

    return {
        "success": True,
        "message": "Email prepared successfully.",
        "email_message": email_message,
        "queue_row": queue_row,
        "template_row": template_row,
        "rendered_subject": rendered_subject,
        "rendered_body": rendered_body,
        "attached_files": attached_files,
        "missing_files": missing_files,
        "attachment_count": len(attached_files),
        "attachment_rows": attachment_rows,
        "graph_attachment_files": attachment_files_for_graph,
        "from_email": from_email,
        "to_email": to_email,
        "cc_email": cc_email,
        "bcc_email": bcc_email,
    }


def send_operation_queue_email(
    operation_queue_id,
    template_name,
    to_email,
    cc_email="",
    bcc_email="",
    selected_attachment_ids=None,
):
    result = build_operation_queue_email(
        operation_queue_id=operation_queue_id,
        template_name=template_name,
        to_email=to_email,
        cc_email=cc_email,
        bcc_email=bcc_email,
        selected_attachment_ids=selected_attachment_ids,
    )

    if not result.get("success"):
        return result

    email_message = result.get("email_message")
    if not email_message:
        return {
            "success": False,
            "message": "Email message could not be built.",
        }

    mail_backend = _get_mail_backend()

    if mail_backend == "GRAPH":
        graph_result = _send_email_via_graph(
            sender_email=result.get("from_email"),
            to_email=result.get("to_email"),
            cc_email=result.get("cc_email"),
            bcc_email=result.get("bcc_email"),
            rendered_subject=result.get("rendered_subject"),
            rendered_body=result.get("rendered_body"),
            attached_files=result.get("graph_attachment_files", []),
        )

        if graph_result.get("success"):
            response_message = "Email sent successfully."
            if result.get("missing_files"):
                response_message = "Email sent successfully, but some attachment files were not found on disk."

            return {
                "success": True,
                "message": response_message,
                "rendered_subject": result.get("rendered_subject"),
                "rendered_body": result.get("rendered_body"),
                "attached_files": result.get("attached_files", []),
                "missing_files": result.get("missing_files", []),
                "attachment_count": result.get("attachment_count", 0),
            }

        return {
            "success": False,
            "message": graph_result.get("message") or "Failed to send email through Microsoft Graph.",
            "rendered_subject": result.get("rendered_subject"),
            "rendered_body": result.get("rendered_body"),
            "attached_files": result.get("attached_files", []),
            "missing_files": result.get("missing_files", []),
            "attachment_count": result.get("attachment_count", 0),
        }

    smtp_host = (
        current_app.config.get("MAIL_SERVER")
        or current_app.config.get("SMTP_HOST")
        or ""
    )
    smtp_port = int(
        current_app.config.get("MAIL_PORT")
        or current_app.config.get("SMTP_PORT")
        or 587
    )
    smtp_username = (
        current_app.config.get("MAIL_USERNAME")
        or current_app.config.get("SMTP_USERNAME")
        or ""
    )
    smtp_password = (
        current_app.config.get("MAIL_PASSWORD")
        or current_app.config.get("SMTP_PASSWORD")
        or ""
    )
    use_tls = bool(current_app.config.get("MAIL_USE_TLS", True))
    use_ssl = bool(current_app.config.get("MAIL_USE_SSL", False))

    if not smtp_host:
        return {
            "success": False,
            "message": "SMTP host / MAIL_SERVER is not configured.",
        }

    try:
        if use_ssl:
            with smtplib.SMTP_SSL(smtp_host, smtp_port) as server:
                if smtp_username and smtp_password:
                    server.login(smtp_username, smtp_password)
                server.send_message(email_message)
        else:
            with smtplib.SMTP(smtp_host, smtp_port) as server:
                server.ehlo()
                if use_tls:
                    server.starttls()
                    server.ehlo()
                if smtp_username and smtp_password:
                    server.login(smtp_username, smtp_password)
                server.send_message(email_message)

        response_message = "Email sent successfully."
        if result.get("missing_files"):
            response_message = (
                "Email sent successfully, but some attachment files were not found on disk."
            )

        return {
            "success": True,
            "message": response_message,
            "rendered_subject": result.get("rendered_subject"),
            "rendered_body": result.get("rendered_body"),
            "attached_files": result.get("attached_files", []),
            "missing_files": result.get("missing_files", []),
            "attachment_count": result.get("attachment_count", 0),
        }

    except Exception as e:
        return {
            "success": False,
            "message": str(e),
            "rendered_subject": result.get("rendered_subject"),
            "rendered_body": result.get("rendered_body"),
            "attached_files": result.get("attached_files", []),
            "missing_files": result.get("missing_files", []),
            "attachment_count": result.get("attachment_count", 0),
        }


from app.queue_management.queue_conversion_email_service import (
    precheck_queue_email_conversion,
    claim_conversion_lock,
    get_hospital_city_from_master,
    convert_queue_to_business_records,
    process_without_email,
    send_email_with_conversion,
    finalize_email_processing_metadata,
    start_email_conversion_session,
)
