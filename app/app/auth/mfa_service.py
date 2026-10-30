from __future__ import annotations

import hashlib
import hmac
import secrets
import time
from flask import current_app, session


def is_mfa_required(user: dict) -> bool:
    if not current_app.config.get("MFA_ENABLED"):
        return False
    mode = str(current_app.config.get("MFA_MODE") or "ALL").upper()
    if mode == "ALL":
        return True
    if mode == "PRIVILEGED":
        return str(user.get("user_type") or "").upper() in set(current_app.config.get("MFA_PRIVILEGED_USER_TYPES") or set())
    return False


def begin_mfa(user: dict) -> str:
    code = f"{secrets.randbelow(1_000_000):06d}"
    nonce = secrets.token_hex(16)
    digest = hmac.new(current_app.config["SECRET_KEY"].encode(), f"{nonce}:{code}".encode(), hashlib.sha256).hexdigest()
    session.clear()
    session["pending_mfa"] = {
        "user_code": str(user.get("user_code") or ""),
        "nonce": nonce,
        "digest": digest,
        "expires": int(time.time()) + int(current_app.config.get("MFA_OTP_TTL_SECONDS") or 300),
        "attempts": 0,
    }
    session.permanent = True
    return code


def verify_mfa(code: str) -> tuple[bool, str | None]:
    pending = session.get("pending_mfa") or {}
    if not pending or int(pending.get("expires") or 0) < int(time.time()):
        session.pop("pending_mfa", None)
        return False, None
    attempts = int(pending.get("attempts") or 0) + 1
    pending["attempts"] = attempts
    session["pending_mfa"] = pending
    if attempts > int(current_app.config.get("MFA_MAX_ATTEMPTS") or 5):
        session.pop("pending_mfa", None)
        return False, None
    expected = hmac.new(current_app.config["SECRET_KEY"].encode(), f"{pending.get('nonce')}:{str(code or '').strip()}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, str(pending.get("digest") or "")):
        return False, "invalid"
    user_code = str(pending.get("user_code") or "")
    session.pop("pending_mfa", None)
    return True, user_code
