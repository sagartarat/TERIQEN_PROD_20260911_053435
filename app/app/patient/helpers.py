import io
import mimetypes
from flask import current_app
import re
from datetime import date, datetime
from typing import Any, Dict, List, Optional

from flask import abort, request, send_file, url_for
from mysql.connector.connection import MySQLConnection
from werkzeug.datastructures import FileStorage

from app.common.file_validation import validate_document_bytes
from app.common.db import get_db


def _safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def validate_passport_expiry(
    value: Any,
    field_label: str = "Passport Expiry Date",
) -> Dict[str, Any]:
    """
    Validate that a passport expiry date is strictly later than today.

    Returns:
        {
            "success": bool,
            "date": datetime.date | None,
            "message": str,
        }
    """
    if value is None or _safe_str(value) == "":
        return {
            "success": False,
            "date": None,
            "message": f"{field_label} is required.",
        }

    parsed_date = None

    if isinstance(value, datetime):
        parsed_date = value.date()
    elif isinstance(value, date):
        parsed_date = value
    else:
        raw_value = _safe_str(value)
        for date_format in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                parsed_date = datetime.strptime(raw_value, date_format).date()
                break
            except ValueError:
                continue

    if parsed_date is None:
        return {
            "success": False,
            "date": None,
            "message": (
                f"{field_label} is invalid. Please enter a valid date "
                "in DD-MM-YYYY format."
            ),
        }

    today = date.today()
    display_date = parsed_date.strftime("%d-%m-%Y")

    if parsed_date < today:
        expired_days = (today - parsed_date).days
        return {
            "success": False,
            "date": parsed_date,
            "message": (
                f"Passport expired on {display_date} "
                f"({expired_days} day(s) ago). Please enter a passport "
                "expiry date later than today."
            ),
        }

    if parsed_date == today:
        return {
            "success": False,
            "date": parsed_date,
            "message": (
                f"Passport expires today ({display_date}). "
                "Passport Expiry Date must be later than today."
            ),
        }

    return {
        "success": True,
        "date": parsed_date,
        "message": "",
    }



def validate_patient_dob(
    value: Any,
    field_label: str = "Patient D.O.B.",
) -> Dict[str, Any]:
    """
    Validate that the patient's date of birth is strictly earlier than today.

    Returns:
        {
            "success": bool,
            "date": datetime.date | None,
            "message": str,
        }
    """
    if value is None or _safe_str(value) == "":
        return {
            "success": False,
            "date": None,
            "message": f"{field_label} is required.",
        }

    parsed_date = None

    if isinstance(value, datetime):
        parsed_date = value.date()
    elif isinstance(value, date):
        parsed_date = value
    else:
        raw_value = _safe_str(value)
        for date_format in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y"):
            try:
                parsed_date = datetime.strptime(raw_value, date_format).date()
                break
            except ValueError:
                continue

    if parsed_date is None:
        return {
            "success": False,
            "date": None,
            "message": (
                f"{field_label} is invalid. Please enter a valid date "
                "in DD-MM-YYYY format."
            ),
        }

    today = date.today()
    display_date = parsed_date.strftime("%d-%m-%Y")

    if parsed_date == today:
        return {
            "success": False,
            "date": parsed_date,
            "message": (
                f"Patient D.O.B. cannot be today ({display_date}). "
                "Patient D.O.B. must be earlier than today."
            ),
        }

    if parsed_date > today:
        return {
            "success": False,
            "date": parsed_date,
            "message": (
                f"Patient D.O.B. cannot be a future date ({display_date}). "
                "Please enter a date earlier than today."
            ),
        }

    return {
        "success": True,
        "date": parsed_date,
        "message": "",
    }

def _normalize_gender(value: Any) -> str:
    val = _safe_str(value).strip().lower()

    if val in ("m", "male"):
        return "Male"
    if val in ("f", "female"):
        return "Female"
    if val in ("o", "other", "others"):
        return "Others"

    return ""


def _normalize_country_value(value: Any) -> str:
    """
    Normalize country text so that DB-stored uppercase values like:
        BANGLADESH
        UNITED ARAB EMIRATES
        SAUDI_ARABIA
    can match UI dropdown option values like:
        Bangladesh
        United Arab Emirates
        Saudi Arabia
    """
    val = _safe_str(value)
    if not val:
        return ""

    val = val.replace("_", " ").replace("-", " ")
    val = re.sub(r"\s+", " ", val).strip()

    if val == val.upper():
        val = val.title()

    return val


def _normalize_patient_row(row: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    if not row:
        return None

    for k in list(row.keys()):
        if row[k] is None:
            row[k] = ""

    row["patient_country"] = _normalize_country_value(row.get("patient_country"))
    row["psprtissue_country"] = _normalize_country_value(row.get("psprtissue_country"))
    row["patient_gender"] = _normalize_gender(row.get("patient_gender"))

    return row


def fetch_patient_by_passport(conn, passport_no: str):
    passport_no = _safe_str(passport_no)
    if not passport_no:
        return None

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT
                passport_no,
                patient_givennm,
                patient_surnm,
                patient_country,
                patient_contno,
                patient_email,
                patient_dob,
                patient_address,
                passport_expirydt,
                psprtissue_country,
                patient_gender,
                patient_status
            FROM patient_create
            WHERE TRIM(passport_no) = %s
            LIMIT 1
            """,
            (passport_no,),
        )
        row = cur.fetchone()
        return _normalize_patient_row(row)
    finally:
        cur.close()


def fetch_patient_docs(conn, passport_no: str):
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """
        SELECT file_name, file_type, file_size
        FROM patientcreate_docs
        WHERE passport_no = %s
        ORDER BY file_name ASC
        """,
        (passport_no,),
    )
    rows = cur.fetchall() or []
    cur.close()

    for r in rows:
        r["file_name"] = r.get("file_name") or ""
        r["file_type"] = r.get("file_type") or ""
        r["file_size"] = r.get("file_size") or 0
        r["doc_url"] = url_for("patient_doc_open", passport_no=passport_no, filename=r["file_name"])

    return rows


def patient_doc_open(passport_no, filename):
    passport_no = (passport_no or "").strip()
    filename = (filename or "").strip()

    if not passport_no or not filename:
        abort(404)

    conn = None
    cur = None
    try:
        conn = get_db()
        cur = conn.cursor(dictionary=True)

        cur.execute(
            """
            SELECT file_name, file_type, file_size, document_data
            FROM patientcreate_docs
            WHERE passport_no = %s AND file_name = %s
            ORDER BY file_size DESC, doc_id DESC
            LIMIT 1
            """,
            (passport_no, filename),
        )
        row = cur.fetchone()

        if not row or not row.get("document_data"):
            abort(404)

        file_name = row.get("file_name") or "document"
        mime = row.get("file_type") or mimetypes.guess_type(file_name)[0] or "application/octet-stream"
        data = row["document_data"]

        download_flag = (request.args.get("download") or "").strip()
        inline = False
        if not download_flag:
            if mime.startswith("image/") or mime in ("application/pdf", "text/plain"):
                inline = True

        return send_file(
            io.BytesIO(data),
            mimetype=mime,
            as_attachment=not inline,
            download_name=file_name,
            max_age=0,
        )

    except Exception as e:
        print("patient_doc_open error:", e)
        abort(500)

    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def get_patient_by_passport(
    conn: MySQLConnection,
    passport_no: str,
) -> Optional[Dict[str, Any]]:
    passport_no = _safe_str(passport_no)
    if not passport_no:
        return None

    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT
                passport_no,
                patient_country,
                patient_givennm,
                patient_surnm,
                patient_dob,
                patient_gender,
                patient_address,
                patient_contno,
                passport_expirydt,
                patient_status,
                entered_by,
                entered_date,
                psprtissue_country,
                patient_email
            FROM patient_create
            WHERE TRIM(passport_no) = %s
            LIMIT 1
            """,
            (passport_no,),
        )
        row = cursor.fetchone()
        return _normalize_patient_row(row)
    finally:
        cursor.close()


def insert_patient_docs(
    conn: MySQLConnection,
    passport_no: str,
    uploaded_files: Optional[List[FileStorage]] = None,
) -> Dict[str, Any]:
    passport_no = _safe_str(passport_no)
    if not passport_no:
        return {
            "success": False,
            "inserted_count": 0,
            "message": "Passport number is blank.",
        }

    if not uploaded_files:
        return {
            "success": True,
            "inserted_count": 0,
            "message": "No documents to upload.",
        }

    cursor = conn.cursor()
    inserted_count = 0

    try:
        for file_obj in uploaded_files:
            if not file_obj:
                continue

            file_name = _safe_str(getattr(file_obj, "filename", ""))
            if not file_name:
                continue

            document_data = file_obj.read()
            if not document_data:
                continue

            validation = validate_document_bytes(
                file_name,
                document_data,
                allowed_extensions={
                    "pdf", "doc", "docx", "xls", "xlsx",
                    "txt", "jpg", "jpeg", "png", "webp",
                },
                max_bytes=int(current_app.config.get("PATIENT_CREATE_UPLOAD_BYTES") or (2 * 1024 * 1024)),
            )
            file_name = validation["safe_name"]
            file_type = _safe_str(getattr(file_obj, "mimetype", "")) or (
                mimetypes.guess_type(file_name)[0] or "application/octet-stream"
            )
            file_size = validation["size"]

            cursor.execute(
                """
                INSERT INTO patientcreate_docs (
                    passport_no,
                    file_name,
                    file_type,
                    file_size,
                    document_data,
                    uploaded_at
                ) VALUES (%s, %s, %s, %s, %s, NOW())
                """,
                (
                    passport_no,
                    file_name,
                    file_type,
                    file_size,
                    document_data,
                ),
            )
            inserted_count += 1

        return {
            "success": True,
            "inserted_count": inserted_count,
            "message": f"{inserted_count} document(s) uploaded successfully.",
        }
    finally:
        cursor.close()



def _build_patient_create_payload_from_queue_patient(
    queue_row: Dict[str, Any],
    created_by: str = "SYSTEM",
) -> Dict[str, Any]:
    """
    Build patient_create payload for the main patient from operation_queue_master row.
    Existing main patient field mapping is preserved.
    """

    patient_country = _normalize_country_value(queue_row.get("patient_country"))

    return {
        "passport_no": _safe_str(queue_row.get("patpassport_no")),
        "patient_country": patient_country,
        "patient_givennm": _safe_str(queue_row.get("patgiven_nm")),
        "patient_surnm": _safe_str(queue_row.get("patsur_nm")),
        "patient_dob": queue_row.get("patient_dob"),
        "patient_gender": _normalize_gender(queue_row.get("patient_gender")),
        "patient_address": _safe_str(queue_row.get("patient_address")),
        "patient_contno": _safe_str(queue_row.get("patient_contno")),
        "passport_expirydt": queue_row.get("patpass_expirydt"),
        "patient_status": _safe_str(queue_row.get("patient_status")) or "Active",
        "entered_by": _safe_str(created_by) or "SYSTEM",
        "psprtissue_country": _normalize_country_value(queue_row.get("psprtissue_country")) or patient_country,
        "patient_email": _safe_str(queue_row.get("patient_email")),
    }


def _build_patient_create_payload_from_attender(
    queue_row: Dict[str, Any],
    attender_prefix: str,
    created_by: str = "SYSTEM",
) -> Dict[str, Any]:
    """
    Build patient_create payload for Attender 1 / Attender 2 / Attender 3.

    Mapping:
      attnXpassport_no   -> passport_no
      attnX_country      -> patient_country and psprtissue_country
      attnX_givennm      -> patient_givennm
      attnX_surnm        -> patient_surnm
      attnX_dob          -> patient_dob
      attnX_gender       -> patient_gender
      attnX_address      -> patient_address
      attnX_contno       -> patient_contno
      attnXpass_expirydt -> passport_expirydt
      attnX_email        -> patient_email
    """

    prefix = _safe_str(attender_prefix)
    attender_country = _normalize_country_value(queue_row.get(f"{prefix}_country"))

    return {
        "passport_no": _safe_str(queue_row.get(f"{prefix}passport_no")),
        "patient_country": attender_country,
        "patient_givennm": _safe_str(queue_row.get(f"{prefix}_givennm")),
        "patient_surnm": _safe_str(queue_row.get(f"{prefix}_surnm")),
        "patient_dob": queue_row.get(f"{prefix}_dob"),
        "patient_gender": _normalize_gender(queue_row.get(f"{prefix}_gender")),
        "patient_address": _safe_str(queue_row.get(f"{prefix}_address")),
        "patient_contno": _safe_str(queue_row.get(f"{prefix}_contno")),
        "passport_expirydt": queue_row.get(f"{prefix}pass_expirydt"),
        "patient_status": "Active",
        "entered_by": _safe_str(created_by) or "SYSTEM",
        "psprtissue_country": attender_country,
        "patient_email": _safe_str(queue_row.get(f"{prefix}_email")),
    }


def _upsert_patient_create_from_payload(
    conn: MySQLConnection,
    payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Insert one patient_create row if passport does not exist.
    If passport exists, update only non-blank incoming values.

    This helper is used for:
    - main patient
    - attender1
    - attender2
    - attender3

    It preserves the earlier duplicate-prevention behavior based on passport_no.
    """

    payload = payload or {}
    passport_no = _safe_str(payload.get("passport_no"))

    if not passport_no:
        return {
            "success": False,
            "created": False,
            "passport_no": "",
            "message": "Passport number is blank.",
        }

    dob_validation = validate_patient_dob(
        payload.get("patient_dob"),
        field_label=f"Patient D.O.B. for {passport_no}",
    )

    if not dob_validation.get("success"):
        return {
            "success": False,
            "created": False,
            "passport_no": passport_no,
            "message": dob_validation.get("message"),
        }

    # Store a normalized Python date object for reliable MySQL insert/update.
    payload["patient_dob"] = dob_validation.get("date")

    expiry_validation = validate_passport_expiry(
        payload.get("passport_expirydt"),
        field_label=f"Passport Expiry Date for {passport_no}",
    )

    if not expiry_validation.get("success"):
        return {
            "success": False,
            "created": False,
            "passport_no": passport_no,
            "message": expiry_validation.get("message"),
        }

    # Store a normalized Python date object for reliable MySQL insert/update.
    payload["passport_expirydt"] = expiry_validation.get("date")

    existing = get_patient_by_passport(conn, passport_no)

    if existing:
        cursor = conn.cursor()
        try:
            cursor.execute(
                """
                UPDATE patient_create
                SET
                    patient_country = COALESCE(NULLIF(%s, ''), patient_country),
                    patient_givennm = COALESCE(NULLIF(%s, ''), patient_givennm),
                    patient_surnm = COALESCE(NULLIF(%s, ''), patient_surnm),
                    patient_dob = COALESCE(%s, patient_dob),
                    patient_gender = COALESCE(NULLIF(%s, ''), patient_gender),
                    patient_address = COALESCE(NULLIF(%s, ''), patient_address),
                    patient_contno = COALESCE(NULLIF(%s, ''), patient_contno),
                    passport_expirydt = COALESCE(%s, passport_expirydt),
                    psprtissue_country = COALESCE(NULLIF(%s, ''), psprtissue_country),
                    patient_email = COALESCE(NULLIF(%s, ''), patient_email),
                    patient_status = COALESCE(NULLIF(%s, ''), patient_status)
                WHERE TRIM(passport_no) = %s
                """,
                (
                    payload.get("patient_country"),
                    payload.get("patient_givennm"),
                    payload.get("patient_surnm"),
                    payload.get("patient_dob"),
                    payload.get("patient_gender"),
                    payload.get("patient_address"),
                    payload.get("patient_contno"),
                    payload.get("passport_expirydt"),
                    payload.get("psprtissue_country"),
                    payload.get("patient_email"),
                    payload.get("patient_status"),
                    passport_no,
                ),
            )
        finally:
            cursor.close()

        return {
            "success": True,
            "created": False,
            "passport_no": passport_no,
            "message": "Patient already exists and details updated.",
        }

    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            INSERT INTO patient_create (
                passport_no,
                patient_country,
                patient_givennm,
                patient_surnm,
                patient_dob,
                patient_gender,
                patient_address,
                patient_contno,
                passport_expirydt,
                patient_status,
                entered_by,
                entered_date,
                psprtissue_country,
                patient_email
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, CURDATE(), %s, %s
            )
            """,
            (
                passport_no,
                payload.get("patient_country"),
                payload.get("patient_givennm"),
                payload.get("patient_surnm"),
                payload.get("patient_dob"),
                payload.get("patient_gender"),
                payload.get("patient_address"),
                payload.get("patient_contno"),
                payload.get("passport_expirydt"),
                payload.get("patient_status") or "Active",
                payload.get("entered_by") or "SYSTEM",
                payload.get("psprtissue_country"),
                payload.get("patient_email"),
            ),
        )
    finally:
        cursor.close()

    return {
        "success": True,
        "created": True,
        "passport_no": passport_no,
        "message": "Patient created successfully.",
    }


def insert_patient_if_not_exists(
    conn: MySQLConnection,
    queue_row: Dict[str, Any],
    created_by: str = "SYSTEM",
    uploaded_files: Optional[List[FileStorage]] = None,
) -> Dict[str, Any]:
    """
    Create/update patient_create records from operation_queue_master row.

    Existing behavior preserved:
    - Main patient is inserted if passport does not exist.
    - Main patient is updated with non-blank values if passport already exists.
    - Documents are uploaded only against the main patient passport, as before.

    New behavior:
    - Attender 1, Attender 2 and Attender 3 are also inserted/updated in
      patient_create as separate patient records when their passport numbers
      are available.
    - No duplicate patient_create row is created because passport_no remains
      the primary duplicate check.
    """

    queue_row = queue_row or {}
    created_by = _safe_str(created_by) or "SYSTEM"

    main_payload = _build_patient_create_payload_from_queue_patient(
        queue_row=queue_row,
        created_by=created_by,
    )

    passport_no = _safe_str(main_payload.get("passport_no"))

    if not passport_no:
        return {
            "success": False,
            "created": False,
            "patient_id": None,
            "docs_inserted_count": 0,
            "attenders_created_count": 0,
            "attenders_updated_count": 0,
            "attenders_skipped_count": 0,
            "attender_results": [],
            "message": "Patient passport number is blank.",
        }

    main_result = _upsert_patient_create_from_payload(
        conn=conn,
        payload=main_payload,
    )

    if not main_result.get("success"):
        return {
            "success": False,
            "created": False,
            "patient_id": None,
            "passport_no": passport_no,
            "docs_inserted_count": 0,
            "attenders_created_count": 0,
            "attenders_updated_count": 0,
            "attenders_skipped_count": 0,
            "attender_results": [],
            "message": main_result.get("message") or "Patient validation failed.",
        }

    docs_result = insert_patient_docs(
        conn=conn,
        passport_no=passport_no,
        uploaded_files=uploaded_files,
    )
    docs_inserted_count = docs_result.get("inserted_count", 0)

    attender_results = []
    attenders_created_count = 0
    attenders_updated_count = 0
    attenders_skipped_count = 0

    for attender_prefix, attender_label in (
        ("attn1", "Attender 1"),
        ("attn2", "Attender 2"),
        ("attn3", "Attender 3"),
    ):
        attender_payload = _build_patient_create_payload_from_attender(
            queue_row=queue_row,
            attender_prefix=attender_prefix,
            created_by=created_by,
        )

        attender_passport_no = _safe_str(attender_payload.get("passport_no"))

        if not attender_passport_no:
            attenders_skipped_count += 1
            attender_results.append({
                "attender": attender_label,
                "success": True,
                "created": False,
                "updated": False,
                "skipped": True,
                "passport_no": "",
                "message": f"{attender_label} passport number is blank. Skipped.",
            })
            continue

        attender_result = _upsert_patient_create_from_payload(
            conn=conn,
            payload=attender_payload,
        )

        if not attender_result.get("success"):
            attender_results.append({
                "attender": attender_label,
                "success": False,
                "created": False,
                "updated": False,
                "skipped": False,
                "passport_no": attender_passport_no,
                "message": attender_result.get("message"),
            })

            return {
                "success": False,
                "created": main_result.get("created", False),
                "patient_id": None,
                "passport_no": passport_no,
                "docs_inserted_count": docs_inserted_count,
                "attenders_created_count": attenders_created_count,
                "attenders_updated_count": attenders_updated_count,
                "attenders_skipped_count": attenders_skipped_count,
                "attender_results": attender_results,
                "message": (
                    f"{attender_label} validation failed. "
                    f"{attender_result.get('message') or 'Invalid passport details.'}"
                ),
            }

        if attender_result.get("created"):
            attenders_created_count += 1
        else:
            attenders_updated_count += 1

        attender_results.append({
            "attender": attender_label,
            "success": attender_result.get("success", False),
            "created": attender_result.get("created", False),
            "updated": bool(attender_result.get("success")) and not bool(attender_result.get("created")),
            "skipped": False,
            "passport_no": attender_passport_no,
            "message": attender_result.get("message"),
        })

    main_created = bool(main_result.get("created"))

    message_parts = []
    if main_created:
        message_parts.append("Patient created successfully.")
    else:
        message_parts.append("Patient already exists and details updated.")

    if docs_inserted_count:
        message_parts.append(f"{docs_inserted_count} document(s) uploaded successfully.")

    if attenders_created_count or attenders_updated_count:
        message_parts.append(
            f"Attenders processed: {attenders_created_count} created, {attenders_updated_count} updated."
        )

    if attenders_skipped_count:
        message_parts.append(f"{attenders_skipped_count} attender(s) skipped because passport number was blank.")

    return {
        "success": True,
        "created": main_created,
        "patient_id": None,
        "passport_no": passport_no,
        "docs_inserted_count": docs_inserted_count,
        "attenders_created_count": attenders_created_count,
        "attenders_updated_count": attenders_updated_count,
        "attenders_skipped_count": attenders_skipped_count,
        "attender_results": attender_results,
        "message": " ".join(message_parts),
    }
