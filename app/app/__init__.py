import os
import logging
from datetime import datetime

from flask import Flask, g, got_request_exception
from flask_login import current_user

from app.config import Config
from app.extensions import login_manager, csrf
from app.common.decorators import register_error_handlers
from app.common.lov_routes import common_bp
from app.common.db import get_db_connection
from app.common.access_control import register_rbac_guard
from app.common.security import (
    configure_proxy_headers,
    register_health_routes,
    register_security_hooks,
    validate_production_config,
)
from app.common.logger import (
    assign_request_id,
    log_exception,
    log_request,
    log_response,
    setup_logging,
)

from app.auth import init_app as auth_init_app
from app.dashboard import init_app as dashboard_init_app
from app.patient import init_app as patient_init_app
from app.lead import init_app as lead_init_app
from app.workflow import init_app as workflow_init_app
from app.partner import init_app as partner_init_app
from app.finance import init_app as finance_init_app
from app.queue_management import init_app as queue_management_init_app
from app.queue_management.scheduler import start_queue_parser_scheduler
from app.system_control import init_app as system_control_init_app
from app.reports import init_app as reports_init_app
from app.whatsapp import init_app as whatsapp_init_app
from app.alert_management import init_app as alert_management_init_app
from app.broadcast_management import init_app as broadcast_management_init_app


# ============================================================
# SYSTEM MONITORING
# ============================================================

from app.system_monitoring import init_app as system_monitoring_init_app


logger = logging.getLogger("app")


# ============================================================
# SCREEN ACCESS CONTEXT
# ============================================================

def register_screen_access_context(app):

    @app.context_processor
    def inject_screen_access():

        def _get_current_user_code():
            if not current_user or not current_user.is_authenticated:
                return None

            user_code = getattr(current_user, "user_code", None)

            if not user_code:
                try:
                    user_code = current_user.get_id()
                except Exception:
                    user_code = None

            return user_code

        def _load_user_screen_access():
            user_code = _get_current_user_code()

            if not user_code:
                return set()

            cache_key = f"_screen_access_{user_code}"

            if hasattr(g, cache_key):
                return getattr(g, cache_key)

            allowed_screens = set()
            conn = None
            cursor = None

            try:
                conn = get_db_connection()

                if not conn:
                    setattr(g, cache_key, allowed_screens)
                    return allowed_screens

                cursor = conn.cursor(dictionary=True)

                cursor.execute(
                    """
                    SELECT user_type, user_status
                    FROM user_master
                    WHERE user_code = %s
                    LIMIT 1
                    """,
                    (user_code,)
                )

                user = cursor.fetchone() or {}

                user_status = str(
                    user.get("user_status") or ""
                ).strip().upper()

                user_type = str(
                    user.get("user_type") or ""
                ).strip().upper()

                if user_status != "ACTIVE":
                    allowed_screens = set()

                elif user_type == "ADMIN":
                    cursor.execute(
                        """
                        SELECT screen_code
                        FROM screen_master
                        WHERE COALESCE(is_active, 'Y') = 'Y'
                        """
                    )

                    rows = cursor.fetchall() or []

                    allowed_screens = {
                        row.get("screen_code")
                        for row in rows
                        if row.get("screen_code")
                    }

                else:
                    cursor.execute(
                        """
                        SELECT usa.screen_code
                        FROM user_screen_access usa
                        INNER JOIN screen_master sm
                            ON sm.screen_code = usa.screen_code
                           AND COALESCE(sm.is_active, 'Y') = 'Y'
                        WHERE usa.user_code = %s
                          AND usa.can_view = 'Y'
                          AND COALESCE(usa.is_active, 'Y') = 'Y'
                        """,
                        (user_code,)
                    )

                    rows = cursor.fetchall() or []

                    allowed_screens = {
                        row.get("screen_code")
                        for row in rows
                        if row.get("screen_code")
                    }

            except Exception as e:
                logger.exception(
                    "Screen access load failed: %s",
                    e
                )
                allowed_screens = set()

            finally:
                if cursor:
                    cursor.close()

                if conn:
                    conn.close()

            setattr(
                g,
                cache_key,
                allowed_screens
            )

            return allowed_screens

        def can_view_screen(screen_code):
            if not screen_code:
                return False

            allowed_screens = _load_user_screen_access()

            return screen_code in allowed_screens

        def can_view_any_screen(screen_codes):
            if not screen_codes:
                return False

            allowed_screens = _load_user_screen_access()

            for code in screen_codes:
                if code in allowed_screens:
                    return True

            return False

        return dict(
            can_view_screen=can_view_screen,
            can_view_any_screen=can_view_any_screen
        )


# ============================================================
# BRAND CONTEXT
# ============================================================

def register_brand_context(app):
    """Expose one authoritative TERIQEN/Cadupius brand lockup to all templates."""

    @app.context_processor
    def inject_brand_context():
        return {
            "brand_product_name": app.config.get("BRAND_PRODUCT_NAME", "TERIQEN"),
            "brand_company_name": app.config.get(
                "BRAND_COMPANY_NAME", "Cadupius Healthassist Pvt. Ltd."
            ),
            "brand_tagline": app.config.get(
                "BRAND_TAGLINE", "Healthcare Technology Platform"
            ),
            "brand_logo_filename": app.config.get(
                "BRAND_LOGO_FILENAME", "images/cadupius_healthassist_logo.png"
            ),
            "current_year": datetime.now().year,
        }


# ============================================================
# APPLICATION FACTORY
# ============================================================

def create_app():

    setup_logging()

    app = Flask(__name__)
    app.config.from_object(Config)

    configure_proxy_headers(app)
    validate_production_config(app)

    logger.info(
        "SECRET_KEY loaded: %s",
        bool(app.config["SECRET_KEY"])
    )

    # ========================================================
    # EXTENSIONS
    # ========================================================

    login_manager.init_app(app)
    csrf.init_app(app)

    # ========================================================
    # COMMON APPLICATION HOOKS
    # ========================================================

    register_brand_context(app)
    register_logging_hooks(app)
    register_error_handlers(app)
    register_security_hooks(app)
    register_rbac_guard(app)
    register_health_routes(app)

    # ========================================================
    # COMMON BLUEPRINT
    # ========================================================

    app.register_blueprint(common_bp)

    # ========================================================
    # SCREEN ACCESS CONTEXT
    # ========================================================

    register_screen_access_context(app)

    # ========================================================
    # APPLICATION MODULES
    # ========================================================

    auth_init_app(app)
    dashboard_init_app(app)
    patient_init_app(app)
    lead_init_app(app)
    workflow_init_app(app)
    partner_init_app(app)
    finance_init_app(app)

    queue_management_init_app(app)

    system_control_init_app(app)

    reports_init_app(app)
    whatsapp_init_app(app)

    alert_management_init_app(app)

    # ========================================================
    # BROADCAST MANAGEMENT
    # ========================================================

    broadcast_management_init_app(app)

    # ========================================================
    # SYSTEM MONITORING
    # ========================================================

    system_monitoring_init_app(app)

    # ========================================================
    # BACKGROUND SCHEDULER
    # ========================================================

    if app.config.get("ENABLE_BACKGROUND_SCHEDULER"):
        logger.info(
            "Starting queue parser scheduler in dedicated scheduler process"
        )
        start_queue_parser_scheduler(app)
    else:
        logger.info(
            "Background scheduler disabled for this process"
        )

    return app


# ============================================================
# LOGGING HOOKS
# ============================================================

def register_logging_hooks(app):

    app.before_request(
        assign_request_id
    )

    app.before_request(
        log_request
    )

    app.after_request(
        log_response
    )

    got_request_exception.connect(
        log_exception,
        app
    )