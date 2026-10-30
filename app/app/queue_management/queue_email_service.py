import os
import re
import json
import base64
import mimetypes
from datetime import datetime
from urllib.parse import urlencode, quote
from urllib.request import Request, urlopen
from urllib.error import HTTPError, URLError

from flask import current_app
from jinja2.sandbox import ImmutableSandboxedEnvironment

from app.common.db import get_db_connection


def _safe_str(value):
    if value is None:
        return ""
    return str(value).strip()


def _get_generic_table_columns(cursor, table_name):
    cursor.execute(f"SHOW COLUMNS FROM {table_name}")
    rows = cursor.fetchall() or []
    columns = []

    for row in rows:
        if isinstance(row, dict):
            col_name = row.get("Field")
        else:
            col_name = row[0]

        if col_name:
            columns.append(str(col_name).strip())

    return columns



def _normalize_key(value):
    """
    Normalize a column/key name for loose matching.
    Example: Patient Name, patient_name, patient-name -> patientname
    """
    value = _safe_str(value).lower()
    if not value:
        return ""
    return re.sub(r"[^a-z0-9]", "", value)


def _build_browser_file_url(file_path):
    """
    Convert stored file_path into browser-usable /static/... URL.
    This is used only for showing attachment links in the UI.
    """
    raw_path = _safe_str(file_path)
    if not raw_path:
        return ""

    normalized = raw_path.replace("\\", "/")

    if normalized.startswith("http://") or normalized.startswith("https://"):
        return normalized

    lower_path = normalized.lower()

    private_marker = "private/whatsapp_docs/"
    if lower_path.startswith(private_marker):
        relative = normalized[len(private_marker):].lstrip("/")
        if not relative or ".." in relative.split("/"):
            return ""
        browser_path = "/whatsapp/media/" + relative
        return quote(browser_path, safe="/:._-() ")

    static_marker = "/static/"
    static_index = lower_path.rfind(static_marker)
    if static_index >= 0:
        browser_path = normalized[static_index:]
        return quote(browser_path, safe="/:._-() ")

    if lower_path.startswith("static/"):
        browser_path = "/" + normalized.lstrip("/")
        return quote(browser_path, safe="/:._-() ")

    app_static_marker = "app/static/"
    app_static_index = lower_path.rfind(app_static_marker)
    if app_static_index >= 0:
        browser_path = "/" + normalized[app_static_index + len("app/"):]
        return quote(browser_path, safe="/:._-() ")

    uploads_marker = "/uploads/"
    uploads_index = lower_path.rfind(uploads_marker)
    if uploads_index >= 0:
        browser_path = "/static" + normalized[uploads_index:]
        return quote(browser_path, safe="/:._-() ")

    if lower_path.startswith("uploads/"):
        browser_path = "/static/" + normalized
        return quote(browser_path, safe="/:._-() ")

    return ""


def _fetch_latest_operation_queue_row_by_thread_id(cursor, channel_thread_id):
    """
    Fetch the latest non-closed operation queue row for template placeholder rendering.
    """
    channel_thread_id = _safe_str(channel_thread_id)
    if not channel_thread_id:
        return {}

    cursor.execute(
        """
        SELECT *
        FROM operation_queue_master
        WHERE channel_thread_id = %s
          AND UPPER(TRIM(IFNULL(wrkflw_status, ''))) <> 'CLOSED'
        ORDER BY id DESC
        LIMIT 1
        """,
        (channel_thread_id,),
    )

    return cursor.fetchone() or {}

def _table_has_required_partner_email_columns(cursor):
    columns = [col.lower() for col in _get_generic_table_columns(cursor, "partner_reg")]
    return (
        "short_name" in columns and
        "partner_type" in columns and
        "partner_email" in columns
    )


def _normalize_hospital_key(value):
    value = _safe_str(value).upper()
    if not value:
        return ""
    return (
        value.replace("-", "")
        .replace("_", "")
        .replace(" ", "")
        .replace(".", "")
        .replace(",", "")
        .replace("/", "")
        .replace("\\", "")
    )


def _fetch_hospital_email_exact(cursor, hospital_shrtnm):
    sql = """
        SELECT
            IFNULL(partner_email, '') AS hospital_email,
            IFNULL(short_name, '') AS matched_short_name,
            IFNULL(partner_type, '') AS matched_partner_type
        FROM partner_reg
        WHERE UPPER(TRIM(IFNULL(short_name, ''))) COLLATE utf8mb4_unicode_ci
              = UPPER(TRIM(%s)) COLLATE utf8mb4_unicode_ci
          AND UPPER(TRIM(IFNULL(partner_type, ''))) COLLATE utf8mb4_unicode_ci = 'HOSPITAL'
        ORDER BY short_name ASC
        LIMIT 1
    """
    cursor.execute(sql, (hospital_shrtnm,))
    return cursor.fetchone() or {}


def _fetch_hospital_email_candidates(cursor):
    sql = """
        SELECT
            IFNULL(short_name, '') AS short_name,
            IFNULL(partner_type, '') AS partner_type,
            IFNULL(partner_email, '') AS partner_email
        FROM partner_reg
        WHERE UPPER(TRIM(IFNULL(partner_type, ''))) COLLATE utf8mb4_unicode_ci = 'HOSPITAL'
          AND IFNULL(TRIM(short_name), '') <> ''
        ORDER BY short_name ASC
    """
    cursor.execute(sql)
    return cursor.fetchall() or []


def _pick_best_hospital_candidate(hospital_shrtnm, candidates):
    target_raw = _safe_str(hospital_shrtnm)
    target_norm = _normalize_hospital_key(target_raw)

    if not target_norm:
        return {}

    exact_norm_matches = []
    prefix_matches = []
    token_matches = []

    for row in candidates or []:
        short_name = _safe_str(row.get("short_name"))
        partner_email = _safe_str(row.get("partner_email"))
        if not short_name or not partner_email:
            continue

        short_norm = _normalize_hospital_key(short_name)

        if not short_norm:
            continue

        if short_norm == target_norm:
            exact_norm_matches.append(row)
            continue

        if short_norm.startswith(target_norm) or target_norm.startswith(short_norm):
            prefix_matches.append(row)
            continue

        if target_norm in short_norm or short_norm in target_norm:
            token_matches.append(row)

    if exact_norm_matches:
        return exact_norm_matches[0]

    if prefix_matches:
        prefix_matches.sort(key=lambda row: len(_normalize_hospital_key(row.get("short_name"))))
        return prefix_matches[0]

    if token_matches:
        token_matches.sort(key=lambda row: len(_normalize_hospital_key(row.get("short_name"))))
        return token_matches[0]

    return {}


def get_hospital_email_by_short_name(hospital_shrtnm):
    hospital_shrtnm = _safe_str(hospital_shrtnm)

    if not hospital_shrtnm:
        return {
            "success": False,
            "hospital_email": "",
            "hospital_shrtnm": hospital_shrtnm,
            "message": "Hospital short name is required.",
        }

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        if not _table_has_required_partner_email_columns(cursor):
            return {
                "success": False,
                "hospital_email": "",
                "hospital_shrtnm": hospital_shrtnm,
                "message": "Required partner_reg columns short_name / partner_type / partner_email are not available.",
            }

        exact_row = _fetch_hospital_email_exact(cursor, hospital_shrtnm)
        hospital_email = _safe_str(exact_row.get("hospital_email"))

        if hospital_email:
            return {
                "success": True,
                "hospital_email": hospital_email,
                "hospital_shrtnm": hospital_shrtnm,
                "matched_short_name": _safe_str(exact_row.get("matched_short_name")),
                "matched_partner_type": _safe_str(exact_row.get("matched_partner_type")),
                "match_type": "EXACT",
                "message": "Hospital email fetched successfully.",
            }

        candidates = _fetch_hospital_email_candidates(cursor)
        best_row = _pick_best_hospital_candidate(hospital_shrtnm, candidates)
        fallback_email = _safe_str(best_row.get("partner_email"))

        if fallback_email:
            return {
                "success": True,
                "hospital_email": fallback_email,
                "hospital_shrtnm": hospital_shrtnm,
                "matched_short_name": _safe_str(best_row.get("short_name")),
                "matched_partner_type": _safe_str(best_row.get("partner_type")),
                "match_type": "FALLBACK",
                "message": "Hospital email fetched successfully using fallback hospital-name matching.",
            }

        return {
            "success": False,
            "hospital_email": "",
            "hospital_shrtnm": hospital_shrtnm,
            "match_type": "NONE",
            "message": "Hospital email not found.",
        }

    except Exception as e:
        return {
            "success": False,
            "hospital_email": "",
            "hospital_shrtnm": hospital_shrtnm,
            "message": str(e),
        }

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_active_email_templates():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                id,
                template_code,
                template_name,
                template_subject,
                template_body,
                template_type,
                template_status,
                template_language,
                template_channel,
                template_category,
                template_description,
                version_no,
                is_default,
                remarks,
                created_by,
                created_on,
                updated_by,
                updated_on
            FROM email_template_master
            WHERE UPPER(TRIM(IFNULL(template_status, ''))) = 'ACTIVE'
              AND (
                    template_channel IS NULL
                    OR TRIM(template_channel) = ''
                    OR UPPER(TRIM(template_channel)) = 'EMAIL'
                  )
            ORDER BY
                CASE WHEN IFNULL(is_default, 0) = 1 THEN 0 ELSE 1 END,
                template_name ASC,
                id DESC
            """
        )

        return cursor.fetchall() or []

    except Exception:
        return []

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_email_template_by_name(template_name):
    template_name = _safe_str(template_name)

    if not template_name:
        return {}

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                id,
                template_code,
                template_name,
                template_subject,
                template_body,
                template_type,
                template_status,
                template_language,
                template_channel,
                template_category,
                template_description,
                version_no,
                is_default,
                remarks,
                created_by,
                created_on,
                updated_by,
                updated_on
            FROM email_template_master
            WHERE UPPER(TRIM(IFNULL(template_name, ''))) = UPPER(TRIM(%s))
              AND UPPER(TRIM(IFNULL(template_status, ''))) = 'ACTIVE'
              AND (
                    template_channel IS NULL
                    OR TRIM(template_channel) = ''
                    OR UPPER(TRIM(template_channel)) = 'EMAIL'
                  )
            ORDER BY
                CASE WHEN IFNULL(is_default, 0) = 1 THEN 0 ELSE 1 END,
                id DESC
            LIMIT 1
            """,
            (template_name,),
        )

        return cursor.fetchone() or {}

    except Exception:
        return {}

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_active_email_placeholder_mappings():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                placeholder_key,
                placeholder_label,
                source_table,
                source_column,
                module_name,
                data_type,
                sample_value,
                status
            FROM email_template_placeholder_master
            WHERE UPPER(TRIM(IFNULL(status, ''))) = 'ACTIVE'
              AND IFNULL(TRIM(placeholder_key), '') <> ''
              AND IFNULL(TRIM(source_table), '') <> ''
              AND IFNULL(TRIM(source_column), '') <> ''
            ORDER BY placeholder_key
            """
        )

        return cursor.fetchall() or []

    except Exception:
        return []

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_thread_attachments(channel_thread_id=None, operation_queue_id=None):
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        if _safe_str(channel_thread_id):
            cursor.execute(
                """
                SELECT
                    a.id,
                    a.operation_queue_id,
                    oq.channel_thread_id,
                    a.channel_message_log_id,
                    a.channel_message_id,
                    a.file_name,
                    a.file_ext,
                    a.mime_type,
                    a.file_size,
                    a.file_path,
                    a.remarks,
                    a.created_on,
                    a.updated_on
                FROM operation_queue_attachment a
                INNER JOIN operation_queue_master oq
                    ON oq.id = a.operation_queue_id
                WHERE oq.channel_thread_id = %s
                ORDER BY a.created_on DESC, a.id DESC
                """,
                (channel_thread_id,),
            )
        elif _safe_str(operation_queue_id):
            cursor.execute(
                """
                SELECT
                    a.id,
                    a.operation_queue_id,
                    oq.channel_thread_id,
                    a.channel_message_log_id,
                    a.channel_message_id,
                    a.file_name,
                    a.file_ext,
                    a.mime_type,
                    a.file_size,
                    a.file_path,
                    a.remarks,
                    a.created_on,
                    a.updated_on
                FROM operation_queue_attachment a
                LEFT JOIN operation_queue_master oq
                    ON oq.id = a.operation_queue_id
                WHERE a.operation_queue_id = %s
                ORDER BY a.created_on DESC, a.id DESC
                """,
                (operation_queue_id,),
            )
        else:
            return []

        rows = cursor.fetchall() or []

        for row in rows:
            row["file_url"] = _build_browser_file_url(row.get("file_path"))

        return rows

    except Exception as e:
        print("get_thread_attachments error:", e)
        return []

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def get_available_thread_attachments(
    channel_thread_id=None,
    operation_queue_id=None,
):
    """
    Return only attachments whose physical files are available on the server.

    get_thread_attachments() remains unchanged so existing thread-history and
    audit behaviour is preserved. This filtered function is intended only for
    selectable attachments in the Email Communication window.
    """
    attachment_rows = get_thread_attachments(
        channel_thread_id=channel_thread_id,
        operation_queue_id=operation_queue_id,
    )

    available_attachments = []

    for attachment in attachment_rows or []:
        attachment = dict(attachment or {})

        stored_file_path = _safe_str(attachment.get("file_path"))
        actual_path = _normalize_filesystem_path(stored_file_path)

        if not actual_path or not _is_allowed_attachment_file(actual_path):
            continue

        attachment["resolved_file_path"] = actual_path
        attachment["file_available"] = True
        attachment["file_url"] = _build_browser_file_url(stored_file_path)

        available_attachments.append(attachment)

    return available_attachments


def _normalize_date_for_template(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return _safe_str(value)


def _normalize_placeholder_value(value):
    if value is None:
        return ""
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    return _safe_str(value)


def _get_value_from_row_by_column_name(row, source_column):
    row = row or {}
    source_column = _safe_str(source_column)

    if not source_column:
        return ""

    if source_column in row:
        return row.get(source_column)

    normalized_source_column = _normalize_key(source_column)
    if not normalized_source_column:
        return ""

    for key, value in row.items():
        if _normalize_key(key) == normalized_source_column:
            return value

    return ""


def _fetch_partner_row_for_mapping(cursor, latest_queue_row):
    latest_queue_row = latest_queue_row or {}

    hospital_shrtnm = _safe_str(latest_queue_row.get("hospital_shrtnm"))
    agent_shrtnm = _safe_str(latest_queue_row.get("agent_shrtnm"))

    if hospital_shrtnm:
        try:
            cursor.execute(
                """
                SELECT *
                FROM partner_reg
                WHERE UPPER(TRIM(IFNULL(short_name, ''))) = UPPER(TRIM(%s))
                  AND UPPER(TRIM(IFNULL(partner_type, ''))) = 'HOSPITAL'
                ORDER BY id DESC
                LIMIT 1
                """,
                (hospital_shrtnm,),
            )
            row = cursor.fetchone() or {}
            if row:
                return row
        except Exception:
            pass

    if agent_shrtnm:
        try:
            cursor.execute(
                """
                SELECT *
                FROM partner_reg
                WHERE UPPER(TRIM(IFNULL(short_name, ''))) = UPPER(TRIM(%s))
                  AND UPPER(TRIM(IFNULL(partner_type, ''))) = 'AGENCY'
                ORDER BY id DESC
                LIMIT 1
                """,
                (agent_shrtnm,),
            )
            row = cursor.fetchone() or {}
            if row:
                return row
        except Exception:
            pass

    return {}


def _get_value_from_source_table(cursor, source_table, source_column, latest_queue_row):
    source_table = _safe_str(source_table).lower()
    source_column = _safe_str(source_column)

    if not source_table or not source_column:
        return ""

    if source_table == "operation_queue_master":
        return _normalize_placeholder_value(
            _get_value_from_row_by_column_name(latest_queue_row, source_column)
        )

    if source_table == "partner_reg":
        partner_row = _fetch_partner_row_for_mapping(cursor, latest_queue_row)
        return _normalize_placeholder_value(
            _get_value_from_row_by_column_name(partner_row, source_column)
        )

    return ""


def _build_email_template_context(queue_row):
    queue_row = queue_row or {}
    channel_thread_id = _safe_str(queue_row.get("channel_thread_id"))

    if not channel_thread_id:
        return {}

    mappings = get_active_email_placeholder_mappings()
    if not mappings:
        return {}

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        latest_queue_row = _fetch_latest_operation_queue_row_by_thread_id(cursor, channel_thread_id)
        if not latest_queue_row:
            latest_queue_row = queue_row

        context = {}

        for mapping in mappings:
            placeholder_key = _safe_str(mapping.get("placeholder_key"))
            source_table = _safe_str(mapping.get("source_table"))
            source_column = _safe_str(mapping.get("source_column"))

            if not placeholder_key or not source_table or not source_column:
                continue

            value = _get_value_from_source_table(
                cursor=cursor,
                source_table=source_table,
                source_column=source_column,
                latest_queue_row=latest_queue_row,
            )

            context[placeholder_key] = value

        context["channel_thread_id"] = _safe_str(latest_queue_row.get("channel_thread_id"))
        context["channel_message_id"] = _safe_str(latest_queue_row.get("channel_message_id"))
        context["channel_code"] = _safe_str(latest_queue_row.get("channel_code"))
        context["assigned_to"] = _safe_str(latest_queue_row.get("assigned_to"))
        context["assigned_by"] = _safe_str(latest_queue_row.get("assigned_by"))
        context["assigned_at"] = _normalize_date_for_template(latest_queue_row.get("assigned_at"))
        context["wrkflw_status"] = _safe_str(latest_queue_row.get("wrkflw_status"))
        context["priority_status"] = _safe_str(latest_queue_row.get("priority_status"))
        context["parse_status"] = _safe_str(latest_queue_row.get("parse_status"))
        context["parse_remarks"] = _safe_str(latest_queue_row.get("parse_remarks"))

        # ------------------------------------------------------------
        # DERIVED / CALCULATED PLACEHOLDERS
        # ------------------------------------------------------------
        # patient_full_name is not a physical DB column.
        # Placeholder mapping may show it as Derived(patgiven_nm + patsur_nm),
        # so we calculate it here without changing the existing placeholder flow.
        patient_given_name = _safe_str(latest_queue_row.get("patgiven_nm"))
        patient_short_name = _safe_str(latest_queue_row.get("patsur_nm"))
        context["patient_full_name"] = (patient_given_name + " " + patient_short_name).strip()

        return context

    except Exception:
        return {}

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _render_email_template_text(template_text, context):
    template_text = template_text or ""
    context = context or {}

    try:
        return ImmutableSandboxedEnvironment(autoescape=False).from_string(template_text).render(**context)
    except Exception:
        return template_text


def _remove_unresolved_template_tokens(text):
    text = text or ""
    return re.sub(r"\{\{\s*[^{}]+\s*\}\}", "", text)


def _clean_rendered_subject(text):
    text = _remove_unresolved_template_tokens(text)
    text = text.replace("\r", " ").replace("\n", " ")
    text = re.sub(r"\s*\|\|\s*", " || ", text)
    text = re.sub(r"(\s*\|\|\s*){2,}", " || ", text)
    text = re.sub(r"^\s*(\|\|)+\s*", "", text)
    text = re.sub(r"\s*(\|\|)+\s*$", "", text)
    text = re.sub(r"\s{2,}", " ", text)
    return text.strip(" |")


def _should_keep_email_body_line(line):
    stripped = _safe_str(line)
    if not stripped:
        return False

    if re.fullmatch(r"[\|\-:_.,/\\\s]+", stripped):
        return False

    if ":" in stripped:
        left, right = stripped.split(":", 1)
        if _safe_str(left) and not _safe_str(right):
            return False

    return True


def _clean_rendered_body(text):
    text = _remove_unresolved_template_tokens(text or "")
    text = text.replace("\r\n", "\n").replace("\r", "\n")

    cleaned_lines = []
    blank_streak = 0

    for raw_line in text.split("\n"):
        line = raw_line.rstrip()

        if not _should_keep_email_body_line(line):
            continue

        if not _safe_str(line):
            blank_streak += 1
            if blank_streak <= 1:
                cleaned_lines.append("")
            continue

        blank_streak = 0
        cleaned_lines.append(line)

    cleaned_text = "\n".join(cleaned_lines)
    cleaned_text = re.sub(r"\n{3,}", "\n\n", cleaned_text)

    return cleaned_text.strip()


_PRIVATE_WHATSAPP_PREFIX = "private/whatsapp_docs/"

# Fail closed for email attachments. Macro-enabled / executable / script
# formats are intentionally not accepted in the medical-document flow.
_ALLOWED_EMAIL_ATTACHMENT_EXTENSIONS = {
    ".pdf",
    ".jpg",
    ".jpeg",
    ".png",
    ".webp",
    ".gif",
    ".bmp",
    ".tif",
    ".tiff",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".csv",
    ".txt",
    ".rtf",
    ".dcm",
}


def _trusted_attachment_roots():
    """
    Physical roots from which Operation Queue email attachments may be read.

    Private WhatsApp medical documents are stored outside Flask static content:
        <instance_path>/uploads/whatsapp_docs/

    Legacy TERIQEN attachments may still exist under:
        <app_root>/static/uploads/

    No project-wide or arbitrary absolute filesystem access is permitted.
    """
    app_root = os.path.realpath(current_app.root_path)
    project_root = os.path.realpath(os.path.dirname(app_root))

    roots = [
        os.path.realpath(
            os.path.join(current_app.instance_path, "uploads", "whatsapp_docs")
        ),
        os.path.realpath(
            os.path.join(app_root, "static", "uploads")
        ),
        os.path.realpath(
            os.path.join(project_root, "app", "static", "uploads")
        ),
    ]

    unique_roots = []
    seen = set()
    for root in roots:
        key = os.path.normcase(root)
        if key not in seen:
            seen.add(key)
            unique_roots.append(root)

    return unique_roots


def _is_path_within_root(candidate_path, root_path):
    """
    Return True only when candidate_path resolves inside root_path.
    realpath() also protects against symlink-based escape.
    """
    try:
        candidate_real = os.path.realpath(candidate_path)
        root_real = os.path.realpath(root_path)

        return (
            os.path.commonpath([candidate_real, root_real])
            == root_real
        )
    except (ValueError, OSError):
        return False


def _sanitize_attachment_filename(file_name, fallback_path=""):
    """
    Keep only a basename for mail metadata and remove CR/LF/NUL characters.
    This prevents path text or control characters from becoming an email
    attachment filename.
    """
    raw_name = _safe_str(file_name)

    if not raw_name:
        raw_name = os.path.basename(
            _safe_str(fallback_path).replace("\\", "/")
        )

    raw_name = raw_name.replace("\\", "/").split("/")[-1]
    raw_name = (
        raw_name
        .replace("\x00", "")
        .replace("\r", "")
        .replace("\n", "")
        .strip()
    )

    if not raw_name:
        return "attachment"

    return raw_name[:255]


def _is_allowed_attachment_file(actual_path):
    """
    Defence-in-depth validation after path resolution.
    """
    actual_path = _safe_str(actual_path)

    if not actual_path or not os.path.isfile(actual_path):
        return False

    extension = os.path.splitext(actual_path)[1].lower()
    if extension not in _ALLOWED_EMAIL_ATTACHMENT_EXTENSIONS:
        return False

    try:
        # Do not send empty placeholder files.
        if os.path.getsize(actual_path) <= 0:
            return False
    except OSError:
        return False

    return True


def _resolve_private_whatsapp_path(raw_path):
    """
    Strict logical-to-physical mapping:

        private/whatsapp_docs/<relative>
            ->
        <instance_path>/uploads/whatsapp_docs/<relative>

    The database never needs to contain or expose the physical server path.
    """
    normalized_slash = _safe_str(raw_path).replace("\\", "/")

    if "\x00" in normalized_slash:
        return ""

    lower_path = normalized_slash.lower()
    if not lower_path.startswith(_PRIVATE_WHATSAPP_PREFIX):
        return ""

    relative = normalized_slash[len(_PRIVATE_WHATSAPP_PREFIX):].lstrip("/")
    if not relative:
        return ""

    parts = [part for part in relative.split("/") if part]
    if not parts or any(part in (".", "..") for part in parts):
        return ""

    relative_native = os.path.join(*parts)

    # A drive-qualified or absolute "relative" value must never escape the root.
    if os.path.isabs(relative_native) or os.path.splitdrive(relative_native)[0]:
        return ""

    private_root = os.path.realpath(
        os.path.join(
            current_app.instance_path,
            "uploads",
            "whatsapp_docs",
        )
    )
    candidate = os.path.realpath(
        os.path.join(private_root, relative_native)
    )

    if not _is_path_within_root(candidate, private_root):
        return ""

    if not _is_allowed_attachment_file(candidate):
        return ""

    return candidate


def _normalize_filesystem_path(file_path):
    """
    Resolve an attachment only from approved TERIQEN storage roots.

    Security properties:
    - private/whatsapp_docs is mapped only to instance/uploads/whatsapp_docs
    - path traversal is rejected
    - arbitrary absolute paths are rejected unless already inside an approved root
    - arbitrary project/app relative paths are not searched
    - only approved medical/business attachment extensions are accepted
    """
    raw_path = _safe_str(file_path)
    if not raw_path or "\x00" in raw_path:
        return ""

    normalized_slash = raw_path.replace("\\", "/")
    lower_raw = normalized_slash.lower()

    # Private patient documents use an exact logical prefix.
    if lower_raw.startswith(_PRIVATE_WHATSAPP_PREFIX):
        return _resolve_private_whatsapp_path(normalized_slash)

    # If the private marker appears anywhere other than the beginning,
    # fail closed instead of trying alternate filesystem locations.
    if _PRIVATE_WHATSAPP_PREFIX in lower_raw:
        return ""

    trusted_roots = _trusted_attachment_roots()
    candidates = []

    normalized_native = normalized_slash.replace("/", os.sep)

    # Existing absolute legacy path: only if it is inside a trusted root.
    if os.path.isabs(normalized_native):
        candidates.append(os.path.realpath(normalized_native))

    app_root = os.path.realpath(current_app.root_path)
    project_root = os.path.realpath(os.path.dirname(app_root))

    # Supported legacy DB aliases. Do not fall back to project-wide searches.
    if lower_raw.startswith("uploads/"):
        relative = normalized_slash[len("uploads/"):]
        candidates.append(
            os.path.realpath(
                os.path.join(app_root, "static", "uploads", relative.replace("/", os.sep))
            )
        )

    elif lower_raw.startswith("static/uploads/"):
        relative = normalized_slash[len("static/uploads/"):]
        candidates.append(
            os.path.realpath(
                os.path.join(app_root, "static", "uploads", relative.replace("/", os.sep))
            )
        )

    elif lower_raw.startswith("app/static/uploads/"):
        relative = normalized_slash[len("app/static/uploads/"):]
        candidates.append(
            os.path.realpath(
                os.path.join(project_root, "app", "static", "uploads", relative.replace("/", os.sep))
            )
        )

    seen = set()
    for candidate in candidates:
        key = os.path.normcase(candidate)
        if key in seen:
            continue
        seen.add(key)

        if not any(
            _is_path_within_root(candidate, root)
            for root in trusted_roots
        ):
            continue

        if _is_allowed_attachment_file(candidate):
            return candidate

    return ""


def _attach_files_to_message(email_message, attachment_rows):
    attached_files = []
    missing_files = []

    for attachment in attachment_rows or []:
        stored_path = _safe_str(attachment.get("file_path"))
        file_name = _sanitize_attachment_filename(
            attachment.get("file_name"),
            fallback_path=stored_path,
        )
        mime_type = _safe_str(attachment.get("mime_type"))

        actual_path = _normalize_filesystem_path(stored_path)
        if not actual_path or not _is_allowed_attachment_file(actual_path):
            missing_files.append({
                "id": attachment.get("id"),
                "file_name": file_name,
                "file_path": stored_path,
                "reason": "Attachment unavailable or blocked by security validation.",
            })
            continue

        detected_mime, _ = mimetypes.guess_type(actual_path)
        final_mime = detected_mime or mime_type or "application/octet-stream"

        if "/" in final_mime:
            maintype, subtype = final_mime.split("/", 1)
        else:
            maintype, subtype = "application", "octet-stream"

        with open(actual_path, "rb") as fp:
            email_message.add_attachment(
                fp.read(),
                maintype=maintype,
                subtype=subtype,
                filename=file_name,
            )

        # Return only the logical DB path in response metadata.
        # The physical server path remains internal.
        attached_files.append({
            "id": attachment.get("id"),
            "file_name": file_name,
            "file_path": stored_path,
        })

    return attached_files, missing_files


def _collect_attachment_files(attachment_rows):
    """
    Build the internal attachment list used by SMTP / Microsoft Graph.

    Physical paths are resolved server-side only and are never taken from
    browser input.
    """
    attached_files = []
    missing_files = []

    for attachment in attachment_rows or []:
        stored_path = _safe_str(attachment.get("file_path"))
        file_name = _sanitize_attachment_filename(
            attachment.get("file_name"),
            fallback_path=stored_path,
        )
        mime_type = _safe_str(attachment.get("mime_type"))

        actual_path = _normalize_filesystem_path(stored_path)
        if not actual_path or not _is_allowed_attachment_file(actual_path):
            missing_files.append({
                "id": attachment.get("id"),
                "file_name": file_name,
                "file_path": stored_path,
                "reason": "Attachment unavailable or blocked by security validation.",
            })
            continue

        detected_mime, _ = mimetypes.guess_type(actual_path)
        final_mime = detected_mime or mime_type or "application/octet-stream"

        attached_files.append({
            "id": attachment.get("id"),
            "file_name": file_name,
            "file_path": actual_path,  # internal only
            "mime_type": final_mime,
        })

    return attached_files, missing_files


def _build_graph_file_attachments(attached_files):
    graph_attachments = []

    for file_info in attached_files or []:
        requested_path = _safe_str(file_info.get("file_path"))
        file_name = _sanitize_attachment_filename(
            file_info.get("file_name"),
            fallback_path=requested_path,
        )
        mime_type = _safe_str(file_info.get("mime_type")) or "application/octet-stream"

        file_path = _normalize_filesystem_path(requested_path)
        if not file_path or not _is_allowed_attachment_file(file_path):
            continue

        with open(file_path, "rb") as fp:
            file_content = fp.read()

        graph_attachments.append({
            "@odata.type": "#microsoft.graph.fileAttachment",
            "name": file_name,
            "contentType": mime_type,
            "contentBytes": base64.b64encode(file_content).decode("utf-8"),
        })

    return graph_attachments


def _split_email_addresses(value):
    raw = _safe_str(value)
    if not raw:
        return []

    normalized = raw.replace(";", ",").replace("|", ",")
    parts = [item.strip() for item in normalized.split(",") if item and item.strip()]

    unique_values = []
    seen = set()
    for item in parts:
        key = item.lower()
        if key not in seen:
            seen.add(key)
            unique_values.append(item)

    return unique_values


def _build_graph_recipients(value):
    recipients = []
    for email in _split_email_addresses(value):
        recipients.append({
            "emailAddress": {
                "address": email
            }
        })
    return recipients


def _get_mail_backend():
    backend = _safe_str(current_app.config.get("MAIL_BACKEND")).upper()
    return backend or "SMTP"


def _get_default_sender_email():
    return (
        current_app.config.get("MS_GRAPH_USER_EMAIL")
        or current_app.config.get("GRAPH_SENDER_EMAIL")
        or current_app.config.get("MAIL_DEFAULT_SENDER")
        or current_app.config.get("MAIL_USERNAME")
        or current_app.config.get("SMTP_USERNAME")
        or current_app.config.get("EMAIL_FROM")
        or ""
    )


def _graph_get_access_token():
    tenant_id = _safe_str(current_app.config.get("MS_TENANT_ID"))
    client_id = _safe_str(current_app.config.get("MS_CLIENT_ID"))
    client_secret = _safe_str(current_app.config.get("MS_CLIENT_SECRET"))
    scope_value = _safe_str(current_app.config.get("MS_GRAPH_SCOPES")) or "https://graph.microsoft.com/.default"

    if not tenant_id:
        return {
            "success": False,
            "message": "MS_TENANT_ID is not configured.",
            "access_token": "",
        }

    if not client_id:
        return {
            "success": False,
            "message": "MS_CLIENT_ID is not configured.",
            "access_token": "",
        }

    if not client_secret:
        return {
            "success": False,
            "message": "MS_CLIENT_SECRET is not configured.",
            "access_token": "",
        }

    token_url = f"https://login.microsoftonline.com/{tenant_id}/oauth2/v2.0/token"

    payload = urlencode({
        "client_id": client_id,
        "client_secret": client_secret,
        "scope": scope_value,
        "grant_type": "client_credentials",
    }).encode("utf-8")

    request_obj = Request(
        token_url,
        data=payload,
        headers={
            "Content-Type": "application/x-www-form-urlencoded",
        },
        method="POST",
    )

    try:
        with urlopen(request_obj, timeout=30) as response:
            raw_data = response.read().decode("utf-8")
            parsed = json.loads(raw_data or "{}")
            access_token = _safe_str(parsed.get("access_token"))

            if not access_token:
                return {
                    "success": False,
                    "message": parsed.get("error_description") or "Access token not returned by Microsoft Graph token endpoint.",
                    "access_token": "",
                }

            return {
                "success": True,
                "message": "Access token fetched successfully.",
                "access_token": access_token,
            }

    except HTTPError as e:
        try:
            error_body = e.read().decode("utf-8")
            parsed = json.loads(error_body or "{}")
            message = (
                parsed.get("error_description")
                or parsed.get("error", {}).get("message")
                or error_body
                or str(e)
            )
        except Exception:
            message = str(e)

        return {
            "success": False,
            "message": message,
            "access_token": "",
        }

    except URLError as e:
        return {
            "success": False,
            "message": str(e),
            "access_token": "",
        }

    except Exception as e:
        return {
            "success": False,
            "message": str(e),
            "access_token": "",
        }


def _send_email_via_graph(
    sender_email,
    to_email,
    cc_email,
    bcc_email,
    rendered_subject,
    rendered_body,
    attached_files,
):
    sender_email = _safe_str(sender_email)

    if not sender_email:
        return {
            "success": False,
            "message": "Microsoft Graph sender email is not configured.",
        }

    token_result = _graph_get_access_token()
    if not token_result.get("success"):
        return {
            "success": False,
            "message": token_result.get("message") or "Failed to acquire Microsoft Graph access token.",
        }

    access_token = token_result.get("access_token") or ""
    graph_attachments = _build_graph_file_attachments(attached_files)

    # Keep a clear separation between the TERIQEN message body and any
    # server-side Microsoft 365 / Exchange disclaimer appended after send.
    # Existing body formatting is preserved; only a trailing blank line is added.
    graph_body_html = (rendered_body or "").replace("\r\n", "\n").replace("\r", "\n")
    graph_body_html = graph_body_html.rstrip("\n").replace("\n", "<br>")
    if graph_body_html:
        graph_body_html += "<br><br>"

    graph_message = {
        "subject": rendered_subject or "",
        "body": {
            "contentType": "HTML",
            "content": graph_body_html
        },
        "toRecipients": _build_graph_recipients(to_email),
        "ccRecipients": _build_graph_recipients(cc_email),
        "bccRecipients": _build_graph_recipients(bcc_email),
    }

    if graph_attachments:
        graph_message["attachments"] = graph_attachments

    request_body = json.dumps({
        "message": graph_message,
        "saveToSentItems": True,
    }).encode("utf-8")

    graph_url = f"https://graph.microsoft.com/v1.0/users/{quote(sender_email)}/sendMail"

    request_obj = Request(
        graph_url,
        data=request_body,
        headers={
            "Authorization": f"Bearer {access_token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )

    try:
        with urlopen(request_obj, timeout=60) as response:
            status_code = getattr(response, "status", None) or response.getcode()
            if status_code not in (200, 201, 202):
                return {
                    "success": False,
                    "message": f"Microsoft Graph sendMail returned unexpected status code {status_code}.",
                }

        return {
            "success": True,
            "message": "Email sent successfully.",
        }

    except HTTPError as e:
        try:
            error_body = e.read().decode("utf-8")
            parsed = json.loads(error_body or "{}")
            message = (
                parsed.get("error", {}).get("message")
                or parsed.get("error_description")
                or error_body
                or str(e)
            )
        except Exception:
            message = str(e)

        return {
            "success": False,
            "message": message,
        }

    except URLError as e:
        return {
            "success": False,
            "message": str(e),
        }

    except Exception as e:
        return {
            "success": False,
            "message": str(e),
        }
