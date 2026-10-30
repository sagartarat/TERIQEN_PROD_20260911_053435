"""Routes for TERIQEN System Monitoring.

Read-only monitoring screens are backed by system_monitoring_service.  Existing
placeholder action routes (run/toggle/retry/resolve) are intentionally preserved
until their write-side behaviour is explicitly connected.
"""

from __future__ import annotations

from flask import abort, redirect, render_template, request, session, url_for
from flask_login import current_user

from app.system_monitoring import system_monitoring_bp
from app.common.access_control import enforce_screen_permission
from app.system_monitoring.system_monitoring_service import (
    get_execution_detail_context,
    get_execution_history_context,
    get_operations_control_center_context,
    get_process_monitor_context,
    get_queue_monitor_context,
    get_scheduler_detail_context,
    get_scheduler_monitor_context,
    get_system_health_context,
)


_ENDPOINT_SCREEN = {
    "system_monitoring.operations_control_center": "SYSTEM_MONITORING_DASHBOARD",
    "system_monitoring.scheduler_monitor": "SCHEDULER_MONITOR",
    "system_monitoring.scheduler_detail": "SCHEDULER_MONITOR",
    "system_monitoring.run_scheduler": "SCHEDULER_MONITOR",
    "system_monitoring.toggle_scheduler": "SCHEDULER_MONITOR",
    "system_monitoring.process_monitor": "PROCESS_MONITOR",
    "system_monitoring.process_detail": "PROCESS_MONITOR",
    "system_monitoring.queue_monitor": "QUEUE_MONITOR",
    "system_monitoring.queue_detail": "QUEUE_MONITOR",
    "system_monitoring.execution_history": "EXECUTION_HISTORY",
    "system_monitoring.execution_detail": "EXECUTION_HISTORY",
    "system_monitoring.error_monitor": "ERROR_MONITOR",
    "system_monitoring.error_detail": "ERROR_MONITOR",
    "system_monitoring.retry_error": "ERROR_MONITOR",
    "system_monitoring.resolve_error": "ERROR_MONITOR",
    "system_monitoring.system_health": "SYSTEM_HEALTH",
}


@system_monitoring_bp.before_request
def _system_monitoring_access():
    endpoint = request.endpoint or ""
    required = _ENDPOINT_SCREEN.get(endpoint, "SYSTEM_MONITORING_DASHBOARD")
    action = "edit" if endpoint in {
        "system_monitoring.run_scheduler",
        "system_monitoring.toggle_scheduler",
        "system_monitoring.retry_error",
        "system_monitoring.resolve_error",
    } else "view"
    return enforce_screen_permission(required, action)


def _request_filters(*names: str) -> dict[str, str]:
    """Return only supported GET filter values for one monitoring screen."""
    return {
        name: str(request.args.get(name) or "").strip()
        for name in names
    }


# ============================================================
# OPERATIONS CONTROL CENTER
# ============================================================

@system_monitoring_bp.route("/", methods=["GET"])
def operations_control_center():
    context = get_operations_control_center_context()
    return render_template(
        "system_monitoring/operations_control_center.html",
        **context,
    )


# ============================================================
# SCHEDULER MONITOR
# ============================================================

@system_monitoring_bp.route("/schedulers", methods=["GET"])
def scheduler_monitor():
    filters = _request_filters(
        "q",
        "module",
        "status",
        "enabled",
        "heartbeat",
    )
    context = get_scheduler_monitor_context(filters=filters)
    return render_template(
        "system_monitoring/scheduler_monitor.html",
        **context,
    )


# ============================================================
# SCHEDULER DETAIL
# ============================================================

@system_monitoring_bp.route(
    "/schedulers/<int:scheduler_id>",
    methods=["GET"],
)
def scheduler_detail(scheduler_id):
    context = get_scheduler_detail_context(scheduler_id)
    return render_template(
        "system_monitoring/scheduler_detail.html",
        **context,
    )


# ============================================================
# RUN SCHEDULER
# ============================================================

@system_monitoring_bp.route(
    "/schedulers/<int:scheduler_id>/run",
    methods=["POST"],
)
def run_scheduler(scheduler_id):
    # Existing behaviour preserved.  Manual scheduler execution can be wired
    # separately without affecting read-only monitoring.
    return redirect(
        url_for(
            "system_monitoring.scheduler_detail",
            scheduler_id=scheduler_id,
        )
    )


# ============================================================
# ENABLE / DISABLE SCHEDULER
# ============================================================

@system_monitoring_bp.route(
    "/schedulers/<int:scheduler_id>/toggle",
    methods=["POST"],
)
def toggle_scheduler(scheduler_id):
    # Existing behaviour preserved.  Database-controlled enable/disable writes
    # can be connected separately.
    return redirect(
        url_for(
            "system_monitoring.scheduler_detail",
            scheduler_id=scheduler_id,
        )
    )


# ============================================================
# PROCESS MONITOR
# ============================================================

@system_monitoring_bp.route("/processes", methods=["GET"])
def process_monitor():
    filters = _request_filters(
        "q",
        "module",
        "status",
        "from_time",
        "to_time",
    )
    context = get_process_monitor_context(filters=filters)
    return render_template(
        "system_monitoring/process_monitor.html",
        **context,
    )


# ============================================================
# QUEUE MONITOR
# ============================================================

@system_monitoring_bp.route("/queues", methods=["GET"])
def queue_monitor():
    filters = _request_filters(
        "queue_code",
        "status",
        "reference",
        "min_age",
        "stuck",
    )
    context = get_queue_monitor_context(filters=filters)
    return render_template(
        "system_monitoring/queue_monitor.html",
        **context,
    )


# ============================================================
# EXECUTION HISTORY
# ============================================================

@system_monitoring_bp.route("/executions", methods=["GET"])
def execution_history():
    filters = _request_filters(
        "scheduler_id",
        "status",
        "from_time",
        "to_time",
        "execution_id",
    )
    context = get_execution_history_context(filters=filters)
    return render_template(
        "system_monitoring/execution_history.html",
        **context,
    )


# ============================================================
# EXECUTION DETAIL
# ============================================================

@system_monitoring_bp.route(
    "/executions/<int:execution_id>",
    methods=["GET"],
)
def execution_detail(execution_id):
    context = get_execution_detail_context(execution_id)
    return render_template(
        "system_monitoring/execution_detail.html",
        **context,
    )


# ============================================================
# ERROR MONITOR
# ============================================================

@system_monitoring_bp.route("/errors", methods=["GET"])
def error_monitor():
    # Error-monitor persistence/actions are not part of this correction.  Keep
    # the existing screen contract unchanged until an error master/log is wired.
    return render_template(
        "system_monitoring/error_monitor.html",
        summary={
            "open": 0,
            "critical": 0,
            "error": 0,
            "warning": 0,
            "resolved_today": 0,
        },
        error_rows=[],
        filters={},
        module_options=[],
        severity_options=[
            "INFO",
            "WARNING",
            "ERROR",
            "CRITICAL",
        ],
        resolution_status_options=[
            "OPEN",
            "RESOLVED",
        ],
        pagination=None,
    )


# ============================================================
# ERROR DETAIL
# ============================================================

@system_monitoring_bp.route(
    "/errors/<int:error_id>",
    methods=["GET"],
)
def error_detail(error_id):
    error = {
        "id": error_id,
        "severity": "ERROR",
        "resolution_status": "OPEN",
        "error_code": None,
        "error_message": None,
        "module_name": None,
        "process_name": None,
        "scheduler_name": None,
        "reference_type": None,
        "reference_id": None,
        "execution_id": None,
        "retry_count": 0,
        "last_retry_on": None,
        "occurred_on": None,
        "resolved_by": None,
        "resolved_on": None,
        "stack_trace": None,
        "resolution_notes": None,
        "can_retry": False,
        "can_resolve": False,
    }

    return render_template(
        "system_monitoring/error_detail.html",
        error=error,
    )


# ============================================================
# RETRY ERROR
# ============================================================

@system_monitoring_bp.route(
    "/errors/<int:error_id>/retry",
    methods=["POST"],
)
def retry_error(error_id):
    # Existing placeholder behaviour preserved.
    return redirect(
        url_for(
            "system_monitoring.error_detail",
            error_id=error_id,
        )
    )


# ============================================================
# RESOLVE ERROR
# ============================================================

@system_monitoring_bp.route(
    "/errors/<int:error_id>/resolve",
    methods=["POST"],
)
def resolve_error(error_id):
    # Existing placeholder behaviour preserved.
    return redirect(
        url_for(
            "system_monitoring.error_detail",
            error_id=error_id,
        )
    )


# ============================================================
# SYSTEM HEALTH
# ============================================================

@system_monitoring_bp.route("/health", methods=["GET"])
def system_health():
    context = get_system_health_context()
    return render_template(
        "system_monitoring/system_health.html",
        **context,
    )
