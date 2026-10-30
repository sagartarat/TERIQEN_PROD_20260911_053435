from app.common.db import get_db_connection


YES_NO = {"Y", "N"}


def _fetch_dicts(cursor):
    cols = [c[0] for c in cursor.description]
    return [dict(zip(cols, row)) for row in cursor.fetchall()]


def _normalize_yn(value, default="N"):
    value = (value or default or "N").strip().upper()
    return value if value in YES_NO else default


def get_user_screen_access_context(selected_user_code=None, success_message=None, error_message=None):
    users = []
    screens = []
    selected_access = []

    selected_user_code = (selected_user_code or "").strip().upper()

    conn = get_db_connection()
    if not conn:
        return {
            "users": users,
            "screens": screens,
            "selected_user_code": selected_user_code,
            "selected_access": selected_access,
            "success_message": success_message,
            "error_message": error_message or "Database connection failed.",
        }

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                user_code,
                user_name,
                user_status,
                user_type
            FROM user_master
            ORDER BY user_name ASC, user_code ASC
        """)
        users = _fetch_dicts(cursor)

        cursor.execute("""
            SELECT
                sm.screen_code,
                sm.screen_name,
                sm.module_name,
                sm.endpoint_name,
                sm.screen_url,
                sm.menu_order,
                COALESCE(sm.is_active, 'Y') AS is_active,
                COALESCE(usa.can_view, 'N') AS can_view,
                COALESCE(usa.can_add, 'N') AS can_add,
                COALESCE(usa.can_edit, 'N') AS can_edit,
                COALESCE(usa.can_delete, 'N') AS can_delete
            FROM screen_master sm
            LEFT JOIN user_screen_access usa
                ON usa.screen_code = sm.screen_code
               AND usa.user_code = %s
               AND COALESCE(usa.is_active, 'Y') = 'Y'
            WHERE COALESCE(sm.is_active, 'Y') = 'Y'
            ORDER BY
                sm.module_name,
                COALESCE(sm.menu_order, 9999),
                sm.screen_name
        """, (selected_user_code,))

        screens = _fetch_dicts(cursor)

        if selected_user_code:
            selected_access = [
                {
                    "screen_code": row.get("screen_code"),
                    "can_view": row.get("can_view") or "N",
                    "can_add": row.get("can_add") or "N",
                    "can_edit": row.get("can_edit") or "N",
                    "can_delete": row.get("can_delete") or "N",
                }
                for row in screens
            ]

        cursor.close()

    except Exception as e:
        error_message = str(e)
    finally:
        conn.close()

    return {
        "users": users,
        "screens": screens,
        "selected_user_code": selected_user_code,
        "selected_access": selected_access,
        "success_message": success_message,
        "error_message": error_message,
    }


def get_user_screen_access_for_user(user_code):
    user_code = (user_code or "").strip().upper()

    if not user_code:
        return {"success": False, "message": "User Code is required.", "access": []}

    conn = get_db_connection()
    if not conn:
        return {"success": False, "message": "Database connection failed.", "access": []}

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*)
            FROM user_master
            WHERE user_code = %s
        """, (user_code,))
        if cursor.fetchone()[0] == 0:
            cursor.close()
            return {"success": False, "message": "Selected user does not exist.", "access": []}

        cursor.execute("""
            SELECT
                sm.screen_code,
                COALESCE(usa.can_view, 'N') AS can_view,
                COALESCE(usa.can_add, 'N') AS can_add,
                COALESCE(usa.can_edit, 'N') AS can_edit,
                COALESCE(usa.can_delete, 'N') AS can_delete
            FROM screen_master sm
            LEFT JOIN user_screen_access usa
                ON usa.screen_code = sm.screen_code
               AND usa.user_code = %s
               AND COALESCE(usa.is_active, 'Y') = 'Y'
            WHERE COALESCE(sm.is_active, 'Y') = 'Y'
            ORDER BY
                sm.module_name,
                COALESCE(sm.menu_order, 9999),
                sm.screen_name
        """, (user_code,))

        access = _fetch_dicts(cursor)
        cursor.close()

        return {
            "success": True,
            "message": "Access loaded successfully.",
            "access": access,
        }

    except Exception as e:
        return {"success": False, "message": str(e), "access": []}
    finally:
        conn.close()


def save_user_screen_access_payload(payload, updated_by="SYSTEM"):
    user_code = (payload.get("user_code") or "").strip().upper()
    access_rows = payload.get("access") or []

    if not user_code:
        return {"success": False, "message": "Please select a user."}

    if not isinstance(access_rows, list):
        return {"success": False, "message": "Invalid access payload."}

    conn = get_db_connection()
    if not conn:
        return {"success": False, "message": "Database connection failed."}

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*)
            FROM user_master
            WHERE user_code = %s
        """, (user_code,))
        if cursor.fetchone()[0] == 0:
            cursor.close()
            return {"success": False, "message": "Selected user does not exist."}

        cursor.execute("""
            SELECT screen_code
            FROM screen_master
            WHERE COALESCE(is_active, 'Y') = 'Y'
        """)
        valid_screen_codes = {row[0] for row in cursor.fetchall()}

        saved_count = 0

        for item in access_rows:
            screen_code = (item.get("screen_code") or "").strip().upper()
            if not screen_code or screen_code not in valid_screen_codes:
                continue

            can_view = _normalize_yn(item.get("can_view"))
            can_add = _normalize_yn(item.get("can_add"))
            can_edit = _normalize_yn(item.get("can_edit"))
            can_delete = _normalize_yn(item.get("can_delete"))

            if can_add == "Y" or can_edit == "Y" or can_delete == "Y":
                can_view = "Y"

            cursor.execute("""
                INSERT INTO user_screen_access
                (
                    user_code,
                    screen_code,
                    can_view,
                    can_add,
                    can_edit,
                    can_delete,
                    is_active,
                    created_on,
                    updated_on
                )
                VALUES (%s, %s, %s, %s, %s, %s, 'Y', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)
                ON DUPLICATE KEY UPDATE
                    can_view = VALUES(can_view),
                    can_add = VALUES(can_add),
                    can_edit = VALUES(can_edit),
                    can_delete = VALUES(can_delete),
                    is_active = 'Y',
                    updated_on = CURRENT_TIMESTAMP
            """, (
                user_code,
                screen_code,
                can_view,
                can_add,
                can_edit,
                can_delete,
            ))
            saved_count += 1

        conn.commit()
        cursor.close()

        return {
            "success": True,
            "message": f"User screen access saved successfully. Screens processed: {saved_count}.",
        }

    except Exception as e:
        conn.rollback()
        return {"success": False, "message": str(e)}
    finally:
        conn.close()


def get_user_screen_permissions(user_code, user_type=None):
    user_code = (user_code or "").strip().upper()
    user_type = (user_type or "").strip()

    permissions = {}
    allowed_screens = []

    if not user_code:
        return allowed_screens, permissions

    conn = get_db_connection()
    if not conn:
        return allowed_screens, permissions

    try:
        cursor = conn.cursor()

        if user_type == "Admin":
            cursor.execute("""
                SELECT screen_code
                FROM screen_master
                WHERE COALESCE(is_active, 'Y') = 'Y'
            """)
            rows = cursor.fetchall()
            for row in rows:
                code = row[0]
                allowed_screens.append(code)
                permissions[code] = {
                    "can_view": "Y",
                    "can_add": "Y",
                    "can_edit": "Y",
                    "can_delete": "Y",
                }
        else:
            cursor.execute("""
                SELECT
                    usa.screen_code,
                    usa.can_view,
                    usa.can_add,
                    usa.can_edit,
                    usa.can_delete
                FROM user_screen_access usa
                INNER JOIN screen_master sm
                    ON sm.screen_code = usa.screen_code
                   AND COALESCE(sm.is_active, 'Y') = 'Y'
                WHERE usa.user_code = %s
                  AND usa.can_view = 'Y'
                  AND COALESCE(usa.is_active, 'Y') = 'Y'
            """, (user_code,))
            cols = [c[0] for c in cursor.description]
            for row in cursor.fetchall():
                item = dict(zip(cols, row))
                code = item["screen_code"]
                allowed_screens.append(code)
                permissions[code] = {
                    "can_view": item.get("can_view") or "N",
                    "can_add": item.get("can_add") or "N",
                    "can_edit": item.get("can_edit") or "N",
                    "can_delete": item.get("can_delete") or "N",
                }

        cursor.close()

    except Exception:
        allowed_screens = []
        permissions = {}
    finally:
        conn.close()

    return allowed_screens, permissions