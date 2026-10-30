from datetime import date, timedelta
import hashlib
import hmac

from flask import current_app, render_template, request, redirect, url_for, flash
from flask_login import login_required, current_user
from mysql.connector import Error

from app.common.db import get_db
from app.masters.services import fetch_countries
from .helpers import (
    fetch_patient_by_passport,
    fetch_patient_docs,
    insert_patient_if_not_exists,
    insert_patient_docs,
    patient_doc_open as patient_doc_open_helper,
    validate_passport_expiry,
    validate_patient_dob,
)


# Passport-number column names used across TERIQEN modules.
# Add another name here only when a dependent table uses a different column name.
PATIENT_PASSPORT_COLUMN_NAMES = (
    "passport_no",
    "patpassport_no",
    "patient_passport_no",
    "passport_number",
)

# These tables belong to the Patient master itself and therefore must not block deletion.
# patientcreate_docs is deleted together with the Patient, preserving existing behaviour.
PATIENT_DELETE_EXCLUDED_TABLES = {
    "patient_create",
    "patientcreate_docs",
    "patient_delete_audit",
    "privacy_deletion_audit",
}


def _quote_mysql_identifier(identifier):
    """
    Safely quotes a MySQL table or column identifier obtained from
    INFORMATION_SCHEMA.
    """
    return "`" + str(identifier).replace("`", "``") + "`"


def _get_patient_dependencies(conn, passport_no):
    """
    Finds dependent records for a Patient by Passport Number.

    The function inspects the current MySQL database for tables containing one
    of the configured Passport Number columns, excluding the Patient master and
    Patient document tables. It then counts records matching the Patient's
    Passport Number.

    Returns:
        list[dict]: [
            {
                "table_name": "...",
                "column_name": "...",
                "record_count": 1,
            }
        ]
    """
    metadata_cursor = None
    count_cursor = None

    try:
        metadata_cursor = conn.cursor(dictionary=True)

        placeholders = ", ".join(["%s"] * len(PATIENT_PASSPORT_COLUMN_NAMES))

        metadata_cursor.execute(
            f"""
            SELECT
                TABLE_NAME AS table_name,
                COLUMN_NAME AS column_name
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND LOWER(COLUMN_NAME) IN ({placeholders})
            ORDER BY TABLE_NAME, ORDINAL_POSITION
            """,
            tuple(name.lower() for name in PATIENT_PASSPORT_COLUMN_NAMES),
        )

        candidate_columns = metadata_cursor.fetchall() or []
        dependencies = []

        count_cursor = conn.cursor()

        for item in candidate_columns:
            table_name = str(item.get("table_name") or "").strip()
            column_name = str(item.get("column_name") or "").strip()

            if not table_name or not column_name:
                continue

            if table_name.lower() in PATIENT_DELETE_EXCLUDED_TABLES:
                continue

            quoted_table = _quote_mysql_identifier(table_name)
            quoted_column = _quote_mysql_identifier(column_name)

            count_cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM {quoted_table}
                WHERE TRIM(COALESCE({quoted_column}, '')) = %s
                """,
                (passport_no,),
            )

            row = count_cursor.fetchone()
            record_count = int(row[0] or 0) if row else 0

            if record_count > 0:
                dependencies.append(
                    {
                        "table_name": table_name,
                        "column_name": column_name,
                        "record_count": record_count,
                    }
                )

        return dependencies

    finally:
        try:
            if metadata_cursor:
                metadata_cursor.close()
        except Exception:
            pass

        try:
            if count_cursor:
                count_cursor.close()
        except Exception:
            pass


def _format_patient_dependency_message(dependencies):
    """
    Creates a readable message showing where Patient dependencies were found.
    """
    details = []

    for dependency in dependencies:
        table_name = dependency.get("table_name") or "Unknown table"
        record_count = int(dependency.get("record_count") or 0)
        details.append(f"{table_name}: {record_count}")

    return (
        "Patient cannot be deleted because dependent records exist for this "
        "Passport Number. "
        + ", ".join(details)
        + "."
    )



def _get_patient_deleted_by():
    """
    Returns the logged-in user identifier for the Patient deletion audit.
    Existing user-code behaviour is preserved, with safe fallbacks.
    """
    for attribute_name in (
        "user_code",
        "user_id",
        "userid",
        "username",
        "id",
    ):
        value = getattr(current_user, attribute_name, None)
        if value is not None and str(value).strip():
            return str(value).strip()

    return "SYSTEM"


def _record_patient_deletion_audit(conn, passport_no, deleted_by):
    """Write a privacy-preserving deletion event without retaining Patient PII."""
    audit_cursor = None
    normalized_ref = str(passport_no or "").strip().upper()
    audit_key = str(current_app.config.get("AUDIT_HASH_KEY") or "").encode("utf-8")

    if not normalized_ref or not audit_key:
        raise Exception("Deletion audit configuration is incomplete.")

    reference_hash = hmac.new(
        audit_key,
        normalized_ref.encode("utf-8"),
        hashlib.sha256,
    ).hexdigest()
    masked_ref = ("*" * max(len(normalized_ref) - 4, 0)) + normalized_ref[-4:]

    try:
        audit_cursor = conn.cursor()
        audit_cursor.execute(
            """
            INSERT INTO privacy_deletion_audit (
                entity_type,
                source_table,
                entity_ref_hash,
                entity_ref_masked,
                deleted_by,
                deleted_at
            ) VALUES (%s, %s, %s, %s, %s, NOW())
            """,
            (
                "PATIENT",
                "patient_create",
                reference_hash,
                masked_ref,
                deleted_by,
            ),
        )
    finally:
        if audit_cursor:
            try:
                audit_cursor.close()
            except Exception:
                pass


def register_patient_routes(app):

    @app.route("/patient-creation", methods=["GET", "POST"])
    @login_required
    def patient_creation():
        conn = None
        cursor = None

        mode = (request.args.get("mode") or request.form.get("mode") or "create").strip().lower()
        key = (request.args.get("key") or request.form.get("key") or "").strip()
        passport_min_date = (date.today() + timedelta(days=1)).isoformat()
        dob_max_date = (date.today() - timedelta(days=1)).isoformat()

        if mode not in ("create", "view", "modify"):
            mode = "create"

        try:
            conn = get_db()
            cursor = conn.cursor(dictionary=True)

            countries = fetch_countries(conn)

            patient = None
            docs = []

            if request.method == "GET":
                if mode in ("view", "modify"):
                    if not key:
                        flash("Missing patient key (passport number).", "error")
                        return redirect(url_for("patient_view"))

                    patient = fetch_patient_by_passport(conn, key)

                    if not patient:
                        flash("Patient not found for passport number: " + key, "error")
                        return redirect(url_for("patient_view"))

                    docs = fetch_patient_docs(conn, key)

                return render_template(
                    "patientcreation.html",
                    user=current_user,
                    countries=countries,
                    mode=mode,
                    key=key,
                    patient=patient,
                    docs=docs,
                    passport_min_date=passport_min_date,
                    dob_max_date=dob_max_date,
                )

            form_mode = (request.form.get("mode") or mode or "create").strip().lower()
            if form_mode not in ("create", "modify"):
                form_mode = "create"

            passport_no = (request.form.get("passport_no") or "").strip()
            patient_country = (request.form.get("country") or "").strip()
            patient_givennm = (request.form.get("given_name") or "").strip()
            patient_surnm = (request.form.get("sur_name") or "").strip()
            patient_dob = request.form.get("dob") or None
            patient_gender = (request.form.get("gender") or "").strip()
            patient_address = (request.form.get("address") or "").strip()
            patient_contno = (request.form.get("contact_no") or "").strip()
            passport_expirydt = request.form.get("passport_expiry") or None
            patient_status = (request.form.get("patient_status") or "Active").strip()
            psprtissue_country = (request.form.get("issuing_country") or patient_country).strip()
            patient_email = (request.form.get("patient_email") or "").strip()

            dob_validation = validate_patient_dob(patient_dob)
            if not dob_validation.get("success"):
                flash(
                    dob_validation.get("message")
                    or "Patient D.O.B. must be earlier than today.",
                    "error",
                )
                return render_template(
                    "patientcreation.html",
                    user=current_user,
                    countries=countries,
                    mode=form_mode,
                    key=key,
                    patient=request.form,
                    docs=(fetch_patient_docs(conn, passport_no) if form_mode == "modify" and passport_no else []),
                    passport_min_date=passport_min_date,
                    dob_max_date=dob_max_date,
                )

            # Use the normalized date returned by the common validator.
            patient_dob = dob_validation.get("date")

            expiry_validation = validate_passport_expiry(passport_expirydt)
            if not expiry_validation.get("success"):
                flash(
                    expiry_validation.get("message")
                    or "Passport Expiry Date must be later than today.",
                    "error",
                )
                return render_template(
                    "patientcreation.html",
                    user=current_user,
                    countries=countries,
                    mode=form_mode,
                    key=key,
                    patient=request.form,
                    docs=(fetch_patient_docs(conn, passport_no) if form_mode == "modify" and passport_no else []),
                    passport_min_date=passport_min_date,
                    dob_max_date=dob_max_date,
                )

            # Use the normalized date returned by the common validator.
            passport_expirydt = expiry_validation.get("date")

            uploaded_files = []
            for field_name in ("documents", "patient_docs", "files", "patient_documents"):
                uploaded_files.extend(request.files.getlist(field_name))

            uploaded_files = [
                f for f in uploaded_files
                if f and getattr(f, "filename", None) and f.filename.strip()
            ]

            if not passport_no:
                flash("Passport Number is required.", "error")
                return render_template(
                    "patientcreation.html",
                    user=current_user,
                    countries=countries,
                    mode=form_mode,
                    key=key,
                    patient=request.form,
                    docs=[],
                    passport_min_date=passport_min_date,
                    dob_max_date=dob_max_date,
                )

            if form_mode == "create":
                queue_row = {
                    "patpassport_no": passport_no,
                    "patgiven_nm": patient_givennm,
                    "patsur_nm": patient_surnm,
                    "patient_country": patient_country,
                    "patient_dob": patient_dob,
                    "patient_gender": patient_gender,
                    "patient_address": patient_address,
                    "patient_contno": patient_contno,
                    "patpass_expirydt": passport_expirydt,
                    "patient_email": patient_email,
                    "psprtissue_country": psprtissue_country,
                    "patient_status": patient_status,
                }

                result = insert_patient_if_not_exists(
                    conn=conn,
                    queue_row=queue_row,
                    created_by=getattr(current_user, "user_code", None) or "SYSTEM",
                    uploaded_files=uploaded_files,
                )

                if not result.get("success"):
                    conn.rollback()
                    flash(result.get("message") or "Patient creation failed.", "error")
                    return render_template(
                        "patientcreation.html",
                        user=current_user,
                        countries=countries,
                        mode="create",
                        key=key,
                        patient=request.form,
                        docs=[],
                        passport_min_date=passport_min_date,
                        dob_max_date=dob_max_date,
                    )

                conn.commit()

                if result.get("created"):
                    if result.get("docs_inserted_count", 0) > 0:
                        flash(
                            f"Patient created successfully and {result.get('docs_inserted_count', 0)} document(s) uploaded.",
                            "success",
                        )
                    else:
                        flash("Patient Creation is successful", "success")
                else:
                    if result.get("docs_inserted_count", 0) > 0:
                        flash(
                            f"Patient already exists. {result.get('docs_inserted_count', 0)} document(s) uploaded successfully.",
                            "success",
                        )
                    else:
                        flash("Patient already exists.", "warning")

                return redirect(url_for("patient_creation"))

            existing = fetch_patient_by_passport(conn, passport_no)
            if not existing:
                flash("Patient not found for passport number: " + passport_no, "error")
                return redirect(url_for("patient_view"))

            update_cursor = conn.cursor()
            try:
                update_cursor.execute(
                    """
                    UPDATE patient_create
                    SET
                        patient_country = %s,
                        patient_givennm = %s,
                        patient_surnm = %s,
                        patient_dob = %s,
                        patient_gender = %s,
                        patient_address = %s,
                        patient_contno = %s,
                        passport_expirydt = %s,
                        patient_status = %s,
                        entered_by = %s,
                        entered_date = CURDATE(),
                        psprtissue_country = %s,
                        patient_email = %s
                    WHERE passport_no = %s
                    """,
                    (
                        patient_country,
                        patient_givennm,
                        patient_surnm,
                        patient_dob,
                        patient_gender,
                        patient_address,
                        patient_contno,
                        passport_expirydt,
                        patient_status,
                        getattr(current_user, "user_code", None) or "SYSTEM",
                        psprtissue_country,
                        patient_email,
                        passport_no,
                    ),
                )
            finally:
                update_cursor.close()

            if request.form.get("replace_docs") == "1":
                delete_cursor = conn.cursor()
                try:
                    delete_cursor.execute(
                        "DELETE FROM patientcreate_docs WHERE passport_no = %s",
                        (passport_no,),
                    )
                finally:
                    delete_cursor.close()

            docs_result = {"inserted_count": 0}
            if uploaded_files:
                docs_result = insert_patient_docs(
                    conn=conn,
                    passport_no=passport_no,
                    uploaded_files=uploaded_files,
                )

            conn.commit()

            if docs_result.get("inserted_count", 0) > 0:
                flash(
                    f"Patient updated successfully and {docs_result.get('inserted_count', 0)} document(s) uploaded.",
                    "success",
                )
            else:
                flash("Patient updated successfully.", "success")

            return redirect(url_for("patient_creation", mode="modify", key=passport_no))

        except Error as e:
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass
            print("DB ERROR:", e)
            flash(f"Database Error: {str(e)}", "error")
            return redirect(url_for("patient_creation"))

        except Exception as e:
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass
            print("patient_creation error:", e)
            flash(f"Error: {str(e)}", "error")
            return redirect(url_for("patient_creation"))

        finally:
            try:
                if cursor:
                    cursor.close()
            except Exception:
                pass
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/patient-doc-open/<string:passport_no>/<path:filename>", methods=["GET"])
    @login_required
    def patient_doc_open(passport_no, filename):
        return patient_doc_open_helper(passport_no, filename)

    @app.route("/patient-view", methods=["GET"])
    @login_required
    def patient_view():
        conn = None
        cur = None

        try:
            conn = get_db()
            cur = conn.cursor(dictionary=True)

            countries = fetch_countries(conn)

            cur.execute(
                """
                SELECT
                    passport_no,
                    patient_givennm,
                    patient_surnm,
                    patient_country,
                    patient_contno,
                    patient_email,
                    passport_expirydt,
                    patient_gender,
                    patient_status
                FROM patient_create
                ORDER BY entered_date DESC, passport_no ASC
                """
            )

            patients = cur.fetchall() or []

            for p in patients:
                for k in list(p.keys()):
                    if p[k] is None:
                        p[k] = ""

            return render_template(
                "patientview.html",
                user=current_user,
                patients=patients,
                countries=countries,
            )

        except Exception as e:
            print("patient_view error:", e)
            flash(f"Error: {str(e)}", "error")
            return redirect(url_for("dashboard"))

        finally:
            if cur:
                cur.close()
            if conn:
                conn.close()

    @app.route("/patient-delete/<string:passport_no>", methods=["POST"])
    @login_required
    def patient_delete(passport_no):
        conn = None
        cur = None

        try:
            passport_no = (passport_no or "").strip()

            if not passport_no:
                flash("Patient Passport Number is required for deletion.", "error")
                return redirect(url_for("patient_view"))

            conn = get_db()
            cur = conn.cursor()

            # Lock the Patient row while dependency checking, audit archiving,
            # document deletion and Patient deletion are completed.
            cur.execute(
                """
                SELECT passport_no
                FROM patient_create
                WHERE passport_no = %s
                LIMIT 1
                FOR UPDATE
                """,
                (passport_no,),
            )

            patient_exists_row = cur.fetchone()

            if not patient_exists_row:
                conn.rollback()
                flash(
                    f"Patient not found for passport number: {passport_no}",
                    "error",
                )
                return redirect(url_for("patient_view"))

            # Preserve the existing dependency validation.
            dependencies = _get_patient_dependencies(
                conn=conn,
                passport_no=passport_no,
            )

            if dependencies:
                conn.rollback()
                flash(
                    _format_patient_dependency_message(dependencies),
                    "warning",
                )
                return redirect(url_for("patient_view"))

            deleted_by = _get_patient_deleted_by()

            # Create the audit record before deleting documents or Patient data.
            _record_patient_deletion_audit(
                conn=conn,
                passport_no=passport_no,
                deleted_by=deleted_by,
            )

            # Preserve the existing functionality of deleting Patient documents
            # before deleting the Patient master record.
            cur.execute(
                """
                DELETE FROM patientcreate_docs
                WHERE passport_no = %s
                """,
                (passport_no,),
            )

            deleted_document_count = cur.rowcount

            cur.execute(
                """
                DELETE FROM patient_create
                WHERE passport_no = %s
                """,
                (passport_no,),
            )

            if cur.rowcount == 0:
                raise Exception(
                    "Patient record could not be deleted after audit creation."
                )

            # Audit insert, document deletion and Patient deletion are committed
            # together. Any error rolls back the complete operation.
            conn.commit()

            if deleted_document_count > 0:
                flash(
                    (
                        "Patient Deleted Successfully. "
                        f"{deleted_document_count} document(s) were also deleted. "
                        "A privacy-preserving deletion audit event was recorded."
                    ),
                    "success",
                )
            else:
                flash(
                    (
                        "Patient Deleted Successfully. "
                        "A privacy-preserving deletion audit event was recorded."
                    ),
                    "success",
                )

        except Exception as e:
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass

            print("patient_delete error:", e)
            flash(
                f"Delete failed. No changes were committed. Error: {e}",
                "error",
            )

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

        return redirect(url_for("patient_view"))

