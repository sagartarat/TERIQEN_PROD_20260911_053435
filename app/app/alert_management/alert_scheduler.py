"""Alert scheduler wrapper.

This file is intentionally small. The master APScheduler remains in
app/queue_management/scheduler.py.
"""

from __future__ import annotations

import traceback

from app.alert_management.alert_engine import process_due_alert_rules
from app.common.logger import get_logger


alert_scheduler_logger = get_logger("alert_scheduler")


def run_alert_engine_job() -> dict:
    alert_scheduler_logger.info("ALERT_ENGINE_JOB_TRIGGERED")

    try:
        totals = process_due_alert_rules()
        alert_scheduler_logger.info(
            "ALERT_ENGINE_JOB_COMPLETED rules=%s matched=%s created=%s "
            "escalated=%s repeated=%s deliveries=%s resolved=%s failed_rules=%s",
            totals["rules"],
            totals["matched"],
            totals["created"],
            totals["escalated"],
            totals["repeated"],
            totals["deliveries"],
            totals["resolved"],
            totals["failed_rules"],
        )

        failed_rules = int(totals.get("failed_rules") or 0)
        run_status = "PARTIAL" if failed_rules > 0 else "SUCCESS"

        return {
            "monitoring_status": run_status,
            "records_read": int(totals.get("rules") or 0),
            "records_processed": int(totals.get("matched") or 0),
            "records_success": max(
                int(totals.get("rules") or 0) - failed_rules,
                0,
            ),
            "records_failed": failed_rules,
            "records_skipped": 0,
            "remarks": (
                "Alert Engine completed. "
                f"rules={totals.get('rules', 0)}, "
                f"matched={totals.get('matched', 0)}, "
                f"created={totals.get('created', 0)}, "
                f"escalated={totals.get('escalated', 0)}, "
                f"repeated={totals.get('repeated', 0)}, "
                f"deliveries={totals.get('deliveries', 0)}, "
                f"resolved={totals.get('resolved', 0)}, "
                f"failed_rules={failed_rules}."
            ),
            "alert_totals": totals,
        }

    except Exception as exc:
        alert_scheduler_logger.exception(
            "ALERT_ENGINE_JOB_FAILED error=%s",
            exc,
        )
        return {
            "monitoring_status": "FAILED",
            "records_read": 0,
            "records_processed": 0,
            "records_success": 0,
            "records_failed": 0,
            "records_skipped": 0,
            "error_message": str(exc),
            "stack_trace": traceback.format_exc(),
            "remarks": "Alert Engine failed.",
        }
