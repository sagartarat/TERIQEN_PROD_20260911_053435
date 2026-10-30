import os
from datetime import timedelta
from dotenv import load_dotenv

load_dotenv()


def _env_bool(name, default=False):
    value = os.getenv(name)
    if value is None:
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "y", "on"}


class Config:
    APP_ENV = os.getenv("APP_ENV", "development").strip().lower()
    IS_PRODUCTION = APP_ENV == "production"

    # Centralized application branding. These defaults are production-safe and
    # can be overridden without code changes when TERIQEN is white-labelled.
    BRAND_PRODUCT_NAME = os.getenv("BRAND_PRODUCT_NAME", "TERIQEN").strip() or "TERIQEN"
    BRAND_COMPANY_NAME = (
        os.getenv("BRAND_COMPANY_NAME", "Cadupius Healthassist Pvt. Ltd.").strip()
        or "Cadupius Healthassist Pvt. Ltd."
    )
    BRAND_TAGLINE = (
        os.getenv("BRAND_TAGLINE", "Healthcare Technology Platform").strip()
        or "Healthcare Technology Platform"
    )
    BRAND_LOGO_FILENAME = (
        os.getenv("BRAND_LOGO_FILENAME", "images/cadupius_healthassist_logo.png").strip()
        or "images/cadupius_healthassist_logo.png"
    )
    TERIQEN_PUBLIC_BASE_URL = os.getenv("TERIQEN_PUBLIC_BASE_URL", "").strip().rstrip("/")
    TRUSTED_HOSTS = [
        host.strip().lower()
        for host in os.getenv("TRUSTED_HOSTS", "").split(",")
        if host.strip()
    ] or None
    CONTENT_SECURITY_POLICY = os.getenv(
        "CONTENT_SECURITY_POLICY",
        "default-src 'self' https: data: blob:; script-src 'self' 'unsafe-inline' https:; style-src 'self' 'unsafe-inline' https:; object-src 'none'; base-uri 'self'; "
        "frame-ancestors 'self'; form-action 'self'; img-src 'self' https: data: blob:; "
        "connect-src 'self' https:; upgrade-insecure-requests",
    ).strip()

    SECRET_KEY = os.getenv("SECRET_KEY", "").strip() or (
        "development-only-change-me" if not IS_PRODUCTION else ""
    )
    AUDIT_HASH_KEY = os.getenv("AUDIT_HASH_KEY", "").strip() or (
        "development-audit-hash-key" if not IS_PRODUCTION else ""
    )

    MAX_TOTAL_UPLOAD_BYTES = int(os.getenv("MAX_TOTAL_UPLOAD_BYTES", 10 * 1024 * 1024))
    PATIENT_CREATE_UPLOAD_BYTES = int(os.getenv("PATIENT_CREATE_UPLOAD_BYTES", 2 * 1024 * 1024))
    LEAD_DOC_UPLOAD_BYTES = int(os.getenv("LEAD_DOC_UPLOAD_BYTES", 10 * 1024 * 1024))
    MAX_CONTENT_LENGTH = MAX_TOTAL_UPLOAD_BYTES

    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SECURE = _env_bool("SESSION_COOKIE_SECURE", IS_PRODUCTION)
    SESSION_COOKIE_SAMESITE = os.getenv("SESSION_COOKIE_SAMESITE", "Lax")
    REMEMBER_COOKIE_HTTPONLY = True
    REMEMBER_COOKIE_SECURE = SESSION_COOKIE_SECURE
    REMEMBER_COOKIE_SAMESITE = SESSION_COOKIE_SAMESITE
    # Security: authenticated TERIQEN sessions use a short rolling inactivity
    # lifetime. Active browser sessions send a lightweight heartbeat only while
    # the user is interacting with the application.
    SESSION_LIFETIME_MINUTES = max(1, int(os.getenv("SESSION_LIFETIME_MINUTES", "5")))
    PERMANENT_SESSION_LIFETIME = timedelta(minutes=SESSION_LIFETIME_MINUTES)
    SESSION_REFRESH_EACH_REQUEST = True
    PREFERRED_URL_SCHEME = "https" if IS_PRODUCTION else "http"

    WTF_CSRF_ENABLED = _env_bool("WTF_CSRF_ENABLED", True)
    WTF_CSRF_TIME_LIMIT = int(
    os.getenv("WTF_CSRF_TIME_LIMIT_SECONDS", "7200")
)

    TRUST_PROXY_HEADERS = _env_bool("TRUST_PROXY_HEADERS", IS_PRODUCTION)
    ENABLE_BACKGROUND_SCHEDULER = _env_bool("ENABLE_BACKGROUND_SCHEDULER", False)

    # Production authentication hardening. Email OTP is deliberately mandatory
    # by default in production; set MFA_MODE=PRIVILEGED only after defining
    # MFA_PRIVILEGED_USER_TYPES for your deployment.
    MFA_ENABLED = _env_bool("MFA_ENABLED", IS_PRODUCTION)
    MFA_MODE = os.getenv("MFA_MODE", "ALL" if IS_PRODUCTION else "OFF").strip().upper()
    MFA_OTP_TTL_SECONDS = int(os.getenv("MFA_OTP_TTL_SECONDS", "300"))
    MFA_MAX_ATTEMPTS = int(os.getenv("MFA_MAX_ATTEMPTS", "5"))
    AUTH_VERSION_CHECK_SECONDS = int(os.getenv("AUTH_VERSION_CHECK_SECONDS", "60"))
    MFA_PRIVILEGED_USER_TYPES = {
        item.strip().upper()
        for item in os.getenv(
            "MFA_PRIVILEGED_USER_TYPES",
            "ADMIN,ADMINISTRATOR,SUPERADMIN,SYSTEM_ADMIN",
        ).split(",")
        if item.strip()
    }

    MALWARE_SCAN_ENABLED = _env_bool("MALWARE_SCAN_ENABLED", IS_PRODUCTION)
    MALWARE_SCAN_FAIL_CLOSED = _env_bool("MALWARE_SCAN_FAIL_CLOSED", IS_PRODUCTION)
    CLAMAV_COMMAND = os.getenv("CLAMAV_COMMAND", "clamdscan").strip() or "clamdscan"

    ALLOW_LEGACY_PLAINTEXT_PASSWORDS = _env_bool(
        "ALLOW_LEGACY_PLAINTEXT_PASSWORDS", False
    )

    DB_HOST = os.getenv("DB_HOST", "127.0.0.1")
    DB_USER = os.getenv("DB_USER", "root")
    DB_PASSWORD = os.getenv("DB_PASSWORD", "")
    DB_NAME = os.getenv("DB_NAME", "cadupius_ops")
    DB_PORT = int(os.getenv("DB_PORT", 3306))
    DB_CONNECT_TIMEOUT = int(os.getenv("DB_CONNECT_TIMEOUT", "10"))

    MAIL_SERVER = os.getenv("MAIL_SERVER", "smtp.office365.com")
    MAIL_PORT = int(os.getenv("MAIL_PORT", 587))
    MAIL_USE_TLS = _env_bool("MAIL_USE_TLS", True)
    MAIL_USE_SSL = _env_bool("MAIL_USE_SSL", False)
    MAIL_USERNAME = os.getenv("MAIL_USERNAME", "")
    MAIL_PASSWORD = os.getenv("MAIL_PASSWORD", "")
    MAIL_DEFAULT_SENDER = os.getenv("MAIL_DEFAULT_SENDER", MAIL_USERNAME)

    SMTP_HOST = os.getenv("SMTP_HOST", MAIL_SERVER)
    SMTP_PORT = int(os.getenv("SMTP_PORT", MAIL_PORT))
    SMTP_USERNAME = os.getenv("SMTP_USERNAME", MAIL_USERNAME)
    SMTP_PASSWORD = os.getenv("SMTP_PASSWORD", MAIL_PASSWORD)
    EMAIL_FROM = os.getenv("EMAIL_FROM", MAIL_DEFAULT_SENDER)

    MAIL_BACKEND = os.getenv("MAIL_BACKEND", "SMTP").strip().upper()
    MS_TENANT_ID = os.getenv("MS_TENANT_ID", "").strip()
    MS_CLIENT_ID = os.getenv("MS_CLIENT_ID", "").strip()
    MS_CLIENT_SECRET = os.getenv("MS_CLIENT_SECRET", "").strip()
    MS_GRAPH_SCOPES = os.getenv(
        "MS_GRAPH_SCOPES", "https://graph.microsoft.com/.default"
    ).strip()
    MS_GRAPH_USER_EMAIL = os.getenv("MS_GRAPH_USER_EMAIL", "").strip()
    GRAPH_SENDER_EMAIL = os.getenv(
        "GRAPH_SENDER_EMAIL", MS_GRAPH_USER_EMAIL or MAIL_DEFAULT_SENDER
    ).strip()

    WHATSAPP_ENABLED = _env_bool("WHATSAPP_ENABLED", False)
    WHATSAPP_ACCESS_TOKEN = os.getenv("WHATSAPP_ACCESS_TOKEN", "").strip()
    WHATSAPP_BUSINESS_ACCOUNT_ID = os.getenv("WHATSAPP_BUSINESS_ACCOUNT_ID", "")
    WHATSAPP_APP_ID = os.getenv("WHATSAPP_APP_ID", "")
    WHATSAPP_PHONE_NUMBER_ID = os.getenv("WHATSAPP_PHONE_NUMBER_ID", "")
    WHATSAPP_MEDIA_MAX_BYTES = int(os.getenv("WHATSAPP_MEDIA_MAX_BYTES", 10 * 1024 * 1024))
    WHATSAPP_API_VERSION = os.getenv("WHATSAPP_API_VERSION", "v25.0").strip()
    WHATSAPP_VERIFY_TOKEN = os.getenv("WHATSAPP_VERIFY_TOKEN", "").strip()
    META_APP_SECRET = os.getenv("META_APP_SECRET", "").strip()
    WHATSAPP_REQUIRE_SIGNATURE = _env_bool(
        "WHATSAPP_REQUIRE_SIGNATURE", IS_PRODUCTION
    )

    LOG_DIR = os.getenv("LOG_DIR", "logs").strip() or "logs"
