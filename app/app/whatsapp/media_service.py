import os
import requests
from datetime import datetime

from flask import current_app
from werkzeug.utils import secure_filename



def _debug_print(*args, **kwargs):
    import os
    if str(os.getenv("WHATSAPP_DEBUG_LOGGING", "false")).strip().lower() in {"1", "true", "yes", "on"}:
        print(*args, **kwargs)


def _clean_env_value(value):
    if value is None:
        return ""

    return str(value).strip().replace('"', "").replace("'", "")


def _get_whatsapp_access_token():
    token = _clean_env_value(
        os.getenv("WHATSAPP_ACCESS_TOKEN")
    )

    if not token:
        try:
            token = _clean_env_value(
                current_app.config.get("WHATSAPP_ACCESS_TOKEN")
            )
        except Exception:
            token = ""

    if not token:
        raise Exception(
            "WHATSAPP_ACCESS_TOKEN missing in .env or Flask config"
        )

    return token


def download_whatsapp_media(
    media_id,
    filename=None,
    mime_type=None,
):
    """
    Downloads WhatsApp media from Meta and saves it in private application
    storage under instance/uploads/whatsapp_docs/YYYYMMDD/. Files are not
    directly web-addressable and are served only through an authenticated
    application route.

    Returns saved file details for operation_queue_attachment insert.
    """

    access_token = _get_whatsapp_access_token()

    api_version = (
        _clean_env_value(
            os.getenv("WHATSAPP_API_VERSION")
        )
        or "v23.0"
    )

    media_id = _clean_env_value(media_id)

    if not media_id:
        raise Exception("WhatsApp media_id missing")

    headers = {
        "Authorization": f"Bearer {access_token}"
    }

    media_api_url = (
        f"https://graph.facebook.com/"
        f"{api_version}/{media_id}"
    )

    masked_token = (
        f"{access_token[:8]}...{access_token[-5:]}"
        if access_token
        else "NOT FOUND"
    )

    _debug_print(
        "WHATSAPP MEDIA ID:",
        media_id,
        flush=True,
    )

    _debug_print(
        "WHATSAPP MEDIA API URL:",
        media_api_url,
        flush=True,
    )

    _debug_print(
        "WHATSAPP TOKEN:",
        masked_token,
        "LEN:",
        len(access_token),
        flush=True,
    )

    # Step 1: Get temporary media download URL
    try:
        media_resp = requests.get(
            media_api_url,
            headers=headers,
            timeout=30,
        )
    except requests.RequestException as exc:
        raise Exception(
            f"Unable to connect to Meta media API: {exc}"
        ) from exc

    _debug_print(
        "WHATSAPP MEDIA METADATA STATUS:",
        media_resp.status_code,
        flush=True,
    )

    if media_resp.status_code >= 400:
        raise Exception(
            "Meta media API error. "
            f"Status={media_resp.status_code}, "
            f"Response={media_resp.text}"
        )

    try:
        media_info = media_resp.json()
    except ValueError as exc:
        raise Exception(
            "Invalid JSON received from Meta media API: "
            f"{media_resp.text}"
        ) from exc

    download_url = media_info.get("url")

    if not download_url:
        raise Exception(
            "No media download URL received from Meta. "
            f"Response={media_info}"
        )

    meta_mime_type = _clean_env_value(
        media_info.get("mime_type")
    )

    resolved_mime_type = (
        mime_type
        or meta_mime_type
        or ""
    )

    # Step 2: Download actual media file
    try:
        file_resp = requests.get(
            download_url,
            headers=headers,
            timeout=60,
        )
    except requests.RequestException as exc:
        raise Exception(
            f"Unable to download WhatsApp media file: {exc}"
        ) from exc

    _debug_print(
        "WHATSAPP MEDIA FILE STATUS:",
        file_resp.status_code,
        flush=True,
    )

    if file_resp.status_code >= 400:
        raise Exception(
            "Meta media file download error. "
            f"Status={file_resp.status_code}, "
            f"Response={file_resp.text}"
        )

    if not file_resp.content:
        raise Exception(
            "Meta returned an empty WhatsApp media file"
        )

    max_bytes = int(current_app.config.get("WHATSAPP_MEDIA_MAX_BYTES") or (10 * 1024 * 1024))
    if len(file_resp.content) > max_bytes:
        raise Exception("WhatsApp media exceeds the configured maximum size")

    response_mime_type = _clean_env_value(
        file_resp.headers.get("Content-Type")
    )

    if not resolved_mime_type:
        resolved_mime_type = response_mime_type

    resolved_mime_type = (resolved_mime_type or "").split(";", 1)[0].strip().lower()
    allowed_mime_types = {
        "application/pdf",
        "image/jpeg", "image/jpg", "image/png", "image/webp",
        "video/mp4",
        "audio/ogg", "audio/opus", "audio/mpeg", "audio/mp4",
        "text/plain",
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    }
    if resolved_mime_type not in allowed_mime_types:
        raise Exception("Unsupported WhatsApp media type")

    today_folder = datetime.now().strftime("%Y%m%d")

    upload_dir = os.path.join(
        current_app.instance_path,
        "uploads",
        "whatsapp_docs",
        today_folder,
    )

    os.makedirs(
        upload_dir,
        exist_ok=True,
    )

    if not filename:
        extension = get_extension_from_mime(
            resolved_mime_type
        )

        filename = (
            f"WA_MEDIA_{media_id}.{extension}"
        )

    safe_filename = secure_filename(filename)

    if not safe_filename:
        extension = get_extension_from_mime(
            resolved_mime_type
        )

        safe_filename = (
            f"WA_MEDIA_{media_id}.{extension}"
        )

    full_file_path = os.path.join(
        upload_dir,
        safe_filename,
    )

    if os.path.exists(full_file_path):
        name, extension = os.path.splitext(
            safe_filename
        )

        unique_suffix = datetime.now().strftime(
            "%H%M%S%f"
        )

        safe_filename = (
            f"{name}_{unique_suffix}{extension}"
        )

        full_file_path = os.path.join(
            upload_dir,
            safe_filename,
        )

    try:
        with open(full_file_path, "wb") as file_handle:
            file_handle.write(file_resp.content)
    except OSError as exc:
        raise Exception(
            "Unable to save WhatsApp media file. "
            f"Path={full_file_path}, Error={exc}"
        ) from exc

    file_ext = (
        os.path.splitext(safe_filename)[1]
        .replace(".", "")
        .lower()
    )

    relative_path = (
        f"private/whatsapp_docs/"
        f"{today_folder}/"
        f"{safe_filename}"
    )

    _debug_print(
        "WHATSAPP MEDIA SAVED:",
        relative_path,
        flush=True,
    )

    return {
        "file_name": safe_filename,
        "file_ext": file_ext,
        "mime_type": (
            resolved_mime_type
            or "application/octet-stream"
        ),
        "file_size": len(file_resp.content),
        "file_path": relative_path,
    }


def get_extension_from_mime(mime_type):
    """
    Returns file extension from MIME type.
    """

    mime_type = (
        mime_type
        or ""
    ).lower().strip()

    mime_type = mime_type.split(";")[0].strip()

    if mime_type == "application/pdf":
        return "pdf"

    if mime_type == "application/msword":
        return "doc"

    if mime_type == "application/vnd.openxmlformats-officedocument.wordprocessingml.document":
        return "docx"

    if mime_type == "application/vnd.ms-excel":
        return "xls"

    if mime_type == "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet":
        return "xlsx"

    if mime_type == "text/plain":
        return "txt"

    if mime_type in (
        "image/jpeg",
        "image/jpg",
    ):
        return "jpg"

    if mime_type == "image/png":
        return "png"

    if mime_type == "image/webp":
        return "webp"

    if mime_type == "video/mp4":
        return "mp4"

    if mime_type in (
        "audio/ogg",
        "audio/opus",
    ):
        return "ogg"

    if mime_type == "audio/mpeg":
        return "mp3"

    if mime_type == "audio/mp4":
        return "m4a"

    return "bin"