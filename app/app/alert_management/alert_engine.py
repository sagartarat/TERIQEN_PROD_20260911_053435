"""
Alert evaluation engine.

Supported triggers:
    WORKFLOW_STATUS
    DATE_EXPIRY

Module source mapping:
    PATIENT_PROCESS -> patient_wrkflw
    INVOICE         -> invoice_master
    Other/default modules -> operation_queue_master
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.alert_management import alert_repository
from app.alert_management.alert_delivery_service import create_delivery_rows
from app.common.logger import get_logger


alert_engine_logger = get_logger("alert_engine")


def _determine_severity(
    rule: dict[str, Any],
    record: dict[str, Any],
) -> str | None:
    critical = int(rule.get("critical_after_minutes") or 0)
    warning = int(rule.get("warning_after_minutes") or 0)
    trigger_type = str(rule.get("trigger_type") or "").strip().upper()

    if trigger_type == "DATE_EXPIRY":
        remaining = record.get("remaining_minutes")
        if remaining is None:
            return None
        remaining_minutes = int(remaining)
        if critical > 0 and remaining_minutes <= critical:
            return "CRITICAL"
        if warning > 0 and remaining_minutes <= warning:
            return "WARNING"
        return None

    ageing_minutes = int(record.get("ageing_minutes") or 0)
    if critical > 0 and ageing_minutes >= critical:
        return "CRITICAL"
    if warning > 0 and ageing_minutes >= warning:
        return "WARNING"
    return None


def _evaluate_record(
    rule: dict[str, Any],
    record: dict[str, Any],
) -> dict[str, int]:
    counts = {
        "created": 0,
        "escalated": 0,
        "repeated": 0,
        "deliveries": 0,
    }

    severity = _determine_severity(rule, record)

    if severity is None:
        return counts

    reference_id = str(record.get("id") or "").strip()
    if not reference_id:
        alert_engine_logger.warning(
            "ALERT_RECORD_SKIPPED missing_reference_id "
            "rule_id=%s module_code=%s",
            rule.get("id"),
            rule.get("module_code"),
        )
        return counts

    # alert_instance.reference_id is VARCHAR. Numeric patient/queue IDs remain
    # valid as text, while Invoice Alerts use the actual invoice number.
    source_record = dict(record)
    source_record["id"] = reference_id

    reference_type = alert_repository.get_reference_type_for_rule(rule)

    instance = alert_repository.get_existing_alert_instance(
        rule_id=rule["id"],
        reference_type=reference_type,
        reference_id=reference_id,
    )

    should_deliver = False
    is_repeat_notification = False

    if instance is None:
        alert_instance_id = alert_repository.create_alert_instance(
            rule=rule,
            record=source_record,
            severity=severity,
        )
        counts["created"] += 1
        should_deliver = True

    else:
        alert_instance_id = int(instance["id"])
        existing_severity = str(
            instance.get("severity") or ""
        ).strip().upper()

        if existing_severity == "WARNING" and severity == "CRITICAL":
            alert_repository.update_alert_instance(
                alert_instance_id,
                severity,
                int(source_record.get("ageing_minutes") or 0),
                escalate=True,
            )
            counts["escalated"] += 1
            should_deliver = True

        else:
            alert_repository.update_alert_instance(
                alert_instance_id,
                severity,
                int(source_record.get("ageing_minutes") or 0),
            )

            refreshed = alert_repository.get_existing_alert_instance(
                rule_id=rule["id"],
                reference_type=reference_type,
                reference_id=reference_id,
            ) or instance

            if alert_repository.should_repeat(refreshed, rule):
                counts["repeated"] += 1
                should_deliver = True
                is_repeat_notification = True

    if should_deliver:
        # Capture the current scheduler-cycle minute before synchronous channel
        # delivery begins. Email delivery may take one or more seconds; using
        # its completion time as the repeat base can miss the next heartbeat.
        notification_anchor = datetime.now().replace(
            second=0,
            microsecond=0,
        )

        sequence = alert_repository.get_next_notification_sequence(
            alert_instance_id
        )

        counts["deliveries"] += create_delivery_rows(
            rule=rule,
            source_record=source_record,
            alert_instance_id=alert_instance_id,
            severity=severity,
            notification_sequence=sequence,
        )

        alert_repository.mark_alert_notified(
            alert_instance_id,
            int(rule.get("repeat_interval_minutes") or 0),
            notification_anchor=notification_anchor,
            is_repeat=is_repeat_notification,
        )

    return counts


def evaluate_rule(rule: dict[str, Any]) -> dict[str, int]:
    result = {
        "matched": 0,
        "created": 0,
        "escalated": 0,
        "repeated": 0,
        "deliveries": 0,
        "resolved": 0,
    }

    trigger_type = str(
        rule.get("trigger_type") or ""
    ).strip().upper()

    if trigger_type not in {"WORKFLOW_STATUS", "DATE_EXPIRY"}:
        alert_engine_logger.warning(
            "ALERT_RULE_SKIPPED unsupported_trigger "
            "rule_id=%s trigger_type=%s",
            rule.get("id"),
            trigger_type,
        )
        return result

    records = alert_repository.get_matching_records(rule)
    result["matched"] = len(records)

    alert_engine_logger.info(
        "ALERT_RULE_MATCH_RESULT rule_id=%s module_code=%s "
        "trigger_type=%s workflow_status=%s matched=%s reference_type=%s",
        rule.get("id"),
        rule.get("module_code"),
        trigger_type,
        rule.get("workflow_status"),
        result["matched"],
        alert_repository.get_reference_type_for_rule(rule),
    )

    for record in records:
        record_result = _evaluate_record(rule, record)

        for key in (
            "created",
            "escalated",
            "repeated",
            "deliveries",
        ):
            result[key] += record_result[key]

    result["resolved"] = (
        alert_repository.auto_resolve_nonmatching_alerts(rule)
    )

    return result


def process_due_alert_rules() -> dict[str, int]:
    totals = {
        "rules": 0,
        "matched": 0,
        "created": 0,
        "escalated": 0,
        "repeated": 0,
        "deliveries": 0,
        "resolved": 0,
        "failed_rules": 0,
    }

    rules = alert_repository.get_due_active_rules(limit=100)
    totals["rules"] = len(rules)

    for rule in rules:
        try:
            result = evaluate_rule(rule)

            for key in (
                "matched",
                "created",
                "escalated",
                "repeated",
                "deliveries",
                "resolved",
            ):
                totals[key] += result[key]

            alert_repository.mark_rule_evaluated(rule)

            alert_engine_logger.info(
                "ALERT_RULE_COMPLETED rule_id=%s module_code=%s "
                "matched=%s created=%s escalated=%s repeated=%s "
                "deliveries=%s resolved=%s",
                rule.get("id"),
                rule.get("module_code"),
                result["matched"],
                result["created"],
                result["escalated"],
                result["repeated"],
                result["deliveries"],
                result["resolved"],
            )

        except Exception as exc:
            totals["failed_rules"] += 1

            alert_engine_logger.exception(
                "ALERT_RULE_FAILED rule_id=%s module_code=%s error=%s",
                rule.get("id"),
                rule.get("module_code"),
                exc,
            )

    return totals
