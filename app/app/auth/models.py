from flask_login import UserMixin
from app.extensions import login_manager
from app.common.db import get_db


class User(UserMixin):
    def __init__(self, user_code, user_name=None, user_type=None, user_status=None):
        self.id = str(user_code)
        self.user_code = str(user_code)
        self.user_name = user_name
        self.user_type = user_type
        self.user_status = user_status


@login_manager.user_loader
def load_user(user_id):
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT user_code, user_name, user_type, user_status FROM user_master WHERE user_code = %s",
            (user_id,),
        )
        user = cursor.fetchone()
        cursor.close()
        conn.close()

        if user and str(user.get("user_status") or "").strip().upper() == "ACTIVE":
            return User(
                user["user_code"],
                user.get("user_name"),
                user.get("user_type"),
                user.get("user_status"),
            )
        return None
    except Exception as e:
        print("user_loader error:", e)
        return None