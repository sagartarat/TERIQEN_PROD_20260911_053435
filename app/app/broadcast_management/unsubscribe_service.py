from __future__ import annotations

import os
import re
from typing import Iterable
from urllib.parse import quote, urlparse

from flask import current_app
from itsdangerous import BadSignature, URLSafeSerializer

from app.common.db import get_db_connection


_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")
_TOKEN_SALT = "teriqen-broadcast-email-unsubscribe-v1"
_LEGACY_TOKEN_SALT = "healtheasy-broadcast-email-unsubscribe-v1"


def normalize_email(value: str) -> str:
    return str(value or "").strip().lower()[:254]


def _unsubscribe_secret() -> str:
    secret = str(
        current_app.config.get("BROADCAST_UNSUBSCRIBE_SECRET")
        or os.environ.get("BROADCAST_UNSUBSCRIBE_SECRET")
        or current_app.config.get("SECRET_KEY")
        or ""
    )
    if not secret:
        raise RuntimeError(
            "Email unsubscribe is not configured. Set BROADCAST_UNSUBSCRIBE_SECRET "
            "or a stable Flask SECRET_KEY."
        )
    return secret


def _serializer(salt: str = _TOKEN_SALT) -> URLSafeSerializer:
    return URLSafeSerializer(secret_key=_unsubscribe_secret(), salt=salt)


def make_unsubscribe_token(
    email: str,
    *,
    campaign_id: int | None = None,
    recipient_id: int | None = None,
) -> str:
    email = normalize_email(email)
    if not _EMAIL_RE.fullmatch(email):
        raise ValueError("A valid recipient email is required for unsubscribe.")
    payload = {
        "v": 1,
        "e": email,
        "c": int(campaign_id) if campaign_id else None,
        "r": int(recipient_id) if recipient_id else None,
    }
    return str(_serializer().dumps(payload))


def parse_unsubscribe_token(token: str) -> dict:
    token = str(token or "").strip()
    if not token or len(token) > 4096:
        raise BadSignature("Invalid unsubscribe token.")
    try:
        payload = _serializer().loads(token)
    except BadSignature:
        # Keep unsubscribe links generated before the TERIQEN rebrand valid.
        payload = _serializer(_LEGACY_TOKEN_SALT).loads(token)
    if not isinstance(payload, dict) or int(payload.get("v") or 0) != 1:
        raise BadSignature("Invalid unsubscribe token.")
    email = normalize_email(payload.get("e"))
    if not _EMAIL_RE.fullmatch(email):
        raise BadSignature("Invalid unsubscribe token.")
    return {
        "email": email,
        "campaign_id": int(payload["c"]) if payload.get("c") else None,
        "recipient_id": int(payload["r"]) if payload.get("r") else None,
    }


def public_base_url() -> str:
    value = str(
        current_app.config.get("BROADCAST_PUBLIC_BASE_URL")
        or os.environ.get("BROADCAST_PUBLIC_BASE_URL")
        or current_app.config.get("TERIQEN_PUBLIC_BASE_URL")
        or os.environ.get("TERIQEN_PUBLIC_BASE_URL")
        or current_app.config.get("HEALTHEASY_PUBLIC_BASE_URL")
        or os.environ.get("HEALTHEASY_PUBLIC_BASE_URL")
        or ""
    ).strip().rstrip("/")

    if not value:
        server_name = str(current_app.config.get("SERVER_NAME") or "").strip()
        scheme = str(current_app.config.get("PREFERRED_URL_SCHEME") or "").strip().lower()
        if server_name and scheme in {"https", "http"}:
            value = f"{scheme}://{server_name}".rstrip("/")

    parsed = urlparse(value)
    if (
        parsed.scheme.lower() not in {"https", "http"}
        or not parsed.netloc
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        return ""

    # Production marketing email should always use HTTPS. HTTP is accepted only
    # in Flask debug/testing mode for local development.
    if parsed.scheme.lower() != "https" and not (
        bool(current_app.debug) or bool(current_app.testing)
    ):
        return ""
    return value


def build_unsubscribe_url(
    email: str,
    *,
    campaign_id: int | None = None,
    recipient_id: int | None = None,
) -> str:
    base = public_base_url()
    if not base:
        return ""
    token = make_unsubscribe_token(
        email,
        campaign_id=campaign_id,
        recipient_id=recipient_id,
    )
    return f"{base}/broadcast-management/unsubscribe?token={quote(token, safe='')}"


def _row_status(row) -> str:
    if not row:
        return ""
    if isinstance(row, dict):
        return str(row.get("suppression_status") or "")
    try:
        return str(row[0] or "")
    except Exception:
        return ""


def is_email_unsubscribed(email: str, *, cursor=None) -> bool:
    email = normalize_email(email)
    if not email:
        return False

    own_conn = own_cursor = None
    try:
        if cursor is None:
            own_conn = get_db_connection()
            if not own_conn:
                raise RuntimeError("Database unavailable.")
            own_cursor = own_conn.cursor(dictionary=True)
            cursor = own_cursor

        cursor.execute(
            """
            SELECT suppression_status
            FROM broadcast_email_suppression
            WHERE email_id=%s
            LIMIT 1
            """,
            (email,),
        )
        return _row_status(cursor.fetchone()).strip().upper() == "UNSUBSCRIBED"
    finally:
        if own_cursor:
            own_cursor.close()
        if own_conn:
            own_conn.close()


def unsubscribed_email_set(emails: Iterable[str], *, cursor) -> set[str]:
    unique = sorted({normalize_email(value) for value in emails if normalize_email(value)})
    if not unique:
        return set()

    output: set[str] = set()
    for start in range(0, len(unique), 500):
        chunk = unique[start:start + 500]
        placeholders = ",".join(["%s"] * len(chunk))
        cursor.execute(
            f"""
            SELECT email_id
            FROM broadcast_email_suppression
            WHERE suppression_status='UNSUBSCRIBED'
              AND email_id IN ({placeholders})
            """,
            tuple(chunk),
        )
        for row in cursor.fetchall() or []:
            if isinstance(row, dict):
                value = normalize_email(row.get("email_id"))
            else:
                value = normalize_email(row[0] if row else "")
            if value:
                output.add(value)
    return output


def suppress_email(
    email: str,
    *,
    source: str = "EMAIL_LINK",
    campaign_id: int | None = None,
    recipient_id: int | None = None,
    reason: str = "Recipient requested marketing email unsubscribe.",
) -> dict:
    email = normalize_email(email)
    if not _EMAIL_RE.fullmatch(email):
        raise ValueError("Invalid email address.")

    source = str(source or "EMAIL_LINK").strip().upper()[:30]
    reason = str(reason or "")[:255] or None

    conn = cursor = None
    try:
        conn = get_db_connection()
        if not conn:
            raise RuntimeError("Database unavailable.")
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            INSERT INTO broadcast_email_suppression
            (
                email_id, suppression_status, suppression_source,
                source_campaign_id, source_recipient_id, reason,
                unsubscribed_on, resubscribed_on, created_on, updated_on
            )
            VALUES
            (%s,'UNSUBSCRIBED',%s,%s,%s,%s,NOW(),NULL,NOW(),NOW())
            ON DUPLICATE KEY UPDATE
                suppression_status='UNSUBSCRIBED',
                suppression_source=VALUES(suppression_source),
                source_campaign_id=VALUES(source_campaign_id),
                source_recipient_id=VALUES(source_recipient_id),
                reason=VALUES(reason),
                unsubscribed_on=NOW(),
                resubscribed_on=NULL,
                updated_on=NOW()
            """,
            (email, source, campaign_id, recipient_id, reason),
        )

        # If a campaign is still queued/scheduled when the recipient opts out,
        # suppress all unsent copies of the same email immediately.
        cursor.execute(
            """
            UPDATE broadcast_recipient r
            JOIN broadcast_campaign c ON c.id=r.campaign_id
            SET r.delivery_status='UNSUBSCRIBED',
                r.last_error='Marketing email suppressed: recipient unsubscribed.',
                r.updated_on=NOW()
            WHERE c.channel_code='EMAIL'
              AND UPPER(COALESCE(c.campaign_type,'MARKETING'))='MARKETING'
              AND LOWER(TRIM(r.email_id))=%s
              AND r.delivery_status IN ('DRAFT','QUEUED','RETRY')
            """,
            (email,),
        )
        affected_unsent = int(cursor.rowcount or 0)

        conn.commit()
        return {
            "email": email,
            "status": "UNSUBSCRIBED",
            "affected_unsent": affected_unsent,
        }
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def masked_email(email: str) -> str:
    email = normalize_email(email)
    if "@" not in email:
        return ""
    local, domain = email.split("@", 1)
    if len(local) <= 2:
        left = local[:1] + "*"
    else:
        left = local[:2] + "*" * min(6, max(2, len(local) - 2))
    return f"{left}@{domain}"
