import json
import mimetypes
import os
import re
import uuid
from datetime import datetime

from werkzeug.utils import secure_filename

from .utils import clean
from .file_validation import DEFAULT_DOCUMENT_EXTENSIONS, validate_document_bytes


# =========================================================
# EXISTING HELPERS - KEPT AS-IS
# =========================================================
def collect_uploaded_files(field_names, total_limit_bytes):
    from flask import request

    files = []
    for field_name in field_names:
        try:
            files.extend(request.files.getlist(field_name))
        except Exception:
            pass

    files = [f for f in files if f and (f.filename or "").strip()]

    file_rows = []
    total_bytes = 0

    for f in files:
        safe_name = secure_filename(f.filename) or "document"
        data = f.read() or b""
        validation = validate_document_bytes(
            safe_name,
            data,
            allowed_extensions=DEFAULT_DOCUMENT_EXTENSIONS,
            max_bytes=total_limit_bytes,
        )
        size = validation["size"]
        total_bytes += size

        file_rows.append({
            "file_name": validation["safe_name"],
            "file_type": (f.mimetype or mimetypes.guess_type(validation["safe_name"])[0] or "application/octet-stream"),
            "file_size": size,
            "document_data": data,
        })

    if total_bytes > total_limit_bytes:
        raise ValueError(
            f"Total document upload size cannot exceed {int(total_limit_bytes / (1024 * 1024))} MB."
        )

    return file_rows


def save_vil_docs(conn, passport_no, file_rows):
    passport_no = clean(passport_no)
    if not passport_no or not file_rows:
        return 0

    cur = conn.cursor()
    count = 0
    try:
        for row in file_rows:
            cur.execute(
                """
                INSERT INTO vilcreate_docs
                    (document_data, file_name, file_size, file_type, passport_no)
                VALUES (%s, %s, %s, %s, %s)
                """,
                (
                    row["document_data"],
                    row["file_name"],
                    row["file_size"],
                    row["file_type"],
                    passport_no,
                ),
            )
            count += 1
    finally:
        try:
            cur.close()
        except Exception:
            pass

    return count


# =========================================================
# NEW GENERIC FILE HELPERS - ADDITIVE ONLY
# =========================================================
def ensure_directory(path):
    if path and not os.path.exists(path):
        os.makedirs(path, exist_ok=True)


def normalize_path(path):
    if not path:
        return ""
    return os.path.normpath(str(path)).replace("\\", "/")


def get_file_extension(filename):
    if not filename or "." not in str(filename):
        return ""
    return str(filename).rsplit(".", 1)[1].lower().strip()


def sanitize_filename(filename):
    if not filename:
        return "file"
    cleaned = secure_filename(str(filename))
    return cleaned or "file"


def get_file_size_from_storage(file_obj):
    """
    Safely read uploaded file size without disturbing later save operations.
    """
    if not file_obj:
        return 0

    try:
        current_pos = file_obj.stream.tell()
    except Exception:
        current_pos = 0

    try:
        file_obj.stream.seek(0, os.SEEK_END)
        size = file_obj.stream.tell()
        file_obj.stream.seek(current_pos)
        return int(size or 0)
    except Exception:
        try:
            file_obj.stream.seek(0)
            data = file_obj.stream.read() or b""
            size = len(data)
            file_obj.stream.seek(0)
            return int(size)
        except Exception:
            return 0


def format_file_size(num_bytes):
    try:
        num_bytes = float(num_bytes or 0)
    except Exception:
        num_bytes = 0

    if num_bytes < 1024:
        return f"{int(num_bytes)} B"
    if num_bytes < 1024 * 1024:
        return f"{round(num_bytes / 1024, 2)} KB"
    if num_bytes < 1024 * 1024 * 1024:
        return f"{round(num_bytes / (1024 * 1024), 2)} MB"
    return f"{round(num_bytes / (1024 * 1024 * 1024), 2)} GB"


def build_unique_filename(filename, prefix=None):
    safe_name = sanitize_filename(filename)
    timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
    random_part = uuid.uuid4().hex[:8]

    if prefix:
        safe_prefix = re.sub(r"[^A-Za-z0-9_\-]", "_", str(prefix))
        return f"{safe_prefix}_{timestamp}_{random_part}_{safe_name}"

    return f"{timestamp}_{random_part}_{safe_name}"


def parse_attachment_json(attachment_json_value):
    if not attachment_json_value:
        return []

    if isinstance(attachment_json_value, list):
        return attachment_json_value

    try:
        parsed = json.loads(attachment_json_value)
        if isinstance(parsed, list):
            return parsed
        return []
    except Exception:
        return []


def build_attachment_json(attachments):
    try:
        return json.dumps(attachments or [])
    except Exception:
        return "[]"


# =========================================================
# WHATSAPP DOC UPLOAD HELPERS - NEW
# =========================================================
ALLOWED_WHATSAPP_EXTENSIONS = {
    "jpg",
    "jpeg",
    "png",
    "pdf",
    "txt",
    "xls",
    "xlsx",
    "doc",
    "docx",
}

MAX_WHATSAPP_MESSAGE_TOTAL_SIZE = 10 * 1024 * 1024  # 10 MB total per message


def is_allowed_whatsapp_file(filename):
    ext = get_file_extension(filename)
    return ext in ALLOWED_WHATSAPP_EXTENSIONS


def build_whatsapp_upload_subfolder(base_folder, channel_message_id=None):
    date_part = datetime.now().strftime("%Y%m%d")
    safe_message_id = re.sub(
        r"[^A-Za-z0-9_\-]",
        "_",
        str(channel_message_id or uuid.uuid4().hex)
    )
    return os.path.join(base_folder, date_part, safe_message_id)


def save_uploaded_files(files, base_folder, channel_message_id=None):
    """
    Saves WhatsApp/stub uploaded files physically to disk and returns metadata.

    Return structure:
    {
        "success": True/False,
        "message": "...",
        "attachment_count": int,
        "attachment_json": json string,
        "attachments": [ ... ],
        "total_size": int
    }

    Constraint:
    - Total size of all files in one message must not exceed 10 MB.
    """
    files = files or []

    valid_files = [f for f in files if f and getattr(f, "filename", None)]
    if not valid_files:
        return {
            "success": True,
            "message": "No files uploaded.",
            "attachment_count": 0,
            "attachment_json": "[]",
            "attachments": [],
            "total_size": 0,
        }

    total_size = 0
    scanned_files = []

    # -----------------------------------------------------
    # Validation pass
    # -----------------------------------------------------
    for file_obj in valid_files:
        original_name = file_obj.filename or ""
        safe_original_name = sanitize_filename(original_name)

        if not is_allowed_whatsapp_file(safe_original_name):
            return {
                "success": False,
                "message": f"File type not allowed: {original_name}",
                "attachment_count": 0,
                "attachment_json": "[]",
                "attachments": [],
                "total_size": 0,
            }

        current_pos = file_obj.stream.tell()
        file_obj.stream.seek(0)
        file_bytes = file_obj.stream.read() or b""
        file_obj.stream.seek(current_pos)
        validation = validate_document_bytes(
            safe_original_name,
            file_bytes,
            allowed_extensions=ALLOWED_WHATSAPP_EXTENSIONS,
            max_bytes=MAX_WHATSAPP_MESSAGE_TOTAL_SIZE,
        )
        file_size = validation["size"]
        total_size += file_size

        if total_size > MAX_WHATSAPP_MESSAGE_TOTAL_SIZE:
            return {
                "success": False,
                "message": "Total attachment size per message cannot exceed 10 MB.",
                "attachment_count": 0,
                "attachment_json": "[]",
                "attachments": [],
                "total_size": total_size,
            }

        scanned_files.append(
            {
                "file_obj": file_obj,
                "original_name": original_name,
                "safe_original_name": safe_original_name,
                "file_size": file_size,
                "mime_type": (
                    getattr(file_obj, "mimetype", None)
                    or mimetypes.guess_type(safe_original_name)[0]
                    or "application/octet-stream"
                ),
                "file_ext": get_file_extension(safe_original_name),
            }
        )

    # -----------------------------------------------------
    # Save pass
    # -----------------------------------------------------
    target_folder = build_whatsapp_upload_subfolder(
        base_folder=base_folder,
        channel_message_id=channel_message_id
    )
    ensure_directory(target_folder)

    attachments = []

    for item in scanned_files:
        file_obj = item["file_obj"]
        original_name = item["original_name"]
        safe_original_name = item["safe_original_name"]

        stored_file_name = build_unique_filename(
            safe_original_name,
            prefix=channel_message_id or "WA"
        )
        absolute_path = os.path.join(target_folder, stored_file_name)

        file_obj.stream.seek(0)
        file_obj.save(absolute_path)

        attachments.append(
            {
                "file_name": original_name,
                "stored_file_name": stored_file_name,
                "file_ext": item["file_ext"],
                "mime_type": item["mime_type"],
                "file_size": item["file_size"],
                "file_size_display": format_file_size(item["file_size"]),
                "file_path": normalize_path(absolute_path),
                "remarks": "Uploaded from WhatsApp/stub message",
            }
        )

    return {
        "success": True,
        "message": "Files uploaded successfully.",
        "attachment_count": len(attachments),
        "attachment_json": build_attachment_json(attachments),
        "attachments": attachments,
        "total_size": total_size,
    }