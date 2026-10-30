from __future__ import annotations

import csv
import io
import re
from datetime import datetime
from typing import Iterable

from flask import current_app, session

from app.common.db import get_db_connection
from app.broadcast_management.providers import ProviderError, send_channel
from app.broadcast_management.rich_text import html_to_plain_text, sanitize_email_html
from app.broadcast_management.unsubscribe_service import (
    is_email_unsubscribed,
    normalize_email,
    unsubscribed_email_set,
)


MOBILE_RE = re.compile(r"^\+\d{10,15}$")
EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")


def _user_code() -> str:
    return str(session.get("user_code") or session.get("username") or "SYSTEM")[:50]


_RECIPIENT_FIELD_ALIASES = {
    "name": {
        "name", "receiver_name", "recipient_name", "receiver", "recipient",
    },
    "mobile": {
        "mobile", "mobile_no", "mobile_number", "phone", "phone_no", "phone_number",
        "whatsapp", "whatsapp_no", "whatsapp_number",
    },
    "email": {
        "email", "email_id", "email_address", "mail", "mail_id",
    },
    "country": {
        "country", "country_name",
    },
    "language": {
        "language", "language_code", "lang",
    },
    "reference_no": {
        "reference_no", "reference_number", "reference", "ref_no", "ref_number", "ref",
    },
    "tag": {
        "tag", "recipient_tag", "receiver_tag",
    },
    "telegram_chat_id": {
        "telegram_chat_id", "telegram_id", "telegram_chat", "chat_id",
    },
}

_RECIPIENT_FIELD_LOOKUP = {
    alias: canonical
    for canonical, aliases in _RECIPIENT_FIELD_ALIASES.items()
    for alias in aliases
}


def _normalise_field_name(value) -> str:
    """Normalise CSV/form headings to a stable field key."""
    key = str(value or "").replace("\ufeff", "").strip().lower()
    key = re.sub(r"[^a-z0-9]+", "_", key).strip("_")
    return key


def _recipient_source(row: dict | None) -> dict:
    source = {}
    for raw_key, value in (row or {}).items():
        key = _normalise_field_name(raw_key)
        if not key:
            continue
        canonical = _RECIPIENT_FIELD_LOOKUP.get(key, key)
        # If aliases duplicate the same field, keep the first non-empty value.
        if canonical not in source or not str(source.get(canonical) or "").strip():
            source[canonical] = value
    return source


def normalize_recipient(row: dict) -> dict:
    source = _recipient_source(row)
    return {
        "name": str(source.get("name") or "").strip()[:150],
        "mobile": str(source.get("mobile") or "").strip()[:30],
        "email": str(source.get("email") or "").strip()[:254],
        "country": str(source.get("country") or "").strip()[:100],
        "language": str(source.get("language") or "English").strip()[:30],
        "reference_no": str(source.get("reference_no") or "").strip()[:100],
        "tag": str(source.get("tag") or "").strip()[:100],
        "telegram_chat_id": str(source.get("telegram_chat_id") or "").strip()[:100],
    }


def parse_csv_bytes(raw: bytes) -> list[dict]:
    text = raw.decode("utf-8-sig", errors="strict")
    if not text.strip():
        return []

    # Excel CSV delimiter can vary by regional settings.
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t")
    except csv.Error:
        dialect = csv.excel

    reader = csv.DictReader(io.StringIO(text), dialect=dialect)
    if not reader.fieldnames:
        return []

    rows = []
    for raw_row in reader:
        row = normalize_recipient(raw_row)
        # Ignore completely blank lines. Language defaults to English, so it is
        # intentionally excluded from this blank-row test.
        if any(
            str(row.get(key) or "").strip()
            for key in (
                "name", "mobile", "email", "country", "reference_no", "tag",
                "telegram_chat_id",
            )
        ):
            rows.append(row)
    return rows


def validate_recipients(rows: Iterable[dict], channel: str) -> dict:
    channel = str(channel or "").upper()
    seen = set()
    output = []
    counts = {"total": 0, "valid": 0, "duplicate": 0, "invalid": 0}

    for raw in rows:
        row = normalize_recipient(raw)
        counts["total"] += 1

        if channel == "EMAIL":
            address = row["email"].lower()
            is_valid = bool(EMAIL_RE.match(row["email"]))
            invalid_reason = (
                "Email is required for Email broadcasts."
                if not row["email"]
                else "Email format is invalid."
            )
        elif channel == "TELEGRAM":
            address = row["telegram_chat_id"] or row["mobile"]
            is_valid = bool(address)
            invalid_reason = "Telegram Chat ID or Mobile is required for Telegram broadcasts."
        else:
            address = row["mobile"]
            is_valid = bool(MOBILE_RE.match(row["mobile"]))
            invalid_reason = (
                "Mobile is required for WhatsApp/SMS broadcasts."
                if not row["mobile"]
                else "Mobile must use international format, for example +919876543210."
            )

        if not is_valid:
            status = "INVALID"
            reason = invalid_reason
            counts["invalid"] += 1
        elif address in seen:
            status = "DUPLICATE"
            reason = "Duplicate destination for the selected channel."
            counts["duplicate"] += 1
        else:
            seen.add(address)
            status = "VALID"
            reason = ""
            counts["valid"] += 1

        row["validation_status"] = status
        row["validation_reason"] = reason
        output.append(row)

    return {"rows": output, "counts": counts}

def create_campaign(payload: dict, recipients: list[dict]) -> int:
    channel = str(payload.get("channel") or "").upper()
    campaign_type = str(payload.get("campaign_type") or "MARKETING").strip().upper()

    email_mode = str(payload.get("email_mode") or "RICH").strip().upper()
    if email_mode not in {"RICH", "PLAIN"}:
        email_mode = "RICH"
    if channel != "EMAIL":
        email_mode = "RICH"

    valid = validate_recipients(recipients, channel)
    send_mode = str(payload.get("send_mode") or "NOW").upper()
    initial_status = "DRAFT" if send_mode == "DRAFT" else ("SCHEDULED" if send_mode == "SCHEDULE" else "QUEUED")
    message_html = sanitize_email_html(str(payload.get("message_html") or "")) if channel == "EMAIL" else ""
    message_text = str(payload.get("message_text") or "")
    if channel == "EMAIL" and not message_text.strip() and message_html:
        message_text = html_to_plain_text(message_html)

    conn = cursor = None
    try:
        conn = get_db_connection()
        if not conn:
            raise RuntimeError("Database unavailable.")
        cursor = conn.cursor()

        suppressed_emails = set()
        if channel == "EMAIL" and campaign_type == "MARKETING":
            suppressed_emails = unsubscribed_email_set(
                (
                    row.get("email")
                    for row in valid["rows"]
                    if row.get("validation_status") == "VALID"
                ),
                cursor=cursor,
            )

        cursor.execute(
            """
            INSERT INTO broadcast_campaign
            (
                campaign_name, campaign_type, email_mode, channel_code, email_subject,
                message_html, message_text, language_code,
                website_url, contact_number, cta_text, cta_mode,
                media_type, media_url, media_name,
                send_mode, scheduled_on, campaign_status,
                total_count, valid_count, duplicate_count, invalid_count,
                created_by, created_on, updated_by, updated_on
            )
            VALUES
            (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
             %s,%s,%s,%s,%s,NOW(),%s,NOW())
            """,
            (
                str(payload.get("campaign_name") or "")[:200],
                campaign_type[:50],
                email_mode[:20],
                channel,
                str(payload.get("email_subject") or "")[:300],
                message_html,
                message_text,
                str(payload.get("language") or "English")[:30],
                str(payload.get("website_url") or "")[:500],
                str(payload.get("contact_number") or "")[:50],
                str(payload.get("cta_text") or "")[:300],
                str(payload.get("cta_mode") or "both")[:20],
                str(payload.get("media_type") or "")[:30] or None,
                str(payload.get("media_url") or "")[:1000] or None,
                str(payload.get("media_name") or "")[:255] or None,
                send_mode[:20],
                payload.get("scheduled_on") or None,
                initial_status,
                valid["counts"]["total"],
                valid["counts"]["valid"],
                valid["counts"]["duplicate"],
                valid["counts"]["invalid"],
                _user_code(),
                _user_code(),
            ),
        )
        campaign_id = int(cursor.lastrowid)

        insert_sql = """
            INSERT INTO broadcast_recipient
            (
                campaign_id, receiver_name, mobile_no, email_id, country_name,
                language_code, reference_no, recipient_tag, telegram_chat_id,
                validation_status, delivery_status, attempt_count, created_on
            )
            VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,0,NOW())
        """
        params = []
        suppressed_count = 0
        for row in valid["rows"]:
            email_is_suppressed = (
                channel == "EMAIL"
                and campaign_type == "MARKETING"
                and row["validation_status"] == "VALID"
                and normalize_email(row.get("email")) in suppressed_emails
            )
            if email_is_suppressed:
                delivery = "UNSUBSCRIBED"
                suppressed_count += 1
            elif row["validation_status"] == "VALID":
                delivery = "DRAFT" if initial_status == "DRAFT" else "QUEUED"
            else:
                delivery = "SKIPPED"
            params.append((
                campaign_id, row["name"], row["mobile"], row["email"], row["country"],
                row["language"], row["reference_no"], row["tag"], row["telegram_chat_id"],
                row["validation_status"], delivery,
            ))
        if params:
            cursor.executemany(insert_sql, params)

        if suppressed_count:
            cursor.execute(
                """
                UPDATE broadcast_campaign
                SET skipped_count=%s, updated_on=NOW()
                WHERE id=%s
                """,
                (suppressed_count, campaign_id),
            )

        conn.commit()
        return campaign_id
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_campaign(campaign_id: int) -> dict | None:
    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM broadcast_campaign WHERE id=%s", (campaign_id,))
        return cursor.fetchone()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()



def activate_campaign(campaign_id: int) -> dict:
    """Release a saved draft (or an interrupted queued campaign) for immediate sending."""
    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, campaign_status, send_mode FROM broadcast_campaign WHERE id=%s",
            (campaign_id,),
        )
        campaign = cursor.fetchone()
        if not campaign:
            raise RuntimeError("Campaign not found.")

        status = str(campaign.get("campaign_status") or "").upper()
        if status == "COMPLETED":
            raise RuntimeError("This broadcast is already completed.")

        # New drafts use delivery_status=DRAFT. Older V1.2.3/V1.2.5 drafts may
        # contain QUEUED rows; both are intentionally supported here.
        cursor.execute(
            """
            UPDATE broadcast_recipient
            SET delivery_status='QUEUED', updated_on=NOW()
            WHERE campaign_id=%s
              AND validation_status='VALID'
              AND attempt_count=0
              AND delivery_status IN ('DRAFT','QUEUED')
            """,
            (campaign_id,),
        )
        cursor.execute(
            """
            UPDATE broadcast_campaign
            SET campaign_status='QUEUED', send_mode='NOW', scheduled_on=NULL,
                updated_by=%s, updated_on=NOW()
            WHERE id=%s
            """,
            (_user_code(), campaign_id),
        )
        conn.commit()
        return campaign_status(campaign_id)
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

def dispatch_campaign(campaign_id: int, *, max_rows: int = 100) -> dict:
    """
    Send at most max_rows QUEUED rows. This is safe to call repeatedly from the
    existing scheduler worker or manually via the protected API endpoint.
    """
    conn = cursor = None
    result = {"processed": 0, "success": 0, "failed": 0, "unsubscribed": 0}
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("SELECT * FROM broadcast_campaign WHERE id=%s", (campaign_id,))
        campaign = cursor.fetchone()
        if not campaign:
            raise RuntimeError("Campaign not found.")
        if str(campaign.get("campaign_status") or "").upper() == "DRAFT":
            raise RuntimeError("Draft is not in the delivery queue. Use Send Now to release it first.")

        cursor.execute(
            """
            SELECT * FROM broadcast_recipient
            WHERE campaign_id=%s AND delivery_status IN ('QUEUED','RETRY')
            ORDER BY id
            LIMIT %s
            """,
            (campaign_id, int(max_rows)),
        )
        rows = cursor.fetchall() or []

        for row in rows:
            result["processed"] += 1

            if (
                str(campaign.get("channel_code") or "").upper() == "EMAIL"
                and str(campaign.get("campaign_type") or "MARKETING").strip().upper() == "MARKETING"
                and is_email_unsubscribed(row.get("email_id"), cursor=cursor)
            ):
                cursor.execute(
                    """
                    UPDATE broadcast_recipient
                    SET delivery_status='UNSUBSCRIBED',
                        last_error='Marketing email suppressed: recipient unsubscribed.',
                        updated_on=NOW()
                    WHERE id=%s
                    """,
                    (row["id"],),
                )
                result["unsubscribed"] += 1
                continue

            attempt = int(row.get("attempt_count") or 0) + 1
            try:
                provider_ref = send_channel(campaign["channel_code"], {
                    "recipient_id": row.get("id"),
                    "name": row.get("receiver_name"),
                    "mobile": row.get("mobile_no"),
                    "email": row.get("email_id"),
                    "country": row.get("country_name"),
                    "language": row.get("language_code"),
                    "reference_no": row.get("reference_no"),
                    "tag": row.get("recipient_tag"),
                    "telegram_chat_id": row.get("telegram_chat_id"),
                }, campaign)
                cursor.execute(
                    """
                    UPDATE broadcast_recipient
                    SET delivery_status='SUCCESS', attempt_count=%s,
                        provider_reference=%s, last_error=NULL,
                        sent_on=NOW(), updated_on=NOW()
                    WHERE id=%s
                    """,
                    (attempt, str(provider_ref)[:255], row["id"]),
                )
                result["success"] += 1
            except Exception as exc:
                current_app.logger.exception(
                    "BROADCAST_DELIVERY_FAILED campaign_id=%s recipient_id=%s",
                    campaign_id, row["id"]
                )
                cursor.execute(
                    """
                    UPDATE broadcast_recipient
                    SET delivery_status='FAILED', attempt_count=%s,
                        last_error=%s, updated_on=NOW()
                    WHERE id=%s
                    """,
                    (attempt, str(exc)[:1000], row["id"]),
                )
                result["failed"] += 1

        cursor.execute(
            """
            SELECT
                SUM(delivery_status='QUEUED') AS queued_count,
                SUM(delivery_status='SUCCESS') AS success_count,
                SUM(delivery_status='FAILED') AS failed_count,
                SUM(delivery_status IN ('SKIPPED','UNSUBSCRIBED')) AS skipped_count
            FROM broadcast_recipient WHERE campaign_id=%s
            """,
            (campaign_id,),
        )
        counts = cursor.fetchone() or {}
        queued = int(counts.get("queued_count") or 0)
        status = "COMPLETED" if queued == 0 else "IN_PROGRESS"
        cursor.execute(
            """
            UPDATE broadcast_campaign
            SET campaign_status=%s,
                success_count=%s, failed_count=%s, skipped_count=%s,
                started_on=COALESCE(started_on,NOW()),
                completed_on=CASE WHEN %s='COMPLETED' THEN NOW() ELSE completed_on END,
                updated_on=NOW()
            WHERE id=%s
            """,
            (
                status,
                int(counts.get("success_count") or 0),
                int(counts.get("failed_count") or 0),
                int(counts.get("skipped_count") or 0),
                status,
                campaign_id,
            ),
        )
        conn.commit()
        result["status"] = status
        return result
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def retry_failed(campaign_id: int) -> int:
    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE broadcast_recipient
            SET delivery_status='RETRY', updated_on=NOW()
            WHERE campaign_id=%s
              AND delivery_status='FAILED'
              AND attempt_count < 3
            """,
            (campaign_id,),
        )
        changed = cursor.rowcount
        conn.commit()
        return int(changed or 0)
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def campaign_status(campaign_id: int) -> dict:
    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT c.id, c.campaign_name, c.channel_code, c.campaign_status,
                   c.total_count, c.valid_count, c.duplicate_count, c.invalid_count,
                   c.success_count, c.failed_count, c.skipped_count,
                   SUM(r.delivery_status IN ('QUEUED','RETRY')) AS queued_count
            FROM broadcast_campaign c
            LEFT JOIN broadcast_recipient r ON r.campaign_id=c.id
            WHERE c.id=%s
            GROUP BY c.id
            """,
            (campaign_id,),
        )
        return cursor.fetchone() or {}
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
