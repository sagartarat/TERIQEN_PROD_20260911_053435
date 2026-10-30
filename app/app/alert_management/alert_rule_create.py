"""
Alert Rule service functions.

File:
    app/alert_management/alert_rule_create.py

Routes are defined only in:
    app/alert_management/routes.py
"""

from __future__ import annotations

import json
import re
from typing import Any

from flask import request

from app.common.db import get_db_connection


def clean_text(
    value: Any,
    default: str | None = None,
) -> str | None:
    if value is None:
        return default

    text = str(value).strip()
    return text if text else default


def to_yn_flag(
    value: Any,
    default: str = "N",
) -> str:
    """
    Convert checkbox, JSON boolean, integer and text values to
    database ENUM values Y or N.
    """
    if value is None:
        return default

    if isinstance(value, bool):
        return "Y" if value else "N"

    if isinstance(value, (int, float)):
        return "Y" if int(value) == 1 else "N"

    return (
        "Y"
        if str(value).strip().lower()
        in {
            "1",
            "true",
            "yes",
            "y",
            "on",
            "checked",
        }
        else "N"
    )


def to_int(
    value: Any,
    field_name: str,
    *,
    default: int | None = None,
    minimum: int | None = None,
) -> int | None:
    if value in (None, ""):
        return default

    try:
        converted = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(
            f"{field_name} must be a whole number."
        ) from exc

    if minimum is not None and converted < minimum:
        raise ValueError(
            f"{field_name} must be at least {minimum}."
        )

    return converted


VALID_TIME_UNITS = {"MINUTES", "HOURS", "DAYS"}
VALID_TRIGGER_TYPES = {"WORKFLOW_STATUS", "DATE_EXPIRY"}

VALID_AGEING_BASES = {
    "STATUS_CHANGED_ON",
    "RECORD_CREATED_ON",
    "LAST_ACTIVITY_ON",
    "PRINT_DATE",
    "CONTRACT_END_DATE",
}

AGEING_BASIS_ALIASES = {
    "STATUS_CHANGED_DATE": "STATUS_CHANGED_ON",
    "STATUS_DATE": "STATUS_CHANGED_ON",
    "RECORD_CREATED_DATE": "RECORD_CREATED_ON",
    "CREATED_DATE": "RECORD_CREATED_ON",
    "CREATED_ON": "RECORD_CREATED_ON",
    "LAST_ACTIVITY_DATE": "LAST_ACTIVITY_ON",
    "LAST_UPDATED_ON": "LAST_ACTIVITY_ON",
    "UPDATED_ON": "LAST_ACTIVITY_ON",
    "PRINTED_DATE": "PRINT_DATE",
    "PRINTED_ON": "PRINT_DATE",
}


def normalise_ageing_basis(
    value: Any,
    module_code: str,
    trigger_type: str = "WORKFLOW_STATUS",
) -> str:
    if module_code == "INVOICE":
        return "PRINT_DATE"

    if trigger_type == "DATE_EXPIRY":
        return "CONTRACT_END_DATE" if module_code == "PARTNER" else ""

    basis = (clean_text(value, "") or "").upper()
    basis = AGEING_BASIS_ALIASES.get(basis, basis)

    if not basis:
        raise ValueError("Ageing Basis is required.")

    if basis not in VALID_AGEING_BASES - {"PRINT_DATE", "CONTRACT_END_DATE"}:
        raise ValueError(
            "Ageing Basis must be Status Changed Date, "
            "Record Created Date or Last Activity Date."
        )

    return basis


def normalise_time_unit(
    value: Any,
    field_name: str,
    *,
    default: str | None = None,
) -> str | None:
    unit = (clean_text(value, "") or "").upper()
    if not unit:
        return default
    if unit not in VALID_TIME_UNITS:
        raise ValueError(
            f"{field_name} must be MINUTES, HOURS or DAYS."
        )
    return unit


def get_request_data() -> dict[str, Any]:
    """
    Support application/json and normal HTML form submissions.
    """
    if request.is_json:
        payload = request.get_json(silent=True)
        return payload if isinstance(payload, dict) else {}

    return request.form.to_dict(flat=True)


def parse_recipients(
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    recipients = payload.get("recipients")

    if recipients is None:
        recipients = payload.get("recipients_json", "[]")

    if isinstance(recipients, str):
        recipients = recipients.strip() or "[]"

        try:
            recipients = json.loads(recipients)
        except json.JSONDecodeError as exc:
            raise ValueError(
                "Recipient details contain invalid JSON."
            ) from exc

    if not isinstance(recipients, list):
        raise ValueError(
            "Recipients must be supplied as a list."
        )

    return recipients


def normalise_module_code(value: Any) -> str:
    """Return a stable Alert Module code without changing known codes."""
    text = clean_text(value, "") or ""
    code = re.sub(r"[^A-Z0-9]+", "_", text.upper()).strip("_")

    aliases = {
        "INVOICE_MANAGEMENT": "INVOICE",
        "INVOICE_MODULE": "INVOICE",
        "PARTNER_MANAGEMENT": "PARTNER",
        "PARTNER_REGISTRATION": "PARTNER",
        "PARTNER_MODULE": "PARTNER",
        "PARTNER_DETAILS": "PARTNER",
    }

    return aliases.get(code, code)


def validate_and_normalize(
    payload: dict[str, Any],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    alert_name = clean_text(payload.get("alert_name"))
    module_code = normalise_module_code(payload.get("module_code"))
    trigger_type = (clean_text(payload.get("trigger_type"), "") or "").upper()
    workflow_status = clean_text(payload.get("workflow_status"))
    ageing_basis = normalise_ageing_basis(
        payload.get("ageing_basis"),
        module_code,
        trigger_type,
    )

    if module_code == "INVOICE":
        # Invoice Alerts always monitor printed, generated invoices and age from
        # invoice_master.prntentered_at.
        workflow_status = (workflow_status or "INVOICE GENERATED").upper()

    if not alert_name:
        raise ValueError("Alert Name is required.")

    if not module_code:
        raise ValueError("Module is required.")

    if not trigger_type:
        raise ValueError("Trigger Type is required.")

    if trigger_type not in VALID_TRIGGER_TYPES:
        raise ValueError("Trigger Type is invalid.")

    if trigger_type == "WORKFLOW_STATUS" and not workflow_status:
        raise ValueError("Workflow Status is required.")

    if trigger_type == "DATE_EXPIRY":
        if module_code != "PARTNER":
            raise ValueError(
                "Contract End Date alert is supported for Partner module only."
            )
        # The existing workflow_status column is NOT NULL. Store an empty
        # string because workflow status is not applicable to a date alert.
        # No placeholder or dummy business value is stored.
        workflow_status = ""
        ageing_basis = "CONTRACT_END_DATE"

    if module_code == "INVOICE":
        if trigger_type != "WORKFLOW_STATUS":
            raise ValueError(
                "Invoice Alerts support Workflow Status trigger only."
            )

        if workflow_status != "INVOICE GENERATED":
            raise ValueError(
                "Invoice Alert Workflow Status must be INVOICE GENERATED."
            )

    warning_after = to_int(
        payload.get("warning_after_minutes"),
        "Warning After Minutes",
        default=0,
        minimum=0,
    )

    critical_after = to_int(
        payload.get("critical_after_minutes"),
        "Critical After Minutes",
        default=0,
        minimum=0,
    )

    repeat_interval = to_int(
        payload.get("repeat_interval_minutes"),
        "Repeat Interval Minutes",
        default=0,
        minimum=0,
    )

    maximum_repeat_count = to_int(
        payload.get("maximum_repeat_count"),
        "Maximum Repeat Count",
        default=0,
        minimum=0,
    )

    warning_unit = normalise_time_unit(
        payload.get("warning_unit"),
        "Warning Unit",
        default="MINUTES",
    )
    critical_unit = normalise_time_unit(
        payload.get("critical_unit"),
        "Critical Unit",
        default="MINUTES",
    )
    repeat_unit = normalise_time_unit(
        payload.get("repeat_unit"),
        "Repeat Unit",
        default="MINUTES" if repeat_interval else None,
    )

    if not warning_after:
        raise ValueError(
            "Warning After Minutes must be greater than zero."
        )

    if not critical_after:
        raise ValueError(
            "Critical After Minutes must be greater than zero."
        )

    if trigger_type == "DATE_EXPIRY":
        if warning_after <= critical_after:
            raise ValueError(
                "For Contract End Date alerts, Warning must be set farther "
                "before expiry than Critical."
            )
    elif critical_after <= warning_after:
        raise ValueError(
            "Critical After Minutes must be greater than "
            "Warning After Minutes."
        )

    if maximum_repeat_count and not repeat_interval:
        raise ValueError(
            "Repeat Interval Minutes is required when Maximum Repeat "
            "Count is greater than zero."
        )

    rule = {
        "alert_name": alert_name,
        "alert_description": clean_text(
            payload.get("alert_description")
        ),
        "module_code": module_code,
        "trigger_type": trigger_type,
        "workflow_status": workflow_status,
        "ageing_basis": ageing_basis,
        "warning_after_minutes": warning_after,
        "warning_unit": warning_unit,
        "critical_after_minutes": critical_after,
        "critical_unit": critical_unit,
        "repeat_interval_minutes": repeat_interval,
        "repeat_unit": repeat_unit,
        "maximum_repeat_count": maximum_repeat_count,
        "business_hours_only": to_yn_flag(
            payload.get("business_hours_only")
        ),
        "exclude_weekends": to_yn_flag(
            payload.get("exclude_weekends")
        ),
        "exclude_holidays": to_yn_flag(
            payload.get("exclude_holidays")
        ),
        "auto_resolve": to_yn_flag(
            payload.get("auto_resolve")
        ),
        "active_flag": to_yn_flag(
            payload.get("active_flag"),
            default="Y",
        ),
    }

    raw_recipients = parse_recipients(payload)
    recipients: list[dict[str, Any]] = []

    valid_severities = {
        "WARNING",
        "CRITICAL",
    }

    # UI can send USER; database ENUM expects SPECIFIC_USER.
    valid_input_recipient_types = {
        "ASSIGNED_USER",
        "USER",
        "SPECIFIC_USER",
        "ROLE",
        "TEAM_LEADER",
        "ADMINISTRATOR",
    }

    recipient_type_mapping = {
        "USER": "SPECIFIC_USER",
    }

    for row_number, item in enumerate(
        raw_recipients,
        start=1,
    ):
        if not isinstance(item, dict):
            raise ValueError(
                f"Recipient row {row_number} is invalid."
            )

        severity = (
            clean_text(item.get("severity"), "") or ""
        ).upper()

        recipient_type = (
            clean_text(item.get("recipient_type"), "") or ""
        ).upper()

        recipient_value = clean_text(
            item.get("recipient_value")
        )

        if severity not in valid_severities:
            raise ValueError(
                f"Recipient row {row_number}: Severity must be "
                "WARNING or CRITICAL."
            )

        if recipient_type not in valid_input_recipient_types:
            raise ValueError(
                f"Recipient row {row_number}: Invalid Recipient Type "
                f"'{recipient_type}'."
            )

        recipient_type = recipient_type_mapping.get(
            recipient_type,
            recipient_type,
        )

        if not recipient_value:
            raise ValueError(
                f"Recipient row {row_number}: "
                "Recipient Value is required."
            )

        normalized = {
            "severity": severity,
            "recipient_type": recipient_type,
            "recipient_value": recipient_value,
            "in_app_flag": to_yn_flag(
                item.get("in_app_flag"),
                default="Y",
            ),
            "email_flag": to_yn_flag(
                item.get("email_flag")
            ),
            "whatsapp_flag": to_yn_flag(
                item.get("whatsapp_flag")
            ),
            "sms_flag": to_yn_flag(
                item.get("sms_flag")
            ),
            "active_flag": to_yn_flag(
                item.get("active_flag"),
                default="Y",
            ),
        }

        if not any(
            normalized[channel] == "Y"
            for channel in (
                "in_app_flag",
                "email_flag",
                "whatsapp_flag",
                "sms_flag",
            )
        ):
            raise ValueError(
                f"Recipient row {row_number}: Select at least one "
                "notification channel."
            )

        recipients.append(normalized)

    if not recipients:
        raise ValueError(
            "Add at least one alert recipient."
        )

    return rule, recipients


def create_alert_rule_in_db(
    rule: dict[str, Any],
    recipients: list[dict[str, Any]],
    user_code: str,
) -> int:
    """
    Insert alert_rule_master and alert_rule_recipient records within
    one database transaction.
    """
    connection = None
    cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor()

        cursor.execute(
            """
            INSERT INTO alert_rule_master
            (
                alert_name,
                alert_description,
                module_code,
                trigger_type,
                workflow_status,
                ageing_basis,
                warning_after_minutes,
                warning_unit,
                critical_after_minutes,
                critical_unit,
                repeat_interval_minutes,
                repeat_unit,
                maximum_repeat_count,
                business_hours_only,
                exclude_weekends,
                exclude_holidays,
                auto_resolve,
                active_flag,
                created_by,
                created_on,
                updated_by,
                updated_on
            )
            VALUES
            (
                %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, NOW(), %s, NOW()
            )
            """,
            (
                rule["alert_name"],
                rule["alert_description"],
                rule["module_code"],
                rule["trigger_type"],
                rule["workflow_status"],
                rule["ageing_basis"],
                rule["warning_after_minutes"],
                rule["warning_unit"],
                rule["critical_after_minutes"],
                rule["critical_unit"],
                rule["repeat_interval_minutes"],
                rule["repeat_unit"],
                rule["maximum_repeat_count"],
                rule["business_hours_only"],
                rule["exclude_weekends"],
                rule["exclude_holidays"],
                rule["auto_resolve"],
                rule["active_flag"],
                user_code,
                user_code,
            ),
        )

        rule_id = int(cursor.lastrowid)

        cursor.executemany(
            """
            INSERT INTO alert_rule_recipient
            (
                rule_id,
                severity,
                recipient_type,
                recipient_value,
                in_app_flag,
                email_flag,
                whatsapp_flag,
                sms_flag,
                active_flag,
                created_by,
                created_on,
                updated_by,
                updated_on
            )
            VALUES
            (
                %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                %s, NOW(), %s, NOW()
            )
            """,
            [
                (
                    rule_id,
                    recipient["severity"],
                    recipient["recipient_type"],
                    recipient["recipient_value"],
                    recipient["in_app_flag"],
                    recipient["email_flag"],
                    recipient["whatsapp_flag"],
                    recipient["sms_flag"],
                    recipient["active_flag"],
                    user_code,
                    user_code,
                )
                for recipient in recipients
            ],
        )

        connection.commit()
        return rule_id

    except Exception:
        if connection is not None:
            connection.rollback()
        raise

    finally:
        if cursor is not None:
            cursor.close()

        if connection is not None:
            connection.close()
