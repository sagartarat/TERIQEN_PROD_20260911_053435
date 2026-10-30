from __future__ import annotations

from pathlib import Path
from uuid import uuid4

from flask import abort, current_app, jsonify, make_response, render_template, request, send_from_directory
from flask_login import login_required
from itsdangerous import BadSignature

from app.broadcast_management import broadcast_management_bp
from app.broadcast_management.service import (
    activate_campaign,
    campaign_status,
    create_campaign,
    dispatch_campaign,
    parse_csv_bytes,
    retry_failed,
    validate_recipients,
)
from app.broadcast_management.unsubscribe_service import (
    is_email_unsubscribed,
    masked_email,
    parse_unsubscribe_token,
    suppress_email,
)
from app.broadcast_management.template_service import (
    ALLOWED_MAPPING_SOURCES,
    archive_email_template,
    create_whatsapp_template,
    delete_whatsapp_template,
    get_template,
    list_templates,
    save_email_template,
    save_variable_mapping,
    sync_whatsapp_templates,
)
from app.common.access_control import enforce_screen_permission
from app.common.db import get_db_connection
from app.extensions import csrf


_BROADCAST_ASSET_DIR = Path(__file__).resolve().parent / "assets"
_BROADCAST_ASSET_FILES = {
    "broadcast_management.css",
    "broadcast_management.js",
    "broadcast_history.js",
    "broadcast_templates.js",
}


_BROADCAST_IMAGE_TYPES = {
    ".jpg": "JPEG",
    ".jpeg": "JPEG",
    ".png": "PNG",
    ".gif": "GIF",
    ".webp": "WEBP",
}
_BROADCAST_IMAGE_MAX_BYTES = 5 * 1024 * 1024
_BROADCAST_IMAGE_MAX_DIMENSION = 6000
_BROADCAST_IMAGE_MAX_PIXELS = 20_000_000

_BROADCAST_DELETE_PROTECTED_STATUSES = {"QUEUED", "IN_PROGRESS", "SCHEDULED", "ACTIVE", "SENDING"}


def _campaign_delivery_summary(campaign_id: int) -> dict:
    """Return stable campaign counts for the Broadcast Centre success dialog."""
    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT id, channel_code, campaign_status, total_count, valid_count, success_count,
                   failed_count, skipped_count
            FROM broadcast_campaign
            WHERE id=%s
            """,
            (campaign_id,),
        )
        row = cursor.fetchone() or {}
        valid = int(row.get("valid_count") or 0)
        success = int(row.get("success_count") or 0)
        failed = int(row.get("failed_count") or 0)
        skipped = int(row.get("skipped_count") or 0)
        pending = max(0, valid - success - failed - skipped)
        return {
            "campaign_id": int(row.get("id") or campaign_id),
            "channel": str(row.get("channel_code") or "").upper(),
            "status": str(row.get("campaign_status") or "").upper(),
            "total": int(row.get("total_count") or 0),
            "valid": valid,
            "success": success,
            "failed": failed,
            "skipped": skipped,
            "pending": pending,
            "complete": pending == 0 and valid > 0,
        }
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _jpeg_dimensions(raw: bytes) -> tuple[int, int] | None:
    if len(raw) < 4 or raw[:3] != b"\xff\xd8\xff":
        return None
    i = 2
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3, 0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB, 0xCD, 0xCE, 0xCF,
    }
    while i + 4 <= len(raw):
        if raw[i] != 0xFF:
            i += 1
            continue
        while i < len(raw) and raw[i] == 0xFF:
            i += 1
        if i >= len(raw):
            break
        marker = raw[i]
        i += 1
        if marker in {0xD8, 0xD9}:
            continue
        if marker == 0xDA:
            break
        if i + 2 > len(raw):
            return None
        seg_len = int.from_bytes(raw[i:i + 2], "big")
        if seg_len < 2 or i + seg_len > len(raw):
            return None
        if marker in sof_markers:
            if seg_len < 7:
                return None
            height = int.from_bytes(raw[i + 3:i + 5], "big")
            width = int.from_bytes(raw[i + 5:i + 7], "big")
            return width, height
        i += seg_len
    return None


def _broadcast_image_info(raw: bytes) -> tuple[str, int, int] | None:
    """Identify supported image bytes and read canvas dimensions without decoding pixels."""
    if len(raw) >= 24 and raw.startswith(b"\x89PNG\r\n\x1a\n") and raw[12:16] == b"IHDR":
        return "PNG", int.from_bytes(raw[16:20], "big"), int.from_bytes(raw[20:24], "big")
    if len(raw) >= 10 and raw[:6] in {b"GIF87a", b"GIF89a"}:
        return "GIF", int.from_bytes(raw[6:8], "little"), int.from_bytes(raw[8:10], "little")
    jpeg = _jpeg_dimensions(raw)
    if jpeg:
        return "JPEG", jpeg[0], jpeg[1]
    if len(raw) >= 30 and raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        chunk = raw[12:16]
        if chunk == b"VP8X" and len(raw) >= 30:
            width = 1 + int.from_bytes(raw[24:27], "little")
            height = 1 + int.from_bytes(raw[27:30], "little")
            return "WEBP", width, height
        if chunk == b"VP8 " and len(raw) >= 30 and raw[23:26] == b"\x9d\x01\x2a":
            width = int.from_bytes(raw[26:28], "little") & 0x3FFF
            height = int.from_bytes(raw[28:30], "little") & 0x3FFF
            return "WEBP", width, height
        if chunk == b"VP8L" and len(raw) >= 25 and raw[20] == 0x2F:
            bits = int.from_bytes(raw[21:25], "little")
            width = 1 + (bits & 0x3FFF)
            height = 1 + ((bits >> 14) & 0x3FFF)
            return "WEBP", width, height
    return None


def _validate_broadcast_image(raw: bytes, ext: str) -> tuple[str, int, int]:
    info = _broadcast_image_info(raw)
    if not info:
        raise ValueError("The selected file is not a valid supported image.")
    actual_format, width, height = info
    expected_format = _BROADCAST_IMAGE_TYPES.get(str(ext or "").lower())
    if not expected_format or actual_format != expected_format:
        raise ValueError("The image file content does not match its filename extension.")
    if width <= 0 or height <= 0:
        raise ValueError("The image has invalid dimensions.")
    if width > _BROADCAST_IMAGE_MAX_DIMENSION or height > _BROADCAST_IMAGE_MAX_DIMENSION:
        raise ValueError(
            f"Image dimensions must not exceed {_BROADCAST_IMAGE_MAX_DIMENSION} x "
            f"{_BROADCAST_IMAGE_MAX_DIMENSION} pixels."
        )
    if width * height > _BROADCAST_IMAGE_MAX_PIXELS:
        raise ValueError("Image pixel count is too large for Broadcast Email.")
    return actual_format, width, height


@broadcast_management_bp.get("/assets/<path:filename>")
@login_required
def broadcast_asset(filename: str):
    # Keep Broadcast Management assets inside the module rather than relying
    # on the application's global /static directory.
    if filename not in _BROADCAST_ASSET_FILES:
        abort(404)
    return send_from_directory(_BROADCAST_ASSET_DIR, filename, max_age=0)


@broadcast_management_bp.before_request
def _broadcast_access():
    path = request.path.rstrip("/")
    if path.startswith("/broadcast-management/assets/"):
        return None
    # Recipient email clients must be able to fetch generic Broadcast marketing
    # images without a TERIQEN login. Only random-name, validated image files
    # are exposed by the dedicated public-media route below.
    if path.startswith("/broadcast-management/public-media/"):
        return None
    # Public capability URL used only for recipient marketing-email opt-out.
    if path == "/broadcast-management/unsubscribe":
        return None
    if "/history" in path:
        action = "view" if request.method == "GET" else "write"
        return enforce_screen_permission("BROADCAST_HISTORY", action)
    if "/templates" in path:
        action = "view" if request.method == "GET" else "write"
        return enforce_screen_permission("BROADCAST_TEMPLATES", action)
    action = "view" if request.method == "GET" else "write"
    return enforce_screen_permission("BROADCAST_CENTRE", action)


@broadcast_management_bp.get("/")
@login_required
def broadcast_centre():
    return render_template("broadcast_centre.html")


def _history_page_args() -> tuple[str, int, int]:
    tab = str(request.args.get("tab") or "history").strip().lower()
    if tab not in {"history", "drafts"}:
        tab = "history"
    try:
        page = max(1, int(request.args.get("page") or 1))
    except (TypeError, ValueError):
        page = 1
    try:
        page_size = int(request.args.get("page_size") or 25)
    except (TypeError, ValueError):
        page_size = 25
    if page_size not in {25, 50, 100}:
        page_size = 25
    return tab, page, page_size


def _page_numbers(page: int, pages: int) -> list[int]:
    if pages <= 7:
        return list(range(1, pages + 1))
    start = max(1, page - 2)
    end = min(pages, page + 2)
    nums = list(range(start, end + 1))
    if 1 not in nums:
        nums.insert(0, 1)
    if pages not in nums:
        nums.append(pages)
    return nums


@broadcast_management_bp.get("/history")
@login_required
def broadcast_history():
    conn = cursor = None
    rows = []
    tab, page, page_size = _history_page_args()
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("SELECT COUNT(*) AS total FROM broadcast_campaign WHERE campaign_status <> 'DRAFT'")
        history_total = int((cursor.fetchone() or {}).get("total") or 0)
        cursor.execute("SELECT COUNT(*) AS total FROM broadcast_campaign WHERE campaign_status = 'DRAFT'")
        draft_total = int((cursor.fetchone() or {}).get("total") or 0)

        total = draft_total if tab == "drafts" else history_total
        pages = max(1, (total + page_size - 1) // page_size)
        if page > pages:
            page = pages
        offset = (page - 1) * page_size

        if tab == "drafts":
            cursor.execute(
                """
                SELECT id, campaign_name, campaign_type, channel_code, campaign_status,
                       total_count, valid_count, created_by, created_on
                FROM broadcast_campaign
                WHERE campaign_status = 'DRAFT'
                ORDER BY id DESC
                LIMIT %s OFFSET %s
                """,
                (page_size, offset),
            )
        else:
            cursor.execute(
                """
                SELECT id, campaign_name, campaign_type, channel_code, campaign_status,
                       total_count, valid_count, success_count, failed_count, skipped_count,
                       template_id, provider_template_name, provider_template_language,
                       created_by, created_on, scheduled_on, started_on, completed_on
                FROM broadcast_campaign
                WHERE campaign_status <> 'DRAFT'
                ORDER BY id DESC
                LIMIT %s OFFSET %s
                """,
                (page_size, offset),
            )
        rows = cursor.fetchall() or []
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()

    start_row = ((page - 1) * page_size + 1) if total else 0
    end_row = min(page * page_size, total)
    return render_template(
        "broadcast_history.html",
        rows=rows,
        active_tab=tab,
        history_total=history_total,
        draft_total=draft_total,
        total=total,
        page=page,
        pages=pages,
        page_size=page_size,
        page_numbers=_page_numbers(page, pages),
        start_row=start_row,
        end_row=end_row,
    )


@broadcast_management_bp.get("/api/history/<int:campaign_id>")
@login_required
def api_history_detail(campaign_id: int):
    conn = campaign_cursor = recipient_cursor = count_cursor = None
    try:
        try:
            page = max(1, int(request.args.get("page") or 1))
        except (TypeError, ValueError):
            page = 1
        try:
            page_size = int(request.args.get("page_size") or 50)
        except (TypeError, ValueError):
            page_size = 50
        if page_size not in {25, 50, 100}:
            page_size = 50

        conn = get_db_connection()
        campaign_cursor = conn.cursor(dictionary=True)
        campaign_cursor.execute(
            """
            SELECT id, campaign_name, campaign_type, channel_code, campaign_status,
                   total_count, valid_count, duplicate_count, invalid_count,
                   success_count, failed_count, skipped_count, created_on,
                   scheduled_on, started_on, completed_on
            FROM broadcast_campaign
            WHERE id=%s
            """,
            (campaign_id,),
        )
        campaign = campaign_cursor.fetchone()
        if not campaign:
            abort(404)

        count_cursor = conn.cursor(dictionary=True)
        count_cursor.execute(
            "SELECT COUNT(*) AS total FROM broadcast_recipient WHERE campaign_id=%s",
            (campaign_id,),
        )
        total = int((count_cursor.fetchone() or {}).get("total") or 0)
        pages = max(1, (total + page_size - 1) // page_size)
        if page > pages:
            page = pages
        offset = (page - 1) * page_size

        recipient_cursor = conn.cursor(dictionary=True)
        recipient_cursor.execute(
            """
            SELECT id, receiver_name, mobile_no, email_id, country_name, language_code,
                   reference_no, recipient_tag, telegram_chat_id, validation_status,
                   delivery_status, attempt_count, provider_reference, last_error,
                   sent_on, updated_on
            FROM broadcast_recipient
            WHERE campaign_id=%s
            ORDER BY id
            LIMIT %s OFFSET %s
            """,
            (campaign_id, page_size, offset),
        )
        recipients = recipient_cursor.fetchall() or []
        if str(campaign.get("campaign_status") or "").upper() == "DRAFT":
            for row in recipients:
                if int(row.get("attempt_count") or 0) == 0 and str(row.get("delivery_status") or "").upper() == "QUEUED":
                    row["delivery_status"] = "DRAFT"

        return jsonify({
            "success": True,
            "campaign": campaign,
            "recipients": recipients,
            "pagination": {
                "page": page,
                "pages": pages,
                "page_size": page_size,
                "total": total,
                "start": (offset + 1) if total else 0,
                "end": min(offset + len(recipients), total),
            },
        })
    finally:
        if count_cursor:
            count_cursor.close()
        if recipient_cursor:
            recipient_cursor.close()
        if campaign_cursor:
            campaign_cursor.close()
        if conn:
            conn.close()


@broadcast_management_bp.delete("/api/history/<int:campaign_id>")
@login_required
def api_history_delete(campaign_id: int):
    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            "SELECT id, campaign_status FROM broadcast_campaign WHERE id=%s",
            (campaign_id,),
        )
        campaign = cursor.fetchone()
        if not campaign:
            return jsonify({"success": False, "message": "Broadcast not found."}), 404

        status = str(campaign.get("campaign_status") or "").upper()
        if status in _BROADCAST_DELETE_PROTECTED_STATUSES:
            return jsonify({
                "success": False,
                "message": "An active/queued broadcast cannot be deleted. Complete or stop delivery first.",
            }), 409

        cursor.execute("DELETE FROM broadcast_recipient WHERE campaign_id=%s", (campaign_id,))
        deleted_recipients = int(cursor.rowcount or 0)
        cursor.execute("DELETE FROM broadcast_campaign WHERE id=%s", (campaign_id,))
        if int(cursor.rowcount or 0) != 1:
            raise RuntimeError("Broadcast delete did not complete.")
        conn.commit()
        return jsonify({
            "success": True,
            "deleted_recipients": deleted_recipients,
            "campaign_status": status,
        })
    except Exception:
        if conn:
            conn.rollback()
        current_app.logger.exception("BROADCAST_HISTORY_DELETE_FAILED campaign_id=%s", campaign_id)
        return jsonify({
            "success": False,
            "message": "Unable to delete this broadcast. It may still be referenced by another record.",
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@broadcast_management_bp.delete("/api/history/bulk")
@login_required
def api_history_bulk_delete():
    """Delete multiple non-active broadcasts/drafts in one transaction."""
    payload = request.get_json(silent=True) or {}
    raw_ids = payload.get("campaign_ids")
    if not isinstance(raw_ids, list):
        return jsonify({"success": False, "message": "Select one or more broadcasts to delete."}), 400

    campaign_ids: list[int] = []
    seen: set[int] = set()
    for raw in raw_ids:
        try:
            campaign_id = int(raw)
        except (TypeError, ValueError):
            continue
        if campaign_id <= 0 or campaign_id in seen:
            continue
        seen.add(campaign_id)
        campaign_ids.append(campaign_id)

    if not campaign_ids:
        return jsonify({"success": False, "message": "Select one or more broadcasts to delete."}), 400
    if len(campaign_ids) > 100:
        return jsonify({"success": False, "message": "Bulk delete is limited to 100 broadcasts at a time."}), 400

    conn = cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        placeholders = ",".join(["%s"] * len(campaign_ids))
        cursor.execute(
            f"SELECT id, campaign_status FROM broadcast_campaign WHERE id IN ({placeholders})",
            tuple(campaign_ids),
        )
        found = cursor.fetchall() or []
        status_by_id = {int(row["id"]): str(row.get("campaign_status") or "").upper() for row in found}

        protected_ids = [
            campaign_id for campaign_id in campaign_ids
            if status_by_id.get(campaign_id) in _BROADCAST_DELETE_PROTECTED_STATUSES
        ]
        missing_ids = [campaign_id for campaign_id in campaign_ids if campaign_id not in status_by_id]
        deletable_ids = [
            campaign_id for campaign_id in campaign_ids
            if campaign_id in status_by_id and campaign_id not in protected_ids
        ]

        deleted_recipients = 0
        deleted_campaigns = 0
        if deletable_ids:
            delete_placeholders = ",".join(["%s"] * len(deletable_ids))
            cursor.execute(
                f"DELETE FROM broadcast_recipient WHERE campaign_id IN ({delete_placeholders})",
                tuple(deletable_ids),
            )
            deleted_recipients = int(cursor.rowcount or 0)
            cursor.execute(
                f"DELETE FROM broadcast_campaign WHERE id IN ({delete_placeholders})",
                tuple(deletable_ids),
            )
            deleted_campaigns = int(cursor.rowcount or 0)
            if deleted_campaigns != len(deletable_ids):
                raise RuntimeError("Bulk broadcast delete did not complete for every selected campaign.")

        conn.commit()
        return jsonify({
            "success": True,
            "deleted_campaigns": deleted_campaigns,
            "deleted_recipients": deleted_recipients,
            "protected_ids": protected_ids,
            "missing_ids": missing_ids,
        })
    except Exception:
        if conn:
            conn.rollback()
        current_app.logger.exception("BROADCAST_HISTORY_BULK_DELETE_FAILED campaign_ids=%s", campaign_ids)
        return jsonify({
            "success": False,
            "message": "Unable to bulk delete the selected broadcasts. No partial bulk delete was kept.",
        }), 500
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


@broadcast_management_bp.get("/templates")
@login_required
def broadcast_templates():
    return render_template(
        "broadcast_templates.html",
        mapping_sources=sorted(ALLOWED_MAPPING_SOURCES),
    )


@broadcast_management_bp.get("/api/templates")
@login_required
def api_templates():
    channel = str(request.args.get("channel") or "").strip().upper() or None
    status = str(request.args.get("status") or "").strip().upper() or None
    return jsonify({"success": True, "templates": list_templates(channel=channel, status=status)})


@broadcast_management_bp.get("/api/templates/<int:template_id>")
@login_required
def api_template_detail(template_id: int):
    item = get_template(template_id)
    if not item:
        abort(404)
    return jsonify({"success": True, "template": item})


@broadcast_management_bp.post("/api/templates/sync-meta")
@login_required
def api_template_sync_meta():
    try:
        result = sync_whatsapp_templates()
        return jsonify({"success": True, "data": result})
    except Exception as exc:
        return jsonify({"success": False, "message": str(exc)}), 502


@broadcast_management_bp.post("/api/templates/<int:template_id>/mapping")
@login_required
def api_template_mapping(template_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        item = save_variable_mapping(template_id, payload.get("mapping") or {})
        return jsonify({"success": True, "template": item})
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400


@broadcast_management_bp.post("/api/templates/whatsapp")
@login_required
def api_whatsapp_template_create():
    # V1.3.5 accepts multipart/form-data so IMAGE/VIDEO/DOCUMENT sample media can be
    # uploaded to Meta's Resumable Upload API before the template is submitted.
    if request.mimetype and request.mimetype.startswith("multipart/form-data"):
        payload = request.form.to_dict(flat=True)
        media_file = request.files.get("header_media")
    else:
        payload = request.get_json(silent=True) or {}
        media_file = None
    try:
        data = create_whatsapp_template(payload, media_file=media_file)
        return jsonify({"success": True, "data": data})
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        current_app.logger.exception("BROADCAST_WHATSAPP_TEMPLATE_CREATE_FAILED")
        return jsonify({"success": False, "message": str(exc)}), 502


@broadcast_management_bp.delete("/api/templates/whatsapp/<int:template_id>")
@login_required
def api_whatsapp_template_delete(template_id: int):
    try:
        result = delete_whatsapp_template(template_id)
        return jsonify({"success": True, "data": result})
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    except Exception as exc:
        current_app.logger.exception(
            "BROADCAST_WHATSAPP_TEMPLATE_DELETE_FAILED template_id=%s",
            template_id,
        )
        return jsonify({"success": False, "message": str(exc)}), 502


@broadcast_management_bp.post("/api/templates/email")
@login_required
def api_email_template_create():
    payload = request.get_json(silent=True) or {}
    try:
        item = save_email_template(payload)
        return jsonify({"success": True, "template": item})
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400


@broadcast_management_bp.put("/api/templates/email/<int:template_id>")
@login_required
def api_email_template_update(template_id: int):
    payload = request.get_json(silent=True) or {}
    try:
        item = save_email_template(payload, template_id=template_id)
        return jsonify({"success": True, "template": item})
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400


@broadcast_management_bp.delete("/api/templates/email/<int:template_id>")
@login_required
def api_email_template_delete(template_id: int):
    try:
        archive_email_template(template_id)
        return jsonify({"success": True})
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400


@broadcast_management_bp.post("/api/validate")
@login_required
def api_validate():
    payload = request.get_json(silent=True) or {}
    return jsonify({
        "success": True,
        "data": validate_recipients(payload.get("recipients") or [], payload.get("channel")),
    })



def _unsubscribe_no_cache_response(content, status: int = 200):
    response = make_response(content, status)
    response.headers["Cache-Control"] = "no-store, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["X-Robots-Tag"] = "noindex, nofollow"
    return response


@broadcast_management_bp.route("/unsubscribe", methods=["GET", "POST"])
@csrf.exempt
def broadcast_email_unsubscribe():
    """Public, signed recipient opt-out endpoint for Marketing Email only.

    GET never changes subscription state; this prevents mail-security link
    scanners from unsubscribing recipients. RFC 8058 one-click unsubscribe uses
    an explicit POST and is handled immediately.
    """
    token = str(request.args.get("token") or request.form.get("token") or "").strip()
    try:
        payload = parse_unsubscribe_token(token)
    except BadSignature:
        content = render_template(
            "broadcast_unsubscribe.html",
            invalid=True,
            completed=False,
            already_unsubscribed=False,
            token="",
            masked_email="",
        )
        return _unsubscribe_no_cache_response(content, status=400)
    except Exception:
        current_app.logger.exception("BROADCAST_UNSUBSCRIBE_TOKEN_ERROR")
        content = render_template(
            "broadcast_unsubscribe.html",
            invalid=True,
            completed=False,
            already_unsubscribed=False,
            token="",
            masked_email="",
        )
        return _unsubscribe_no_cache_response(content, status=500)

    email = payload["email"]
    masked = masked_email(email)

    if request.method == "GET":
        try:
            already = is_email_unsubscribed(email)
        except Exception:
            current_app.logger.exception("BROADCAST_UNSUBSCRIBE_STATUS_ERROR")
            content = render_template(
                "broadcast_unsubscribe.html",
                invalid=False,
                service_error=True,
                completed=False,
                already_unsubscribed=False,
                token=token,
                masked_email=masked,
            )
            return _unsubscribe_no_cache_response(content, status=503)

        content = render_template(
            "broadcast_unsubscribe.html",
            invalid=False,
            service_error=False,
            completed=already,
            already_unsubscribed=already,
            token=token,
            masked_email=masked,
        )
        return _unsubscribe_no_cache_response(content)

    one_click = (
        str(request.form.get("List-Unsubscribe") or "").strip().lower()
        == "one-click"
    )
    source = "RFC8058" if one_click else "EMAIL_LINK"

    try:
        suppress_email(
            email,
            source=source,
            campaign_id=payload.get("campaign_id"),
            recipient_id=payload.get("recipient_id"),
        )
    except Exception:
        current_app.logger.exception(
            "BROADCAST_UNSUBSCRIBE_SAVE_ERROR campaign_id=%s recipient_id=%s",
            payload.get("campaign_id"),
            payload.get("recipient_id"),
        )
        if one_click:
            return _unsubscribe_no_cache_response("Unable to process unsubscribe.", status=503)
        content = render_template(
            "broadcast_unsubscribe.html",
            invalid=False,
            service_error=True,
            completed=False,
            already_unsubscribed=False,
            token=token,
            masked_email=masked,
        )
        return _unsubscribe_no_cache_response(content, status=503)

    if one_click:
        return _unsubscribe_no_cache_response("Unsubscribed", status=200)

    content = render_template(
        "broadcast_unsubscribe.html",
        invalid=False,
        service_error=False,
        completed=True,
        already_unsubscribed=False,
        token=token,
        masked_email=masked,
    )
    return _unsubscribe_no_cache_response(content)


@broadcast_management_bp.get("/public-media/<path:filename>")
def public_broadcast_media(filename: str):
    """Serve only random-name validated Broadcast marketing images publicly.

    This route is intentionally unauthenticated because external email clients
    (including Gmail's image proxy) must fetch these images. It does not expose
    patient/private upload locations and accepts only the 32-hex filenames that
    the Broadcast Email uploader itself creates.
    """
    safe_name = Path(filename).name
    if safe_name != filename:
        abort(404)

    lower_name = safe_name.lower()
    ext = Path(lower_name).suffix
    stem = Path(lower_name).stem
    if (
        ext not in _BROADCAST_IMAGE_TYPES
        or len(stem) != 32
        or any(ch not in "0123456789abcdef" for ch in stem)
    ):
        abort(404)

    media_dir = Path(current_app.instance_path) / "broadcast_media"
    response = send_from_directory(media_dir, safe_name, max_age=604800, conditional=True)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Content-Disposition"] = "inline"
    response.headers["Cache-Control"] = "public, max-age=604800, immutable"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Cross-Origin-Resource-Policy"] = "cross-origin"
    response.headers["Content-Security-Policy"] = "default-src 'none'"
    return response


@broadcast_management_bp.get("/api/media/<path:filename>")
@login_required
def api_media_view(filename: str):
    safe_name = Path(filename).name
    if safe_name != filename or Path(safe_name).suffix.lower() not in _BROADCAST_IMAGE_TYPES:
        abort(404)
    media_dir = Path(current_app.instance_path) / "broadcast_media"
    response = send_from_directory(media_dir, safe_name, max_age=0)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "private, no-store, max-age=0"
    response.headers["Content-Disposition"] = "inline"
    return response


@broadcast_management_bp.post("/api/media/upload")
@login_required
def api_media_upload():
    upload = request.files.get("file")
    if not upload or not upload.filename:
        return jsonify({"success": False, "message": "Select an image file first."}), 400

    original_name = Path(upload.filename).name[:255]
    ext = Path(original_name).suffix.lower()
    if ext not in _BROADCAST_IMAGE_TYPES:
        return jsonify({
            "success": False,
            "message": "Email Broadcast accepts only JPG, JPEG, PNG, GIF or WEBP images.",
        }), 400

    raw = upload.stream.read(_BROADCAST_IMAGE_MAX_BYTES + 1)
    if len(raw) > _BROADCAST_IMAGE_MAX_BYTES:
        return jsonify({"success": False, "message": "Broadcast email image must be 5 MB or smaller."}), 400
    if not raw:
        return jsonify({"success": False, "message": "The selected image file is empty."}), 400

    try:
        image_format, width, height = _validate_broadcast_image(raw, ext)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    media_dir = Path(current_app.instance_path) / "broadcast_media"
    media_dir.mkdir(parents=True, exist_ok=True)
    canonical_ext = ".jpg" if image_format == "JPEG" else "." + image_format.lower()
    stored_name = f"{uuid4().hex}{canonical_ext}"
    target = media_dir / stored_name
    target.write_bytes(raw)

    return jsonify({
        "success": True,
        "media_url": f"local://broadcast_media/{stored_name}",
        "media_name": original_name,
        "media_type": "image",
        "width": width,
        "height": height,
    })


@broadcast_management_bp.post("/api/import-csv")
@login_required
def api_import_csv():
    file = request.files.get("file")
    if not file:
        return jsonify({"success": False, "message": "CSV file is required."}), 400
    if not str(file.filename or "").lower().endswith(".csv"):
        return jsonify({"success": False, "message": "Only CSV files are permitted."}), 400
    raw = file.read(2 * 1024 * 1024 + 1)
    if len(raw) > 2 * 1024 * 1024:
        return jsonify({"success": False, "message": "CSV cannot exceed 2 MB."}), 413
    try:
        rows = parse_csv_bytes(raw)
    except Exception:
        return jsonify({"success": False, "message": "Unable to parse CSV as UTF-8."}), 400
    return jsonify({"success": True, "rows": rows})


@broadcast_management_bp.post("/api/campaigns")
@login_required
def api_create_campaign():
    payload = request.get_json(silent=True) or {}
    recipients = payload.pop("recipients", []) or []
    if not str(payload.get("campaign_name") or "").strip():
        return jsonify({"success": False, "message": "Broadcast name is required."}), 400
    channel = str(payload.get("channel") or "").upper()
    if channel != "WHATSAPP" and not str(payload.get("message_text") or "").strip():
        return jsonify({"success": False, "message": "Message content is required."}), 400
    validation = validate_recipients(recipients, channel)
    if validation["counts"]["valid"] <= 0:
        return jsonify({"success": False, "message": "No valid receivers."}), 400
    try:
        campaign_id = create_campaign(payload, recipients)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400
    return jsonify({"success": True, "campaign_id": campaign_id})


@broadcast_management_bp.post("/api/campaigns/<int:campaign_id>/activate")
@login_required
def api_activate(campaign_id: int):
    try:
        data = activate_campaign(campaign_id)
        return jsonify({"success": True, "data": data})
    except RuntimeError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400


@broadcast_management_bp.post("/api/campaigns/<int:campaign_id>/dispatch")
@login_required
def api_dispatch(campaign_id: int):
    data = dispatch_campaign(campaign_id, max_rows=100)
    return jsonify({
        "success": True,
        "data": data,
        "summary": _campaign_delivery_summary(campaign_id),
    })


@broadcast_management_bp.post("/api/campaigns/<int:campaign_id>/retry")
@login_required
def api_retry(campaign_id: int):
    changed = retry_failed(campaign_id)
    return jsonify({"success": True, "retry_count": changed})


@broadcast_management_bp.get("/api/campaigns/<int:campaign_id>/status")
@login_required
def api_status(campaign_id: int):
    data = campaign_status(campaign_id)
    if not data:
        abort(404)
    return jsonify({
        "success": True,
        "data": data,
        "summary": _campaign_delivery_summary(campaign_id),
    })
