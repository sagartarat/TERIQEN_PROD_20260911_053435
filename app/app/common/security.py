from __future__ import annotations

import os
import hashlib
import hmac
import time
from flask import abort, current_app, jsonify, request, session, redirect, url_for
from flask_login import current_user, logout_user
from werkzeug.middleware.proxy_fix import ProxyFix


_PLACEHOLDERS = {
    "",
    "dev-secret-change-me",
    "development-only-change-me",
    "supersecretkey",
    "changeme",
}


def validate_production_config(app):
    if not app.config.get("IS_PRODUCTION"):
        return

    errors = []
    secret = str(app.config.get("SECRET_KEY") or "").strip()
    if secret.lower() in _PLACEHOLDERS or len(secret) < 32:
        errors.append("SECRET_KEY must be a unique random value of at least 32 characters.")

    if str(os.getenv("FLASK_DEBUG", "false")).strip().lower() in {"1", "true", "yes", "on"}:
        errors.append("FLASK_DEBUG must be false in production.")

    audit_hash_key = str(app.config.get("AUDIT_HASH_KEY") or "").strip()
    if len(audit_hash_key) < 32:
        errors.append("AUDIT_HASH_KEY must be a unique random value of at least 32 characters.")

    for unsafe_log_flag in (
        "ENABLE_SQL_LOGGING",
        "ENABLE_REQUEST_PAYLOAD_LOGGING",
        "ENABLE_REQUEST_ARGS_LOGGING",
        "WHATSAPP_DEBUG_LOGGING",
    ):
        if str(os.getenv(unsafe_log_flag, "false")).strip().lower() in {"1", "true", "yes", "on"}:
            errors.append(f"{unsafe_log_flag} must be false in production to prevent sensitive-data leakage.")

    if app.config.get("ALLOW_LEGACY_PLAINTEXT_PASSWORDS"):
        errors.append("ALLOW_LEGACY_PLAINTEXT_PASSWORDS must be false in production.")

    if not app.config.get("WTF_CSRF_ENABLED"):
        errors.append("WTF_CSRF_ENABLED must be true in production.")

    if not app.config.get("SESSION_COOKIE_SECURE"):
        errors.append("SESSION_COOKIE_SECURE must be true in production.")

    if not app.config.get("TRUST_PROXY_HEADERS"):
        errors.append("TRUST_PROXY_HEADERS must be true behind the production Nginx proxy.")

    trusted_hosts = app.config.get("TRUSTED_HOSTS") or []
    if not trusted_hosts:
        errors.append("TRUSTED_HOSTS must explicitly list the production hostname(s).")

    public_base = str(app.config.get("TERIQEN_PUBLIC_BASE_URL") or "").strip()
    if not public_base.startswith("https://"):
        errors.append("TERIQEN_PUBLIC_BASE_URL must be a fixed HTTPS URL in production.")

    if not app.config.get("MFA_ENABLED"):
        errors.append("MFA_ENABLED must be true in production.")
    if str(app.config.get("MFA_MODE") or "").upper() not in {"ALL", "PRIVILEGED"}:
        errors.append("MFA_MODE must be ALL or PRIVILEGED in production.")

    if not app.config.get("MALWARE_SCAN_ENABLED"):
        errors.append("MALWARE_SCAN_ENABLED must be true in production.")
    if not app.config.get("MALWARE_SCAN_FAIL_CLOSED"):
        errors.append("MALWARE_SCAN_FAIL_CLOSED must be true in production.")

    if not str(app.config.get("DB_PASSWORD") or "").strip():
        errors.append("DB_PASSWORD is required in production.")

    if str(app.config.get("DB_USER") or "").strip().lower() == "root":
        errors.append("Use a dedicated non-root MySQL user in production.")

    if app.config.get("WHATSAPP_ENABLED"):
        if not str(app.config.get("WHATSAPP_ACCESS_TOKEN") or "").strip():
            errors.append("WHATSAPP_ACCESS_TOKEN is required when WHATSAPP_ENABLED=true.")
        verify_token = str(app.config.get("WHATSAPP_VERIFY_TOKEN") or "").strip()
        if len(verify_token) < 16 or verify_token.lower() in {"healtheasy123", "changeme"}:
            errors.append("WHATSAPP_VERIFY_TOKEN must be a strong unique value.")
        if app.config.get("WHATSAPP_REQUIRE_SIGNATURE") and not str(app.config.get("META_APP_SECRET") or "").strip():
            errors.append("META_APP_SECRET is required when WhatsApp signature verification is enabled.")

    if errors:
        raise RuntimeError("Production configuration error(s): " + " | ".join(errors))


def configure_proxy_headers(app):
    if app.config.get("TRUST_PROXY_HEADERS"):
        app.wsgi_app = ProxyFix(
            app.wsgi_app,
            x_for=1,
            x_proto=1,
            x_host=1,
            x_port=1,
        )


def make_auth_version(password_hash: str, secret_key: str) -> str:
    return hmac.new(str(secret_key).encode("utf-8"), str(password_hash or "").encode("utf-8"), hashlib.sha256).hexdigest()


def register_security_hooks(app):
    @app.before_request
    def enforce_session_security_version():
        if not current_user.is_authenticated or request.path.startswith("/static/"):
            return None
        now = int(time.time())
        last_check = int(session.get("auth_version_checked_at") or 0)
        if now - last_check < int(app.config.get("AUTH_VERSION_CHECK_SECONDS") or 60):
            return None
        user_code = current_user.get_id()
        from app.common.db import get_db_connection
        conn = cursor = None
        try:
            conn = get_db_connection(); cursor = conn.cursor(dictionary=True)
            cursor.execute("SELECT user_password, user_status FROM user_master WHERE user_code=%s LIMIT 1", (user_code,))
            row = cursor.fetchone() or {}
            expected = make_auth_version(row.get("user_password") or "", app.config.get("SECRET_KEY") or "")
            if str(row.get("user_status") or "").upper() != "ACTIVE" or not hmac.compare_digest(expected, str(session.get("auth_version") or "")):
                logout_user(); session.clear()
                if request.path.startswith("/api/"):
                    return jsonify({"success": False, "message": "Session expired. Please sign in again."}), 401
                return redirect(url_for("login"))
            session["auth_version_checked_at"] = now
        except Exception:
            current_app.logger.exception("Session security-version validation failed")
            if app.config.get("IS_PRODUCTION"):
                logout_user(); session.clear()
                return redirect(url_for("login"))
        finally:
            if cursor: cursor.close()
            if conn: conn.close()
        return None

    @app.before_request
    def protect_runtime_uploads():
        if not request.path.startswith("/static/uploads/"):
            return None

        if not current_user.is_authenticated:
            abort(401)

        if request.path.startswith("/static/uploads/whatsapp_docs/"):
            from app.common.access_control import has_screen_permission

            if not (
                has_screen_permission("OPERATION_QUEUE", "view")
                or has_screen_permission("MANUAL_REVIEW", "view")
            ):
                abort(403)

        return None

    @app.after_request
    def add_security_headers(response):
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
        response.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        response.headers.setdefault(
            "Permissions-Policy",
            "camera=(), microphone=(), geolocation=(), payment=(), usb=(), interest-cohort=()",
        )
        csp = str(app.config.get("CONTENT_SECURITY_POLICY") or "").strip()
        if csp:
            response.headers.setdefault("Content-Security-Policy", csp)
        response.headers.setdefault("Cross-Origin-Opener-Policy", "same-origin")
        response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")

        if app.config.get("IS_PRODUCTION"):
            response.headers.setdefault(
                "Strict-Transport-Security",
                "max-age=31536000; includeSubDomains",
            )

        # Dynamic TERIQEN pages/API responses can contain authentication state,
        # MFA state, patient data or other confidential information.  Prevent
        # browser/proxy caching so Back/Forward navigation cannot revive a
        # usable sensitive page after logout or timeout.  Deliberately preserve
        # caching for versioned static assets and the public broadcast-media
        # endpoint, which sets its own cache policy.
        static_asset = request.path.startswith("/static/") and not request.path.startswith("/static/uploads/")
        public_broadcast_media = request.path.startswith("/broadcast-management/public-media/")

        if not static_asset and not public_broadcast_media:
            response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private, max-age=0"
            response.headers["Pragma"] = "no-cache"
            response.headers["Expires"] = "0"
            response.vary.add("Cookie")

        return response


def register_health_routes(app):
    @app.get("/healthz")
    def healthz():
        return jsonify({"status": "ok"}), 200

    @app.get("/readyz")
    def readyz():
        from app.common.db import get_db_connection

        conn = None
        cursor = None
        try:
            conn = get_db_connection()
            if not conn:
                return jsonify({"status": "not_ready"}), 503
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
            return jsonify({"status": "ready"}), 200
        except Exception:
            current_app.logger.exception("Readiness database check failed")
            return jsonify({"status": "not_ready"}), 503
        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()
