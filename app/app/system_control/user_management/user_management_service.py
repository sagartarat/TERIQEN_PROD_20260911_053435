from werkzeug.security import generate_password_hash

from app.common.db import get_db_connection


USER_STATUS_LIST = ["Active", "Inactive"]
USER_TYPE_LIST = ["User", "Admin"]
USER_GENDER_LIST = ["MALE", "FEMALE", "OTHERS"]


def _row_to_dict(cursor, row):
    if not row:
        return None

    cols = [column[0] for column in cursor.description]
    return dict(zip(cols, row))


def get_user_management_context(
    selected_user_code=None,
    success_message=None,
    error_message=None
):
    users = []
    selected_user = None

    conn = get_db_connection()

    if not conn:
        return {
            "users": users,
            "selected_user": selected_user,
            "status_list": USER_STATUS_LIST,
            "type_list": USER_TYPE_LIST,
            "gender_list": USER_GENDER_LIST,
            "success_message": None,
            "error_message": "Database connection failed.",
        }

    cursor = None

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT
                user_name,
                user_code,
                user_email,
                user_dob,
                user_gender,
                user_conctno AS user_contno,
                user_status,
                user_type
            FROM user_master
            ORDER BY user_name ASC
        """)

        cols = [column[0] for column in cursor.description]
        users = [
            dict(zip(cols, row))
            for row in cursor.fetchall()
        ]

        if selected_user_code:
            selected_user_code = str(selected_user_code).strip().upper()

            cursor.execute("""
                SELECT
                    user_name,
                    user_code,
                    user_email,
                    user_dob,
                    user_gender,
                    user_conctno AS user_contno,
                    user_status,
                    user_type
                FROM user_master
                WHERE user_code = %s
                LIMIT 1
            """, (selected_user_code,))

            selected_user = _row_to_dict(
                cursor,
                cursor.fetchone()
            )

    except Exception as error:
        error_message = str(error)

    finally:
        if cursor:
            cursor.close()

        conn.close()

    return {
        "users": users,
        "selected_user": selected_user,
        "status_list": USER_STATUS_LIST,
        "type_list": USER_TYPE_LIST,
        "gender_list": USER_GENDER_LIST,
        "success_message": success_message,
        "error_message": error_message,
    }


def save_user(form):
    user_code = (
        form.get("user_code") or ""
    ).strip().upper()

    mode = (
        form.get("mode") or "add"
    ).strip().lower()

    if not user_code:
        return {
            "success": False,
            "error": "User Code is required.",
        }

    if mode not in ("add", "edit", "delete"):
        return {
            "success": False,
            "error": "Invalid operation mode.",
        }

    conn = get_db_connection()

    if not conn:
        return {
            "success": False,
            "error": "Database connection failed.",
        }

    cursor = None

    try:
        cursor = conn.cursor()

        cursor.execute("""
            SELECT COUNT(*)
            FROM user_master
            WHERE user_code = %s
        """, (user_code,))

        exists = cursor.fetchone()[0] > 0

        # ==========================================================
        # DELETE USER
        # ==========================================================
        if mode == "delete":
            if not exists:
                return {
                    "success": False,
                    "error": "User does not exist or has already been deleted.",
                }

            cursor.execute("""
                DELETE FROM user_master
                WHERE user_code = %s
            """, (user_code,))

            conn.commit()

            return {
                "success": True,
                "message": f"User {user_code} deleted successfully.",
                "user_code": None,
            }

        # ==========================================================
        # READ ADD / EDIT VALUES
        # ==========================================================
        user_name = (
            form.get("user_name") or ""
        ).strip()

        user_email = (
            form.get("user_email") or ""
        ).strip() or None

        user_dob = form.get("user_dob") or None

        user_gender = (
            form.get("user_gender") or ""
        ).strip().upper()

        user_conctno = (
            form.get("user_contno")
            or form.get("user_conctno")
            or ""
        ).strip() or None

        user_status = (
            form.get("user_status") or "Active"
        ).strip()

        user_type = (
            form.get("user_type") or "User"
        ).strip()

        user_password = (
            form.get("user_password") or ""
        ).strip()

        # ==========================================================
        # VALIDATION
        # ==========================================================
        if not user_name:
            return {
                "success": False,
                "error": "User Name is required.",
            }

        if user_gender not in USER_GENDER_LIST:
            return {
                "success": False,
                "error": "Invalid Gender.",
            }

        if user_status not in USER_STATUS_LIST:
            return {
                "success": False,
                "error": "Invalid User Status.",
            }

        if user_type not in USER_TYPE_LIST:
            return {
                "success": False,
                "error": "Invalid User Type.",
            }

        if mode == "add" and exists:
            return {
                "success": False,
                "error": (
                    "User Code already exists. "
                    "Please select the user and modify it."
                ),
            }

        if mode == "edit" and not exists:
            return {
                "success": False,
                "error": "Selected user does not exist.",
            }

        if mode == "add" and not user_password:
            return {
                "success": False,
                "error": "Password is required for new user creation.",
            }

        # ==========================================================
        # UPDATE EXISTING USER
        # ==========================================================
        if mode == "edit":
            if user_password:
                password_hash = generate_password_hash(user_password)

                cursor.execute("""
                    UPDATE user_master
                    SET
                        user_name = %s,
                        user_email = %s,
                        user_dob = %s,
                        user_gender = %s,
                        user_conctno = %s,
                        user_status = %s,
                        user_type = %s,
                        user_password = %s
                    WHERE user_code = %s
                """, (
                    user_name,
                    user_email,
                    user_dob,
                    user_gender,
                    user_conctno,
                    user_status,
                    user_type,
                    password_hash,
                    user_code,
                ))

            else:
                cursor.execute("""
                    UPDATE user_master
                    SET
                        user_name = %s,
                        user_email = %s,
                        user_dob = %s,
                        user_gender = %s,
                        user_conctno = %s,
                        user_status = %s,
                        user_type = %s
                    WHERE user_code = %s
                """, (
                    user_name,
                    user_email,
                    user_dob,
                    user_gender,
                    user_conctno,
                    user_status,
                    user_type,
                    user_code,
                ))

            message = "User modified successfully."

        # ==========================================================
        # INSERT NEW USER
        # ==========================================================
        else:
            password_hash = generate_password_hash(user_password)

            cursor.execute("""
                INSERT INTO user_master
                (
                    user_name,
                    user_code,
                    user_email,
                    user_dob,
                    user_gender,
                    user_conctno,
                    user_status,
                    user_type,
                    user_password
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
            """, (
                user_name,
                user_code,
                user_email,
                user_dob,
                user_gender,
                user_conctno,
                user_status,
                user_type,
                password_hash,
            ))

            message = "User added successfully."

        conn.commit()

        return {
            "success": True,
            "message": message,
            "user_code": user_code,
        }

    except Exception as error:
        conn.rollback()

        error_text = str(error)

        if mode == "delete":
            error_text = (
                "User could not be deleted. The user may be referenced by "
                "assignment, workflow, alert or audit records. Database error: "
                f"{error_text}"
            )

        return {
            "success": False,
            "error": error_text,
        }

    finally:
        if cursor:
            cursor.close()

        conn.close()
