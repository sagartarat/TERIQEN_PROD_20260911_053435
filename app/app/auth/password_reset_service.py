import hashlib
import secrets
from datetime import datetime, timedelta
from werkzeug.security import check_password_hash, generate_password_hash

from app.common.db import get_db_connection


TOKEN_EXPIRY_MINUTES = 30


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def create_password_reset_token(user_code, user_email, ip_address=None, user_agent=None):
    raw_token = secrets.token_urlsafe(48)
    token_hash = _hash_token(raw_token)
    expires_at = datetime.now() + timedelta(minutes=TOKEN_EXPIRY_MINUTES)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            UPDATE password_reset_token
               SET token_status = 'USED',
                   used_at = NOW()
             WHERE user_code = %s
               AND token_status = 'ACTIVE'
        """, (user_code,))

        cursor.execute("""
            INSERT INTO password_reset_token
            (
                user_code,
                user_email,
                reset_token,
                token_hash,
                token_status,
                expires_at,
                used_at,
                created_at,
                ip_address,
                user_agent
            )
            VALUES
            (
                %s, %s, %s, %s, 'ACTIVE', %s, NULL, NOW(), %s, %s
            )
        """, (
            user_code,
            user_email,
            token_hash,
            token_hash,
            expires_at,
            ip_address,
            user_agent
        ))

        conn.commit()
        return raw_token

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()


def get_user_by_email_or_code(identifier):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT user_code,
                   user_name,
                   user_email,
                   user_status
              FROM user_master
             WHERE LOWER(user_email) = LOWER(%s)
                OR LOWER(user_code) = LOWER(%s)
             LIMIT 1
        """, (identifier, identifier))

        return cursor.fetchone()

    finally:
        cursor.close()
        conn.close()


def validate_reset_token(token):
    token_hash = _hash_token(token)

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            UPDATE password_reset_token
               SET token_status = 'EXPIRED'
             WHERE token_status = 'ACTIVE'
               AND expires_at < NOW()
        """)

        conn.commit()

        cursor.execute("""
            SELECT prt.id,
                   prt.user_code,
                   prt.user_email,
                   prt.expires_at,
                   prt.token_status,
                   um.user_name,
                   um.user_status
              FROM password_reset_token prt
              JOIN user_master um
                ON um.user_code = prt.user_code
             WHERE prt.token_hash = %s
               AND prt.token_status = 'ACTIVE'
               AND prt.expires_at >= NOW()
               AND UPPER(TRIM(COALESCE(um.user_status, ''))) = 'ACTIVE'
             LIMIT 1
        """, (token_hash,))

        return cursor.fetchone()

    finally:
        cursor.close()
        conn.close()


def _matches_password_hash(password, stored_hash):
    if not stored_hash:
        return False

    try:
        return check_password_hash(stored_hash, password)
    except (ValueError, TypeError):
        return False


def reset_user_password(token, new_password):
    token_row = validate_reset_token(token)

    if not token_row:
        return False, "Invalid or expired reset link."

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        # Lock the user row while the password is being changed.
        cursor.execute("""
            SELECT user_password
              FROM user_master
             WHERE user_code = %s
             FOR UPDATE
        """, (token_row["user_code"],))

        user_row = cursor.fetchone()

        if not user_row:
            conn.rollback()
            return False, "User account could not be found."

        current_password_hash = user_row.get("user_password")

        # Current password + previous 4 = last 5 passwords protected.
        password_hashes = []

        if current_password_hash:
            password_hashes.append(current_password_hash)

        cursor.execute("""
            SELECT password_hash
              FROM user_password_history
             WHERE user_code = %s
             ORDER BY changed_at DESC, id DESC
             LIMIT 4
        """, (token_row["user_code"],))

        history_rows = cursor.fetchall() or []

        for row in history_rows:
            if row.get("password_hash"):
                password_hashes.append(row["password_hash"])

        for stored_hash in password_hashes:
            if _matches_password_hash(new_password, stored_hash):
                conn.rollback()
                return False, (
                    "You cannot reuse any of your last 5 passwords. "
                    "Please choose a different password."
                )

        hashed_password = generate_password_hash(new_password)

        # Preserve the password that is being replaced.
        if current_password_hash:
            cursor.execute("""
                INSERT INTO user_password_history
                    (user_code, password_hash)
                VALUES (%s, %s)
            """, (
                token_row["user_code"],
                current_password_hash
            ))

        cursor.execute("""
            UPDATE user_master
               SET user_password = %s
             WHERE user_code = %s
        """, (
            hashed_password,
            token_row["user_code"]
        ))

        # Keep only the four previous passwords in history.
        # Together with the current password in user_master,
        # this protects the last five passwords.
        cursor.execute("""
            SELECT id
              FROM user_password_history
             WHERE user_code = %s
             ORDER BY changed_at DESC, id DESC
        """, (token_row["user_code"],))

        history_ids = [
            row["id"]
            for row in (cursor.fetchall() or [])
        ]

        old_ids = history_ids[4:]

        if old_ids:
            placeholders = ", ".join(["%s"] * len(old_ids))

            cursor.execute(
                f"""
                DELETE FROM user_password_history
                 WHERE user_code = %s
                   AND id IN ({placeholders})
                """,
                tuple([token_row["user_code"]] + old_ids)
            )

        cursor.execute("""
            UPDATE password_reset_token
               SET token_status = 'USED',
                   used_at = NOW()
             WHERE id = %s
        """, (token_row["id"],))

        conn.commit()

        return True, (
            "Password reset successfully. "
            "Please login with your new password."
        )

    except Exception:
        conn.rollback()
        raise

    finally:
        cursor.close()
        conn.close()

def validate_password_rules(password):
    errors = []

    if len(password) < 12:
        errors.append("Password must be at least 12 characters long.")

    if not any(ch.isupper() for ch in password):
        errors.append("Password must contain at least one uppercase letter.")

    if not any(ch.islower() for ch in password):
        errors.append("Password must contain at least one lowercase letter.")

    if not any(ch.isdigit() for ch in password):
        errors.append("Password must contain at least one number.")

    if not any(ch in "!@#$%^&*()_+-=[]{}|;:,.<>?" for ch in password):
        errors.append("Password must contain at least one special character.")

    if any(ch.isspace() for ch in password):
        errors.append("Password must not contain spaces.")

    return errors