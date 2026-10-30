from datetime import datetime
from math import ceil
from urllib.parse import urlencode, quote


def _dict_cursor(conn):
    return conn.cursor(dictionary=True)


def _safe_int(value, default=0):
    try:
        if value is None or value == "":
            return default
        return int(value)
    except Exception:
        try:
            return int(float(value))
        except Exception:
            return default


def _safe_float(value, default=0.0):
    try:
        if value is None or value == "":
            return default
        return float(value)
    except Exception:
        return default


def _safe_str(value, default=""):
    if value is None:
        return default
    return str(value).strip()


def _fetch_one(conn, sql, params=None):
    cursor = None
    try:
        cursor = _dict_cursor(conn)
        cursor.execute(sql, params or ())
        return cursor.fetchone() or {}
    finally:
        if cursor:
            cursor.close()


def _fetch_all(conn, sql, params=None):
    cursor = None
    try:
        cursor = _dict_cursor(conn)
        cursor.execute(sql, params or ())
        return cursor.fetchall() or []
    finally:
        if cursor:
            cursor.close()


def _build_pagination(request_args, page_param_name, current_page, total_count, per_page):
    total_pages = max(1, ceil((total_count or 0) / float(per_page or 1)))
    current_page = max(1, min(current_page, total_pages))

    args_copy = {}
    for key in request_args.keys():
        args_copy[key] = request_args.get(key)

    def _page_url(page_number):
        local_args = dict(args_copy)
        local_args[page_param_name] = page_number
        return f"?{urlencode(local_args)}"

    pages = []
    start_page = max(1, current_page - 2)
    end_page = min(total_pages, current_page + 2)

    for page_number in range(start_page, end_page + 1):
        pages.append({
            "label": str(page_number),
            "active": page_number == current_page,
            "url": None if page_number == current_page else _page_url(page_number)
        })

    return {
        "current_page": current_page,
        "total_pages": total_pages,
        "total_count": total_count or 0,
        "per_page": per_page,
        "prev_url": _page_url(current_page - 1) if current_page > 1 else None,
        "next_url": _page_url(current_page + 1) if current_page < total_pages else None,
        "pages": pages
    }


def _default_context():
    now_str = datetime.now().strftime("%d-%b-%Y %I:%M %p")
    return {
        "summary": {
            "total_active_queue": 0,
            "assigned": 0,
            "in_progress": 0,
            "closed_today": 0,
            "thread_groups": 0,
            "near_breach": 0,
            "breached": 0,
            "escalated": 0,
            "within_sla": 0,
            "sla_compliance": 0,
        },
        "finance": {
            "invoices_generated": 0,
            "pending_invoices": 0,
            "paid_invoices": 0,
            "partially_paid": 0,
            "total_invoice_value": 0,
            "outstanding_amount": 0,
            "collections_today": 0,
            "payment_ratio": 0,
        },
        "operation_queue_rows": [],
        "invoice_rows": [],
        "quick_alerts": [],
        "operation_status_chart": [],
        "finance_trend_chart": [],
        "dashboard_last_updated": now_str,
        "selected_hospital": "",
        "selected_department": "",
        "selected_date_range": "",
        "now": now_str,
        "operation_queue_pagination": {},
        "invoice_pagination": {},
    }


def _get_date_filter_meta(date_range_key):
    key = _safe_str(date_range_key).lower()

    # Default dashboard period must be Today.
    # Select "All Period" from LOV to remove the date filter.
    if not key:
        key = "today"

    if key in ("all", "all_period", "all period"):
        return {
            "key": "all",
            "label": "All Period",
            "operation_condition": "",
            "invoice_condition": "",
            "recon_condition": "",
        }

    if key == "today":
        return {
            "key": "today",
            "label": "Today",
            "operation_condition": " AND DATE(COALESCE(updated_on, created_on, NOW())) = CURDATE() ",
            "invoice_condition": " AND DATE(COALESCE(created_on, NOW())) = CURDATE() ",
            "recon_condition": " AND DATE(COALESCE(created_on, NOW())) = CURDATE() ",
        }

    if key == "7d":
        return {
            "key": "7d",
            "label": "7 Days",
            "operation_condition": " AND DATE(COALESCE(updated_on, created_on, NOW())) >= (CURDATE() - INTERVAL 6 DAY) ",
            "invoice_condition": " AND DATE(COALESCE(created_on, NOW())) >= (CURDATE() - INTERVAL 6 DAY) ",
            "recon_condition": " AND DATE(COALESCE(created_on, NOW())) >= (CURDATE() - INTERVAL 6 DAY) ",
        }

    if key == "month":
        return {
            "key": "month",
            "label": "Month",
            "operation_condition": " AND YEAR(COALESCE(updated_on, created_on, NOW())) = YEAR(CURDATE()) AND MONTH(COALESCE(updated_on, created_on, NOW())) = MONTH(CURDATE()) ",
            "invoice_condition": " AND YEAR(COALESCE(created_on, NOW())) = YEAR(CURDATE()) AND MONTH(COALESCE(created_on, NOW())) = MONTH(CURDATE()) ",
            "recon_condition": " AND YEAR(COALESCE(created_on, NOW())) = YEAR(CURDATE()) AND MONTH(COALESCE(created_on, NOW())) = MONTH(CURDATE()) ",
        }

    if key == "quarter":
        return {
            "key": "quarter",
            "label": "Quarter",
            "operation_condition": " AND YEAR(COALESCE(updated_on, created_on, NOW())) = YEAR(CURDATE()) AND QUARTER(COALESCE(updated_on, created_on, NOW())) = QUARTER(CURDATE()) ",
            "invoice_condition": " AND YEAR(COALESCE(created_on, NOW())) = YEAR(CURDATE()) AND QUARTER(COALESCE(created_on, NOW())) = QUARTER(CURDATE()) ",
            "recon_condition": " AND YEAR(COALESCE(created_on, NOW())) = YEAR(CURDATE()) AND QUARTER(COALESCE(created_on, NOW())) = QUARTER(CURDATE()) ",
        }

    if key == "year":
        return {
            "key": "year",
            "label": "Year",
            "operation_condition": " AND YEAR(COALESCE(updated_on, created_on, NOW())) = YEAR(CURDATE()) ",
            "invoice_condition": " AND YEAR(COALESCE(created_on, NOW())) = YEAR(CURDATE()) ",
            "recon_condition": " AND YEAR(COALESCE(created_on, NOW())) = YEAR(CURDATE()) ",
        }

    return {
        "key": "today",
        "label": "Today",
        "operation_condition": " AND DATE(COALESCE(updated_on, created_on, NOW())) = CURDATE() ",
        "invoice_condition": " AND DATE(COALESCE(created_on, NOW())) = CURDATE() ",
        "recon_condition": " AND DATE(COALESCE(created_on, NOW())) = CURDATE() ",
    }

def _table_exists_by_name(conn, table_name):
    cursor = None
    try:
        cursor = _dict_cursor(conn)
        cursor.execute("SHOW TABLES LIKE %s", (table_name,))
        return cursor.fetchone() is not None
    except Exception:
        return False
    finally:
        if cursor:
            cursor.close()


def _get_table_columns_safe(conn, table_name):
    cursor = None
    try:
        cursor = _dict_cursor(conn)
        cursor.execute(f"SHOW COLUMNS FROM {table_name}")
        rows = cursor.fetchall() or []
        columns = set()
        for row in rows:
            field_name = row.get("Field") if isinstance(row, dict) else (row[0] if row else None)
            if field_name:
                columns.add(str(field_name))
        return columns
    except Exception:
        return set()
    finally:
        if cursor:
            cursor.close()



def _first_existing_column(columns, candidates):
    columns = columns or set()
    lower_map = {str(col).lower(): str(col) for col in columns}
    for candidate in candidates or []:
        key = str(candidate).lower()
        if key in lower_map:
            return lower_map[key]
    return ""


def _sql_identifier(column_name, table_alias=""):
    column_name = _safe_str(column_name)
    table_alias = _safe_str(table_alias)
    if not column_name:
        return "NULL"
    if table_alias:
        return f"{table_alias}.`{column_name}`"
    return f"`{column_name}`"


def _sql_text_expr(columns, candidates, alias, default_value="", table_alias=""):
    column_name = _first_existing_column(columns, candidates)
    if column_name:
        return f"COALESCE({_sql_identifier(column_name, table_alias)}, '') AS {alias}"
    safe_default = str(default_value).replace("'", "''")
    return f"'{safe_default}' AS {alias}"


def _sql_amount_expr(columns, candidates, alias, default_value=0, table_alias=""):
    column_name = _first_existing_column(columns, candidates)
    if column_name:
        return f"COALESCE({_sql_identifier(column_name, table_alias)}, 0) AS {alias}"
    return f"{float(default_value)} AS {alias}"


def _build_period_condition_for_columns(columns, date_filter_meta, preferred_date_columns, table_alias=""):
    date_filter_meta = date_filter_meta or {}
    key = _safe_str(date_filter_meta.get("key")).lower()

    if not key or key in ("all", "all_period", "all period"):
        return ""

    date_col = _first_existing_column(columns, preferred_date_columns)
    if not date_col:
        return ""

    date_expr = f"DATE({_sql_identifier(date_col, table_alias)})"

    if key == "today":
        return f" AND {date_expr} = CURDATE() "
    if key == "7d":
        return f" AND {date_expr} >= (CURDATE() - INTERVAL 6 DAY) "
    if key == "month":
        return f" AND YEAR({_sql_identifier(date_col, table_alias)}) = YEAR(CURDATE()) AND MONTH({_sql_identifier(date_col, table_alias)}) = MONTH(CURDATE()) "
    if key == "quarter":
        return f" AND YEAR({_sql_identifier(date_col, table_alias)}) = YEAR(CURDATE()) AND QUARTER({_sql_identifier(date_col, table_alias)}) = QUARTER(CURDATE()) "
    if key == "year":
        return f" AND YEAR({_sql_identifier(date_col, table_alias)}) = YEAR(CURDATE()) "

    return ""


def _get_invoice_master_columns(conn):
    return _get_table_columns_safe(conn, "invoice_master")


def _get_invoice_recon_columns(conn):
    return _get_table_columns_safe(conn, "invoice_recon")

def _get_latest_sla_status_map(conn, date_filter_meta=None):
    """
    Returns latest SLA state per channel_thread_id.
    Supports both old/new column names:
    - status / sla_status / current_status
    - escalated_flag / is_escalated
    - current_escalation_level / escalation_level
    """
    try:
        if not _table_exists_by_name(conn, "operation_queue_sla_log"):
            return {}

        table_columns = _get_table_columns_safe(conn, "operation_queue_sla_log")
        if not table_columns or "channel_thread_id" not in table_columns:
            return {}

        status_col = _first_existing_column(table_columns, [
            "status", "sla_status", "current_status", "breach_status", "sla_state"
        ])
        escalated_col = _first_existing_column(table_columns, [
            "escalated_flag", "is_escalated", "escalation_flag"
        ])
        escalation_level_col = _first_existing_column(table_columns, [
            "current_escalation_level", "escalation_level", "level"
        ])
        latest_order_col = _first_existing_column(table_columns, [
            "id", "updated_on", "created_on", "last_evaluated_on", "evaluated_on"
        ])

        if not latest_order_col:
            return {}

        status_expr = f"UPPER(TRIM(COALESCE(s.`{status_col}`, '')))" if status_col else "''"
        escalated_expr = f"COALESCE(s.`{escalated_col}`, 0)" if escalated_col else "0"
        escalation_level_expr = f"COALESCE(s.`{escalation_level_col}`, 0)" if escalation_level_col else "0"

        sql = f"""
            SELECT
                s.channel_thread_id,
                {status_expr} AS sla_state,
                {escalated_expr} AS escalated_flag,
                {escalation_level_expr} AS current_escalation_level
            FROM operation_queue_sla_log s
            INNER JOIN (
                SELECT channel_thread_id, MAX(`{latest_order_col}`) AS latest_key
                FROM operation_queue_sla_log
                WHERE channel_thread_id IS NOT NULL
                  AND TRIM(channel_thread_id) <> ''
                GROUP BY channel_thread_id
            ) latest
                ON latest.channel_thread_id = s.channel_thread_id
               AND latest.latest_key = s.`{latest_order_col}`
        """

        rows = _fetch_all(conn, sql)
        result = {}
        for row in rows:
            thread_id = _safe_str(row.get("channel_thread_id"))
            if not thread_id:
                continue

            state = _safe_str(row.get("sla_state")).upper()
            state = state.replace("_", " ").replace("-", " ")
            if state in ("NEARBREACH", "NEAR BREACH"):
                state = "NEAR BREACH"
            elif state in ("BREACH", "BREACHED"):
                state = "BREACHED"
            elif state in ("ESCALATE", "ESCALATED"):
                state = "ESCALATED"
            elif state in ("WITHIN", "WITHIN SLA", "OK", "ACTIVE"):
                state = "WITHIN SLA"

            result[thread_id] = {
                "sla_state": state,
                "escalated_flag": _safe_int(row.get("escalated_flag"), 0),
                "current_escalation_level": _safe_int(row.get("current_escalation_level"), 0),
            }
        return result
    except Exception as e:
        print("[DASHBOARD] SLA map query failed:", e)
        return {}


def _normalize_sla_status_label(value):
    state = _safe_str(value).upper().replace("_", " ").replace("-", " ")
    state = " ".join(state.split())

    if state in ("WITHIN", "WITHIN SLA", "OK", "ACTIVE", "ON TRACK", "ONTRACK", "MET"):
        return "WITHIN SLA"
    if state in ("NEARBREACH", "NEAR BREACH", "NEAR TO BREACH", "APPROACHING BREACH"):
        return "NEAR BREACH"
    if state in ("BREACH", "BREACHED", "SLA BREACHED"):
        return "BREACHED"
    if state in ("ESCALATE", "ESCALATED", "SLA ESCALATED"):
        return "ESCALATED"
    return state


def _get_sla_health_counts(conn, date_filter_meta=None):
    """
    SLA Health source of truth.

    Business rule:
    - WITHIN SLA / NEAR BREACH / BREACHED are counted from latest
      operation_queue_sla_log.status per channel_thread_id.
    - ESCALATED is derived from rows where status = BREACHED and either
      escalated_flag = 1/Y/YES/TRUE or current_escalation_level > 0.
    - If any old row stores status itself as ESCALATED, it is counted as
      both breached and escalated for dashboard health.
    """
    default_counts = {
        "within_sla": 0,
        "near_breach": 0,
        "breached": 0,
        "escalated": 0,
    }

    try:
        if not _table_exists_by_name(conn, "operation_queue_sla_log"):
            return default_counts

        columns = _get_table_columns_safe(conn, "operation_queue_sla_log")
        if not columns or "channel_thread_id" not in columns:
            return default_counts

        status_col = _first_existing_column(columns, [
            "status", "sla_status", "current_status", "breach_status", "sla_state"
        ])
        escalated_col = _first_existing_column(columns, [
            "escalated_flag", "is_escalated", "escalation_flag"
        ])
        escalation_level_col = _first_existing_column(columns, [
            "current_escalation_level", "escalation_level", "level"
        ])
        latest_order_col = _first_existing_column(columns, [
            "id", "updated_on", "created_on", "last_evaluated_on", "evaluated_on"
        ])

        if not status_col or not latest_order_col:
            return default_counts

        period_condition = _build_period_condition_for_columns(
            columns,
            date_filter_meta,
            ["updated_on", "created_on", "last_evaluated_on", "evaluated_on"],
            table_alias="",
        )

        escalated_expr = f"base_sla.`{escalated_col}`" if escalated_col else "0"
        escalation_level_expr = f"base_sla.`{escalation_level_col}`" if escalation_level_col else "0"

        sql = f"""
            SELECT
                latest_sla.sla_status,
                latest_sla.escalated_flag,
                latest_sla.current_escalation_level
            FROM (
                SELECT
                    base_sla.channel_thread_id,
                    UPPER(TRIM(COALESCE(base_sla.`{status_col}`, ''))) AS sla_status,
                    {escalated_expr} AS escalated_flag,
                    {escalation_level_expr} AS current_escalation_level
                FROM operation_queue_sla_log base_sla
                INNER JOIN (
                    SELECT channel_thread_id, MAX(`{latest_order_col}`) AS latest_key
                    FROM operation_queue_sla_log
                    WHERE channel_thread_id IS NOT NULL
                      AND TRIM(channel_thread_id) <> ''
                      {period_condition}
                    GROUP BY channel_thread_id
                ) latest
                    ON latest.channel_thread_id = base_sla.channel_thread_id
                   AND latest.latest_key = base_sla.`{latest_order_col}`
                WHERE base_sla.channel_thread_id IS NOT NULL
                  AND TRIM(base_sla.channel_thread_id) <> ''
            ) latest_sla
        """

        rows = _fetch_all(conn, sql)
        counts = dict(default_counts)

        for row in rows:
            status = _normalize_sla_status_label(row.get("sla_status"))

            escalated_flag_raw = _safe_str(row.get("escalated_flag")).upper()
            escalated_flag = (
                _safe_int(row.get("escalated_flag"), 0) == 1
                or escalated_flag_raw in ("Y", "YES", "TRUE", "T", "ESCALATED")
            )
            escalation_level = _safe_int(row.get("current_escalation_level"), 0)
            is_escalated_marker = escalated_flag or escalation_level > 0

            # Dashboard business rule:
            # - WITHIN SLA is derived from current_escalation_level = 0.
            # - NEAR BREACH and BREACHED continue to come from latest status.
            # - ESCALATED is a subset of BREACHED.
            if status == "NEAR BREACH":
                counts["near_breach"] += 1
            elif status == "BREACHED":
                counts["breached"] += 1
                if is_escalated_marker:
                    counts["escalated"] += 1
            elif status == "ESCALATED":
                counts["breached"] += 1
                counts["escalated"] += 1
            elif escalation_level == 0:
                counts["within_sla"] += 1

        return counts
    except Exception as e:
        print("[DASHBOARD] SLA health counts query failed:", e)
        return default_counts
def _get_operation_summary(conn, sla_status_map, date_filter_meta):
    """
    Dashboard operation KPIs must match Operation Queue business view.

    Important rule:
    - operation_queue_master stores multiple rows for the same channel_thread_id.
    - KPI status counts must be calculated from the LATEST row of each thread only.
    - Historical rows must not inflate ASSIGNED / IN PROGRESS counts.
    - CLOSED uses the selected dashboard period when a period filter is applied.
      Without a period filter, it keeps the earlier dashboard behavior: CLOSED for today.
    """
    try:
        date_filter_meta = date_filter_meta or {}
        operation_condition = date_filter_meta.get('operation_condition', '') or ''
        selected_date_key = _safe_str(date_filter_meta.get('key')).lower()

        latest_thread_sql = f"""
            SELECT
                oq.channel_thread_id,
                UPPER(TRIM(COALESCE(oq.wrkflw_status, ''))) AS wrkflw_status,
                COALESCE(oq.updated_on, oq.created_on, NOW()) AS activity_dt
            FROM operation_queue_master oq
            INNER JOIN (
                SELECT channel_thread_id, MAX(id) AS max_id
                FROM operation_queue_master
                WHERE channel_thread_id IS NOT NULL
                  AND TRIM(channel_thread_id) <> ''
                  {operation_condition}
                GROUP BY channel_thread_id
            ) latest_oq
                ON latest_oq.channel_thread_id = oq.channel_thread_id
               AND latest_oq.max_id = oq.id
        """
        latest_thread_rows = _fetch_all(conn, latest_thread_sql)

        total_active_queue = 0
        assigned = 0
        in_progress = 0
        closed_count = 0
        near_breach = 0
        breached = 0
        escalated = 0

        for latest_thread in latest_thread_rows:
            thread_id = _safe_str(latest_thread.get("channel_thread_id"))
            wrkflw_status = _safe_str(latest_thread.get("wrkflw_status")).upper()

            if not thread_id:
                continue

            if wrkflw_status != "CLOSED":
                total_active_queue += 1

            if wrkflw_status == "ASSIGNED":
                assigned += 1
            elif wrkflw_status == "IN PROGRESS":
                in_progress += 1
            elif wrkflw_status == "CLOSED":
                if selected_date_key:
                    closed_count += 1
                else:
                    closed_date = latest_thread.get("activity_dt")
                    try:
                        if hasattr(closed_date, "date"):
                            is_today = closed_date.date() == datetime.now().date()
                        else:
                            is_today = str(closed_date)[:10] == datetime.now().strftime("%Y-%m-%d")
                    except Exception:
                        is_today = False
                    if is_today:
                        closed_count += 1

            if wrkflw_status == "CLOSED":
                continue

            sla_data = sla_status_map.get(thread_id, {})
            state = _safe_str(sla_data.get("sla_state")).upper()
            escalated_flag = _safe_int(sla_data.get("escalated_flag"), 0)
            escalation_level = _safe_int(sla_data.get("current_escalation_level"), 0)

            if state == "NEAR BREACH":
                near_breach += 1
            elif state == "BREACHED":
                breached += 1
            elif state == "ESCALATED":
                escalated += 1

            if (escalated_flag == 1 or escalation_level > 0) and state != "ESCALATED":
                escalated += 1

        # SLA Health counts must come from operation_queue_sla_log.status directly.
        sla_counts = _get_sla_health_counts(conn, date_filter_meta)

        thread_groups = len(latest_thread_rows)
        within_sla = _safe_int(sla_counts.get("within_sla"), 0)
        near_breach = _safe_int(sla_counts.get("near_breach"), 0)
        breached = _safe_int(sla_counts.get("breached"), 0)
        escalated = _safe_int(sla_counts.get("escalated"), 0)

        # ESCALATED is a subset of BREACHED when status = BREACHED and escalation markers exist.
        # Do not add escalated again in denominator, otherwise compliance becomes artificially low.
        sla_total = within_sla + near_breach + breached
        sla_compliance = 0
        if sla_total > 0:
            sla_compliance = round((within_sla / sla_total) * 100)

        return {
            "total_active_queue": total_active_queue,
            "assigned": assigned,
            "in_progress": in_progress,
            "closed_today": closed_count,
            "thread_groups": thread_groups,
            "near_breach": near_breach,
            "breached": breached,
            "escalated": escalated,
            "within_sla": within_sla,
            "sla_compliance": sla_compliance
        }
    except Exception as e:
        print("[DASHBOARD] Operation summary query failed:", e)
        return _default_context()["summary"]

def _apply_chart_heights(items):
    max_value = max([_safe_float(item.get("value")) for item in items], default=0)
    for item in items:
        value = _safe_float(item.get("value"))
        item["height_pct"] = max(8, round((value / max_value) * 100)) if max_value > 0 and value > 0 else 0
    return items


def _status_color_class(status_label):
    label = _safe_str(status_label).upper()
    if label in ("OPEN", "NEW", "ASSIGNED"):
        return "blue"
    if label in ("IN PROGRESS", "IN_PROGRESS"):
        return "green"
    if label in ("MANUAL REVIEW", "PARTIAL", "PENDING"):
        return "amber"
    if label in ("CLOSED", "COMPLETED", "CONVERTED"):
        return "purple"
    if label in ("REJECTED", "CANCELLED", "FAILED", "BREACHED"):
        return "red"
    return "blue"




def _sla_sort_rank(sla_state, escalation_level=0, escalated_flag=0):
    """Dashboard table SLA ordering. Lower rank appears first."""
    state = _normalize_sla_status_label(sla_state)
    state = _safe_str(state).upper()
    level = _safe_int(escalation_level, 0)
    flag_raw = _safe_str(escalated_flag).upper()
    flag = _safe_int(escalated_flag, 0) == 1 or flag_raw in ("Y", "YES", "TRUE", "T", "ESCALATED")

    if state in ("BREACHED", "ESCALATED"):
        return 1
    if state == "NEAR BREACH":
        return 2
    if state in ("MET", "WITHIN SLA", "WITHIN", "OK", "ACTIVE", "ON TRACK", "ONTRACK"):
        return 3
    if level > 0 or flag:
        return 4
    return 5

def _get_operation_status_chart(conn, date_filter_meta):
    """Operation Status Distribution from latest queue row per thread."""
    try:
        condition = (date_filter_meta or {}).get("operation_condition", "") or ""
        sql = f"""
            SELECT
                CASE
                    WHEN TRIM(COALESCE(latest_rows.wrkflw_status, '')) = '' THEN 'UNKNOWN'
                    ELSE UPPER(TRIM(latest_rows.wrkflw_status))
                END AS status_label,
                COUNT(*) AS total_count
            FROM (
                SELECT oq.channel_thread_id, oq.wrkflw_status
                FROM operation_queue_master oq
                INNER JOIN (
                    SELECT channel_thread_id, MAX(id) AS max_id
                    FROM operation_queue_master
                    WHERE channel_thread_id IS NOT NULL
                      AND TRIM(channel_thread_id) <> ''
                      {condition}
                    GROUP BY channel_thread_id
                ) latest_oq
                    ON latest_oq.channel_thread_id = oq.channel_thread_id
                   AND latest_oq.max_id = oq.id
            ) latest_rows
            GROUP BY CASE
                WHEN TRIM(COALESCE(latest_rows.wrkflw_status, '')) = '' THEN 'UNKNOWN'
                ELSE UPPER(TRIM(latest_rows.wrkflw_status))
            END
            ORDER BY total_count DESC, status_label ASC
        """
        rows = _fetch_all(conn, sql)
        items = []
        for row in rows:
            label = _safe_str(row.get("status_label")) or "UNKNOWN"
            label = label.replace("_", " ").title()
            items.append({
                "label": label,
                "value": _safe_int(row.get("total_count")),
                "color_class": _status_color_class(label),
            })
        return _apply_chart_heights(items)
    except Exception as e:
        print("[DASHBOARD] Operation status chart query failed:", e)
        return []


def _get_operation_queue_total(conn, date_filter_meta):
    try:
        sql = f"""
            SELECT COUNT(*) AS total_count
            FROM (
                SELECT channel_thread_id, MAX(id) AS max_id
                FROM operation_queue_master
                WHERE channel_thread_id IS NOT NULL
                  AND TRIM(channel_thread_id) <> ''
                  {date_filter_meta.get('operation_condition', '')}
                GROUP BY channel_thread_id
            ) latest_oq
        """
        row = _fetch_one(conn, sql)
        return _safe_int(row.get("total_count"))
    except Exception as e:
        print("[DASHBOARD] Operation queue total query failed:", e)
        return 0


def _get_operation_queue_rows(conn, page, per_page, sla_status_map, date_filter_meta):
    """
    Live Operation Queue rows.

    Important fixes:
    - Fetch only the latest operation_queue_master row per channel_thread_id.
    - Merge the same SLA value that is displayed in the table.
    - Sort after SLA merge so BREACHED records always appear before MET/WITHIN SLA,
      even across pagination.
    """
    try:
        page = max(1, _safe_int(page, 1))
        per_page = max(1, _safe_int(per_page, 5))
        operation_condition = (date_filter_meta or {}).get("operation_condition", "") or ""

        sql = f"""
            SELECT
                oq.channel_thread_id,
                oq.patgiven_nm,
                oq.patsur_nm,
                oq.patpassport_no,
                oq.hospital_shrtnm,
                oq.wrkflw_status,
                oq.priority_status,
                oq.assigned_to,
                oq.id AS operation_queue_id,
                COALESCE(oq.updated_on, oq.created_on, NOW()) AS activity_dt
            FROM operation_queue_master oq
            INNER JOIN (
                SELECT channel_thread_id, MAX(id) AS max_id
                FROM operation_queue_master
                WHERE channel_thread_id IS NOT NULL
                  AND TRIM(channel_thread_id) <> ''
                  {operation_condition}
                GROUP BY channel_thread_id
            ) latest_oq
                ON latest_oq.channel_thread_id = oq.channel_thread_id
               AND latest_oq.max_id = oq.id
        """
        rows = _fetch_all(conn, sql)

        result = []
        for row in rows:
            given_name = _safe_str(row.get("patgiven_nm"))
            sur_name = _safe_str(row.get("patsur_nm"))
            patient_name = " ".join([part for part in [given_name, sur_name] if part]).strip()

            thread_id = _safe_str(row.get("channel_thread_id"))
            sla_info = sla_status_map.get(thread_id, {}) or {}

            sla_state = _normalize_sla_status_label(sla_info.get("sla_state"))
            escalation_level = _safe_int(sla_info.get("current_escalation_level"), 0)
            escalated_flag = sla_info.get("escalated_flag", 0)

            result.append({
                "channel_thread_id": thread_id,
                "patgiven_nm": given_name,
                "patsur_nm": sur_name,
                "patient_name": patient_name,
                "patpassport_no": _safe_str(row.get("patpassport_no")),
                "hospital_shrtnm": _safe_str(row.get("hospital_shrtnm")),
                "wrkflw_status": _safe_str(row.get("wrkflw_status")),
                "priority_status": _safe_str(row.get("priority_status")),
                "assigned_to": _safe_str(row.get("assigned_to")),
                "sla_state": sla_state,
                "_sla_sort_rank": _sla_sort_rank(sla_state, escalation_level, escalated_flag),
                "_operation_queue_id": _safe_int(row.get("operation_queue_id"), 0),
            })

        result.sort(key=lambda item: (item.get("_sla_sort_rank", 5), -_safe_int(item.get("_operation_queue_id"), 0)))

        offset = (page - 1) * per_page
        paged_result = result[offset:offset + per_page]
        for item in paged_result:
            item.pop("_sla_sort_rank", None)
            item.pop("_operation_queue_id", None)

        return paged_result
    except Exception as e:
        print("[DASHBOARD] Operation queue rows query failed:", e)
        return []

def _get_finance_summary(conn, date_filter_meta):
    """
    Finance dashboard rules:
    1. PAID INVOICES      = invrecon_master.recon_status = 'MATCHED'
    2. PARTIALLY PAID     = invrecon_master.recon_status = 'PARTIAL MATCH'
    3. PENDING INVOICES   = INVOICES GENERATED - (PAID INVOICES + PARTIALLY PAID)
    4. OUTSTANDING AMOUNT = TOTAL INVOICE VALUE - SUM(invrecon_master.recvd_amount)
    5. COLLECTIONS        = SUM(invrecon_master.recvd_amount) as per selected period
    6. PAYMENT RATIO      = 1 - (OUTSTANDING AMOUNT / TOTAL INVOICE VALUE)
    """
    finance_default = _default_context()["finance"]

    try:
        invoice_columns = _get_invoice_master_columns(conn)
        recon_columns = _get_table_columns_safe(conn, "invrecon_master")

        if not invoice_columns:
            return finance_default

        invoice_number_col = _first_existing_column(invoice_columns, ["invoice_number", "invoice_no", "invoice_num", "bill_number"])
        invoice_amount_col = _first_existing_column(invoice_columns, ["netinv_amount", "totalinv_amount", "total_invoice_amount", "invoice_amount", "inv_amount", "sumof_invamnt", "net_amount", "gross_amount", "amount"])

        if not invoice_number_col:
            return finance_default

        invoice_date_condition = _build_period_condition_for_columns(
            invoice_columns,
            date_filter_meta,
            ["status_date", "created_on", "invoice_date", "generated_on", "invoice_generated_on", "updated_on", "entered_date", "entered_on"],
            table_alias="im",
        )

        invoice_number_expr = _sql_identifier(invoice_number_col, "im")
        invoice_amount_expr = _sql_identifier(invoice_amount_col, "im") if invoice_amount_col else "0"

        base_invoice_sql = f"""
            SELECT
                TRIM(COALESCE({invoice_number_expr}, '')) AS invoice_number,
                MAX(COALESCE({invoice_amount_expr}, 0)) AS invoice_amount
            FROM invoice_master im
            WHERE TRIM(COALESCE({invoice_number_expr}, '')) <> ''
            {invoice_date_condition}
            GROUP BY TRIM(COALESCE({invoice_number_expr}, ''))
        """

        invoice_summary_sql = f"""
            SELECT
                COUNT(*) AS invoices_generated,
                COALESCE(SUM(invoice_amount), 0) AS total_invoice_value
            FROM ({base_invoice_sql}) inv
        """
        invoice_summary = _fetch_one(conn, invoice_summary_sql)

        invoices_generated = _safe_int(invoice_summary.get("invoices_generated"))
        total_invoice_value = round(_safe_float(invoice_summary.get("total_invoice_value")), 2)

        paid_invoices = 0
        partially_paid = 0
        received_against_selected_invoices = 0.0
        collections_value = 0.0

        if recon_columns and _table_exists_by_name(conn, "invrecon_master"):
            recon_invoice_number_col = _first_existing_column(recon_columns, ["invoice_number", "invoice_no", "invoice_num", "bill_number"])
            recon_status_col = _first_existing_column(recon_columns, ["recon_status", "status", "payment_status"])
            recvd_amount_col = _first_existing_column(recon_columns, ["recvd_amount", "received_amount", "matched_amount", "collected_amount", "paid_amount", "payment_amount", "amount"])

            if recon_invoice_number_col:
                recon_invoice_number_expr = _sql_identifier(recon_invoice_number_col, "ir")
                recon_status_expr = f"UPPER(TRIM(COALESCE({_sql_identifier(recon_status_col, 'ir')}, '')))" if recon_status_col else "''"
                recvd_amount_expr = _sql_identifier(recvd_amount_col, "ir") if recvd_amount_col else "0"

                recon_for_selected_sql = f"""
                    SELECT
                        COUNT(CASE WHEN recon.recon_status = 'MATCHED' THEN 1 END) AS paid_invoices,
                        COUNT(CASE WHEN recon.recon_status = 'PARTIAL MATCH' THEN 1 END) AS partially_paid,
                        COALESCE(SUM(COALESCE(recon.received_amount, 0)), 0) AS received_amount
                    FROM ({base_invoice_sql}) inv
                    LEFT JOIN (
                        SELECT
                            TRIM(COALESCE({recon_invoice_number_expr}, '')) AS invoice_number,
                            MAX({recon_status_expr}) AS recon_status,
                            SUM(COALESCE({recvd_amount_expr}, 0)) AS received_amount
                        FROM invrecon_master ir
                        WHERE TRIM(COALESCE({recon_invoice_number_expr}, '')) <> ''
                        GROUP BY TRIM(COALESCE({recon_invoice_number_expr}, ''))
                    ) recon ON recon.invoice_number = inv.invoice_number
                """
                recon_for_selected = _fetch_one(conn, recon_for_selected_sql)
                paid_invoices = _safe_int(recon_for_selected.get("paid_invoices"))
                partially_paid = _safe_int(recon_for_selected.get("partially_paid"))
                received_against_selected_invoices = round(_safe_float(recon_for_selected.get("received_amount")), 2)

                if recvd_amount_col:
                    recon_date_condition = _build_period_condition_for_columns(
                        recon_columns,
                        date_filter_meta,
                        ["pay_date", "payment_date", "status_date", "created_on", "recon_date", "matched_on", "collection_date", "updated_on"],
                        table_alias="ir",
                    )
                    collections_sql = f"""
                        SELECT COALESCE(SUM(COALESCE({recvd_amount_expr}, 0)), 0) AS collections_value
                        FROM invrecon_master ir
                        WHERE 1 = 1
                        {recon_date_condition}
                    """
                    collections_row = _fetch_one(conn, collections_sql)
                    collections_value = round(_safe_float(collections_row.get("collections_value")), 2)

        pending_invoices = invoices_generated - (paid_invoices + partially_paid)
        if pending_invoices < 0:
            pending_invoices = 0

        outstanding_amount = total_invoice_value - received_against_selected_invoices
        if outstanding_amount < 0:
            outstanding_amount = 0.0
        outstanding_amount = round(outstanding_amount, 2)

        payment_ratio = 0
        if total_invoice_value > 0:
            payment_ratio = round((1 - (outstanding_amount / total_invoice_value)) * 100)

        return {
            "invoices_generated": invoices_generated,
            "pending_invoices": pending_invoices,
            "paid_invoices": paid_invoices,
            "partially_paid": partially_paid,
            "total_invoice_value": total_invoice_value,
            "outstanding_amount": outstanding_amount,
            "collections_today": collections_value,
            "collections_label": "Collections",
            "payment_ratio": payment_ratio,
        }
    except Exception as e:
        print("[DASHBOARD] Finance summary query failed:", e)
        return finance_default

def _get_finance_trend_chart(conn, date_filter_meta):
    """Collections & Outstanding Trend from invoice_master + invrecon_master for selected period."""
    try:
        invoice_columns = _get_invoice_master_columns(conn)
        recon_columns = _get_table_columns_safe(conn, "invrecon_master")

        if not invoice_columns:
            return []

        invoice_number_col = _first_existing_column(invoice_columns, ["invoice_number", "invoice_no", "invoice_num", "bill_number"])
        invoice_amount_col = _first_existing_column(invoice_columns, [
            "netinv_amount", "totalinv_amount", "total_invoice_amount", "invoice_amount",
            "inv_amount", "sumof_invamnt", "net_amount", "gross_amount", "amount"
        ])
        invoice_date_col = _first_existing_column(invoice_columns, [
            "status_date", "created_on", "invoice_date", "generated_on",
            "invoice_generated_on", "updated_on", "entered_date", "entered_on"
        ])

        if not invoice_number_col or not invoice_date_col:
            return []

        invoice_date_condition = _build_period_condition_for_columns(
            invoice_columns,
            date_filter_meta,
            ["status_date", "created_on", "invoice_date", "generated_on", "invoice_generated_on", "updated_on", "entered_date", "entered_on"],
            table_alias="im",
        )

        invoice_number_expr = _sql_identifier(invoice_number_col, "im")
        invoice_amount_expr = _sql_identifier(invoice_amount_col, "im") if invoice_amount_col else "0"
        invoice_date_expr = _sql_identifier(invoice_date_col, "im")

        recvd_join_sql = ""
        recvd_select_sql = "0 AS collections"
        outstanding_expr = "COALESCE(SUM(inv.invoice_amount), 0) AS outstanding"

        if recon_columns and _table_exists_by_name(conn, "invrecon_master"):
            recon_invoice_number_col = _first_existing_column(recon_columns, ["invoice_number", "invoice_no", "invoice_num", "bill_number"])
            recvd_amount_col = _first_existing_column(recon_columns, ["recvd_amount", "received_amount", "matched_amount", "collected_amount", "paid_amount", "payment_amount", "amount"])
            if recon_invoice_number_col and recvd_amount_col:
                recon_invoice_number_expr = _sql_identifier(recon_invoice_number_col, "ir")
                recvd_amount_expr = _sql_identifier(recvd_amount_col, "ir")
                recvd_select_sql = "COALESCE(SUM(COALESCE(recon.received_amount, 0)), 0) AS collections"
                outstanding_expr = "COALESCE(SUM(inv.invoice_amount), 0) - COALESCE(SUM(COALESCE(recon.received_amount, 0)), 0) AS outstanding"
                recvd_join_sql = f"""
                    LEFT JOIN (
                        SELECT
                            TRIM(COALESCE({recon_invoice_number_expr}, '')) AS invoice_number,
                            SUM(COALESCE({recvd_amount_expr}, 0)) AS received_amount
                        FROM invrecon_master ir
                        WHERE TRIM(COALESCE({recon_invoice_number_expr}, '')) <> ''
                        GROUP BY TRIM(COALESCE({recon_invoice_number_expr}, ''))
                    ) recon ON recon.invoice_number = inv.invoice_number
                """

        sql = f"""
            SELECT
                inv.period_label,
                COALESCE(SUM(inv.invoice_amount), 0) AS invoice_value,
                {recvd_select_sql},
                {outstanding_expr}
            FROM (
                SELECT
                    TRIM(COALESCE({invoice_number_expr}, '')) AS invoice_number,
                    DATE_FORMAT({invoice_date_expr}, '%d-%b') AS period_label,
                    DATE({invoice_date_expr}) AS period_sort,
                    MAX(COALESCE({invoice_amount_expr}, 0)) AS invoice_amount
                FROM invoice_master im
                WHERE TRIM(COALESCE({invoice_number_expr}, '')) <> ''
                {invoice_date_condition}
                GROUP BY
                    TRIM(COALESCE({invoice_number_expr}, '')),
                    DATE_FORMAT({invoice_date_expr}, '%d-%b'),
                    DATE({invoice_date_expr})
            ) inv
            {recvd_join_sql}
            GROUP BY inv.period_label, inv.period_sort
            ORDER BY inv.period_sort ASC
        """

        rows = _fetch_all(conn, sql)
        items = []
        for row in rows[-7:]:
            collections = round(_safe_float(row.get("collections")), 2)
            outstanding = round(max(_safe_float(row.get("outstanding")), 0), 2)
            items.append({
                "label": _safe_str(row.get("period_label")),
                "value": collections,
                "outstanding": outstanding,
                "invoice_value": round(_safe_float(row.get("invoice_value")), 2),
                "color_class": "green" if collections >= outstanding else "red",
            })

        return _apply_chart_heights(items)
    except Exception as e:
        print("[DASHBOARD] Finance trend chart query failed:", e)
        return []


def _get_invoice_total(conn, date_filter_meta):
    """Return only OUTSTANDING invoice count for the dashboard invoice grid."""
    try:
        invoice_columns = _get_invoice_master_columns(conn)
        if not invoice_columns:
            return 0

        invoice_number_col = _first_existing_column(invoice_columns, ["invoice_number", "invoice_no", "invoice_num", "bill_number", "bill_no"])
        invoice_amount_col = _first_existing_column(invoice_columns, ["netinv_amount", "totalinv_amount", "total_invoice_amount", "invoice_amount", "inv_amount", "sumof_invamnt", "net_amount", "gross_amount", "amount"])

        if not invoice_number_col:
            return 0

        invoice_date_condition = _build_period_condition_for_columns(
            invoice_columns, date_filter_meta,
            ["created_on", "invoice_date", "status_date", "generated_on", "invoice_generated_on", "updated_on", "entered_date", "entered_on"],
            table_alias="im",
        )

        invoice_number_expr = _sql_identifier(invoice_number_col, "im")
        invoice_amount_expr = _sql_identifier(invoice_amount_col, "im") if invoice_amount_col else "0"

        paid_alias = "0"
        recon_join_sql = ""
        if _table_exists_by_name(conn, "invrecon_master"):
            recon_columns = _get_table_columns_safe(conn, "invrecon_master")
            recon_invoice_number_col = _first_existing_column(recon_columns, ["invoice_number", "invoice_no", "invoice_num", "bill_number", "bill_no"])
            recvd_amount_col = _first_existing_column(recon_columns, ["recvd_amount", "received_amount", "matched_amount", "collected_amount", "paid_amount", "payment_amount", "amount"])
            if recon_invoice_number_col and recvd_amount_col:
                recon_invoice_number_expr = _sql_identifier(recon_invoice_number_col, "ir")
                recvd_amount_expr = _sql_identifier(recvd_amount_col, "ir")
                paid_alias = "COALESCE(paid_src.paid_amount, 0)"
                recon_join_sql = f"""
                    LEFT JOIN (
                        SELECT
                            TRIM(COALESCE({recon_invoice_number_expr}, '')) AS invoice_number,
                            SUM(COALESCE({recvd_amount_expr}, 0)) AS paid_amount
                        FROM invrecon_master ir
                        WHERE TRIM(COALESCE({recon_invoice_number_expr}, '')) <> ''
                        GROUP BY TRIM(COALESCE({recon_invoice_number_expr}, ''))
                    ) paid_src ON paid_src.invoice_number = inv.invoice_number
                """

        sql = f"""
            SELECT COUNT(*) AS total_count
            FROM (
                SELECT inv.invoice_number
                FROM (
                    SELECT
                        TRIM(COALESCE({invoice_number_expr}, '')) AS invoice_number,
                        MAX(COALESCE({invoice_amount_expr}, 0)) AS invoice_value
                    FROM invoice_master im
                    WHERE TRIM(COALESCE({invoice_number_expr}, '')) <> ''
                    {invoice_date_condition}
                    GROUP BY TRIM(COALESCE({invoice_number_expr}, ''))
                ) inv
                {recon_join_sql}
                WHERE GREATEST(inv.invoice_value - {paid_alias}, 0) > 0.009
            ) outstanding_invoices
        """
        row = _fetch_one(conn, sql)
        return _safe_int(row.get("total_count"))
    except Exception as e:
        print("[DASHBOARD] Outstanding invoice total query failed:", e)
        return 0


def _get_invoice_rows(conn, page, per_page, date_filter_meta):
    """
    Invoice Management Summary rows.
    Shows only outstanding invoices, uses invrecon_master for paid amount,
    and displays the latest reconciliation status.
    """
    try:
        invoice_columns = _get_invoice_master_columns(conn)
        if not invoice_columns:
            return []

        page = max(1, _safe_int(page, 1))
        per_page = max(1, _safe_int(per_page, 5))
        offset = (page - 1) * per_page

        invoice_number_col = _first_existing_column(invoice_columns, ["invoice_number", "invoice_no", "invoice_num", "bill_number", "bill_no"])
        invoice_amount_col = _first_existing_column(invoice_columns, ["netinv_amount", "totalinv_amount", "total_invoice_amount", "invoice_amount", "inv_amount", "sumof_invamnt", "net_amount", "gross_amount", "amount"])
        patient_col = _first_existing_column(invoice_columns, ["patient_name", "pat_name", "patgiven_nm", "customer_name"])
        hospital_name_col = _first_existing_column(invoice_columns, ["hospital_name", "bill_to", "customer_name"])
        hospital_short_col = _first_existing_column(invoice_columns, ["hospital_shrtnm", "hospital_short_name", "short_name"])
        invoice_status_col = _first_existing_column(invoice_columns, ["invoice_status", "payment_status", "recon_status", "print_status", "status"])
        invoice_order_col = _first_existing_column(invoice_columns, ["id", "created_on", "invoice_date", "status_date", "invoice_number"])

        if not invoice_number_col:
            return []

        invoice_date_condition = _build_period_condition_for_columns(
            invoice_columns, date_filter_meta,
            ["created_on", "invoice_date", "status_date", "generated_on", "invoice_generated_on", "updated_on", "entered_date", "entered_on"],
            table_alias="im",
        )

        invoice_number_expr = _sql_identifier(invoice_number_col, "im")
        invoice_amount_expr = _sql_identifier(invoice_amount_col, "im") if invoice_amount_col else "0"
        patient_expr = _sql_identifier(patient_col, "im") if patient_col else "''"
        hospital_name_expr = _sql_identifier(hospital_name_col, "im") if hospital_name_col else "''"
        hospital_short_expr = _sql_identifier(hospital_short_col, "im") if hospital_short_col else "''"
        invoice_status_expr = _sql_identifier(invoice_status_col, "im") if invoice_status_col else "'GENERATED'"
        invoice_order_expr = _sql_identifier(invoice_order_col, "im") if invoice_order_col else invoice_number_expr

        paid_select = "0 AS paid_amount"
        balance_expr = "GREATEST(inv.invoice_value, 0)"
        recon_join_sql = ""
        latest_status_select = "'' AS latest_recon_status"
        latest_status_join_sql = ""

        if _table_exists_by_name(conn, "invrecon_master"):
            recon_columns = _get_table_columns_safe(conn, "invrecon_master")
            recon_invoice_number_col = _first_existing_column(recon_columns, ["invoice_number", "invoice_no", "invoice_num", "bill_number", "bill_no"])
            recvd_amount_col = _first_existing_column(recon_columns, ["recvd_amount", "received_amount", "matched_amount", "collected_amount", "paid_amount", "payment_amount", "amount"])
            recon_status_col = _first_existing_column(recon_columns, ["recon_status", "status", "payment_status"])
            recon_order_col = _first_existing_column(recon_columns, ["id", "updated_on", "created_on", "status_date", "recon_date", "reconciliation_date"])

            if recon_invoice_number_col:
                if recvd_amount_col:
                    recon_invoice_number_expr = _sql_identifier(recon_invoice_number_col, "ir")
                    recvd_amount_expr = _sql_identifier(recvd_amount_col, "ir")
                    paid_select = "COALESCE(paid_src.paid_amount, 0) AS paid_amount"
                    balance_expr = "GREATEST(inv.invoice_value - COALESCE(paid_src.paid_amount, 0), 0)"
                    recon_join_sql = f"""
                        LEFT JOIN (
                            SELECT
                                TRIM(COALESCE({recon_invoice_number_expr}, '')) AS invoice_number,
                                SUM(COALESCE({recvd_amount_expr}, 0)) AS paid_amount
                            FROM invrecon_master ir
                            WHERE TRIM(COALESCE({recon_invoice_number_expr}, '')) <> ''
                            GROUP BY TRIM(COALESCE({recon_invoice_number_expr}, ''))
                        ) paid_src ON paid_src.invoice_number = inv.invoice_number
                    """

                if recon_status_col and recon_order_col:
                    latest_status_select = "COALESCE(latest_recon.latest_recon_status, '') AS latest_recon_status"
                    latest_status_join_sql = f"""
                        LEFT JOIN (
                            SELECT
                                lr.invoice_number,
                                UPPER(TRIM(COALESCE(ir_status.`{recon_status_col}`, ''))) AS latest_recon_status
                            FROM (
                                SELECT
                                    TRIM(COALESCE(`{recon_invoice_number_col}`, '')) AS invoice_number,
                                    MAX(`{recon_order_col}`) AS latest_key
                                FROM invrecon_master
                                WHERE TRIM(COALESCE(`{recon_invoice_number_col}`, '')) <> ''
                                GROUP BY TRIM(COALESCE(`{recon_invoice_number_col}`, ''))
                            ) lr
                            INNER JOIN invrecon_master ir_status
                                ON TRIM(COALESCE(ir_status.`{recon_invoice_number_col}`, '')) = lr.invoice_number
                               AND ir_status.`{recon_order_col}` = lr.latest_key
                        ) latest_recon ON latest_recon.invoice_number = inv.invoice_number
                    """

        sql = f"""
            SELECT *
            FROM (
                SELECT
                    inv.invoice_number AS invoice_no,
                    inv.patient_name,
                    inv.hospital_name,
                    inv.hospital_shrtnm,
                    inv.invoice_value,
                    {paid_select},
                    {balance_expr} AS balance_amount,
                    inv.invoice_status AS invoice_master_status,
                    {latest_status_select},
                    inv.latest_invoice_key
                FROM (
                    SELECT
                        TRIM(COALESCE({invoice_number_expr}, '')) AS invoice_number,
                        MAX(COALESCE({patient_expr}, '')) AS patient_name,
                        MAX(COALESCE({hospital_name_expr}, '')) AS hospital_name,
                        MAX(COALESCE({hospital_short_expr}, '')) AS hospital_shrtnm,
                        MAX(COALESCE({invoice_amount_expr}, 0)) AS invoice_value,
                        MAX(COALESCE({invoice_status_expr}, 'GENERATED')) AS invoice_status,
                        MAX({invoice_order_expr}) AS latest_invoice_key
                    FROM invoice_master im
                    WHERE TRIM(COALESCE({invoice_number_expr}, '')) <> ''
                    {invoice_date_condition}
                    GROUP BY TRIM(COALESCE({invoice_number_expr}, ''))
                ) inv
                {recon_join_sql}
                {latest_status_join_sql}
            ) invoice_live
            WHERE COALESCE(invoice_live.balance_amount, 0) > 0.009
            ORDER BY invoice_live.latest_invoice_key DESC
            LIMIT %s OFFSET %s
        """
        rows = _fetch_all(conn, sql, (per_page, offset))

        result = []
        for row in rows:
            invoice_no = _safe_str(row.get("invoice_no"))
            invoice_value = round(_safe_float(row.get("invoice_value")), 2)
            paid_amount = round(_safe_float(row.get("paid_amount")), 2)
            balance_amount = round(max(_safe_float(row.get("balance_amount")), 0), 2)

            latest_recon_status = _safe_str(row.get("latest_recon_status")).upper()
            invoice_master_status = _safe_str(row.get("invoice_master_status")).upper()

            if latest_recon_status:
                display_status = latest_recon_status
            elif paid_amount > 0 and balance_amount > 0:
                display_status = "PARTIAL MATCH"
            elif balance_amount <= 0:
                display_status = "MATCHED"
            else:
                display_status = invoice_master_status or "PENDING"

            result.append({
                "invoice_no": invoice_no,
                "invoice_url": f"/invoice-print-pdf/{quote(invoice_no, safe='')}",
                "patient_name": _safe_str(row.get("patient_name")),
                "hospital_name": _safe_str(row.get("hospital_name")),
                "hospital_shrtnm": _safe_str(row.get("hospital_shrtnm")),
                "invoice_value": invoice_value,
                "paid_amount": paid_amount,
                "balance_amount": balance_amount,
                "invoice_status": display_status,
            })

        return result
    except Exception as e:
        print("[DASHBOARD] Outstanding invoice rows query failed:", e)
        return []

def _get_quick_alerts(summary, finance):
    alerts = []

    if _safe_int(summary.get("breached")) > 0:
        alerts.append({
            "title": "SLA Breached Cases",
            "description": f"{summary.get('breached', 0)} record(s) have crossed SLA threshold and need immediate action.",
            "badge_label": "Breached",
            "badge_class": "badge-red",
            "color_class": "red"
        })

    if _safe_int(summary.get("escalated")) > 0:
        alerts.append({
            "title": "Escalated Queue Records",
            "description": f"{summary.get('escalated', 0)} record(s) are escalated for urgent management attention.",
            "badge_label": "Escalated",
            "badge_class": "badge-purple",
            "color_class": "purple"
        })

    if _safe_int(summary.get("near_breach")) > 0:
        alerts.append({
            "title": "Near Breach Warning",
            "description": f"{summary.get('near_breach', 0)} record(s) are approaching SLA breach.",
            "badge_label": "Near Breach",
            "badge_class": "badge-amber",
            "color_class": "amber"
        })

    if _safe_float(finance.get("outstanding_amount")) > 0:
        alerts.append({
            "title": "Outstanding Receivables",
            "description": f"Outstanding invoice value is {finance.get('outstanding_amount', 0)} and requires collection follow-up.",
            "badge_label": "Finance",
            "badge_class": "badge-red",
            "color_class": "red"
        })

    if not alerts:
        alerts.append({
            "title": "No critical alerts",
            "description": "There are currently no alert or exception records to display.",
            "badge_label": "Normal",
            "badge_class": "badge-green",
            "color_class": "green"
        })

    return alerts[:6]


def get_hospital_dashboard_context(conn, request_args=None):
    request_args = request_args or {}
    context = _default_context()

    operation_queue_page = _safe_int(request_args.get("op_page", 1), 1)
    invoice_page = _safe_int(request_args.get("inv_page", 1), 1)

    operation_queue_per_page = 5
    invoice_per_page = 5

    context["selected_hospital"] = _safe_str(request_args.get("hospital"))
    context["selected_department"] = _safe_str(request_args.get("department"))

    date_filter_meta = _get_date_filter_meta(request_args.get("date_range"))
    context["selected_date_range"] = date_filter_meta.get("label", "")

    sla_status_map = _get_latest_sla_status_map(conn, date_filter_meta)

    context["summary"] = _get_operation_summary(conn, sla_status_map, date_filter_meta)
    context["finance"] = _get_finance_summary(conn, date_filter_meta)

    operation_queue_total = _get_operation_queue_total(conn, date_filter_meta)
    invoice_total = _get_invoice_total(conn, date_filter_meta)

    context["operation_queue_rows"] = _get_operation_queue_rows(
        conn=conn,
        page=operation_queue_page,
        per_page=operation_queue_per_page,
        sla_status_map=sla_status_map,
        date_filter_meta=date_filter_meta
    )

    context["invoice_rows"] = _get_invoice_rows(
        conn=conn,
        page=invoice_page,
        per_page=invoice_per_page,
        date_filter_meta=date_filter_meta
    )

    context["operation_queue_pagination"] = _build_pagination(
        request_args=request_args,
        page_param_name="op_page",
        current_page=operation_queue_page,
        total_count=operation_queue_total,
        per_page=operation_queue_per_page
    )

    context["invoice_pagination"] = _build_pagination(
        request_args=request_args,
        page_param_name="inv_page",
        current_page=invoice_page,
        total_count=invoice_total,
        per_page=invoice_per_page
    )

    context["operation_status_chart"] = _get_operation_status_chart(conn, date_filter_meta)
    context["finance_trend_chart"] = _get_finance_trend_chart(conn, date_filter_meta)
    context["quick_alerts"] = _get_quick_alerts(context["summary"], context["finance"])

    return context


def get_hospital_dashboard_export_context(conn, request_args=None):
    """
    Returns full dashboard data for Excel export.

    This keeps the same dashboard calculations and filters, but removes
    on-screen pagination so exported Excel contains all rows for the
    selected dashboard period/filter.
    """
    request_args = request_args or {}
    context = _default_context()

    context["selected_hospital"] = _safe_str(request_args.get("hospital"))
    context["selected_department"] = _safe_str(request_args.get("department"))

    date_filter_meta = _get_date_filter_meta(request_args.get("date_range"))
    context["selected_date_range"] = date_filter_meta.get("label", "")

    sla_status_map = _get_latest_sla_status_map(conn, date_filter_meta)

    context["summary"] = _get_operation_summary(conn, sla_status_map, date_filter_meta)
    context["finance"] = _get_finance_summary(conn, date_filter_meta)

    operation_queue_total = _get_operation_queue_total(conn, date_filter_meta)
    invoice_total = _get_invoice_total(conn, date_filter_meta)

    context["operation_queue_rows"] = _get_operation_queue_rows(
        conn=conn,
        page=1,
        per_page=max(operation_queue_total, 1),
        sla_status_map=sla_status_map,
        date_filter_meta=date_filter_meta
    )

    context["invoice_rows"] = _get_invoice_rows(
        conn=conn,
        page=1,
        per_page=max(invoice_total, 1),
        date_filter_meta=date_filter_meta
    )

    context["operation_status_chart"] = _get_operation_status_chart(conn, date_filter_meta)
    context["finance_trend_chart"] = _get_finance_trend_chart(conn, date_filter_meta)
    context["quick_alerts"] = _get_quick_alerts(context["summary"], context["finance"])
    context["dashboard_last_updated"] = datetime.now().strftime("%d-%b-%Y %I:%M %p")
    context["now"] = context["dashboard_last_updated"]

    context["operation_queue_pagination"] = {}
    context["invoice_pagination"] = {}

    return context

