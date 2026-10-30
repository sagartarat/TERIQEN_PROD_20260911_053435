import os
import io
import uuid
import mimetypes
from datetime import datetime, date

import pandas as pd
import mysql.connector


# ============================================================
# CONFIG / CONSTANTS
# ============================================================

PATIENT_UPLOAD_REQUIRED_COLUMNS = {
    "Passport No": "passport_no",
    "Patient Country": "patient_country",
    "Patient Given Name": "patient_givennm",
    "Patient Sur Name": "patient_surnm",
    "Patient DOB": "patient_dob",
    "Patient Gender": "patient_gender",
    "Patient Address": "patient_address",
    "Patient Contact No": "patient_contno",
    "Passport Expiry Date": "passport_expirydt",
    "Passport Country": "psprtissue_country",
    "Patient Email": "patient_email",
}

ALLOWED_PATIENT_GENDERS = {"Male", "Female", "Others"}

FILE_UPLOAD_STATUS_PROCESSING = "PROCESSING"
FILE_UPLOAD_STATUS_SUCCESS = "SUCCESS"
FILE_UPLOAD_STATUS_PARTIAL = "PARTIAL"
FILE_UPLOAD_STATUS_FAILED = "FAILED"

FILE_UPLOAD_TYPE_PATIENT = "Patient Upload"


# ============================================================
# HELPERS
# ============================================================

def normalize_header(value: str) -> str:
    return " ".join(str(value or "").strip().split())


def normalize_text(value):
    if value is None:
        return None
    value = str(value).strip()
    return value if value else None


def normalize_gender(value):
    value = normalize_text(value)
    if not value:
        return None

    lookup = {
        "male": "Male",
        "m": "Male",
        "female": "Female",
        "f": "Female",
        "others": "Others",
        "other": "Others",
        "o": "Others",
    }
    return lookup.get(value.lower(), value)


def parse_excel_date(value):
    """
    Converts Excel/pandas values into Python date or None.
    """
    if value is None or str(value).strip() == "":
        return None

    if isinstance(value, pd.Timestamp):
        return value.date()

    if isinstance(value, datetime):
        return value.date()

    if isinstance(value, date):
        return value

    try:
        parsed = pd.to_datetime(value, errors="coerce")
        if pd.isna(parsed):
            return None
        return parsed.date()
    except Exception:
        return None


def make_upload_ref_no() -> str:
    return f"UPL_{datetime.now().strftime('%Y%m%d%H%M%S')}"


def make_file_reference() -> str:
    return f"FILEREF_{uuid.uuid4().hex[:12].upper()}"


def make_stored_file_name(original_filename: str) -> str:
    """
    Keep original file name exactly as uploaded.
    No timestamp / random suffix should be appended.
    """
    return os.path.basename((original_filename or "").strip())


def get_file_ext(filename: str) -> str:
    _, ext = os.path.splitext(filename or "")
    return ext.lower().replace(".", "")


def get_mime_type(filename: str) -> str:
    mime_type, _ = mimetypes.guess_type(filename or "")
    return mime_type or "application/octet-stream"


def read_uploaded_dataframe(uploaded_file, filename: str) -> pd.DataFrame:
    """
    uploaded_file can be:
      - Werkzeug FileStorage
      - bytes
      - file-like object
    """
    ext = get_file_ext(filename)

    if hasattr(uploaded_file, "read"):
        content = uploaded_file.read()
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)
    elif isinstance(uploaded_file, (bytes, bytearray)):
        content = bytes(uploaded_file)
    else:
        raise ValueError("Unsupported uploaded_file type")

    if ext == "csv":
        return pd.read_csv(io.BytesIO(content), dtype=str)
    if ext in {"xlsx", "xls"}:
        return pd.read_excel(io.BytesIO(content), dtype=str)

    raise ValueError("Unsupported file type. Allowed: csv, xlsx, xls")


def read_uploaded_bytes(uploaded_file) -> bytes:
    if hasattr(uploaded_file, "read"):
        content = uploaded_file.read()
        if hasattr(uploaded_file, "seek"):
            uploaded_file.seek(0)
        return content
    if isinstance(uploaded_file, (bytes, bytearray)):
        return bytes(uploaded_file)
    raise ValueError("Unsupported uploaded_file type")


def validate_required_headers(df: pd.DataFrame):
    normalized_actual = {normalize_header(col): col for col in df.columns}
    missing = []

    for excel_col in PATIENT_UPLOAD_REQUIRED_COLUMNS.keys():
        if normalize_header(excel_col) not in normalized_actual:
            missing.append(excel_col)

    return missing


def standardize_dataframe_columns(df: pd.DataFrame) -> pd.DataFrame:
    """
    Renames upload columns to DB column names based on mapping.
    """
    rename_map = {}
    normalized_actual = {normalize_header(col): col for col in df.columns}

    for excel_col, db_col in PATIENT_UPLOAD_REQUIRED_COLUMNS.items():
        actual_col = normalized_actual.get(normalize_header(excel_col))
        if actual_col:
            rename_map[actual_col] = db_col

    return df.rename(columns=rename_map)


def is_duplicate_file_upload(conn, stored_filename: str) -> bool:
    """
    Returns True if the same file name already exists in file_upload.
    """
    cursor = conn.cursor()
    try:
        cursor.execute(
            """
            SELECT 1
            FROM file_upload
            WHERE file_name = %s
            LIMIT 1
            """,
            (stored_filename,),
        )
        row = cursor.fetchone()
        return row is not None
    finally:
        cursor.close()


# ============================================================
# DB INSERT FUNCTIONS
# ============================================================

def insert_file_upload_processing(
    conn,
    *,
    original_filename: str,
    stored_filename: str,
    uploaded_by: str,
    upload_content: bytes,
    upload_type: str,
    total_records: int,
    file_reference: str,
    upload_ref_no: str,
    remarks: str = None,
):
    cursor = conn.cursor()

    sql = """
        INSERT INTO file_upload (
            file_name,
            fileupld_status,
            fileupld_by,
            fileupld_content,
            total_records,
            success_records,
            rejected_records,
            reject_reasons,
            file_reference,
            upload_type,
            upload_ref_no,
            file_ext,
            mime_type,
            file_size,
            rejection_remarks,
            processed_on
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
    """

    cursor.execute(
        sql,
        (
            stored_filename,
            FILE_UPLOAD_STATUS_PROCESSING,
            uploaded_by,
            upload_content,
            total_records,
            0,
            0,
            None,
            file_reference,
            upload_type,
            upload_ref_no,
            get_file_ext(original_filename),
            get_mime_type(original_filename),
            len(upload_content) if upload_content else 0,
            remarks,
            None,
        ),
    )
    cursor.close()


def update_file_upload_final_status(
    conn,
    *,
    stored_filename: str,
    final_status: str,
    success_records: int,
    rejected_records: int,
    reject_reasons: str = None,
    rejection_remarks: str = None,
):
    cursor = conn.cursor()

    sql = """
        UPDATE file_upload
           SET fileupld_status = %s,
               success_records = %s,
               rejected_records = %s,
               reject_reasons = %s,
               rejection_remarks = %s,
               processed_on = %s
         WHERE file_name = %s
    """

    cursor.execute(
        sql,
        (
            final_status,
            success_records,
            rejected_records,
            reject_reasons,
            rejection_remarks,
            datetime.now(),
            stored_filename,
        ),
    )
    cursor.close()


def insert_patient_create_row(
    conn,
    *,
    row_data: dict,
    entered_by: str,
    patient_status: str = "ACTIVE",
):
    cursor = conn.cursor()

    sql = """
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
        )
        VALUES (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
    """

    cursor.execute(
        sql,
        (
            row_data.get("passport_no"),
            row_data.get("patient_country"),
            row_data.get("patient_givennm"),
            row_data.get("patient_surnm"),
            row_data.get("patient_dob"),
            row_data.get("patient_gender"),
            row_data.get("patient_address"),
            row_data.get("patient_contno"),
            row_data.get("passport_expirydt"),
            patient_status,
            entered_by,
            date.today(),
            row_data.get("psprtissue_country"),
            row_data.get("patient_email"),
        ),
    )
    cursor.close()


# ============================================================
# MAIN SERVICE
# ============================================================

def process_patient_upload(
    conn,
    *,
    uploaded_file,
    original_filename: str,
    uploaded_by: str,
    remarks: str = None,
):
    """
    Main method to:
    1. Read upload
    2. Validate template
    3. Insert file_upload record
    4. Insert valid rows into patient_create
    5. Update file_upload with final result

    Returns structured result dict.
    """

    result = {
        "success": False,
        "message": "",
        "total_records": 0,
        "success_records": 0,
        "rejected_records": 0,
        "errors": [],
        "upload_ref_no": None,
        "file_reference": None,
        "stored_file_name": None,
    }

    stored_filename = make_stored_file_name(original_filename)
    upload_ref_no = make_upload_ref_no()
    file_reference = make_file_reference()

    result["upload_ref_no"] = upload_ref_no
    result["file_reference"] = file_reference
    result["stored_file_name"] = stored_filename

    if not stored_filename:
        result["message"] = "Invalid file name."
        return result

    if is_duplicate_file_upload(conn, stored_filename):
        result["message"] = f"Duplicate file upload is not allowed. File '{stored_filename}' already exists."
        result["errors"].append(result["message"])
        return result

    file_bytes = read_uploaded_bytes(uploaded_file)
    df = read_uploaded_dataframe(uploaded_file, original_filename)

    if df is None or df.empty:
        result["message"] = "Uploaded file is empty."
        return result

    missing_headers = validate_required_headers(df)
    if missing_headers:
        result["message"] = f"Missing required column(s): {', '.join(missing_headers)}"
        return result

    df = standardize_dataframe_columns(df)
    df = df.where(pd.notnull(df), None)

    total_records = len(df.index)
    result["total_records"] = total_records

    try:
        insert_file_upload_processing(
            conn,
            original_filename=original_filename,
            stored_filename=stored_filename,
            uploaded_by=uploaded_by,
            upload_content=file_bytes,
            upload_type=FILE_UPLOAD_TYPE_PATIENT,
            total_records=total_records,
            file_reference=file_reference,
            upload_ref_no=upload_ref_no,
            remarks=remarks,
        )

        success_count = 0
        rejected_count = 0
        row_errors = []

        for idx, row in df.iterrows():
            excel_row_no = idx + 2  # header is row 1

            try:
                row_data = {
                    "passport_no": normalize_text(row.get("passport_no")),
                    "patient_country": normalize_text(row.get("patient_country")),
                    "patient_givennm": normalize_text(row.get("patient_givennm")),
                    "patient_surnm": normalize_text(row.get("patient_surnm")),
                    "patient_dob": parse_excel_date(row.get("patient_dob")),
                    "patient_gender": normalize_gender(row.get("patient_gender")),
                    "patient_address": normalize_text(row.get("patient_address")),
                    "patient_contno": normalize_text(row.get("patient_contno")),
                    "passport_expirydt": parse_excel_date(row.get("passport_expirydt")),
                    "psprtissue_country": normalize_text(row.get("psprtissue_country")),
                    "patient_email": normalize_text(row.get("patient_email")),
                }

                if not row_data["passport_no"]:
                    raise ValueError("Passport No is required")

                if not row_data["patient_givennm"]:
                    raise ValueError("Patient Given Name is required")

                if not row_data["patient_country"]:
                    raise ValueError("Patient Country is required")

                if row_data["patient_gender"] and row_data["patient_gender"] not in ALLOWED_PATIENT_GENDERS:
                    raise ValueError("Patient Gender must be Male, Female, or Others")

                insert_patient_create_row(
                    conn,
                    row_data=row_data,
                    entered_by=uploaded_by,
                    patient_status="ACTIVE",
                )
                success_count += 1

            except mysql.connector.IntegrityError as ie:
                rejected_count += 1
                row_errors.append(f"Row {excel_row_no}: DB integrity error - {str(ie)}")

            except Exception as row_error:
                rejected_count += 1
                row_errors.append(f"Row {excel_row_no}: {str(row_error)}")

        if success_count == total_records:
            final_status = FILE_UPLOAD_STATUS_SUCCESS
            final_message = "Patient upload completed successfully."
        elif success_count > 0:
            final_status = FILE_UPLOAD_STATUS_PARTIAL
            final_message = "Patient upload completed partially."
        else:
            final_status = FILE_UPLOAD_STATUS_FAILED
            final_message = "Patient upload failed."

        update_file_upload_final_status(
            conn,
            stored_filename=stored_filename,
            final_status=final_status,
            success_records=success_count,
            rejected_records=rejected_count,
            reject_reasons="\n".join(row_errors) if row_errors else None,
            rejection_remarks=remarks,
        )

        conn.commit()

        result["success"] = success_count > 0
        result["message"] = final_message
        result["success_records"] = success_count
        result["rejected_records"] = rejected_count
        result["errors"] = row_errors
        return result

    except Exception as e:
        conn.rollback()
        result["message"] = f"Upload processing failed: {str(e)}"
        result["errors"].append(str(e))
        return result


# ============================================================
# OPTIONAL FLASK ROUTE EXAMPLE
# ============================================================

def handle_patient_upload_request(conn, request, current_user):
    """
    Example helper for Flask route usage.
    """
    upload_file = request.files.get("upload_file")
    upload_type = (request.form.get("upload_type") or "").strip()
    remarks = (request.form.get("remarks") or "").strip() or None
    user_code = getattr(current_user, "user_code", None) or "SYSTEM"

    if not upload_file or not upload_file.filename:
        return {"success": False, "message": "Please choose a file to upload."}

    allowed_ext = {"csv", "xlsx", "xls"}
    ext = get_file_ext(upload_file.filename)
    if ext not in allowed_ext:
        return {"success": False, "message": "Unsupported file type. Allowed: csv, xlsx, xls."}

    if upload_type != "PATIENT_UPLOAD":
        return {"success": False, "message": "This handler supports only PATIENT_UPLOAD."}

    return process_patient_upload(
        conn,
        uploaded_file=upload_file,
        original_filename=upload_file.filename,
        uploaded_by=user_code,
        remarks=remarks,
    )