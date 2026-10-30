import logging
import os
import re
import time
import uuid
from logging.handlers import RotatingFileHandler

from flask import g, has_request_context, request
from flask_login import current_user


LOG_FORMAT = (
    "%(asctime)s | %(levelname)s | %(name)s | "
    "request_id=%(request_id)s | user=%(user)s | %(message)s"
)
MAX_LOG_BYTES = 10 * 1024 * 1024
BACKUP_COUNT = 10
SENSITIVE_KEYS = {
    "password",
    "passwd",
    "pwd",
    "secret",
    "token",
    "access_token",
    "refresh_token",
    "authorization",
    "cookie",
    "set-cookie",
    "api_key",
    "passport",
    "patient",
    "mobile",
    "contact",
    "contno",
    "email",
    "address",
    "dob",
    "date_of_birth",
    "document_data",
    "attachment_json",
}

LOG_FILES = {
    "app": ("app.log", logging.INFO),
    "error": ("error.log", logging.ERROR),
    "finance": ("finance.log", logging.INFO),
    "request_response": ("request_response.log", logging.INFO),
    "sql": ("sql_query.log", logging.INFO),
    "exception": ("exception_trace.log", logging.ERROR),
    "scheduler": ("background_scheduler.log", logging.INFO),
    "whatsapp_parser": ("whatsapp_parser.log", logging.INFO),
    "sla_engine": ("sla_engine.log", logging.INFO),
    "user_activity": ("user_activity.log", logging.INFO),
}

MODULE_LOGGERS = {
    "auth",
    "dashboard",
    "patient",
    "lead",
    "workflow",
    "partner",
    "finance",
    "queue_management",
    "system_control",
    "reports",
    "file_upload",
    "common",
}
_LOGGING_CONFIGURED = False
_LOGGERS = {}


def env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


def is_sql_logging_enabled():
    return env_bool("ENABLE_SQL_LOGGING", False)


def is_request_logging_enabled():
    return env_bool("ENABLE_REQUEST_LOGGING", True)


def is_request_payload_logging_enabled():
    return env_bool("ENABLE_REQUEST_PAYLOAD_LOGGING", False)


def is_module_request_logging_enabled():
    return env_bool("ENABLE_MODULE_REQUEST_LOGGING", False)


def is_user_activity_logging_enabled():
    return env_bool("ENABLE_USER_ACTIVITY_LOGGING", True)


class RequestContextFilter(logging.Filter):
    def filter(self, record):
        record.request_id = get_request_id()
        record.user = get_current_user_code()
        return True


def get_request_id():
    if has_request_context():
        request_id = getattr(g, "request_id", None)
        if request_id:
            return request_id
    return "-"


def get_current_user_code():
    if not has_request_context():
        return "-"

    try:
        if current_user and current_user.is_authenticated:
            return (
                getattr(current_user, "user_code", None)
                or current_user.get_id()
                or "authenticated"
            )
    except Exception:
        return "unknown"

    return "anonymous"


def _build_file_logger(name, level, filename):
    logger = logging.getLogger(name)
    logger.setLevel(level)
    logger.propagate = False

    log_dir = os.getenv("LOG_DIR", "logs").strip() or "logs"
    log_path = os.path.join(log_dir, filename)
    if not any(
        isinstance(handler, RotatingFileHandler)
        and getattr(handler, "baseFilename", None) == os.path.abspath(log_path)
        for handler in logger.handlers
    ):
        handler = RotatingFileHandler(
            log_path,
            maxBytes=MAX_LOG_BYTES,
            backupCount=BACKUP_COUNT,
            encoding="utf-8",
        )
        handler.setFormatter(logging.Formatter(LOG_FORMAT))
        handler.addFilter(RequestContextFilter())
        logger.addHandler(handler)

    return logger


def setup_logging():
    global _LOGGING_CONFIGURED, _LOGGERS

    if _LOGGING_CONFIGURED:
        return _LOGGERS

    log_dir = os.getenv("LOG_DIR", "logs").strip() or "logs"

    if not os.path.exists(log_dir):
        os.makedirs(log_dir)

    _LOGGERS = {
        name: _build_file_logger(name, level, filename)
        for name, (filename, level) in LOG_FILES.items()
    }
    for module_name in MODULE_LOGGERS:
        logger_name = f"module.{module_name}"
        _LOGGERS[logger_name] = _build_file_logger(
            logger_name,
            logging.INFO,
            f"module_{module_name}.log",
        )

    flask_logger = logging.getLogger("flask.app")
    flask_logger.setLevel(logging.INFO)
    for handler in _LOGGERS["app"].handlers:
        if handler not in flask_logger.handlers:
            flask_logger.addHandler(handler)

    _LOGGING_CONFIGURED = True
    return _LOGGERS


def get_logger(name="app"):
    if not _LOGGING_CONFIGURED:
        setup_logging()
    return logging.getLogger(name)


def get_module_logger(module_name):
    if not _LOGGING_CONFIGURED:
        setup_logging()
    safe_name = re.sub(r"[^a-zA-Z0-9_.-]+", "_", str(module_name or "general")).strip("_")
    safe_name = safe_name.split(".")[0] or "general"
    return _build_file_logger(f"module.{safe_name}", logging.INFO, f"module_{safe_name}.log")


def sanitize_payload(value, max_length=1000):
    sanitized = _sanitize_value(value)
    text = repr(sanitized)
    if len(text) > max_length:
        return text[:max_length] + "...<truncated>"
    return text


def _sanitize_value(value):
    if isinstance(value, dict):
        return {
            key: "***REDACTED***" if _is_sensitive_key(key) else _sanitize_value(val)
            for key, val in value.items()
        }

    if isinstance(value, (list, tuple)):
        sanitized_items = [_sanitize_value(item) for item in value[:25]]
        if len(value) > 25:
            sanitized_items.append("...<truncated>")
        return type(value)(sanitized_items) if isinstance(value, tuple) else sanitized_items

    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"

    return value


def _is_sensitive_key(key):
    key_text = str(key).lower()
    return any(sensitive in key_text for sensitive in SENSITIVE_KEYS)


def normalize_sql(sql):
    return re.sub(r"\s+", " ", str(sql or "")).strip()


def assign_request_id():
    inbound_id = request.headers.get("X-Request-ID") if has_request_context() else None
    g.request_id = inbound_id or uuid.uuid4().hex
    g.request_started_at = time.perf_counter()


def safe_request_path():
    """Never persist bearer-style URL tokens in application logs."""
    if not has_request_context():
        return "-"
    path = request.path or "/"
    path = re.sub(r"^/reset-password/[^/]+", "/reset-password/<redacted>", path)
    path = re.sub(r"^/whatsapp/privacy/[^/]+", "/whatsapp/privacy/<redacted>", path)
    return path


def log_request():
    if not is_request_logging_enabled() and not is_user_activity_logging_enabled():
        return

    body = None
    args = "-"

    if is_request_payload_logging_enabled() and request.method in {"POST", "PUT", "PATCH", "DELETE"}:
        if request.is_json:
            body = request.get_json(silent=True)
        else:
            body = request.form.to_dict(flat=False) if request.form else None

    if env_bool("ENABLE_REQUEST_ARGS_LOGGING", False):
        args = sanitize_payload(request.args.to_dict(flat=False))

    if is_request_logging_enabled():
        get_logger("request_response").info(
            "REQUEST method=%s path=%s endpoint=%s remote_addr=%s args=%s body=%s",
            request.method,
            safe_request_path(),
            request.endpoint,
            request.headers.get("X-Forwarded-For", request.remote_addr),
            args,
            sanitize_payload(body) if body is not None else "-",
        )

    if is_user_activity_logging_enabled():
        get_logger("user_activity").info(
            "USER_ACTIVITY method=%s path=%s endpoint=%s",
            request.method,
            safe_request_path(),
            request.endpoint,
        )

    if is_module_request_logging_enabled() and request.blueprint:
        get_module_logger(request.blueprint).info(
            "MODULE_REQUEST method=%s path=%s endpoint=%s",
            request.method,
            safe_request_path(),
            request.endpoint,
        )


def log_response(response):
    if not is_request_logging_enabled():
        response.headers["X-Request-ID"] = get_request_id()
        return response

    elapsed_ms = None
    started_at = getattr(g, "request_started_at", None)
    if started_at is not None:
        elapsed_ms = round((time.perf_counter() - started_at) * 1000, 2)

    get_logger("request_response").info(
        "RESPONSE method=%s path=%s status=%s elapsed_ms=%s content_length=%s",
        request.method,
        safe_request_path(),
        response.status_code,
        elapsed_ms,
        response.calculate_content_length(),
    )
    response.headers["X-Request-ID"] = get_request_id()
    return response


def log_exception(sender, exception, **extra):
    get_logger("exception").exception(
        "UNHANDLED_EXCEPTION path=%s endpoint=%s exception=%s",
        safe_request_path(),
        request.endpoint if has_request_context() else "-",
        exception,
    )
