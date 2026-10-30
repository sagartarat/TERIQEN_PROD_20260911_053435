from flask import has_request_context, request

from app.common.db import get_db_connection
from app.masters.services import (
    fetch_agents,
    fetch_countries,
    fetch_hospitals,
    fetch_ivacs,
)


def _fetch_all_dict(cursor):
    rows = cursor.fetchall()
    if not rows:
        return []
    columns = [col[0] for col in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def _fetch_one_dict(cursor):
    row = cursor.fetchone()
    if not row:
        return None
    columns = [col[0] for col in cursor.description]
    return dict(zip(columns, row))


def get_country_options():
    conn = get_db_connection()
    try:
        return fetch_countries(conn)
    finally:
        conn.close()


def get_hospital_options():
    conn = get_db_connection()
    try:
        return [{"short_name": value} for value in fetch_hospitals(conn)]
    finally:
        conn.close()


def get_ivac_options():
    conn = get_db_connection()
    try:
        return [{"ivac_centre": value} for value in fetch_ivacs(conn)]
    finally:
        conn.close()


def get_agent_options():
    conn = get_db_connection()
    try:
        return [{"short_name": value} for value in fetch_agents(conn)]
    finally:
        conn.close()


def _normalize_checkbox_flag(form, field_name):
    return "Y" if form.get(field_name) == "Y" else "N"


def _normalize_int(value, default=0):
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(value)
    except Exception:
        return default


def _normalize_text(value, default=""):
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def _normalize_upper(value, default=""):
    value = _normalize_text(value, default)
    return value.upper() if value else default


def _safe_rule_id(value):
    try:
        if value is None or str(value).strip() == "":
            return None
        return int(value)
    except Exception:
        return None


def _has_escalation_payload(form):
    escalation_fields = [
        "linked_sla_rule",
        "escalation_level",
        "escalation_basis",
        "trigger_after_value",
        "trigger_after_unit",
        "recipient_type",
        "recipient_value",
        "action_type",
        "notification_channel",
        "escalation_message_template",
    ]
    return any(str(form.get(field, "")).strip() for field in escalation_fields)


def _normalize_priority_status(value):
    value = _normalize_upper(value)
    allowed_values = {"ALL", "HIGH", "MEDIUM", "LOW"}
    return value if value in allowed_values else ""


def _normalize_active_flag(value):
    value = _normalize_upper(value)
    allowed_values = {"ACTIVE", "INACTIVE", "DRAFT"}
    return value if value in allowed_values else "DRAFT"


def _normalize_trigger_event(value):
    value = _normalize_upper(value)
    allowed_values = {"VIL", "APPOINTMENT"}
    return value if value in allowed_values else ""


def _normalize_start_event(value):
    value = _normalize_upper(value)
    allowed_values = {"OPEN", "ASSIGNED", "IN PROGRESS", "CLOSED"}
    return value if value in allowed_values else ""


def _normalize_stop_basis(value):
    value = _normalize_upper(value)
    allowed_values = {"CLOSED", "STATUS CHANGE"}
    return value if value in allowed_values else ""


def _build_default_form_data():
    return {
        "id": "",
        "rule_id": "",
        "rule_code": "",
        "rule_name": "",
        "module_name": "",
        "intent_remarks": "All",
        "priority_status": "",
        "trigger_event": "",
        "hospital_shrtnm": "All",
        "ivac_center": "All",
        "agent_shrtnm": "All",
        "applicable_country": "All",
        "start_event": "",
        "stop_basis": "",
        "target_duration_mins": "",
        "near_breach_mins": "",
        "calendar_type": "",
        "pause_on_hold_flag": "N",
        "resume_on_reopen_flag": "N",
        "auto_close_on_workflow_closed_flag": "N",
        "condition_expression": "",
        "remarks": "",
        "active_flag": "ACTIVE",
        "linked_sla_rule": "",
        "escalation_level": "",
        "escalation_basis": "",
        "trigger_after_value": "",
        "trigger_after_unit": "",
        "recipient_type": "",
        "recipient_value": "",
        "action_type": "",
        "notification_channel": "",
        "escalation_message_template": "",
        "escalation_remarks": "",
    }


def get_sla_rule_by_id(rule_id):
    safe_rule_id = _safe_rule_id(rule_id)
    if not safe_rule_id:
        return None

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
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
            WHERE id = %s
            LIMIT 1
        """, (safe_rule_id,))
        row = _fetch_one_dict(cursor)
        if row:
            row["rule_id"] = row.get("id")
        return row
    finally:
        cursor.close()
        conn.close()


def get_escalation_by_rule_id(rule_id):
    safe_rule_id = _safe_rule_id(rule_id)
    if not safe_rule_id:
        return None

    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
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
                remarks
            FROM sla_rule_escalation
            WHERE sla_rule_id = %s
            ORDER BY escalation_level ASC, id ASC
            LIMIT 1
        """, (safe_rule_id,))
        row = _fetch_one_dict(cursor)
        if row:
            row["linked_sla_rule"] = row.get("sla_rule_id")
            row["escalation_remarks"] = row.get("remarks", "")
        return row
    finally:
        cursor.close()
        conn.close()


def _sync_escalation_row(cursor, form, user_code, rule_id):
    cursor.execute("""
        SELECT id
        FROM sla_rule_escalation
        WHERE sla_rule_id = %s
        ORDER BY escalation_level ASC, id ASC
        LIMIT 1
    """, (rule_id,))
    existing = cursor.fetchone()

    linked_sla_rule = form.get("linked_sla_rule")
    escalation_rule_id = (
        _normalize_int(linked_sla_rule, rule_id)
        if str(linked_sla_rule or "").strip()
        else rule_id
    )

    if existing:
        escalation_id = existing[0]
        cursor.execute("""
            UPDATE sla_rule_escalation
            SET
                sla_rule_id = %s,
                escalation_level = %s,
                escalation_basis = %s,
                trigger_after_value = %s,
                trigger_after_unit = %s,
                recipient_type = %s,
                recipient_value = %s,
                action_type = %s,
                notification_channel = %s,
                escalation_message_template = %s,
                active_flag = %s,
                remarks = %s,
                updated_by = %s,
                updated_on = NOW()
            WHERE id = %s
        """, (
            escalation_rule_id,
            _normalize_int(form.get("escalation_level"), 1),
            _normalize_text(form.get("escalation_basis")),
            _normalize_int(form.get("trigger_after_value"), 0),
            _normalize_text(form.get("trigger_after_unit"), "Minutes"),
            _normalize_text(form.get("recipient_type")),
            _normalize_text(form.get("recipient_value")),
            _normalize_text(form.get("action_type")),
            _normalize_text(form.get("notification_channel")),
            _normalize_text(form.get("escalation_message_template")),
            "ACTIVE",
            _normalize_text(form.get("escalation_remarks")),
            user_code,
            escalation_id
        ))
    else:
        cursor.execute("""
            INSERT INTO sla_rule_escalation (
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
                remarks,
                created_by,
                created_on
            ) VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s, NOW()
            )
        """, (
            escalation_rule_id,
            _normalize_int(form.get("escalation_level"), 1),
            _normalize_text(form.get("escalation_basis")),
            _normalize_int(form.get("trigger_after_value"), 0),
            _normalize_text(form.get("trigger_after_unit"), "Minutes"),
            _normalize_text(form.get("recipient_type")),
            _normalize_text(form.get("recipient_value")),
            _normalize_text(form.get("action_type")),
            _normalize_text(form.get("notification_channel")),
            _normalize_text(form.get("escalation_message_template")),
            "ACTIVE",
            _normalize_text(form.get("escalation_remarks")),
            user_code
        ))


def save_sla_rule(form, user_code="SYSTEM"):
    form_action = _normalize_text(form.get("form_action"), "save").lower()
    rule_id = _safe_rule_id(form.get("rule_id"))

    if form_action == "delete":
        if not rule_id:
            return {"success": False, "error": "Invalid SLA Rule ID for delete."}

        conn = get_db_connection()
        cursor = conn.cursor()
        try:
            cursor.execute("DELETE FROM sla_rule_escalation WHERE sla_rule_id = %s", (rule_id,))
            cursor.execute("DELETE FROM sla_rule_master WHERE id = %s", (rule_id,))
            deleted_count = cursor.rowcount
            conn.commit()
            return {
                "success": True,
                "deleted": deleted_count > 0,
                "sla_rule_id": None,
                "message": "SLA Rule deleted successfully." if deleted_count > 0 else "No SLA Rule found to delete.",
                "action": "delete",
            }
        except Exception as e:
            conn.rollback()
            return {"success": False, "error": str(e)}
        finally:
            cursor.close()
            conn.close()

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        pause_flag = _normalize_checkbox_flag(form, "pause_on_hold_flag")
        resume_flag = _normalize_checkbox_flag(form, "resume_on_reopen_flag")
        auto_close_flag = _normalize_checkbox_flag(form, "auto_close_on_workflow_closed_flag")

        normalized_module_name = _normalize_text(form.get("module_name"))
        normalized_intent_remarks = _normalize_text(form.get("intent_remarks"), "All")
        normalized_priority_status = _normalize_priority_status(form.get("priority_status"))
        normalized_trigger_event = _normalize_trigger_event(form.get("trigger_event"))
        normalized_hospital_shrtnm = _normalize_text(form.get("hospital_shrtnm"), "All")
        normalized_ivac_center = _normalize_text(form.get("ivac_center"), "All")
        normalized_agent_shrtnm = _normalize_text(form.get("agent_shrtnm"), "All")
        normalized_applicable_country = _normalize_text(form.get("applicable_country"), "All")
        normalized_start_event = _normalize_start_event(form.get("start_event"))
        normalized_stop_basis = _normalize_stop_basis(form.get("stop_basis"))
        normalized_calendar_type = _normalize_text(form.get("calendar_type"))
        normalized_condition_expression = _normalize_text(form.get("condition_expression"))
        normalized_remarks = _normalize_text(form.get("remarks"))
        normalized_active_flag = _normalize_active_flag(form.get("active_flag"))

        if form_action == "save_escalation":
            if not rule_id:
                return {"success": False, "error": "Save SLA Rule Master first before saving escalation matrix."}

            if not _has_escalation_payload(form):
                return {"success": False, "error": "Please enter escalation matrix values before saving."}

            _sync_escalation_row(cursor, form, user_code, rule_id)
            conn.commit()
            return {
                "success": True,
                "sla_rule_id": rule_id,
                "message": "Escalation Matrix saved successfully.",
                "action": "save_escalation",
            }

        if form_action == "update":
            if not rule_id:
                return {"success": False, "error": "Invalid SLA Rule ID for update."}

            cursor.execute("""
                UPDATE sla_rule_master
                SET
                    module_name = %s,
                    intent_remarks = %s,
                    priority_status = %s,
                    trigger_event = %s,
                    hospital_shrtnm = %s,
                    ivac_center = %s,
                    agent_shrtnm = %s,
                    applicable_country = %s,
                    start_event = %s,
                    stop_basis = %s,
                    target_duration_mins = %s,
                    near_breach_mins = %s,
                    calendar_type = %s,
                    pause_on_hold_flag = %s,
                    resume_on_reopen_flag = %s,
                    auto_close_on_workflow_closed_flag = %s,
                    condition_expression = %s,
                    remarks = %s,
                    active_flag = %s,
                    updated_by = %s,
                    updated_on = NOW()
                WHERE id = %s
            """, (
                normalized_module_name,
                normalized_intent_remarks,
                normalized_priority_status,
                normalized_trigger_event,
                normalized_hospital_shrtnm,
                normalized_ivac_center,
                normalized_agent_shrtnm,
                normalized_applicable_country,
                normalized_start_event,
                normalized_stop_basis,
                _normalize_int(form.get("target_duration_mins"), 0),
                _normalize_int(form.get("near_breach_mins"), 0),
                normalized_calendar_type,
                pause_flag,
                resume_flag,
                auto_close_flag,
                normalized_condition_expression,
                normalized_remarks,
                normalized_active_flag,
                user_code,
                rule_id
            ))
            conn.commit()
            return {
                "success": True,
                "sla_rule_id": rule_id,
                "message": "SLA Rule updated successfully.",
                "action": "update",
            }

        cursor.execute("""
            INSERT INTO sla_rule_master (
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
                active_flag,
                created_by,
                created_on
            ) VALUES (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, %s, %s,
                %s, %s, %s,
                %s, NOW()
            )
        """, (
            _normalize_text(form.get("rule_code")),
            _normalize_text(form.get("rule_name")),
            normalized_module_name,
            normalized_intent_remarks,
            normalized_priority_status,
            normalized_trigger_event,
            normalized_hospital_shrtnm,
            normalized_ivac_center,
            normalized_agent_shrtnm,
            normalized_applicable_country,
            normalized_start_event,
            normalized_stop_basis,
            _normalize_int(form.get("target_duration_mins"), 0),
            _normalize_int(form.get("near_breach_mins"), 0),
            normalized_calendar_type,
            pause_flag,
            resume_flag,
            auto_close_flag,
            normalized_condition_expression,
            normalized_remarks,
            normalized_active_flag,
            user_code
        ))
        new_sla_rule_id = cursor.lastrowid
        conn.commit()
        return {
            "success": True,
            "sla_rule_id": new_sla_rule_id,
            "message": "SLA Rule saved successfully.",
            "action": "save",
        }

    except Exception as e:
        conn.rollback()
        return {"success": False, "error": str(e)}
    finally:
        cursor.close()
        conn.close()


def get_configured_rules():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT
                id,
                rule_code,
                rule_name,
                module_name,
                trigger_event,
                priority_status,
                start_event,
                target_duration_mins,
                active_flag
            FROM sla_rule_master
            ORDER BY
                CASE
                    WHEN UPPER(COALESCE(active_flag, '')) = 'ACTIVE' THEN 1
                    WHEN UPPER(COALESCE(active_flag, '')) = 'DRAFT' THEN 2
                    ELSE 3
                END,
                rule_code ASC
        """)
        return _fetch_all_dict(cursor)
    finally:
        cursor.close()
        conn.close()


def get_escalation_preview():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT
                e.id,
                e.sla_rule_id,
                e.escalation_level,
                e.escalation_basis,
                e.trigger_after_value,
                e.trigger_after_unit,
                e.action_type,
                e.recipient_type,
                e.recipient_value,
                e.notification_channel,
                e.active_flag,
                m.rule_code,
                m.rule_name
            FROM sla_rule_escalation e
            LEFT JOIN sla_rule_master m
                ON e.sla_rule_id = m.id
            ORDER BY e.sla_rule_id ASC, e.escalation_level ASC
        """)
        return _fetch_all_dict(cursor)
    finally:
        cursor.close()
        conn.close()


def get_runtime_sla_logs():
    conn = get_db_connection()
    cursor = conn.cursor()
    try:
        cursor.execute("""
            SELECT
                channel_thread_id,
                rule_code,
                status,
                elapsed_mins,
                escalated_to
            FROM operation_queue_sla_log
            ORDER BY id DESC
            LIMIT 20
        """)
        return _fetch_all_dict(cursor)
    finally:
        cursor.close()
        conn.close()


def get_sla_summary_counts():
    conn = get_db_connection()
    cursor = conn.cursor()
    summary = {"total_rules": 0, "active_rules": 0, "breached_today": 0, "escalations_triggered": 0}

    try:
        try:
            cursor.execute("SELECT COUNT(*) FROM sla_rule_master")
            row = cursor.fetchone()
            summary["total_rules"] = row[0] if row else 0
        except Exception:
            pass

        try:
            cursor.execute("SELECT COUNT(*) FROM sla_rule_master WHERE UPPER(COALESCE(active_flag, '')) = 'ACTIVE'")
            row = cursor.fetchone()
            summary["active_rules"] = row[0] if row else 0
        except Exception:
            pass

        try:
            cursor.execute("""
                SELECT COUNT(*)
                FROM operation_queue_sla_log
                WHERE UPPER(COALESCE(status, '')) = 'BREACHED'
                  AND DATE(created_on) = CURDATE()
            """)
            row = cursor.fetchone()
            summary["breached_today"] = row[0] if row else 0
        except Exception:
            pass

        try:
            cursor.execute("SELECT COUNT(*) FROM sla_rule_escalation WHERE UPPER(COALESCE(active_flag, '')) = 'ACTIVE'")
            row = cursor.fetchone()
            summary["escalations_triggered"] = row[0] if row else 0
        except Exception:
            pass
    finally:
        cursor.close()
        conn.close()

    return summary


def get_sla_rule_builder_context(form_data=None, selected_rule_id=None, mode=None, save_error=None, success_message=None):
    summary = get_sla_summary_counts()

    if selected_rule_id is None and has_request_context():
        selected_rule_id = _safe_rule_id(request.args.get("rule_id"))
    else:
        selected_rule_id = _safe_rule_id(selected_rule_id)

    if not mode and has_request_context():
        mode = _normalize_text(request.args.get("mode"), "add").lower()

    mode = mode if mode in ("view", "edit", "add") else "add"

    resolved_form_data = _build_default_form_data()
    resolved_rule_id = selected_rule_id

    if selected_rule_id:
        rule_row = get_sla_rule_by_id(selected_rule_id)
        if rule_row:
            resolved_form_data.update(rule_row)
            resolved_form_data["id"] = rule_row.get("id", "")
            resolved_form_data["rule_id"] = rule_row.get("id", "")

        escalation_row = get_escalation_by_rule_id(selected_rule_id)
        if escalation_row:
            resolved_form_data.update(escalation_row)

    if form_data:
        try:
            form_data_dict = form_data.to_dict(flat=True)
        except Exception:
            form_data_dict = dict(form_data)

        resolved_form_data.update(form_data_dict)

        if not resolved_rule_id:
            resolved_rule_id = _safe_rule_id(
                form_data_dict.get("id") or form_data_dict.get("rule_id")
            )

    if resolved_rule_id and not resolved_form_data.get("id"):
        resolved_form_data["id"] = resolved_rule_id
    if resolved_rule_id and not resolved_form_data.get("rule_id"):
        resolved_form_data["rule_id"] = resolved_rule_id
    if resolved_rule_id and not resolved_form_data.get("linked_sla_rule"):
        resolved_form_data["linked_sla_rule"] = resolved_rule_id

    return {
        "form_data": resolved_form_data,
        "country_options": get_country_options(),
        "hospital_options": get_hospital_options(),
        "ivac_options": get_ivac_options(),
        "agent_options": get_agent_options(),
        "configured_rules": get_configured_rules(),
        "escalation_preview": get_escalation_preview(),
        "runtime_sla_logs": get_runtime_sla_logs(),
        "total_rules": summary["total_rules"],
        "active_rules": summary["active_rules"],
        "breached_today": summary["breached_today"],
        "escalations_triggered": summary["escalations_triggered"],
        "selected_rule_id": resolved_rule_id,
        "page_mode": mode,
        "is_view_mode": mode == "view",
        "is_edit_mode": mode == "edit",
        "save_error": save_error,
        "success_message": success_message,
    }
