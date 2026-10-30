"""Read-side service for TERIQEN System Monitoring screens.

This module intentionally keeps monitoring read logic separate from scheduler/business
processing.  It maps the existing monitoring tables and operational tables into the
field names already expected by the System Monitoring HTML templates.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from app.common.logger import get_logger
from app.system_monitoring import system_monitoring_repository as repository


service_logger = get_logger("system_monitoring_service")

HEARTBEAT_STALE_SECONDS = 180
CHANNEL_QUEUE_STUCK_MINUTES = 10
MANUAL_REVIEW_STUCK_MINUTES = 60
OPERATION_QUEUE_STUCK_MINUTES = 60
MAX_QUEUE_SOURCE_ROWS = 5000
MAX_PROCESS_SOURCE_ROWS = 5000
MAX_VISIBLE_ROWS = 500

TERMINAL_WORKFLOW_STATUSES = {
    "CLOSED",
    "CANCEL",
    "CANCELLED",
    "NO PAYMENT",
    "NOT INTERESTED WITHOUT PAY",
}


# -----------------------------------------------------------------------------
# Generic helpers
# -----------------------------------------------------------------------------

def _safe_int(value: Any, default: int = 0) -> int:
    try:
        return int(value or 0)
    except Exception:
        return default


def _safe_float(value: Any) -> float | None:
    if value is None:
        return None
    try:
        return float(value)
    except Exception:
        return None


def _safe_upper(value: Any) -> str:
    return str(value or "").strip().upper()


def _duration_ms_from_seconds(value: Any) -> int | None:
    seconds = _safe_float(value)
    if seconds is None:
        return None
    return max(int(round(seconds * 1000)), 0)


def _duration_ms_between(started_on: Any, completed_on: Any) -> int | None:
    if not isinstance(started_on, datetime) or not isinstance(completed_on, datetime):
        return None
    try:
        return max(int((completed_on - started_on).total_seconds() * 1000), 0)
    except Exception:
        return None


def _seconds_since(value: Any) -> int | None:
    if not isinstance(value, datetime):
        return None
    try:
        return max(int((datetime.now() - value).total_seconds()), 0)
    except Exception:
        return None


def _minutes_since(value: Any) -> int | None:
    seconds = _seconds_since(value)
    if seconds is None:
        return None
    return max(int(seconds // 60), 0)


def _parse_filter_datetime(value: Any) -> datetime | None:
    text = str(value or "").strip()
    if not text:
        return None

    for fmt in (
        "%Y-%m-%dT%H:%M",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
    ):
        try:
            return datetime.strptime(text, fmt)
        except Exception:
            continue
    return None


def _sortable_datetime(value: Any) -> datetime:
    """Return a datetime suitable for stable descending sort operations."""
    if isinstance(value, datetime):
        return value

    parsed = _parse_filter_datetime(value)
    return parsed or datetime.min


def _frequency_label(job: dict[str, Any]) -> str:
    scheduler_type = _safe_upper(job.get("scheduler_type"))

    if scheduler_type == "INTERVAL":
        value = job.get("interval_value")
        unit = str(job.get("interval_unit") or "").strip().lower()
        if value is not None and unit:
            try:
                numeric_value = int(value)
            except Exception:
                numeric_value = value

            if str(numeric_value) == "1" and unit.endswith("s"):
                unit = unit[:-1]

            return f"Every {numeric_value} {unit}"

    if scheduler_type == "CRON":
        return str(job.get("cron_expression") or "Cron")

    if scheduler_type == "MANUAL":
        return "Manual"

    return scheduler_type or "-"


def _heartbeat_state(
    heartbeat: dict[str, Any] | None,
) -> tuple[str, int | None]:
    heartbeat = heartbeat or {}
    age = _seconds_since(heartbeat.get("last_heartbeat_on"))
    scheduler_status = _safe_upper(heartbeat.get("scheduler_status")) or "STOPPED"

    if scheduler_status == "ERROR":
        return "FAILED", age

    if scheduler_status != "RUNNING":
        return "STOPPED", age

    if age is None:
        return "MISSING", None

    if age > HEARTBEAT_STALE_SECONDS:
        return "STALE", age

    return "HEALTHY", age


def _job_health(
    job: dict[str, Any],
    heartbeat_status: str,
    latest_run: dict[str, Any] | None,
) -> str:
    if _safe_upper(job.get("active_flag") or "Y") != "Y":
        return "DISABLED"

    if heartbeat_status in {"FAILED", "STOPPED", "STALE", "MISSING"}:
        return heartbeat_status

    if not latest_run:
        return "UNKNOWN"

    last_status = _safe_upper(latest_run.get("run_status"))

    if last_status == "FAILED":
        return "FAILED"
    if last_status == "PARTIAL":
        return "WARNING"
    if last_status == "RUNNING":
        return "RUNNING"
    if last_status == "SUCCESS":
        return "HEALTHY"

    return "UNKNOWN"


def _job_row(
    job: dict[str, Any],
    heartbeat: dict[str, Any] | None,
    latest_run: dict[str, Any] | None = None,
    last_success_on: datetime | None = None,
) -> dict[str, Any]:
    heartbeat = heartbeat or {}
    latest_run = latest_run or {}

    heartbeat_status, heartbeat_age = _heartbeat_state(heartbeat)
    execution_status = _safe_upper(latest_run.get("run_status")) or "NEVER_RUN"
    health_status = _job_health(job, heartbeat_status, latest_run or None)

    duration_ms = _duration_ms_from_seconds(
        latest_run.get("duration_seconds")
        if latest_run
        else job.get("last_duration_seconds")
    )

    row = dict(job)
    row.update(
        {
            "id": job.get("id"),
            "scheduler_id": job.get("id"),
            "scheduler_code": job.get("job_code"),
            "scheduler_name": job.get("job_name"),
            "module_name": job.get("module_code"),
            "enabled_flag": job.get("active_flag"),
            "frequency_label": _frequency_label(job),
            "heartbeat_status": heartbeat_status,
            "heartbeat_age_seconds": heartbeat_age,
            "health_status": health_status,
            "status": health_status,
            "health_message": heartbeat.get("remarks"),
            "last_heartbeat_on": heartbeat.get("last_heartbeat_on"),
            "server_name": heartbeat.get("server_name"),
            "process_id": heartbeat.get("process_id"),
            "last_run_on": latest_run.get("start_time") or job.get("last_run_on"),
            "next_run_on": job.get("next_run_on"),
            "execution_status": execution_status,
            "last_run_status": execution_status,
            "duration_ms": duration_ms,
            "records_found": _safe_int(latest_run.get("records_read")),
            "records_processed": _safe_int(latest_run.get("records_processed")),
            "success_count": _safe_int(latest_run.get("records_success")),
            "latest_failure_count": _safe_int(latest_run.get("records_failed")),
            "skipped_count": _safe_int(latest_run.get("records_skipped")),
            "failure_count": _safe_int(job.get("failure_count")),
            "last_success_on": last_success_on,
            "latest_run_id": latest_run.get("run_id"),
            "latest_execution_id": latest_run.get("id"),
            "latest_error_message": latest_run.get("error_message"),
            "latest_remarks": latest_run.get("remarks"),
        }
    )
    return row


def _filter_scheduler_rows(
    rows: list[dict[str, Any]],
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    q = str(filters.get("q") or "").strip().lower()
    module = _safe_upper(filters.get("module"))
    status = _safe_upper(filters.get("status"))
    enabled = _safe_upper(filters.get("enabled"))
    heartbeat = _safe_upper(filters.get("heartbeat"))

    filtered: list[dict[str, Any]] = []

    for row in rows:
        if q:
            haystack = " ".join(
                str(row.get(key) or "")
                for key in (
                    "scheduler_name",
                    "scheduler_code",
                    "module_name",
                    "job_description",
                )
            ).lower()
            if q not in haystack:
                continue

        if module and _safe_upper(row.get("module_name")) != module:
            continue

        if status and status not in {
            _safe_upper(row.get("health_status")),
            _safe_upper(row.get("execution_status")),
        }:
            continue

        if enabled:
            expected = "Y" if enabled in {"Y", "1", "TRUE", "ENABLED"} else "N"
            actual = "Y" if _safe_upper(row.get("enabled_flag")) in {"Y", "1", "TRUE"} else "N"
            if actual != expected:
                continue

        if heartbeat:
            actual_heartbeat = _safe_upper(row.get("heartbeat_status"))
            if heartbeat == "MISSING":
                if actual_heartbeat not in {"MISSING", "UNKNOWN"}:
                    continue
            elif actual_heartbeat != heartbeat:
                continue

        filtered.append(row)

    return filtered


# -----------------------------------------------------------------------------
# Scheduler monitor / detail
# -----------------------------------------------------------------------------

def get_scheduler_monitor_context(
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    filters = dict(filters or {})

    jobs = repository.fetch_jobs()
    heartbeat = repository.fetch_latest_heartbeat()
    latest_runs = repository.fetch_latest_runs_by_job()
    last_success_map = repository.fetch_last_success_by_job()

    all_rows = [
        _job_row(
            job,
            heartbeat,
            latest_runs.get(_safe_int(job.get("id"))),
            last_success_map.get(_safe_int(job.get("id"))),
        )
        for job in jobs
    ]

    rows = _filter_scheduler_rows(all_rows, filters)

    summary = {
        "registered": len(rows),
        "enabled": sum(
            1
            for row in rows
            if _safe_upper(row.get("enabled_flag")) in {"Y", "1", "TRUE"}
        ),
        "running": sum(
            1
            for row in rows
            if _safe_upper(row.get("execution_status")) == "RUNNING"
        ),
        "failed": sum(
            1
            for row in rows
            if _safe_upper(row.get("execution_status")) == "FAILED"
        ),
        "stale_heartbeat": sum(
            1
            for row in rows
            if _safe_upper(row.get("heartbeat_status")) in {"STALE", "MISSING"}
        ),
    }

    return {
        "scheduler_rows": rows,
        "summary": summary,
        "filters": filters,
        "module_options": sorted(
            {
                row.get("module_name")
                for row in all_rows
                if row.get("module_name")
            }
        ),
        "status_options": [
            "HEALTHY",
            "RUNNING",
            "WARNING",
            "FAILED",
            "STALE",
            "STOPPED",
            "UNKNOWN",
            "DISABLED",
        ],
        "pagination": None,
    }


def get_scheduler_detail_context(scheduler_id: int) -> dict[str, Any]:
    job = repository.fetch_job_by_id(scheduler_id)
    heartbeat = repository.fetch_latest_heartbeat()

    if not job:
        scheduler = {
            "id": scheduler_id,
            "scheduler_name": None,
            "scheduler_code": None,
            "health_status": "UNKNOWN",
            "heartbeat_status": "UNKNOWN",
            "execution_status": "NEVER_RUN",
            "can_run_now": False,
            "can_toggle": False,
        }
        return {
            "scheduler": scheduler,
            "latest_execution": None,
            "execution_rows": [],
        }

    executions = repository.fetch_runs_for_job(job["id"], limit=100)
    latest_run = executions[0] if executions else None
    last_success_on = None
    for run in executions:
        if _safe_upper(run.get("run_status")) == "SUCCESS":
            last_success_on = run.get("end_time") or run.get("start_time")
            break

    scheduler = _job_row(
        job,
        heartbeat,
        latest_run,
        last_success_on,
    )
    scheduler.update(
        {
            "job_function": job.get("job_function"),
            "scheduler_type": job.get("scheduler_type"),
            "max_runtime_seconds": job.get("max_runtime_seconds"),
            "retry_count": _safe_int(job.get("retry_count")),
            "can_run_now": False,
            "can_toggle": False,
        }
    )

    execution_rows = [_execution_row(row, job=job) for row in executions]

    return {
        "scheduler": scheduler,
        "latest_execution": execution_rows[0] if execution_rows else None,
        "execution_rows": execution_rows,
    }


# -----------------------------------------------------------------------------
# Execution history
# -----------------------------------------------------------------------------

def _execution_row(
    row: dict[str, Any],
    job: dict[str, Any] | None = None,
) -> dict[str, Any]:
    job = job or row

    result = dict(row)
    result.update(
        {
            "id": row.get("id"),
            "execution_id": row.get("run_id") or row.get("id"),
            "scheduler_id": row.get("job_id"),
            "scheduler_name": job.get("job_name"),
            "scheduler_code": job.get("job_code"),
            "module_name": job.get("module_code"),
            "started_on": row.get("start_time"),
            "completed_on": row.get("end_time"),
            "duration_ms": _duration_ms_from_seconds(row.get("duration_seconds")),
            "execution_status": _safe_upper(row.get("run_status")) or "UNKNOWN",
            "records_found": _safe_int(row.get("records_read")),
            "records_processed": _safe_int(row.get("records_processed")),
            "success_count": _safe_int(row.get("records_success")),
            "failure_count": _safe_int(row.get("records_failed")),
            "skipped_count": _safe_int(row.get("records_skipped")),
            "result_message": row.get("remarks"),
        }
    )
    return result


def _filter_execution_rows(
    rows: list[dict[str, Any]],
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    scheduler_id = str(filters.get("scheduler_id") or "").strip()
    status = _safe_upper(filters.get("status"))
    execution_id = str(filters.get("execution_id") or "").strip().lower()
    from_time = _parse_filter_datetime(filters.get("from_time"))
    to_time = _parse_filter_datetime(filters.get("to_time"))

    filtered: list[dict[str, Any]] = []

    for row in rows:
        if scheduler_id and str(row.get("scheduler_id") or "") != scheduler_id:
            continue

        if status and _safe_upper(row.get("execution_status")) != status:
            continue

        if execution_id:
            haystack = " ".join(
                [
                    str(row.get("execution_id") or ""),
                    str(row.get("id") or ""),
                ]
            ).lower()
            if execution_id not in haystack:
                continue

        started_on = row.get("started_on")
        if from_time and isinstance(started_on, datetime) and started_on < from_time:
            continue
        if to_time and isinstance(started_on, datetime) and started_on > to_time:
            continue

        filtered.append(row)

    return filtered


def get_execution_history_context(
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    filters = dict(filters or {})

    runs = repository.fetch_run_logs(limit=1000)
    all_rows = [_execution_row(row) for row in runs]
    rows = _filter_execution_rows(all_rows, filters)

    durations = [
        _safe_float(row.get("duration_ms"))
        for row in rows
        if row.get("duration_ms") is not None
    ]
    durations = [value for value in durations if value is not None]

    summary = {
        "total": len(rows),
        "success": sum(
            1 for row in rows if _safe_upper(row.get("execution_status")) == "SUCCESS"
        ),
        "warning": sum(
            1 for row in rows if _safe_upper(row.get("execution_status")) == "PARTIAL"
        ),
        "failed": sum(
            1 for row in rows if _safe_upper(row.get("execution_status")) == "FAILED"
        ),
        "avg_duration_ms": (
            int(round(sum(durations) / len(durations))) if durations else None
        ),
    }

    jobs = repository.fetch_jobs()
    scheduler_options = [
        {
            "id": job.get("id"),
            "scheduler_name": job.get("job_name") or job.get("job_code"),
        }
        for job in jobs
    ]

    return {
        "summary": summary,
        "execution_rows": rows,
        "filters": filters,
        "scheduler_options": scheduler_options,
        "status_options": ["RUNNING", "SUCCESS", "PARTIAL", "FAILED"],
        "pagination": None,
    }


def get_execution_detail_context(execution_id: int) -> dict[str, Any]:
    row = repository.fetch_run_by_id(execution_id)
    if not row:
        return {
            "execution": {
                "id": execution_id,
                "execution_id": execution_id,
                "execution_status": "UNKNOWN",
            },
            "process_rows": [],
        }

    return {
        "execution": _execution_row(row),
        "process_rows": [],
    }


# -----------------------------------------------------------------------------
# Queue monitor
# -----------------------------------------------------------------------------

def _channel_queue_row(row: dict[str, Any]) -> dict[str, Any] | None:
    parse_status = _safe_upper(row.get("parse_status")) or "PENDING"

    if parse_status == "MANUAL_REVIEW":
        queue_code = "MANUAL_REVIEW"
        queue_name = "Manual Review Queue"
        status = "PENDING"
        stuck_threshold = MANUAL_REVIEW_STUCK_MINUTES
    else:
        queue_code = "CHANNEL_PARSER"
        queue_name = "Inbound Parser Queue"
        stuck_threshold = CHANNEL_QUEUE_STUCK_MINUTES

        if parse_status in {"PENDING", "READY_FOR_PARSE"}:
            status = "PENDING"
        elif parse_status == "PARSING":
            status = "PROCESSING"
        elif parse_status == "PARTIAL":
            status = "RETRY_PENDING"
        elif parse_status == "FAILED":
            status = "FAILED"
        elif parse_status in {"PARSED", "LINKED", "JUNK"}:
            status = "COMPLETED"
        else:
            status = parse_status or "UNKNOWN"

    created_on = row.get("received_on") or row.get("created_on")
    updated_on = row.get("updated_on") or created_on
    age_minutes = None if status == "COMPLETED" else _minutes_since(created_on)
    is_stuck = bool(
        status in {"PENDING", "PROCESSING", "RETRY_PENDING"}
        and age_minutes is not None
        and age_minutes >= stuck_threshold
    )

    reference_id = row.get("channel_message_id") or row.get("id")
    correlation_id = (
        row.get("channel_thread_id")
        or row.get("linked_triage_id")
        or row.get("sender_mobile")
    )

    return {
        "queue_code": queue_code,
        "queue_name": queue_name,
        "record_id": row.get("id"),
        "reference_type": "CHANNEL_MESSAGE",
        "reference_id": reference_id,
        "correlation_id": correlation_id,
        "channel_thread_id": row.get("channel_thread_id"),
        "status": status,
        "source_status": parse_status,
        "attempt_count": 0,
        "created_on": created_on,
        "last_attempt_on": updated_on,
        "age_minutes": age_minutes,
        "is_stuck": is_stuck,
        "error_message": row.get("remarks") if status == "FAILED" else None,
        "remarks": row.get("remarks"),
        "raw_source": "channel_message_log",
        "linked_queue_id": row.get("linked_queue_id"),
    }


def _operation_queue_row(row: dict[str, Any]) -> dict[str, Any]:
    parse_status = _safe_upper(row.get("parse_status"))
    workflow_status = _safe_upper(row.get("wrkflw_status"))
    queue_status = _safe_upper(row.get("status"))
    conversion_status = _safe_upper(row.get("conversion_status"))

    if parse_status == "MANUAL_REVIEW":
        queue_code = "MANUAL_REVIEW"
        queue_name = "Manual Review Queue"
        status = "PENDING"
        stuck_threshold = MANUAL_REVIEW_STUCK_MINUTES
    else:
        queue_code = "OPERATION_QUEUE"
        queue_name = "Operation Queue"
        stuck_threshold = OPERATION_QUEUE_STUCK_MINUTES

        if parse_status == "FAILED" or queue_status == "FAILED":
            status = "FAILED"
        elif workflow_status in TERMINAL_WORKFLOW_STATUSES:
            status = "COMPLETED"
        elif conversion_status == "IN_PROGRESS" or workflow_status in {"IN PROGRESS", "IN_PROGRESS"}:
            status = "PROCESSING"
        elif conversion_status in {"RETRY_PENDING", "RETRY"}:
            status = "RETRY_PENDING"
        else:
            status = "PENDING"

    created_on = (
        row.get("created_on")
        or row.get("received_on")
        or row.get("updated_on")
    )
    updated_on = row.get("updated_on") or created_on
    age_minutes = None if status == "COMPLETED" else _minutes_since(created_on)

    # An Operation Queue item may legitimately remain open for human workflow work.
    # Treat only active processing/retry rows as technically stuck; age is still shown
    # for ordinary pending work so operators can assess backlog age.
    is_stuck = bool(
        status in {"PROCESSING", "RETRY_PENDING"}
        and age_minutes is not None
        and age_minutes >= stuck_threshold
    )

    reference_id = (
        row.get("vil_internalno")
        or row.get("ld_internalno")
        or row.get("channel_message_id")
        or row.get("id")
    )

    return {
        "queue_code": queue_code,
        "queue_name": queue_name,
        "record_id": row.get("id"),
        "reference_type": "OPERATION_QUEUE",
        "reference_id": reference_id,
        "correlation_id": row.get("channel_thread_id") or row.get("channel_message_id"),
        "channel_thread_id": row.get("channel_thread_id"),
        "status": status,
        "source_status": workflow_status or parse_status or queue_status,
        "attempt_count": 0,
        "created_on": created_on,
        "last_attempt_on": updated_on,
        "age_minutes": age_minutes,
        "is_stuck": is_stuck,
        "error_message": row.get("remarks") if status == "FAILED" else None,
        "remarks": row.get("remarks"),
        "raw_source": "operation_queue_master",
        "channel_message_log_id": row.get("channel_message_log_id"),
    }


def _build_queue_rows() -> list[dict[str, Any]]:
    channel_rows = repository.fetch_channel_message_rows(
        limit=MAX_QUEUE_SOURCE_ROWS
    )
    operation_rows = repository.fetch_operation_queue_rows(
        limit=MAX_QUEUE_SOURCE_ROWS
    )

    normalized_operations = [_operation_queue_row(row) for row in operation_rows]

    manual_operation_ids = {
        str(row.get("record_id"))
        for row in normalized_operations
        if row.get("queue_code") == "MANUAL_REVIEW" and row.get("record_id") is not None
    }

    normalized_channels: list[dict[str, Any]] = []
    for raw_row in channel_rows:
        normalized = _channel_queue_row(raw_row)
        if not normalized:
            continue

        # If a Manual Review operation_queue_master row already represents the same
        # request, do not double-count its channel_message_log parent as another
        # Manual Review backlog item.
        if normalized.get("queue_code") == "MANUAL_REVIEW":
            linked_queue_id = normalized.get("linked_queue_id")
            if linked_queue_id is not None and str(linked_queue_id) in manual_operation_ids:
                continue

        normalized_channels.append(normalized)

    rows = normalized_channels + normalized_operations
    rows.sort(
        key=lambda item: _sortable_datetime(item.get("created_on")),
        reverse=True,
    )
    return rows


def _filter_queue_rows(
    rows: list[dict[str, Any]],
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    queue_code = _safe_upper(filters.get("queue_code"))
    status = _safe_upper(filters.get("status"))
    reference = str(filters.get("reference") or "").strip().lower()
    stuck = _safe_upper(filters.get("stuck"))

    try:
        min_age = int(filters.get("min_age")) if str(filters.get("min_age") or "").strip() else None
    except Exception:
        min_age = None

    filtered: list[dict[str, Any]] = []

    for row in rows:
        if queue_code and _safe_upper(row.get("queue_code")) != queue_code:
            continue

        if status and _safe_upper(row.get("status")) != status:
            continue

        if reference:
            haystack = " ".join(
                str(row.get(key) or "")
                for key in (
                    "record_id",
                    "reference_id",
                    "correlation_id",
                    "channel_thread_id",
                    "remarks",
                )
            ).lower()
            if reference not in haystack:
                continue

        if min_age is not None:
            age = row.get("age_minutes")
            if age is None or _safe_int(age) < min_age:
                continue

        if stuck == "Y" and not row.get("is_stuck"):
            continue
        if stuck == "N" and row.get("is_stuck"):
            continue

        filtered.append(row)

    return filtered


def _queue_summary_rows(
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    queue_codes = ["CHANNEL_PARSER", "MANUAL_REVIEW", "OPERATION_QUEUE"]
    queue_names = {
        "CHANNEL_PARSER": "Inbound Parser Queue",
        "MANUAL_REVIEW": "Manual Review Queue",
        "OPERATION_QUEUE": "Operation Queue",
    }

    summary_rows: list[dict[str, Any]] = []
    now = datetime.now()

    for queue_code in queue_codes:
        queue_rows = [row for row in rows if row.get("queue_code") == queue_code]
        if not queue_rows:
            continue

        pending_count = sum(1 for row in queue_rows if row.get("status") == "PENDING")
        processing_count = sum(1 for row in queue_rows if row.get("status") == "PROCESSING")
        completed_count = sum(1 for row in queue_rows if row.get("status") == "COMPLETED")
        failed_count = sum(1 for row in queue_rows if row.get("status") == "FAILED")
        retry_pending_count = sum(1 for row in queue_rows if row.get("status") == "RETRY_PENDING")
        stuck_count = sum(1 for row in queue_rows if row.get("is_stuck"))

        backlog_ages = [
            _safe_int(row.get("age_minutes"))
            for row in queue_rows
            if row.get("status") in {"PENDING", "PROCESSING", "RETRY_PENDING"}
            and row.get("age_minutes") is not None
        ]

        throughput_per_hour = 0
        for row in queue_rows:
            if row.get("status") != "COMPLETED":
                continue
            completed_on = row.get("last_attempt_on") or row.get("created_on")
            if isinstance(completed_on, datetime):
                try:
                    if (now - completed_on).total_seconds() <= 3600:
                        throughput_per_hour += 1
                except Exception:
                    pass

        health_status = "HEALTHY"
        if failed_count > 0 or stuck_count > 0:
            health_status = "WARNING"

        summary_rows.append(
            {
                "queue_code": queue_code,
                "queue_name": queue_names[queue_code],
                "pending_count": pending_count,
                "processing_count": processing_count,
                "completed_count": completed_count,
                "failed_count": failed_count,
                "retry_pending_count": retry_pending_count,
                "stuck_count": stuck_count,
                "oldest_pending_minutes": max(backlog_ages) if backlog_ages else None,
                "throughput_per_hour": throughput_per_hour,
                "health_status": health_status,
            }
        )

    return summary_rows


def get_queue_monitor_context(
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    filters = dict(filters or {})
    all_rows = _build_queue_rows()
    filtered_rows = _filter_queue_rows(all_rows, filters)
    summary_rows = _queue_summary_rows(filtered_rows)

    backlog_ages = [
        _safe_int(row.get("age_minutes"))
        for row in filtered_rows
        if row.get("status") in {"PENDING", "PROCESSING", "RETRY_PENDING"}
        and row.get("age_minutes") is not None
    ]

    summary = {
        "pending": sum(1 for row in filtered_rows if row.get("status") == "PENDING"),
        "processing": sum(1 for row in filtered_rows if row.get("status") == "PROCESSING"),
        "failed": sum(1 for row in filtered_rows if row.get("status") == "FAILED"),
        "stuck": sum(1 for row in filtered_rows if row.get("is_stuck")),
        "oldest_pending_minutes": max(backlog_ages) if backlog_ages else None,
    }

    queue_options = [
        {"queue_code": "CHANNEL_PARSER", "queue_name": "Inbound Parser Queue"},
        {"queue_code": "MANUAL_REVIEW", "queue_name": "Manual Review Queue"},
        {"queue_code": "OPERATION_QUEUE", "queue_name": "Operation Queue"},
    ]

    return {
        "summary": summary,
        "queue_summary_rows": summary_rows,
        "queue_rows": filtered_rows[:MAX_VISIBLE_ROWS],
        "filters": filters,
        "queue_options": queue_options,
        "status_options": [
            "PENDING",
            "PROCESSING",
            "RETRY_PENDING",
            "COMPLETED",
            "FAILED",
        ],
        "pagination": None,
    }


# -----------------------------------------------------------------------------
# Business process monitor
# -----------------------------------------------------------------------------

def _normalize_process_status(value: Any, process_type: str) -> str:
    status = _safe_upper(value)

    if process_type == "EMAIL":
        if status in {"", "NOT_SENT", "PENDING", "QUEUED"}:
            return "PENDING"
        if status in {"SENDING", "IN_PROGRESS", "PROCESSING"}:
            return "PROCESSING"
        if status in {"SENT", "SUCCESS", "COMPLETED"}:
            return "SUCCESS"
        if status in {"RETRY", "RETRY_PENDING"}:
            return "RETRY_PENDING"
        if status in {"FAILED", "ERROR"}:
            return "FAILED"
        return status or "UNKNOWN"

    if status in {"", "NOT_STARTED", "PENDING", "QUEUED"}:
        return "PENDING"
    if status in {"IN_PROGRESS", "PROCESSING"}:
        return "PROCESSING"
    if status in {"COMPLETED", "SUCCESS", "DONE"}:
        return "SUCCESS"
    if status in {"RETRY", "RETRY_PENDING"}:
        return "RETRY_PENDING"
    if status in {"FAILED", "ERROR"}:
        return "FAILED"
    return status or "UNKNOWN"


def _operation_processing_records() -> list[dict[str, Any]]:
    raw_rows = repository.fetch_operation_processing_rows(
        limit=MAX_PROCESS_SOURCE_ROWS
    )
    rows: list[dict[str, Any]] = []

    for raw in raw_rows:
        operation_queue_id = raw.get("operation_queue_id")
        thread_id = raw.get("channel_thread_id")

        conversion_started = raw.get("conversion_started_on")
        conversion_completed = raw.get("conversion_completed_on")
        conversion_status = _normalize_process_status(
            raw.get("conversion_status"),
            "CONVERSION",
        )

        rows.append(
            {
                "id": f"CONV-{raw.get('id')}",
                "execution_id": raw.get("conversion_request_id") or f"CONV-{raw.get('id')}",
                "process_code": "QUEUE_CONVERSION",
                "process_name": "Queue Conversion",
                "module_name": "QUEUE_MANAGEMENT",
                "reference_type": "OPERATION_QUEUE",
                "reference_id": operation_queue_id,
                "parent_reference_type": "CHANNEL_THREAD",
                "parent_reference_id": thread_id,
                "process_status": conversion_status,
                "attempt_no": 1 if conversion_started else 0,
                "started_on": conversion_started,
                "completed_on": conversion_completed,
                "duration_ms": _duration_ms_between(
                    conversion_started,
                    conversion_completed,
                ),
                "error_message": raw.get("conversion_error"),
                "created_on": raw.get("created_on"),
                "updated_on": raw.get("updated_on"),
            }
        )

        email_status = _normalize_process_status(raw.get("email_status"), "EMAIL")
        email_sent_on = raw.get("email_sent_on")

        rows.append(
            {
                "id": f"EMAIL-{raw.get('id')}",
                "execution_id": f"EMAIL-{raw.get('id')}",
                "process_code": "QUEUE_EMAIL",
                "process_name": "Queue Email Delivery",
                "module_name": "QUEUE_MANAGEMENT",
                "reference_type": "OPERATION_QUEUE",
                "reference_id": operation_queue_id,
                "parent_reference_type": "CHANNEL_THREAD",
                "parent_reference_id": thread_id,
                "process_status": email_status,
                "attempt_no": _safe_int(raw.get("email_attempt_count")),
                "started_on": raw.get("updated_on") if email_status == "PROCESSING" else None,
                "completed_on": email_sent_on,
                "duration_ms": None,
                "error_message": raw.get("email_error"),
                "created_on": raw.get("created_on"),
                "updated_on": raw.get("updated_on"),
            }
        )

    return rows


def _manual_review_process_records() -> list[dict[str, Any]]:
    queue_context = get_queue_monitor_context({"queue_code": "MANUAL_REVIEW"})
    rows: list[dict[str, Any]] = []

    for queue_row in queue_context.get("queue_rows", []):
        if queue_row.get("status") == "COMPLETED":
            continue

        rows.append(
            {
                "id": f"MR-{queue_row.get('record_id')}",
                "execution_id": f"MR-{queue_row.get('record_id')}",
                "process_code": "MANUAL_REVIEW",
                "process_name": "Manual Review",
                "module_name": "QUEUE_MANAGEMENT",
                "reference_type": queue_row.get("reference_type"),
                "reference_id": queue_row.get("reference_id"),
                "parent_reference_type": "CHANNEL_THREAD",
                "parent_reference_id": queue_row.get("channel_thread_id"),
                "process_status": "MANUAL_REVIEW",
                "attempt_no": 0,
                "started_on": queue_row.get("created_on"),
                "completed_on": None,
                "duration_ms": None,
                "error_message": queue_row.get("remarks"),
                "created_on": queue_row.get("created_on"),
                "updated_on": queue_row.get("last_attempt_on"),
            }
        )

    return rows


def _filter_process_rows(
    rows: list[dict[str, Any]],
    filters: dict[str, Any],
) -> list[dict[str, Any]]:
    q = str(filters.get("q") or "").strip().lower()
    module = _safe_upper(filters.get("module"))
    status = _safe_upper(filters.get("status"))
    from_time = _parse_filter_datetime(filters.get("from_time"))
    to_time = _parse_filter_datetime(filters.get("to_time"))

    filtered: list[dict[str, Any]] = []

    for row in rows:
        if q:
            haystack = " ".join(
                str(row.get(key) or "")
                for key in (
                    "execution_id",
                    "process_name",
                    "process_code",
                    "reference_id",
                    "parent_reference_id",
                    "error_message",
                )
            ).lower()
            if q not in haystack:
                continue

        if module and _safe_upper(row.get("module_name")) != module:
            continue

        if status and _safe_upper(row.get("process_status")) != status:
            continue

        compare_time = row.get("started_on") or row.get("created_on")
        if from_time and isinstance(compare_time, datetime) and compare_time < from_time:
            continue
        if to_time and isinstance(compare_time, datetime) and compare_time > to_time:
            continue

        filtered.append(row)

    return filtered


def _process_summary_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    definitions = [
        ("QUEUE_CONVERSION", "Queue Conversion", "QUEUE_MANAGEMENT"),
        ("QUEUE_EMAIL", "Queue Email Delivery", "QUEUE_MANAGEMENT"),
        ("MANUAL_REVIEW", "Manual Review", "QUEUE_MANAGEMENT"),
    ]

    summary_rows: list[dict[str, Any]] = []

    for process_code, process_name, module_name in definitions:
        process_rows = [row for row in rows if row.get("process_code") == process_code]
        if not process_rows:
            continue

        pending_count = sum(1 for row in process_rows if row.get("process_status") == "PENDING")
        processing_count = sum(1 for row in process_rows if row.get("process_status") == "PROCESSING")
        success_count = sum(1 for row in process_rows if row.get("process_status") == "SUCCESS")
        retry_pending_count = sum(1 for row in process_rows if row.get("process_status") == "RETRY_PENDING")
        manual_review_count = sum(1 for row in process_rows if row.get("process_status") == "MANUAL_REVIEW")
        failed_count = sum(1 for row in process_rows if row.get("process_status") == "FAILED")

        pending_ages: list[int] = []
        durations: list[int] = []

        for row in process_rows:
            if row.get("process_status") in {
                "PENDING",
                "PROCESSING",
                "RETRY_PENDING",
                "MANUAL_REVIEW",
            }:
                started = row.get("started_on") or row.get("created_on")
                age = _minutes_since(started)
                if age is not None:
                    pending_ages.append(age)

            if row.get("duration_ms") is not None:
                durations.append(_safe_int(row.get("duration_ms")))

        health_status = "HEALTHY"
        if failed_count > 0 or retry_pending_count > 0:
            health_status = "WARNING"
        if processing_count > 0:
            health_status = "RUNNING"

        summary_rows.append(
            {
                "process_code": process_code,
                "process_name": process_name,
                "module_name": module_name,
                "pending_count": pending_count,
                "processing_count": processing_count,
                "success_count": success_count,
                "retry_pending_count": retry_pending_count,
                "manual_review_count": manual_review_count,
                "failed_count": failed_count,
                "oldest_pending_minutes": max(pending_ages) if pending_ages else None,
                "avg_duration_ms": (
                    int(round(sum(durations) / len(durations))) if durations else None
                ),
                "health_status": health_status,
            }
        )

    return summary_rows


def get_process_monitor_context(
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    filters = dict(filters or {})

    all_rows = _operation_processing_records() + _manual_review_process_records()
    all_rows.sort(
        key=lambda item: _sortable_datetime(
            item.get("started_on") or item.get("created_on")
        ),
        reverse=True,
    )

    rows = _filter_process_rows(all_rows, filters)
    summary_rows = _process_summary_rows(rows)

    summary = {
        "pending": sum(1 for row in rows if row.get("process_status") == "PENDING"),
        "processing": sum(1 for row in rows if row.get("process_status") == "PROCESSING"),
        "retry_pending": sum(1 for row in rows if row.get("process_status") == "RETRY_PENDING"),
        "manual_review": sum(1 for row in rows if row.get("process_status") == "MANUAL_REVIEW"),
        "failed": sum(1 for row in rows if row.get("process_status") == "FAILED"),
    }

    return {
        "summary": summary,
        "process_summary_rows": summary_rows,
        "process_rows": rows[:MAX_VISIBLE_ROWS],
        "filters": filters,
        "module_options": sorted(
            {row.get("module_name") for row in all_rows if row.get("module_name")}
        ),
        "status_options": [
            "PENDING",
            "PROCESSING",
            "SUCCESS",
            "RETRY_PENDING",
            "MANUAL_REVIEW",
            "FAILED",
        ],
        "pagination": None,
    }


# -----------------------------------------------------------------------------
# System health
# -----------------------------------------------------------------------------

def get_system_health_context() -> dict[str, Any]:
    checked_on = datetime.now()

    heartbeat = repository.fetch_latest_heartbeat()
    heartbeat_status, heartbeat_age = _heartbeat_state(heartbeat)

    jobs = repository.fetch_jobs()
    latest_runs = repository.fetch_latest_runs_by_job()
    last_success_map = repository.fetch_last_success_by_job()

    database_check = repository.check_database_health()

    health_rows: list[dict[str, Any]] = []

    database_status = "HEALTHY" if database_check.get("healthy") else "DOWN"
    health_rows.append(
        {
            "service_name": "MySQL Database",
            "service_code": "DATABASE",
            "category": "DATABASE",
            "status": database_status,
            "response_time_ms": database_check.get("response_time_ms"),
            "last_checked_on": database_check.get("checked_on") or checked_on,
            "last_success_on": (
                database_check.get("checked_on") if database_check.get("healthy") else None
            ),
            "failure_count": 0 if database_check.get("healthy") else 1,
            "status_detail": database_check.get("detail")
            or ("Database connection successful." if database_check.get("healthy") else "Database connection failed."),
        }
    )

    health_rows.append(
        {
            "service_name": "Background Scheduler",
            "service_code": "BACKGROUND_SCHEDULER",
            "category": "SCHEDULER",
            "status": heartbeat_status,
            "response_time_ms": None,
            "last_checked_on": (heartbeat or {}).get("last_heartbeat_on"),
            "last_success_on": (
                (heartbeat or {}).get("last_heartbeat_on")
                if heartbeat_status == "HEALTHY"
                else None
            ),
            "failure_count": 0 if heartbeat_status == "HEALTHY" else 1,
            "status_detail": (
                f"Heartbeat received {heartbeat_age} seconds ago."
                if heartbeat_age is not None
                else "No scheduler heartbeat has been recorded."
            ),
        }
    )

    job_rows = [
        _job_row(
            job,
            heartbeat,
            latest_runs.get(_safe_int(job.get("id"))),
            last_success_map.get(_safe_int(job.get("id"))),
        )
        for job in jobs
    ]

    for row in job_rows:
        execution_status = _safe_upper(row.get("execution_status"))
        detail_parts = [f"Latest execution: {execution_status}."]

        if row.get("duration_ms") is not None:
            detail_parts.append(f"Runtime: {row.get('duration_ms')} ms.")
        if row.get("next_run_on") is not None:
            detail_parts.append(f"Next run: {row.get('next_run_on')}.")
        if row.get("latest_error_message"):
            detail_parts.append(str(row.get("latest_error_message")))

        health_rows.append(
            {
                "service_name": row.get("scheduler_name"),
                "service_code": row.get("scheduler_code"),
                "category": "SCHEDULED_JOB",
                "status": row.get("health_status"),
                "response_time_ms": row.get("duration_ms"),
                "last_checked_on": row.get("last_run_on"),
                "last_success_on": row.get("last_success_on"),
                "failure_count": row.get("failure_count"),
                "status_detail": " ".join(detail_parts),
            }
        )

    statuses = [_safe_upper(row.get("status")) or "UNKNOWN" for row in health_rows]
    down_statuses = {"DOWN", "FAILED", "STOPPED", "STALE", "MISSING"}
    warning_statuses = {"WARNING", "PARTIAL", "UNKNOWN", "NEVER_RUN"}

    if any(status in down_statuses for status in statuses):
        overall_status = "DOWN"
    elif any(status in warning_statuses for status in statuses):
        overall_status = "WARNING"
    else:
        overall_status = "HEALTHY"

    unhealthy_names = [
        row.get("service_name")
        for row in health_rows
        if _safe_upper(row.get("status")) in down_statuses | warning_statuses
    ]

    if overall_status == "HEALTHY":
        overall_message = "Database, scheduler heartbeat and registered scheduler jobs are operating normally."
    elif unhealthy_names:
        overall_message = "Review: " + ", ".join(str(name) for name in unhealthy_names if name)
    else:
        overall_message = "System monitoring requires review."

    running_jobs = sum(
        1 for row in job_rows if _safe_upper(row.get("execution_status")) == "RUNNING"
    )

    infrastructure_metrics = [
        {
            "metric_code": "SCHEDULER_JOBS",
            "label": "Scheduler Jobs",
            "value": len(jobs),
            "unit": None,
        },
        {
            "metric_code": "ACTIVE_JOBS",
            "label": "Active Jobs",
            "value": sum(
                1
                for job in jobs
                if _safe_upper(job.get("active_flag") or "Y") == "Y"
            ),
            "unit": None,
        },
        {
            "metric_code": "RUNNING_JOBS",
            "label": "Running Now",
            "value": running_jobs,
            "unit": None,
        },
        {
            "metric_code": "DB_RESPONSE_TIME",
            "label": "Database Response",
            "value": database_check.get("response_time_ms"),
            "unit": "ms" if database_check.get("response_time_ms") is not None else None,
        },
        {
            "metric_code": "SCHEDULER_PROCESS_ID",
            "label": "Scheduler Process ID",
            "value": (heartbeat or {}).get("process_id"),
            "unit": None,
        },
        {
            "metric_code": "SCHEDULER_SERVER",
            "label": "Scheduler Server",
            "value": (heartbeat or {}).get("server_name"),
            "unit": None,
        },
    ]

    return {
        "health_summary": {
            "overall_status": overall_status,
            "message": overall_message,
            "checked_on": checked_on,
            "total_checks": len(health_rows),
            "healthy_count": sum(
                1
                for status in statuses
                if status in {"HEALTHY", "RUNNING", "SUCCESS"}
            ),
            "warning_count": sum(1 for status in statuses if status in warning_statuses),
            "down_count": sum(1 for status in statuses if status in down_statuses),
            "scheduler_heartbeat_age_seconds": heartbeat_age,
        },
        "health_rows": health_rows,
        "infrastructure_metrics": infrastructure_metrics,
    }


# -----------------------------------------------------------------------------
# Operations Control Center
# -----------------------------------------------------------------------------

def get_operations_control_center_context() -> dict[str, Any]:
    scheduler_context = get_scheduler_monitor_context()
    execution_context = get_execution_history_context()
    health_context = get_system_health_context()
    queue_context = get_queue_monitor_context()
    process_context = get_process_monitor_context()

    today_summary = repository.fetch_today_run_summary()

    executions_today = _safe_int(today_summary.get("total"))
    success_today = _safe_int(today_summary.get("success"))
    partial_today = _safe_int(today_summary.get("partial_count"))
    failed_today = _safe_int(today_summary.get("failed"))
    completed_today = success_today + partial_today + failed_today

    success_rate = None
    if completed_today > 0:
        success_rate = round((success_today / completed_today) * 100, 1)

    queue_summary_rows = queue_context.get("queue_summary_rows", [])
    queue_backlog = sum(
        _safe_int(row.get("pending_count"))
        + _safe_int(row.get("processing_count"))
        + _safe_int(row.get("retry_pending_count"))
        for row in queue_summary_rows
    )

    queue_ages = [
        _safe_int(row.get("oldest_pending_minutes"))
        for row in queue_summary_rows
        if row.get("oldest_pending_minutes") is not None
    ]
    oldest_backlog_minutes = max(queue_ages) if queue_ages else None

    recent_executions = execution_context["execution_rows"][:10]
    failed_executions = [
        row
        for row in execution_context["execution_rows"]
        if _safe_upper(row.get("execution_status")) == "FAILED"
    ][:10]

    recent_errors = [
        {
            **row,
            "occurred_on": row.get("completed_on") or row.get("started_on"),
            "severity": "ERROR",
            "resolution_status": "OPEN",
            "error_message": row.get("error_message")
            or row.get("result_message")
            or "Scheduler execution failed.",
            "reference_type": "SCHEDULER_RUN",
            "reference_id": row.get("execution_id"),
        }
        for row in failed_executions
    ]

    health_summary = health_context["health_summary"]

    monitoring_summary = {
        # Keys used by operations_control_center.html
        "overall_status": health_summary.get("overall_status") or "UNKNOWN",
        "overall_message": health_summary.get("message"),
        "last_refreshed_on": datetime.now(),
        "active_jobs": scheduler_context["summary"].get("enabled", 0),
        "total_jobs": scheduler_context["summary"].get("registered", 0),
        "running_jobs": scheduler_context["summary"].get("running", 0),
        "failed_today": failed_today,
        "queue_backlog": queue_backlog,
        "oldest_backlog_minutes": oldest_backlog_minutes,
        "success_rate": success_rate,
        "executions_today": executions_today,
        "critical_incidents": _safe_int(health_summary.get("down_count")),

        # Backward-compatible aliases for any older template fragments.
        "scheduler_registered": scheduler_context["summary"].get("registered", 0),
        "scheduler_running": scheduler_context["summary"].get("running", 0),
        "scheduler_failed": scheduler_context["summary"].get("failed", 0),
        "system_status": health_summary.get("overall_status") or "UNKNOWN",
        "execution_total": execution_context["summary"].get("total", 0),
        "execution_failed": execution_context["summary"].get("failed", 0),
    }

    return {
        "monitoring_summary": monitoring_summary,
        "scheduler_rows": scheduler_context["scheduler_rows"],
        "process_rows": process_context.get("process_summary_rows", []),
        "recent_executions": recent_executions,
        "service_rows": health_context["health_rows"],
        "queue_rows": queue_summary_rows,
        "recent_errors": recent_errors,
    }
