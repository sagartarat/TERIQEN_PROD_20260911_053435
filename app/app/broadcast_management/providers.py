from __future__ import annotations

import base64
import html
import json
import mimetypes
import os
import re
import smtplib
from email import encoders, policy
from email.mime.base import MIMEBase
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from pathlib import Path
from urllib.parse import urlparse

import requests
from flask import current_app

from app.broadcast_management.rich_text import html_to_plain_text, sanitize_email_html
from app.broadcast_management.unsubscribe_service import build_unsubscribe_url


class ProviderError(RuntimeError):
    pass


def _whatsapp_text(text: str) -> str:
    return (text or "").strip()


def send_whatsapp(to_mobile: str, text: str, *, media_url: str | None = None,
                  media_kind: str | None = None) -> str:
    token = str(current_app.config.get("WHATSAPP_ACCESS_TOKEN") or "").strip()
    phone_number_id = str(
        current_app.config.get("WHATSAPP_PHONE_NUMBER_ID") or ""
    ).strip()
    version = str(
        current_app.config.get("WHATSAPP_GRAPH_VERSION")
        or current_app.config.get("WHATSAPP_API_VERSION")
        or "v25.0"
    ).strip()

    if not token or not phone_number_id:
        raise ProviderError("WhatsApp provider is not configured.")

    url = f"https://graph.facebook.com/{version}/{phone_number_id}/messages"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}

    # Free-form text is valid only when Meta allows it for the conversation.
    # Marketing broadcasts should normally use an approved template in production.
    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": str(to_mobile or "").replace("+", "").strip(),
        "type": "text",
        "text": {"preview_url": True, "body": _whatsapp_text(text)},
    }

    # Media URL support is deliberately conservative. A public HTTPS asset can
    # be delivered directly as image/video. Local/private uploads should first
    # be uploaded to Meta Media API and the resulting media id used.
    if media_url and str(media_url).startswith("https://") and media_kind in {"image", "video"}:
        payload["type"] = media_kind
        payload[media_kind] = {"link": media_url, "caption": _whatsapp_text(text)[:1024]}

    response = requests.post(url, headers=headers, json=payload, timeout=30)
    if response.status_code >= 400:
        raise ProviderError(f"WhatsApp API {response.status_code}: {response.text[:500]}")

    data = response.json() if response.content else {}
    messages = data.get("messages") or []
    return str(messages[0].get("id") if messages else "") or "WHATSAPP_ACCEPTED"



_TOKEN_RE = re.compile(r"{{\s*([A-Za-z0-9_]+)\s*}}")

# Builder metadata is required only while the template is stored. It is removed
# from the recipient-facing email to reduce payload size and avoid Gmail clipping.
_BUILDER_META_RE = re.compile(r"<!--HE-BUILDER:[A-Za-z0-9_-]{1,180000}-->", re.IGNORECASE)
_LEGACY_RESPONSIVE_CSS = (
    "@media only screen and (max-width:480px){"
    ".he-email-inner{width:100%!important;max-width:100%!important}"
    ".he-card-cell{display:block!important;width:100%!important;max-width:100%!important;box-sizing:border-box!important}"
    ".he-card-section-pad{padding-left:12px!important;padding-right:12px!important}"
    ".he-card-section-title{padding-left:16px!important;padding-right:16px!important}"
    "}"
)
_OUTBOUND_RESPONSIVE_CSS = (
    "@media only screen and (max-width:480px){"
    ".he-email-inner{width:100%!important;max-width:100%!important}"
    ".he-card-cell,.he-stack-cell{display:block!important;width:100%!important;max-width:100%!important;box-sizing:border-box!important}"
    ".he-feature-cell{display:inline-block!important;box-sizing:border-box!important;vertical-align:top!important}"
    ".he-feature-cell-two{width:50%!important;max-width:50%!important}"
    ".he-feature-cell-one{display:block!important;width:100%!important;max-width:100%!important}"
    ".he-card-section-pad,.he-grid-pad{padding-left:10px!important;padding-right:10px!important}"
    ".he-card-section-title,.he-grid-title{padding-left:14px!important;padding-right:14px!important}"
    ".he-mobile-section-pad{padding-left:16px!important;padding-right:16px!important}"
    ".he-mobile-brand{font-size:22px!important;line-height:1.25!important;overflow-wrap:anywhere!important;word-break:normal!important;white-space:normal!important}"
    ".he-mobile-eyebrow{font-size:10px!important;line-height:1.35!important;overflow-wrap:anywhere!important;word-break:normal!important;white-space:normal!important}"
    ".he-mobile-hero-heading{font-size:24px!important;line-height:1.25!important;overflow-wrap:anywhere!important;word-break:normal!important;white-space:normal!important}"
    ".he-mobile-cta-heading{font-size:19px!important;line-height:1.3!important;overflow-wrap:anywhere!important;word-break:normal!important;white-space:normal!important}"
    ".he-mobile-copy,.he-mobile-rich{overflow-wrap:anywhere!important;word-break:normal!important;white-space:normal!important}"
    ".he-mobile-button{max-width:100%!important;box-sizing:border-box!important;white-space:normal!important;overflow-wrap:anywhere!important}"
    "}"
)


def _finalize_outbound_html(fragment: str) -> str:
    """Build the final recipient HTML without internal builder metadata."""
    body = str(fragment or "")
    body = _BUILDER_META_RE.sub("", body)
    # Older V1.2.12 saves may already contain the media query as plain text
    # because the sanitizer removed <style> tags but retained their contents.
    body = body.replace(_LEGACY_RESPONSIVE_CSS, "")
    body = re.sub(
        r"<style\b[^>]*>\s*" + re.escape(_LEGACY_RESPONSIVE_CSS) + r"\s*</style>",
        "",
        body,
        count=1,
        flags=re.IGNORECASE,
    )
    responsive = (
        f'<style type="text/css">{_OUTBOUND_RESPONSIVE_CSS}</style>'
        if any(token in body for token in (
            "he-email-inner", "he-card-cell", "he-card-section-pad", "he-card-section-title",
            "he-stack-cell", "he-feature-cell", "he-grid-pad", "he-grid-title"
        ))
        else ""
    )
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        '<meta name="viewport" content="width=device-width,initial-scale=1">'
        + responsive
        + '</head><body style="margin:0;padding:0">'
        + body
        + '</body></html>'
    )


def _is_plain_email(campaign: dict) -> bool:
    return (
        str(campaign.get("email_mode") or "RICH")
        .strip()
        .upper()
        == "PLAIN"
    )


def _recipient_tokens(recipient: dict, campaign: dict) -> dict[str, str]:
    name = str(recipient.get("name") or "").strip()
    first_name = name.split()[0] if name else ""
    unsubscribe_url = ""
    if (
        not _is_plain_email(campaign)
        and str(campaign.get("campaign_type") or "").strip().upper() == "MARKETING"
    ):
        unsubscribe_url = build_unsubscribe_url(
            str(recipient.get("email") or ""),
            campaign_id=campaign.get("id"),
            recipient_id=recipient.get("recipient_id"),
        )
    return {
        "recipient_name": name,
        "name": name,
        "first_name": first_name,
        "mobile": str(recipient.get("mobile") or "").strip(),
        "mobile_no": str(recipient.get("mobile") or "").strip(),
        "recipient_mobile": str(recipient.get("mobile") or "").strip(),
        "email": str(recipient.get("email") or "").strip(),
        "email_id": str(recipient.get("email") or "").strip(),
        "recipient_email": str(recipient.get("email") or "").strip(),
        "country": str(recipient.get("country") or "").strip(),
        "country_name": str(recipient.get("country") or "").strip(),
        "language": str(recipient.get("language") or "").strip(),
        "language_code": str(recipient.get("language") or "").strip(),
        "reference_no": str(recipient.get("reference_no") or "").strip(),
        "recipient_tag": str(recipient.get("tag") or "").strip(),
        "tag": str(recipient.get("tag") or "").strip(),
        "telegram_chat_id": str(recipient.get("telegram_chat_id") or "").strip(),
        "campaign_name": str(campaign.get("campaign_name") or "").strip(),
        "website_url": str(campaign.get("website_url") or "").strip(),
        "contact_number": str(campaign.get("contact_number") or "").strip(),
        "unsubscribe_url": unsubscribe_url,
    }


def _marketing_unsubscribe_url(campaign: dict, recipient: dict) -> str:
    if _is_plain_email(campaign):
        return ""
    if str(campaign.get("campaign_type") or "").strip().upper() != "MARKETING":
        return ""
    url = build_unsubscribe_url(
        str(recipient.get("email") or ""),
        campaign_id=campaign.get("id"),
        recipient_id=recipient.get("recipient_id"),
    )
    if not url:
        raise ProviderError(
            "Marketing Email requires a public HTTPS unsubscribe URL. "
            "Set BROADCAST_PUBLIC_BASE_URL to this TERIQEN application's public HTTPS origin."
        )
    return url


def _email_unsubscribe_html(campaign: dict, recipient: dict, existing_html: str) -> str:
    url = _marketing_unsubscribe_url(campaign, recipient)
    if not url:
        return ""

    escaped_url = html.escape(url, quote=True)
    if escaped_url in str(existing_html or "") or url in str(existing_html or ""):
        return ""

    return (
        '<div style="margin:18px 0 0;padding:14px 18px;'
        'border-top:1px solid #e5e7eb;text-align:center;'
        'font-family:Arial,sans-serif;font-size:11px;line-height:1.5;color:#687782">'
        'You are receiving this marketing email from Cadupius Health Assist. '
        f'<a href="{escaped_url}" style="color:#0B6E99;text-decoration:underline">'
        'Unsubscribe</a>'
        '</div>'
    )


def _email_unsubscribe_headers(campaign: dict, recipient: dict) -> dict[str, str]:
    url = _marketing_unsubscribe_url(campaign, recipient)
    if not url:
        return {}
    return {
        "List-Unsubscribe": f"<{url}>",
        "List-Unsubscribe-Post": "List-Unsubscribe=One-Click",
    }


def _render_tokens(value: str, recipient: dict, campaign: dict, *, html_mode: bool = False) -> str:
    tokens = _recipient_tokens(recipient, campaign)

    def repl(match):
        key = str(match.group(1) or "").lower()
        if key not in tokens:
            return match.group(0)
        result = tokens.get(key, "")
        return html.escape(result, quote=True) if html_mode else result

    return _TOKEN_RE.sub(repl, str(value or ""))


def _safe_web_url(value: str) -> str:
    value = str(value or "").strip()
    if value.lower().startswith(("https://", "http://")):
        return value
    return ""


def _email_cta_html(campaign: dict, recipient: dict) -> str:
    mode = str(campaign.get("cta_mode") or "both").strip().lower()
    cta_text = _render_tokens(str(campaign.get("cta_text") or ""), recipient, campaign).strip()
    website = _render_tokens(str(campaign.get("website_url") or ""), recipient, campaign).strip()
    phone = _render_tokens(str(campaign.get("contact_number") or ""), recipient, campaign).strip()

    parts = []
    if cta_text:
        parts.append(f'<div style="margin-bottom:8px;font-weight:600">{html.escape(cta_text)}</div>')

    if mode in {"both", "web", "website"} and website:
        safe_url = _safe_web_url(website)
        if safe_url:
            parts.append(
                '<div style="margin:4px 0">🌐 '
                f'<a href="{html.escape(safe_url, quote=True)}">{html.escape(website)}</a></div>'
            )
        else:
            parts.append(f'<div style="margin:4px 0">🌐 {html.escape(website)}</div>')

    if mode in {"both", "phone", "call"} and phone:
        digits = re.sub(r"\D", "", phone)
        phone_display = html.escape(phone)
        links = []
        if digits:
            links.append(f'<a href="tel:+{digits}">{phone_display}</a>')
            links.append(f'<a href="https://wa.me/{digits}">WhatsApp</a>')
        if links:
            parts.append('<div style="margin:4px 0">📞 Call / WhatsApp: ' + ' &nbsp;|&nbsp; '.join(links) + '</div>')
        else:
            parts.append(f'<div style="margin:4px 0">📞 Call / WhatsApp: {phone_display}</div>')

    if not parts:
        return ""
    return '<div style="margin-top:20px;padding-top:14px;border-top:1px solid #e5e7eb">' + ''.join(parts) + '</div>'


def _resolve_local_media(media_url: str) -> Path | None:
    prefix = "local://broadcast_media/"
    media_url = str(media_url or "")
    if not media_url.startswith(prefix):
        return None
    filename = Path(media_url[len(prefix):]).name
    if not filename:
        return None
    base = Path(current_app.instance_path).resolve() / "broadcast_media"
    path = (base / filename).resolve()
    if path.parent != base.resolve() or not path.is_file():
        return None
    return path


def _broadcast_public_base_url() -> str:
    """Return the externally reachable HTTPS root for public Broadcast media.

    This is intentionally explicit. TERIQEN must never guess a private/local
    host and place it in recipient email. Set BROADCAST_PUBLIC_BASE_URL to the
    public HTTPS origin that serves this same TERIQEN application.
    """
    configured = (
        current_app.config.get("BROADCAST_PUBLIC_BASE_URL")
        or os.environ.get("BROADCAST_PUBLIC_BASE_URL")
        or current_app.config.get("TERIQEN_PUBLIC_BASE_URL")
        or os.environ.get("TERIQEN_PUBLIC_BASE_URL")
        or current_app.config.get("HEALTHEASY_PUBLIC_BASE_URL")
        or os.environ.get("HEALTHEASY_PUBLIC_BASE_URL")
        or ""
    )
    value = str(configured or "").strip().rstrip("/")

    if not value:
        server_name = str(current_app.config.get("SERVER_NAME") or "").strip()
        scheme = str(current_app.config.get("PREFERRED_URL_SCHEME") or "").strip().lower()
        if server_name and scheme == "https":
            value = f"https://{server_name}".rstrip("/")

    if not value:
        raise ProviderError(
            "Email Builder images require a public HTTPS media address. "
            "Set BROADCAST_PUBLIC_BASE_URL to this TERIQEN site's public URL "
            "(for example https://app.example.com), restart TERIQEN, and retry."
        )

    parsed = urlparse(value)
    if parsed.scheme.lower() != "https" or not parsed.netloc or parsed.username or parsed.password:
        raise ProviderError(
            "BROADCAST_PUBLIC_BASE_URL must be a public HTTPS URL without embedded credentials."
        )
    if parsed.query or parsed.fragment:
        raise ProviderError(
            "BROADCAST_PUBLIC_BASE_URL must not contain a query string or fragment."
        )
    return value


def _public_media_url(media_url: str) -> str:
    """Convert a controlled local Broadcast image reference to its public URL."""
    path = _resolve_local_media(media_url)
    if not path:
        raise ProviderError(
            "An image used by this Email Template is missing from TERIQEN Broadcast Media. "
            "Open the template, choose the image again, and save it before sending."
        )
    return (
        _broadcast_public_base_url()
        + "/broadcast-management/public-media/"
        + path.name
    )


def _publicize_html_media(body_html: str) -> str:
    """Rewrite Builder image references to the dedicated public HTTPS route.

    V1.3.4 also recognises the authenticated Builder-preview URL used by older
    saved templates. The conversion is intentionally limited to ``src``
    attributes whose filename resolves inside ``instance/broadcast_media``.
    """
    pattern = re.compile(
        r"(?P<prefix>\bsrc\s*=\s*)(?P<quote>[\"'])(?P<url>"
        r"local://broadcast_media/[A-Za-z0-9._-]+"
        r"|(?:https://[^\s<>'\"]+)?/broadcast-management/api/media/[A-Za-z0-9._-]+"
        r")(?P=quote)",
        re.IGNORECASE,
    )

    def replace(match: re.Match) -> str:
        media_ref = match.group("url")
        if media_ref.lower().startswith("local://broadcast_media/"):
            local_ref = media_ref
        else:
            filename = Path(urlparse(media_ref).path).name
            local_ref = f"local://broadcast_media/{filename}"
        public_url = _public_media_url(local_ref)
        quote = match.group("quote")
        return f'{match.group("prefix")}{quote}{html.escape(public_url, quote=True)}{quote}'

    return pattern.sub(replace, str(body_html or ""))


def _assert_no_private_builder_media(body_html: str) -> None:
    """Fail closed instead of silently sending an email with invisible images."""
    value = str(body_html or "")
    if re.search(r"\bsrc\s*=\s*[\"']local://broadcast_media/", value, re.IGNORECASE):
        raise ProviderError(
            "Email Builder image conversion did not complete. No email was sent. "
            "Please reopen the template and retry."
        )
    if re.search(
        r"\bsrc\s*=\s*[\"'](?:https://[^\s<>'\"]+)?/broadcast-management/api/media/",
        value,
        re.IGNORECASE,
    ):
        raise ProviderError(
            "Email Builder preview image URL remained in outbound email. No email was sent. "
            "Please reopen the template and retry."
        )


def _inline_email_media(campaign: dict) -> dict | None:
    media_url = str(campaign.get("media_url") or "").strip()
    media_name = str(campaign.get("media_name") or "broadcast-image").strip() or "broadcast-image"
    path = _resolve_local_media(media_url)
    if not path:
        return None
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if not mime_type.startswith("image/"):
        return None
    try:
        raw = path.read_bytes()
    except OSError as exc:
        raise ProviderError(f"Unable to read Broadcast email media: {exc}") from exc
    if not raw:
        return None
    return {
        "name": media_name,
        "mime_type": mime_type,
        "bytes": raw,
        "content_id": "broadcast_media",
    }


def _inline_html_media(body_html: str) -> tuple[str, list[dict]]:
    """Replace TERIQEN local image URLs in template HTML with CID references."""
    attachments: list[dict] = []
    by_url: dict[str, str] = {}
    pattern = re.compile(
        r"(?P<prefix>\bsrc\s*=\s*)(?P<quote>[\"'])(?P<url>local://broadcast_media/[A-Za-z0-9._-]+)(?P=quote)",
        re.IGNORECASE,
    )

    def replace(match: re.Match) -> str:
        media_url = match.group("url")
        cid = by_url.get(media_url)
        if not cid:
            path = _resolve_local_media(media_url)
            if not path:
                raise ProviderError(
                    "An image used by this Email Template is missing from TERIQEN Broadcast Media. "
                    "Open the template, choose the image again, and save it before sending."
                )
            mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
            if not mime_type.startswith("image/"):
                raise ProviderError("Email Template local media must be an image file.")
            try:
                raw = path.read_bytes()
            except OSError as exc:
                raise ProviderError(f"Unable to read Email Template image: {exc}") from exc
            if not raw:
                raise ProviderError("An Email Template image file is empty.")
            cid = f"broadcast_body_{len(attachments) + 1}"
            by_url[media_url] = cid
            attachments.append({
                "name": path.name,
                "mime_type": mime_type,
                "bytes": raw,
                "content_id": cid,
            })
        quote = match.group("quote")
        return f'{match.group("prefix")}{quote}cid:{cid}{quote}'

    return pattern.sub(replace, str(body_html or "")), attachments


def _normalize_inline_media(inline_media) -> list[dict]:
    if not inline_media:
        return []
    if isinstance(inline_media, dict):
        return [inline_media]
    if isinstance(inline_media, (list, tuple)):
        return [item for item in inline_media if isinstance(item, dict)]
    return []


def _email_media_html(campaign: dict, inline_media: dict | None = None) -> str:
    # V1.3.3: local Broadcast images are published through the dedicated
    # random-name public-media route instead of being attached as CID/MIME parts.
    media_url = str(campaign.get("media_url") or "").strip()
    if media_url.startswith("local://broadcast_media/"):
        media_url = _public_media_url(media_url)

    if media_url.lower().startswith(("https://", "http://")):
        lower = media_url.lower().split("?", 1)[0]
        if lower.endswith((".jpg", ".jpeg", ".png", ".gif", ".webp")):
            return (
                '<div style="margin:0 0 16px 0">'
                f'<img src="{html.escape(media_url, quote=True)}" alt="" '
                'style="display:block;max-width:100%;height:auto;border:0;border-radius:8px">'
                '</div>'
            )
        return (
            '<div style="margin:0 0 16px 0">'
            f'<a href="{html.escape(media_url, quote=True)}">{html.escape(str(campaign.get("media_name") or media_url))}</a>'
            '</div>'
        )
    return ""


def _prepare_email_content(recipient: dict, campaign: dict) -> tuple[str, str, str, list[dict]]:
    subject = _render_tokens(
        str(campaign.get("email_subject") or ""),
        recipient,
        campaign,
    )

    plain_email = _is_plain_email(campaign)

    # V1.3.4 converts Builder-local image references BEFORE sanitization. This
    # guarantees the sanitizer sees an ordinary HTTPS src and also supports
    # templates that were saved with the authenticated /api/media preview URL.
    raw_html_source = str(campaign.get("message_html") or "").strip()
    public_html_source = _publicize_html_media(raw_html_source)
    html_source = sanitize_email_html(public_html_source)

    # Public HTTPS images require no CID/MIME inline attachments.
    campaign_inline_media = None

    plain_body = _render_tokens(
        str(campaign.get("message_text") or ""),
        recipient,
        campaign,
    )

    if html_source:
        rendered_html = _render_tokens(
            html_source,
            recipient,
            campaign,
            html_mode=True,
        )

        if plain_email:
            body_html = rendered_html
        else:
            body_html = (
                _email_media_html(campaign, campaign_inline_media)
                + rendered_html
                + _email_cta_html(campaign, recipient)
            )
    else:
        base_html = html.escape(plain_body).replace("\n", "<br>")

        if plain_email:
            body_html = base_html
        else:
            body_html = (
                _email_media_html(campaign, campaign_inline_media)
                + base_html
            )

    # V1.3.8: every MARKETING email must contain a working signed opt-out URL.
    # New/edited Builder footers can contain {{unsubscribe_url}} directly.
    # Older saved templates receive this compact fallback automatically.
    body_html += _email_unsubscribe_html(campaign, recipient, body_html)

    unsubscribe_url = _marketing_unsubscribe_url(campaign, recipient)
    if unsubscribe_url and unsubscribe_url not in plain_body:
        if plain_body.strip():
            plain_body = plain_body.rstrip() + "\n\n"
        plain_body += f"Unsubscribe from marketing emails: {unsubscribe_url}"

    # Generate the plain-text alternative before adding the controlled <head>
    # stylesheet, otherwise CSS from the head could leak into the plain part.
    if not plain_body.strip():
        plain_body = html_to_plain_text(body_html)

    # Remove internal builder metadata / legacy visible CSS and add the one
    # controlled responsive stylesheet after sanitization.
    body_html = _finalize_outbound_html(body_html)

    # Defence in depth for legacy fragments introduced outside message_html.
    body_html = _publicize_html_media(body_html)
    _assert_no_private_builder_media(body_html)

    return subject, body_html, plain_body, []


def _mail_backend() -> str:
    backend = str(current_app.config.get("MAIL_BACKEND") or "SMTP").strip().upper()
    aliases = {
        "MSGRAPH": "GRAPH",
        "MS_GRAPH": "GRAPH",
        "MICROSOFT_GRAPH": "GRAPH",
    }
    return aliases.get(backend, backend)


def _default_sender_email() -> str:
    return str(
        current_app.config.get("MS_GRAPH_USER_EMAIL")
        or current_app.config.get("GRAPH_SENDER_EMAIL")
        or current_app.config.get("MAIL_DEFAULT_SENDER")
        or current_app.config.get("MAIL_USERNAME")
        or current_app.config.get("SMTP_USERNAME")
        or current_app.config.get("EMAIL_FROM")
        or ""
    ).strip()


def _graph_access_token() -> str:
    tenant_id = str(current_app.config.get("MS_TENANT_ID") or "").strip()
    client_id = str(current_app.config.get("MS_CLIENT_ID") or "").strip()
    client_secret = str(current_app.config.get("MS_CLIENT_SECRET") or "").strip()
    scope = str(
        current_app.config.get("MS_GRAPH_SCOPES")
        or "https://graph.microsoft.com/.default"
    ).strip()

    missing = [
        name for name, value in (
            ("MS_TENANT_ID", tenant_id),
            ("MS_CLIENT_ID", client_id),
            ("MS_CLIENT_SECRET", client_secret),
        ) if not value
    ]
    if missing:
        raise ProviderError(
            "Microsoft Graph email is not fully configured. Missing: "
            + ", ".join(missing)
        )

    response = requests.post(
        f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token",
        data={
            "client_id": client_id,
            "client_secret": client_secret,
            "scope": scope,
            "grant_type": "client_credentials",
        },
        timeout=30,
    )
    if response.status_code >= 400:
        try:
            data = response.json()
            reason = data.get("error_description") or data.get("error") or response.text
        except Exception:
            reason = response.text
        raise ProviderError(
            f"Microsoft Graph token request failed ({response.status_code}): "
            f"{str(reason)[:700]}"
        )

    data = response.json() if response.content else {}
    token = str(data.get("access_token") or "").strip()
    if not token:
        raise ProviderError("Microsoft Graph access token was not returned.")
    return token


def _apply_email_headers(msg, extra_headers=None) -> None:
    """Apply the small allow-list of standards-based marketing email headers."""
    allowed = {"List-Unsubscribe", "List-Unsubscribe-Post"}
    for name, value in dict(extra_headers or {}).items():
        name = str(name or "").strip()
        value = str(value or "").strip()
        if name not in allowed or not value or "\r" in value or "\n" in value:
            continue
        if name in msg:
            del msg[name]
        msg[name] = value


def _build_inline_mime_message(
    sender: str,
    to_email: str,
    subject: str,
    html_body: str,
    *,
    plain_body: str = "",
    inline_media=None,
    extra_headers=None,
) -> MIMEMultipart:
    """Create a CID-related MIME message without attachment filenames.

    V1.3.2 deliberately omits the MIME filename/name parameters for inline
    images. Gmail otherwise surfaces Microsoft Graph fileAttachment.name as
    visible attachment chips even when Graph isInline=true. The images remain
    referenced from the HTML by Content-ID and render inside the email body.
    """
    msg = MIMEMultipart("related")
    alternative = MIMEMultipart("alternative")
    alternative.attach(
        MIMEText(plain_body or html_to_plain_text(html_body), "plain", "utf-8")
    )
    alternative.attach(MIMEText(html_body or "", "html", "utf-8"))
    msg.attach(alternative)

    for index, item in enumerate(_normalize_inline_media(inline_media)):
        main_type, _, sub_type = str(
            item.get("mime_type") or "application/octet-stream"
        ).partition("/")
        part = MIMEBase(main_type or "application", sub_type or "octet-stream")
        part.set_payload(item.get("bytes") or b"")
        encoders.encode_base64(part)
        content_id = item.get("content_id") or f"broadcast_media_{index + 1}"
        part.add_header("Content-ID", f"<{content_id}>")
        part.add_header("Content-Disposition", "inline")
        # Gmail recognises this header for CID body images. It is not a filename.
        part.add_header("X-Attachment-Id", str(content_id))
        msg.attach(part)

    msg["From"] = sender
    msg["To"] = str(to_email or "").strip()
    msg["Subject"] = subject or "Cadupius Healthassist"
    _apply_email_headers(msg, extra_headers)
    return msg


def _send_email_graph(
    to_email: str,
    subject: str,
    html_body: str,
    *,
    plain_body: str = "",
    inline_media=None,
    extra_headers=None,
) -> str:
    sender = _default_sender_email()
    if not sender:
        raise ProviderError(
            "Microsoft Graph sender email is not configured. "
            "Set MS_GRAPH_USER_EMAIL or GRAPH_SENDER_EMAIL."
        )

    token = _graph_access_token()
    inline_items = _normalize_inline_media(inline_media)
    extra_headers = dict(extra_headers or {})

    if inline_items or extra_headers:
        # Graph JSON fileAttachment requires a visible `name`. Gmail can expose
        # that name as an attachment chip even for isInline=true. Sending the
        # same CID images as raw MIME lets us keep Content-Disposition:inline
        # without a filename, so they remain body images rather than downloads.
        mime_message = _build_inline_mime_message(
            sender,
            to_email,
            subject,
            html_body,
            plain_body=plain_body,
            inline_media=inline_items,
            extra_headers=extra_headers,
        )
        mime_bytes = mime_message.as_bytes(policy=policy.SMTP)
        response = requests.post(
            f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "text/plain",
            },
            data=base64.b64encode(mime_bytes).decode("ascii"),
            timeout=60,
        )
    else:
        payload = {
            "message": {
                "subject": subject or "Cadupius Healthassist",
                "body": {"contentType": "HTML", "content": html_body or ""},
                "toRecipients": [
                    {"emailAddress": {"address": str(to_email or "").strip()}}
                ],
            },
            "saveToSentItems": True,
        }
        response = requests.post(
            f"https://graph.microsoft.com/v1.0/users/{sender}/sendMail",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=60,
        )

    if response.status_code not in (200, 201, 202):
        try:
            data = response.json()
            reason = (
                (data.get("error") or {}).get("message")
                if isinstance(data.get("error"), dict)
                else data.get("error")
            ) or response.text
        except Exception:
            reason = response.text
        raise ProviderError(
            f"Microsoft Graph sendMail failed ({response.status_code}): "
            f"{str(reason)[:700]}"
        )

    return "GRAPH_ACCEPTED"


def send_email(
    to_email: str,
    subject: str,
    html_body: str,
    *,
    plain_body: str = "",
    inline_media=None,
    extra_headers=None,
) -> str:
    """
    Broadcast email follows TERIQEN's configured MAIL_BACKEND.

    Supported backends:
      - GRAPH: Microsoft Graph / Office 365
      - SMTP: existing SMTP/MAIL_* settings

    This avoids changing the application's global mail backend just for
    Broadcast Management.
    """
    backend = _mail_backend()

    if backend == "GRAPH":
        return _send_email_graph(
            to_email, subject, html_body, plain_body=plain_body,
            inline_media=inline_media, extra_headers=extra_headers
        )

    if backend != "SMTP":
        raise ProviderError(
            f"Unsupported MAIL_BACKEND={backend}. Broadcast Email supports GRAPH or SMTP."
        )

    host = current_app.config.get("SMTP_HOST") or current_app.config.get("MAIL_SERVER")
    port = int(current_app.config.get("SMTP_PORT") or current_app.config.get("MAIL_PORT") or 587)
    username = current_app.config.get("SMTP_USERNAME") or current_app.config.get("MAIL_USERNAME")
    password = current_app.config.get("SMTP_PASSWORD") or current_app.config.get("MAIL_PASSWORD")
    sender = current_app.config.get("EMAIL_FROM") or current_app.config.get("MAIL_DEFAULT_SENDER") or username

    if not host or not sender:
        raise ProviderError("Email provider is not configured.")

    inline_items = _normalize_inline_media(inline_media)
    if inline_items:
        msg = MIMEMultipart("related")
        alternative = MIMEMultipart("alternative")
        alternative.attach(
            MIMEText(plain_body or html_to_plain_text(html_body), "plain", "utf-8")
        )
        alternative.attach(MIMEText(html_body or "", "html", "utf-8"))
        msg.attach(alternative)

        for index, item in enumerate(inline_items):
            main_type, _, sub_type = str(item.get("mime_type") or "application/octet-stream").partition("/")
            attachment = MIMEBase(main_type or "application", sub_type or "octet-stream")
            attachment.set_payload(item.get("bytes") or b"")
            encoders.encode_base64(attachment)
            content_id = item.get("content_id") or f"broadcast_media_{index + 1}"
            attachment.add_header("Content-ID", f'<{content_id}>')
            attachment.add_header("Content-Disposition", "inline")
            attachment.add_header("X-Attachment-Id", str(content_id))
            msg.attach(attachment)
    else:
        msg = MIMEMultipart("alternative")
        msg.attach(
            MIMEText(plain_body or html_to_plain_text(html_body), "plain", "utf-8")
        )
        msg.attach(MIMEText(html_body or "", "html", "utf-8"))

    msg["From"] = sender
    msg["To"] = to_email
    msg["Subject"] = subject or "Cadupius Healthassist"
    _apply_email_headers(msg, extra_headers)

    use_ssl = bool(current_app.config.get("MAIL_USE_SSL", False))
    use_tls = bool(current_app.config.get("MAIL_USE_TLS", True))

    smtp_cls = smtplib.SMTP_SSL if use_ssl else smtplib.SMTP
    with smtp_cls(host, port, timeout=30) as server:
        if not use_ssl and use_tls:
            server.starttls()
        if username:
            server.login(username, password or "")
        server.sendmail(sender, [to_email], msg.as_string())

    return "SMTP_ACCEPTED"


def send_telegram(chat_id: str, text: str, *, media_url: str | None = None,
                  media_kind: str | None = None) -> str:
    token = str(current_app.config.get("TELEGRAM_BOT_TOKEN") or "").strip()
    if not token:
        raise ProviderError("Telegram provider is not configured.")

    base = f"https://api.telegram.org/bot{token}"
    if media_url and str(media_url).startswith("https://") and media_kind in {"image", "video", "gif"}:
        if media_kind == "image":
            method, media_key = "sendPhoto", "photo"
        elif media_kind == "video":
            method, media_key = "sendVideo", "video"
        else:
            method, media_key = "sendAnimation", "animation"
        payload = {"chat_id": chat_id, media_key: media_url, "caption": (text or "")[:1024]}
    else:
        method = "sendMessage"
        payload = {"chat_id": chat_id, "text": text or "", "disable_web_page_preview": False}

    response = requests.post(f"{base}/{method}", json=payload, timeout=30)
    if response.status_code >= 400:
        raise ProviderError(f"Telegram API {response.status_code}: {response.text[:500]}")
    data = response.json()
    result = data.get("result") or {}
    return str(result.get("message_id") or "TELEGRAM_ACCEPTED")


def send_sms(to_mobile: str, text: str) -> str:
    """
    Generic SMS HTTP adapter.

    Configure:
      SMS_API_URL
      SMS_API_TOKEN
      SMS_SENDER_ID

    The endpoint is called with JSON:
      {"to": "...", "message": "...", "sender_id": "..."}

    This deliberately avoids hard-coding a vendor. For Indian production SMS,
    map the provider-specific DLT template/entity fields in this function.
    """
    url = str(current_app.config.get("SMS_API_URL") or "").strip()
    token = str(current_app.config.get("SMS_API_TOKEN") or "").strip()
    sender_id = str(current_app.config.get("SMS_SENDER_ID") or "").strip()
    if not url or not token:
        raise ProviderError("SMS provider is not configured.")

    response = requests.post(
        url,
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json"},
        json={"to": to_mobile, "message": text or "", "sender_id": sender_id},
        timeout=30,
    )
    if response.status_code >= 400:
        raise ProviderError(f"SMS API {response.status_code}: {response.text[:500]}")
    try:
        data = response.json()
    except Exception:
        data = {}
    return str(data.get("message_id") or data.get("id") or "SMS_ACCEPTED")


def send_channel(channel: str, recipient: dict, campaign: dict) -> str:
    channel = str(channel or "").upper()
    text = str(campaign.get("message_text") or "")
    media_url = campaign.get("media_url")
    media_kind = str(campaign.get("media_type") or "").lower() or None

    if channel == "WHATSAPP":
        return send_whatsapp(recipient.get("mobile"), text, media_url=media_url, media_kind=media_kind)
    if channel == "EMAIL":
        subject, body_html, plain_body, inline_media = _prepare_email_content(
            recipient, campaign
        )
        return send_email(
            recipient.get("email"),
            subject,
            body_html,
            plain_body=plain_body,
            inline_media=inline_media,
            extra_headers=_email_unsubscribe_headers(campaign, recipient),
        )
    if channel == "TELEGRAM":
        return send_telegram(recipient.get("telegram_chat_id") or recipient.get("mobile"), text, media_url=media_url, media_kind=media_kind)
    if channel == "SMS":
        return send_sms(recipient.get("mobile"), text)

    raise ProviderError(f"Unsupported channel: {channel}")
