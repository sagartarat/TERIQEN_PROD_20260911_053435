from flask_login import current_user

from app.broadcast_management.rich_text import sanitize_email_html
from app.common.db import get_db_connection


def _safe_close(cursor=None, conn=None):
    try:
        if cursor is not None:
            cursor.close()
    except Exception:
        pass

    try:
        if conn is not None:
            conn.close()
    except Exception:
        pass


def _get_dict_cursor(conn):
    try:
        return conn.cursor(dictionary=True)
    except TypeError:
        return conn.cursor()


def _normalize_row(row, columns=None):
    if isinstance(row, dict):
        return row

    if columns and isinstance(row, (tuple, list)):
        return {columns[idx]: row[idx] for idx in range(min(len(columns), len(row)))}

    return {}


def _safe_int(value, default=0):
    try:
        return int(value or 0)
    except Exception:
        return default


def _safe_str(value):
    return (value or "").strip()


def _normalize_upper(value, default_value=""):
    cleaned = _safe_str(value)
    return cleaned.upper() if cleaned else default_value


def _normalize_title_status(value, default_value="Draft"):
    raw = _normalize_upper(value, "")
    if raw == "DRAFT":
        return "Draft"
    if raw == "ACTIVE":
        return "Active"
    if raw == "INACTIVE":
        return "Inactive"
    return default_value


def _get_current_username():
    if not current_user or not getattr(current_user, "is_authenticated", False):
        return "SYSTEM"

    for attr in ["username", "user_name", "email", "id"]:
        value = getattr(current_user, attr, None)
        if value is not None and str(value).strip():
            return str(value).strip()

    return "SYSTEM"


def _fetch_placeholder_master_rows():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = _get_dict_cursor(conn)

        query = """
            SELECT
                id,
                placeholder_key,
                placeholder_label,
                source_table,
                source_column,
                module_name,
                data_type,
                sample_value,
                status,
                remarks,
                created_by,
                created_on,
                updated_by,
                updated_on
            FROM email_template_placeholder_master
            WHERE COALESCE(status, 'ACTIVE') = 'ACTIVE'
            ORDER BY
                CASE
                    WHEN module_name = 'Patient' THEN 1
                    WHEN module_name = 'Attender' THEN 2
                    WHEN module_name = 'Workflow' THEN 3
                    ELSE 99
                END,
                placeholder_label,
                id
        """
        cursor.execute(query)
        rows = cursor.fetchall() or []

        if rows and not isinstance(rows[0], dict):
            columns = [desc[0] for desc in (cursor.description or [])]
            rows = [_normalize_row(row, columns) for row in rows]

        return rows

    except Exception as exc:
        print(f"get_email_template_builder_context placeholder fetch error: {exc}")
        return []

    finally:
        _safe_close(cursor, conn)


def _fetch_template_counts():
    conn = None
    cursor = None

    counts = {
        "total_templates": 0,
        "active_templates": 0,
        "draft_templates": 0,
    }

    try:
        conn = get_db_connection()
        cursor = _get_dict_cursor(conn)

        query = """
            SELECT
                COUNT(*) AS total_templates,
                SUM(CASE WHEN UPPER(COALESCE(template_status, '')) = 'ACTIVE' THEN 1 ELSE 0 END) AS active_templates,
                SUM(CASE WHEN UPPER(COALESCE(template_status, '')) = 'DRAFT' THEN 1 ELSE 0 END) AS draft_templates
            FROM email_template_master
        """
        cursor.execute(query)
        row = cursor.fetchone()

        if row and not isinstance(row, dict):
            columns = [desc[0] for desc in (cursor.description or [])]
            row = _normalize_row(row, columns)

        if row:
            counts["total_templates"] = _safe_int(row.get("total_templates"))
            counts["active_templates"] = _safe_int(row.get("active_templates"))
            counts["draft_templates"] = _safe_int(row.get("draft_templates"))

        return counts

    except Exception as exc:
        print(f"get_email_template_builder_context template counts error: {exc}")
        return counts

    finally:
        _safe_close(cursor, conn)


def _fetch_saved_templates():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = _get_dict_cursor(conn)

        query = """
            SELECT
                id,
                template_code,
                template_name,
                template_category,
                template_status,
                template_language,
                template_channel,
                template_description,
                template_subject,
                template_body,
                template_type,
                version_no,
                is_default,
                remarks,
                created_by,
                created_on,
                updated_by,
                updated_on
            FROM email_template_master
            WHERE UPPER(COALESCE(template_status, '')) IN ('DRAFT', 'ACTIVE', 'INACTIVE')
            ORDER BY
                updated_on DESC,
                created_on DESC,
                id DESC
        """
        cursor.execute(query)
        rows = cursor.fetchall() or []

        if rows and not isinstance(rows[0], dict):
            columns = [desc[0] for desc in (cursor.description or [])]
            rows = [_normalize_row(row, columns) for row in rows]

        output = []

        for row in rows:
            output.append({
                "id": row.get("id"),
                "template_code": row.get("template_code", "") or "",
                "template_name": row.get("template_name", "") or "",
                "template_category": row.get("template_category", "") or "",
                "template_status": _normalize_title_status(row.get("template_status"), row.get("template_status", "") or ""),
                "template_language": row.get("template_language", "") or "",
                "template_channel": row.get("template_channel", "") or "",
                "template_description": row.get("template_description", "") or "",
                "template_subject": row.get("template_subject", "") or "",
                "template_body": sanitize_email_html(row.get("template_body", "") or ""),
                "template_type": row.get("template_type", "") or "",
                "version_no": row.get("version_no", "") or "",
                "is_default": row.get("is_default", 0) or 0,
                "remarks": row.get("remarks", "") or "",
                "created_by": row.get("created_by", "") or "",
                "created_on": row.get("created_on", "") or "",
                "updated_by": row.get("updated_by", "") or "",
                "updated_on": row.get("updated_on", "") or "",
            })

        return output

    except Exception as exc:
        print(f"_fetch_saved_templates error: {exc}")
        return []

    finally:
        _safe_close(cursor, conn)


def _group_placeholders(rows):
    group_map = {}

    for row in rows:
        module_name = (row.get("module_name") or "Other").strip()
        group_name = f"{module_name} Details" if module_name.lower() in ("patient", "attender") else module_name

        if group_name not in group_map:
            group_map[group_name] = {
                "group_name": group_name,
                "items": []
            }

        placeholder_key = (row.get("placeholder_key") or "").strip()
        placeholder_label = (row.get("placeholder_label") or placeholder_key).strip()
        source_table = (row.get("source_table") or "").strip()
        source_column = (row.get("source_column") or "").strip()
        data_type = (row.get("data_type") or "TEXT").strip()
        sample_value = row.get("sample_value") or ""
        remarks = row.get("remarks") or ""

        group_map[group_name]["items"].append({
            "placeholder_key": placeholder_key,
            "placeholder_label": placeholder_label,
            "source_table": source_table,
            "source_column": source_column,
            "data_type": data_type,
            "sample_value": sample_value,
            "remarks": remarks,
        })

    ordered_groups = []

    for preferred_name in ["Patient Details", "Attender Details", "Workflow"]:
        if preferred_name in group_map:
            ordered_groups.append(group_map.pop(preferred_name))

    for key in sorted(group_map.keys()):
        ordered_groups.append(group_map[key])

    return ordered_groups


def _build_placeholder_mapping_rows(rows):
    output = []

    for row in rows:
        output.append({
            "placeholder_key": row.get("placeholder_key", "") or "",
            "placeholder_label": row.get("placeholder_label", "") or "",
            "source_table": row.get("source_table", "") or "",
            "source_column": row.get("source_column", "") or "",
            "module_name": row.get("module_name", "") or "",
            "data_type": row.get("data_type", "") or "TEXT",
            "sample_value": row.get("sample_value", "") or "",
            "status": row.get("status", "") or "ACTIVE",
            "remarks": row.get("remarks", "") or "",
        })

    return output


def _build_preview_values(rows):
    preview_values = {}

    for row in rows:
        placeholder_key = (row.get("placeholder_key") or "").strip()
        if not placeholder_key:
            continue

        preview_values[placeholder_key] = row.get("sample_value") or ""

    return preview_values


def validate_email_template_payload(data):
    template_id = _safe_int(data.get("id"), 0)
    is_existing_record = _safe_int(data.get("is_existing_record"), 0)
    original_status = _normalize_upper(data.get("original_status"), "")
    save_action = _safe_str(data.get("save_action")) or "save"

    template_code = _safe_str(data.get("template_code"))
    template_name = _safe_str(data.get("template_name"))
    template_category = _safe_str(data.get("template_category"))
    template_status = _normalize_upper(data.get("template_status"), "ACTIVE")
    template_language = _safe_str(data.get("template_language"))
    template_channel = _normalize_upper(data.get("template_channel"), "EMAIL")
    template_description = _safe_str(data.get("template_description"))
    template_subject = _safe_str(data.get("template_subject"))
    raw_template_body = (data.get("template_body") or "").strip()
    template_body = sanitize_email_html(raw_template_body) if raw_template_body else ""

    version_no = data.get("version_no", 1)
    is_default = data.get("is_default", 0)
    remarks = _safe_str(data.get("remarks"))

    errors = []

    if not template_code:
        errors.append("Template Code is required.")
    if not template_name:
        errors.append("Template Name is required.")
    if not template_category:
        errors.append("Category is required.")
    if not template_status:
        errors.append("Status is required.")
    if not template_language:
        errors.append("Language is required.")
    if not template_channel:
        errors.append("Channel is required.")
    if not template_subject:
        errors.append("Email Subject is required.")
    if not template_body:
        errors.append("Email Body is required.")

    allowed_statuses = {"ACTIVE", "INACTIVE", "DRAFT"}
    if template_status not in allowed_statuses:
        errors.append("Status must be ACTIVE, INACTIVE, or DRAFT.")

    allowed_channels = {"EMAIL", "SMS", "WHATSAPP"}
    if template_channel not in allowed_channels:
        errors.append("Channel must be EMAIL, SMS, or WHATSAPP.")

    try:
        version_no = int(version_no or 1)
    except Exception:
        version_no = 1

    try:
        is_default = int(is_default or 0)
    except Exception:
        is_default = 0

    payload = {
        "id": template_id,
        "is_existing_record": is_existing_record,
        "original_status": original_status,
        "save_action": save_action,
        "template_code": template_code,
        "template_name": template_name,
        "template_category": template_category,
        "template_status": template_status,
        "template_language": template_language,
        "template_channel": template_channel,
        "template_description": template_description,
        "template_subject": template_subject,
        "template_body": template_body,
        "template_type": "EMAIL",
        "version_no": version_no,
        "is_default": is_default,
        "remarks": remarks,
    }

    return payload, errors


def _fetch_template_by_id(cursor, template_id):
    query = """
        SELECT
            id,
            template_code,
            template_name,
            template_category,
            template_status,
            template_language,
            template_channel,
            template_description,
            template_subject,
            template_body,
            template_type,
            version_no,
            is_default,
            remarks,
            created_by,
            created_on,
            updated_by,
            updated_on
        FROM email_template_master
        WHERE id = %s
        LIMIT 1
    """
    cursor.execute(query, (template_id,))
    row = cursor.fetchone()

    if row and not isinstance(row, dict):
        columns = [desc[0] for desc in (cursor.description or [])]
        row = _normalize_row(row, columns)

    return row or None


def _fetch_template_by_code(cursor, template_code):
    query = """
        SELECT
            id,
            template_code,
            template_name,
            template_category,
            template_status,
            template_language,
            template_channel,
            template_description,
            template_subject,
            template_body,
            template_type,
            version_no,
            is_default,
            remarks,
            created_by,
            created_on,
            updated_by,
            updated_on
        FROM email_template_master
        WHERE template_code = %s
        LIMIT 1
    """
    cursor.execute(query, (template_code,))
    row = cursor.fetchone()

    if row and not isinstance(row, dict):
        columns = [desc[0] for desc in (cursor.description or [])]
        row = _normalize_row(row, columns)

    return row or None


def _validate_existing_record_edit_rules(existing_row, payload):
    errors = []

    existing_template_code = _safe_str(existing_row.get("template_code"))
    existing_template_language = _safe_str(existing_row.get("template_language"))
    existing_template_channel = _normalize_upper(existing_row.get("template_channel"), "")
    existing_status = _normalize_upper(existing_row.get("template_status"), "")

    if payload["template_code"] != existing_template_code:
        errors.append("Template Code cannot be modified for existing records.")

    if payload["template_language"] != existing_template_language:
        errors.append("Language cannot be modified for existing records.")

    if payload["template_channel"] != existing_template_channel:
        errors.append("Channel cannot be modified for existing records.")

    if existing_status == "INACTIVE":
        if _safe_str(payload["template_name"]) != _safe_str(existing_row.get("template_name")):
            errors.append("Inactive records allow modification only for Status field.")
        if _safe_str(payload["template_category"]) != _safe_str(existing_row.get("template_category")):
            errors.append("Inactive records allow modification only for Status field.")
        if _safe_str(payload["template_description"]) != _safe_str(existing_row.get("template_description")):
            errors.append("Inactive records allow modification only for Status field.")
        if _safe_str(payload["template_subject"]) != _safe_str(existing_row.get("template_subject")):
            errors.append("Inactive records allow modification only for Status field.")
        if (payload.get("template_body") or "").strip() != ((existing_row.get("template_body") or "").strip()):
            errors.append("Inactive records allow modification only for Status field.")

    return errors


def save_email_template_record(data):
    conn = None
    cursor = None

    try:
        payload, errors = validate_email_template_payload(data)

        if errors:
            return {
                "success": False,
                "message": "Validation failed.",
                "errors": errors
            }, 400

        login_user_name = _get_current_username()

        conn = get_db_connection()
        cursor = _get_dict_cursor(conn)

        existing_row = None

        if payload["id"] > 0:
            existing_row = _fetch_template_by_id(cursor, payload["id"])

            if not existing_row:
                return {
                    "success": False,
                    "message": "Selected template record not found for update."
                }, 404

            additional_errors = _validate_existing_record_edit_rules(existing_row, payload)
            if additional_errors:
                return {
                    "success": False,
                    "message": "Validation failed.",
                    "errors": additional_errors
                }, 400

            update_sql = """
                UPDATE email_template_master
                SET
                    template_name = %s,
                    template_category = %s,
                    template_status = %s,
                    template_description = %s,
                    template_subject = %s,
                    template_body = %s,
                    template_type = %s,
                    is_default = %s,
                    remarks = %s,
                    updated_by = %s
                WHERE id = %s
            """

            update_values = (
                payload["template_name"],
                payload["template_category"],
                payload["template_status"],
                payload["template_description"],
                payload["template_subject"],
                payload["template_body"],
                payload["template_type"],
                payload["is_default"],
                payload["remarks"],
                login_user_name,
                payload["id"],
            )

            cursor.execute(update_sql, update_values)
            conn.commit()

            return {
                "success": True,
                "message": "Email template updated successfully.",
                "template_id": payload["id"],
                "action": "updated"
            }, 200

        duplicate_row = _fetch_template_by_code(cursor, payload["template_code"])
        if duplicate_row:
            return {
                "success": False,
                "message": f"Template Code '{payload['template_code']}' already exists."
            }, 409

        insert_sql = """
            INSERT INTO email_template_master
            (
                template_code,
                template_name,
                template_category,
                template_status,
                template_language,
                template_channel,
                template_description,
                template_subject,
                template_body,
                template_type,
                version_no,
                is_default,
                remarks,
                created_by,
                updated_by
            )
            VALUES
            (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
        """

        insert_values = (
            payload["template_code"],
            payload["template_name"],
            payload["template_category"],
            payload["template_status"],
            payload["template_language"],
            payload["template_channel"],
            payload["template_description"],
            payload["template_subject"],
            payload["template_body"],
            payload["template_type"],
            payload["version_no"],
            payload["is_default"],
            payload["remarks"],
            login_user_name,
            login_user_name,
        )

        cursor.execute(insert_sql, insert_values)
        conn.commit()

        return {
            "success": True,
            "message": "Email template saved successfully.",
            "template_id": getattr(cursor, "lastrowid", None),
            "action": "inserted"
        }, 200

    except Exception as exc:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass

        return {
            "success": False,
            "message": f"Unable to save email template. Error: {str(exc)}"
        }, 500

    finally:
        _safe_close(cursor, conn)



def delete_email_template_record(data):
    """Permanently delete one saved email template after identity checks."""
    template_id = _safe_int((data or {}).get("id"), 0)
    requested_code = _safe_str((data or {}).get("template_code"))

    if template_id <= 0:
        return {
            "success": False,
            "message": "A valid saved template is required for deletion.",
        }, 400

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = _get_dict_cursor(conn)

        existing = _fetch_template_by_id(cursor, template_id)
        if not existing:
            return {
                "success": False,
                "message": "The selected email template no longer exists.",
            }, 404

        stored_code = _safe_str(existing.get("template_code"))
        stored_name = _safe_str(existing.get("template_name"))
        stored_status = _normalize_title_status(
            existing.get("template_status"),
            _safe_str(existing.get("template_status")),
        )

        # Prevent a stale browser row from deleting a different record.
        if requested_code and stored_code and requested_code != stored_code:
            return {
                "success": False,
                "message": "Template identity changed. Refresh the page before deleting.",
            }, 409

        cursor.execute(
            "DELETE FROM email_template_master WHERE id = %s",
            (template_id,),
        )

        if getattr(cursor, "rowcount", 0) != 1:
            try:
                conn.rollback()
            except Exception:
                pass
            return {
                "success": False,
                "message": "Email template was not deleted. Refresh and try again.",
            }, 409

        conn.commit()

        return {
            "success": True,
            "message": f"Email template {stored_code or stored_name} deleted successfully.",
            "template_id": template_id,
            "template_code": stored_code,
            "template_name": stored_name,
            "template_status": stored_status,
        }, 200

    except Exception as exc:
        if conn:
            try:
                conn.rollback()
            except Exception:
                pass

        return {
            "success": False,
            "message": f"Unable to delete email template. Error: {str(exc)}",
        }, 500

    finally:
        _safe_close(cursor, conn)


# EMAIL_TEMPLATE_DELETE_V1:
def get_email_template_builder_context():
    # CONTRACT_EXPIRY_TEMPLATE_V1:
    # Contract Expiry is a dedicated email-template category used by
    # Partner Contract End Date alerts.
    template_categories = [
        "Operation Queue",
        "Appointment",
        "VIL",
        "Clarification",
        "Alert Notification",
        "Contract Expiry",
        "Closure",
        "Follow Up",
        "Invoice",
    ]

    template_statuses = [
        "Draft",
        "Active",
        "Inactive",
    ]

    template_languages = [
        "English",
        "Arabic",
        "French",
    ]

    placeholder_master_rows = _fetch_placeholder_master_rows()
    placeholder_groups = _group_placeholders(placeholder_master_rows)
    placeholder_mapping_rows = _build_placeholder_mapping_rows(placeholder_master_rows)
    preview_values = _build_preview_values(placeholder_master_rows)
    template_counts = _fetch_template_counts()
    saved_templates = _fetch_saved_templates()

    stats = {
        "total_templates": template_counts.get("total_templates", 0),
        "active_templates": template_counts.get("active_templates", 0),
        "draft_templates": template_counts.get("draft_templates", 0),
        "total_placeholders": len(placeholder_mapping_rows),
    }

    template_data = {
        "template_code": "",
        "template_name": "",
        "template_category": "Operation Queue",
        "template_status": "Draft",
        "template_language": "English",
        "template_channel": "EMAIL",
        "template_description": "",
        "template_subject": "",
        "template_body": "",
    }

    return {
        "template_categories": template_categories,
        "template_statuses": template_statuses,
        "template_languages": template_languages,
        "placeholder_groups": placeholder_groups,
        "placeholder_mapping_rows": placeholder_mapping_rows,
        "preview_values": preview_values,
        "stats": stats,
        "template_data": template_data,
        "saved_templates": saved_templates,
        "placeholder_open": "{{",
        "placeholder_close": "}}",
    }