import re
import io
import json
import uuid
import datetime
from typing import Any, Dict, Optional

from flask import request, abort, send_file
from mysql.connector.connection import MySQLConnection

from app.common.db import get_db
from app.common.utils import clean, clean_keep_empty, date_to_db, _iso
from app.common.db import get_db_connection
from app.masters.services import fetch_ivacs as fetch_master_ivacs


def _safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_status_name(status_value):
    val = (status_value or "").strip().upper()
    val = re.sub(r"\s+", " ", val)

    # spelling compatibility
    if val == "FULL PAYMENT RECIEVED":
        val = "FULL PAYMENT RECEIVED"
    return val


DEFAULT_WORKFLOW_RULES = {
    "LEAD GENERATED": {
        "status_name": "LEAD GENERATED",
        "allow_edit": 1,
        "vil_date_required": 0,
        "appointment_date_required": 0,
        "patient_workflow_visible": 1,
        "other_checks": "",
    },
    "ESTIMATION REQUESTED": {
        "status_name": "ESTIMATION REQUESTED",
        "allow_edit": 1,
        "vil_date_required": 0,
        "appointment_date_required": 0,
        "patient_workflow_visible": 1,
        "other_checks": "",
    },
    "ESTIMATION OBTAINED": {
        "status_name": "ESTIMATION OBTAINED",
        "allow_edit": 1,
        "vil_date_required": 0,
        "appointment_date_required": 0,
        "patient_workflow_visible": 1,
        "other_checks": "",
    },
    "VIL REQUESTED": {
        "status_name": "VIL REQUESTED",
        "allow_edit": 1,
        "vil_date_required": 1,
        "appointment_date_required": 0,
        "patient_workflow_visible": 1,
        "other_checks": "",
    },
    "VIL SHARED": {
        "status_name": "VIL SHARED",
        "allow_edit": 1,
        "vil_date_required": 0,
        "appointment_date_required": 0,
        "patient_workflow_visible": 1,
        "other_checks": "",
    },
    "VISA IN PROGRESS": {
        "status_name": "VISA IN PROGRESS",
        "allow_edit": 1,
        "vil_date_required": 0,
        "appointment_date_required": 0,
        "patient_workflow_visible": 1,
        "other_checks": "",
    },
    "VISA OBTAINED": {
        "status_name": "VISA OBTAINED",
        "allow_edit": 1,
        "vil_date_required": 0,
        "appointment_date_required": 0,
        "patient_workflow_visible": 1,
        "other_checks": "",
    },
    "VISA REJECTED": {
        "status_name": "VISA REJECTED",
        "allow_edit": 1,
        "vil_date_required": 0,
        "appointment_date_required": 0,
        "patient_workflow_visible": 1,
        "other_checks": "",
    },
    "VISA RESCHEDULE": {
        "status_name": "VISA RESCHEDULE",
        "allow_edit": 1,
        "vil_date_required": 0,
        "appointment_date_required": 0,
        "patient_workflow_visible": 1,
        "other_checks": "",
    },
    "APPOINTMENT BOOKED": {
        "status_name": "APPOINTMENT BOOKED",
        "allow_edit": 1,
        "vil_date_required": 0,
        "appointment_date_required": 1,
        "patient_workflow_visible": 1,
        "other_checks": "",
    },
    "TREATMENT IN PROGRESS": {
        "status_name": "TREATMENT IN PROGRESS",
        "allow_edit": 1,
        "vil_date_required": 0,
        "appointment_date_required": 0,
        "patient_workflow_visible": 1,
        "other_checks": "",
    },
    "TREATMENT COMPLETED": {
        "status_name": "TREATMENT COMPLETED",
        "allow_edit": 1,
        "vil_date_required": 0,
        "appointment_date_required": 0,
        "patient_workflow_visible": 1,
        "other_checks": "",
    },
    "CLOSED": {
        "status_name": "CLOSED",
        "allow_edit": 0,
        "vil_date_required": 0,
        "appointment_date_required": 0,
        "patient_workflow_visible": 1,
        "other_checks": "",
    },
}


NON_EDITABLE_WORKFLOW_STATUSES = {
    "CLOSED",
}


def get_default_workflow_rule(status_name):
    status_norm = normalize_status_name(status_name)
    rule = DEFAULT_WORKFLOW_RULES.get(status_norm)
    if rule:
        return dict(rule)
    return None


def fetch_status_master_rows(conn):
    rows_out = []
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT status_name, color_name
            FROM status_master
            WHERE status_name IS NOT NULL AND TRIM(status_name) <> ''
            ORDER BY status_name ASC
            """
        )
        rows = cur.fetchall() or []
        for row in rows:
            status_name = normalize_status_name(row.get("status_name"))
            if not status_name:
                continue
            rows_out.append({
                "status_name": status_name,
                "color_name": clean_keep_empty(row.get("color_name")) or "Blue",
            })
    except Exception as e:
        print("fetch_status_master_rows warning:", e)
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass
    return rows_out


def fetch_status_color_map(conn):
    status_rows = fetch_status_master_rows(conn)
    color_map = {}
    for row in status_rows:
        color_map[normalize_status_name(row.get("status_name"))] = clean_keep_empty(row.get("color_name")) or "Blue"
    return color_map


def fetch_workflow_status_options_from_status_master(conn):
    """
    Return statuses selectable in Patient Workflow Status Change.

    wrkflwrule_master.patient_workflow_visible is the source of truth.
    status_master remains a display/color master and must not determine
    whether a status is operationally selectable.
    """
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT status_name
            FROM wrkflwrule_master
            WHERE status_name IS NOT NULL
              AND TRIM(status_name) <> ''
              AND COALESCE(patient_workflow_visible, 1) = 1
            ORDER BY status_name ASC
            """
        )

        rows = cur.fetchall() or []
        options = []

        for row in rows:
            status_name = normalize_status_name(row.get("status_name"))
            if not status_name:
                continue

            options.append({
                "value": status_name,
                "label": status_name,
            })

        return options

    except Exception as e:
        print("fetch_workflow_status_options_from_status_master warning:", e)
        return []

    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass


def fetch_workflow_status_master(conn):
    db_rows = []
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT
                status_name,
                allow_edit,
                vil_date_required,
                appointment_date_required,
                other_checks
            FROM wrkflwrule_master
            ORDER BY status_name ASC
            """
        )
        rows = cur.fetchall() or []
        for r in rows:
            status_name = normalize_status_name(r.get("status_name"))
            if not status_name:
                continue
            db_rows.append({
                "status_name": status_name,
                "allow_edit": int(r.get("allow_edit") or 0),
                "vil_date_required": int(r.get("vil_date_required") or 0),
                "appointment_date_required": int(r.get("appointment_date_required") or 0),
                "other_checks": (r.get("other_checks") or "").strip(),
            })
    except Exception as e:
        print("fetch_workflow_status_master warning:", e)
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass

    merged = {}
    for status_name, rule in DEFAULT_WORKFLOW_RULES.items():
        merged[status_name] = dict(rule)

    for row in db_rows:
        merged[row["status_name"]] = dict(row)

    return [merged[k] for k in sorted(merged.keys())]


def fetch_workflow_rule_by_status(conn, status_name):
    status_name = normalize_status_name(status_name)
    if not status_name:
        return None

    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT
                status_name,
                allow_edit,
                vil_date_required,
                appointment_date_required,
                patient_workflow_visible,
                other_checks
            FROM wrkflwrule_master
            WHERE UPPER(TRIM(status_name)) = UPPER(TRIM(%s))
            LIMIT 1
            """,
            (status_name,),
        )
        row = cur.fetchone()
        if row:
            return {
                "status_name": normalize_status_name(row.get("status_name")),
                "allow_edit": int(row.get("allow_edit") or 0),
                "vil_date_required": int(row.get("vil_date_required") or 0),
                "appointment_date_required": int(row.get("appointment_date_required") or 0),
                "patient_workflow_visible": int(
                    row.get("patient_workflow_visible")
                    if row.get("patient_workflow_visible") is not None
                    else 1
                ),
                "other_checks": (row.get("other_checks") or "").strip(),
            }
    except Exception as e:
        print("fetch_workflow_rule_by_status warning:", e)
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass

    return get_default_workflow_rule(status_name)


def fetch_workflow_record_row_by_ld_internalno(conn, ld_internalno):
    ld_internalno = clean(ld_internalno)
    if not ld_internalno:
        return None

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT
                ld_internalno,
                patpassport_no,
                patgiven_nm,
                patsur_nm,
                patient_country,
                hospital_shrtnm,
                hospital_city,
                hospital_dept,
                wrkflw_status,
                patient_dob,

                attn1_givennm,
                attn1_surnm,
                attn1passport_no,
                attn1_dob,
                attn1pat_relation,

                attn2_givennm,
                attn2_surnm,
                attn2passport_no,
                attn2_dob,
                attn2pat_relation,

                attn3_givennm,
                attn3_surnm,
                attn3passport_no,
                attn3_dob,
                attn3pat_relation,

                ivac_center,
                doctor_nm,
                vil_date,
                appointment_dt,
                wrkflw_remarks,
                entered_by,
                entered_date,
                ld_seqno
            FROM patient_wrkflw
            WHERE ld_internalno = %s
            LIMIT 1
            """,
            (ld_internalno,),
        )
        return cur.fetchone()
    finally:
        try:
            cur.close()
        except Exception:
            pass


def fetch_workflow_records_for_status_change(conn):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT
                ld_internalno,
                patpassport_no,
                patgiven_nm,
                patsur_nm,
                patient_country,
                hospital_shrtnm,
                hospital_city,
                hospital_dept,
                wrkflw_status,
                patient_dob,

                attn1_givennm,
                attn1_surnm,
                attn1passport_no,
                attn1_dob,
                attn1pat_relation,

                attn2_givennm,
                attn2_surnm,
                attn2passport_no,
                attn2_dob,
                attn2pat_relation,

                attn3_givennm,
                attn3_surnm,
                attn3passport_no,
                attn3_dob,
                attn3pat_relation,

                ivac_center,
                doctor_nm,
                vil_date,
                appointment_dt,
                wrkflw_remarks,
                entered_by,
                entered_date,
                ld_seqno
            FROM patient_wrkflw
            ORDER BY entered_date DESC, ld_seqno DESC, ld_internalno DESC
            """
        )
        rows = cur.fetchall() or []
        return rows
    finally:
        try:
            cur.close()
        except Exception:
            pass


def build_lead_options_from_rows(rows):
    options = []
    for r in rows:
        ld_internalno = clean_keep_empty(r.get("ld_internalno"))
        passport_no = clean_keep_empty(r.get("patpassport_no"))
        given_name = clean_keep_empty(r.get("patgiven_nm"))
        sur_name = clean_keep_empty(r.get("patsur_nm"))

        display_parts = [x for x in [ld_internalno, passport_no, given_name, sur_name] if x]
        display_text = "-".join(display_parts)

        options.append({
            "id": ld_internalno,
            "display_text": display_text,
        })
    return options


def build_patient_grid_rows_from_workflow_rows(rows):
    out = []
    for r in rows:
        given_name = clean_keep_empty(r.get("patgiven_nm"))
        sur_name = clean_keep_empty(r.get("patsur_nm"))
        patient_name = " ".join([x for x in [given_name, sur_name] if x]).strip()

        out.append({
            "lead_id": clean_keep_empty(r.get("ld_internalno")),
            "id": clean_keep_empty(r.get("ld_internalno")),
            "lead_no": clean_keep_empty(r.get("ld_internalno")),
            "passport_no": clean_keep_empty(r.get("patpassport_no")),
            "patient_name": patient_name,
            "nationality": clean_keep_empty(r.get("patient_country")),
            "hospital_name": clean_keep_empty(r.get("hospital_shrtnm")),
            "city": clean_keep_empty(r.get("hospital_city")),
            "wrkflw_status": clean_keep_empty(r.get("wrkflw_status")),
        })
    return out


def build_lead_view_model(row):
    if not row:
        return None

    given_name = clean_keep_empty(row.get("patgiven_nm"))
    sur_name = clean_keep_empty(row.get("patsur_nm"))
    passport_no = clean_keep_empty(row.get("patpassport_no"))

    return {
        "id": clean_keep_empty(row.get("ld_internalno")),
        "lead_no": clean_keep_empty(row.get("ld_internalno")),
        "passport_no": passport_no,
        "patient_name": " ".join([x for x in [given_name, sur_name] if x]).strip(),
        "nationality": clean_keep_empty(row.get("patient_country")),
        "hospital_name": clean_keep_empty(row.get("hospital_shrtnm")),
        "city": clean_keep_empty(row.get("hospital_city")),
        "hospital_department": clean_keep_empty(row.get("hospital_dept")),
        "wrkflw_status": clean_keep_empty(row.get("wrkflw_status")),
        "doctor_name": clean_keep_empty(row.get("doctor_nm")),
        "ivac": clean_keep_empty(row.get("ivac_center")),
        "vil_date": _iso(row.get("vil_date")),
        "appt_date": _iso(row.get("appointment_dt")),
        "remarks": clean_keep_empty(row.get("wrkflw_remarks")),
    }


def build_patient_view_model(row):
    if not row:
        return None

    return {
        "given_name": clean_keep_empty(row.get("patgiven_nm")),
        "surname": clean_keep_empty(row.get("patsur_nm")),
        "passport_no": clean_keep_empty(row.get("patpassport_no")),
        "dob": _iso(row.get("patient_dob")),
    }


def build_attender_view_model(row, no):
    if not row:
        return None

    given_key = f"attn{no}_givennm"
    sur_key = f"attn{no}_surnm"
    passport_key = f"attn{no}passport_no"
    dob_key = f"attn{no}_dob"
    relation_key = f"attn{no}pat_relation"

    given_name = clean_keep_empty(row.get(given_key))
    sur_name = clean_keep_empty(row.get(sur_key))
    passport_no = clean_keep_empty(row.get(passport_key))
    dob = _iso(row.get(dob_key))
    relationship = clean_keep_empty(row.get(relation_key))

    has_any = any([given_name, sur_name, passport_no, dob, relationship])
    if not has_any:
        return None

    return {
        "given_name": given_name,
        "surname": sur_name,
        "passport_no": passport_no,
        "dob": dob,
        "relationship": relationship,
    }


def fetch_vil_documents_by_passport_no(conn, passport_no):
    passport_no = clean(passport_no)
    if not passport_no:
        return []

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT
                doc_id,
                passport_no,
                file_name,
                file_type,
                file_size,
                uploaded_at
            FROM vilcreate_docs
            WHERE passport_no = %s
            ORDER BY uploaded_at DESC, doc_id DESC
            """,
            (passport_no,),
        )
        return cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass


def validate_workflow_status_change(conn, current_status, next_status, vil_date, appointment_dt):
    current_status_norm = normalize_status_name(current_status)
    next_status_norm = normalize_status_name(next_status)

    if not current_status_norm:
        raise ValueError("Current Status is required.")

    if not next_status_norm:
        raise ValueError("Next Status is required.")

    current_rule = fetch_workflow_rule_by_status(conn, current_status_norm)
    if not current_rule:
        raise ValueError(f"Workflow rule not found for current status: {current_status}")

    if int(current_rule.get("allow_edit") or 0) != 1:
        raise ValueError(f"Modify not allowed for current status: {current_status_norm}")

    next_rule = fetch_workflow_rule_by_status(conn, next_status_norm)
    if not next_rule:
        raise ValueError(f"Workflow rule not found for next status: {next_status}")

    if int(next_rule.get("patient_workflow_visible", 1)) != 1:
        raise ValueError(
            f"Status is not available for Patient Workflow: {next_status_norm}"
        )

    if int(next_rule.get("vil_date_required") or 0) == 1 and not vil_date:
        raise ValueError(f"VIL Date is mandatory for status: {next_status_norm}")

    if int(next_rule.get("appointment_date_required") or 0) == 1 and not appointment_dt:
        raise ValueError(f"Appointment Date is mandatory for status: {next_status_norm}")

    return {
        "current_rule": current_rule,
        "next_rule": next_rule,
    }


def _normalize_compare_text(value):
    if value is None:
        return ""
    return str(value).strip()


def _normalize_compare_status(value):
    return normalize_status_name(value or "")


def _normalize_compare_date(value):
    if value in (None, "", "None"):
        return ""

    if isinstance(value, datetime.datetime):
        return value.date().strftime("%Y-%m-%d")

    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")

    value = str(value).strip()
    if not value:
        return ""

    if len(value) == 10 and value[4] == "-" and value[7] == "-":
        return value

    if len(value) == 10 and value[2] == "-" and value[5] == "-":
        dd, mm, yyyy = value.split("-")
        return f"{yyyy}-{mm}-{dd}"

    try:
        converted = date_to_db(value)
        return converted or ""
    except Exception:
        return value


def _has_workflow_editable_change(existing_row, next_status, vil_date, appointment_dt, ivac_center, wrkflw_remarks):
    old_status = _normalize_compare_status(existing_row.get("wrkflw_status"))
    new_status = _normalize_compare_status(next_status)

    old_vil_date = _normalize_compare_date(existing_row.get("vil_date"))
    new_vil_date = _normalize_compare_date(vil_date)

    old_appointment_dt = _normalize_compare_date(existing_row.get("appointment_dt"))
    new_appointment_dt = _normalize_compare_date(appointment_dt)

    old_ivac_center = _normalize_compare_text(existing_row.get("ivac_center"))
    new_ivac_center = _normalize_compare_text(ivac_center)

    old_wrkflw_remarks = _normalize_compare_text(existing_row.get("wrkflw_remarks"))
    new_wrkflw_remarks = _normalize_compare_text(wrkflw_remarks)

    return any([
        old_status != new_status,
        old_vil_date != new_vil_date,
        old_appointment_dt != new_appointment_dt,
        old_ivac_center != new_ivac_center,
        old_wrkflw_remarks != new_wrkflw_remarks,
    ])


def update_patient_workflow_status(conn, updated_by):
    is_json = request.is_json
    payload = request.get_json(silent=True) if is_json else request.form

    ld_internalno = clean(
        (payload.get("lead_id") if payload else None) or
        (payload.get("lead_no") if payload else None) or
        (payload.get("ld_internalno") if payload else None)
    )

    next_status = clean(
        (payload.get("next_status") if payload else None) or
        (payload.get("workflow_status") if payload else None) or
        (payload.get("wrkflw_status") if payload else None)
    )

    next_status = normalize_status_name(next_status)

    vil_date = date_to_db((payload.get("vil_date") if payload else None))
    appointment_dt = date_to_db(
        (payload.get("appointment_date") if payload else None) or
        (payload.get("appointment_dt") if payload else None)
    )

    wrkflw_remarks = clean(
        (payload.get("remarks") if payload else None) or
        (payload.get("wrkflw_remarks") if payload else None)
    )

    ivac_center = clean(
        (payload.get("ivac") if payload else None) or
        (payload.get("ivac_center") if payload else None)
    )

    if not ld_internalno:
        raise ValueError("Lead Record is required.")

    existing_row = fetch_workflow_record_row_by_ld_internalno(conn, ld_internalno)
    if not existing_row:
        raise ValueError("Workflow record not found.")

    current_status = normalize_status_name(existing_row.get("wrkflw_status"))

    validate_workflow_status_change(
        conn=conn,
        current_status=current_status,
        next_status=next_status,
        vil_date=vil_date,
        appointment_dt=appointment_dt,
    )

    has_any_change = _has_workflow_editable_change(
        existing_row=existing_row,
        next_status=next_status,
        vil_date=vil_date,
        appointment_dt=appointment_dt,
        ivac_center=ivac_center,
        wrkflw_remarks=wrkflw_remarks,
    )

    if not has_any_change:
        raise ValueError(
            "No changes detected. Please amend at least one of these fields: "
            "Workflow Status, VIL Date, Appointment Date, IVAC, or Remarks."
        )

    entered_by = clean(updated_by) or "SYSTEM"
    entered_date = datetime.datetime.now()

    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE patient_wrkflw
            SET
                wrkflw_status = %s,
                vil_date = %s,
                appointment_dt = %s,
                wrkflw_remarks = %s,
                ivac_center = %s,
                entered_by = %s,
                entered_date = %s
            WHERE ld_internalno = %s
            """,
            (
                next_status,
                vil_date,
                appointment_dt,
                wrkflw_remarks,
                ivac_center,
                entered_by,
                entered_date,
                ld_internalno,
            ),
        )

        if cur.rowcount <= 0:
            raise ValueError("No workflow record updated.")
    finally:
        try:
            cur.close()
        except Exception:
            pass

    return {
        "ld_internalno": ld_internalno,
        "wrkflw_status": next_status,
        "entered_by": entered_by,
        "entered_date": entered_date.strftime("%Y-%m-%d %H:%M:%S"),
    }


def fetch_ivacs(conn):
    return fetch_master_ivacs(conn)


def view_vil_doc_file(doc_id):
    conn = None
    cur = None

    try:
        conn = get_db()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT
                doc_id,
                file_name,
                file_type,
                document_data
            FROM vilcreate_docs
            WHERE doc_id = %s
            LIMIT 1
            """,
            (doc_id,),
        )
        doc = cur.fetchone()

        if not doc:
            abort(404)

        file_name = (doc.get("file_name") or f"document_{doc_id}").strip()
        file_type = (doc.get("file_type") or "").strip().lower()
        document_data = doc.get("document_data")

        if not document_data:
            abort(404)

        mime_type = "application/octet-stream"

        if file_type == "pdf":
            mime_type = "application/pdf"
        elif file_type in ["jpg", "jpeg"]:
            mime_type = "image/jpeg"
        elif file_type == "png":
            mime_type = "image/png"
        elif file_type == "gif":
            mime_type = "image/gif"
        elif file_type == "bmp":
            mime_type = "image/bmp"
        elif file_type == "webp":
            mime_type = "image/webp"
        elif file_type == "txt":
            mime_type = "text/plain"
        elif file_type == "doc":
            mime_type = "application/msword"
        elif file_type == "docx":
            mime_type = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
        elif file_type == "xls":
            mime_type = "application/vnd.ms-excel"
        elif file_type == "xlsx":
            mime_type = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        if "." not in file_name and file_type:
            file_name = f"{file_name}.{file_type}"

        return send_file(
            io.BytesIO(document_data),
            mimetype=mime_type,
            as_attachment=False,
            download_name=file_name
        )

    except Exception as e:
        print("view_vil_doc error:", e)
        abort(500)

    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def _safe_json_dumps(data):
    try:
        return json.dumps(data, ensure_ascii=False, default=str)
    except Exception:
        return "{}"


def normalize_whatsapp_stub_payload(payload):
    now = datetime.datetime.now()

    attachments = payload.get("attachments") or []

    return {
        "channel_code": "WHATSAPP",
        "channel_message_id": f"STUB-{uuid.uuid4().hex[:16].upper()}",
        "channel_thread_id": None,
        "sender_id": None,
        "sender_name": (payload.get("sender_name") or "").strip() or "Stub User",
        "sender_mobile": (payload.get("sender_mobile") or "").strip() or None,
        "sender_email": (payload.get("sender_email") or "").strip() or None,
        "subject_line": "WhatsApp Incoming Message",
        "raw_message_text": (payload.get("message_text") or "").strip(),
        "raw_payload_json": _safe_json_dumps(payload),
        "attachment_count": len(attachments),
        "attachment_json": _safe_json_dumps(attachments),
        "received_on": now,
        "parsed_flag": 0,
        "parse_status": "PENDING",
        "spam_flag": 0,
        "junk_flag": 0,
        "duplicate_flag": 0,
        "linked_triage_id": None,
        "linked_queue_id": None,
        "remarks": "Received from WhatsApp Stub",
        "created_on": now,
        "updated_on": now,
    }


def normalize_real_whatsapp_payload(payload):
    now = datetime.datetime.now()

    attachments = payload.get("attachments") or []

    message_text = (
        payload.get("message_text")
        or payload.get("text")
        or payload.get("body")
        or ""
    ).strip()

    channel_message_id = (
        payload.get("channel_message_id")
        or payload.get("message_id")
        or payload.get("id")
        or f"WA-{uuid.uuid4().hex[:16].upper()}"
    )

    return {
        "channel_code": "WHATSAPP",
        "channel_message_id": channel_message_id,
        "channel_thread_id": payload.get("channel_thread_id") or payload.get("thread_id") or payload.get("conversation_id"),
        "sender_id": payload.get("sender_id"),
        "sender_name": (payload.get("sender_name") or "").strip() or None,
        "sender_mobile": (payload.get("sender_mobile") or payload.get("from") or "").strip() or None,
        "sender_email": (payload.get("sender_email") or "").strip() or None,
        "subject_line": "WhatsApp Incoming Message",
        "raw_message_text": message_text,
        "raw_payload_json": _safe_json_dumps(payload),
        "attachment_count": len(attachments),
        "attachment_json": _safe_json_dumps(attachments),
        "received_on": now,
        "parsed_flag": 0,
        "parse_status": "PENDING",
        "spam_flag": 0,
        "junk_flag": 0,
        "duplicate_flag": 0,
        "linked_triage_id": None,
        "linked_queue_id": None,
        "remarks": "Received from Real WhatsApp",
        "created_on": now,
        "updated_on": now,
    }


def insert_channel_message_log(conn, message_data):
    cur = conn.cursor()
    try:
        cur.execute(
            """
            INSERT INTO channel_message_log (
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
                spam_flag,
                junk_flag,
                duplicate_flag,
                linked_triage_id,
                linked_queue_id,
                remarks,
                created_on,
                updated_on
            )
            VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
            """,
            (
                message_data["channel_code"],
                message_data["channel_message_id"],
                message_data["channel_thread_id"],
                message_data["sender_id"],
                message_data["sender_name"],
                message_data["sender_mobile"],
                message_data["sender_email"],
                message_data["subject_line"],
                message_data["raw_message_text"],
                message_data["raw_payload_json"],
                message_data["attachment_count"],
                message_data["attachment_json"],
                message_data["received_on"],
                message_data["parsed_flag"],
                message_data["parse_status"],
                message_data["spam_flag"],
                message_data["junk_flag"],
                message_data["duplicate_flag"],
                message_data["linked_triage_id"],
                message_data["linked_queue_id"],
                message_data["remarks"],
                message_data["created_on"],
                message_data["updated_on"],
            )
        )
        conn.commit()
        return cur.lastrowid
    except Exception:
        conn.rollback()
        raise
    finally:
        try:
            cur.close()
        except Exception:
            pass


# =====================================================
# FIXED WORKFLOW HELPERS (patient_wrkflw BASED)
# =====================================================

def get_latest_workflow_for_passport(
    conn: MySQLConnection,
    passport_no: str,
) -> Optional[Dict[str, Any]]:
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT *
            FROM patient_wrkflw
            WHERE patpassport_no = %s
            ORDER BY entered_date DESC, ld_internalno DESC
            LIMIT 1
            """,
            (passport_no,),
        )
        return cursor.fetchone()
    finally:
        cursor.close()


def check_existing_active_workflow(
    conn: MySQLConnection,
    passport_no: str,
) -> Dict[str, Any]:
    latest = get_latest_workflow_for_passport(conn, passport_no)

    if not latest:
        return {
            "allow_new_workflow": True,
            "latest_workflow": None,
            "message": "No existing workflow found.",
        }

    latest_status = normalize_status_name(
        latest.get("wrkflw_status")
    )

    rule_cursor = conn.cursor(dictionary=True)
    try:
        rule_cursor.execute(
            """
            SELECT allow_new_case_same_passport
            FROM wrkflwrule_master
            WHERE UPPER(TRIM(status_name)) = %s
            LIMIT 1
            """,
            (latest_status,),
        )
        rule_row = rule_cursor.fetchone()
    finally:
        rule_cursor.close()

    allow_new_case = (
        rule_row is not None
        and int(
            rule_row.get(
                "allow_new_case_same_passport"
            ) or 0
        ) == 1
    )

    if allow_new_case:
        return {
            "allow_new_workflow": True,
            "latest_workflow": latest,
            "message": (
                f"Workflow status '{latest_status}' "
                "permits creation of a new case."
            ),
        }

    return {
        "allow_new_workflow": False,
        "latest_workflow": latest,
        "message": (
            f"Active workflow exists: {latest_status}. "
            "Current workflow rule does not permit "
            "creation of a new case."
        ),
    }


def create_workflow_from_queue(
    conn: MySQLConnection,
    queue_row: Dict[str, Any],
    user_code: str,
) -> Dict[str, Any]:

    passport_no = _safe_str(queue_row.get("patpassport_no"))

    check = check_existing_active_workflow(conn, passport_no)

    if not check["allow_new_workflow"]:
        return {
            "success": True,
            "created": False,
            "message": check["message"],
        }

    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO patient_wrkflw (
                patpassport_no,
                patgiven_nm,
                patsur_nm,
                patient_country,
                hospital_shrtnm,
                hospital_city,
                wrkflw_status,
                ivac_center,
                doctor_nm,
                vil_date,
                appointment_dt,
                entered_by,
                entered_date
            )
            VALUES (
                %s,%s,%s,%s,%s,%s,
                'OPEN',
                %s,%s,%s,%s,
                %s,NOW()
            )
            """,
            (
                passport_no,
                queue_row.get("patgiven_nm"),
                queue_row.get("patsur_nm"),
                queue_row.get("patient_country"),
                queue_row.get("hospital_shrtnm"),
                queue_row.get("hospital_city"),
                queue_row.get("ivac_center"),
                queue_row.get("doctor_nm"),
                queue_row.get("vil_date"),
                queue_row.get("appointment_dt"),
                user_code,
            ),
        )

        return {
            "success": True,
            "created": True,
            "message": "Workflow created successfully",
        }

    finally:
        cursor.close()
