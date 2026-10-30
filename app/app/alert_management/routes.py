"""
Alert Management Routes
File: app/alert_management/routes.py

Contains:
1. Alert Dashboard
2. Create Alert Rule
3. View Alert Rule
4. Update Alert Rule
5. Delete Alert Rule
"""

from datetime import datetime, timedelta
from typing import Any

from flask import (
    abort, current_app, flash, jsonify, redirect, render_template,
    request, session, url_for,
)
from flask_login import current_user
from mysql.connector import Error, IntegrityError

from app.alert_management import alert_management_bp
from app.alert_management.alert_dashboard import get_alert_dashboard_context
from app.alert_management.alert_rule_create import (
    create_alert_rule_in_db,
    get_request_data,
    validate_and_normalize,
)
from app.common.db import get_db_connection
from app.common.access_control import enforce_screen_permission


def _require_alert_access():
    endpoint = request.endpoint or ""
    if endpoint in {
        "alert_management.create_alert_rule",
        "alert_management.update_alert_rule",
        "alert_management.delete_alert_rule",
    }:
        action = "delete" if endpoint.endswith("delete_alert_rule") else ("edit" if request.method != "GET" else "view")
        return enforce_screen_permission("CREATE_ALERT_RULE", action)

    action = "edit" if endpoint == "alert_management.update_alert_actions" else "view"
    return enforce_screen_permission("ALERT_DASHBOARD", action)


@alert_management_bp.before_request
def _alert_before_request():
    return _require_alert_access()


def _normalise_module_code(value: Any) -> str:
    """Return the module code used by alert_rule_master.

    screen_master stores display-oriented module names such as
    "Invoice Management", while Alert Rules use the stable code
    "INVOICE". The aliases below keep the Module LOV compatible with
    existing rules during create, view and edit operations.
    """
    text = str(value or "").strip()
    code = "_".join(
        text.upper().replace("-", " ").replace("/", " ").split()
    )

    aliases = {
        "INVOICE_MANAGEMENT": "INVOICE",
        "INVOICE_MODULE": "INVOICE",
    }

    return aliases.get(code, code)


def _current_user_code() -> str:
    return str(
        session.get("user_code")
        or session.get("username")
        or "SYSTEM"
    )


def _close_connection(connection=None, cursor=None) -> None:
    if cursor is not None:
        try:
            cursor.close()
        except Exception:
            pass

    if connection is not None:
        try:
            if not hasattr(connection, "is_connected") or connection.is_connected():
                connection.close()
        except Exception:
            pass


def get_alert_rule_reference_data() -> dict[str, list[dict[str, str]]]:
    connection = None
    data = {"modules": [], "workflow_statuses": [], "users": [], "roles": []}

    try:
        connection = get_db_connection()

        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT DISTINCT TRIM(module_name) AS module_name
                FROM screen_master
                WHERE module_name IS NOT NULL AND TRIM(module_name) <> ''
                ORDER BY TRIM(module_name)
            """)
            data["modules"] = [
                {
                    "module_code": _normalise_module_code(row.get("module_name")),
                    "module_name": str(row.get("module_name") or "").strip(),
                }
                for row in (cursor.fetchall() or [])
                if str(row.get("module_name") or "").strip()
            ]
        except Exception:
            current_app.logger.exception("Unable to load Module LOV.")
        finally:
            cursor.close()

        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT DISTINCT TRIM(status_name) AS status_name
                FROM wrkflwrule_master
                WHERE status_name IS NOT NULL AND TRIM(status_name) <> ''
                ORDER BY TRIM(status_name)
            """)
            data["workflow_statuses"] = [
                {
                    "status_code": str(row.get("status_name") or "").strip(),
                    "status_name": str(row.get("status_name") or "").strip(),
                }
                for row in (cursor.fetchall() or [])
                if str(row.get("status_name") or "").strip()
            ]
        except Exception:
            current_app.logger.exception("Unable to load Workflow Status LOV.")
        finally:
            cursor.close()

        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT user_code, user_name
                FROM user_master
                WHERE user_status = 'Active'
                ORDER BY user_name, user_code
            """)
            data["users"] = cursor.fetchall() or []
        except Exception:
            current_app.logger.warning("Unable to load users.", exc_info=True)
        finally:
            cursor.close()

        cursor = connection.cursor(dictionary=True)
        try:
            cursor.execute("""
                SELECT DISTINCT TRIM(role_code) AS role_code
                FROM user_role_mapping
                WHERE role_code IS NOT NULL AND TRIM(role_code) <> ''
                ORDER BY TRIM(role_code)
            """)
            data["roles"] = [
                {"role_code": row.get("role_code"), "role_name": row.get("role_code")}
                for row in (cursor.fetchall() or [])
            ]
        except Exception:
            current_app.logger.warning("Unable to load roles.", exc_info=True)
        finally:
            cursor.close()

    except Exception:
        current_app.logger.exception("Unable to load Alert Rule reference data.")
    finally:
        _close_connection(connection)

    return data


def get_alert_rules() -> list[dict[str, Any]]:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("""
            SELECT
                id AS rule_id, alert_name, alert_description, module_code,
                trigger_type, workflow_status, ageing_basis,
                warning_after_minutes, warning_unit,
                critical_after_minutes, critical_unit,
                repeat_interval_minutes, repeat_unit, maximum_repeat_count,
                business_hours_only, exclude_weekends, exclude_holidays,
                auto_resolve, active_flag, created_by, created_on,
                updated_by, updated_on
            FROM alert_rule_master
            ORDER BY updated_on DESC, id DESC
        """)
        return cursor.fetchall() or []
    except Exception:
        current_app.logger.exception("Unable to load alert rules.")
        return []
    finally:
        _close_connection(connection, cursor)


def get_alert_rule(rule_id: int) -> tuple[dict[str, Any] | None, list[dict[str, Any]]]:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                id AS rule_id, alert_name, alert_description, module_code,
                trigger_type, workflow_status, ageing_basis,
                warning_after_minutes, warning_unit,
                critical_after_minutes, critical_unit,
                repeat_interval_minutes, repeat_unit, maximum_repeat_count,
                business_hours_only, exclude_weekends, exclude_holidays,
                auto_resolve, active_flag, created_by, created_on,
                updated_by, updated_on
            FROM alert_rule_master
            WHERE id = %s
        """, (rule_id,))
        rule = cursor.fetchone()

        if not rule:
            return None, []

        cursor.execute("""
            SELECT
                id, rule_id, severity, recipient_type, recipient_value,
                in_app_flag, email_flag, whatsapp_flag, sms_flag, active_flag
            FROM alert_rule_recipient
            WHERE rule_id = %s
            ORDER BY id
        """, (rule_id,))
        return rule, cursor.fetchall() or []

    finally:
        _close_connection(connection, cursor)


def render_alert_rule_page(*, form_data=None, status_code=200,
                           selected_rule=None, selected_recipients=None,
                           page_mode="create"):
    reference_data = get_alert_rule_reference_data()
    return (
        render_template(
            "create_alert_rule.html",
            modules=reference_data["modules"],
            workflow_statuses=reference_data["workflow_statuses"],
            users=reference_data["users"],
            roles=reference_data["roles"],
            form_data=form_data or {},
            alert_rules=get_alert_rules(),
            selected_rule=selected_rule,
            selected_recipients=selected_recipients or [],
            page_mode=page_mode,
        ),
        status_code,
    )


def update_alert_rule_in_db(rule_id: int, rule: dict[str, Any],
                            recipients: list[dict[str, Any]],
                            user_code: str) -> None:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute("""
            UPDATE alert_rule_master
            SET alert_name=%s, alert_description=%s, module_code=%s,
                trigger_type=%s, workflow_status=%s, ageing_basis=%s,
                warning_after_minutes=%s, warning_unit=%s,
                critical_after_minutes=%s, critical_unit=%s,
                repeat_interval_minutes=%s, repeat_unit=%s,
                maximum_repeat_count=%s,
                business_hours_only=%s, exclude_weekends=%s,
                exclude_holidays=%s, auto_resolve=%s, active_flag=%s,
                updated_by=%s, updated_on=NOW()
            WHERE id=%s
        """, (
            rule["alert_name"], rule["alert_description"], rule["module_code"],
            rule["trigger_type"], rule["workflow_status"], rule["ageing_basis"],
            rule["warning_after_minutes"], rule["warning_unit"],
            rule["critical_after_minutes"], rule["critical_unit"],
            rule["repeat_interval_minutes"], rule["repeat_unit"],
            rule["maximum_repeat_count"],
            rule["business_hours_only"], rule["exclude_weekends"],
            rule["exclude_holidays"], rule["auto_resolve"],
            rule["active_flag"], user_code, rule_id,
        ))

        if cursor.rowcount == 0:
            raise ValueError("Alert rule not found.")

        cursor.execute("DELETE FROM alert_rule_recipient WHERE rule_id=%s", (rule_id,))

        cursor.executemany("""
            INSERT INTO alert_rule_recipient
            (
                rule_id, severity, recipient_type, recipient_value,
                in_app_flag, email_flag, whatsapp_flag, sms_flag,
                active_flag, created_by, created_on, updated_by, updated_on
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,NOW(),%s,NOW())
        """, [
            (
                rule_id, recipient["severity"], recipient["recipient_type"],
                recipient["recipient_value"], recipient["in_app_flag"],
                recipient["email_flag"], recipient["whatsapp_flag"],
                recipient["sms_flag"], recipient["active_flag"],
                user_code, user_code,
            )
            for recipient in recipients
        ])

        connection.commit()

    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        _close_connection(connection, cursor)


def delete_alert_rule_in_db(rule_id: int) -> None:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("DELETE FROM alert_rule_recipient WHERE rule_id=%s", (rule_id,))
        cursor.execute("DELETE FROM alert_rule_master WHERE id=%s", (rule_id,))
        if cursor.rowcount == 0:
            raise ValueError("Alert rule not found.")
        connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        _close_connection(connection, cursor)


@alert_management_bp.route("/dashboard", methods=["GET"])
def alert_dashboard():
    filters = {
        "alert_name": request.args.get("alert_name"),
        "severity": request.args.get("severity"),
        "alert_status": request.args.get("alert_status"),
        "workflow_status": request.args.get("workflow_status"),
        "assigned_user": request.args.get("assigned_user"),
        "agency": request.args.get("agency"),
        "country": request.args.get("country"),
        "passport_number": (
            request.args.get("passport_number")
            or request.args.get("passport_no")
        ),
        "ageing_from_days": (
            request.args.get("ageing_from_days")
            or request.args.get("ageing_from")
            or 0
        ),
        "ageing_to_days": (
            request.args.get("ageing_to_days")
            or request.args.get("ageing_to")
            or 30
        ),
        "from_date": request.args.get("from_date"),
        "to_date": request.args.get("to_date"),
        "search_text": request.args.get("search_text"),
    }

    page = request.args.get("page", 1, type=int)
    page_size = request.args.get("page_size", 100, type=int)

    dashboard_context = get_alert_dashboard_context(
        filters=filters,
        page=page,
        page_size=page_size,
    )

    summary = dashboard_context.get("summary") or {}
    alerts = dashboard_context.get("alert_records") or []

    # Compatibility aliases expected by the existing HTML and JavaScript.
    for row in alerts:
        row["passport_no"] = (
            row.get("passport_no")
            or row.get("passport_number")
            or ""
        )
        row["last_alert_on"] = (
            row.get("last_notified_on")
            or row.get("critical_triggered_on")
            or row.get("warning_triggered_on")
            or row.get("created_on")
        )
        row["operation_queue_no"] = (
            row.get("operation_queue_no")
            or row.get("reference_id")
            or ""
        )

    metrics = {
        "total_open": int(summary.get("total_open_alerts") or 0),
        "warning": int(summary.get("warning_count") or 0),
        "critical": int(summary.get("critical_count") or 0),
        "acknowledged": int(summary.get("acknowledged_count") or 0),
        "snoozed": int(summary.get("snoozed_count") or 0),
    }

    return render_template(
        "alert_dashboard.html",
        alerts=alerts,
        metrics=metrics,
        alert_names=dashboard_context.get("alert_names") or [],
        workflow_statuses=(
            dashboard_context.get("workflow_statuses") or []
        ),
        users=dashboard_context.get("assigned_users") or [],
        agencies=dashboard_context.get("agencies") or [],
        countries=dashboard_context.get("countries") or [],
        filters=dashboard_context.get("filters") or {},
        visible_summary=dashboard_context.get("visible_summary") or {},
        visible_alert_count=int(
            dashboard_context.get("visible_alert_count") or 0
        ),
        selected_alert_count=int(
            dashboard_context.get("selected_alert_count") or 0
        ),
        total_count=int(dashboard_context.get("total_count") or 0),
        page=dashboard_context.get("page") or 1,
        page_size=dashboard_context.get("page_size") or 100,
        total_pages=dashboard_context.get("total_pages") or 1,
        error_message=dashboard_context.get("error_message"),
        success_message=dashboard_context.get("success_message"),
    )


def _normalise_alert_ids(value: Any) -> list[int]:
    raw_values = value if isinstance(value, list) else [value]
    alert_ids: list[int] = []
    seen: set[int] = set()

    for raw_value in raw_values:
        try:
            alert_id = int(raw_value)
        except (TypeError, ValueError):
            continue

        if alert_id <= 0 or alert_id in seen:
            continue

        seen.add(alert_id)
        alert_ids.append(alert_id)

    return alert_ids


VALID_SNOOZE_UNITS = {"MINUTES", "HOURS", "DAYS"}
SNOOZE_MAXIMUMS = {
    "MINUTES": 10080,
    "HOURS": 168,
    "DAYS": 7,
}


def _normalise_snooze_unit(value: Any) -> str:
    unit = str(value or "").strip().upper()
    return unit if unit in VALID_SNOOZE_UNITS else "MINUTES"


def _parse_snooze_value(value: Any) -> int:
    if isinstance(value, bool):
        raise ValueError("Snooze duration must be a whole number.")

    text = str(value or "").strip()
    if not text or not text.isdigit():
        raise ValueError("Snooze duration must contain numbers only.")

    converted = int(text)
    if converted < 1:
        raise ValueError("Snooze duration must be greater than zero.")

    return converted


def _build_snooze_delta(value: int, unit: str) -> timedelta:
    if unit == "DAYS":
        return timedelta(days=value)
    if unit == "HOURS":
        return timedelta(hours=value)
    return timedelta(minutes=value)


@alert_management_bp.route("/alerts/action", methods=["POST"])
def update_alert_actions():
    """
    Persist Acknowledge, Snooze and Resolve actions in alert_instance.

    Snooze accepts a number only. Its unit is read from the Alert Rule:
    Warning alerts use warning_unit and Critical alerts use critical_unit.
    """
    connection = cursor = None

    try:
        payload = request.get_json(silent=True) or {}
        action = str(payload.get("action") or "").strip().upper()
        alert_ids = _normalise_alert_ids(
            payload.get("alert_ids")
            if payload.get("alert_ids") is not None
            else payload.get("alert_id")
        )

        if action not in {"ACKNOWLEDGE", "SNOOZE", "RESOLVE"}:
            return jsonify({
                "success": False,
                "message": "Invalid alert action.",
            }), 400

        if not alert_ids:
            return jsonify({
                "success": False,
                "message": "At least one valid alert id is required.",
            }), 400

        user_code = _current_user_code()
        placeholders = ", ".join(["%s"] * len(alert_ids))

        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            f"""
            SELECT
                ai.id,
                ai.alert_status,
                ai.severity,
                ai.rule_id,
                CASE
                    WHEN UPPER(COALESCE(ai.severity, 'WARNING')) = 'CRITICAL'
                    THEN COALESCE(NULLIF(TRIM(arm.critical_unit), ''), 'MINUTES')
                    ELSE COALESCE(NULLIF(TRIM(arm.warning_unit), ''), 'MINUTES')
                END AS snooze_unit
            FROM alert_instance ai
            LEFT JOIN alert_rule_master arm
              ON arm.id = ai.rule_id
            WHERE ai.id IN ({placeholders})
            FOR UPDATE
            """,
            tuple(alert_ids),
        )
        existing_rows = cursor.fetchall() or []
        existing_ids = {
            int(row.get("id"))
            for row in existing_rows
            if row.get("id") is not None
        }

        missing_ids = [
            alert_id for alert_id in alert_ids
            if alert_id not in existing_ids
        ]
        if missing_ids:
            connection.rollback()
            return jsonify({
                "success": False,
                "message": (
                    "Alert record(s) not found: "
                    + ", ".join(str(item) for item in missing_ids)
                ),
            }), 404

        now_value = datetime.now()
        snoozed_until = None
        snooze_value = None
        snooze_unit = None
        snoozed_alerts: list[dict[str, Any]] = []
        resolution_reason = None
        affected_rows = 0

        if action == "ACKNOWLEDGE":
            cursor.execute(
                f"""
                UPDATE alert_instance
                SET alert_status = 'ACKNOWLEDGED',
                    acknowledged_by = %s,
                    acknowledged_on = %s,
                    updated_on = %s
                WHERE id IN ({placeholders})
                  AND UPPER(COALESCE(alert_status, 'OPEN')) <> 'RESOLVED'
                """,
                (
                    user_code,
                    now_value,
                    now_value,
                    *alert_ids,
                ),
            )
            affected_rows = int(cursor.rowcount or 0)
            response_status = "ACKNOWLEDGED"
            response_message = (
                f"{affected_rows} alert(s) acknowledged successfully."
            )

        elif action == "SNOOZE":
            try:
                snooze_value = _parse_snooze_value(
                    payload.get("snooze_value")
                )
            except ValueError as exc:
                connection.rollback()
                return jsonify({
                    "success": False,
                    "message": str(exc),
                }), 400

            prepared_snoozes: list[dict[str, Any]] = []

            for row in existing_rows:
                row_unit = _normalise_snooze_unit(row.get("snooze_unit"))
                maximum = SNOOZE_MAXIMUMS[row_unit]

                if snooze_value > maximum:
                    connection.rollback()
                    return jsonify({
                        "success": False,
                        "message": (
                            f"Snooze duration must be between 1 and "
                            f"{maximum} {row_unit.lower()}."
                        ),
                    }), 400

                row_until = now_value + _build_snooze_delta(
                    snooze_value,
                    row_unit,
                )
                prepared_snoozes.append({
                    "alert_id": int(row["id"]),
                    "snooze_unit": row_unit,
                    "snoozed_until": row_until,
                })

            for item in prepared_snoozes:
                cursor.execute(
                    """
                    UPDATE alert_instance
                    SET alert_status = 'SNOOZED',
                        snoozed_by = %s,
                        snoozed_on = %s,
                        snoozed_until = %s,
                        next_notification_on = %s,
                        updated_on = %s
                    WHERE id = %s
                      AND UPPER(COALESCE(alert_status, 'OPEN')) <> 'RESOLVED'
                    """,
                    (
                        user_code,
                        now_value,
                        item["snoozed_until"],
                        item["snoozed_until"],
                        now_value,
                        item["alert_id"],
                    ),
                )
                affected_rows += int(cursor.rowcount or 0)
                snoozed_alerts.append({
                    "alert_id": item["alert_id"],
                    "snooze_value": snooze_value,
                    "snooze_unit": item["snooze_unit"],
                    "snoozed_until": item["snoozed_until"].isoformat(
                        timespec="seconds"
                    ),
                })

            if len(prepared_snoozes) == 1:
                snoozed_until = prepared_snoozes[0]["snoozed_until"]
                snooze_unit = prepared_snoozes[0]["snooze_unit"]
                display_unit = snooze_unit.title()
                if snooze_value == 1:
                    display_unit = display_unit.rstrip("s")
                response_message = (
                    f"{affected_rows} alert(s) snoozed for "
                    f"{snooze_value} {display_unit} until "
                    f"{snoozed_until:%Y-%m-%d %H:%M:%S}."
                )
            else:
                response_message = (
                    f"{affected_rows} alert(s) snoozed according to "
                    "their Alert Rule units."
                )

            response_status = "SNOOZED"

        else:
            resolution_reason = str(
                payload.get("resolution_reason")
                or "Resolved from Alert Dashboard."
            ).strip()

            cursor.execute(
                f"""
                UPDATE alert_instance
                SET alert_status = 'RESOLVED',
                    resolved_by = %s,
                    resolved_on = %s,
                    resolution_reason = %s,
                    next_notification_on = NULL,
                    updated_on = %s
                WHERE id IN ({placeholders})
                  AND UPPER(COALESCE(alert_status, 'OPEN')) <> 'RESOLVED'
                """,
                (
                    user_code,
                    now_value,
                    resolution_reason[:500],
                    now_value,
                    *alert_ids,
                ),
            )
            affected_rows = int(cursor.rowcount or 0)
            response_status = "RESOLVED"
            response_message = (
                f"{affected_rows} alert(s) resolved successfully."
            )

        connection.commit()

        return jsonify({
            "success": True,
            "message": response_message,
            "action": action,
            "alert_status": response_status,
            "affected_rows": affected_rows,
            "alert_ids": alert_ids,
            "performed_by": user_code,
            "action_on": now_value.isoformat(timespec="seconds"),
            "snooze_value": snooze_value,
            "snooze_unit": snooze_unit,
            "snoozed_until": (
                snoozed_until.isoformat(timespec="seconds")
                if snoozed_until is not None
                else None
            ),
            "snoozed_alerts": snoozed_alerts,
            "resolution_reason": resolution_reason,
        }), 200

    except Exception as exc:
        if connection is not None:
            try:
                connection.rollback()
            except Exception:
                pass

        current_app.logger.exception(
            "Unable to update alert action."
        )
        return jsonify({
            "success": False,
            "message": f"Unable to update alert action: {exc}",
        }), 500

    finally:
        _close_connection(connection, cursor)


@alert_management_bp.route("/create-alert-rule", methods=["GET", "POST"])
def create_alert_rule():
    if request.method == "GET":
        rule_id = request.args.get("rule_id", type=int)
        mode = str(request.args.get("mode") or "create").lower()

        if rule_id and mode in {"view", "edit"}:
            selected_rule, selected_recipients = get_alert_rule(rule_id)
            if not selected_rule:
                flash("Alert rule not found.", "danger")
                return redirect(url_for("alert_management.create_alert_rule"))

            return render_alert_rule_page(
                selected_rule=selected_rule,
                selected_recipients=selected_recipients,
                page_mode=mode,
            )

        return render_alert_rule_page()

    try:
        payload = get_request_data()
        rule, recipients = validate_and_normalize(payload)
        rule_id = create_alert_rule_in_db(rule, recipients, _current_user_code())

        if request.is_json:
            return jsonify({
                "success":True,
                "message":"Alert rule created successfully.",
                "rule_id":rule_id,
            }), 201

        flash(f"Alert rule created successfully. Rule ID: {rule_id}", "success")
        return redirect(url_for("alert_management.create_alert_rule"))

    except ValueError as exc:
        return _error_response(exc, 400)
    except IntegrityError as exc:
        current_app.logger.exception("Duplicate or invalid Alert Rule data.")
        return _error_response(exc, 409, "Duplicate or invalid alert rule data.")
    except Error as exc:
        current_app.logger.exception("Database error while creating Alert Rule.")
        return _error_response(exc, 500, "Database error while creating alert rule.")
    except Exception as exc:
        current_app.logger.exception("Unexpected error while creating Alert Rule.")
        return _error_response(exc, 500, "Unexpected error while creating alert rule.")


@alert_management_bp.route("/alert-rules/<int:rule_id>", methods=["PUT"])
def update_alert_rule(rule_id: int):
    try:
        payload = get_request_data()
        rule, recipients = validate_and_normalize(payload)
        update_alert_rule_in_db(rule_id, rule, recipients, _current_user_code())
        return jsonify({"success":True,"message":"Alert rule updated successfully.","rule_id":rule_id})
    except ValueError as exc:
        return jsonify({"success":False,"message":str(exc)}), 400
    except IntegrityError as exc:
        current_app.logger.exception("Integrity error while updating Alert Rule.")
        return jsonify({"success":False,"message":f"Database integrity error: {exc}"}), 409
    except Exception as exc:
        current_app.logger.exception("Unable to update Alert Rule.")
        return jsonify({"success":False,"message":f"Unable to update alert rule: {exc}"}), 500


@alert_management_bp.route("/alert-rules/<int:rule_id>", methods=["DELETE"])
def delete_alert_rule(rule_id: int):
    try:
        delete_alert_rule_in_db(rule_id)
        return jsonify({"success":True,"message":"Alert rule deleted successfully."})
    except ValueError as exc:
        return jsonify({"success":False,"message":str(exc)}), 404
    except IntegrityError:
        current_app.logger.exception("Alert Rule has dependent records.")
        return jsonify({
            "success":False,
            "message":"This alert rule cannot be deleted because dependent alert records exist.",
        }), 409
    except Exception as exc:
        current_app.logger.exception("Unable to delete Alert Rule.")
        return jsonify({"success":False,"message":f"Unable to delete alert rule: {exc}"}), 500


def _error_response(exc: Exception, status_code: int, message: str | None = None):
    final_message = message or str(exc)
    if request.is_json:
        return jsonify({"success":False,"message":final_message}), status_code
    flash(final_message, "danger")
    return render_alert_rule_page(form_data=request.form, status_code=status_code)
