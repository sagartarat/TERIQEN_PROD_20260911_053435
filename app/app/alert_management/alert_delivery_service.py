"""
Alert notification delivery service.

Delivery behaviour:
- IN_APP is written directly as DELIVERED.
- EMAIL is rendered from the configured Alert email template and sent
  immediately through the configured GRAPH or SMTP backend.
- WHATSAPP and SMS remain PENDING until their provider integrations
  are connected.

Default operational template code: ALERT_NOTIFICATION
Default invoice template code: INVOICE_NOTIFICATION
Overrides: ALERT_EMAIL_TEMPLATE_CODE / INVOICE_ALERT_EMAIL_TEMPLATE_CODE
"""

from __future__ import annotations

import json
import re
import smtplib
from datetime import date, datetime
from email.message import EmailMessage
from typing import Any

from flask import current_app
from jinja2.sandbox import ImmutableSandboxedEnvironment

from app.alert_management import alert_repository
from app.alert_management.alert_recipient_resolver import resolve_recipients
from app.common.logger import get_logger
from app.queue_management.queue_email_service import (
    _send_email_via_graph,
    get_active_email_templates,
)


alert_delivery_logger = get_logger("alert_delivery_service")

CHANNEL_FLAGS = {
    "IN_APP": "in_app_flag",
    "EMAIL": "email_flag",
    "WHATSAPP": "whatsapp_flag",
    "SMS": "sms_flag",
}
DEFAULT_ALERT_TEMPLATE_CODE = "ALERT_NOTIFICATION"
DEFAULT_INVOICE_ALERT_TEMPLATE_CODE = "INVOICE_NOTIFICATION"
CONTRACT_EXPIRY_TEMPLATE_CATEGORY = "CONTRACT EXPIRY"
_TRUE_VALUES = {"Y", "YES", "1", "TRUE", "ON"}


def _safe_text(value: Any) -> str:
    return "" if value is None else str(value).strip()


def _is_yes(value: Any) -> bool:
    return _safe_text(value).upper() in _TRUE_VALUES


def _config_bool(key: str, default: bool = False) -> bool:
    value = current_app.config.get(key)
    if value is None:
        return default
    return value if isinstance(value, bool) else _is_yes(value)


def _destination(channel: str, user: dict[str, Any]) -> str | None:
    value = {
        "IN_APP": user.get("user_code"),
        "EMAIL": user.get("user_email"),
        "WHATSAPP": user.get("user_conctno"),
        "SMS": user.get("user_conctno"),
    }.get(channel)
    return _safe_text(value) or None


def _format_template_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, date):
        return value.strftime("%Y-%m-%d")
    return _safe_text(value)


def _normalise_module_code(value: Any) -> str:
    return _safe_text(value).upper()


def _is_partner_contract_expiry_rule(
    rule: dict[str, Any] | None = None,
) -> bool:
    """Return True only for the Partner Contract End Date date-expiry trigger."""
    rule = rule or {}
    module_code = _normalise_module_code(rule.get("module_code"))
    trigger_type = _normalise_module_code(rule.get("trigger_type"))
    ageing_basis = _normalise_module_code(rule.get("ageing_basis"))

    partner_modules = {
        "PARTNER",
        "PARTNER_DETAILS",
        "PARTNER_MANAGEMENT",
        "PARTNER_REGISTRATION",
        "PARTNER_MODULE",
    }

    return (
        module_code in partner_modules
        and trigger_type == "DATE_EXPIRY"
        and ageing_basis == "CONTRACT_END_DATE"
    )


def _get_alert_template_code(rule: dict[str, Any] | None = None) -> str:
    module_code = _normalise_module_code((rule or {}).get("module_code"))

    if module_code in {"INVOICE", "INVOICE_MANAGEMENT"}:
        return (
            _safe_text(
                current_app.config.get("INVOICE_ALERT_EMAIL_TEMPLATE_CODE")
            )
            or DEFAULT_INVOICE_ALERT_TEMPLATE_CODE
        )

    return (
        _safe_text(current_app.config.get("ALERT_EMAIL_TEMPLATE_CODE"))
        or DEFAULT_ALERT_TEMPLATE_CODE
    )


def _get_alert_email_template(
    rule: dict[str, Any] | None = None,
) -> dict[str, Any]:
    templates = get_active_email_templates() or []

    # CONTRACT_EXPIRY_TEMPLATE_V1:
    # Partner Contract End Date alerts use the active Contract Expiry category.
    # If no such template exists, the existing ALERT_NOTIFICATION behaviour is
    # preserved as the fallback.
    if _is_partner_contract_expiry_rule(rule):
        category_matches = [
            row
            for row in templates
            if _safe_text(row.get("template_category")).upper()
            == CONTRACT_EXPIRY_TEMPLATE_CATEGORY
        ]
        if category_matches:
            category_matches.sort(
                key=lambda row: (
                    0 if int(row.get("is_default") or 0) == 1 else 1,
                    -int(row.get("id") or 0),
                )
            )
            return category_matches[0]

    required_code = _get_alert_template_code(rule).upper()
    for template_row in templates:
        if _safe_text(template_row.get("template_code")).upper() == required_code:
            return template_row
    return {}


def _get_alert_instance(
    *, rule: dict[str, Any], source_record: dict[str, Any]
) -> dict[str, Any]:
    reference_id = source_record.get("id")
    if reference_id is None:
        return {}

    return alert_repository.get_existing_alert_instance(
        rule_id=int(rule["id"]),
        reference_type=alert_repository.get_reference_type_for_rule(rule),
        reference_id=str(reference_id).strip(),
    ) or {}


def _get_stored_alert_context(instance: dict[str, Any]) -> dict[str, Any]:
    """Return alert_context_json as a dictionary without raising errors."""
    raw_context = instance.get("alert_context_json")
    if isinstance(raw_context, dict):
        return raw_context
    if not raw_context:
        return {}

    try:
        parsed = json.loads(str(raw_context))
        return parsed if isinstance(parsed, dict) else {}
    except (TypeError, ValueError, json.JSONDecodeError):
        return {}


def _get_threshold_value(rule: dict[str, Any], severity: str) -> Any:
    key = (
        "critical_after_minutes"
        if _safe_text(severity).upper() == "CRITICAL"
        else "warning_after_minutes"
    )
    return rule.get(key)


def _get_triggered_on(instance: dict[str, Any], severity: str) -> Any:
    if _safe_text(severity).upper() == "CRITICAL":
        return (
            instance.get("critical_triggered_on")
            or instance.get("warning_triggered_on")
            or instance.get("created_on")
            or datetime.now()
        )
    return (
        instance.get("warning_triggered_on")
        or instance.get("created_on")
        or datetime.now()
    )


def _build_template_context(
    *,
    rule: dict[str, Any],
    source_record: dict[str, Any],
    instance: dict[str, Any],
    user: dict[str, Any],
    alert_instance_id: int,
    severity: str,
) -> dict[str, Any]:
    """
    Build template placeholders from the live source record and the context
    saved in alert_instance. This keeps existing placeholders and adds aliases
    such as workflow_status for the physical wrkflw_status column.
    """
    stored_context = _get_stored_alert_context(instance)

    # Make existing source/context fields available to future templates too.
    context = {
        str(key): _format_template_value(value)
        for data in (stored_context, source_record)
        for key, value in (data or {}).items()
        if key is not None
    }

    workflow_status = _safe_text(
        source_record.get("workflow_status")
        or source_record.get("wrkflw_status")
        or stored_context.get("workflow_status")
        or stored_context.get("wrkflw_status")
    )
    channel_thread_id = _safe_text(
        source_record.get("channel_thread_id")
        or stored_context.get("channel_thread_id")
    )

    # Email Template Builder placeholder mapping uses `patient_passport` for
    # operation_queue_master.patpassport_no. Patient workflow alerts expose the
    # physical column as `patpassport_no`, so normalise all supported aliases
    # here before rendering the configured template.
    patient_passport = _safe_text(
        source_record.get("patient_passport")
        or source_record.get("patpassport_no")
        or source_record.get("passport_no")
        or source_record.get("patient_passport_no")
        or source_record.get("patient_passport_number")
        or stored_context.get("patient_passport")
        or stored_context.get("patpassport_no")
        or stored_context.get("passport_no")
        or stored_context.get("patient_passport_no")
        or stored_context.get("patient_passport_number")
    )

    invoice_number = _safe_text(
        source_record.get("invoice_number")
        or stored_context.get("invoice_number")
        or (
            source_record.get("id")
            if alert_repository.get_reference_type_for_rule(rule) == "INVOICE"
            else ""
        )
    )

    printentered_at = (
        source_record.get("printentered_at")
        or source_record.get("prntentered_at")
        or stored_context.get("printentered_at")
        or stored_context.get("prntentered_at")
    )

    totalinv_amount = (
        source_record.get("totalinv_amount")
        if source_record.get("totalinv_amount") is not None
        else stored_context.get("totalinv_amount")
    )

    print_status = _safe_text(
        source_record.get("print_status")
        or stored_context.get("print_status")
    )

    current_value = source_record.get("ageing_minutes")
    if current_value is None:
        current_value = instance.get("ageing_minutes")

    # CONTRACT_EXPIRY_PLACEHOLDERS_V1:
    # These aliases correspond to rows in email_template_placeholder_master.
    # user_name is the resolved alert recipient's name. Contract and agency
    # values come from the Partner record selected by the Contract End Date rule.
    partner_type = _safe_text(
        source_record.get("partner_type")
        or stored_context.get("partner_type")
    ).upper()
    partner_short_name = _safe_text(
        source_record.get("partner_short_name")
        or source_record.get("short_name")
        or stored_context.get("partner_short_name")
        or stored_context.get("short_name")
    )
    contract_end_date = (
        source_record.get("contract_end_date")
        or source_record.get("contend_date")
        or stored_context.get("contract_end_date")
        or stored_context.get("contend_date")
    )
    agency_short_name = _safe_text(
        source_record.get("agency_short_name")
        or stored_context.get("agency_short_name")
        or (partner_short_name if partner_type == "AGENCY" else "")
    )
    # CONTRACT_EXPIRY_PARTNER_TYPE_PLACEHOLDERS_V1_0_1:
    # The existing {{hospital_short_name}} placeholder normally comes from
    # operation_queue_master. Contract Expiry alerts are evaluated directly
    # from partner_reg, so for Hospital partners resolve that same placeholder
    # from partner_reg.short_name. Agency partners continue to use the new
    # {{agency_short_name}} placeholder. Existing workflow alert behaviour is
    # preserved because source/stored hospital_short_name values take priority.
    hospital_short_name = _safe_text(
        source_record.get("hospital_short_name")
        or stored_context.get("hospital_short_name")
        or (partner_short_name if partner_type == "HOSPITAL" else "")
    )

    context.update(
        {
            "user_name": _safe_text(user.get("user_name")),
            "contract_end_date": _format_template_value(contract_end_date),
            "agency_short_name": agency_short_name,
            "hospital_short_name": hospital_short_name,
            "partner_short_name": partner_short_name,
            "alert_name": _safe_text(
                rule.get("alert_name") or instance.get("alert_name")
            ),
            "severity": _safe_text(severity).upper(),
            "current_value": _format_template_value(current_value),
            "threshold_value": _format_template_value(
                _get_threshold_value(rule, severity)
            ),
            "alert_status": _safe_text(instance.get("alert_status") or "OPEN"),
            "triggered_on": _format_template_value(
                _get_triggered_on(instance, severity)
            ),
            "rule_id": _format_template_value(rule.get("id")),
            "alert_instance_id": str(alert_instance_id),
            "channel_thread_id": channel_thread_id,
            "patient_passport": patient_passport,
            "patpassport_no": patient_passport,
            "passport_no": patient_passport,
            "patient_passport_no": patient_passport,
            "patient_passport_number": patient_passport,
            "Patient_Passport": patient_passport,
            "workflow_status": workflow_status,
            "wrkflw_status": workflow_status,
            "invoice_number": invoice_number,
            "printentered_at": _format_template_value(printentered_at),
            "prntentered_at": _format_template_value(printentered_at),
            "totalinv_amount": _format_template_value(totalinv_amount),
            "print_status": print_status,
            "inv_currency": _format_template_value(
                source_record.get("inv_currency")
                or stored_context.get("inv_currency")
            ),
        }
    )
    return context


def _render_template_text(template_text: Any, context: dict[str, Any]) -> str:
    raw_text = str(template_text or "")
    if not raw_text:
        return ""

    try:
        return ImmutableSandboxedEnvironment(autoescape=False).from_string(raw_text).render(**context).strip()
    except Exception as exc:
        alert_delivery_logger.exception(
            "ALERT_TEMPLATE_RENDER_FAILED error=%s", exc
        )
        return raw_text.strip()


def _build_fallback_subject(*, rule: dict[str, Any], severity: str) -> str:
    return (
        f"[{_safe_text(severity).upper()}] "
        f"{_safe_text(rule.get('alert_name'))}"
    ).strip()


def _build_fallback_message(
    *,
    rule: dict[str, Any],
    source_record: dict[str, Any],
    alert_instance_id: int,
    severity: str,
    user: dict[str, Any],
) -> str:
    lines = [
        f"Dear {_safe_text(user.get('user_name')) or 'User'},",
        "",
        "An alert has been generated.",
        "",
        f"Alert Name: {_safe_text(rule.get('alert_name'))}",
        f"Severity: {_safe_text(severity).upper()}",
        f"Current Value: {_safe_text(source_record.get('ageing_minutes'))}",
        f"Threshold Value: {_safe_text(_get_threshold_value(rule, severity))}",
        "Workflow Status: "
        f"{_safe_text(source_record.get('wrkflw_status') or source_record.get('workflow_status'))}",
        f"Rule ID: {_safe_text(rule.get('id'))}",
        f"Alert Instance ID: {alert_instance_id}",
    ]

    channel_thread_id = _safe_text(source_record.get("channel_thread_id"))
    if channel_thread_id:
        lines.append(f"Channel Thread ID: {channel_thread_id}")

    invoice_number = _safe_text(source_record.get("invoice_number"))
    if invoice_number:
        lines.append(f"Invoice Number: {invoice_number}")

    lines.extend(
        [
            "",
            "Please log in to TERIQEN to review the alert.",
            "",
            "Regards,",
            "TERIQEN",
        ]
    )
    return "\n".join(lines)


def _build_email_content(
    *,
    template_row: dict[str, Any],
    context: dict[str, Any],
    rule: dict[str, Any],
    source_record: dict[str, Any],
    alert_instance_id: int,
    severity: str,
    user: dict[str, Any],
) -> tuple[str, str]:
    fallback_subject = _build_fallback_subject(rule=rule, severity=severity)
    fallback_message = _build_fallback_message(
        rule=rule,
        source_record=source_record,
        alert_instance_id=alert_instance_id,
        severity=severity,
        user=user,
    )
    if not template_row:
        return fallback_subject, fallback_message

    return (
        _render_template_text(template_row.get("template_subject"), context)
        or fallback_subject,
        _render_template_text(template_row.get("template_body"), context)
        or fallback_message,
    )


def _get_mail_backend() -> str:
    return _safe_text(
        current_app.config.get("MAIL_BACKEND")
        or current_app.config.get("EMAIL_BACKEND")
        or current_app.config.get("DEFAULT_MAIL_BACKEND")
        or "SMTP"
    ).upper()


def _get_default_sender_email() -> str:
    return _safe_text(
        current_app.config.get("MS_GRAPH_USER_EMAIL")
        or current_app.config.get("GRAPH_SENDER_EMAIL")
        or current_app.config.get("MAIL_DEFAULT_SENDER")
        or current_app.config.get("MAIL_FROM")
        or current_app.config.get("MAIL_USERNAME")
        or current_app.config.get("SMTP_USERNAME")
        or current_app.config.get("EMAIL_FROM")
    )


def _split_email_addresses(value: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()

    for item in _safe_text(value).replace(";", ",").replace("|", ",").split(","):
        email_address = item.strip()
        key = email_address.lower()
        if email_address and key not in seen:
            seen.add(key)
            result.append(email_address)

    return result


def _html_to_plain_text(value: str) -> str:
    text = re.sub(r"(?i)<br\s*/?>", "\n", str(value or ""))
    text = re.sub(r"(?i)</p\s*>", "\n\n", text)
    return re.sub(r"<[^>]+>", "", text).strip()


def _send_email_smtp(
    *, to_email: str, subject: str, body: str
) -> dict[str, Any]:
    smtp_host = _safe_text(
        current_app.config.get("MAIL_SERVER")
        or current_app.config.get("SMTP_HOST")
    )
    smtp_port = int(
        current_app.config.get("MAIL_PORT")
        or current_app.config.get("SMTP_PORT")
        or 587
    )
    username = _safe_text(
        current_app.config.get("MAIL_USERNAME")
        or current_app.config.get("SMTP_USERNAME")
    )
    password = (
        current_app.config.get("MAIL_PASSWORD")
        or current_app.config.get("SMTP_PASSWORD")
        or ""
    )
    sender_email = _get_default_sender_email() or username
    recipients = _split_email_addresses(to_email)

    if not smtp_host:
        return {
            "success": False,
            "message": "MAIL_SERVER or SMTP_HOST is not configured.",
        }
    if not sender_email:
        return {"success": False, "message": "Sender email is not configured."}
    if not recipients:
        return {"success": False, "message": "Recipient email is missing."}

    message = EmailMessage()
    message["Subject"] = subject
    message["From"] = sender_email
    message["To"] = ", ".join(recipients)
    message.set_content(_html_to_plain_text(body) or body or "")

    if "<" in str(body or "") and ">" in str(body or ""):
        message.add_alternative(str(body or ""), subtype="html")

    use_ssl = _config_bool("MAIL_USE_SSL", default=False)
    use_tls = _config_bool("MAIL_USE_TLS", default=not use_ssl)
    timeout_seconds = int(current_app.config.get("MAIL_TIMEOUT", 30))
    server = None

    try:
        server_class = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
        server = server_class(smtp_host, smtp_port, timeout=timeout_seconds)

        if not use_ssl:
            server.ehlo()
            if use_tls:
                server.starttls()
                server.ehlo()

        if username:
            server.login(username, password)

        server.send_message(
            message, from_addr=sender_email, to_addrs=recipients
        )
        return {"success": True, "message": "Email sent successfully."}

    except Exception as exc:
        return {"success": False, "message": str(exc)}

    finally:
        if server is not None:
            try:
                server.quit()
            except Exception:
                pass


def _send_alert_email(
    *, destination: str, subject: str, message: str
) -> dict[str, Any]:
    try:
        if _get_mail_backend() == "GRAPH":
            return _send_email_via_graph(
                sender_email=_get_default_sender_email(),
                to_email=destination,
                cc_email="",
                bcc_email="",
                rendered_subject=subject,
                rendered_body=message,
                attached_files=[],
            )

        return _send_email_smtp(
            to_email=destination, subject=subject, body=message
        )

    except Exception as exc:
        return {"success": False, "message": str(exc)}


def create_delivery_rows(
    *,
    rule: dict[str, Any],
    source_record: dict[str, Any],
    alert_instance_id: int,
    severity: str,
    notification_sequence: int,
) -> int:
    """Resolve recipients, deliver enabled channels, and write delivery logs."""
    configs = alert_repository.get_rule_recipients(rule["id"], severity)
    instance = _get_alert_instance(rule=rule, source_record=source_record)
    template_row = _get_alert_email_template(rule)
    inserted_count = 0

    if template_row:
        alert_delivery_logger.info(
            "ALERT_EMAIL_TEMPLATE_LOADED template_code=%s template_id=%s",
            template_row.get("template_code"),
            template_row.get("id"),
        )
    else:
        alert_delivery_logger.warning(
            "ALERT_EMAIL_TEMPLATE_NOT_FOUND template_code=%s using_fallback=Y",
            _get_alert_template_code(rule),
        )

    default_subject = _build_fallback_subject(rule=rule, severity=severity)
    default_message = _build_fallback_message(
        rule=rule,
        source_record=source_record,
        alert_instance_id=alert_instance_id,
        severity=severity,
        user={},
    )

    for config in configs:
        users = resolve_recipients(config, source_record)

        if not users:
            alert_delivery_logger.warning(
                "ALERT_RECIPIENT_NOT_RESOLVED rule_id=%s severity=%s "
                "recipient_type=%s recipient_value=%s",
                rule.get("id"),
                severity,
                config.get("recipient_type"),
                config.get("recipient_value"),
            )

        for user in users:
            template_context = _build_template_context(
                rule=rule,
                source_record=source_record,
                instance=instance,
                user=user,
                alert_instance_id=alert_instance_id,
                severity=severity,
            )
            email_subject, email_message = _build_email_content(
                template_row=template_row,
                context=template_context,
                rule=rule,
                source_record=source_record,
                alert_instance_id=alert_instance_id,
                severity=severity,
                user=user,
            )

            for channel, flag_name in CHANNEL_FLAGS.items():
                if not _is_yes(config.get(flag_name)):
                    continue

                destination = _destination(channel, user)
                subject_text = email_subject if channel == "EMAIL" else default_subject
                message_text = email_message if channel == "EMAIL" else default_message

                if channel == "EMAIL" and destination:
                    alert_delivery_logger.info(
                        "ALERT_EMAIL_ATTEMPT instance_id=%s rule_id=%s "
                        "recipient=%s template_code=%s",
                        alert_instance_id,
                        rule.get("id"),
                        destination,
                        template_row.get("template_code")
                        if template_row
                        else "FALLBACK",
                    )
                    send_result = _send_alert_email(
                        destination=destination,
                        subject=subject_text,
                        message=message_text,
                    )
                    delivery_status = (
                        "SENT" if send_result.get("success") else "FAILED"
                    )

                    if delivery_status == "SENT":
                        alert_delivery_logger.info(
                            "ALERT_EMAIL_SENT instance_id=%s rule_id=%s recipient=%s",
                            alert_instance_id,
                            rule.get("id"),
                            destination,
                        )
                    else:
                        alert_delivery_logger.error(
                            "ALERT_EMAIL_FAILED instance_id=%s rule_id=%s "
                            "recipient=%s error=%s",
                            alert_instance_id,
                            rule.get("id"),
                            destination,
                            send_result.get("message"),
                        )

                elif channel == "EMAIL":
                    delivery_status = "SKIPPED"
                    alert_delivery_logger.warning(
                        "ALERT_EMAIL_SKIPPED instance_id=%s user_code=%s "
                        "reason=missing_email",
                        alert_instance_id,
                        user.get("user_code"),
                    )

                elif channel == "IN_APP":
                    delivery_status = "DELIVERED" if destination else "SKIPPED"

                else:
                    delivery_status = "PENDING" if destination else "SKIPPED"

                inserted = alert_repository.insert_delivery_log(
                    alert_instance_id=alert_instance_id,
                    rule_id=int(rule["id"]),
                    severity=severity,
                    notification_sequence=notification_sequence,
                    recipient_type=config.get("recipient_type") or "",
                    configured_recipient_value=config.get("recipient_value") or "",
                    resolved_user_code=user.get("user_code"),
                    resolved_recipient_name=user.get("user_name"),
                    delivery_channel=channel,
                    destination=destination,
                    subject_text=subject_text,
                    message_text=message_text,
                    delivery_status=delivery_status,
                )
                inserted_count += 1 if inserted else 0

    return inserted_count
