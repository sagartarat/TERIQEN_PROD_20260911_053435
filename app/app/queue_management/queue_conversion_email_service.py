import os
import mimetypes
import smtplib
import uuid
from datetime import datetime, date
from email.message import EmailMessage
from typing import Any, Dict, Optional

import mysql.connector
from flask import current_app

from app.patient.helpers import insert_patient_if_not_exists
from app.queue_management.queue_email_service import (
    get_thread_attachments,
    _send_email_via_graph,
    _normalize_filesystem_path,
    _sanitize_attachment_filename,
    _is_allowed_attachment_file,
)


def _oq_safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


# ============================================================
# SAFE LOCAL HELPERS
# ============================================================
# These helpers are intentionally kept inside this file so that
# queue_conversion_email_service.py does not depend on helper
# functions from operation_queue_service.py during email conversion.
# Existing business flow is not changed.

def _safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _is_safe_table_name(table_name: Any) -> bool:
    table_name = _safe_str(table_name)
    return bool(table_name) and table_name.replace("_", "").isalnum()


def _get_table_columns(cursor, table_name):
    """
    Return available column names for any table.
    This fixes: name '_get_table_columns' is not defined.
    """
    if not _is_safe_table_name(table_name):
        return []

    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    rows = cursor.fetchall() or []

    columns = []
    for row in rows:
        if isinstance(row, dict):
            col_name = row.get("Field")
        else:
            col_name = row[0] if row else None

        if col_name:
            columns.append(str(col_name))

    return columns


def _get_table_columns_safe(cursor, table_name):
    try:
        return set(_get_table_columns(cursor, table_name))
    except Exception:
        return set()


def _table_exists_by_name(cursor, table_name):
    if not _is_safe_table_name(table_name):
        return False

    try:
        cursor.execute("SHOW TABLES LIKE %s", (table_name,))
        return cursor.fetchone() is not None
    except Exception:
        return False


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


def _get_latest_active_patient_workflow_row(conn, passport_no):
    """
    Return the latest workflow row only when Workflow Rule Master says the
    existing same-passport case must continue.

    wrkflwrule_master.allow_new_case_same_passport is the source of truth:
    - 0 -> existing workflow remains active for this passport; update it.
    - 1 -> a new same-passport case is permitted; return {} so conversion
           inserts a fresh patient_wrkflw row.
    - missing rule -> fail closed by treating the latest workflow as active.

    No workflow status names are hard-coded here.
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

        # Missing rule intentionally fails closed: keep the existing workflow.
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
                allow_new_case_same_passport,
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
        return {
            "success": False,
            "message": f"Workflow status '{wrkflw_status}' is not editable as per wrkflwrule_master.",
            "rule_row": rule_row,
        }

    if vil_date_required == 1 and not _normalize_db_date(queue_row.get("vil_date")):
        return {
            "success": False,
            "message": f"VIL Date is required for workflow status '{wrkflw_status}'.",
            "rule_row": rule_row,
        }

    if appointment_date_required == 1 and not _normalize_db_date(queue_row.get("appointment_dt")):
        return {
            "success": False,
            "message": f"Appointment Date is required for workflow status '{wrkflw_status}'.",
            "rule_row": rule_row,
        }

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

    return {
        "updated": cursor.rowcount > 0,
        "ld_internalno": _safe_str(existing_row.get("ld_internalno")),
        "vil_internalno": vil_internalno,
    }



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


def _get_mail_backend():
    backend = (
        current_app.config.get("MAIL_BACKEND")
        or current_app.config.get("EMAIL_BACKEND")
        or current_app.config.get("DEFAULT_MAIL_BACKEND")
        or "SMTP"
    )
    return _safe_str(backend).upper()


def _get_default_sender_email():
    return _safe_str(
        current_app.config.get("GRAPH_SENDER_EMAIL")
        or current_app.config.get("MAIL_FROM")
        or current_app.config.get("MAIL_USERNAME")
        or current_app.config.get("SMTP_USERNAME")
    )


def _collect_attachment_files(attachment_rows):
    """
    Resolve only database-backed Operation Queue attachment rows through the
    central secure attachment resolver in queue_email_service.py.

    The browser supplies attachment IDs, not filesystem paths. get_thread_attachments()
    first limits those IDs to the current channel thread; this function then
    resolves the stored logical path inside approved server roots.
    """
    attached_files = []
    missing_files = []

    for row in attachment_rows or []:
        stored_path = _safe_str((row or {}).get("file_path"))
        file_name = _sanitize_attachment_filename(
            (row or {}).get("file_name"),
            fallback_path=stored_path,
        )

        if not stored_path:
            continue

        resolved_path = _normalize_filesystem_path(stored_path)

        if not resolved_path or not _is_allowed_attachment_file(resolved_path):
            # Never return physical checked paths to the UI/API.
            missing_files.append({
                "id": (row or {}).get("id"),
                "file_name": file_name,
                "file_path": stored_path,
                "reason": "Attachment unavailable or blocked by security validation.",
            })
            continue

        guessed_mime, _ = mimetypes.guess_type(resolved_path)
        stored_mime = _safe_str((row or {}).get("mime_type"))

        attached_files.append({
            "id": (row or {}).get("id"),
            "file_name": file_name,
            "file_path": resolved_path,  # internal send path only
            "mime_type": guessed_mime or stored_mime or "application/octet-stream",
        })

    return attached_files, missing_files


def _oq_db_connection():
    host = (
        current_app.config.get("DB_HOST")
        or current_app.config.get("MYSQL_HOST")
        or "127.0.0.1"
    )
    user = (
        current_app.config.get("DB_USER")
        or current_app.config.get("MYSQL_USER")
        or "root"
    )
    password = (
        current_app.config.get("DB_PASSWORD")
        or current_app.config.get("MYSQL_PASSWORD")
        or ""
    )
    database = (
        current_app.config.get("DB_NAME")
        or current_app.config.get("MYSQL_DB")
        or ""
    )
    port = int(
        current_app.config.get("DB_PORT")
        or current_app.config.get("MYSQL_PORT")
        or 3306
    )

    if str(host).strip() in ("", ".", "localhost"):
        host = "127.0.0.1"

    return mysql.connector.connect(
        host=host,
        user=user,
        password=password,
        database=database,
        port=port,
        autocommit=False,
        use_pure=True,
        time_zone="+05:30",
    )


def _fetch_latest_queue_row_by_id(conn, queue_id: int) -> Optional[Dict[str, Any]]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT *
            FROM operation_queue_master
            WHERE id = %s
            LIMIT 1
            """,
            (queue_id,),
        )
        return cursor.fetchone()
    finally:
        cursor.close()


# ================================
# NEW PROCESSING HELPERS
# ================================

def _get_processing_row(cursor, queue_id):
    cursor.execute("""
        SELECT * FROM operation_queue_processing
        WHERE operation_queue_id = %s
        LIMIT 1
    """, (queue_id,))
    return cursor.fetchone()


def _create_processing_row(cursor, queue_row):
    cursor.execute("""
        INSERT INTO operation_queue_processing (
            operation_queue_id,
            channel_thread_id,
            patpassport_no,
            conversion_status,
            email_status,
            email_attempt_count
        ) VALUES (%s,%s,%s,'NOT_STARTED','NOT_SENT',0)
    """, (
        queue_row.get("id"),
        queue_row.get("channel_thread_id"),
        queue_row.get("patpassport_no"),
    ))


def _update_processing(cursor, queue_id, updates):
    sets = []
    values = []

    for k, v in updates.items():
        if v == "NOW()":
            sets.append(f"{k} = NOW()")
        elif isinstance(v, str) and v.endswith("+ 1"):
            sets.append(f"{k} = {v}")
        else:
            sets.append(f"{k} = %s")
            values.append(v)

    values.append(queue_id)

    sql = f"""
        UPDATE operation_queue_processing
        SET {", ".join(sets)}
        WHERE operation_queue_id = %s
    """

    cursor.execute(sql, tuple(values))


# ================================
# PRECHECK
# ================================
def precheck_queue_email_conversion(queue_id):
    conn = _oq_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("SELECT * FROM operation_queue_master WHERE id=%s", (queue_id,))
        row = cursor.fetchone()

        if not row:
            return {"success": False, "status": "not_found"}

        if row.get("parse_status") == "FAILED":
            return {"success": False, "status": "parse_failed"}

        processing = _get_processing_row(cursor, queue_id)

        if processing and _safe_str(processing.get("conversion_status")).upper() == "IN_PROGRESS":
            return {"success": False, "status": "already_processing"}

        if processing and _safe_str(processing.get("email_status")).upper() == "SENT":
            return {
                "success": False,
                "status": "already_completed",
                "message": "Email has already been sent for this operation queue request."
            }

        return {"success": True, "status": "ok"}

    finally:
        cursor.close()
        conn.close()

# ================================
# LOCK
# ================================
def claim_conversion_lock(queue_id, request_id, user_code):
    conn = _oq_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("SELECT * FROM operation_queue_master WHERE id=%s FOR UPDATE", (queue_id,))
        queue_row = cursor.fetchone()

        if not queue_row:
            return {
                "success": False,
                "status": "not_found",
                "message": "Operation queue row not found."
            }

        processing = _get_processing_row(cursor, queue_id)
        processing_columns = _get_table_columns(cursor, "operation_queue_processing")

        if not processing:
            _create_processing_row(cursor, queue_row)
            processing = _get_processing_row(cursor, queue_id)
        elif _safe_str(processing.get("conversion_status")).upper() == "IN_PROGRESS":
            return {"success": False, "status": "already_processing", "message": "Email conversion is already in progress."}
        elif _safe_str(processing.get("email_status")).upper() == "SENT":
            return {
                "success": False,
                "status": "already_completed",
                "message": "Email has already been sent for this operation queue request."
            }

        processing_updates = {
            "conversion_status": "IN_PROGRESS",
            "conversion_started_on": "NOW()",
            "conversion_request_id": request_id
        }
        if "sent_intent_remarks" in processing_columns:
            processing_updates["sent_intent_remarks"] = processing.get("sent_intent_remarks") if processing else ""

        _update_processing(cursor, queue_id, processing_updates)

        current_status = _safe_str(queue_row.get("wrkflw_status")).upper()
        next_status = "IN PROGRESS" if current_status in ("", "OPEN", "ASSIGNED", "IN PROGRESS") else current_status

        cursor.execute("""
            UPDATE operation_queue_master
            SET wrkflw_status = %s,
                conversion_status = 'IN_PROGRESS',
                updated_on = NOW()
            WHERE id = %s
        """, (next_status, queue_id))

        if current_status != next_status:
            _close_active_sla_logs_for_thread(
                conn=conn,
                channel_thread_id=queue_row.get("channel_thread_id"),
                source_status=current_status,
                target_status=next_status,
                stop_time=datetime.now(),
                remarks="SLA stopped due to workflow status changed to IN PROGRESS during conversion lock."
            )

        conn.commit()
        return {"success": True, "request_id": request_id}

    except Exception as e:
        conn.rollback()
        print("CLAIM_CONVERSION_LOCK_ERROR:", str(e), flush=True)
        return {"success": False, "status": "failed", "message": str(e)}

    finally:
        cursor.close()
        conn.close()

def get_hospital_city_from_master(cursor, hospital_shrtnm):
    hospital_shrtnm = _safe_str(hospital_shrtnm)

    if not hospital_shrtnm:
        return None

    cursor.execute("""
        SELECT partner_city
        FROM partner_reg
        WHERE UPPER(TRIM(short_name)) = UPPER(TRIM(%s))
          AND UPPER(TRIM(partner_type)) = 'HOSPITAL'
        LIMIT 1
    """, (hospital_shrtnm,))

    row = cursor.fetchone()
    return (row or {}).get("partner_city")


def _derive_patient_workflow_status(queue_row):
    queue_row = queue_row or {}

    request_for = _safe_str(queue_row.get("intent_remarks")).upper()

    if request_for == "VIL" or "VIL" in request_for:
        return "VIL REQUESTED"

    if request_for == "APPOINTMENT" or "APPOINT" in request_for:
        return "APPOINTMENT BOOKED"

    if _normalize_db_date(queue_row.get("appointment_dt")):
        return "APPOINTMENT BOOKED"

    if _normalize_db_date(queue_row.get("vil_date")):
        return "VIL REQUESTED"

    return "LEAD GENERATED"


def _verify_patient_create_record(conn, passport_no):
    """
    Confirm that Patient View / Modify can read the patient from patient_create.

    The Patient View screen is based on patient_create, therefore setting
    patient_created_flag alone is not sufficient. This verification prevents
    the queue from being marked converted when the helper did not actually
    create/update the patient record.
    """
    passport_no = _safe_str(passport_no).upper()
    if not passport_no:
        return {}

    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT
                passport_no,
                patient_country,
                patient_givennm,
                patient_surnm,
                patient_dob,
                patient_gender,
                patient_contno,
                passport_expirydt,
                patient_status,
                patient_email
            FROM patient_create
            WHERE UPPER(TRIM(IFNULL(passport_no, ''))) = %s
            LIMIT 1
            """,
            (passport_no,),
        )
        return cursor.fetchone() or {}
    finally:
        cursor.close()


# ================================
# CONVERSION
# ================================
def convert_queue_to_business_records(queue_id, request_id, user_code):
    conn = _oq_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        row = _fetch_latest_queue_row_by_id(conn, queue_id)

        if not row:
            return {
                "success": False,
                "status": "not_found",
                "message": "Operation queue row not found.",
            }

        passport_no = _safe_str(row.get("patpassport_no")).upper()
        if not passport_no:
            return {
                "success": False,
                "status": "validation_failed",
                "message": "Patient Passport Number is required before patient creation.",
            }

        # ------------------------------------------------------------
        # PATIENT CREATION / UPDATE
        # ------------------------------------------------------------
        # Do not ignore the helper result. Previously the queue row was marked
        # patient_created_flag = 1 even when the helper returned a failure or
        # no patient_create row was available to Patient View / Modify.
        patient_result = insert_patient_if_not_exists(
            conn=conn,
            queue_row=row,
            created_by=_safe_str(user_code) or "SYSTEM",
        )

        if not isinstance(patient_result, dict):
            raise Exception(
                "Patient creation helper returned an invalid response."
            )

        if not patient_result.get("success"):
            raise Exception(
                patient_result.get("message")
                or "Patient record could not be created."
            )

        patient_row = _verify_patient_create_record(conn, passport_no)
        if not patient_row:
            raise Exception(
                "Patient creation helper completed, but no record was found "
                f"in patient_create for passport number {passport_no}."
            )

        cursor.execute(
            """
            UPDATE operation_queue_master
            SET patient_created_flag = 1,
                patient_created_on = COALESCE(patient_created_on, NOW()),
                updated_on = NOW()
            WHERE id = %s
            """,
            (queue_id,),
        )

        hospital_city = _safe_str(row.get("hospital_city"))
        if not hospital_city:
            hospital_city = get_hospital_city_from_master(
                cursor,
                row.get("hospital_shrtnm"),
            )
        hospital_city = hospital_city or "UNKNOWN"

        wrkflw_status = _derive_patient_workflow_status(row)
        validation_result = _validate_workflow_rule_requirements(
            cursor,
            row,
            wrkflw_status,
        )

        if not validation_result.get("success"):
            error_message = validation_result.get(
                "message",
                "Workflow validation failed.",
            )
            _update_processing(
                cursor,
                queue_id,
                {
                    "conversion_status": "FAILED",
                    "conversion_error": error_message,
                },
            )
            cursor.execute(
                """
                UPDATE operation_queue_master
                SET conversion_status = 'FAILED',
                    updated_on = NOW()
                WHERE id = %s
                """,
                (queue_id,),
            )
            conn.commit()
            return {
                "success": False,
                "status": "validation_failed",
                "message": error_message,
                "patient_created": True,
                "patient_action": (
                    "CREATED"
                    if patient_result.get("created")
                    else "UPDATED"
                ),
                "passport_no": passport_no,
            }

        existing_active_row = _get_latest_active_patient_workflow_row(
            conn,
            passport_no,
        )
        workflow_action = "INSERTED"

        if existing_active_row:
            update_result = _update_existing_patient_workflow_row(
                cursor,
                existing_active_row,
                row,
                hospital_city,
                user_code,
                wrkflw_status,
            )
            if not update_result.get("updated"):
                raise Exception(
                    "Existing patient workflow row could not be updated."
                )
            workflow_action = "UPDATED"
        else:
            patient_dob = _normalize_db_date(row.get("patient_dob"))
            patient_age = _calc_age_for_workflow(row.get("patient_dob"))
            patpass_expirydt = _normalize_db_date(
                row.get("patpass_expirydt")
            )
            attn1 = _build_attender_payload(row, "attn1")
            attn2 = _build_attender_payload(row, "attn2")
            attn3 = _build_attender_payload(row, "attn3")
            internal_numbers = _generate_workflow_internal_numbers(
                conn,
                row,
                wrkflw_status,
            )

            cursor.execute(
                """
                INSERT INTO patient_wrkflw (
                    patpassport_no, patgiven_nm, patsur_nm, patient_dob,
                    patient_country, patient_gender, patient_contno,
                    patient_age, patpass_expirydt, patient_address,
                    patient_email,
                    attn1passport_no, attn1pat_relation, attn1_givennm,
                    attn1_surnm, attn1_dob, attn1_country, attn1_gender,
                    attn1_contno, attn1_age, attn1pass_expirydt,
                    attn1_address, attn1_email,
                    attn2passport_no, attn2pat_relation, attn2_givennm,
                    attn2_surnm, attn2_dob, attn2_country, attn2_gender,
                    attn2_contno, attn2_age, attn2pass_expirydt,
                    attn2_address, attn2_email,
                    attn3passport_no, attn3pat_relation, attn3_givennm,
                    attn3_surnm, attn3_dob, attn3_country, attn3_gender,
                    attn3_contno, attn3_age, attn3pass_expirydt,
                    attn3_address, attn3_email,
                    hospital_shrtnm, hospital_city, agent_shrtnm,
                    hospital_dept, ivac_center, doctor_nm,
                    vil_date, appointment_dt,
                    wrkflw_status, ld_seqno, ld_internalno,
                    vil_internalno, entered_by, entered_date
                ) VALUES (
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                    %s,%s,%s,
                    %s,%s,%s,
                    %s,%s,
                    %s,%s,%s,%s,
                    %s,NOW()
                )
                """,
                (
                    passport_no,
                    row.get("patgiven_nm"),
                    row.get("patsur_nm"),
                    patient_dob,
                    row.get("patient_country"),
                    normalize_patient_gender(
                        row.get("patient_gender"),
                        default_if_blank=None,
                    ),
                    row.get("patient_contno"),
                    patient_age,
                    patpass_expirydt,
                    row.get("patient_address"),
                    row.get("patient_email"),
                    attn1["passport_no"],
                    attn1["relation"],
                    attn1["givennm"],
                    attn1["surnm"],
                    attn1["dob"],
                    attn1["country"],
                    attn1["gender"],
                    attn1["contno"],
                    attn1["age"],
                    attn1["pass_expirydt"],
                    attn1["address"],
                    attn1["email"],
                    attn2["passport_no"],
                    attn2["relation"],
                    attn2["givennm"],
                    attn2["surnm"],
                    attn2["dob"],
                    attn2["country"],
                    attn2["gender"],
                    attn2["contno"],
                    attn2["age"],
                    attn2["pass_expirydt"],
                    attn2["address"],
                    attn2["email"],
                    attn3["passport_no"],
                    attn3["relation"],
                    attn3["givennm"],
                    attn3["surnm"],
                    attn3["dob"],
                    attn3["country"],
                    attn3["gender"],
                    attn3["contno"],
                    attn3["age"],
                    attn3["pass_expirydt"],
                    attn3["address"],
                    attn3["email"],
                    row.get("hospital_shrtnm"),
                    hospital_city,
                    row.get("agent_shrtnm"),
                    row.get("hospital_dept"),
                    row.get("ivac_center"),
                    row.get("doctor_nm"),
                    _normalize_db_date(row.get("vil_date")),
                    _normalize_db_date(row.get("appointment_dt")),
                    wrkflw_status,
                    internal_numbers.get("ld_seqno"),
                    internal_numbers.get("ld_internalno"),
                    internal_numbers.get("vil_internalno"),
                    user_code,
                ),
            )

        _update_processing(
            cursor,
            queue_id,
            {
                "conversion_status": "COMPLETED",
                "conversion_completed_on": "NOW()",
                "conversion_error": None,
            },
        )

        cursor.execute(
            """
            UPDATE operation_queue_master
            SET patient_created_flag = 1,
                patient_created_on = COALESCE(patient_created_on, NOW()),
                workflow_created_flag = 1,
                workflow_created_on = NOW(),
                conversion_status = 'COMPLETED',
                wrkflw_status = 'IN PROGRESS',
                updated_on = NOW()
            WHERE id = %s
            """,
            (queue_id,),
        )

        conn.commit()

        return {
            "success": True,
            "status": "completed",
            "patient_created": True,
            "patient_action": (
                "CREATED" if patient_result.get("created") else "UPDATED"
            ),
            "passport_no": passport_no,
            "workflow_action": workflow_action,
            "wrkflw_status": wrkflw_status,
        }

    except Exception as exc:
        conn.rollback()

        error_message = str(exc)
        try:
            processing = _get_processing_row(cursor, queue_id)
            if processing:
                _update_processing(
                    cursor,
                    queue_id,
                    {
                        "conversion_status": "FAILED",
                        "conversion_error": error_message,
                    },
                )

            cursor.execute(
                """
                UPDATE operation_queue_master
                SET conversion_status = 'FAILED',
                    updated_on = NOW()
                WHERE id = %s
                """,
                (queue_id,),
            )
            conn.commit()
        except Exception:
            conn.rollback()

        print(
            "CONVERT_QUEUE_TO_BUSINESS_RECORDS_ERROR:",
            error_message,
            flush=True,
        )
        return {
            "success": False,
            "status": "conversion_failed",
            "message": error_message,
        }

    finally:
        cursor.close()
        conn.close()

def _send_email_smtp(payload, attached_files=None):
    smtp_host = current_app.config.get("MAIL_SERVER")
    smtp_port = int(current_app.config.get("MAIL_PORT", 587))
    username = current_app.config.get("MAIL_USERNAME")
    password = current_app.config.get("MAIL_PASSWORD")

    msg = EmailMessage()
    msg["Subject"] = payload.get("subject")
    msg["From"] = username
    msg["To"] = ", ".join(
        [x.strip() for x in str(payload.get("to", "")).replace(";", ",").split(",") if x.strip()]
    )

    cc_value = ", ".join(
        [x.strip() for x in str(payload.get("cc", "")).replace(";", ",").split(",") if x.strip()]
    )
    bcc_value = ", ".join(
        [x.strip() for x in str(payload.get("bcc", "")).replace(";", ",").split(",") if x.strip()]
    )

    if cc_value:
        msg["Cc"] = cc_value

    if bcc_value:
        msg["Bcc"] = bcc_value

    # Leave one blank line after the TERIQEN body so any server-side
    # Microsoft 365 / Exchange disclaimer starts on a separate line.
    smtp_body = (payload.get("body", "") or "").rstrip("\r\n")
    if smtp_body:
        smtp_body += "\n\n"
    msg.set_content(smtp_body)

    for file_info in attached_files or []:
        file_path = _safe_str((file_info or {}).get("file_path"))
        file_name = _safe_str((file_info or {}).get("file_name")) or os.path.basename(file_path) or "attachment"
        mime_type = _safe_str((file_info or {}).get("mime_type")) or ""

        file_path = _normalize_filesystem_path(file_path)
        if not file_path or not _is_allowed_attachment_file(file_path):
            continue

        file_name = _sanitize_attachment_filename(
            file_name,
            fallback_path=file_path,
        )

        guessed_mime, _ = mimetypes.guess_type(file_path)
        final_mime = mime_type or guessed_mime or "application/octet-stream"

        if "/" in final_mime:
            maintype, subtype = final_mime.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"

        with open(file_path, "rb") as fp:
            msg.add_attachment(
                fp.read(),
                maintype=maintype,
                subtype=subtype,
                filename=file_name,
            )

    with smtplib.SMTP(smtp_host, smtp_port) as server:
        server.starttls()
        server.login(username, password)
        server.send_message(msg)

    return {"success": True}


# ================================
# EMAIL FLOW
# ================================
def send_email_with_conversion(queue_id, request_id, email_payload, user_code):
    conv = convert_queue_to_business_records(queue_id, request_id, user_code)

    if not conv.get("success"):
        return {
            "success": False,
            "status": "conversion_failed",
            "message": conv.get("message", "Conversion failed."),
        }

    conn = _oq_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        processing_columns = _get_table_columns(cursor, "operation_queue_processing")

        sending_updates = {
            "email_status": "SENDING",
            "email_attempt_count": "email_attempt_count + 1"
        }

        if "template_name" in processing_columns:
            sending_updates["template_name"] = _safe_str(email_payload.get("template_name"))

        if "template_code" in processing_columns:
            sending_updates["template_code"] = _safe_str(email_payload.get("template_code"))

        _update_processing(cursor, queue_id, sending_updates)
        conn.commit()

        queue_row = _fetch_latest_queue_row_by_id(conn, queue_id) or {}

        attachment_rows = get_thread_attachments(
            channel_thread_id=queue_row.get("channel_thread_id"),
            operation_queue_id=queue_id,
        )

        selected_attachment_ids = email_payload.get("selected_attachment_ids")

        if selected_attachment_ids is None:
            selected_attachment_ids = []

        selected_id_set = {
            str(x).strip()
            for x in selected_attachment_ids
            if _safe_str(x)
        }

        attachment_rows = [
            row
            for row in attachment_rows
            if str(row.get("id")).strip() in selected_id_set
        ]

        graph_attachment_files, missing_files = _collect_attachment_files(attachment_rows)

        mail_backend = _get_mail_backend()

        if mail_backend == "GRAPH":
            send_result = _send_email_via_graph(
                sender_email=_get_default_sender_email(),
                to_email=email_payload.get("to", ""),
                cc_email=email_payload.get("cc", ""),
                bcc_email=email_payload.get("bcc", ""),
                rendered_subject=email_payload.get("subject", ""),
                rendered_body=email_payload.get("body", ""),
                attached_files=graph_attachment_files,
            )
        else:
            send_result = _send_email_smtp(email_payload, attached_files=graph_attachment_files)

        if send_result.get("success"):
            success_updates = {
                "email_status": "SENT",
                "email_sent_on": "NOW()",
                "conversion_status": "COMPLETED",
                "conversion_completed_on": "NOW()",
                "email_error": None,
                "conversion_error": None,
            }

            if "sent_intent_remarks" in processing_columns:
                success_updates["sent_intent_remarks"] = _safe_str(queue_row.get("intent_remarks"))

            if "template_name" in processing_columns:
                success_updates["template_name"] = _safe_str(email_payload.get("template_name"))

            if "template_code" in processing_columns:
                success_updates["template_code"] = _safe_str(email_payload.get("template_code"))

            _update_processing(cursor, queue_id, success_updates)

            cursor.execute("""
                UPDATE operation_queue_master
                SET converted_flag = 1,
                    converted_on = NOW(),
                    converted_by = %s,
                    conversion_status = 'COMPLETED',
                    updated_on = NOW()
                WHERE id = %s
            """, (user_code, queue_id))

            conn.commit()

            return {
                "success": True,
                "status": "sent",
                "message": "Conversion completed and email sent successfully.",
                "missing_files": missing_files,
                "attachment_count": len(graph_attachment_files),
                "auto_close": True,
                "refresh_parent": True,
                "email_status": "SENT",
                "wrkflw_status": _safe_str(queue_row.get("wrkflw_status")) or "IN PROGRESS",
                "queue_id": queue_id,
                "patient_created": bool(conv.get("patient_created")),
                "patient_action": conv.get("patient_action"),
                "passport_no": conv.get("passport_no"),
                "workflow_action": conv.get("workflow_action"),
            }

        _update_processing(cursor, queue_id, {
            "email_status": "FAILED",
            "email_error": send_result.get("message", "Email sending failed."),
        })

        conn.commit()

        return {
            "success": False,
            "status": "email_failed",
            "message": send_result.get("message", "Email sending failed."),
            "missing_files": missing_files,
            "attachment_count": len(graph_attachment_files),
        }

    except Exception as exc:
        conn.rollback()
        print("🔥 EXCEPTION IN SEND:", str(exc))
        return {
            "success": False,
            "status": "failed",
            "message": str(exc),
        }

    finally:
        cursor.close()
        conn.close()

# ================================
# ENTRY
# ================================
def finalize_email_processing_metadata(queue_id, template_name="", template_code=""):
    conn = None
    cursor = None

    try:
        conn = _oq_db_connection()
        cursor = conn.cursor(dictionary=True)

        queue_row = _fetch_latest_queue_row_by_id(conn, int(queue_id))
        if not queue_row:
            return {"success": False, "message": "Operation queue row not found."}

        processing = _get_processing_row(cursor, int(queue_id))
        if not processing:
            _create_processing_row(cursor, queue_row)
            processing = _get_processing_row(cursor, int(queue_id))

        processing_columns = _get_table_columns(cursor, "operation_queue_processing")
        updates = {
            "email_status": "SENT",
            "email_sent_on": "NOW()",
            "conversion_status": "COMPLETED",
            "conversion_completed_on": "NOW()",
            "email_error": None,
        }

        if "sent_intent_remarks" in processing_columns:
            updates["sent_intent_remarks"] = _safe_str(queue_row.get("intent_remarks"))
        if "template_name" in processing_columns:
            updates["template_name"] = _safe_str(template_name)
        if "template_code" in processing_columns:
            updates["template_code"] = _safe_str(template_code)

        _update_processing(cursor, int(queue_id), updates)

        source_status = _safe_str(queue_row.get("wrkflw_status")).upper()

        cursor.execute("""
            UPDATE operation_queue_master
            SET wrkflw_status = 'IN PROGRESS',
                conversion_status = 'COMPLETED',
                updated_on = NOW()
            WHERE id = %s
              AND UPPER(TRIM(IFNULL(wrkflw_status, ''))) <> 'CLOSED'
        """, (int(queue_id),))

        workflow_status_updated = int(cursor.rowcount or 0) > 0

        if workflow_status_updated and source_status != "IN PROGRESS":
            _close_active_sla_logs_for_thread(
                conn=conn,
                channel_thread_id=queue_row.get("channel_thread_id"),
                source_status=source_status,
                target_status="IN PROGRESS",
                stop_time=datetime.now(),
                remarks="SLA stopped due to workflow status changed to IN PROGRESS."
            )

        conn.commit()

        return {
            "success": True,
            "email_status": "SENT",
            "wrkflw_status": "IN PROGRESS",
            "sent_intent_remarks": _safe_str(queue_row.get("intent_remarks")),
            "template_name": _safe_str(template_name),
            "template_code": _safe_str(template_code),
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


def start_email_conversion_session(queue_id, user_code):
    request_id = str(uuid.uuid4())

    result = claim_conversion_lock(queue_id, request_id, user_code)

    if not result.get("success"):
        return result

    return {
        "success": True,
        "status": "ready",
        "message": "Email communication window can be opened now.",
        "request_id": request_id,
        "open_url": f"/email-communication?queue_id={queue_id}&request_id={request_id}"
    }

# ================================
# PROCESS WITHOUT EMAIL
# ================================
def process_without_email(queue_id, request_id, user_code):
    """
    Convert an Operation Queue record into the existing Patient / Lead /
    Workflow business structure without sending an email.

    This is intentionally separate from send_email_with_conversion()
    so the existing email flow remains unchanged.
    """

    conn = _oq_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        queue_row = _fetch_latest_queue_row_by_id(conn, queue_id)

        if not queue_row:
            return {
                "success": False,
                "status": "not_found",
                "message": "Operation queue row not found.",
            }

        workflow_status = _safe_str(
            queue_row.get("wrkflw_status")
        ).upper()

        if workflow_status == "CLOSED":
            return {
                "success": False,
                "status": "validation_failed",
                "message": (
                    "Process Without Email is disabled because "
                    "the workflow status is CLOSED."
                ),
            }

        converted_flag = _safe_str(
            queue_row.get("converted_flag")
        ).lower()

        if converted_flag in ("1", "true", "yes"):
            return {
                "success": True,
                "status": "already_completed",
                "message": "This case has already been processed.",
                "queue_id": queue_id,
                "refresh_parent": True,
            }

    finally:
        cursor.close()
        conn.close()

    # Use the exact same conversion engine as the existing email flow.
    conversion_result = convert_queue_to_business_records(
        queue_id=queue_id,
        request_id=request_id,
        user_code=user_code,
    )

    if not conversion_result.get("success"):
        return conversion_result

    conn = _oq_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        queue_row = _fetch_latest_queue_row_by_id(conn, queue_id)

        if not queue_row:
            raise Exception(
                "Operation queue row could not be reloaded after conversion."
            )

        cursor.execute(
            """
            UPDATE operation_queue_master
            SET converted_flag = 1,
                converted_on = COALESCE(converted_on, NOW()),
                converted_by = %s,
                conversion_status = 'COMPLETED',
                updated_on = NOW()
            WHERE id = %s
            """,
            (user_code, queue_id),
        )

        conn.commit()

        return {
            "success": True,
            "status": "completed_without_email",
            "message": (
                "Case processed successfully. Patient, Lead and Workflow "
                "processing completed. Email communication was skipped."
            ),
            "queue_id": queue_id,
            "passport_no": conversion_result.get("passport_no"),
            "patient_created": bool(
                conversion_result.get("patient_created")
            ),
            "patient_action": conversion_result.get("patient_action"),
            "workflow_action": conversion_result.get("workflow_action"),
            "wrkflw_status": conversion_result.get("wrkflw_status"),
            "email_skipped": True,
            "refresh_parent": True,
        }

    except Exception as exc:
        conn.rollback()

        return {
            "success": False,
            "status": "failed",
            "message": str(exc),
        }

    finally:
        cursor.close()
        conn.close()
