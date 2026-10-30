"""
Alert Management dashboard service.

This module prepares all data required by the Alert Management dashboard:
- Summary counters
- Filter LOVs
- Active alert records
- Alert summary information
- Optional CSV export data

Supported alert references:
- PATIENT_WORKFLOW -> patient_wrkflw.ld_seqno
- OPERATION_QUEUE -> operation_queue_master.id

The dashboard reads from alert_instance as the source of truth.
"""

from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from app.common.db import get_db_connection
from app.common.logger import get_logger


alert_dashboard_logger = get_logger("alert_dashboard")


ACTIVE_ALERT_STATUSES = ("OPEN", "ACKNOWLEDGED", "SNOOZED")
ALLOWED_ALERT_STATUSES = (
    "OPEN",
    "ACKNOWLEDGED",
    "SNOOZED",
    "RESOLVED",
)
ALLOWED_SEVERITIES = ("WARNING", "CRITICAL")


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


def _safe_text(value: Any) -> str:
    if value is None:
        return ""
    return str(value).strip()


def _safe_int(value: Any, default: int = 0) -> int:
    try:
        if value is None or str(value).strip() == "":
            return default
        return int(value)
    except Exception:
        return default


def _normalise_upper(value: Any) -> str:
    return _safe_text(value).upper()


VALID_TIME_UNITS = {"MINUTES", "HOURS", "DAYS"}


def _normalise_time_unit(value: Any, default: str = "MINUTES") -> str:
    unit = _normalise_upper(value)
    return unit if unit in VALID_TIME_UNITS else default


def _parse_date(value: Any) -> date | None:
    if value is None or value == "":
        return None

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    text = str(value).strip()

    for fmt in (
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
    ):
        try:
            return datetime.strptime(text, fmt).date()
        except Exception:
            continue

    return None


def _parse_json(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return value

    if value is None:
        return {}

    try:
        parsed = json.loads(str(value))
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def _format_datetime(value: Any) -> str:
    if not value:
        return ""

    if isinstance(value, datetime):
        return value.strftime("%d-%b-%Y %H:%M:%S")

    if isinstance(value, date):
        return value.strftime("%d-%b-%Y")

    return _safe_text(value)


def _format_duration_minutes(value: Any) -> str:
    minutes = max(_safe_int(value, 0), 0)

    if minutes == 0:
        return "Immediate"

    days, remaining = divmod(minutes, 1440)
    hours, mins = divmod(remaining, 60)
    parts: list[str] = []

    if days:
        parts.append(f"{days} Day" + ("s" if days != 1 else ""))
    if hours:
        parts.append(f"{hours} Hour" + ("s" if hours != 1 else ""))
    if mins:
        parts.append(f"{mins} Minute" + ("s" if mins != 1 else ""))

    return " ".join(parts) or "Immediate"


def _build_alert_activity(row: dict[str, Any]) -> list[dict[str, str]]:
    activities: list[dict[str, Any]] = []

    def add_activity(
        activity_type: str,
        label: str,
        occurred_on: Any,
        *,
        actor: Any = None,
        detail: str = "",
    ) -> None:
        if not occurred_on:
            return

        activities.append({
            "activity_type": activity_type,
            "label": label,
            "actor": _safe_text(actor) or "SYSTEM",
            "occurred_on": _format_datetime(occurred_on),
            "detail": _safe_text(detail),
            "_sort_value": occurred_on,
        })

    add_activity(
        "RESOLVED",
        "Alert resolved",
        row.get("resolved_on"),
        actor=row.get("resolved_by"),
        detail=row.get("resolution_reason") or "",
    )

    snooze_detail = ""
    if row.get("snoozed_until"):
        snooze_detail = (
            "Snoozed until " + _format_datetime(row.get("snoozed_until"))
        )
    add_activity(
        "SNOOZED",
        "Alert snoozed",
        row.get("snoozed_on"),
        actor=row.get("snoozed_by"),
        detail=snooze_detail,
    )

    add_activity(
        "ACKNOWLEDGED",
        "Alert acknowledged",
        row.get("acknowledged_on"),
        actor=row.get("acknowledged_by"),
    )

    add_activity(
        "NOTIFIED",
        "Alert notification sent",
        row.get("last_notified_on"),
        actor="SYSTEM",
        detail=(
            f"Notification count: {_safe_int(row.get('notification_count'), 0)}"
        ),
    )

    add_activity(
        "CRITICAL",
        "Critical threshold reached",
        row.get("critical_triggered_on"),
        actor="SYSTEM",
    )

    add_activity(
        "WARNING",
        "Warning threshold reached",
        row.get("warning_triggered_on"),
        actor="SYSTEM",
    )

    add_activity(
        "CREATED",
        "Alert created",
        row.get("created_on"),
        actor="SYSTEM",
    )

    def sort_key(item: dict[str, Any]):
        value = item.get("_sort_value")
        if isinstance(value, datetime):
            return value
        if isinstance(value, date):
            return datetime.combine(value, datetime.min.time())
        return datetime.min

    activities.sort(key=sort_key, reverse=True)

    for item in activities:
        item.pop("_sort_value", None)

    return activities


def _get_table_columns(cursor, table_name: str) -> set[str]:
    try:
        cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
        return {row["Field"] for row in (cursor.fetchall() or [])}
    except Exception:
        return set()


def _table_exists(cursor, table_name: str) -> bool:
    cursor.execute("SHOW TABLES LIKE %s", (table_name,))
    return cursor.fetchone() is not None


def _build_filter_values(filters: dict[str, Any] | None) -> dict[str, Any]:
    filters = filters or {}

    return {
        "alert_name": _safe_text(filters.get("alert_name")),
        "severity": _normalise_upper(filters.get("severity")),
        "alert_status": _normalise_upper(filters.get("alert_status")),
        "workflow_status": _safe_text(filters.get("workflow_status")),
        "assigned_user": _safe_text(filters.get("assigned_user")),
        "agency": _safe_text(filters.get("agency")),
        "country": _safe_text(filters.get("country")),
        "passport_number": _safe_text(filters.get("passport_number")),
        "ageing_from_days": max(
            _safe_int(filters.get("ageing_from_days"), 0),
            0,
        ),
        "ageing_to_days": max(
            _safe_int(filters.get("ageing_to_days"), 30),
            0,
        ),
        "from_date": _parse_date(filters.get("from_date")),
        "to_date": _parse_date(filters.get("to_date")),
        "search_text": _safe_text(filters.get("search_text")),
        "include_resolved": _normalise_upper(
            filters.get("include_resolved")
        ) in {"Y", "YES", "TRUE", "1"},
    }


def _build_alert_query(
    filters: dict[str, Any],
    *,
    count_only: bool = False,
    limit: int | None = None,
    offset: int = 0,
) -> tuple[str, tuple[Any, ...]]:
    select_clause = """
        COUNT(*) AS total_count
    """ if count_only else """
        ai.id AS alert_id,
        ai.rule_id,
        ai.reference_type,
        ai.reference_id,
        ai.alert_name,
        ai.module_code,
        ai.severity,
        ai.alert_status,
        ai.ageing_minutes,
        ai.warning_triggered_on,
        ai.critical_triggered_on,
        ai.last_notified_on,
        ai.next_notification_on,
        ai.notification_count,
        ai.alert_context_json,
        ai.acknowledged_by,
        ai.acknowledged_on,
        ai.snoozed_by,
        ai.snoozed_on,
        ai.snoozed_until,
        ai.resolved_by,
        ai.resolved_on,
        ai.resolution_reason,
        ai.created_on,
        ai.updated_on,
        arm.warning_after_minutes,
        arm.warning_unit,
        arm.critical_after_minutes,
        arm.critical_unit,

        CASE
            WHEN ai.reference_type = 'PATIENT_WORKFLOW'
            THEN pw.patpassport_no
            WHEN ai.reference_type = 'OPERATION_QUEUE'
            THEN oq.patpassport_no
            ELSE JSON_UNQUOTE(
                JSON_EXTRACT(ai.alert_context_json, '$.passport_no')
            )
        END AS passport_number,

        CASE
            WHEN ai.reference_type = 'PATIENT_WORKFLOW'
            THEN TRIM(CONCAT_WS(' ', pw.patgiven_nm, pw.patsur_nm))
            WHEN ai.reference_type = 'OPERATION_QUEUE'
            THEN TRIM(CONCAT_WS(' ', oq.patgiven_nm, oq.patsur_nm))
            ELSE JSON_UNQUOTE(
                JSON_EXTRACT(ai.alert_context_json, '$.patient_name')
            )
        END AS patient_name,

        CASE
            WHEN ai.reference_type = 'PATIENT_WORKFLOW'
            THEN pw.wrkflw_status
            WHEN ai.reference_type = 'OPERATION_QUEUE'
            THEN oq.wrkflw_status
            ELSE JSON_UNQUOTE(
                JSON_EXTRACT(ai.alert_context_json, '$.workflow_status')
            )
        END AS workflow_status,

        CASE
            WHEN ai.reference_type = 'PATIENT_WORKFLOW'
            THEN pw.entered_date
            WHEN ai.reference_type = 'OPERATION_QUEUE'
            THEN COALESCE(oq.updated_on, oq.created_on)
            ELSE NULL
        END AS status_since,

        CASE
            WHEN ai.reference_type = 'PATIENT_WORKFLOW'
            THEN pw.entered_by
            WHEN ai.reference_type = 'OPERATION_QUEUE'
            THEN oq.assigned_to
            ELSE JSON_UNQUOTE(
                JSON_EXTRACT(ai.alert_context_json, '$.assigned_to')
            )
        END AS assigned_user_code,

        CASE
            WHEN ai.reference_type = 'PATIENT_WORKFLOW'
            THEN pw.agent_shrtnm
            WHEN ai.reference_type = 'OPERATION_QUEUE'
            THEN oq.agent_shrtnm
            ELSE JSON_UNQUOTE(
                JSON_EXTRACT(ai.alert_context_json, '$.agency')
            )
        END AS agency,

        CASE
            WHEN ai.reference_type = 'PATIENT_WORKFLOW'
            THEN pw.patient_country
            WHEN ai.reference_type = 'OPERATION_QUEUE'
            THEN oq.patient_country
            ELSE JSON_UNQUOTE(
                JSON_EXTRACT(ai.alert_context_json, '$.patient_country')
            )
        END AS country,

        CASE
            WHEN ai.reference_type = 'PATIENT_WORKFLOW'
            THEN pw.entered_by
            WHEN ai.reference_type = 'OPERATION_QUEUE'
            THEN oq.assigned_to
            ELSE CAST(
                JSON_UNQUOTE(
                    JSON_EXTRACT(
                        ai.alert_context_json,
                        '$.assigned_to'
                    )
                ) AS CHAR CHARACTER SET utf8mb4
            )
        END AS assigned_user_name
    """

    sql = f"""
        SELECT
            {select_clause}
        FROM alert_instance ai

        LEFT JOIN alert_rule_master arm
          ON arm.id = ai.rule_id

        LEFT JOIN patient_wrkflw pw
          ON ai.reference_type = 'PATIENT_WORKFLOW'
         AND ai.reference_id = pw.ld_seqno

        LEFT JOIN operation_queue_master oq
          ON ai.reference_type = 'OPERATION_QUEUE'
         AND ai.reference_id = oq.id



        WHERE 1 = 1
    """

    conditions: list[str] = []
    values: list[Any] = []

    if not filters["include_resolved"]:
        conditions.append(
            "UPPER(COALESCE(ai.alert_status, 'OPEN')) "
            "IN ('OPEN', 'ACKNOWLEDGED', 'SNOOZED')"
        )

    if filters["alert_name"]:
        conditions.append("ai.alert_name = %s")
        values.append(filters["alert_name"])

    if filters["severity"]:
        conditions.append("UPPER(ai.severity) = %s")
        values.append(filters["severity"])

    if filters["alert_status"]:
        conditions.append("UPPER(ai.alert_status) = %s")
        values.append(filters["alert_status"])

    if filters["workflow_status"]:
        conditions.append(
            """
            UPPER(TRIM(
                CASE
                    WHEN ai.reference_type = 'PATIENT_WORKFLOW'
                    THEN COALESCE(pw.wrkflw_status, '')
                    WHEN ai.reference_type = 'OPERATION_QUEUE'
                    THEN COALESCE(oq.wrkflw_status, '')
                    ELSE COALESCE(
                        JSON_UNQUOTE(
                            JSON_EXTRACT(
                                ai.alert_context_json,
                                '$.workflow_status'
                            )
                        ),
                        ''
                    )
                END
            )) = UPPER(TRIM(%s))
            """
        )
        values.append(filters["workflow_status"])

    if filters["assigned_user"]:
        conditions.append(
            """
            COALESCE(
                CASE
                    WHEN ai.reference_type = 'PATIENT_WORKFLOW'
                    THEN pw.entered_by
                    WHEN ai.reference_type = 'OPERATION_QUEUE'
                    THEN oq.assigned_to
                    ELSE JSON_UNQUOTE(
                        JSON_EXTRACT(
                            ai.alert_context_json,
                            '$.assigned_to'
                        )
                    )
                END,
                ''
            ) = %s
            """
        )
        values.append(filters["assigned_user"])

    if filters["agency"]:
        conditions.append(
            """
            UPPER(TRIM(COALESCE(
                CASE
                    WHEN ai.reference_type = 'PATIENT_WORKFLOW'
                    THEN pw.agent_shrtnm
                    WHEN ai.reference_type = 'OPERATION_QUEUE'
                    THEN oq.agent_shrtnm
                    ELSE JSON_UNQUOTE(
                        JSON_EXTRACT(
                            ai.alert_context_json,
                            '$.agency'
                        )
                    )
                END,
                ''
            ))) = UPPER(TRIM(%s))
            """
        )
        values.append(filters["agency"])

    if filters["country"]:
        conditions.append(
            """
            UPPER(TRIM(COALESCE(
                CASE
                    WHEN ai.reference_type = 'PATIENT_WORKFLOW'
                    THEN pw.patient_country
                    WHEN ai.reference_type = 'OPERATION_QUEUE'
                    THEN oq.patient_country
                    ELSE JSON_UNQUOTE(
                        JSON_EXTRACT(
                            ai.alert_context_json,
                            '$.patient_country'
                        )
                    )
                END,
                ''
            ))) = UPPER(TRIM(%s))
            """
        )
        values.append(filters["country"])

    if filters["passport_number"]:
        conditions.append(
            """
            UPPER(TRIM(COALESCE(
                CASE
                    WHEN ai.reference_type = 'PATIENT_WORKFLOW'
                    THEN pw.patpassport_no
                    WHEN ai.reference_type = 'OPERATION_QUEUE'
                    THEN oq.patpassport_no
                    ELSE JSON_UNQUOTE(
                        JSON_EXTRACT(
                            ai.alert_context_json,
                            '$.passport_no'
                        )
                    )
                END,
                ''
            ))) LIKE UPPER(%s)
            """
        )
        values.append(f"%{filters['passport_number']}%")

    conditions.append(
        "FLOOR(COALESCE(ai.ageing_minutes, 0) / 1440) >= %s"
    )
    values.append(filters["ageing_from_days"])

    if filters["ageing_to_days"] > 0:
        conditions.append(
            "FLOOR(COALESCE(ai.ageing_minutes, 0) / 1440) <= %s"
        )
        values.append(filters["ageing_to_days"])

    if filters["from_date"]:
        conditions.append("DATE(ai.created_on) >= %s")
        values.append(filters["from_date"])

    if filters["to_date"]:
        conditions.append("DATE(ai.created_on) <= %s")
        values.append(filters["to_date"])

    if filters["search_text"]:
        search_value = f"%{filters['search_text']}%"

        conditions.append(
            """
            (
                CAST(ai.id AS CHAR) LIKE %s
                OR COALESCE(ai.alert_name, '') LIKE %s
                OR COALESCE(
                    CASE
                        WHEN ai.reference_type = 'PATIENT_WORKFLOW'
                        THEN pw.patpassport_no
                        WHEN ai.reference_type = 'OPERATION_QUEUE'
                        THEN oq.patpassport_no
                        ELSE JSON_UNQUOTE(
                            JSON_EXTRACT(
                                ai.alert_context_json,
                                '$.passport_no'
                            )
                        )
                    END,
                    ''
                ) LIKE %s
                OR COALESCE(
                    CASE
                        WHEN ai.reference_type = 'PATIENT_WORKFLOW'
                        THEN CONCAT_WS(
                            ' ',
                            pw.patgiven_nm,
                            pw.patsur_nm
                        )
                        WHEN ai.reference_type = 'OPERATION_QUEUE'
                        THEN CONCAT_WS(
                            ' ',
                            oq.patgiven_nm,
                            oq.patsur_nm
                        )
                        ELSE JSON_UNQUOTE(
                            JSON_EXTRACT(
                                ai.alert_context_json,
                                '$.patient_name'
                            )
                        )
                    END,
                    ''
                ) LIKE %s
            )
            """
        )

        values.extend(
            [search_value, search_value, search_value, search_value]
        )

    if conditions:
        sql += "\n AND " + "\n AND ".join(conditions)

    if not count_only:
        sql += """
            ORDER BY
                CASE UPPER(ai.severity)
                    WHEN 'CRITICAL' THEN 1
                    WHEN 'WARNING' THEN 2
                    ELSE 3
                END,
                COALESCE(ai.ageing_minutes, 0) DESC,
                ai.id DESC
        """

        if limit is not None:
            sql += "\n LIMIT %s OFFSET %s"
            values.extend([int(limit), int(offset)])

    return sql, tuple(values)


def get_dashboard_summary() -> dict[str, int]:
    connection = cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                SUM(
                    CASE
                        WHEN UPPER(alert_status)
                             IN ('OPEN','ACKNOWLEDGED','SNOOZED')
                        THEN 1
                        ELSE 0
                    END
                ) AS total_open_alerts,

                SUM(
                    CASE
                        WHEN UPPER(severity) = 'WARNING'
                         AND UPPER(alert_status)
                             IN ('OPEN','ACKNOWLEDGED','SNOOZED')
                        THEN 1
                        ELSE 0
                    END
                ) AS warning_count,

                SUM(
                    CASE
                        WHEN UPPER(severity) = 'CRITICAL'
                         AND UPPER(alert_status)
                             IN ('OPEN','ACKNOWLEDGED','SNOOZED')
                        THEN 1
                        ELSE 0
                    END
                ) AS critical_count,

                SUM(
                    CASE
                        WHEN UPPER(alert_status) = 'ACKNOWLEDGED'
                        THEN 1
                        ELSE 0
                    END
                ) AS acknowledged_count,

                SUM(
                    CASE
                        WHEN UPPER(alert_status) = 'SNOOZED'
                        THEN 1
                        ELSE 0
                    END
                ) AS snoozed_count
            FROM alert_instance
            """
        )

        row = cursor.fetchone() or {}

        return {
            "total_open_alerts": _safe_int(
                row.get("total_open_alerts")
            ),
            "warning_count": _safe_int(row.get("warning_count")),
            "critical_count": _safe_int(row.get("critical_count")),
            "acknowledged_count": _safe_int(
                row.get("acknowledged_count")
            ),
            "snoozed_count": _safe_int(row.get("snoozed_count")),
        }

    finally:
        _close(connection, cursor)



def _load_user_name_map(user_codes: list[str]) -> dict[str, str]:
    cleaned_codes = sorted({
        _safe_text(code)
        for code in user_codes
        if _safe_text(code)
    })

    if not cleaned_codes:
        return {}

    connection = cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        placeholders = ", ".join(["%s"] * len(cleaned_codes))
        cursor.execute(
            f"""
            SELECT
                user_code,
                user_name
            FROM user_master
            WHERE user_code IN ({placeholders})
            """,
            tuple(cleaned_codes),
        )

        return {
            _safe_text(row.get("user_code")): (
                _safe_text(row.get("user_name"))
                or _safe_text(row.get("user_code"))
            )
            for row in (cursor.fetchall() or [])
        }

    finally:
        _close(connection, cursor)

def get_alert_records(
    filters: dict[str, Any] | None = None,
    *,
    page: int = 1,
    page_size: int = 100,
) -> tuple[list[dict[str, Any]], int]:
    page = max(_safe_int(page, 1), 1)
    page_size = min(max(_safe_int(page_size, 100), 1), 500)
    offset = (page - 1) * page_size

    filter_values = _build_filter_values(filters)

    connection = cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        count_sql, count_values = _build_alert_query(
            filter_values,
            count_only=True,
        )
        cursor.execute(count_sql, count_values)
        count_row = cursor.fetchone() or {}
        total_count = _safe_int(count_row.get("total_count"))

        data_sql, data_values = _build_alert_query(
            filter_values,
            count_only=False,
            limit=page_size,
            offset=offset,
        )
        cursor.execute(data_sql, data_values)
        rows = cursor.fetchall() or []

        formatted_rows: list[dict[str, Any]] = []

        for row in rows:
            context = _parse_json(row.get("alert_context_json"))
            ageing_minutes = _safe_int(row.get("ageing_minutes"))

            row["ageing_days"] = ageing_minutes // 1440
            row["ageing_hours"] = (ageing_minutes % 1440) // 60
            row["ageing_display"] = (
                f"{row['ageing_days']}d {row['ageing_hours']}h"
            )
            row["warning_threshold_display"] = _format_duration_minutes(
                row.get("warning_after_minutes")
            )
            row["critical_threshold_display"] = _format_duration_minutes(
                row.get("critical_after_minutes")
            )
            row["warning_unit"] = _normalise_time_unit(
                row.get("warning_unit")
            )
            row["critical_unit"] = _normalise_time_unit(
                row.get("critical_unit")
            )
            row["snooze_unit"] = (
                row["critical_unit"]
                if _normalise_upper(row.get("severity")) == "CRITICAL"
                else row["warning_unit"]
            )
            row["alert_context"] = context
            row["activity"] = _build_alert_activity(row)

            if not row.get("patient_name"):
                row["patient_name"] = context.get("patient_name") or ""

            if not row.get("passport_number"):
                row["passport_number"] = (
                    context.get("passport_no") or ""
                )

            if not row.get("workflow_status"):
                row["workflow_status"] = (
                    context.get("workflow_status") or ""
                )

            if not row.get("agency"):
                row["agency"] = context.get("agency") or ""

            if not row.get("assigned_user_code"):
                row["assigned_user_code"] = (
                    context.get("assigned_to") or ""
                )

            if not row.get("assigned_user_name"):
                row["assigned_user_name"] = (
                    row.get("assigned_user_code") or ""
                )

            formatted_rows.append(row)

        user_name_map = _load_user_name_map([
            row.get("assigned_user_code")
            for row in formatted_rows
        ])

        for row in formatted_rows:
            user_code = _safe_text(row.get("assigned_user_code"))
            row["assigned_user_name"] = (
                user_name_map.get(user_code)
                or _safe_text(row.get("assigned_user_name"))
                or user_code
            )

        return formatted_rows, total_count

    finally:
        _close(connection, cursor)


def get_filter_lovs() -> dict[str, list[dict[str, Any]]]:
    connection = cursor = None

    lovs = {
        "alert_names": [],
        "workflow_statuses": [],
        "assigned_users": [],
        "agencies": [],
        "countries": [],
    }

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT DISTINCT alert_name
            FROM alert_rule_master
            WHERE UPPER(COALESCE(active_flag, 'N')) = 'Y'
              AND COALESCE(alert_name, '') <> ''
            ORDER BY alert_name
            """
        )
        lovs["alert_names"] = cursor.fetchall() or []

        cursor.execute(
            """
            SELECT DISTINCT workflow_status
            FROM alert_rule_master
            WHERE UPPER(COALESCE(active_flag, 'N')) = 'Y'
              AND COALESCE(workflow_status, '') <> ''
            ORDER BY workflow_status
            """
        )
        lovs["workflow_statuses"] = cursor.fetchall() or []

        cursor.execute(
            """
            SELECT
                user_code,
                user_name
            FROM user_master
            WHERE UPPER(COALESCE(user_status, 'INACTIVE')) = 'ACTIVE'
            ORDER BY user_name, user_code
            """
        )
        lovs["assigned_users"] = cursor.fetchall() or []

        # Load agencies separately and merge them in Python.
        # This avoids MySQL UNION collation errors between patient_wrkflw
        # and operation_queue_master.
        agency_values: set[str] = set()

        cursor.execute(
            """
            SELECT DISTINCT agent_shrtnm AS agency
            FROM patient_wrkflw
            WHERE agent_shrtnm IS NOT NULL
              AND TRIM(agent_shrtnm) <> ''
            """
        )
        for row in cursor.fetchall() or []:
            value = _safe_text(row.get("agency"))
            if value:
                agency_values.add(value)

        cursor.execute(
            """
            SELECT DISTINCT agent_shrtnm AS agency
            FROM operation_queue_master
            WHERE agent_shrtnm IS NOT NULL
              AND TRIM(agent_shrtnm) <> ''
            """
        )
        for row in cursor.fetchall() or []:
            value = _safe_text(row.get("agency"))
            if value:
                agency_values.add(value)

        lovs["agencies"] = [
            {"agency": value}
            for value in sorted(
                agency_values,
                key=lambda item: item.casefold(),
            )
        ]

        # Load countries separately and merge them in Python for the same
        # collation-safety reason.
        country_values: set[str] = set()

        cursor.execute(
            """
            SELECT DISTINCT patient_country AS country
            FROM patient_wrkflw
            WHERE patient_country IS NOT NULL
              AND TRIM(patient_country) <> ''
            """
        )
        for row in cursor.fetchall() or []:
            value = _safe_text(row.get("country"))
            if value:
                country_values.add(value)

        cursor.execute(
            """
            SELECT DISTINCT patient_country AS country
            FROM operation_queue_master
            WHERE patient_country IS NOT NULL
              AND TRIM(patient_country) <> ''
            """
        )
        for row in cursor.fetchall() or []:
            value = _safe_text(row.get("country"))
            if value:
                country_values.add(value)

        lovs["countries"] = [
            {"country": value}
            for value in sorted(
                country_values,
                key=lambda item: item.casefold(),
            )
        ]

        return lovs

    finally:
        _close(connection, cursor)


def get_alert_summary_for_records(
    records: list[dict[str, Any]],
) -> dict[str, Any]:
    total_visible = len(records)

    warning_count = sum(
        1
        for row in records
        if _normalise_upper(row.get("severity")) == "WARNING"
    )

    critical_count = sum(
        1
        for row in records
        if _normalise_upper(row.get("severity")) == "CRITICAL"
    )

    acknowledged_count = sum(
        1
        for row in records
        if _normalise_upper(row.get("alert_status")) == "ACKNOWLEDGED"
    )

    snoozed_count = sum(
        1
        for row in records
        if _normalise_upper(row.get("alert_status")) == "SNOOZED"
    )

    highest_severity = "NORMAL"

    if critical_count > 0:
        highest_severity = "CRITICAL"
    elif warning_count > 0:
        highest_severity = "WARNING"

    return {
        "current_view": "All Active Alerts",
        "highest_severity": highest_severity,
        "total_visible": total_visible,
        "warning_count": warning_count,
        "critical_count": critical_count,
        "acknowledged_count": acknowledged_count,
        "snoozed_count": snoozed_count,
    }


def get_alert_dashboard_context(
    filters: dict[str, Any] | None = None,
    *,
    page: int = 1,
    page_size: int = 100,
    success_message: str | None = None,
    error_message: str | None = None,
) -> dict[str, Any]:
    """
    Main function to be called from the Flask dashboard route.
    """
    try:
        normalised_filters = _build_filter_values(filters)

        records, total_count = get_alert_records(
            normalised_filters,
            page=page,
            page_size=page_size,
        )

        summary = get_dashboard_summary()
        lovs = get_filter_lovs()
        visible_summary = get_alert_summary_for_records(records)

        total_pages = max(
            (total_count + page_size - 1) // page_size,
            1,
        )

        return {
            "summary": summary,
            "alerts": records,
            "alert_records": records,
            "total_count": total_count,
            "visible_alert_count": len(records),
            "selected_alert_count": 0,
            "visible_summary": visible_summary,
            "filters": normalised_filters,
            "alert_names": lovs["alert_names"],
            "workflow_statuses": lovs["workflow_statuses"],
            "assigned_users": lovs["assigned_users"],
            "agencies": lovs["agencies"],
            "countries": lovs["countries"],
            "severity_list": list(ALLOWED_SEVERITIES),
            "alert_status_list": list(ALLOWED_ALERT_STATUSES),
            "page": page,
            "page_size": page_size,
            "total_pages": total_pages,
            "success_message": success_message,
            "error_message": error_message,
        }

    except Exception as exc:
        alert_dashboard_logger.exception(
            "ALERT_DASHBOARD_CONTEXT_FAILED error=%s",
            exc,
        )

        return {
            "summary": {
                "total_open_alerts": 0,
                "warning_count": 0,
                "critical_count": 0,
                "acknowledged_count": 0,
                "snoozed_count": 0,
            },
            "alerts": [],
            "alert_records": [],
            "total_count": 0,
            "visible_alert_count": 0,
            "selected_alert_count": 0,
            "visible_summary": {
                "current_view": "All Active Alerts",
                "highest_severity": "NORMAL",
                "total_visible": 0,
                "warning_count": 0,
                "critical_count": 0,
                "acknowledged_count": 0,
                "snoozed_count": 0,
            },
            "filters": _build_filter_values(filters),
            "alert_names": [],
            "workflow_statuses": [],
            "assigned_users": [],
            "agencies": [],
            "countries": [],
            "severity_list": list(ALLOWED_SEVERITIES),
            "alert_status_list": list(ALLOWED_ALERT_STATUSES),
            "page": 1,
            "page_size": page_size,
            "total_pages": 1,
            "success_message": success_message,
            "error_message": str(exc),
        }


def get_alert_export_rows(
    filters: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    """
    Return dashboard records without normal pagination for CSV export.
    """
    records, _ = get_alert_records(
        filters,
        page=1,
        page_size=500,
    )

    export_rows: list[dict[str, Any]] = []

    for row in records:
        export_rows.append(
            {
                "Alert ID": row.get("alert_id"),
                "Alert Name": row.get("alert_name"),
                "Severity": row.get("severity"),
                "Alert Status": row.get("alert_status"),
                "Patient": row.get("patient_name"),
                "Passport Number": row.get("passport_number"),
                "Workflow Status": row.get("workflow_status"),
                "Status Since": row.get("status_since"),
                "Ageing Minutes": row.get("ageing_minutes"),
                "Ageing": row.get("ageing_display"),
                "Assigned User": row.get("assigned_user_name"),
                "Agency": row.get("agency"),
                "Country": row.get("country"),
                "Reference Type": row.get("reference_type"),
                "Reference ID": row.get("reference_id"),
                "Created On": row.get("created_on"),
            }
        )

    return export_rows
