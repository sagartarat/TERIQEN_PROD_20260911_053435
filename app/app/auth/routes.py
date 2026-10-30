import logging

from flask import render_template, redirect, url_for, flash, request, session, current_app
from urllib.parse import quote
from flask_login import login_user, logout_user, login_required
from werkzeug.security import check_password_hash, generate_password_hash

from app.common.db import get_db
from app.common.security import make_auth_version
from .forms import LoginForm
from .models import User

from app.auth.password_reset_service import (
    get_user_by_email_or_code,
    create_password_reset_token,
    validate_reset_token,
    reset_user_password,
    validate_password_rules
)
from app.auth.mail_service import send_password_reset_email, send_login_otp_email
from app.auth.mfa_service import is_mfa_required, begin_mfa, verify_mfa
from app.auth.login_throttle import is_blocked, register_failure, clear_failures

from app.system_control.user_management.user_screen_access_service import (
    get_user_screen_permissions,
)


logger = logging.getLogger("app")


def _finish_login(user):
    login_user(User(user["user_code"], user.get("user_name"), user.get("user_type"), user.get("user_status")))
    allowed_screens, screen_permissions = get_user_screen_permissions(user_code=user.get("user_code"), user_type=user.get("user_type"))
    session["allowed_screens"] = allowed_screens
    session["screen_permissions"] = screen_permissions
    session["user_code"] = user.get("user_code")
    session["user_name"] = user.get("user_name")
    session["user_type"] = user.get("user_type")
    session["auth_version"] = make_auth_version(user.get("user_password") or "", current_app.config.get("SECRET_KEY") or "")
    session["auth_version_checked_at"] = 0
    session.permanent = True


def register_auth_routes(app):
    @app.route("/", methods=["GET", "POST"])
    def login():
        form = LoginForm()

        if form.validate_on_submit():
            username = (form.username.data or "").strip()
            password = form.password.data or ""
            client_ip = request.remote_addr or ""

            if is_blocked(username, client_ip):
                flash("Too many failed sign-in attempts. Please try again later.", "error")
                return render_template("login.html", form=form), 429

            try:
                conn = get_db()
                cursor = conn.cursor(dictionary=True)
                cursor.execute("SELECT * FROM user_master WHERE user_code = %s", (username,))
                user = cursor.fetchone()
                cursor.close()
                conn.close()

                if not user or str(user.get("user_status") or "").strip().upper() != "ACTIVE":
                    register_failure(username, client_ip)
                    flash("Invalid Username or Password", "error")
                    return render_template("login.html", form=form)

                db_pass = user.get("user_password") or ""

                ok = False
                try:
                    ok = check_password_hash(db_pass, password)
                except Exception as e:
                    logger.exception("check_password_hash error: %s", e)

                legacy_plaintext = False
                allow_legacy_plaintext = bool(app.config.get("ALLOW_LEGACY_PLAINTEXT_PASSWORDS"))
                if allow_legacy_plaintext and not ok and db_pass == password:
                    ok = True
                    legacy_plaintext = True

                if ok:
                    if legacy_plaintext:
                        upgrade_conn = None
                        upgrade_cursor = None
                        try:
                            upgrade_conn = get_db()
                            upgrade_cursor = upgrade_conn.cursor()
                            upgrade_cursor.execute(
                                "UPDATE user_master SET user_password = %s WHERE user_code = %s",
                                (generate_password_hash(password), user.get("user_code")),
                            )
                            upgrade_conn.commit()
                            logger.info("Legacy plaintext password upgraded for user=%s", user.get("user_code"))
                        except Exception:
                            if upgrade_conn:
                                upgrade_conn.rollback()
                            logger.exception("Unable to upgrade legacy password hash")
                        finally:
                            if upgrade_cursor:
                                upgrade_cursor.close()
                            if upgrade_conn:
                                upgrade_conn.close()

                    clear_failures(username, client_ip)
                    if is_mfa_required(user):
                        if not user.get("user_email"):
                            logger.error("MFA blocked user without email user=%s", user.get("user_code"))
                            flash("Sign-in verification is required. Please contact the administrator to configure your registered email.", "error")
                            return render_template("login.html", form=form), 403
                        otp = begin_mfa(user)
                        send_login_otp_email(user.get("user_email"), user.get("user_name"), otp)
                        return redirect(url_for("mfa_verify"))

                    _finish_login(user)
                    return redirect(url_for("dashboard"))

                register_failure(username, client_ip)
                flash("Invalid Username or Password", "error")
                return render_template("login.html", form=form)

            except Exception as e:
                logger.exception("Login exception: %s", e)
                flash("Login error occurred. Check server logs.", "error")

        return render_template("login.html", form=form)

    @app.route("/mfa", methods=["GET", "POST"])
    def mfa_verify():
        if not session.get("pending_mfa"):
            return redirect(url_for("login"))
        if request.method == "POST":
            ok, result = verify_mfa(request.form.get("otp_code", ""))
            if not ok:
                flash("Invalid or expired verification code.", "error")
                if not session.get("pending_mfa"):
                    return redirect(url_for("login"))
                return render_template("mfa_verify.html"), 400
            conn = get_db(); cursor = conn.cursor(dictionary=True)
            try:
                cursor.execute("SELECT * FROM user_master WHERE user_code=%s AND user_status='ACTIVE' LIMIT 1", (result,))
                user = cursor.fetchone()
            finally:
                cursor.close(); conn.close()
            if not user:
                session.clear(); return redirect(url_for("login"))
            _finish_login(user)
            return redirect(url_for("dashboard"))
        return render_template("mfa_verify.html")

    @app.route("/forgot-password", methods=["GET", "POST"])
    def forgot_password():
        if request.method == "POST":
            identifier = request.form.get("user_identifier", "").strip()

            if not identifier:
                flash("Please enter your User Code or registered Email.", "warning")
                return render_template("forgot_password.html")

            try:
                user = get_user_by_email_or_code(identifier)

                if user and user.get("user_email"):
                    token = create_password_reset_token(
                        user_code=user["user_code"],
                        user_email=user["user_email"],
                        ip_address=request.remote_addr,
                        user_agent=request.headers.get("User-Agent", "")[:255]
                    )

                    public_base = str(app.config.get("TERIQEN_PUBLIC_BASE_URL") or "").rstrip("/")
                    if not public_base:
                        raise RuntimeError("TERIQEN_PUBLIC_BASE_URL is not configured.")
                    reset_link = f"{public_base}/reset-password/{quote(token, safe='')}"

                    send_password_reset_email(
                        to_email=user["user_email"],
                        user_name=user.get("user_name"),
                        reset_link=reset_link
                    )

                flash(
                    "If the account exists, a password reset link has been sent to the registered email.",
                    "success"
                )
                return redirect(url_for("forgot_password"))

            except Exception as e:
                logger.exception("Forgot password exception: %s", e)
                flash("Unable to process password reset request. Check server logs.", "error")
                return render_template("forgot_password.html")

        return render_template("forgot_password.html")

    @app.route("/reset-password/<token>", methods=["GET", "POST"])
    def reset_password(token):
        try:
            token_row = validate_reset_token(token)

            if not token_row:
                flash("Invalid or expired reset link.", "danger")
                return redirect(url_for("forgot_password"))

            if request.method == "POST":
                new_password = request.form.get("new_password", "")
                confirm_password = request.form.get("confirm_password", "")

                if not new_password or not confirm_password:
                    flash("Please enter and confirm your new password.", "warning")
                    return render_template("reset_password.html", token=token)

                if new_password != confirm_password:
                    flash("New password and confirm password do not match.", "danger")
                    return render_template("reset_password.html", token=token)

                password_errors = validate_password_rules(new_password)

                if password_errors:
                    for error in password_errors:
                        flash(error, "danger")
                    return render_template("reset_password.html", token=token)

                success, message = reset_user_password(token, new_password)

                if success:
                    flash(message, "success")
                    return redirect(url_for("login", password_reset="success"))

                flash(message, "danger")

            return render_template("reset_password.html", token=token)

        except Exception as e:
            logger.exception("Reset password exception: %s", e)
            flash("Unable to reset password. Check server logs.", "error")
            return redirect(url_for("forgot_password"))

    @app.route("/session/heartbeat", methods=["POST"])
    @login_required
    def session_heartbeat():
        # A heartbeat is sent only while the browser has recent user activity.
        # Touching the permanent session refreshes its rolling expiry without
        # exposing any application or patient data.
        session.modified = True
        return ("", 204)

    @app.route("/logout")
    @login_required
    def logout():
        # Explicit logout is immediate.  Do not leave MFA, RBAC, flash or other
        # application state behind in the signed Flask session cookie.
        logout_user()
        session.clear()
        return redirect(url_for("login"))
