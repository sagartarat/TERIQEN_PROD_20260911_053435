import json
from urllib.parse import quote
from datetime import date, datetime
from decimal import Decimal

from flask import current_app, jsonify, render_template, request
from flask_login import current_user, login_required

from app.common.db import get_db_connection
from app.masters.services import fetch_active_users, fetch_departments, fetch_ivacs
from app.queue_management.conversion_service import (
    process_without_email_service,
    send_with_conversion,
)
from app.queue_management.queue_email_service import (
    get_available_thread_attachments,
)
from app.queue_management.operation_queue_service import (
    build_operation_queue_email,
    create_operation_queue_followup_row,
    create_operation_queue_followup_rows,
    get_active_email_templates,
    get_hospital_email_by_short_name,
    get_operation_queue_grid_data,
    get_operation_queue_record_by_id,
    get_operation_queue_summary_counts,
    start_email_conversion_session,
    finalize_email_processing_metadata,
)


def _json_serializer(obj):
    if isinstance(obj, (datetime, date)):
        return obj.isoformat()
    return str(obj)


def _safe_str(value):
    if value is None:
        return ""
    return str(value).strip()


def _json_safe_value(value):
    if value is None:
        return ""

    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")

    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")

    if isinstance(value, Decimal):
        return float(value)

    return value


def _json_safe_rows(rows):
    safe_rows = []

    for row in rows or []:
        if not isinstance(row, dict):
            safe_rows.append(row)
            continue

        safe_row = {}
        for key, value in row.items():
            safe_row[key] = _json_safe_value(value)

        safe_rows.append(safe_row)

    return safe_rows


def _split_multi_values(value):
    raw = _safe_str(value)
    if not raw:
        return []

    normalized = raw.replace("|", ",").replace(";", ",")
    parts = [item.strip() for item in normalized.split(",") if item and item.strip()]

    seen = set()
    cleaned = []
    for item in parts:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            cleaned.append(item)
    return cleaned


def _normalize_department_csv(value):
    """
    Normalize one or more department values into the existing comma-separated
    storage format without changing the one-request/one-queue-row design.
    """
    normalized = []
    seen = set()

    for item in _split_multi_values(value):
        department = _safe_str(item).upper()
        if not department or department in seen:
            continue
        seen.add(department)
        normalized.append(department)

    return ",".join(normalized)


def _normalize_request_for(value):
    text = _safe_str(value).upper()

    if not text:
        return ""

    if "APPOINT" in text:
        return "APPOINTMENT"

    if "VIL" in text:
        return "VIL"

    return text


def _get_ivac_lov():
    conn = None
    try:
        conn = get_db_connection()
        return fetch_ivacs(conn)
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def _get_department_lov():
    conn = None
    try:
        conn = get_db_connection()
        return fetch_departments(conn)
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def _get_country_lov():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT DISTINCT
                country_name
            FROM country_master
            WHERE country_name IS NOT NULL
              AND TRIM(country_name) <> ''
            ORDER BY country_name
        """)

        return cursor.fetchall() or []

    except Exception as exc:
        print("COUNTRY_LOV_LOAD_ERROR:", str(exc), flush=True)
        return []

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _get_hospital_lov():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT DISTINCT
                short_name
            FROM partner_reg
            WHERE partner_type = 'Hospital'
              AND short_name IS NOT NULL
              AND TRIM(short_name) <> ''
            ORDER BY short_name
        """)

        return cursor.fetchall() or []

    except Exception as exc:
        print("HOSPITAL_LOV_LOAD_ERROR:", str(exc), flush=True)
        return []

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _get_agency_lov():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT DISTINCT short_name
            FROM partner_reg
            WHERE UPPER(TRIM(partner_type)) = 'AGENCY'
              AND short_name IS NOT NULL
              AND TRIM(short_name) <> ''
            ORDER BY short_name
        """)
        return cursor.fetchall() or []
    except Exception as exc:
        print("AGENCY_LOV_LOAD_ERROR:", str(exc), flush=True)
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _canonical_agency(cursor, supplied):
    agency = _safe_str(supplied)
    if not agency:
        return None
    cursor.execute("""
        SELECT short_name FROM partner_reg
        WHERE UPPER(TRIM(partner_type)) = 'AGENCY'
          AND UPPER(TRIM(short_name)) = UPPER(TRIM(%s))
        LIMIT 1
    """, (agency,))
    row = cursor.fetchone()
    return _safe_str(row.get('short_name')) if row else None


def _get_hospital_department_lov():
    conn = None
    cursor = None

    query_options = [
        """
            SELECT DISTINCT
                hospital_shrtnm,
                hospital_dept
            FROM hospdept_master
            WHERE hospital_dept IS NOT NULL
              AND TRIM(hospital_dept) <> ''
            ORDER BY hospital_shrtnm, hospital_dept
        """,
        """
            SELECT DISTINCT
                hospital_short_name AS hospital_shrtnm,
                hospital_dept
            FROM hospdept_master
            WHERE hospital_dept IS NOT NULL
              AND TRIM(hospital_dept) <> ''
            ORDER BY hospital_short_name, hospital_dept
        """,
        """
            SELECT DISTINCT
                hospital_name AS hospital_shrtnm,
                hospital_dept
            FROM hospdept_master
            WHERE hospital_dept IS NOT NULL
              AND TRIM(hospital_dept) <> ''
            ORDER BY hospital_name, hospital_dept
        """,
        """
            SELECT DISTINCT
                '' AS hospital_shrtnm,
                hospital_dept
            FROM hospdept_master
            WHERE hospital_dept IS NOT NULL
              AND TRIM(hospital_dept) <> ''
            ORDER BY hospital_dept
        """,
    ]

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        last_error = None
        for query in query_options:
            try:
                cursor.execute(query)
                return cursor.fetchall() or []
            except Exception as exc:
                last_error = exc

        if last_error:
            print("HOSPITAL_DEPARTMENT_LOV_LOAD_ERROR:", str(last_error), flush=True)

        return []

    except Exception as exc:
        print("HOSPITAL_DEPARTMENT_LOV_CONNECTION_ERROR:", str(exc), flush=True)
        return []

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _get_manual_review_ivac_lov():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT DISTINCT
                ivac_centre
            FROM ivac_master
            WHERE ivac_centre IS NOT NULL
              AND TRIM(ivac_centre) <> ''
            ORDER BY ivac_centre
        """)

        return cursor.fetchall() or []

    except Exception as exc:
        print("MANUAL_REVIEW_IVAC_LOV_LOAD_ERROR:", str(exc), flush=True)
        return []

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _get_user_lov():
    conn = None
    try:
        conn = get_db_connection()
        return fetch_active_users(conn)
    except Exception:
        return []
    finally:
        if conn:
            conn.close()


def _enrich_queue_rows(queue_rows, users_lov):
    user_name_by_code = {
        _safe_str(item.get("user_code")): _safe_str(item.get("user_name"))
        for item in users_lov
        if item.get("user_code")
    }

    enriched_rows = []

    for row in queue_rows or []:
        if not isinstance(row, dict):
            enriched_rows.append(row)
            continue

        enriched = dict(row)

        assigned_to_code = _safe_str(enriched.get("assigned_to"))
        hospital_dept_raw = _safe_str(enriched.get("hospital_dept"))
        ivac_center_raw = _safe_str(enriched.get("ivac_center"))
        intent_raw = _safe_str(enriched.get("intent_remarks"))

        enriched["assigned_to_display"] = (
            user_name_by_code.get(assigned_to_code, assigned_to_code)
            if assigned_to_code
            else ""
        )
        enriched["hospital_dept_values"] = _split_multi_values(hospital_dept_raw)
        enriched["ivac_center_display"] = ivac_center_raw
        enriched["intent_remarks_normalized"] = _normalize_request_for(intent_raw)

        enriched_rows.append(enriched)

    return enriched_rows



def _build_manual_review_file_url(file_path):
    raw_path = _safe_str(file_path)
    if not raw_path:
        return ""

    normalized = raw_path.replace("\\", "/")
    lower_path = normalized.lower()

    private_marker = "private/whatsapp_docs/"
    private_index = lower_path.rfind(private_marker)
    if private_index >= 0:
        relative = normalized[private_index + len(private_marker):].lstrip("/")
        return "/whatsapp/media/" + quote(relative, safe="/:._-() ")

    if lower_path.startswith(("http://", "https://")):
        return normalized

    if lower_path.startswith("/static/"):
        return normalized

    if lower_path.startswith("static/"):
        return "/" + normalized.lstrip("/")

    app_static_marker = "app/static/"
    app_static_index = lower_path.rfind(app_static_marker)
    if app_static_index >= 0:
        return "/static/" + normalized[app_static_index + len(app_static_marker):].lstrip("/")

    uploads_marker = "uploads/"
    uploads_index = lower_path.rfind(uploads_marker)
    if uploads_index >= 0:
        return "/static/" + normalized[uploads_index:].lstrip("/")

    return normalized


def _parse_manual_review_attachment_json(raw_attachment_json):
    if isinstance(raw_attachment_json, list):
        return [
            dict(item)
            for item in raw_attachment_json
            if isinstance(item, dict)
        ]

    if isinstance(raw_attachment_json, str) and raw_attachment_json.strip():
        try:
            parsed = json.loads(raw_attachment_json)
            if isinstance(parsed, list):
                return [
                    dict(item)
                    for item in parsed
                    if isinstance(item, dict)
                ]
        except (TypeError, ValueError, json.JSONDecodeError):
            return []

    return []


def _manual_review_attachment_signature(item):
    item = item or {}

    return (
        _safe_str(
            item.get("file_path")
            or item.get("local_path")
            or item.get("saved_path")
            or item.get("relative_path")
            or item.get("url")
        ),
        _safe_str(
            item.get("media_id")
            or item.get("file_name")
            or item.get("filename")
            or item.get("name")
        ),
        _safe_str(item.get("sha256")),
    )


def _merge_manual_review_attachment_lists(*attachment_lists):
    merged = []
    seen = set()

    for attachment_list in attachment_lists:
        for raw_item in attachment_list or []:
            if not isinstance(raw_item, dict):
                continue

            item = dict(raw_item)
            signature = _manual_review_attachment_signature(item)

            if signature == ("", "", ""):
                signature = ("RAW", json.dumps(item, sort_keys=True, default=_json_serializer), "")

            if signature in seen:
                continue

            seen.add(signature)
            merged.append(item)

    return merged


def _merge_grouped_manual_review_attachments(cursor, queue_rows):
    """
    Merge attachment_json from grouped child messages through linked_triage_id
    into their Manual Review parent rows for display. A child is identified by
    actual attachment metadata rather than its classifier message_type because
    submission-window children may still have a blank/unset message_type.

    Database records remain separate for audit. Only the returned parent-row
    payload is enriched here.
    """
    rows = [dict(row or {}) for row in (queue_rows or [])]
    parent_ids = [
        row.get("message_log_id") or row.get("id")
        for row in rows
        if row.get("message_log_id") or row.get("id")
    ]

    if not parent_ids:
        return rows

    placeholders = ", ".join(["%s"] * len(parent_ids))
    cursor.execute(
        f"""
        SELECT
            id,
            linked_triage_id,
            attachment_count,
            attachment_json,
            received_on
        FROM channel_message_log
        WHERE linked_triage_id IN ({placeholders})
          AND (
                COALESCE(attachment_count, 0) > 0
                OR (
                    attachment_json IS NOT NULL
                    AND TRIM(attachment_json) <> ''
                    AND LOWER(TRIM(attachment_json)) NOT IN ('[]', 'null')
                )
              )
          AND COALESCE(junk_flag, 0) = 0
          AND COALESCE(duplicate_flag, 0) = 0
        ORDER BY linked_triage_id ASC, received_on ASC, id ASC
        """,
        tuple(parent_ids),
    )
    child_rows = cursor.fetchall() or []

    children_by_parent = {}
    for child in child_rows:
        parent_id = child.get("linked_triage_id")
        children_by_parent.setdefault(parent_id, []).append(child)

    for row in rows:
        parent_id = row.get("message_log_id") or row.get("id")
        parent_attachments = _parse_manual_review_attachment_json(
            row.get("attachment_json")
        )

        grouped_attachments = []
        for child in children_by_parent.get(parent_id, []):
            grouped_attachments.extend(
                _parse_manual_review_attachment_json(
                    child.get("attachment_json")
                )
            )

        merged_attachments = _merge_manual_review_attachment_lists(
            parent_attachments,
            grouped_attachments,
        )

        row["attachment_json"] = json.dumps(
            merged_attachments,
            default=_json_serializer,
        )
        row["attachment_count"] = len(merged_attachments)
        row["grouped_attachment_count"] = len(grouped_attachments)

    return rows


def _get_manual_review_merged_attachment_json(cursor, message_log_id):
    """
    Build the final attachment_json used when a Manual Review parent is saved
    back to PENDING. This ensures grouped child attachments continue through
    the existing parser and operation-queue attachment flow.
    """
    cursor.execute(
        """
        SELECT
            id,
            linked_triage_id,
            attachment_json
        FROM channel_message_log
        WHERE id = %s
           OR linked_triage_id = %s
        ORDER BY
            CASE WHEN id = %s THEN 0 ELSE 1 END,
            received_on ASC,
            id ASC
        """,
        (message_log_id, message_log_id, message_log_id),
    )
    rows = cursor.fetchall() or []

    attachment_lists = [
        _parse_manual_review_attachment_json(row.get("attachment_json"))
        for row in rows
    ]
    merged = _merge_manual_review_attachment_lists(*attachment_lists)

    return json.dumps(merged, default=_json_serializer), len(merged)


def _normalize_manual_review_attachments(queue_rows):
    normalized_rows = []

    for source_row in queue_rows or []:
        row = dict(source_row or {})
        raw_attachment_json = row.get("attachment_json")

        if isinstance(raw_attachment_json, list):
            attachments = raw_attachment_json
        elif isinstance(raw_attachment_json, str) and raw_attachment_json.strip():
            try:
                parsed = json.loads(raw_attachment_json)
                attachments = parsed if isinstance(parsed, list) else []
            except (TypeError, ValueError, json.JSONDecodeError):
                attachments = []
        else:
            attachments = []

        normalized_attachments = []
        for raw_item in attachments:
            if not isinstance(raw_item, dict):
                continue

            item = dict(raw_item)
            # Always prefer the locally downloaded Flask-static file.
            # The Meta/WhatsApp URL is temporary and may expire or require
            # authentication when the browser tries to open it.
            file_path = _safe_str(
                item.get("file_path")
                or item.get("local_path")
                or item.get("file_url")
                or item.get("url")
            )
            file_url = _build_manual_review_file_url(file_path)

            item["file_name"] = _safe_str(
                item.get("file_name")
                or item.get("filename")
                or item.get("media_id")
                or "Attachment"
            )
            item["mime_type"] = _safe_str(
                item.get("mime_type")
                or item.get("media_type")
            )
            item["file_path"] = file_path
            item["file_url"] = file_url
            normalized_attachments.append(item)

        row["attachments"] = normalized_attachments
        row["attachment_json"] = json.dumps(
            normalized_attachments,
            default=_json_serializer,
        )
        row["attachment_count"] = len(normalized_attachments)
        normalized_rows.append(row)

    return normalized_rows

def _get_actor_code():
    return _safe_str(
        getattr(current_user, "user_code", None)
        or getattr(current_user, "id", None)
        or getattr(current_user, "username", None)
    )


def _build_action_payload(data):
    data = data or {}

    parse_remarks = (
        data.get("parse_remarks")
        or data.get("operation_remarks")
        or data.get("operational_remarks")
        or data.get("remarks")
        or data.get("operationRemarks")
        or data.get("parseRemarks")
        or ""
    )

    payload = {
        "row_id": _safe_str(
            data.get("row_id")
            or data.get("id")
            or data.get("operation_queue_id")
            or data.get("selected_row_id")
        ),
        "channel_thread_id": _safe_str(data.get("channel_thread_id")),
        "ivac_center": _safe_str(data.get("ivac_center")),
        "hospital_shrtnm": _safe_str(data.get("hospital_shrtnm")),
        "hospital_dept": _normalize_department_csv(data.get("hospital_dept")),
        "doctor_nm": _safe_str(data.get("doctor_nm")),
        "intent_remarks": _safe_str(
            data.get("intent_remarks") or data.get("request_for")
        ),
        "priority_status": _safe_str(data.get("priority_status")),
        "vil_date": _safe_str(data.get("vil_date")),
        "appointment_dt": _safe_str(data.get("appointment_dt")),
        "assigned_to": _safe_str(data.get("assigned_to")),
        "agent_shrtnm": _safe_str(data.get("agent_shrtnm")),
        "parse_remarks": _safe_str(parse_remarks),
    }
    return payload


def _split_email_values(value):
    raw = _safe_str(value)
    if not raw:
        return ""

    normalized = raw.replace(";", ",").replace("|", ",")
    parts = [item.strip() for item in normalized.split(",") if item and item.strip()]
    return ", ".join(parts)


def _extract_selected_attachment_ids(data):
    data = data or {}

    selected_attachment_ids = data.get("selected_attachment_ids")
    if isinstance(selected_attachment_ids, list):
        cleaned = []
        for item in selected_attachment_ids:
            item_value = _safe_str(item)
            if item_value:
                cleaned.append(item_value)
        return cleaned

    thread_attachments = data.get("thread_attachments") or data.get("selected_attachments") or []
    cleaned = []

    if isinstance(thread_attachments, list):
        for item in thread_attachments:
            if not isinstance(item, dict):
                continue

            is_selected = item.get("selected")
            if is_selected in (True, "true", "True", 1, "1", "Y", "y", "YES", "yes"):
                item_id = _safe_str(item.get("id"))
                if item_id:
                    cleaned.append(item_id)

    return cleaned


def _resolve_to_email_for_apply_template(data):
    data = data or {}

    to_email = _safe_str(
        data.get("to_email")
        or data.get("hospital_email")
        or data.get("email_to")
    )
    if to_email:
        return to_email

    hospital_shrtnm = _safe_str(
        data.get("hospital_shrtnm")
        or data.get("hospital_short_name")
        or data.get("hospital_name")
    )
    if hospital_shrtnm:
        hospital_email_result = get_hospital_email_by_short_name(hospital_shrtnm)
        if hospital_email_result.get("success"):
            resolved_email = _safe_str(hospital_email_result.get("hospital_email"))
            if resolved_email:
                return resolved_email

    return ""


def _get_selected_row_by_thread_id(queue_rows, channel_thread_id):
    channel_thread_id = _safe_str(channel_thread_id)
    if not channel_thread_id:
        return queue_rows[0] if queue_rows else {}

    matching_rows = [
        row for row in (queue_rows or [])
        if _safe_str(row.get("channel_thread_id")) == channel_thread_id
    ]

    if not matching_rows:
        return queue_rows[0] if queue_rows else {}

    try:
        matching_rows.sort(key=lambda row: int(row.get("id") or 0), reverse=True)
    except Exception:
        pass

    return matching_rows[0] if matching_rows else (queue_rows[0] if queue_rows else {})



MANUAL_REVIEW_FIELD_GROUPS = [
    ("Patient Details", [
        ("patient_given_name", "Patient Given Name"),
        ("patient_short_name", "Patient Short Name"),
        ("patient_passport_number", "Patient Passport Number"),
        ("patient_passport_expiry_date", "Patient Passport Expiry Date"),
        ("patient_gender", "Patient Gender"),
        ("patient_dob", "Patient Dob"),
        ("patient_age", "Patient Age"),
        ("patient_contact_number", "Patient Contact Number"),
        ("patient_address", "Patient Address"),
        ("patient_email", "Patient Email"),
        ("patient_nationality", "Patient Nationality"),
    ]),
    ("Attender1 Details", [
        ("attender1_given_name", "Attender1 Given Name"),
        ("attender1_short_name", "Attender1 Short Name"),
        ("attender1_passport_number", "Attender1 Passport Number"),
        ("attender1_passport_expiry_date", "Attender1 Passport Expiry Date"),
        ("attender1_gender", "Attender1 Gender"),
        ("attender1_dob", "Attender1 Dob"),
        ("attender1_age", "Attender1 Age"),
        ("attender1_address", "Attender1 Address"),
        ("attender1_nationality", "Attender1 Nationality"),
        ("attender1_contact_number", "Attender1 Contact Number"),
        ("attender1_relationship", "Attender1 Relationship With Patient"),
    ]),
    ("Attender2 Details", [
        ("attender2_given_name", "Attender2 Given Name"),
        ("attender2_short_name", "Attender2 Short Name"),
        ("attender2_passport_number", "Attender2 Passport Number"),
        ("attender2_passport_expiry_date", "Attender2 Passport Expiry Date"),
        ("attender2_gender", "Attender2 Gender"),
        ("attender2_dob", "Attender2 Dob"),
        ("attender2_age", "Attender2 Age"),
        ("attender2_address", "Attender2 Address"),
        ("attender2_nationality", "Attender2 Nationality"),
        ("attender2_contact_number", "Attender2 Contact Number"),
        ("attender2_relationship", "Attender2 Relationship With Patient"),
    ]),
    ("Attender3 Details", [
        ("attender3_given_name", "Attender3 Given Name"),
        ("attender3_short_name", "Attender3 Short Name"),
        ("attender3_passport_number", "Attender3 Passport Number"),
        ("attender3_passport_expiry_date", "Attender3 Passport Expiry Date"),
        ("attender3_gender", "Attender3 Gender"),
        ("attender3_dob", "Attender3 Dob"),
        ("attender3_age", "Attender3 Age"),
        ("attender3_address", "Attender3 Address"),
        ("attender3_nationality", "Attender3 Nationality"),
        ("attender3_contact_number", "Attender3 Contact Number"),
        ("attender3_relationship", "Attender3 Relationship With Patient"),
    ]),
    ("Hospital / Request Details", [
        ("agent_shrtnm", "Agency"),
        ("hospital_name", "Hospital Name"),
        ("department", "Department"),
        ("doctor_name", "Doctor's Name"),
        ("request_nationality", "Nationality"),
        ("embassy", "Embassy"),
        ("intent", "Intent"),
        ("approximate_date", "Approximate Date"),
    ]),
]


def _build_manual_review_structured_text(data):
    data = data or {}
    lines = []

    for group_title, fields in MANUAL_REVIEW_FIELD_GROUPS:
        group_lines = []

        for key, label in fields:
            if key == "department":
                value = _normalize_department_csv(data.get(key))
            else:
                value = _safe_str(data.get(key))

            if value:
                group_lines.append(f"{label}\t{value}")

        if group_lines:
            if lines:
                lines.append("")
            lines.extend(group_lines)

    missing_fields = _safe_str(data.get("missing_fields"))
    reviewer_remarks = _safe_str(data.get("reviewer_remarks"))

    if missing_fields:
        if lines:
            lines.append("")
        lines.append(f"Manual Review Notes\t{missing_fields}")

    if reviewer_remarks:
        if lines:
            lines.append("")
        lines.append(f"Reviewer Remarks\t{reviewer_remarks}")

    return "\n".join(lines)


def _build_manual_review_remarks(data):
    remarks = []
    review_status = _safe_str(data.get("review_status"))
    missing_fields = _safe_str(data.get("missing_fields"))
    reviewer_remarks = _safe_str(data.get("reviewer_remarks"))

    if review_status:
        remarks.append(f"Review Status: {review_status}")

    if missing_fields:
        remarks.append(f"Missing / Review Notes: {missing_fields}")

    if reviewer_remarks:
        remarks.append(f"Reviewer Remarks: {reviewer_remarks}")

    remarks.append("Manual review updated. Parser reset to PENDING.")

    return " | ".join(remarks)

def register_queue_management_routes(app):
    @app.route("/operation-queue")
    @login_required
    def operation_queue():
        requested_channel_thread_id = _safe_str(request.args.get("channel_thread_id"))

        queue_rows = get_operation_queue_grid_data()
        summary = get_operation_queue_summary_counts()
        ivac_lov = _get_ivac_lov()
        department_lov = _get_department_lov()
        users_lov = _get_user_lov()
        agency_lov = _get_agency_lov()
        email_templates_lov = get_active_email_templates()

        request_for_lov = ["VIL", "APPOINTMENT"]

        queue_rows = _enrich_queue_rows(queue_rows, users_lov)
        queue_rows = _json_safe_rows(queue_rows)
        email_templates_lov = _json_safe_rows(email_templates_lov)

        selected_row = _get_selected_row_by_thread_id(
            queue_rows=queue_rows,
            channel_thread_id=requested_channel_thread_id,
        )

        return render_template(
            "operationqueue.html",
            user=current_user,
            queue_rows=queue_rows,
            queue_rows_json=json.dumps(queue_rows, default=_json_serializer),
            selected_row=selected_row,
            selected_row_json=json.dumps(selected_row, default=_json_serializer),
            summary=summary,
            ivac_lov=ivac_lov,
            department_lov=department_lov,
            users_lov=users_lov,
            agency_lov=agency_lov,
            request_for_lov=request_for_lov,
            email_templates_lov=email_templates_lov,
            requested_channel_thread_id=requested_channel_thread_id,
        )

    @app.route("/api/operation-queue/hospital-email", methods=["GET"])
    @login_required
    def operation_queue_hospital_email():
        hospital_shrtnm = _safe_str(request.args.get("hospital_shrtnm"))
        result = get_hospital_email_by_short_name(hospital_shrtnm)
        return jsonify(result), 200

    @app.route("/operation-queue/save", methods=["POST"])
    @login_required
    def operation_queue_save():
        data = request.get_json(silent=True) or {}
        payload = _build_action_payload(data)

        print(
            "OPQ_ASSIGN_DEBUG ROUTE",
            "raw_assigned_to=", repr(data.get("assigned_to")),
            "payload_assigned_to=", repr(payload.get("assigned_to")),
            "actor_code=", repr(_get_actor_code()),
            flush=True,
        )

        result = create_operation_queue_followup_row(
            payload=payload,
            target_status="IN PROGRESS",
            actor_code=_get_actor_code(),
        )
        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code

    @app.route("/operation-queue/resolve", methods=["POST"])
    @login_required
    def operation_queue_resolve():
        data = request.get_json(silent=True) or {}
        payload = _build_action_payload(data)

        result = create_operation_queue_followup_row(
            payload=payload,
            target_status="CLOSED",
            actor_code=_get_actor_code(),
        )
        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code

    @app.route("/operation-queue/save-selected", methods=["POST"])
    @login_required
    def operation_queue_save_selected():
        data = request.get_json(silent=True) or {}
        rows = data.get("rows") or []

        payload_rows = []
        for item in rows:
            if isinstance(item, dict):
                payload_rows.append(_build_action_payload(item))

        result = create_operation_queue_followup_rows(
            payload_rows=payload_rows,
            target_status="IN PROGRESS",
            actor_code=_get_actor_code(),
        )
        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code

    @app.route("/operation-queue/resolve-selected", methods=["POST"])
    @login_required
    def operation_queue_resolve_selected():
        data = request.get_json(silent=True) or {}
        rows = data.get("rows") or []

        payload_rows = []
        for item in rows:
            if isinstance(item, dict):
                payload_rows.append(_build_action_payload(item))

        result = create_operation_queue_followup_rows(
            payload_rows=payload_rows,
            target_status="CLOSED",
            actor_code=_get_actor_code(),
        )
        status_code = 200 if result.get("success") else 400
        return jsonify(result), status_code

    @app.route("/api/operation-queue/apply-email-template", methods=["POST"])
    @login_required
    def operation_queue_apply_email_template():
        data = request.get_json(silent=True) or request.form.to_dict(flat=True) or {}

        operation_queue_id = _safe_str(
            data.get("operation_queue_id")
            or data.get("row_id")
            or data.get("id")
            or data.get("selected_row_id")
        )
        template_name = _safe_str(
            data.get("template_name")
            or data.get("email_template")
            or data.get("email_template_name")
        )
        to_email = _resolve_to_email_for_apply_template(data)
        cc_email = _split_email_values(
            data.get("cc_email")
            or data.get("email_cc")
            or data.get("cc")
        )
        bcc_email = _split_email_values(
            data.get("bcc_email")
            or data.get("email_bcc")
            or data.get("bcc")
        )

        selected_attachment_ids = _extract_selected_attachment_ids(data)

        if not operation_queue_id:
            return jsonify({
                "success": False,
                "message": "Operation queue id is required.",
            }), 400

        if not template_name:
            return jsonify({
                "success": False,
                "message": "Email template is required.",
            }), 400

        if not to_email:
            return jsonify({
                "success": False,
                "message": "To email is required.",
            }), 400

        result = build_operation_queue_email(
            operation_queue_id=operation_queue_id,
            template_name=template_name,
            to_email=to_email,
            cc_email=cc_email,
            bcc_email=bcc_email,
            selected_attachment_ids=selected_attachment_ids,
        )

        if not result.get("success"):
            return jsonify(result), 400

        response = {
            "success": True,
            "message": result.get("message", "Template applied successfully."),
            "rendered_subject": result.get("rendered_subject", ""),
            "rendered_body": result.get("rendered_body", ""),
            "attachment_count": result.get("attachment_count", 0),
            "attached_files": result.get("attached_files", []),
            "missing_files": result.get("missing_files", []),
            "queue_row": _json_safe_rows([result.get("queue_row") or {}])[0],
            "template_row": _json_safe_rows([result.get("template_row") or {}])[0],
            "from_email": _safe_str(result.get("from_email")),
            "to_email": _safe_str(result.get("to_email")),
            "cc_email": _safe_str(result.get("cc_email")),
            "bcc_email": _safe_str(result.get("bcc_email")),
        }
        return jsonify(response), 200

    @app.route("/api/operation-queue/start-email", methods=["POST"])
    @login_required
    def start_operation_queue_email():
        try:
            payload = request.get_json(silent=True) or {}
            queue_id = payload.get("queue_id")

            if not queue_id:
                return jsonify({
                    "success": False,
                    "status": "validation_failed",
                    "message": "queue_id is required."
                }), 400

            user_code = getattr(current_user, "user_code", None) or getattr(current_user, "id", None) or "SYSTEM"
            current_app.logger.info("Operation queue email start requested queue_id=%s user=%s", queue_id, user_code)

            result = start_email_conversion_session(
                queue_id=int(queue_id),
                user_code=str(user_code),
            )

            status_map = {
                "not_found": 404,
                "validation_failed": 400,
                "already_processing": 409,
                "already_completed": 200,
                "already_started": 200,
                "failed": 500,
            }

            status_code = 200 if result.get("success") else status_map.get(result.get("status"), 409)

            return jsonify(result), status_code

        except Exception:
            current_app.logger.exception("Operation queue email start failed queue_id=%s", locals().get("queue_id"))
            return jsonify({
                "success": False,
                "status": "failed",
                "message": "Unable to start email processing.",
            }), 500

    @app.route("/operation-queue/email-communication", methods=["GET"])
    @login_required
    def operation_queue_email_communication():
        queue_id = _safe_str(request.args.get("queue_id"))
        request_id = _safe_str(request.args.get("request_id"))

        if not queue_id:
            return "queue_id is required.", 400

        queue_row = get_operation_queue_record_by_id(queue_id)
        if not queue_row:
            return "Operation queue record not found.", 404

        queue_rows = get_operation_queue_grid_data()
        summary = get_operation_queue_summary_counts()
        ivac_lov = _get_ivac_lov()
        department_lov = _get_department_lov()
        users_lov = _get_user_lov()
        agency_lov = _get_agency_lov()
        email_templates_lov = get_active_email_templates()

        request_for_lov = ["VIL", "APPOINTMENT"]

        queue_rows = _enrich_queue_rows(queue_rows, users_lov)
        queue_rows = _json_safe_rows(queue_rows)
        email_templates_lov = _json_safe_rows(email_templates_lov)
        queue_row = _json_safe_rows([queue_row])[0]

        return render_template(
            "operationqueue.html",
            user=current_user,
            queue_rows=queue_rows,
            queue_rows_json=json.dumps(queue_rows, default=_json_serializer),
            selected_row=queue_row,
            selected_row_json=json.dumps(queue_row, default=_json_serializer),
            summary=summary,
            ivac_lov=ivac_lov,
            department_lov=department_lov,
            users_lov=users_lov,
            agency_lov=agency_lov,
            request_for_lov=request_for_lov,
            email_templates_lov=email_templates_lov,
            request_id=request_id,
            queue_id=queue_id,
        )

    @app.route("/email-communication", methods=["GET"])
    @login_required
    def email_communication_page():
        queue_id = _safe_str(request.args.get("queue_id"))
        request_id = _safe_str(request.args.get("request_id"))

        if not queue_id:
            return "queue_id is required.", 400

        queue_row = get_operation_queue_record_by_id(queue_id)
        if not queue_row:
            return "Operation queue record not found.", 404

        email_templates_lov = get_active_email_templates()
        email_templates_lov = _json_safe_rows(email_templates_lov)
        queue_row = _json_safe_rows([queue_row])[0]

        thread_attachments = get_available_thread_attachments(
            channel_thread_id=queue_row.get("channel_thread_id"),
            operation_queue_id=queue_id,
        )
        thread_attachments = _json_safe_rows(thread_attachments)

        return render_template(
            "email_communication.html",
            user=current_user,
            queue_id=queue_id,
            request_id=request_id,
            email_templates_lov=email_templates_lov,
            queue_row=queue_row,
            thread_attachments=thread_attachments,
        )

    @app.route("/api/operation-queue/send-email", methods=["POST"])
    @login_required
    def operation_queue_send_email():
        try:
            payload = request.get_json(silent=True) or request.form.to_dict()

            queue_id = payload.get("queue_id")
            request_id = payload.get("request_id")

            if not queue_id:
                return jsonify({
                    "success": False,
                    "status": "validation_failed",
                    "message": "queue_id is required."
                }), 400

            if not request_id:
                return jsonify({
                    "success": False,
                    "status": "validation_failed",
                    "message": "request_id is required."
                }), 400

            email_payload = {
                "to": payload.get("to"),
                "cc": payload.get("cc"),
                "bcc": payload.get("bcc"),
                "subject": payload.get("subject"),
                "body": payload.get("body"),
                "from_email": payload.get("from_email"),
                "from_name": payload.get("from_name"),
                "template_name": payload.get("template_name"),
                "template_code": payload.get("template_code"),
                "selected_attachment_ids": payload.get("selected_attachment_ids") or [],
            }

            user_code = getattr(current_user, "user_code", None) or getattr(current_user, "id", None) or "SYSTEM"

            try:
                queue_id_int = int(queue_id)
            except Exception:
                return jsonify({
                    "success": False,
                    "status": "validation_failed",
                    "message": f"Invalid queue_id: {queue_id}"
                }), 400

            current_app.logger.info("Operation queue email send requested queue_id=%s request_id=%s user=%s", queue_id, request_id, user_code)

            result = send_with_conversion(
                queue_id=queue_id_int,
                request_id=str(request_id),
                email_payload=email_payload,
                user_code=str(user_code),
            )

            if result.get("success"):
                return jsonify(result), 200

            status_map = {
                "already_processing": 409,
                "already_completed": 200,
                "conversion_failed": 500,
                "email_failed": 500,
                "not_found": 404,
                "validation_failed": 400,
            }

            http_status = status_map.get(result.get("status"), 500)
            return jsonify(result), http_status

        except Exception:
            current_app.logger.exception(
                "Operation queue email send failed queue_id=%s request_id=%s",
                locals().get("queue_id"),
                locals().get("request_id"),
            )
            return jsonify({
                "success": False,
                "status": "failed",
                "message": "Unable to send email.",
            }), 500


    @app.route(
        "/api/operation-queue/process-without-email",
        methods=["POST"],
    )
    @login_required
    def operation_queue_process_without_email():
        try:
            payload = (
                request.get_json(silent=True)
                or request.form.to_dict()
            )

            queue_id = payload.get("queue_id")
            request_id = payload.get("request_id")

            if not queue_id:
                return jsonify({
                    "success": False,
                    "status": "validation_failed",
                    "message": "queue_id is required.",
                }), 400

            if not request_id:
                return jsonify({
                    "success": False,
                    "status": "validation_failed",
                    "message": "request_id is required.",
                }), 400

            try:
                queue_id_int = int(queue_id)
            except Exception:
                return jsonify({
                    "success": False,
                    "status": "validation_failed",
                    "message": f"Invalid queue_id: {queue_id}",
                }), 400

            user_code = (
                getattr(current_user, "user_code", None)
                or getattr(current_user, "id", None)
                or "SYSTEM"
            )

            current_app.logger.info(
                "Operation queue process without email requested "
                "queue_id=%s request_id=%s user=%s",
                queue_id,
                request_id,
                user_code,
            )

            result = process_without_email_service(
                queue_id=queue_id_int,
                request_id=str(request_id),
                user_code=str(user_code),
            )

            if result.get("success"):
                return jsonify(result), 200

            status_map = {
                "already_completed": 200,
                "not_found": 404,
                "validation_failed": 400,
                "already_processing": 409,
                "conversion_failed": 500,
                "failed": 500,
            }

            http_status = status_map.get(
                result.get("status"),
                500,
            )

            return jsonify(result), http_status

        except Exception:
            current_app.logger.exception(
                "Operation queue process without email failed "
                "queue_id=%s request_id=%s",
                locals().get("queue_id"),
                locals().get("request_id"),
            )

            return jsonify({
                "success": False,
                "status": "failed",
                "message": (
                    "Unable to process the case without email."
                ),
            }), 500


    @app.route("/manual_review")
    @login_required
    def manual_review():
        requested_channel_thread_id = _safe_str(request.args.get("channel_thread_id"))

        conn = None
        cursor = None

        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            cursor.execute("""
                SELECT
                    cml.id AS message_log_id,
                    cml.id,
                    cml.channel_code,
                    cml.channel_message_id,
                    cml.channel_thread_id,
                    cml.sender_id,
                    cml.sender_name,
                    cml.sender_mobile,
                    cml.sender_email,
                    cml.subject_line,
                    cml.raw_message_text,
                    cml.raw_payload_json,
                    cml.attachment_count,
                    cml.attachment_json,
                    cml.received_on,
                    cml.parsed_flag,
                    cml.parse_status,
                    cml.message_type,
                    cml.classifier_status,
                    cml.classifier_reason,
                    cml.thread_resolution_status,
                    cml.requires_manual_review,
                    cml.spam_flag,
                    cml.junk_flag,
                    cml.duplicate_flag,
                    cml.linked_triage_id,
                    cml.linked_queue_id,
                    cml.remarks,
                    cml.created_on,
                    cml.updated_on,

                    'PENDING' AS review_status,
                    IFNULL(oq.assigned_to, '') AS assigned_to,
                    IFNULL(oq.agent_shrtnm, '') AS agent_shrtnm,

                    IFNULL(oq.patpassport_no, '') AS possible_passport,
                    TRIM(CONCAT_WS(' ', oq.patgiven_nm, oq.patsur_nm)) AS possible_patient_name,
                    IFNULL(oq.hospital_shrtnm, '') AS possible_hospital,
                    IFNULL(oq.hospital_dept, '') AS possible_department,
                    IFNULL(oq.doctor_nm, '') AS possible_doctor,
                    IFNULL(oq.intent_remarks, '') AS possible_request_for,
                    IFNULL(oq.parse_remarks, '') AS missing_fields,
                    '' AS reviewer_remarks,
                    '[]' AS possible_matches_json,

                    oq.channel_message_log_id,
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
                    oq.priority_status,
                    oq.wrkflw_status,
                    oq.parse_remarks

                FROM channel_message_log cml
                LEFT JOIN operation_queue_master oq
                    ON oq.id = cml.linked_queue_id
                WHERE cml.parse_status = 'MANUAL_REVIEW'
                  AND cml.linked_triage_id IS NULL
                ORDER BY
                    cml.received_on DESC,
                    cml.id DESC
            """)

            queue_rows = cursor.fetchall() or []
            queue_rows = _merge_grouped_manual_review_attachments(
                cursor=cursor,
                queue_rows=queue_rows,
            )

            print("MANUAL_REVIEW_ROWS_COUNT:", len(queue_rows), flush=True)

        except Exception as exc:
            print("MANUAL_REVIEW_LOAD_ERROR:", str(exc), flush=True)
            queue_rows = []

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

        summary = get_operation_queue_summary_counts()
        ivac_lov = _get_manual_review_ivac_lov()
        department_lov = _get_department_lov()
        country_lov = _get_country_lov()
        hospital_lov = _get_hospital_lov()
        hospital_department_lov = _get_hospital_department_lov()
        users_lov = _get_user_lov()
        agency_lov = _get_agency_lov()
        email_templates_lov = get_active_email_templates()

        request_for_lov = ["VIL", "APPOINTMENT"]

        queue_rows = _enrich_queue_rows(queue_rows, users_lov)
        queue_rows = _normalize_manual_review_attachments(queue_rows)
        queue_rows = _json_safe_rows(queue_rows)
        email_templates_lov = _json_safe_rows(email_templates_lov)

        selected_row = _get_selected_row_by_thread_id(
            queue_rows=queue_rows,
            channel_thread_id=requested_channel_thread_id,
        )

        return render_template(
            "manual_review.html",
            user=current_user,
            queue_rows=queue_rows,
            queue_rows_json=json.dumps(queue_rows, default=_json_serializer),
            selected_row=selected_row,
            selected_row_json=json.dumps(selected_row, default=_json_serializer),
            summary=summary,
            ivac_lov=ivac_lov,
            department_lov=department_lov,
            country_lov=country_lov,
            hospital_lov=hospital_lov,
            hospital_department_lov=hospital_department_lov,
            users_lov=users_lov,
            agency_lov=agency_lov,
            request_for_lov=request_for_lov,
            email_templates_lov=email_templates_lov,
            requested_channel_thread_id=requested_channel_thread_id,
        )


    @app.route("/manual-review/save-review", methods=["POST"])
    @login_required
    def manual_review_save_review():
        data = request.get_json(silent=True) or {}

        message_log_id = _safe_str(
            data.get("message_log_id")
            or data.get("id")
            or data.get("channel_message_log_id")
        )

        if not message_log_id:
            return jsonify({
                "success": False,
                "message": "Message Log ID is required.",
            }), 400

        corrected_message_text = _build_manual_review_structured_text(data)
        if not corrected_message_text:
            return jsonify({
                "success": False,
                "message": "Please enter at least one corrected field before saving.",
            }), 400

        remarks = _build_manual_review_remarks(data)

        conn = None
        cursor = None

        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            cursor.execute("""
                SELECT
                    id,
                    parse_status
                FROM channel_message_log
                WHERE id = %s
                LIMIT 1
                FOR UPDATE
            """, (message_log_id,))

            existing_row = cursor.fetchone()

            if not existing_row:
                return jsonify({
                    "success": False,
                    "message": "Manual review message record not found.",
                }), 404

            if _safe_str(existing_row.get("parse_status")).upper() != "MANUAL_REVIEW":
                return jsonify({"success": False, "message": "This record is no longer pending Manual Review."}), 409

            canonical_agency = _canonical_agency(cursor, data.get("agent_shrtnm"))
            if not canonical_agency:
                return jsonify({"success": False, "message": "Select a valid Agency from Partner Master."}), 400
            data["agent_shrtnm"] = canonical_agency
            corrected_message_text = _build_manual_review_structured_text(data)

            merged_attachment_json, merged_attachment_count = (
                _get_manual_review_merged_attachment_json(
                    cursor=cursor,
                    message_log_id=message_log_id,
                )
            )

            cursor.execute("""
                UPDATE channel_message_log
                SET
                    raw_message_text = %s,
                    attachment_json = %s,
                    attachment_count = %s,
                    parse_status = 'PENDING',
                    parsed_flag = '0',
                    message_type = 'STRUCTURED_REQUEST',
                    classifier_status = 'PENDING',
                    classifier_reason = 'Manual review corrected by user; parser reset to PENDING.',
                    thread_resolution_status = NULL,
                    requires_manual_review = '0',
                    remarks = %s,
                    updated_on = NOW()
                WHERE id = %s
            """, (
                corrected_message_text,
                merged_attachment_json,
                merged_attachment_count,
                remarks,
                message_log_id,
            ))

            conn.commit()

            return jsonify({
                "success": True,
                "message": "Manual review saved successfully. Parse status changed to PENDING and parser will pick this record in the next run.",
                "message_log_id": message_log_id,
                "parse_status": "PENDING",
            }), 200

        except Exception as exc:
            if conn:
                conn.rollback()

            print("MANUAL_REVIEW_SAVE_ERROR:", str(exc), flush=True)

            return jsonify({
                "success": False,
                "message": str(exc),
            }), 500

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()


    @app.route("/manual-review/mark-junk/<int:message_id>", methods=["POST"])
    @login_required
    def mark_manual_review_junk(message_id):

        conn = None
        cursor = None

        try:
            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            cursor.execute("""
                SELECT
                    id,
                    parse_status
                FROM channel_message_log
                WHERE id = %s
                LIMIT 1
            """, (message_id,))

            existing_row = cursor.fetchone()

            if not existing_row:
                return jsonify({
                    "success": False,
                    "message": "Manual review message record not found.",
                }), 404

            cursor.execute("""
                UPDATE channel_message_log
                SET
                    parse_status = 'JUNK',
                    parsed_flag = '0',
                    classifier_status = 'CLASSIFIED',
                    classifier_reason = 'Marked as JUNK from Manual Review screen',
                    junk_flag = '1',
                    requires_manual_review = '0',
                    remarks = 'Message manually marked as JUNK from Manual Review screen.',
                    updated_on = NOW()
                WHERE id = %s
            """, (message_id,))

            conn.commit()

            return jsonify({
                "success": True,
                "message": "Message marked as JUNK successfully.",
                "message_id": message_id,
                "parse_status": "JUNK",
            }), 200

        except Exception as exc:

            if conn:
                conn.rollback()

            print("MANUAL_REVIEW_MARK_JUNK_ERROR:", str(exc), flush=True)

            return jsonify({
                "success": False,
                "message": str(exc),
            }), 500

        finally:
            if cursor:
                cursor.close()

            if conn:
                conn.close()
