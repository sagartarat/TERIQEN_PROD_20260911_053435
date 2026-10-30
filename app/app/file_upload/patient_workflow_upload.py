import io
import math
import os
from datetime import date, datetime

import pandas as pd
from werkzeug.utils import secure_filename

from app.common.db_metadata import get_table_columns
from app.lead.helpers import insert_patient_workflow_from_payload


PATIENT_WORKFLOW_UPLOAD_MAP = {
    "Patient Given Name": "patgiven_nm",
    "Patient Short Name": "patsur_nm",
    "Patient Passport Number": "patpassport_no",
    "Patient Passport Expiry Date": "patpass_expirydt",
    "Patient Gender": "patient_gender",
    "Patient Dob": "patient_dob",
    "Patient Age": "patient_age",
    "Patient Contact Number": "patient_contno",
    "Patient Address": "patient_address",
    "Patient Email": "patient_email",
    "Patient Nationality": "patient_country",

    "Attender1 Given Name": "attn1_givennm",
    "Attender1 Short Name": "attn1_surnm",
    "Attender1 Passport Number": "attn1passport_no",
    "Attender1 Passport Expiry Date": "attn1pass_expirydt",
    "Attender1 Gender": "attn1_gender",
    "Attender1 Dob": "attn1_dob",
    "Attender1 Age": "attn1_age",
    "Attender1 Address": "attn1_address",
    "Attender1 Nationality": "attn1_country",
    "Attender1 Contact Number": "attn1_contno",
    "Attender1 Relationship With Patient": "attn1pat_relation",

    "Attender2 Given Name": "attn2_givennm",
    "Attender2 Short Name": "attn2_surnm",
    "Attender2 Passport Number": "attn2passport_no",
    "Attender2 Passport Expiry Date": "attn2pass_expirydt",
    "Attender2 Gender": "attn2_gender",
    "Attender2 Dob": "attn2_dob",
    "Attender2 Age": "attn2_age",
    "Attender2 Address": "attn2_address",
    "Attender2 Nationality": "attn2_country",
    "Attender2 Contact Number": "attn2_contno",
    "Attender2 Relationship With Patient": "attn2pat_relation",

    "Attender3 Given Name": "attn3_givennm",
    "Attender3 Short Name": "attn3_surnm",
    "Attender3 Passport Number": "attn3passport_no",
    "Attender3 Passport Expiry Date": "attn3pass_expirydt",
    "Attender3 Gender": "attn3_gender",
    "Attender3 Dob": "attn3_dob",
    "Attender3 Age": "attn3_age",
    "Attender3 Address": "attn3_address",
    "Attender3 Nationality": "attn3_country",
    "Attender3 Contact Number": "attn3_contno",
    "Attender3 Relationship With Patient": "attn3pat_relation",

    "Hospital Name": "hospital_shrtnm",
    "Department": "hospital_dept",
    "Doctor's Name": "doctor_nm",
    "Nationality": "_request_nationality",
    "Embassy": "ivac_center",
    "Agency Name": "agent_shrtnm",
    "Intent": "_intent",
    "Approximate Date": "_approximate_date",
}

# All 51 headings define the common WhatsApp / File Upload structure.
PATIENT_WORKFLOW_REQUIRED_HEADERS = list(PATIENT_WORKFLOW_UPLOAD_MAP.keys())

# Preserve the principal business validations from the previous upload format.
PATIENT_WORKFLOW_MANDATORY_COLUMNS = [
    "Patient Passport Number",
    "Patient Given Name",
    "Patient Short Name",
    "Patient Dob",
    "Patient Nationality",
    "Patient Gender",
    "Patient Contact Number",
    "Patient Age",
    "Patient Passport Expiry Date",
    "Patient Address",
    "Hospital Name",
    "Department",
    "Embassy",
    "Agency Name",
    "Intent",
]

ATTENDER_REQUIRED_GROUPS = {
    "Attender1 Passport Number": [
        "Attender1 Relationship With Patient",
        "Attender1 Given Name",
        "Attender1 Short Name",
        "Attender1 Dob",
        "Attender1 Nationality",
        "Attender1 Gender",
        "Attender1 Contact Number",
        "Attender1 Age",
        "Attender1 Passport Expiry Date",
        "Attender1 Address",
    ],
    "Attender2 Passport Number": [
        "Attender2 Relationship With Patient",
        "Attender2 Given Name",
        "Attender2 Short Name",
        "Attender2 Dob",
        "Attender2 Nationality",
        "Attender2 Gender",
        "Attender2 Contact Number",
        "Attender2 Age",
        "Attender2 Passport Expiry Date",
        "Attender2 Address",
    ],
    "Attender3 Passport Number": [
        "Attender3 Relationship With Patient",
        "Attender3 Given Name",
        "Attender3 Short Name",
        "Attender3 Dob",
        "Attender3 Nationality",
        "Attender3 Gender",
        "Attender3 Contact Number",
        "Attender3 Age",
        "Attender3 Passport Expiry Date",
        "Attender3 Address",
    ],
}

FILE_UPLOAD_TYPE_WORKFLOW = "Workflow Upload"


def _is_blank(value):
    if value is None:
        return True
    if isinstance(value, float) and math.isnan(value):
        return True
    if pd.isna(value):
        return True
    return str(value).strip() == ""


def _clean_string(value):
    if _is_blank(value):
        return None
    return str(value).strip()


def _clean_date(value):
    if _is_blank(value):
        return None

    if isinstance(value, (datetime, date)):
        return value.strftime("%Y-%m-%d")

    try:
        parsed = pd.to_datetime(value, errors="coerce", dayfirst=False)
        if pd.isna(parsed):
            return None
        return parsed.strftime("%Y-%m-%d")
    except Exception:
        return None


def _clean_int(value):
    if _is_blank(value):
        return None
    try:
        return int(float(value))
    except Exception:
        return None


def _normalize_intent(value):
    value = _clean_string(value)
    if not value:
        return None

    upper = value.upper()

    if "VIL" in upper or ("VISA" in upper and "INVIT" in upper):
        return "VIL"

    if "APPOINT" in upper:
        return "APPOINTMENT"

    return upper


def _normalize_row(row_dict):
    normalized = {}

    for excel_col, db_col in PATIENT_WORKFLOW_UPLOAD_MAP.items():
        value = row_dict.get(excel_col)

        if db_col in {
            "patient_dob", "patpass_expirydt",
            "attn1_dob", "attn1pass_expirydt",
            "attn2_dob", "attn2pass_expirydt",
            "attn3_dob", "attn3pass_expirydt",
        }:
            normalized[db_col] = _clean_date(value)

        elif db_col in {
            "patient_age", "attn1_age",
            "attn2_age", "attn3_age",
        }:
            normalized[db_col] = _clean_int(value)

        else:
            normalized[db_col] = _clean_string(value)

    # The final request-level Nationality is retained as a fallback to
    # Patient Nationality so the common WhatsApp format is not altered.
    if not normalized.get("patient_country"):
        normalized["patient_country"] = normalized.get("_request_nationality")

    intent = _normalize_intent(normalized.get("_intent"))
    approximate_date = _clean_date(normalized.get("_approximate_date"))

    normalized["intent_remarks"] = intent

    # Match current WhatsApp Intent / Approximate Date behaviour.
    if intent == "VIL":
        normalized["vil_date"] = approximate_date
        normalized["appointment_dt"] = approximate_date
        normalized["wrkflw_status"] = "VIL REQUESTED"

    elif intent == "APPOINTMENT":
        normalized["vil_date"] = None
        normalized["appointment_dt"] = approximate_date
        normalized["wrkflw_status"] = "APPOINTMENT BOOKED"

    else:
        normalized["vil_date"] = None
        normalized["appointment_dt"] = None
        normalized["wrkflw_status"] = "LEAD GENERATED"

    if not normalized.get("doctor_nm"):
        normalized["doctor_nm"] = "As per Availability"

    return normalized


def _validate_required_columns(df):
    return [
        col
        for col in PATIENT_WORKFLOW_REQUIRED_HEADERS
        if col not in df.columns
    ]


def _validate_row_business_rules(row_dict, row_no):
    errors = []

    for col in PATIENT_WORKFLOW_MANDATORY_COLUMNS:
        if _is_blank(row_dict.get(col)):
            errors.append(f"Row {row_no}: '{col}' is mandatory.")

    for passport_col, dependent_cols in ATTENDER_REQUIRED_GROUPS.items():
        if not _is_blank(row_dict.get(passport_col)):
            for dep_col in dependent_cols:
                if _is_blank(row_dict.get(dep_col)):
                    errors.append(
                        f"Row {row_no}: '{dep_col}' is mandatory "
                        f"when '{passport_col}' is filled."
                    )

    intent = _normalize_intent(row_dict.get("Intent"))

    if intent in {"VIL", "APPOINTMENT"}:
        if _is_blank(row_dict.get("Approximate Date")):
            errors.append(
                f"Row {row_no}: 'Approximate Date' is mandatory "
                f"when Intent is '{intent}'."
            )
        elif _clean_date(row_dict.get("Approximate Date")) is None:
            errors.append(
                f"Row {row_no}: 'Approximate Date' is not a valid date."
            )

    return errors


def _get_table_columns(conn, table_name):
    try:
        return get_table_columns(conn, table_name)
    except Exception as e:
        print(f"{table_name} columns read error:", e)
        return set()


def _check_patient_active(conn, passport_no):
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT patient_status
            FROM patient_create
            WHERE passport_no = %s
            LIMIT 1
            """,
            (passport_no,),
        )
        row = cursor.fetchone()

        if not row:
            return False, "Patient passport not found in patient_create."

        status = (row.get("patient_status") or "").strip().upper()
        if status != "ACTIVE":
            return False, f"Patient is not active. Current status: {status or 'BLANK'}"

        return True, None
    finally:
        cursor.close()


def _check_attender_active_if_exists(conn, passport_no, label):
    if _is_blank(passport_no):
        return True, None

    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT patient_status
            FROM patient_create
            WHERE passport_no = %s
            LIMIT 1
            """,
            (passport_no,),
        )
        row = cursor.fetchone()

        if not row:
            return True, None

        status = (row.get("patient_status") or "").strip().upper()
        if status != "ACTIVE":
            return False, f"{label} is present in patient_create but not active. Current status: {status or 'BLANK'}"

        return True, None
    finally:
        cursor.close()


def _check_duplicate_workflow(conn, passport_no):
    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT
                pw.wrkflw_status,
                wr.allow_new_case_same_passport
            FROM patient_wrkflw pw
            LEFT JOIN wrkflwrule_master wr
              ON UPPER(TRIM(wr.status_name)) =
                 UPPER(TRIM(pw.wrkflw_status))
            WHERE pw.patpassport_no = %s
            ORDER BY pw.entered_date DESC, pw.ld_internalno DESC
            LIMIT 1
            """,
            (passport_no,),
        )
        row = cursor.fetchone()

        if not row:
            return True, None

        existing_status = (
            row.get("wrkflw_status") or ""
        ).strip().upper()

        allow_new_case = (
            int(row.get("allow_new_case_same_passport") or 0) == 1
        )

        if allow_new_case:
            return True, None

        return False, (
            f"Duplicate workflow exists for passport '{passport_no}'. "
            f"Current latest workflow status is '{existing_status}', "
            "and this status does not permit creation of a new case."
        )
    finally:
        cursor.close()


def _count_non_blank_rows(df):
    count = 0
    for _, row in df.iterrows():
        row_dict = row.to_dict()
        if any(not _is_blank(v) for v in row_dict.values()):
            count += 1
    return count


def _read_dataframe_from_bytes(file_bytes, file_ext):
    if file_ext == ".csv":
        return pd.read_csv(io.BytesIO(file_bytes))
    return pd.read_excel(io.BytesIO(file_bytes))


def _insert_file_upload_log(
    conn,
    *,
    original_filename,
    safe_filename,
    file_bytes,
    file_ext,
    mime_type,
    upload_type,
    uploaded_by,
    remarks,
    fileupld_status,
    total_records,
    success_records,
    rejected_records,
    reject_reasons,
):
    columns = _get_table_columns(conn, "file_upload")
    if not columns:
        return

    payload = {}

    def add_if_exists(column_name, value):
        if column_name in columns:
            payload[column_name] = value

    add_if_exists("file_name", safe_filename or original_filename)
    add_if_exists("upload_type", upload_type)
    add_if_exists("fileupld_status", fileupld_status)
    add_if_exists("fileupld_by", uploaded_by)
    add_if_exists("remarks", remarks)
    add_if_exists("fileupld_at", datetime.now())
    add_if_exists("processed_on", datetime.now())
    add_if_exists("total_records", total_records)
    add_if_exists("success_records", success_records)
    add_if_exists("rejected_records", rejected_records)
    add_if_exists("reject_reasons", reject_reasons)
    add_if_exists("rejection_remarks", reject_reasons)
    add_if_exists("fileupld_content", file_bytes)
    add_if_exists("mime_type", mime_type)
    add_if_exists("file_ext", file_ext)

    if not payload:
        return

    columns_sql = ", ".join(payload.keys())
    placeholders = ", ".join(["%s"] * len(payload))
    values = list(payload.values())

    cursor = conn.cursor()
    try:
        cursor.execute(
            f"""
            INSERT INTO file_upload ({columns_sql})
            VALUES ({placeholders})
            """,
            values,
        )
    finally:
        cursor.close()


def _build_shared_payload(row_dict, normalized):
    payload = {
        "patpassport_no": normalized.get("patpassport_no"),
        "patgiven_nm": normalized.get("patgiven_nm"),
        "patsur_nm": normalized.get("patsur_nm"),
        "patient_dob": normalized.get("patient_dob"),
        "patient_country": normalized.get("patient_country"),
        "patient_gender": normalized.get("patient_gender"),
        "patient_contno": normalized.get("patient_contno"),
        "patient_age": normalized.get("patient_age"),
        "patpass_expirydt": normalized.get("patpass_expirydt"),
        "patient_address": normalized.get("patient_address"),
        "patient_email": normalized.get("patient_email"),

        "attn1passport_no": normalized.get("attn1passport_no"),
        "attn1pat_relation": normalized.get("attn1pat_relation"),
        "attn1_givennm": normalized.get("attn1_givennm"),
        "attn1_surnm": normalized.get("attn1_surnm"),
        "attn1_dob": normalized.get("attn1_dob"),
        "attn1_country": normalized.get("attn1_country"),
        "attn1_gender": normalized.get("attn1_gender"),
        "attn1_contno": normalized.get("attn1_contno"),
        "attn1_age": normalized.get("attn1_age"),
        "attn1pass_expirydt": normalized.get("attn1pass_expirydt"),
        "attn1_address": normalized.get("attn1_address"),
        "attn1_email": normalized.get("attn1_email"),

        "attn2passport_no": normalized.get("attn2passport_no"),
        "attn2pat_relation": normalized.get("attn2pat_relation"),
        "attn2_givennm": normalized.get("attn2_givennm"),
        "attn2_surnm": normalized.get("attn2_surnm"),
        "attn2_dob": normalized.get("attn2_dob"),
        "attn2_country": normalized.get("attn2_country"),
        "attn2_gender": normalized.get("attn2_gender"),
        "attn2_contno": normalized.get("attn2_contno"),
        "attn2_age": normalized.get("attn2_age"),
        "attn2pass_expirydt": normalized.get("attn2pass_expirydt"),
        "attn2_address": normalized.get("attn2_address"),
        "attn2_email": normalized.get("attn2_email"),

        "attn3passport_no": normalized.get("attn3passport_no"),
        "attn3pat_relation": normalized.get("attn3pat_relation"),
        "attn3_givennm": normalized.get("attn3_givennm"),
        "attn3_surnm": normalized.get("attn3_surnm"),
        "attn3_dob": normalized.get("attn3_dob"),
        "attn3_country": normalized.get("attn3_country"),
        "attn3_gender": normalized.get("attn3_gender"),
        "attn3_contno": normalized.get("attn3_contno"),
        "attn3_age": normalized.get("attn3_age"),
        "attn3pass_expirydt": normalized.get("attn3pass_expirydt"),
        "attn3_address": normalized.get("attn3_address"),
        "attn3_email": normalized.get("attn3_email"),

        "wrkflw_status": normalized.get("wrkflw_status"),
        "hospital_shrtnm": normalized.get("hospital_shrtnm"),
        "hospital_city": normalized.get("hospital_city"),
        "hospital_dept": normalized.get("hospital_dept"),
        "ivac_center": normalized.get("ivac_center"),
        "agent_shrtnm": normalized.get("agent_shrtnm"),
        "doctor_nm": normalized.get("doctor_nm"),
        "vil_date": normalized.get("vil_date"),
        "appointment_dt": normalized.get("appointment_dt"),
        "wrkflw_remarks": normalized.get("wrkflw_remarks"),
        "ayush_no": normalized.get("ayush_no"),
        "external_refno": normalized.get("external_refno"),
        "patient_uhid": normalized.get("patient_uhid"),
    }

    return payload


def _resolve_agent_short_name(conn, agent_short_name):
    requested = _clean_string(agent_short_name)
    if not requested:
        return None

    cursor = conn.cursor(dictionary=True)
    try:
        cursor.execute(
            """
            SELECT short_name
            FROM partner_reg
            WHERE partner_type = 'Agency'
              AND UPPER(TRIM(short_name)) = UPPER(TRIM(%s))
            LIMIT 1
            """,
            (requested,),
        )
        row = cursor.fetchone()
        return _clean_string(row.get("short_name")) if row else None
    finally:
        cursor.close()


def _process_dataframe(
    conn,
    df,
    current_user_code="SYSTEM",
):
    df.columns = [str(col).strip() for col in df.columns]


    missing_columns = _validate_required_columns(df)
    if missing_columns:
        return {
            "success": False,
            "partial_success": False,
            "message": "Upload template is invalid.",
            "errors": [f"Missing required upload columns: {', '.join(missing_columns)}"],
            "total_records": 0,
            "success_records": 0,
            "rejected_records": 0,
            "inserted_count": 0,
        }

    total_records = _count_non_blank_rows(df)
    inserted_count = 0
    errors = []
    rejected_row_numbers = set()

    for idx, row in df.iterrows():
        row_no = idx + 2
        row_dict = row.to_dict()

        if all(_is_blank(v) for v in row_dict.values()):
            continue

        row_errors = _validate_row_business_rules(row_dict, row_no)
        normalized = _normalize_row(row_dict)

        row_agent = normalized.get("agent_shrtnm")

        if row_agent:
            resolved_agent = _resolve_agent_short_name(conn, row_agent)
            if resolved_agent:
                normalized["agent_shrtnm"] = resolved_agent
            else:
                row_errors.append(
                    f"Row {row_no}: Agency Name '{row_agent}' "
                    f"was not found as an Agency in Partner Master."
                )
        else:
            row_errors.append(
                f"Row {row_no}: Agency Name is mandatory."
            )

        passport_no = normalized.get("patpassport_no")
        if passport_no:
            active_ok, active_msg = _check_patient_active(conn, passport_no)
            if not active_ok:
                row_errors.append(f"Row {row_no}: {active_msg}")

            dup_ok, dup_msg = _check_duplicate_workflow(conn, passport_no)
            if not dup_ok:
                row_errors.append(f"Row {row_no}: {dup_msg}")
        else:
            row_errors.append(f"Row {row_no}: Patient Passport Number is mandatory.")

        attender_checks = [
            ("Attender1", normalized.get("attn1passport_no")),
            ("Attender2", normalized.get("attn2passport_no")),
            ("Attender3", normalized.get("attn3passport_no")),
        ]

        for attender_label, attender_passport in attender_checks:
            ok, msg = _check_attender_active_if_exists(conn, attender_passport, attender_label)
            if not ok:
                row_errors.append(f"Row {row_no}: {msg}")

        if row_errors:
            errors.extend(row_errors)
            rejected_row_numbers.add(row_no)
            continue

        shared_payload = _build_shared_payload(row_dict, normalized)

        try:
            insert_patient_workflow_from_payload(
                conn=conn,
                payload=shared_payload,
                created_by=current_user_code,
            )
            inserted_count += 1
        except Exception as e:
            errors.append(f"Row {row_no}: {str(e)}")
            rejected_row_numbers.add(row_no)

    rejected_count = len(rejected_row_numbers)

    if inserted_count > 0 and rejected_count > 0:
        return {
            "success": True,
            "partial_success": True,
            "message": (
                f"{inserted_count} Patient Workflow record(s) uploaded successfully. "
                f"{rejected_count} record(s) rejected."
            ),
            "errors": errors,
            "total_records": total_records,
            "success_records": inserted_count,
            "rejected_records": rejected_count,
            "inserted_count": inserted_count,
        }

    if inserted_count > 0 and rejected_count == 0:
        return {
            "success": True,
            "partial_success": False,
            "message": f"{inserted_count} Patient Workflow record(s) uploaded successfully.",
            "errors": [],
            "total_records": total_records,
            "success_records": inserted_count,
            "rejected_records": 0,
            "inserted_count": inserted_count,
        }

    return {
        "success": False,
        "partial_success": False,
        "message": "Validation failed.",
        "errors": errors,
        "total_records": total_records,
        "success_records": 0,
        "rejected_records": rejected_count,
        "inserted_count": 0,
    }


def process_patient_workflow_upload(conn, upload_path, current_user_code="SYSTEM"):
    file_ext = os.path.splitext(upload_path)[1].lower()

    if file_ext == ".csv":
        df = pd.read_csv(upload_path)
    else:
        df = pd.read_excel(upload_path)

    result = _process_dataframe(conn, df, current_user_code=current_user_code)

    if result.get("success"):
        conn.commit()
    else:
        conn.rollback()

    return result


def process_patient_upload(conn, uploaded_file, original_filename, uploaded_by="SYSTEM", remarks=None):
    safe_filename = secure_filename(
        original_filename or getattr(uploaded_file, "filename", "") or "patient_workflow_upload"
    )
    file_ext = os.path.splitext(safe_filename)[1].lower()
    mime_type = getattr(uploaded_file, "mimetype", None) or "application/octet-stream"

    if file_ext not in {".csv", ".xlsx", ".xls"}:
        return {
            "success": False,
            "partial_success": False,
            "message": "Unsupported file type. Allowed: .csv, .xlsx, .xls",
            "errors": ["Unsupported file type."],
            "total_records": 0,
            "success_records": 0,
            "rejected_records": 0,
            "inserted_count": 0,
        }

    file_bytes = uploaded_file.read()
    if not file_bytes:
        return {
            "success": False,
            "partial_success": False,
            "message": "Uploaded file is empty.",
            "errors": ["Uploaded file is empty."],
            "total_records": 0,
            "success_records": 0,
            "rejected_records": 0,
            "inserted_count": 0,
        }

    try:
        df = _read_dataframe_from_bytes(file_bytes, file_ext)
        result = _process_dataframe(conn, df, current_user_code=uploaded_by)

        reject_reasons = "\n".join(result.get("errors", [])[:500]) if result.get("errors") else None

        if result.get("success"):
            upload_status = "PARTIAL" if result.get("partial_success") else "SUCCESS"
            _insert_file_upload_log(
                conn,
                original_filename=original_filename,
                safe_filename=safe_filename,
                file_bytes=file_bytes,
                file_ext=file_ext,
                mime_type=mime_type,
                upload_type=FILE_UPLOAD_TYPE_WORKFLOW,
                uploaded_by=uploaded_by,
                remarks=remarks,
                fileupld_status=upload_status,
                total_records=result.get("total_records", 0),
                success_records=result.get("success_records", 0),
                rejected_records=result.get("rejected_records", 0),
                reject_reasons=reject_reasons,
            )
            conn.commit()
            return result

        conn.rollback()

        _insert_file_upload_log(
            conn,
            original_filename=original_filename,
            safe_filename=safe_filename,
            file_bytes=file_bytes,
            file_ext=file_ext,
            mime_type=mime_type,
            upload_type=FILE_UPLOAD_TYPE_WORKFLOW,
            uploaded_by=uploaded_by,
            remarks=remarks,
            fileupld_status="FAILED",
            total_records=result.get("total_records", 0),
            success_records=0,
            rejected_records=result.get("rejected_records", 0),
            reject_reasons=reject_reasons or result.get("message"),
        )
        conn.commit()
        return result

    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass

        error_message = f"Upload failed due to system error: {str(e)}"

        try:
            _insert_file_upload_log(
                conn,
                original_filename=original_filename,
                safe_filename=safe_filename,
                file_bytes=file_bytes,
                file_ext=file_ext,
                mime_type=mime_type,
                upload_type=FILE_UPLOAD_TYPE_WORKFLOW,
                uploaded_by=uploaded_by,
                remarks=remarks,
                fileupld_status="FAILED",
                total_records=0,
                success_records=0,
                rejected_records=0,
                reject_reasons=error_message,
            )
            conn.commit()
        except Exception as log_error:
            try:
                conn.rollback()
            except Exception:
                pass
            print("file_upload log insert error:", log_error)

        return {
            "success": False,
            "partial_success": False,
            "message": error_message,
            "errors": [error_message],
            "total_records": 0,
            "success_records": 0,
            "rejected_records": 0,
            "inserted_count": 0,
        }
