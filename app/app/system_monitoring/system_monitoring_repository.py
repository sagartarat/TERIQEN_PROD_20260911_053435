"""Database repository for TERIQEN System Monitoring.

This module owns SQL for the existing scheduler monitoring tables:
    scheduler_job_master
    scheduler_run_log
    scheduler_heartbeat

It also exposes read-only helpers for operational queue/process tables used by the
System Monitoring screens. Business-processing writes remain in their existing
modules.

The code is intentionally backward-compatible with the table structures shared
on 07-Aug-2026. Optional monitoring source tables are read defensively so a missing
source does not prevent the monitoring pages from loading.
"""

from __future__ import annotations

import os
import socket
import threading
import uuid
from time import perf_counter
from datetime import datetime
from typing import Any

from app.common.db import get_db_connection
from app.common.logger import get_logger


repository_logger = get_logger("system_monitoring_repository")


SCHEDULER_JOB_MASTER = "scheduler_job_master"
SCHEDULER_RUN_LOG = "scheduler_run_log"
SCHEDULER_HEARTBEAT = "scheduler_heartbeat"


def _close(connection=None, cursor=None) -> None:
    if cursor is not None:
        try:
            cursor.close()
        except Exception:
            pass
    if connection is not None:
        try:
            connection.close()
        except Exception:
            pass


def _table_columns(cursor, table_name: str) -> set[str]:
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
    return {row["Field"] for row in (cursor.fetchall() or [])}


def _runtime_identity() -> dict[str, str]:
    return {
        "server_name": socket.gethostname(),
        "process_id": str(os.getpid()),
        "thread_name": threading.current_thread().name,
        "thread_id": str(threading.get_ident()),
    }


def _build_run_id(job_code: str) -> str:
    stamp = datetime.now().strftime("%Y%m%d%H%M%S%f")
    suffix = uuid.uuid4().hex[:8].upper()
    return f"{job_code}-{stamp}-{suffix}"


def register_or_update_job(definition: dict[str, Any]) -> int | None:
    """Insert a scheduler master row if missing, otherwise sync technical metadata.

    Administrator-controlled fields on an existing row are deliberately preserved,
    especially active_flag, max_runtime_seconds and retry_count.
    """

    job_code = str(definition.get("job_code") or "").strip().upper()
    if not job_code:
        raise ValueError("job_code is required")

    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        columns = _table_columns(cursor, SCHEDULER_JOB_MASTER)

        cursor.execute(
            "SELECT * FROM scheduler_job_master WHERE job_code = %s LIMIT 1",
            (job_code,),
        )
        existing = cursor.fetchone()

        technical_values = {
            "job_name": definition.get("job_name"),
            "job_group": definition.get("job_group"),
            "module_code": definition.get("module_code"),
            "job_description": definition.get("job_description"),
            "scheduler_type": definition.get("scheduler_type") or "INTERVAL",
            "interval_value": definition.get("interval_value"),
            "interval_unit": definition.get("interval_unit"),
            "cron_expression": definition.get("cron_expression"),
            "apscheduler_job_id": definition.get("apscheduler_job_id"),
            "job_function": definition.get("job_function"),
        }

        if existing:
            set_parts: list[str] = []
            values: list[Any] = []

            for field, value in technical_values.items():
                if field in columns:
                    set_parts.append(f"`{field}` = %s")
                    values.append(value)

            if "updated_by" in columns:
                set_parts.append("updated_by = %s")
                values.append("SYSTEM")

            if set_parts:
                values.append(existing["id"])
                cursor.execute(
                    f"UPDATE scheduler_job_master SET {', '.join(set_parts)} WHERE id = %s",
                    tuple(values),
                )

            connection.commit()
            return int(existing["id"])

        insert_values: dict[str, Any] = {
            "job_code": job_code,
            **technical_values,
            "active_flag": definition.get("active_flag") or "Y",
            "created_by": "SYSTEM",
            "updated_by": "SYSTEM",
            "max_runtime_seconds": definition.get("max_runtime_seconds"),
            "retry_count": definition.get("retry_count", 0),
        }
        insert_values = {
            field: value
            for field, value in insert_values.items()
            if field in columns
        }

        fields = list(insert_values.keys())
        placeholders = ", ".join(["%s"] * len(fields))
        cursor.execute(
            f"""
            INSERT INTO scheduler_job_master ({', '.join(f'`{f}`' for f in fields)})
            VALUES ({placeholders})
            """,
            tuple(insert_values[field] for field in fields),
        )
        connection.commit()
        return int(cursor.lastrowid)

    except Exception:
        if connection:
            connection.rollback()
        repository_logger.exception("SCHEDULER_JOB_REGISTER_FAILED job_code=%s", job_code)
        raise
    finally:
        _close(connection, cursor)


def get_job_by_code(job_code: str) -> dict[str, Any] | None:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM scheduler_job_master WHERE job_code = %s LIMIT 1",
            (str(job_code or "").strip().upper(),),
        )
        return cursor.fetchone()
    finally:
        _close(connection, cursor)


def update_job_next_run(job_code: str, next_run_on: datetime | None) -> None:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE scheduler_job_master
            SET next_run_on = %s,
                updated_by = COALESCE(updated_by, 'SYSTEM')
            WHERE job_code = %s
            """,
            (next_run_on, str(job_code or "").strip().upper()),
        )
        connection.commit()
    except Exception:
        if connection:
            connection.rollback()
        repository_logger.exception(
            "SCHEDULER_NEXT_RUN_UPDATE_FAILED job_code=%s", job_code
        )
        raise
    finally:
        _close(connection, cursor)


def start_run(job_code: str, trigger_type: str = "SCHEDULED") -> dict[str, Any]:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        job_code = str(job_code or "").strip().upper()
        trigger_type = str(trigger_type or "SCHEDULED").strip().upper()
        if trigger_type not in {"SCHEDULED", "MANUAL", "RETRY"}:
            trigger_type = "SCHEDULED"

        cursor.execute(
            "SELECT id FROM scheduler_job_master WHERE job_code = %s LIMIT 1",
            (job_code,),
        )
        job = cursor.fetchone()
        if not job:
            raise LookupError(f"scheduler_job_master row not found for {job_code}")

        run_columns = _table_columns(cursor, SCHEDULER_RUN_LOG)
        identity = _runtime_identity()
        run_id = _build_run_id(job_code)
        start_time = datetime.now()

        insert_values: dict[str, Any] = {
            "job_id": job["id"],
            "run_id": run_id,
            "trigger_type": trigger_type,
            "start_time": start_time,
            "run_status": "RUNNING",
            "server_name": identity["server_name"],
            "process_id": identity["process_id"],
            "thread_name": identity["thread_name"],
            "thread_id": identity["thread_id"],
            "remarks": "Scheduler execution started.",
        }
        insert_values = {
            field: value
            for field, value in insert_values.items()
            if field in run_columns
        }

        fields = list(insert_values.keys())
        cursor.execute(
            f"""
            INSERT INTO scheduler_run_log ({', '.join(f'`{f}`' for f in fields)})
            VALUES ({', '.join(['%s'] * len(fields))})
            """,
            tuple(insert_values[field] for field in fields),
        )
        run_pk = int(cursor.lastrowid)

        cursor.execute(
            """
            UPDATE scheduler_job_master
            SET last_run_on = %s,
                last_run_status = 'RUNNING',
                total_run_count = total_run_count + 1,
                updated_by = COALESCE(updated_by, 'SYSTEM')
            WHERE id = %s
            """,
            (start_time, job["id"]),
        )

        connection.commit()
        return {
            "id": run_pk,
            "run_id": run_id,
            "job_id": int(job["id"]),
            "job_code": job_code,
            "start_time": start_time,
        }

    except Exception:
        if connection:
            connection.rollback()
        repository_logger.exception("SCHEDULER_RUN_START_FAILED job_code=%s", job_code)
        raise
    finally:
        _close(connection, cursor)


def finish_run(
    run_id: str,
    run_status: str,
    records_read: int = 0,
    records_processed: int = 0,
    records_success: int = 0,
    records_failed: int = 0,
    records_skipped: int = 0,
    remarks: str | None = None,
    error_code: str | None = None,
    error_message: str | None = None,
    stack_trace: str | None = None,
) -> None:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        run_status = str(run_status or "SUCCESS").strip().upper()
        if run_status not in {"RUNNING", "SUCCESS", "FAILED", "PARTIAL"}:
            run_status = "FAILED"

        cursor.execute(
            "SELECT * FROM scheduler_run_log WHERE run_id = %s LIMIT 1",
            (run_id,),
        )
        run_row = cursor.fetchone()
        if not run_row:
            raise LookupError(f"scheduler_run_log row not found for run_id={run_id}")

        end_time = datetime.now()
        start_time = run_row.get("start_time") or end_time
        duration_seconds = max((end_time - start_time).total_seconds(), 0.0)

        run_columns = _table_columns(cursor, SCHEDULER_RUN_LOG)
        update_values: dict[str, Any] = {
            "end_time": end_time,
            "run_status": run_status,
            "duration_seconds": round(duration_seconds, 2),
            "records_read": int(records_read or 0),
            "records_processed": int(records_processed or 0),
            "records_success": int(records_success or 0),
            "records_failed": int(records_failed or 0),
            "records_skipped": int(records_skipped or 0),
            "error_code": error_code,
            "error_message": error_message,
            "stack_trace": stack_trace,
            "remarks": str(remarks or "")[:1000] or None,
        }

        set_parts: list[str] = []
        values: list[Any] = []
        for field, value in update_values.items():
            if field in run_columns:
                set_parts.append(f"`{field}` = %s")
                values.append(value)

        values.append(run_id)
        cursor.execute(
            f"UPDATE scheduler_run_log SET {', '.join(set_parts)} WHERE run_id = %s",
            tuple(values),
        )

        success_increment = 1 if run_status == "SUCCESS" else 0
        failure_increment = 1 if run_status == "FAILED" else 0
        cursor.execute(
            """
            UPDATE scheduler_job_master
            SET last_run_status = %s,
                last_duration_seconds = %s,
                success_count = success_count + %s,
                failure_count = failure_count + %s,
                updated_by = COALESCE(updated_by, 'SYSTEM')
            WHERE id = %s
            """,
            (
                run_status,
                round(duration_seconds, 2),
                success_increment,
                failure_increment,
                run_row["job_id"],
            ),
        )

        connection.commit()

    except Exception:
        if connection:
            connection.rollback()
        repository_logger.exception("SCHEDULER_RUN_FINISH_FAILED run_id=%s", run_id)
        raise
    finally:
        _close(connection, cursor)


def upsert_heartbeat(
    scheduler_name: str,
    scheduler_status: str = "RUNNING",
    total_jobs: int = 0,
    active_jobs: int = 0,
    remarks: str | None = None,
) -> None:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        scheduler_name = str(scheduler_name or "TERIQEN_BACKGROUND_SCHEDULER").strip()
        scheduler_status = str(scheduler_status or "RUNNING").strip().upper()
        if scheduler_status not in {"RUNNING", "STOPPED", "ERROR"}:
            scheduler_status = "ERROR"

        identity = _runtime_identity()
        now_dt = datetime.now()

        cursor.execute(
            "SELECT * FROM scheduler_heartbeat WHERE scheduler_name = %s LIMIT 1",
            (scheduler_name,),
        )
        existing = cursor.fetchone()

        if existing:
            process_changed = str(existing.get("process_id") or "") != identity["process_id"]
            started_on = now_dt if process_changed else (existing.get("started_on") or now_dt)
            cursor.execute(
                """
                UPDATE scheduler_heartbeat
                SET server_name = %s,
                    process_id = %s,
                    scheduler_status = %s,
                    started_on = %s,
                    last_heartbeat_on = %s,
                    total_jobs = %s,
                    active_jobs = %s,
                    remarks = %s
                WHERE id = %s
                """,
                (
                    identity["server_name"],
                    identity["process_id"],
                    scheduler_status,
                    started_on,
                    now_dt,
                    int(total_jobs or 0),
                    int(active_jobs or 0),
                    str(remarks or "")[:1000] or None,
                    existing["id"],
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO scheduler_heartbeat
                (
                    scheduler_name,
                    server_name,
                    process_id,
                    scheduler_status,
                    started_on,
                    last_heartbeat_on,
                    total_jobs,
                    active_jobs,
                    remarks
                )
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    scheduler_name,
                    identity["server_name"],
                    identity["process_id"],
                    scheduler_status,
                    now_dt,
                    now_dt,
                    int(total_jobs or 0),
                    int(active_jobs or 0),
                    str(remarks or "")[:1000] or None,
                ),
            )

        connection.commit()

    except Exception:
        if connection:
            connection.rollback()
        repository_logger.exception(
            "SCHEDULER_HEARTBEAT_UPDATE_FAILED scheduler_name=%s", scheduler_name
        )
        raise
    finally:
        _close(connection, cursor)


def fetch_jobs() -> list[dict[str, Any]]:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute("SELECT * FROM scheduler_job_master ORDER BY id ASC")
        return cursor.fetchall() or []
    finally:
        _close(connection, cursor)


def fetch_job_by_id(scheduler_id: int) -> dict[str, Any] | None:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM scheduler_job_master WHERE id = %s LIMIT 1",
            (scheduler_id,),
        )
        return cursor.fetchone()
    finally:
        _close(connection, cursor)


def fetch_latest_heartbeat() -> dict[str, Any] | None:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT *
            FROM scheduler_heartbeat
            ORDER BY last_heartbeat_on DESC, id DESC
            LIMIT 1
            """
        )
        return cursor.fetchone()
    finally:
        _close(connection, cursor)


def fetch_run_logs(limit: int = 200) -> list[dict[str, Any]]:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                r.*,
                j.job_code,
                j.job_name,
                j.job_group,
                j.module_code
            FROM scheduler_run_log r
            INNER JOIN scheduler_job_master j
                ON j.id = r.job_id
            ORDER BY r.id DESC
            LIMIT %s
            """,
            (int(limit or 200),),
        )
        return cursor.fetchall() or []
    finally:
        _close(connection, cursor)


def fetch_runs_for_job(job_id: int, limit: int = 100) -> list[dict[str, Any]]:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT r.*
            FROM scheduler_run_log r
            WHERE r.job_id = %s
            ORDER BY r.id DESC
            LIMIT %s
            """,
            (job_id, int(limit or 100)),
        )
        return cursor.fetchall() or []
    finally:
        _close(connection, cursor)


def fetch_run_by_id(execution_id: int) -> dict[str, Any] | None:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                r.*,
                j.job_code,
                j.job_name,
                j.job_group,
                j.module_code
            FROM scheduler_run_log r
            INNER JOIN scheduler_job_master j
                ON j.id = r.job_id
            WHERE r.id = %s
            LIMIT 1
            """,
            (execution_id,),
        )
        return cursor.fetchone()
    finally:
        _close(connection, cursor)


def fetch_run_summary() -> dict[str, Any]:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN run_status = 'SUCCESS' THEN 1 ELSE 0 END) AS success,
                SUM(CASE WHEN run_status = 'PARTIAL' THEN 1 ELSE 0 END) AS partial_count,
                SUM(CASE WHEN run_status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                AVG(duration_seconds) AS avg_duration_seconds
            FROM scheduler_run_log
            """
        )
        return cursor.fetchone() or {}
    finally:
        _close(connection, cursor)


# -----------------------------------------------------------------------------
# Read helpers used by the System Monitoring dashboards
# -----------------------------------------------------------------------------

def _table_exists(cursor, table_name: str) -> bool:
    """Return whether a table exists in the current database."""
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def fetch_latest_runs_by_job() -> dict[int, dict[str, Any]]:
    """Return the most recent scheduler_run_log row for every registered job."""
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT r.*
            FROM scheduler_run_log r
            INNER JOIN (
                SELECT job_id, MAX(id) AS max_id
                FROM scheduler_run_log
                GROUP BY job_id
            ) latest
                ON latest.job_id = r.job_id
               AND latest.max_id = r.id
            """
        )
        rows = cursor.fetchall() or []
        result: dict[int, dict[str, Any]] = {}
        for row in rows:
            try:
                result[int(row["job_id"])] = row
            except Exception:
                continue
        return result
    finally:
        _close(connection, cursor)


def fetch_last_success_by_job() -> dict[int, datetime | None]:
    """Return the last successful completion timestamp for each scheduler job."""
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                job_id,
                MAX(COALESCE(end_time, start_time)) AS last_success_on
            FROM scheduler_run_log
            WHERE run_status = 'SUCCESS'
            GROUP BY job_id
            """
        )
        rows = cursor.fetchall() or []
        result: dict[int, datetime | None] = {}
        for row in rows:
            try:
                result[int(row["job_id"])] = row.get("last_success_on")
            except Exception:
                continue
        return result
    finally:
        _close(connection, cursor)


def fetch_today_run_summary() -> dict[str, Any]:
    """Return scheduler execution statistics for the current database day."""
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                COUNT(*) AS total,
                SUM(CASE WHEN run_status = 'SUCCESS' THEN 1 ELSE 0 END) AS success,
                SUM(CASE WHEN run_status = 'PARTIAL' THEN 1 ELSE 0 END) AS partial_count,
                SUM(CASE WHEN run_status = 'FAILED' THEN 1 ELSE 0 END) AS failed,
                SUM(CASE WHEN run_status = 'RUNNING' THEN 1 ELSE 0 END) AS running,
                AVG(CASE
                    WHEN run_status IN ('SUCCESS', 'PARTIAL', 'FAILED')
                    THEN duration_seconds
                    ELSE NULL
                END) AS avg_duration_seconds
            FROM scheduler_run_log
            WHERE start_time >= CURDATE()
              AND start_time < DATE_ADD(CURDATE(), INTERVAL 1 DAY)
            """
        )
        return cursor.fetchone() or {}
    finally:
        _close(connection, cursor)


def check_database_health() -> dict[str, Any]:
    """Perform a lightweight database round-trip for the System Health screen."""
    connection = cursor = None
    started = perf_counter()
    checked_on = datetime.now()
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute("SELECT 1")
        cursor.fetchone()
        elapsed_ms = max(int(round((perf_counter() - started) * 1000)), 0)
        return {
            "healthy": True,
            "response_time_ms": elapsed_ms,
            "checked_on": checked_on,
            "detail": "Database connection and query completed successfully.",
        }
    except Exception as exc:
        elapsed_ms = max(int(round((perf_counter() - started) * 1000)), 0)
        repository_logger.exception("SYSTEM_MONITORING_DATABASE_HEALTH_FAILED error=%s", exc)
        return {
            "healthy": False,
            "response_time_ms": elapsed_ms,
            "checked_on": checked_on,
            "detail": str(exc)[:500],
        }
    finally:
        _close(connection, cursor)


def _fetch_operational_rows(
    table_name: str,
    limit: int = 5000,
    preferred_order_columns: tuple[str, ...] = ("updated_on", "created_on", "id"),
) -> list[dict[str, Any]]:
    """Safely read recent rows from a whitelisted operational table.

    These reads are monitoring-only.  A missing optional source table must not make
    the System Monitoring pages unavailable.
    """
    allowed_tables = {
        "channel_message_log",
        "operation_queue_master",
        "operation_queue_processing",
    }
    if table_name not in allowed_tables:
        raise ValueError(f"Unsupported monitoring source table: {table_name}")

    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if not _table_exists(cursor, table_name):
            repository_logger.warning(
                "SYSTEM_MONITORING_SOURCE_TABLE_MISSING table=%s",
                table_name,
            )
            return []

        columns = _table_columns(cursor, table_name)
        order_columns = [
            column for column in preferred_order_columns if column in columns
        ]

        safe_limit = max(min(int(limit or 5000), 10000), 1)
        order_sql = ""
        if order_columns:
            order_sql = " ORDER BY " + ", ".join(
                f"`{column}` DESC" for column in order_columns
            )

        cursor.execute(
            f"SELECT * FROM `{table_name}`{order_sql} LIMIT %s",
            (safe_limit,),
        )
        return cursor.fetchall() or []

    except Exception as exc:
        repository_logger.exception(
            "SYSTEM_MONITORING_SOURCE_READ_FAILED table=%s error=%s",
            table_name,
            exc,
        )
        return []
    finally:
        _close(connection, cursor)


def fetch_channel_message_rows(limit: int = 5000) -> list[dict[str, Any]]:
    return _fetch_operational_rows(
        "channel_message_log",
        limit=limit,
        preferred_order_columns=("updated_on", "received_on", "created_on", "id"),
    )


def fetch_operation_queue_rows(limit: int = 5000) -> list[dict[str, Any]]:
    return _fetch_operational_rows(
        "operation_queue_master",
        limit=limit,
        preferred_order_columns=("updated_on", "created_on", "id"),
    )


def fetch_operation_processing_rows(limit: int = 5000) -> list[dict[str, Any]]:
    return _fetch_operational_rows(
        "operation_queue_processing",
        limit=limit,
        preferred_order_columns=("updated_on", "created_on", "id"),
    )
