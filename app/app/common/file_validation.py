from __future__ import annotations

import io
import zipfile
from pathlib import Path

from werkzeug.utils import secure_filename

from .malware_scan import scan_upload_bytes


DEFAULT_DOCUMENT_EXTENSIONS = {
    "pdf", "doc", "docx", "xls", "xlsx", "ppt", "pptx",
    "csv", "txt", "jpg", "jpeg", "png", "webp",
}


def _validate_ooxml(data: bytes, extension: str) -> None:
    try:
        with zipfile.ZipFile(io.BytesIO(data)) as archive:
            infos = archive.infolist()
            total_uncompressed = sum(max(0, int(info.file_size or 0)) for info in infos)
            # Prevent small compressed uploads expanding to an excessive amount in parsers.
            if total_uncompressed > 50 * 1024 * 1024:
                raise ValueError("Office document expands beyond the permitted safety limit.")

            names = {str(info.filename or "").replace("\\", "/").lower() for info in infos}
            required_prefix = {
                "docx": "word/",
                "xlsx": "xl/",
                "pptx": "ppt/",
            }.get(extension)
            if required_prefix and not any(name.startswith(required_prefix) for name in names):
                raise ValueError("File content does not match its Office document extension.")
    except zipfile.BadZipFile as exc:
        raise ValueError("Invalid Office document container.") from exc


def validate_document_bytes(
    filename: str,
    data: bytes,
    *,
    allowed_extensions=None,
    max_bytes: int | None = None,
) -> dict:
    """Validate upload extension, size, and basic file signature.

    This is intentionally conservative. It is a first-line application control;
    production deployments should also malware-scan documents before downstream
    processing where practical.
    """
    safe_name = secure_filename(str(filename or "").strip())
    if not safe_name:
        raise ValueError("Invalid or empty file name.")

    extension = Path(safe_name).suffix.lower().lstrip(".")
    allowed = set(allowed_extensions or DEFAULT_DOCUMENT_EXTENSIONS)
    if extension not in allowed:
        raise ValueError(f"Unsupported file type: .{extension or 'unknown'}")

    if not isinstance(data, (bytes, bytearray)):
        raise ValueError("Uploaded file content is invalid.")
    data = bytes(data)
    if not data:
        raise ValueError("Uploaded file is empty.")
    if max_bytes is not None and len(data) > int(max_bytes):
        raise ValueError("Uploaded file exceeds the permitted size limit.")

    if extension == "pdf":
        if not data.startswith(b"%PDF-"):
            raise ValueError("File content does not match PDF format.")
    elif extension in {"jpg", "jpeg"}:
        if not data.startswith(b"\xff\xd8\xff"):
            raise ValueError("File content does not match JPEG format.")
    elif extension == "png":
        if not data.startswith(b"\x89PNG\r\n\x1a\n"):
            raise ValueError("File content does not match PNG format.")
    elif extension == "webp":
        if len(data) < 12 or data[:4] != b"RIFF" or data[8:12] != b"WEBP":
            raise ValueError("File content does not match WEBP format.")
    elif extension in {"docx", "xlsx", "pptx"}:
        if not data.startswith(b"PK"):
            raise ValueError("File content does not match modern Office format.")
        _validate_ooxml(data, extension)
    elif extension in {"doc", "xls", "ppt"}:
        if not data.startswith(b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"):
            raise ValueError("File content does not match legacy Office format.")
    elif extension in {"txt", "csv"}:
        if b"\x00" in data[:8192]:
            raise ValueError("Text upload contains binary content.")

    scan_upload_bytes(data, safe_name)

    return {
        "safe_name": safe_name,
        "extension": extension,
        "size": len(data),
    }
