"""Runtime monitoring wrapper for TERIQEN APScheduler jobs.

Monitoring is deliberately non-blocking: a monitoring-table failure is logged but
must never prevent Queue Parser, SLA Engine or Alert Engine business processing.
"""

from __future__ import annotations

import traceback
from typing import Any, Callable

from app.common.logger import get_logger
from app.system_monitoring import system_monitoring_repository as repository


monitoring_logger = get_logger("scheduler_monitoring")

MASTER_SCHEDULER_NAME = "TERIQEN_BACKGROUND_SCHEDULER"


SCHEDULER_JOB_DEFINITIONS: dict[str, dict[str, Any]] = {
    "QUEUE_PARSER": {
        "job_code": "QUEUE_PARSER",
        "apscheduler_job_id": "queue_parser_job",
        "job_name": "Queue Parser",
        "job_group": "CORE",
        "module_code": "QUEUE_MANAGEMENT",
        "job_function": "run_queue_parser_job",
        "job_description": (
            "Processes pending channel messages into the TERIQEN Operation Queue."
        ),
        "scheduler_type": "INTERVAL",
        "interval_value": 1,
        "interval_unit": "MINUTES",
        "active_flag": "Y",
    },
    "SLA_ENGINE": {
        "job_code": "SLA_ENGINE",
        "apscheduler_job_id": "sla_engine_job",
        "job_name": "SLA Engine",
        "job_group": "CORE",
        "module_code": "QUEUE_MANAGEMENT",
        "job_function": "run_sla_engine_job",
        "job_description": (
            "Evaluates Operation Queue SLA rules, breach status and escalations."
        ),
        "scheduler_type": "INTERVAL",
        "interval_value": 1,
        "interval_unit": "MINUTES",
        "active_flag": "Y",
    },
    "ALERT_ENGINE": {
        "job_code": "ALERT_ENGINE",
        "apscheduler_job_id": "alert_engine_job",
        "job_name": "Alert Engine",
        "job_group": "CORE",
        "module_code": "ALERT_MANAGEMENT",
        "job_function": "run_alert_engine_job",
        "job_description": (
            "Evaluates due alert rules and creates, escalates, repeats and resolves alerts."
        ),
        "scheduler_type": "INTERVAL",
        "interval_value": 1,
        "interval_unit": "MINUTES",
        "active_flag": "Y",
    },
}


def _safe_monitoring_call(func: Callable, *args, **kwargs):
    try:
        return func(*args, **kwargs)
    except Exception as exc:
        monitoring_logger.exception(
            "SYSTEM_MONITORING_NON_BLOCKING_ERROR function=%s error=%s",
            getattr(func, "__name__", str(func)),
            exc,
        )
        return None


def register_scheduler_jobs(scheduler=None) -> None:
    """Self-register configured jobs in scheduler_job_master."""

    for definition in SCHEDULER_JOB_DEFINITIONS.values():
        _safe_monitoring_call(repository.register_or_update_job, definition)

    if scheduler is not None:
        sync_next_run_times(scheduler)
        refresh_scheduler_heartbeat(
            scheduler,
            remarks="Scheduler master registry synchronized.",
        )


def is_job_enabled(job_code: str) -> bool:
    """Return DB-controlled active_flag; default to enabled if monitoring is unavailable."""

    try:
        row = repository.get_job_by_code(str(job_code or "").strip().upper())
        if not row:
            return True
        return str(row.get("active_flag") or "Y").strip().upper() == "Y"
    except Exception as exc:
        monitoring_logger.exception(
            "SCHEDULER_ENABLED_CHECK_FAILED job_code=%s error=%s",
            job_code,
            exc,
        )
        return True


def sync_next_run_times(scheduler) -> None:
    if scheduler is None:
        return

    for job_code, definition in SCHEDULER_JOB_DEFINITIONS.items():
        apscheduler_job_id = definition.get("apscheduler_job_id")
        try:
            aps_job = scheduler.get_job(apscheduler_job_id)
            next_run_on = getattr(aps_job, "next_run_time", None) if aps_job else None
            if next_run_on is not None and getattr(next_run_on, "tzinfo", None) is not None:
                next_run_on = next_run_on.replace(tzinfo=None)
            _safe_monitoring_call(
                repository.update_job_next_run,
                job_code,
                next_run_on,
            )
        except Exception as exc:
            monitoring_logger.exception(
                "SCHEDULER_NEXT_RUN_SYNC_FAILED job_code=%s error=%s",
                job_code,
                exc,
            )


def refresh_scheduler_heartbeat(
    scheduler,
    scheduler_status: str = "RUNNING",
    remarks: str | None = None,
) -> None:
    # A heartbeat represents the live APScheduler instance.  If no scheduler
    # object is available (for example a future manual invocation), do not
    # overwrite a healthy heartbeat row with total_jobs=0 / active_jobs=0.
    if scheduler is None:
        return

    try:
        jobs = list(scheduler.get_jobs())
        configured_ids = {
            str(definition.get("apscheduler_job_id"))
            for definition in SCHEDULER_JOB_DEFINITIONS.values()
        }
        business_jobs = [job for job in jobs if str(job.id) in configured_ids]
        active_jobs = sum(
            1 for job in business_jobs if getattr(job, "next_run_time", None) is not None
        )

        _safe_monitoring_call(
            repository.upsert_heartbeat,
            MASTER_SCHEDULER_NAME,
            scheduler_status,
            len(business_jobs),
            active_jobs,
            remarks,
        )
    except Exception as exc:
        monitoring_logger.exception(
            "SCHEDULER_HEARTBEAT_REFRESH_FAILED error=%s",
            exc,
        )


def _normalize_result(result: Any) -> dict[str, Any]:
    if isinstance(result, dict):
        normalized = dict(result)
    else:
        normalized = {}

    for key in (
        "records_read",
        "records_processed",
        "records_success",
        "records_failed",
        "records_skipped",
    ):
        try:
            normalized[key] = int(normalized.get(key) or 0)
        except Exception:
            normalized[key] = 0

    status = str(normalized.get("monitoring_status") or "SUCCESS").strip().upper()
    if status not in {"SUCCESS", "FAILED", "PARTIAL"}:
        status = "SUCCESS"

    # If a business job reports failed records but forgets to downgrade its
    # top-level monitoring status, expose the execution as PARTIAL rather than
    # incorrectly displaying SUCCESS.  Explicit FAILED remains FAILED.
    if status == "SUCCESS" and normalized.get("records_failed", 0) > 0:
        status = "PARTIAL"

    normalized["monitoring_status"] = status
    return normalized


def run_monitored_job(
    job_code: str,
    job_callable: Callable[[], Any],
    scheduler=None,
    trigger_type: str = "SCHEDULED",
):
    """Execute one existing scheduler function and record its runtime metadata.

    If monitoring storage fails, the business function still executes.
    """

    job_code = str(job_code or "").strip().upper()
    definition = SCHEDULER_JOB_DEFINITIONS.get(job_code)

    if definition:
        _safe_monitoring_call(repository.register_or_update_job, definition)

    run_context = _safe_monitoring_call(
        repository.start_run,
        job_code,
        trigger_type,
    )

    refresh_scheduler_heartbeat(
        scheduler,
        remarks=f"{job_code} execution started.",
    )

    try:
        result = _normalize_result(job_callable())
    except Exception as exc:
        result = {
            "monitoring_status": "FAILED",
            "records_read": 0,
            "records_processed": 0,
            "records_success": 0,
            "records_failed": 0,
            "records_skipped": 0,
            "error_message": str(exc),
            "stack_trace": traceback.format_exc(),
            "remarks": f"{job_code} execution failed with an unhandled exception.",
        }
        monitoring_logger.exception(
            "MONITORED_JOB_UNHANDLED_EXCEPTION job_code=%s error=%s",
            job_code,
            exc,
        )

    if run_context:
        _safe_monitoring_call(
            repository.finish_run,
            run_context["run_id"],
            result.get("monitoring_status") or "SUCCESS",
            result.get("records_read", 0),
            result.get("records_processed", 0),
            result.get("records_success", 0),
            result.get("records_failed", 0),
            result.get("records_skipped", 0),
            result.get("remarks"),
            result.get("error_code"),
            result.get("error_message"),
            result.get("stack_trace"),
        )

    if scheduler is not None:
        sync_next_run_times(scheduler)
        refresh_scheduler_heartbeat(
            scheduler,
            remarks=(
                f"{job_code} execution completed with status "
                f"{result.get('monitoring_status')}"
            ),
        )

    return result
