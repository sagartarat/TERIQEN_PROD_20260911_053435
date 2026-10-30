import json
from typing import Any, Dict, List, Optional, Tuple

from app.common.db import get_db_connection


VALID_RULE_TYPES = {"DEFAULT", "AGENCY", "COUNTRY"}


def _text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    value = str(value).strip()
    return value if value else default


def _upper(value: Any, default: str = "") -> str:
    value = _text(value, default)
    return value.upper() if value else default


def _positive_int(value: Any, default: int = 1) -> int:
    try:
        result = int(value)
        return result if result > 0 else default
    except (TypeError, ValueError):
        return default


def _optional_id(value: Any) -> Optional[int]:
    try:
        result = int(value)
        return result if result > 0 else None
    except (TypeError, ValueError):
        return None


def _fetch_all(cursor) -> List[Dict[str, Any]]:
    rows = cursor.fetchall() or []
    if not rows:
        return []
    if isinstance(rows[0], dict):
        return rows
    columns = [item[0] for item in cursor.description]
    return [dict(zip(columns, row)) for row in rows]


def _fetch_one(cursor) -> Optional[Dict[str, Any]]:
    row = cursor.fetchone()
    if not row:
        return None
    if isinstance(row, dict):
        return row
    columns = [item[0] for item in cursor.description]
    return dict(zip(columns, row))


def _cursor(connection):
    try:
        return connection.cursor(dictionary=True)
    except TypeError:
        return connection.cursor()


def get_user_options() -> List[Dict[str, Any]]:
    connection = get_db_connection()
    cursor = _cursor(connection)
    try:
        cursor.execute(
            """
            SELECT user_code, user_name
            FROM user_master
            WHERE user_status = 'Active'
            ORDER BY user_name, user_code
            """
        )
        return _fetch_all(cursor)
    finally:
        cursor.close()
        connection.close()


def get_agency_options() -> List[Dict[str, Any]]:
    connection = get_db_connection()
    cursor = _cursor(connection)
    try:
        cursor.execute(
            """
            SELECT DISTINCT
                TRIM(short_name) AS short_name,
                TRIM(short_name) AS partner_name
            FROM partner_reg
            WHERE UPPER(TRIM(partner_type)) = 'AGENCY'
              AND short_name IS NOT NULL
              AND TRIM(short_name) <> ''
            ORDER BY short_name
            """
        )
        return _fetch_all(cursor)
    finally:
        cursor.close()
        connection.close()


def get_country_options() -> List[Dict[str, Any]]:
    connection = get_db_connection()
    cursor = _cursor(connection)
    try:
        cursor.execute(
            """
            SELECT DISTINCT TRIM(country_name) AS country_name
            FROM country_master
            WHERE country_name IS NOT NULL
              AND TRIM(country_name) <> ''
            ORDER BY country_name
            """
        )
        return _fetch_all(cursor)
    finally:
        cursor.close()
        connection.close()


def get_assignment_pools() -> List[Dict[str, Any]]:
    connection = get_db_connection()
    cursor = _cursor(connection)
    try:
        cursor.execute(
            """
            SELECT
                p.id,
                p.pool_name,
                p.pool_description AS description,
                p.active_flag,
                p.created_on,
                p.updated_on,
                COUNT(DISTINCT m.id) AS member_count,
                COUNT(DISTINCT r.id) AS rule_count
            FROM assignment_pool_master p
            LEFT JOIN assignment_pool_member m
                ON m.pool_id = p.id
               AND m.active_flag = 1
            LEFT JOIN assignment_rule_mapping r
                ON r.pool_id = p.id
            GROUP BY
                p.id, p.pool_name, p.pool_description, p.active_flag,
                p.created_on, p.updated_on
            ORDER BY p.pool_name, p.id
            """
        )
        return _fetch_all(cursor)
    finally:
        cursor.close()
        connection.close()


def get_configured_rules() -> List[Dict[str, Any]]:
    connection = get_db_connection()
    cursor = _cursor(connection)
    try:
        cursor.execute(
            """
            SELECT
                r.id,
                r.rule_name,
                r.rule_type,
                r.rule_value,
                r.pool_id,
                r.priority_order,
                r.active_flag,
                r.created_on,
                r.updated_on,
                p.pool_name,
                COUNT(DISTINCT CASE WHEN m.active_flag = 1 THEN m.id END) AS member_count
            FROM assignment_rule_mapping r
            INNER JOIN assignment_pool_master p
                ON p.id = r.pool_id
            LEFT JOIN assignment_pool_member m
                ON m.pool_id = p.id
            GROUP BY
                r.id, r.rule_name, r.rule_type, r.rule_value,
                r.pool_id, r.priority_order, r.active_flag,
                r.created_on, r.updated_on, p.pool_name
            ORDER BY
                CASE r.rule_type
                    WHEN 'AGENCY' THEN 1
                    WHEN 'COUNTRY' THEN 2
                    WHEN 'DEFAULT' THEN 3
                    ELSE 4
                END,
                r.priority_order,
                r.rule_name
            """
        )
        return _fetch_all(cursor)
    finally:
        cursor.close()
        connection.close()


def get_pool(pool_id: Any) -> Tuple[Dict[str, Any], List[Dict[str, Any]]]:
    pool_id = _optional_id(pool_id)
    if not pool_id:
        return {}, []

    connection = get_db_connection()
    cursor = _cursor(connection)
    try:
        cursor.execute(
            """
            SELECT id, pool_name, pool_description AS description, active_flag, created_on, updated_on
            FROM assignment_pool_master
            WHERE id = %s
            """,
            (pool_id,),
        )
        pool = _fetch_one(cursor) or {}

        cursor.execute(
            """
            SELECT
                m.id,
                m.user_code,
                u.user_name,
                m.priority_order,
                m.active_flag
            FROM assignment_pool_member m
            INNER JOIN user_master u
                ON u.user_code = m.user_code
            WHERE m.pool_id = %s
            ORDER BY m.priority_order, m.id
            """,
            (pool_id,),
        )
        return pool, _fetch_all(cursor)
    finally:
        cursor.close()
        connection.close()


def get_rule(rule_id: Any) -> Dict[str, Any]:
    rule_id = _optional_id(rule_id)
    if not rule_id:
        return {}

    connection = get_db_connection()
    cursor = _cursor(connection)
    try:
        cursor.execute(
            """
            SELECT
                id, rule_name, rule_type, rule_value,
                pool_id, priority_order, active_flag,
                created_on, updated_on
            FROM assignment_rule_mapping
            WHERE id = %s
            """,
            (rule_id,),
        )
        return _fetch_one(cursor) or {}
    finally:
        cursor.close()
        connection.close()


def _parse_pool_members(raw_json: Any) -> List[Dict[str, Any]]:
    try:
        members = json.loads(_text(raw_json, "[]"))
    except json.JSONDecodeError as exc:
        raise ValueError("Pool member data is invalid JSON.") from exc

    if not isinstance(members, list) or not members:
        raise ValueError("Add at least one user to the assignment pool.")

    cleaned: List[Dict[str, Any]] = []
    seen_users = set()

    for index, member in enumerate(members, start=1):
        if not isinstance(member, dict):
            raise ValueError("Invalid pool member entry.")

        user_code = _text(member.get("user_code"))
        if not user_code:
            raise ValueError("Every pool member must contain a User Code.")
        if user_code in seen_users:
            raise ValueError(f"User {user_code} is included more than once.")

        seen_users.add(user_code)
        cleaned.append(
            {
                "user_code": user_code,
                "priority_order": index,
                "active_flag": 1 if str(member.get("active_flag", 1)) == "1" else 0,
            }
        )

    return cleaned


def save_pool(form, current_user_code: Optional[str]) -> Dict[str, Any]:
    pool_id = _optional_id(form.get("pool_id"))
    pool_name = _text(form.get("pool_name"))
    description = _text(form.get("description")) or None
    active_flag = 1 if str(form.get("pool_active_flag", "1")) == "1" else 0

    submitted = {
        "id": pool_id or "",
        "pool_name": pool_name,
        "description": description or "",
        "active_flag": active_flag,
    }

    if not pool_name:
        return {"success": False, "error": "Pool Name is required.", "pool_form": submitted}
    if len(pool_name) > 150:
        return {"success": False, "error": "Pool Name cannot exceed 150 characters.", "pool_form": submitted}

    try:
        members = _parse_pool_members(form.get("pool_members_json"))
    except ValueError as exc:
        return {
            "success": False,
            "error": str(exc),
            "pool_form": submitted,
            "pool_members": [],
        }

    connection = get_db_connection()
    cursor = _cursor(connection)
    try:
        cursor.execute(
            """
            SELECT id
            FROM assignment_pool_master
            WHERE UPPER(TRIM(pool_name)) = UPPER(TRIM(%s))
              AND (%s IS NULL OR id <> %s)
            LIMIT 1
            """,
            (pool_name, pool_id, pool_id),
        )
        if cursor.fetchone():
            return {
                "success": False,
                "error": "Another assignment pool already uses this Pool Name.",
                "pool_form": submitted,
                "pool_members": members,
            }

        user_codes = [member["user_code"] for member in members]
        placeholders = ",".join(["%s"] * len(user_codes))
        cursor.execute(
            f"""
            SELECT user_code
            FROM user_master
            WHERE user_status = 'Active'
              AND user_code IN ({placeholders})
            """,
            tuple(user_codes),
        )
        valid_codes = {row["user_code"] if isinstance(row, dict) else row[0] for row in cursor.fetchall()}
        invalid_codes = [code for code in user_codes if code not in valid_codes]
        if invalid_codes:
            return {
                "success": False,
                "error": "Inactive or unknown users: " + ", ".join(invalid_codes),
                "pool_form": submitted,
                "pool_members": members,
            }

        if pool_id:
            cursor.execute(
                """
                UPDATE assignment_pool_master
                SET pool_name = %s,
                    pool_description = %s,
                    active_flag = %s,
                    updated_by = %s,
                    updated_on = NOW()
                WHERE id = %s
                """,
                (pool_name, description, active_flag, current_user_code, pool_id),
            )
            if cursor.rowcount == 0:
                connection.rollback()
                return {"success": False, "error": "Assignment Pool was not found.", "pool_form": submitted}
        else:
            cursor.execute(
                """
                INSERT INTO assignment_pool_master
                    (pool_name, pool_description, active_flag, created_by, updated_by)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (pool_name, description, active_flag, current_user_code, current_user_code),
            )
            pool_id = cursor.lastrowid

        cursor.execute("DELETE FROM assignment_pool_member WHERE pool_id = %s", (pool_id,))
        cursor.executemany(
            """
            INSERT INTO assignment_pool_member
                (pool_id, user_code, priority_order, active_flag, created_by, updated_by)
            VALUES (%s, %s, %s, %s, %s, %s)
            """,
            [
                (
                    pool_id,
                    member["user_code"],
                    member["priority_order"],
                    member["active_flag"],
                    current_user_code,
                    current_user_code,
                )
                for member in members
            ],
        )

        connection.commit()
        return {"success": True, "pool_id": pool_id, "message": "Assignment Pool saved successfully."}
    except Exception as exc:
        connection.rollback()
        return {"success": False, "error": str(exc), "pool_form": submitted, "pool_members": members}
    finally:
        cursor.close()
        connection.close()


def save_rule(form, current_user_code: Optional[str]) -> Dict[str, Any]:
    rule_id = _optional_id(form.get("rule_id"))
    rule_name = _text(form.get("rule_name"))
    rule_type = _upper(form.get("rule_type"))
    rule_value = _text(form.get("rule_value")) or None
    pool_id = _optional_id(form.get("pool_id"))
    priority_order = _positive_int(form.get("priority_order"), 1)
    active_flag = 1 if str(form.get("active_flag", "1")) == "1" else 0

    submitted = {
        "id": rule_id or "",
        "rule_name": rule_name,
        "rule_type": rule_type,
        "rule_value": rule_value or "",
        "pool_id": pool_id or "",
        "priority_order": priority_order,
        "active_flag": active_flag,
    }

    if not rule_name:
        return {"success": False, "error": "Rule Name is required.", "rule_form": submitted}
    if rule_type not in VALID_RULE_TYPES:
        return {"success": False, "error": "Select Default, Agency, or Country.", "rule_form": submitted}
    if rule_type == "DEFAULT":
        rule_value = None
        submitted["rule_value"] = ""
    elif not rule_value:
        return {"success": False, "error": f"Rule Value is required for {rule_type.title()}.", "rule_form": submitted}
    if not pool_id:
        return {"success": False, "error": "Assignment Pool is required.", "rule_form": submitted}

    connection = get_db_connection()
    cursor = _cursor(connection)
    try:
        cursor.execute(
            "SELECT id FROM assignment_pool_master WHERE id = %s AND active_flag = 1",
            (pool_id,),
        )
        if not cursor.fetchone():
            return {"success": False, "error": "Select an active Assignment Pool.", "rule_form": submitted}

        cursor.execute(
            "SELECT COUNT(*) AS cnt FROM assignment_pool_member WHERE pool_id = %s AND active_flag = 1",
            (pool_id,),
        )
        count_row = _fetch_one(cursor) or {"cnt": 0}
        if int(count_row.get("cnt") or 0) == 0:
            return {"success": False, "error": "Selected pool has no active users.", "rule_form": submitted}

        if rule_type == "AGENCY":
            cursor.execute(
                """
                SELECT 1 FROM partner_reg
                WHERE UPPER(TRIM(partner_type)) = 'AGENCY'
                  AND UPPER(TRIM(short_name)) = UPPER(TRIM(%s))
                LIMIT 1
                """,
                (rule_value,),
            )
            if not cursor.fetchone():
                return {"success": False, "error": "Selected Agency does not exist.", "rule_form": submitted}
        elif rule_type == "COUNTRY":
            cursor.execute(
                """
                SELECT 1 FROM country_master
                WHERE UPPER(TRIM(country_name)) = UPPER(TRIM(%s))
                LIMIT 1
                """,
                (rule_value,),
            )
            if not cursor.fetchone():
                return {"success": False, "error": "Selected Country does not exist.", "rule_form": submitted}

        cursor.execute(
            """
            SELECT id FROM assignment_rule_mapping
            WHERE UPPER(TRIM(rule_name)) = UPPER(TRIM(%s))
              AND (%s IS NULL OR id <> %s)
            LIMIT 1
            """,
            (rule_name, rule_id, rule_id),
        )
        if cursor.fetchone():
            return {"success": False, "error": "Another rule already uses this Rule Name.", "rule_form": submitted}

        if rule_type == "DEFAULT":
            cursor.execute(
                """
                SELECT id FROM assignment_rule_mapping
                WHERE rule_type = 'DEFAULT'
                  AND (%s IS NULL OR id <> %s)
                LIMIT 1
                """,
                (rule_id, rule_id),
            )
        else:
            cursor.execute(
                """
                SELECT id FROM assignment_rule_mapping
                WHERE rule_type = %s
                  AND UPPER(TRIM(rule_value)) = UPPER(TRIM(%s))
                  AND (%s IS NULL OR id <> %s)
                LIMIT 1
                """,
                (rule_type, rule_value, rule_id, rule_id),
            )
        if cursor.fetchone():
            return {"success": False, "error": "A rule already exists for this Rule Type and Rule Value.", "rule_form": submitted}

        if rule_id:
            cursor.execute(
                """
                UPDATE assignment_rule_mapping
                SET rule_name = %s,
                    rule_type = %s,
                    rule_value = %s,
                    pool_id = %s,
                    priority_order = %s,
                    active_flag = %s,
                    updated_by = %s,
                    updated_on = NOW()
                WHERE id = %s
                """,
                (
                    rule_name, rule_type, rule_value, pool_id,
                    priority_order, active_flag, current_user_code, rule_id,
                ),
            )
            if cursor.rowcount == 0:
                connection.rollback()
                return {"success": False, "error": "Assignment Rule was not found.", "rule_form": submitted}
        else:
            cursor.execute(
                """
                INSERT INTO assignment_rule_mapping
                    (rule_name, rule_type, rule_value, pool_id,
                     priority_order, active_flag, created_by, updated_by)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s)
                """,
                (
                    rule_name, rule_type, rule_value, pool_id,
                    priority_order, active_flag, current_user_code, current_user_code,
                ),
            )
            rule_id = cursor.lastrowid

        cursor.execute(
            """
            INSERT INTO assignment_round_robin_state
                (rule_id, pool_id, assignment_count)
            VALUES (%s, %s, 0)
            ON DUPLICATE KEY UPDATE
                pool_id = VALUES(pool_id),
                last_member_id = CASE
                    WHEN pool_id <> VALUES(pool_id) THEN NULL
                    ELSE last_member_id
                END,
                last_user_code = CASE
                    WHEN pool_id <> VALUES(pool_id) THEN NULL
                    ELSE last_user_code
                END,
                last_assigned_on = CASE
                    WHEN pool_id <> VALUES(pool_id) THEN NULL
                    ELSE last_assigned_on
                END,
                updated_on = NOW()
            """,
            (rule_id, pool_id),
        )

        connection.commit()
        return {"success": True, "rule_id": rule_id, "message": "Assignment Rule saved successfully."}
    except Exception as exc:
        connection.rollback()
        return {"success": False, "error": str(exc), "rule_form": submitted}
    finally:
        cursor.close()
        connection.close()


def get_round_robin_statuses() -> List[Dict[str, Any]]:
    connection = get_db_connection()
    cursor = _cursor(connection)
    try:
        cursor.execute(
            """
            SELECT
                r.id AS rule_id,
                r.rule_name,
                r.rule_type,
                r.rule_value,
                p.pool_name,
                s.last_user_code,
                last_user.user_name AS last_user_name,
                s.last_assigned_on,
                COUNT(DISTINCT CASE WHEN m.active_flag = 1 THEN m.id END) AS member_count,
                (
                    SELECT nm.user_code
                    FROM assignment_pool_member nm
                    WHERE nm.pool_id = r.pool_id
                      AND nm.active_flag = 1
                    ORDER BY
                        CASE
                            WHEN s.last_member_id IS NULL THEN 0
                            WHEN nm.priority_order > COALESCE(last_member.priority_order, 0) THEN 0
                            ELSE 1
                        END,
                        nm.priority_order,
                        nm.id
                    LIMIT 1
                ) AS next_user_code,
                (
                    SELECT nu.user_name
                    FROM assignment_pool_member nm
                    INNER JOIN user_master nu ON nu.user_code = nm.user_code
                    WHERE nm.pool_id = r.pool_id
                      AND nm.active_flag = 1
                    ORDER BY
                        CASE
                            WHEN s.last_member_id IS NULL THEN 0
                            WHEN nm.priority_order > COALESCE(last_member.priority_order, 0) THEN 0
                            ELSE 1
                        END,
                        nm.priority_order,
                        nm.id
                    LIMIT 1
                ) AS next_user_name
            FROM assignment_rule_mapping r
            INNER JOIN assignment_pool_master p ON p.id = r.pool_id
            LEFT JOIN assignment_round_robin_state s ON s.rule_id = r.id
            LEFT JOIN assignment_pool_member last_member ON last_member.id = s.last_member_id
            LEFT JOIN user_master last_user ON last_user.user_code = s.last_user_code
            LEFT JOIN assignment_pool_member m ON m.pool_id = p.id
            GROUP BY
                r.id, r.rule_name, r.rule_type, r.rule_value,
                p.pool_name, s.last_user_code, last_user.user_name,
                s.last_assigned_on, s.last_member_id, last_member.priority_order
            ORDER BY
                CASE r.rule_type
                    WHEN 'AGENCY' THEN 1
                    WHEN 'COUNTRY' THEN 2
                    WHEN 'DEFAULT' THEN 3
                    ELSE 4
                END,
                r.priority_order,
                r.rule_name
            """
        )
        return _fetch_all(cursor)
    finally:
        cursor.close()
        connection.close()


def get_summary_counts() -> Dict[str, int]:
    connection = get_db_connection()
    cursor = _cursor(connection)
    try:
        cursor.execute(
            """
            SELECT
                (SELECT COUNT(*) FROM assignment_rule_mapping WHERE active_flag = 1) AS active_rule_count,
                (SELECT COUNT(*) FROM assignment_pool_master) AS pool_count,
                (SELECT COUNT(DISTINCT user_code) FROM assignment_pool_member WHERE active_flag = 1) AS mapped_user_count,
                (SELECT COUNT(*) FROM assignment_round_robin_log WHERE assigned_on >= CURDATE()) AS assignments_today
            """
        )
        row = _fetch_one(cursor) or {}
        return {key: int(row.get(key) or 0) for key in (
            "active_rule_count", "pool_count", "mapped_user_count", "assignments_today"
        )}
    finally:
        cursor.close()
        connection.close()


def build_page_context(
    active_tab: str = "rules",
    rule_id: Any = None,
    pool_id: Any = None,
    rule_form: Optional[Dict[str, Any]] = None,
    pool_form: Optional[Dict[str, Any]] = None,
    pool_members: Optional[List[Dict[str, Any]]] = None,
    rule_error: Optional[str] = None,
    rule_success: Optional[str] = None,
    pool_error: Optional[str] = None,
    pool_success: Optional[str] = None,
) -> Dict[str, Any]:
    active_tab = active_tab if active_tab in {"rules", "pools", "status"} else "rules"

    if rule_form is None:
        rule_form = get_rule(rule_id) if rule_id else {}

    if pool_form is None:
        loaded_pool, loaded_members = get_pool(pool_id) if pool_id else ({}, [])
        pool_form = loaded_pool
        pool_members = loaded_members

    pool_members = pool_members or []
    summary = get_summary_counts()

    return {
        "active_tab": active_tab,
        "rule_form": rule_form or {},
        "pool_form": pool_form or {},
        "pool_members_json": json.dumps(pool_members, default=str),
        "rule_error": rule_error,
        "rule_success": rule_success,
        "pool_error": pool_error,
        "pool_success": pool_success,
        "agency_options": get_agency_options(),
        "country_options": get_country_options(),
        "user_options": get_user_options(),
        "assignment_pools": get_assignment_pools(),
        "configured_rules": get_configured_rules(),
        "round_robin_statuses": get_round_robin_statuses(),
        **summary,
    }
