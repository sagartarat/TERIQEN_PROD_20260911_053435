from __future__ import annotations

import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any
from uuid import uuid4

import requests
from flask import current_app, session

from app.common.db import get_db_connection
from app.broadcast_management.rich_text import html_to_plain_text, sanitize_email_html


VAR_RE = re.compile(r"\{\{\s*([A-Za-z0-9_]+)\s*\}\}")
POSITIONAL_VAR_RE = re.compile(r"^\d+$")
NAMED_VAR_RE = re.compile(r"^[a-z][a-z0-9_]{0,59}$")
ALLOWED_MAPPING_SOURCES = {
    "recipient_name",
    "first_name",
    "reference_no",
    "country_name",
    "language_code",
    "recipient_tag",
    "campaign_name",
    "website_url",
    "contact_number",
}

# TERIQEN-only metadata stored inside local components_json.
# This field is never submitted to Meta; it lets the Message Templates
# screen show the locally uploaded sample image after Meta synchronization.
_LOCAL_HEADER_PREVIEW_KEY = "_local_preview_url"
_LEGACY_HEADER_PREVIEW_KEY = "_healtheasy_preview_url"



def _user_code() -> str:
    return str(session.get("user_code") or session.get("username") or "SYSTEM")[:50]


def _json_load(value: Any, default):
    if value in (None, ""):
        return default
    if isinstance(value, (dict, list)):
        return value
    try:
        return json.loads(value)
    except Exception:
        return default


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _component(components: list[dict], kind: str) -> dict:
    kind = kind.upper()
    for item in components or []:
        if str(item.get("type") or "").upper() == kind:
            return item
    return {}


def _header_type(header: dict) -> str:
    fmt = str(header.get("format") or "").upper()
    if fmt:
        return fmt
    if header.get("text"):
        return "TEXT"
    return "NONE"

def _header_preview_url(components: list[dict]) -> str:
    header = _component(components, "HEADER")
    return str(header.get(_LOCAL_HEADER_PREVIEW_KEY) or header.get(_LEGACY_HEADER_PREVIEW_KEY) or "").strip()


def _with_header_preview_url(components: list[dict], preview_url: str) -> list[dict]:
    """Return a detached local component list carrying a TERIQEN preview URL."""
    detached = _json_load(_json_dump(components or []), [])
    preview_url = str(preview_url or "").strip()
    if preview_url:
        header = _component(detached, "HEADER")
        if _header_type(header) == "IMAGE":
            header[_LOCAL_HEADER_PREVIEW_KEY] = preview_url
    return detached


def _store_header_preview_image(data: bytes, mime_type: str) -> str:
    """Persist a local copy of an IMAGE template sample for TERIQEN preview only."""
    mime = str(mime_type or "").split(";", 1)[0].strip().lower()
    ext = ".jpg" if mime == "image/jpeg" else ".png" if mime == "image/png" else ""
    if not ext:
        return ""
    media_dir = Path(current_app.instance_path) / "broadcast_media"
    media_dir.mkdir(parents=True, exist_ok=True)
    stored_name = f"{uuid4().hex}{ext}"
    (media_dir / stored_name).write_bytes(data)
    return f"/broadcast-management/api/media/{stored_name}"


def _extract_variable_keys(components: list[dict]) -> list[str]:
    body = _component(components, "BODY")
    found = [str(x) for x in VAR_RE.findall(str(body.get("text") or ""))]
    if not found:
        return []
    if all(POSITIONAL_VAR_RE.fullmatch(x) for x in found):
        return [str(x) for x in sorted({int(v) for v in found})]
    seen: set[str] = set()
    ordered: list[str] = []
    for key in found:
        if key not in seen:
            seen.add(key)
            ordered.append(key)
    return ordered


def _parameter_format_from_components(components: list[dict]) -> str:
    keys = _extract_variable_keys(components)
    if keys and all(POSITIONAL_VAR_RE.fullmatch(k) for k in keys):
        return "POSITIONAL"
    if keys:
        return "NAMED"
    return "POSITIONAL"


def _template_to_public(row: dict) -> dict:
    components = _json_load(row.get("components_json"), [])
    buttons = _json_load(row.get("buttons_json"), [])
    mapping = _json_load(row.get("variable_mapping_json"), {})
    variables = _extract_variable_keys(components)
    missing = [key for key in variables if not mapping.get(key)]
    unsupported: list[str] = []
    header = _component(components, "HEADER")
    if VAR_RE.search(str(header.get("text") or "")):
        unsupported.append("Text header variables are not mapped by V1.1.")
    for button in buttons:
        if VAR_RE.search(str(button.get("url") or "")):
            unsupported.append("Dynamic URL button variables are not mapped by V1.1.")
            break
    return {
        "id": int(row["id"]),
        "channel": row.get("channel_code"),
        "template_name": row.get("template_name"),
        "provider_template_id": row.get("provider_template_id"),
        "provider_template_name": row.get("provider_template_name"),
        "category": row.get("category"),
        "language_code": row.get("language_code"),
        "status": row.get("status"),
        "header_type": row.get("header_type") or "NONE",
        "header_preview_url": _header_preview_url(components),
        "header_text": row.get("header_text") or "",
        "body_text": row.get("body_text") or "",
        "footer_text": row.get("footer_text") or "",
        "buttons": buttons,
        "components": components,
        "variable_keys": variables,
        "parameter_format": _parameter_format_from_components(components),
        "variable_mapping": mapping,
        "mapping_complete": not missing and not unsupported,
        "missing_mapping": missing,
        "unsupported_reasons": unsupported,
        "email_subject": row.get("email_subject") or "",
        "body_html": sanitize_email_html(row.get("body_html") or ""),
        "active_flag": bool(row.get("active_flag", 1)),
        "last_synced_on": row.get("last_synced_on"),
        "updated_on": row.get("updated_on"),
    }


def list_templates(*, channel: str | None = None, status: str | None = None) -> list[dict]:
    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        clauses = ["active_flag=1"]
        params: list[Any] = []
        if channel:
            clauses.append("channel_code=%s")
            params.append(str(channel).upper())
        if status:
            clauses.append("status=%s")
            params.append(str(status).upper())
        cursor.execute(
            f"""
            SELECT * FROM broadcast_template_master
            WHERE {' AND '.join(clauses)}
            ORDER BY COALESCE(created_on, updated_on, last_synced_on) DESC, id DESC
            """,
            tuple(params),
        )
        return [_template_to_public(x) for x in (cursor.fetchall() or [])]
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_template(template_id: int) -> dict | None:
    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT * FROM broadcast_template_master WHERE id=%s AND active_flag=1",
            (int(template_id),),
        )
        row = cursor.fetchone()
        return _template_to_public(row) if row else None
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def save_variable_mapping(template_id: int, mapping: dict) -> dict:
    template = get_template(template_id)
    if not template:
        raise ValueError("Template not found.")
    allowed_keys = set(template["variable_keys"])
    cleaned: dict[str, str] = {}
    for raw_key, raw_source in (mapping or {}).items():
        key = str(raw_key).strip()
        source = str(raw_source or "").strip()
        if key not in allowed_keys:
            continue
        if source and source not in ALLOWED_MAPPING_SOURCES:
            raise ValueError(f"Unsupported source field: {source}")
        if source:
            cleaned[key] = source

    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE broadcast_template_master
            SET variable_mapping_json=%s, updated_by=%s, updated_on=NOW()
            WHERE id=%s
            """,
            (_json_dump(cleaned), _user_code(), int(template_id)),
        )
        conn.commit()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return get_template(template_id) or {}


def save_email_template(payload: dict, *, template_id: int | None = None) -> dict:
    name = str(payload.get("template_name") or "").strip()[:200]
    subject = str(payload.get("email_subject") or "").strip()[:300]
    body_html = sanitize_email_html(str(payload.get("body_html") or "").strip())
    supplied_text = str(payload.get("body_text") or "").strip()
    body_text = html_to_plain_text(body_html) if body_html else supplied_text
    if not name:
        raise ValueError("Template name is required.")
    if not subject:
        raise ValueError("Email subject is required.")
    if not body_html and not body_text:
        raise ValueError("Email content is required.")

    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        if template_id:
            cursor.execute(
                """
                UPDATE broadcast_template_master
                SET template_name=%s, email_subject=%s, body_html=%s, body_text=%s,
                    status='ACTIVE', updated_by=%s, updated_on=NOW()
                WHERE id=%s AND channel_code='EMAIL'
                """,
                (name, subject, body_html, body_text, _user_code(), int(template_id)),
            )
            if cursor.rowcount <= 0:
                raise ValueError("Email template not found.")
            saved_id = int(template_id)
        else:
            cursor.execute(
                """
                INSERT INTO broadcast_template_master
                (channel_code, template_name, category, language_code, status,
                 header_type, body_text, email_subject, body_html, active_flag,
                 created_by, created_on, updated_by, updated_on)
                VALUES ('EMAIL',%s,'EMAIL','en','ACTIVE','NONE',%s,%s,%s,1,%s,NOW(),%s,NOW())
                """,
                (name, body_text, subject, body_html, _user_code(), _user_code()),
            )
            saved_id = int(cursor.lastrowid)
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()
    return get_template(saved_id) or {}



def delete_whatsapp_template(template_id: int) -> dict:
    """Delete one WhatsApp template from Meta, then archive its TERIQEN row.

    Meta deletion is completed first so TERIQEN does not silently hide a
    template that still exists in the configured WhatsApp Business Account.
    """
    conn = cursor = None
    row = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, provider_template_id, provider_template_name, template_name,
                   language_code
            FROM broadcast_template_master
            WHERE id=%s AND channel_code='WHATSAPP' AND active_flag=1
            """,
            (int(template_id),),
        )
        row = cursor.fetchone()
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    if not row:
        raise ValueError("WhatsApp template not found or already deleted.")

    provider_name = str(
        row.get("provider_template_name") or row.get("template_name") or ""
    ).strip()
    provider_id = str(row.get("provider_template_id") or "").strip()
    language = str(row.get("language_code") or "").strip()

    if not provider_name:
        raise RuntimeError("The WhatsApp template does not have a Meta template name.")

    token = str(current_app.config.get("WHATSAPP_ACCESS_TOKEN") or "").strip()
    waba_id = str(current_app.config.get("WHATSAPP_BUSINESS_ACCOUNT_ID") or "").strip()
    version = str(
        current_app.config.get("WHATSAPP_GRAPH_VERSION")
        or current_app.config.get("WHATSAPP_API_VERSION")
        or "v25.0"
    ).strip()
    if not token or not waba_id:
        raise RuntimeError(
            "WHATSAPP_ACCESS_TOKEN and WHATSAPP_BUSINESS_ACCOUNT_ID are required "
            "to delete a Meta WhatsApp template."
        )

    auth_headers = {"Authorization": f"Bearer {token}"}
    base_url = f"https://graph.facebook.com/{version}/{waba_id}/message_templates"

    # Prefer delete-by-ID because a template name can have multiple language variants.
    # If an older local row is missing its provider id, resolve the exact language
    # variant from Meta first instead of issuing a broad delete-by-name request.
    if not provider_id:
        lookup = requests.get(
            base_url,
            headers=auth_headers,
            params={
                "name": provider_name,
                "fields": "id,name,language",
                "limit": 100,
            },
            timeout=30,
        )
        if lookup.status_code >= 400:
            raise _meta_error(lookup, "Meta template lookup before deletion failed")
        candidates = (lookup.json() or {}).get("data") or []
        matches = [
            item for item in candidates
            if str(item.get("name") or "").strip() == provider_name
            and str(item.get("language") or "").strip() == language
            and str(item.get("id") or "").strip()
        ]
        if len(matches) != 1:
            raise RuntimeError(
                "Unable to identify one exact Meta template for deletion. "
                "Click Sync from Meta and try again."
            )
        provider_id = str(matches[0].get("id") or "").strip()

    response = requests.delete(
        base_url,
        headers=auth_headers,
        params={"hsm_id": provider_id, "name": provider_name},
        timeout=30,
    )
    if response.status_code >= 400:
        raise _meta_error(response, "Meta template deletion failed")

    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE broadcast_template_master
            SET active_flag=0, updated_by=%s, updated_on=NOW()
            WHERE id=%s AND channel_code='WHATSAPP' AND active_flag=1
            """,
            (_user_code(), int(template_id)),
        )
        if cursor.rowcount <= 0:
            raise ValueError("WhatsApp template not found or already deleted.")
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    return {
        "template_id": int(template_id),
        "provider_template_id": provider_id,
        "provider_template_name": provider_name,
        "language_code": language,
        "provider_deleted": True,
    }


def archive_email_template(template_id: int) -> None:
    """Soft-delete an Email template while preserving audit/history references."""
    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            UPDATE broadcast_template_master
            SET active_flag=0, updated_by=%s, updated_on=NOW()
            WHERE id=%s AND channel_code='EMAIL' AND active_flag=1
            """,
            (_user_code(), int(template_id)),
        )
        if cursor.rowcount <= 0:
            raise ValueError("Email template not found or already deleted.")
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _meta_error(response: requests.Response, prefix: str) -> RuntimeError:
    try:
        provider = response.json() if response.content else {}
    except Exception:
        provider = {}
    message = provider.get("error", {}).get("message") if isinstance(provider, dict) else ""
    return RuntimeError(
        f"{prefix} ({response.status_code}): {message or response.text[:500]}"
    )


def _validate_header_media_bytes(data: bytes, header_type: str, filename: str, mimetype: str) -> tuple[bytes, str, str, str]:
    """Validate/normalise Meta template sample media.

    Returns (bytes, provider_header_type, mime_type, filename).
    GIF is a TERIQEN convenience option and is converted to MP4 when ffmpeg is available,
    because the normal Cloud API media-template send path already treats animated media as VIDEO.
    """
    requested = str(header_type or "").upper()
    original_name = Path(filename or "sample").name[:180] or "sample"
    declared = str(mimetype or "").split(";", 1)[0].strip().lower()

    if not data:
        raise ValueError("A sample media file is required for this header type.")
    if len(data) > 16 * 1024 * 1024:
        raise ValueError("Template sample media cannot exceed 16 MB in TERIQEN.")

    is_jpeg = data[:3] == b"\xff\xd8\xff"
    is_png = data[:8] == b"\x89PNG\r\n\x1a\n"
    is_gif = data[:6] in {b"GIF87a", b"GIF89a"}
    is_pdf = data[:5] == b"%PDF-"
    is_mp4 = len(data) >= 12 and data[4:8] == b"ftyp"

    if requested == "IMAGE":
        if is_jpeg:
            return data, "IMAGE", "image/jpeg", original_name.rsplit('.', 1)[0] + '.jpg'
        if is_png:
            return data, "IMAGE", "image/png", original_name.rsplit('.', 1)[0] + '.png'
        raise ValueError("Image template headers must use a real JPEG or PNG file.")

    if requested == "VIDEO":
        if not is_mp4:
            raise ValueError("Video template headers must use a real MP4 file.")
        return data, "VIDEO", "video/mp4", original_name.rsplit('.', 1)[0] + '.mp4'

    if requested == "DOCUMENT":
        if not is_pdf:
            raise ValueError("Document template headers must use a real PDF file.")
        return data, "DOCUMENT", "application/pdf", original_name.rsplit('.', 1)[0] + '.pdf'

    if requested == "GIF":
        if not is_gif:
            raise ValueError("GIF header selection requires a real animated GIF file.")
        ffmpeg = shutil.which("ffmpeg")
        if not ffmpeg:
            raise ValueError(
                "GIF was selected, but ffmpeg is not installed on this TERIQEN server. "
                "Install ffmpeg or upload the animation as MP4 using the Video header option."
            )
        with tempfile.TemporaryDirectory(prefix="teriqen_wa_gif_") as td:
            src = Path(td) / "input.gif"
            dst = Path(td) / "output.mp4"
            src.write_bytes(data)
            proc = subprocess.run(
                [
                    ffmpeg, "-y", "-i", str(src),
                    "-movflags", "+faststart", "-pix_fmt", "yuv420p",
                    "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    str(dst),
                ],
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=45,
                check=False,
            )
            if proc.returncode != 0 or not dst.exists():
                detail = proc.stderr.decode("utf-8", "ignore")[-500:]
                raise ValueError(f"GIF to MP4 conversion failed. {detail}".strip())
            converted = dst.read_bytes()
        if len(converted) > 16 * 1024 * 1024:
            raise ValueError("The converted GIF/MP4 exceeds the 16 MB TERIQEN template-media limit.")
        return converted, "VIDEO", "video/mp4", original_name.rsplit('.', 1)[0] + '.mp4'

    raise ValueError("Unsupported media header type.")


def _upload_meta_header_handle(
    *,
    token: str,
    version: str,
    app_id: str,
    data: bytes,
    mime_type: str,
    filename: str,
) -> str:
    """Upload sample header media through Meta's Resumable Upload API and return header_handle."""
    app_id = str(app_id or "").strip()
    if not app_id:
        raise RuntimeError("WHATSAPP_APP_ID is required for WhatsApp template media uploads.")
    if not app_id.isdigit():
        raise RuntimeError("WHATSAPP_APP_ID must be the numeric Meta Developer App ID.")

    headers = {"Authorization": f"Bearer {token}"}
    create_url = f"https://graph.facebook.com/{version}/{app_id}/uploads"
    create_response = requests.post(
        create_url,
        headers=headers,
        params={
            "file_length": str(len(data)),
            "file_type": mime_type,
            "file_name": filename,
        },
        timeout=30,
    )
    if create_response.status_code >= 400:
        raise _meta_error(create_response, "Meta media upload session creation failed")
    try:
        upload_id = str((create_response.json() or {}).get("id") or "").strip()
    except Exception:
        upload_id = ""
    if not upload_id:
        raise RuntimeError("Meta media upload session did not return an upload ID.")

    upload_url = f"https://graph.facebook.com/{version}/{upload_id}"
    upload_response = requests.post(
        upload_url,
        headers={
            "Authorization": f"Bearer {token}",
            "file_offset": "0",
            "Content-Type": mime_type,
        },
        data=data,
        timeout=60,
    )
    if upload_response.status_code >= 400:
        raise _meta_error(upload_response, "Meta media sample upload failed")
    try:
        handle = str((upload_response.json() or {}).get("h") or "").strip()
    except Exception:
        handle = ""
    if not handle:
        raise RuntimeError("Meta media upload did not return a header handle.")
    return handle


def create_whatsapp_template(payload: dict, *, media_file=None) -> dict:
    token = str(current_app.config.get("WHATSAPP_ACCESS_TOKEN") or "").strip()
    waba_id = str(current_app.config.get("WHATSAPP_BUSINESS_ACCOUNT_ID") or "").strip()
    app_id = str(current_app.config.get("WHATSAPP_APP_ID") or "").strip()
    version = str(
        current_app.config.get("WHATSAPP_GRAPH_VERSION")
        or current_app.config.get("WHATSAPP_API_VERSION")
        or "v25.0"
    ).strip()
    if not token or not waba_id:
        raise RuntimeError("WHATSAPP_ACCESS_TOKEN and WHATSAPP_BUSINESS_ACCOUNT_ID are required.")

    name = str(payload.get("template_name") or "").strip().lower()
    category = str(payload.get("category") or "MARKETING").strip().upper()
    language = str(payload.get("language_code") or "").strip()
    requested_header_type = str(payload.get("header_type") or "NONE").strip().upper()
    parameter_format = str(payload.get("parameter_format") or "NAMED").strip().upper()
    header_type = requested_header_type
    header_text = str(payload.get("header_text") or "").strip()
    body_text = str(payload.get("body_text") or "").strip()
    footer_text = str(payload.get("footer_text") or "").strip()
    website_text = str(payload.get("website_button_text") or "").strip()
    website_url = str(payload.get("website_url") or "").strip()
    phone_text = str(payload.get("phone_button_text") or "").strip()
    phone_number = str(payload.get("phone_number") or "").strip()

    raw_examples = payload.get("body_examples") or []
    raw_mapping = payload.get("body_mapping") or {}
    if isinstance(raw_examples, str):
        try:
            raw_examples = json.loads(raw_examples)
        except Exception:
            raw_examples = []
    body_examples = [str(x or "").strip()[:200] for x in raw_examples] if isinstance(raw_examples, list) else []
    if isinstance(raw_mapping, str):
        try:
            raw_mapping = json.loads(raw_mapping)
        except Exception:
            raw_mapping = {}
    body_mapping = {}
    if isinstance(raw_mapping, dict):
        for raw_key, raw_source in raw_mapping.items():
            key = str(raw_key or "").strip()
            source = str(raw_source or "").strip()
            if key and source in ALLOWED_MAPPING_SOURCES:
                body_mapping[key] = source

    if not re.fullmatch(r"[a-z0-9_]{1,512}", name):
        raise ValueError("Template name must contain only lowercase letters, numbers and underscores.")
    if category not in {"MARKETING", "UTILITY"}:
        raise ValueError("This TERIQEN builder supports MARKETING or UTILITY templates.")
    if not re.fullmatch(r"[A-Za-z_]{2,10}", language):
        raise ValueError("A valid Meta language code is required.")
    if requested_header_type not in {"NONE", "TEXT", "IMAGE", "VIDEO", "GIF", "DOCUMENT"}:
        raise ValueError("Header type must be NONE, TEXT, IMAGE, VIDEO, GIF or DOCUMENT.")
    if parameter_format != "NAMED":
        raise ValueError("New TERIQEN WhatsApp templates support named placeholders only.")

    raw = b""
    media_mime = ""
    if requested_header_type == "TEXT":
        if not header_text:
            raise ValueError("Header text is required when Header Type is TEXT.")
        if len(header_text) > 60:
            raise ValueError("Header text cannot exceed 60 characters.")
        if "{{" in header_text or "}}" in header_text:
            raise ValueError(
                "Header variables are not enabled because the current TERIQEN broadcast sender maps BODY variables only."
            )
    elif requested_header_type in {"IMAGE", "VIDEO", "GIF", "DOCUMENT"}:
        if media_file is None or not getattr(media_file, "filename", ""):
            raise ValueError("Choose a sample media file before submitting this media template to Meta.")
        raw = media_file.read()
        raw, header_type, media_mime, media_name = _validate_header_media_bytes(
            raw,
            requested_header_type,
            getattr(media_file, "filename", "sample"),
            getattr(media_file, "mimetype", ""),
        )
        header_handle = _upload_meta_header_handle(
            token=token,
            version=version,
            app_id=app_id,
            data=raw,
            mime_type=media_mime,
            filename=media_name,
        )
    else:
        header_handle = ""

    if not body_text:
        raise ValueError("Template body is required.")
    if len(body_text) > 1024:
        raise ValueError("Template body cannot exceed 1024 characters.")
    if len(footer_text) > 60:
        raise ValueError("Footer cannot exceed 60 characters.")

    all_placeholders = [x.strip() for x in re.findall(r"\{\{\s*([^{}]+?)\s*\}\}", body_text)]
    if any(not NAMED_VAR_RE.fullmatch(x) for x in all_placeholders):
        raise ValueError("Use named placeholders only, for example {{recipient_name}}. Numbered placeholders are not supported for new templates.")
    seen: set[str] = set()
    keys = []
    for key in all_placeholders:
        if key not in seen:
            seen.add(key)
            keys.append(key)

    if keys:
        if len(body_examples) != len(keys) or any(not x for x in body_examples):
            raise ValueError("Provide one non-empty sample value for every BODY variable before Meta submission.")
        missing_mapping = [key for key in keys if not body_mapping.get(key)]
        if missing_mapping:
            raise ValueError("Select a TERIQEN field for every BODY placeholder before Meta submission.")

    if website_url:
        if not re.match(r"^https://", website_url, re.IGNORECASE):
            raise ValueError("Website URL must start with https://")
        if not website_text:
            website_text = "Visit Website"
        if len(website_text) > 25:
            raise ValueError("Website button text cannot exceed 25 characters.")
    if phone_number:
        compact_phone = re.sub(r"[\s()-]", "", phone_number)
        if not re.fullmatch(r"\+\d{7,15}", compact_phone):
            raise ValueError("Call number must be in international format, for example +919876543210.")
        phone_number = compact_phone
        if not phone_text:
            phone_text = "Call Us"
        if len(phone_text) > 25:
            raise ValueError("Call button text cannot exceed 25 characters.")

    components: list[dict] = []
    if header_type == "TEXT":
        components.append({"type": "HEADER", "format": "TEXT", "text": header_text})
    elif header_type in {"IMAGE", "VIDEO", "DOCUMENT"}:
        components.append({
            "type": "HEADER",
            "format": header_type,
            "example": {"header_handle": [header_handle]},
        })

    body_component: dict[str, Any] = {"type": "BODY", "text": body_text}
    if keys:
        body_component["example"] = {
            "body_text_named_params": [
                {"param_name": key, "example": body_examples[idx]}
                for idx, key in enumerate(keys)
            ]
        }
    components.append(body_component)

    if footer_text:
        components.append({"type": "FOOTER", "text": footer_text})

    buttons: list[dict] = []
    if website_url:
        buttons.append({"type": "URL", "text": website_text, "url": website_url})
    if phone_number:
        buttons.append({"type": "PHONE_NUMBER", "text": phone_text, "phone_number": phone_number})
    if buttons:
        components.append({"type": "BUTTONS", "buttons": buttons})

    provider_payload = {
        "name": name,
        "language": language,
        "category": category,
        "components": components,
    }
    if keys:
        provider_payload["parameter_format"] = "NAMED"

    url = f"https://graph.facebook.com/{version}/{waba_id}/message_templates"
    headers = {"Authorization": f"Bearer {token}", "Content-Type": "application/json"}
    response = requests.post(url, headers=headers, json=provider_payload, timeout=30)
    provider = response.json() if response.content else {}
    if response.status_code >= 400:
        raise _meta_error(response, "Meta template creation failed")

    provider_id = str(provider.get("id") or "")[:100] or None
    provider_status = str(provider.get("status") or "PENDING").upper()[:30]

    header_preview_url = ""
    if header_type == "IMAGE" and raw:
        try:
            header_preview_url = _store_header_preview_image(raw, media_mime)
        except Exception:
            current_app.logger.exception("BROADCAST_TEMPLATE_PREVIEW_IMAGE_SAVE_FAILED")
    local_components = _with_header_preview_url(components, header_preview_url)

    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO broadcast_template_master
            (
                channel_code, template_name, provider_template_id, provider_template_name,
                category, language_code, status, header_type, header_text, body_text,
                footer_text, buttons_json, components_json, raw_provider_json, variable_mapping_json,
                active_flag, last_synced_on, created_by, created_on, updated_by, updated_on
            )
            VALUES
            ('WHATSAPP',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,NOW(),%s,NOW(),%s,NOW())
            ON DUPLICATE KEY UPDATE
                provider_template_id=COALESCE(VALUES(provider_template_id),provider_template_id),
                category=VALUES(category), status=VALUES(status),
                header_type=VALUES(header_type), header_text=VALUES(header_text),
                body_text=VALUES(body_text), footer_text=VALUES(footer_text),
                buttons_json=VALUES(buttons_json), components_json=VALUES(components_json),
                raw_provider_json=VALUES(raw_provider_json), variable_mapping_json=VALUES(variable_mapping_json), active_flag=1,
                updated_by=VALUES(updated_by), updated_on=NOW()
            """,
            (
                name, provider_id, name, category, language, provider_status,
                header_type, header_text, body_text, footer_text,
                _json_dump(buttons), _json_dump(local_components), _json_dump(provider), _json_dump(body_mapping),
                _user_code(), _user_code(),
            ),
        )
        conn.commit()
    except Exception:
        if conn:
            conn.rollback()
        current_app.logger.exception("BROADCAST_TEMPLATE_LOCAL_SAVE_FAILED")
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    sync_warning = ""
    try:
        sync_whatsapp_templates()
    except Exception as exc:
        sync_warning = str(exc)[:500]

    return {
        "provider": provider,
        "template_name": name,
        "language_code": language,
        "header_type": header_type,
        "requested_header_type": requested_header_type,
        "parameter_format": parameter_format,
        "header_preview_url": header_preview_url,
        "sync_warning": sync_warning,
    }

def sync_whatsapp_templates() -> dict:
    token = str(current_app.config.get("WHATSAPP_ACCESS_TOKEN") or "").strip()
    waba_id = str(current_app.config.get("WHATSAPP_BUSINESS_ACCOUNT_ID") or "").strip()
    version = str(
        current_app.config.get("WHATSAPP_GRAPH_VERSION")
        or current_app.config.get("WHATSAPP_API_VERSION")
        or "v25.0"
    ).strip()
    if not token or not waba_id:
        raise RuntimeError("WHATSAPP_ACCESS_TOKEN and WHATSAPP_BUSINESS_ACCOUNT_ID are required.")

    url = f"https://graph.facebook.com/{version}/{waba_id}/message_templates"
    params = {
        "fields": "id,name,status,category,language,components",
        "limit": 100,
    }
    headers = {"Authorization": f"Bearer {token}"}

    provider_rows: list[dict] = []
    pages = 0
    next_url: str | None = url
    next_params: dict | None = params
    while next_url and pages < 10:
        response = requests.get(next_url, headers=headers, params=next_params, timeout=30)
        if response.status_code >= 400:
            raise RuntimeError(f"Meta template sync failed ({response.status_code}): {response.text[:500]}")
        data = response.json() if response.content else {}
        provider_rows.extend(data.get("data") or [])
        next_url = ((data.get("paging") or {}).get("next") or "").strip() or None
        next_params = None
        pages += 1

    conn = cursor = None
    inserted = updated = 0
    sync_log_id = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor()
        cursor.execute(
            """
            INSERT INTO broadcast_template_sync_log
            (channel_code, sync_started_on, sync_status, created_by)
            VALUES ('WHATSAPP',NOW(),'IN_PROGRESS',%s)
            """,
            (_user_code(),),
        )
        sync_log_id = int(cursor.lastrowid)

        for item in provider_rows:
            # Detach provider components before adding TERIQEN-only preview metadata.
            components = _json_load(_json_dump(item.get("components") or []), [])
            header = _component(components, "HEADER")
            body = _component(components, "BODY")
            footer = _component(components, "FOOTER")
            buttons_comp = _component(components, "BUTTONS")
            provider_id = str(item.get("id") or "")[:100] or None
            provider_name = str(item.get("name") or "").strip()[:512]
            language = str(item.get("language") or "").strip()[:30]
            if not provider_name or not language:
                continue

            cursor.execute(
                """
                SELECT id, components_json FROM broadcast_template_master
                WHERE channel_code='WHATSAPP' AND provider_template_name=%s AND language_code=%s
                """,
                (provider_name, language),
            )
            exists = cursor.fetchone()
            if exists and _header_type(header) == "IMAGE":
                previous_components = _json_load(exists.get("components_json"), [])
                previous_preview = _header_preview_url(previous_components)
                if previous_preview:
                    header[_LOCAL_HEADER_PREVIEW_KEY] = previous_preview

            cursor.execute(
                """
                INSERT INTO broadcast_template_master
                (
                    channel_code, template_name, provider_template_id, provider_template_name,
                    category, language_code, status, header_type, header_text, body_text,
                    footer_text, buttons_json, components_json, raw_provider_json,
                    active_flag, last_synced_on, created_by, created_on, updated_by, updated_on
                )
                VALUES
                ('WHATSAPP',%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,1,NOW(),%s,NOW(),%s,NOW())
                ON DUPLICATE KEY UPDATE
                    template_name=VALUES(template_name),
                    provider_template_id=VALUES(provider_template_id),
                    category=VALUES(category), status=VALUES(status),
                    header_type=VALUES(header_type), header_text=VALUES(header_text),
                    body_text=VALUES(body_text), footer_text=VALUES(footer_text),
                    buttons_json=VALUES(buttons_json), components_json=VALUES(components_json),
                    raw_provider_json=VALUES(raw_provider_json), active_flag=1,
                    last_synced_on=NOW(), updated_by=VALUES(updated_by), updated_on=NOW()
                """,
                (
                    provider_name,
                    provider_id,
                    provider_name,
                    str(item.get("category") or "")[:50],
                    language,
                    str(item.get("status") or "UNKNOWN").upper()[:30],
                    _header_type(header),
                    str(header.get("text") or ""),
                    str(body.get("text") or ""),
                    str(footer.get("text") or ""),
                    _json_dump(buttons_comp.get("buttons") or []),
                    _json_dump(components),
                    _json_dump(item),
                    _user_code(),
                    _user_code(),
                ),
            )
            if exists:
                updated += 1
            else:
                inserted += 1

        cursor.execute(
            """
            UPDATE broadcast_template_sync_log
            SET sync_completed_on=NOW(), templates_received=%s, templates_added=%s,
                templates_updated=%s, sync_status='SUCCESS'
            WHERE id=%s
            """,
            (len(provider_rows), inserted, updated, sync_log_id),
        )
        conn.commit()
        return {
            "received": len(provider_rows),
            "added": inserted,
            "updated": updated,
            "pages": pages,
        }
    except Exception as exc:
        if conn:
            conn.rollback()
        # Best-effort failure audit in a fresh connection.
        try:
            fail_conn = get_db_connection()
            fail_cur = fail_conn.cursor()
            fail_cur.execute(
                """
                INSERT INTO broadcast_template_sync_log
                (channel_code, sync_started_on, sync_completed_on, sync_status, error_message, created_by)
                VALUES ('WHATSAPP',NOW(),NOW(),'FAILED',%s,%s)
                """,
                (str(exc)[:1000], _user_code()),
            )
            fail_conn.commit()
            fail_cur.close()
            fail_conn.close()
        except Exception:
            current_app.logger.exception("BROADCAST_TEMPLATE_SYNC_AUDIT_FAILED")
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def resolve_template_value(source: str, recipient: dict, campaign: dict) -> str:
    name = str(recipient.get("name") or recipient.get("receiver_name") or "").strip()
    values = {
        "recipient_name": name,
        "first_name": (name.split()[0] if name else ""),
        "reference_no": str(recipient.get("reference_no") or "").strip(),
        "country_name": str(recipient.get("country") or recipient.get("country_name") or "").strip(),
        "language_code": str(recipient.get("language") or recipient.get("language_code") or "").strip(),
        "recipient_tag": str(recipient.get("tag") or recipient.get("recipient_tag") or "").strip(),
        "campaign_name": str(campaign.get("campaign_name") or "").strip(),
        "website_url": str(campaign.get("website_url") or "").strip(),
        "contact_number": str(campaign.get("contact_number") or "").strip(),
    }
    return values.get(source, "")


def build_whatsapp_template_components(template_snapshot: dict, recipient: dict, campaign: dict) -> list[dict]:
    components = template_snapshot.get("components") or []
    mapping = template_snapshot.get("variable_mapping") or {}
    output: list[dict] = []

    body = _component(components, "BODY")
    body_vars = _extract_variable_keys(components)
    parameter_format = _parameter_format_from_components(components)
    if body_vars:
        params = []
        for key in body_vars:
            source = mapping.get(str(key))
            if not source:
                raise ValueError(f"Template variable {{{{{key}}}}} has no TERIQEN field mapping.")
            value = resolve_template_value(source, recipient, campaign)
            if not value:
                raise ValueError(f"Template variable {{{{{key}}}}} source '{source}' is empty for this recipient.")
            item = {"type": "text", "text": value[:32768]}
            if parameter_format == "NAMED":
                item["parameter_name"] = str(key)
            params.append(item)
        output.append({"type": "body", "parameters": params})

    header = _component(components, "HEADER")
    header_type = _header_type(header)
    if header_type in {"IMAGE", "VIDEO", "DOCUMENT"}:
        media_url = str(campaign.get("media_url") or "").strip()
        if not media_url.startswith("https://"):
            raise ValueError(f"This template requires a public HTTPS {header_type.lower()} URL for its header.")
        media_key = header_type.lower()
        output.append({
            "type": "header",
            "parameters": [{"type": media_key, media_key: {"link": media_url}}],
        })

    return output
