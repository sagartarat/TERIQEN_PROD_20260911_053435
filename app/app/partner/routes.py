import hashlib
import mimetypes
import os
import uuid
from datetime import date
from pathlib import Path

from flask import (
    abort,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    send_file,
    url_for,
)
from flask_login import current_user, login_required
from werkzeug.utils import secure_filename

from app.common.access_control import has_screen_permission
from app.common.db import get_db
from app.common.file_validation import validate_document_bytes
from app.common.utils import clean
from app.masters.services import fetch_countries, fetch_currencies
from .helpers import (
    delete_partner_document_rows,
    fetch_partner_by_short_name,
    fetch_partner_document_by_id,
    fetch_partner_document_paths,
    fetch_partner_documents,
    fetch_partners_list,
    insert_partner_document,
    mark_partner_document_deleted,
)


PARTNER_TABLE = "partner_reg"
PARTNER_PK = "short_name"
PARTNER_DOCUMENT_TABLE = "partner_document"
PARTNER_DOCUMENT_MAX_BYTES = 10 * 1024 * 1024
PARTNER_DOCUMENT_ALLOWED_EXTENSIONS = {
    "pdf",
    "doc",
    "docx",
    "xls",
    "xlsx",
    "ppt",
    "pptx",
    "csv",
    "txt",
    "jpg",
    "jpeg",
    "png",
    "webp",
}

PARTNER_DB_COLS = [
    "partner_type",
    "invoice_eligible",
    "hospital_coordinator",
    "referral_partner",
    "direct_treating_hospital",
    "contstart_date",
    "contend_date",
    "legal_name",
    "short_name",
    "partner_address",
    "partner_country",
    "partner_state",
    "partner_city",
    "partner_pincode",
    "partner_ccy",
    "partner_email",
    "gst_required",
    "gst_number",
    "pan_number",
    "placeof_supply",
    "tax_preference",
    "tds_percentage",
    "agent_percentage",
    "prim_contper",
    "prim_contno",
    "alt_contper",
    "alt_contno",
    "partner_status",
]

PARTNER_DEPENDENCY_COLUMN_NAMES = (
    "short_name",
    "partner_short_name",
    "partner_code",
    "partner_name",
    "hospital_short_name",
    "coordination_partner_short_name",
    "billing_partner_short_name",
)

# The Partner master table itself must not be treated as a dependency.
PARTNER_DELETE_EXCLUDED_TABLES = {
    PARTNER_TABLE.lower(),
    "partner_reg_delete",
    PARTNER_DOCUMENT_TABLE.lower(),
}


def _quote_mysql_identifier(identifier):
    """
    Safely quote a MySQL table or column identifier obtained from
    INFORMATION_SCHEMA.
    """
    return "`" + str(identifier).replace("`", "``") + "`"


def _get_partner_dependencies(conn, short_name):
    """
    Find dependent records for a Partner using the Partner short name.

    The current MySQL database is inspected for tables containing one of the
    configured Partner reference columns. The Partner master table is excluded.
    """
    metadata_cursor = None
    count_cursor = None

    try:
        metadata_cursor = conn.cursor(dictionary=True)
        placeholders = ", ".join(["%s"] * len(PARTNER_DEPENDENCY_COLUMN_NAMES))

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
            tuple(name.lower() for name in PARTNER_DEPENDENCY_COLUMN_NAMES),
        )

        candidate_columns = metadata_cursor.fetchall() or []
        dependencies = []
        count_cursor = conn.cursor()

        for item in candidate_columns:
            table_name = str(item.get("table_name") or "").strip()
            column_name = str(item.get("column_name") or "").strip()

            if not table_name or not column_name:
                continue

            if table_name.lower() in PARTNER_DELETE_EXCLUDED_TABLES:
                continue

            quoted_table = _quote_mysql_identifier(table_name)
            quoted_column = _quote_mysql_identifier(column_name)

            count_cursor.execute(
                f"""
                SELECT COUNT(*)
                FROM {quoted_table}
                WHERE TRIM(COALESCE({quoted_column}, '')) = %s
                """,
                (short_name,),
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


def _format_partner_dependency_message(dependencies):
    """
    Build a readable message showing the Partner dependency counts.
    """
    details = []

    for dependency in dependencies:
        table_name = dependency.get("table_name") or "Unknown table"
        record_count = int(dependency.get("record_count") or 0)
        details.append(f"{table_name}: {record_count}")

    return (
        "Partner cannot be deleted because dependent records exist for this "
        "Short Name. "
        + ", ".join(details)
        + "."
    )


PARTNER_DELETE_AUDIT_TABLE = "partner_reg_delete"


def _get_logged_in_user_id():
    for attribute_name in ("user_id", "user_code", "userid", "username", "id"):
        value = getattr(current_user, attribute_name, None)
        if value is not None and str(value).strip():
            return str(value).strip()
    return "SYSTEM"


def _get_partner_document_upload_root():
    configured_folder = current_app.config.get("PARTNER_DOCUMENT_UPLOAD_FOLDER")

    if configured_folder:
        upload_root = Path(str(configured_folder))
        if not upload_root.is_absolute():
            upload_root = Path(current_app.root_path) / upload_root
    else:
        upload_root = Path(current_app.instance_path) / "uploads" / "partner_documents"

    upload_root.mkdir(parents=True, exist_ok=True)
    return upload_root.resolve()


def _get_file_size(file_storage):
    stream = file_storage.stream
    current_position = stream.tell()
    stream.seek(0, os.SEEK_END)
    file_size = stream.tell()
    stream.seek(current_position, os.SEEK_SET)
    return int(file_size)


def _validate_partner_documents(uploaded_files):
    validated = []

    for file_storage in uploaded_files or []:
        original_name = str(file_storage.filename or "").strip()
        if not original_name:
            continue

        safe_original_name = secure_filename(original_name)
        if not safe_original_name:
            raise ValueError(f"Invalid document file name: {original_name}")

        extension = Path(safe_original_name).suffix.lower().lstrip(".")
        if extension not in PARTNER_DOCUMENT_ALLOWED_EXTENSIONS:
            raise ValueError(
                f"Unsupported document type for '{original_name}'. "
                "Allowed types: PDF, DOC, DOCX, XLS, XLSX, PPT, PPTX, "
                "CSV, TXT, JPG, JPEG, PNG and WEBP."
            )

        file_storage.stream.seek(0)
        document_bytes = file_storage.stream.read() or b""
        file_storage.stream.seek(0)
        validation = validate_document_bytes(
            safe_original_name,
            document_bytes,
            allowed_extensions=PARTNER_DOCUMENT_ALLOWED_EXTENSIONS,
            max_bytes=PARTNER_DOCUMENT_MAX_BYTES,
        )
        file_size = validation["size"]

        mime_type = (
            file_storage.mimetype
            or mimetypes.guess_type(safe_original_name)[0]
            or "application/octet-stream"
        )

        validated.append(
            {
                "file_storage": file_storage,
                "original_file_name": original_name,
                "safe_original_file_name": safe_original_name,
                "file_extension": extension,
                "file_size_bytes": file_size,
                "mime_type": mime_type,
            }
        )

    return validated


def _partner_document_folder_name(partner_short_name):
    safe_name = secure_filename(partner_short_name) or "partner"
    short_hash = hashlib.sha256(partner_short_name.encode("utf-8")).hexdigest()[:10]
    return f"{safe_name}_{short_hash}"


def _save_partner_documents(conn, partner_short_name, uploaded_files, uploaded_by):
    validated_files = _validate_partner_documents(uploaded_files)
    if not validated_files:
        return []

    upload_root = _get_partner_document_upload_root()
    partner_folder = upload_root / _partner_document_folder_name(partner_short_name)
    partner_folder.mkdir(parents=True, exist_ok=True)

    saved_paths = []

    try:
        for item in validated_files:
            extension = item["file_extension"]
            stored_file_name = f"{uuid.uuid4().hex}.{extension}"
            absolute_path = partner_folder / stored_file_name

            item["file_storage"].stream.seek(0)
            item["file_storage"].save(str(absolute_path))
            saved_paths.append(absolute_path)

            relative_path = absolute_path.relative_to(upload_root).as_posix()
            insert_partner_document(
                conn=conn,
                partner_short_name=partner_short_name,
                original_file_name=item["original_file_name"],
                stored_file_name=stored_file_name,
                file_path=relative_path,
                file_extension=extension,
                mime_type=item["mime_type"],
                file_size_bytes=item["file_size_bytes"],
                uploaded_by=uploaded_by,
            )

        return saved_paths

    except Exception:
        _remove_saved_document_paths(saved_paths)
        raise


def _resolve_partner_document_path(file_path):
    upload_root = _get_partner_document_upload_root()
    candidate_path = (upload_root / str(file_path or "")).resolve()

    try:
        candidate_path.relative_to(upload_root)
    except ValueError as exc:
        raise ValueError("Invalid document path.") from exc

    return candidate_path


def _remove_saved_document_paths(paths):
    for path in paths or []:
        try:
            Path(path).unlink(missing_ok=True)
        except Exception as exc:
            print("partner document file cleanup warning:", exc)


def _remove_relative_document_paths(relative_paths):
    for relative_path in relative_paths or []:
        try:
            _resolve_partner_document_path(relative_path).unlink(missing_ok=True)
        except Exception as exc:
            print("partner document file delete warning:", exc)


def _load_partner_documents_for_render(conn, partner_short_name):
    if not partner_short_name:
        return []
    try:
        return fetch_partner_documents(conn, partner_short_name)
    except Exception as exc:
        print("partner document read warning:", exc)
        return []


def _get_table_columns(conn, table_name):
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT COLUMN_NAME AS column_name, EXTRA AS extra
            FROM INFORMATION_SCHEMA.COLUMNS
            WHERE TABLE_SCHEMA = DATABASE()
              AND TABLE_NAME = %s
            ORDER BY ORDINAL_POSITION
            """,
            (table_name,),
        )
        return cur.fetchall() or []
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass


def _archive_partner_before_delete(conn, short_name, deleted_by):
    source_columns = _get_table_columns(conn, PARTNER_TABLE)
    audit_columns = _get_table_columns(conn, PARTNER_DELETE_AUDIT_TABLE)

    if not audit_columns:
        raise Exception(f"Audit table '{PARTNER_DELETE_AUDIT_TABLE}' does not exist or is not accessible.")

    source_map = {
        str(row.get("column_name") or "").strip().lower(): str(row.get("column_name") or "").strip()
        for row in source_columns
        if str(row.get("column_name") or "").strip()
    }

    # Do not require a specific audit column such as entered_by.
    # partner_reg_delete structures can differ between environments. The
    # insert below dynamically uses only columns that are present in both
    # tables, plus any supported delete-audit metadata columns.

    insert_columns = []
    select_expressions = []
    params = []

    for row in audit_columns:
        name = str(row.get("column_name") or "").strip()
        lower = name.lower()
        extra = str(row.get("extra") or "").lower()
        if not name or "auto_increment" in extra:
            continue

        insert_columns.append(_quote_mysql_identifier(name))

        if lower in ("entered_by", "deleted_by"):
            select_expressions.append("%s")
            params.append(deleted_by)
        elif lower in (
            "entered_date",
            "deleted_at",
            "delete_date",
            "deleted_date",
        ):
            select_expressions.append("NOW()")
        elif lower == "partner_status":
            # partner_reg_delete is an archive of a deleted Partner.
            # Do not carry Active/Inactive status from partner_reg.
            select_expressions.append("%s")
            params.append("Deleted")
        elif lower in source_map:
            select_expressions.append(_quote_mysql_identifier(source_map[lower]))
        else:
            insert_columns.pop()

    if not insert_columns:
        raise Exception("No compatible audit columns were found.")

    cur = None
    try:
        cur = conn.cursor()
        sql = f"""
            INSERT INTO {_quote_mysql_identifier(PARTNER_DELETE_AUDIT_TABLE)}
            ({', '.join(insert_columns)})
            SELECT {', '.join(select_expressions)}
            FROM {_quote_mysql_identifier(PARTNER_TABLE)}
            WHERE {_quote_mysql_identifier(PARTNER_PK)} = %s
        """
        params.append(short_name)
        cur.execute(sql, tuple(params))
        if cur.rowcount != 1:
            raise Exception("Partner audit record could not be created before deletion.")
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass


PARTNER_FORM_ALIASES = {
    "partner_type": ["partner_type", "partnerType", "partnertype"],
    "invoice_eligible": ["invoice_eligible", "invoiceEligible"],
    "hospital_coordinator": ["hospital_coordinator", "hospitalCoordinator"],
    "referral_partner": ["referral_partner", "referralPartner"],
    "direct_treating_hospital": ["direct_treating_hospital", "directTreatingHospital"],
    "contstart_date": ["contstart_date", "contract_start", "contractStart", "contstartDate"],
    "contend_date": ["contend_date", "contract_end", "contractEnd", "contendDate"],
    "legal_name": ["legal_name", "partner_name", "partnerName", "legalName"],
    "short_name": ["short_name", "shortName"],
    "partner_address": ["partner_address", "address", "partnerAddress"],
    "partner_country": ["partner_country", "country", "partnerCountry"],
    "partner_state": ["partner_state", "state", "partnerState"],
    "partner_city": ["partner_city", "city", "partnerCity"],
    "partner_pincode": ["partner_pincode", "pincode", "partnerPincode"],
    "partner_ccy": ["partner_ccy", "currency", "partnerCcy"],
    "partner_email": ["partner_email", "email", "partnerEmail"],
    "gst_required": ["gst_required", "gst", "gstRequired"],
    "gst_number": ["gst_number", "gst_no", "gstNo", "gstNumber"],
    "pan_number": ["pan_number", "pan_no", "panNo", "panNumber"],
    "placeof_supply": ["place_of_supply", "placeof_supply", "placeOfSupply", "placeofSupply"],
    "tax_preference": ["tax_preference", "taxPreference"],
    "tds_percentage": ["tds_percentage", "tds", "tdsPercent", "tds_percent", "tdsPercentage"],
    "agent_percentage": [
        "agent_percentage",
        "agency_percentage",
        "agencyPercentage",
        "agentPercentage",
        "agency_percent",
        "agencyPercent",
    ],
    "prim_contper": ["prim_contper", "primary_contact_person", "primaryContactPerson"],
    "prim_contno": ["prim_contno", "primary_contact_no", "primary_contact", "contact_no", "primaryContactNo"],
    "alt_contper": ["alt_contper", "secondary_contact_person", "secondaryContactPerson"],
    "alt_contno": ["alt_contno", "secondary_contact_no", "secondaryContactNo"],
    "partner_status": ["partner_status", "status", "partnerStatus"],
}


def _first_form_value(*names):
    for n in names:
        if not n:
            continue
        v = request.form.get(n)
        if v is not None:
            return v
    return None


def register_partner_routes(app):
    @app.route("/partner-view", methods=["GET"])
    @login_required
    def partner_view():
        conn = None
        try:
            conn = get_db()
            partners = fetch_partners_list(conn)
            return render_template("partnerview.html", user=current_user, partners=partners)
        except Exception as e:
            print("partner_view error:", e)
            flash(f"Partner View Error: {str(e)}", "error")
            return redirect(url_for("dashboard"))
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass


    @app.route("/hospital-partner-mapping", methods=["GET", "POST"])
    @login_required
    def hospital_partner_mapping():
        conn = None

        try:
            conn = get_db()
            cur = conn.cursor(dictionary=True)

            # -------------------------------------------------
            # POST: Save / Modify / Deactivate mapping
            # -------------------------------------------------
            if request.method == "POST":
                action = (request.form.get("action") or "save").strip().lower()

                mapping_id_raw = (request.form.get("mapping_id") or "").strip()
                mapping_id = int(mapping_id_raw) if mapping_id_raw.isdigit() else None

                acting_user = _get_logged_in_user_id()

                # ---------------------------------------------
                # Deactivate existing mapping
                # ---------------------------------------------
                if action == "deactivate":
                    if not mapping_id:
                        flash("Missing Mapping ID.", "error")
                        return redirect(url_for("hospital_partner_mapping"))

                    cur.execute(
                        """
                        UPDATE hospital_partner_mapping
                        SET
                            mapping_status = 'Inactive',
                            modified_by = %s,
                            modified_date = CURRENT_TIMESTAMP
                        WHERE mapping_id = %s
                        """,
                        (acting_user, mapping_id),
                    )

                    if cur.rowcount == 0:
                        conn.rollback()
                        flash("Mapping record not found.", "error")
                        return redirect(url_for("hospital_partner_mapping"))

                    conn.commit()

                    flash("Hospital Partner Mapping deactivated successfully.", "success")
                    return redirect(url_for("hospital_partner_mapping"))

                # ---------------------------------------------
                # Save / Update
                # ---------------------------------------------
                hospital_short_name = clean(
                    request.form.get("hospital_short_name")
                )
                coordination_partner_short_name = clean(
                    request.form.get("coordination_partner_short_name")
                )
                billing_partner_short_name = clean(
                    request.form.get("billing_partner_short_name")
                )

                effective_from = clean(
                    request.form.get("effective_from")
                )
                effective_to = clean(
                    request.form.get("effective_to")
                ) or None

                mapping_status = (
                    request.form.get("mapping_status") or "Active"
                ).strip()

                missing = []

                if not hospital_short_name:
                    missing.append("Hospital")

                if not coordination_partner_short_name:
                    missing.append("Coordination Partner")

                if not billing_partner_short_name:
                    missing.append("Billing Partner")

                if not effective_from:
                    missing.append("Effective From")

                if missing:
                    flash(
                        "Please fill mandatory fields: " + ", ".join(missing),
                        "error",
                    )
                    return redirect(url_for("hospital_partner_mapping"))

                if mapping_status not in ("Active", "Inactive"):
                    flash("Invalid Mapping Status.", "error")
                    return redirect(url_for("hospital_partner_mapping"))

                # ---------------------------------------------
                # Validate dates
                # ---------------------------------------------
                try:
                    effective_from_date = date.fromisoformat(effective_from)

                    effective_to_date = (
                        date.fromisoformat(effective_to)
                        if effective_to
                        else None
                    )

                except ValueError:
                    flash("Invalid Effective Date.", "error")
                    return redirect(url_for("hospital_partner_mapping"))

                if (
                    effective_to_date
                    and effective_to_date < effective_from_date
                ):
                    flash(
                        "Effective To cannot be earlier than Effective From.",
                        "error",
                    )
                    return redirect(url_for("hospital_partner_mapping"))

                # ---------------------------------------------
                # Validate Hospital LOV rule
                # ---------------------------------------------
                cur.execute(
                    """
                    SELECT short_name
                    FROM partner_reg
                    WHERE short_name = %s
                      AND partner_type = 'Hospital'
                      AND direct_treating_hospital = 'Yes'
                      AND LOWER(partner_status) = 'active'
                    LIMIT 1
                    """,
                    (hospital_short_name,),
                )

                if not cur.fetchone():
                    flash(
                        "Selected Hospital is not an Active Direct Treating Hospital.",
                        "error",
                    )
                    return redirect(url_for("hospital_partner_mapping"))

                # ---------------------------------------------
                # Validate Coordination Partner LOV rule
                # ---------------------------------------------
                cur.execute(
                    """
                    SELECT short_name
                    FROM partner_reg
                    WHERE short_name = %s
                      AND hospital_coordinator = 'Yes'
                      AND LOWER(partner_status) = 'active'
                    LIMIT 1
                    """,
                    (coordination_partner_short_name,),
                )

                if not cur.fetchone():
                    flash(
                        "Selected Coordination Partner is not maintained "
                        "as Hospital Coordinator = Yes.",
                        "error",
                    )
                    return redirect(url_for("hospital_partner_mapping"))

                # ---------------------------------------------
                # Validate Billing Partner LOV rule
                # ---------------------------------------------
                cur.execute(
                    """
                    SELECT short_name
                    FROM partner_reg
                    WHERE short_name = %s
                      AND invoice_eligible = 'Yes'
                      AND LOWER(partner_status) = 'active'
                    LIMIT 1
                    """,
                    (billing_partner_short_name,),
                )

                if not cur.fetchone():
                    flash(
                        "Selected Billing Partner is not maintained "
                        "as Invoice Eligible = Yes.",
                        "error",
                    )
                    return redirect(url_for("hospital_partner_mapping"))

                # ---------------------------------------------
                # Prevent same Hospital + Effective From duplicate
                # ---------------------------------------------
                cur.execute(
                    """
                    SELECT mapping_id
                    FROM hospital_partner_mapping
                    WHERE hospital_short_name = %s
                      AND effective_from = %s
                      AND mapping_id <> %s
                    LIMIT 1
                    """,
                    (
                        hospital_short_name,
                        effective_from,
                        mapping_id or 0,
                    ),
                )

                if cur.fetchone():
                    flash(
                        "A mapping for this Hospital already exists "
                        "with the same Effective From date.",
                        "error",
                    )
                    return redirect(url_for("hospital_partner_mapping"))

                # ---------------------------------------------
                # Prevent overlapping ACTIVE mappings
                # ---------------------------------------------
                if mapping_status == "Active":

                    comparison_end = effective_to or "9999-12-31"

                    cur.execute(
                        """
                        SELECT mapping_id
                        FROM hospital_partner_mapping
                        WHERE hospital_short_name = %s
                          AND mapping_status = 'Active'
                          AND mapping_id <> %s
                          AND effective_from <= %s
                          AND COALESCE(effective_to, '9999-12-31') >= %s
                        LIMIT 1
                        """,
                        (
                            hospital_short_name,
                            mapping_id or 0,
                            comparison_end,
                            effective_from,
                        ),
                    )

                    if cur.fetchone():
                        flash(
                            "An overlapping Active Mapping already exists "
                            "for this Hospital.",
                            "error",
                        )
                        return redirect(url_for("hospital_partner_mapping"))

                # ---------------------------------------------
                # Modify existing mapping
                # ---------------------------------------------
                if mapping_id:

                    cur.execute(
                        """
                        UPDATE hospital_partner_mapping
                        SET
                            hospital_short_name = %s,
                            coordination_partner_short_name = %s,
                            billing_partner_short_name = %s,
                            effective_from = %s,
                            effective_to = %s,
                            mapping_status = %s,
                            modified_by = %s,
                            modified_date = CURRENT_TIMESTAMP
                        WHERE mapping_id = %s
                        """,
                        (
                            hospital_short_name,
                            coordination_partner_short_name,
                            billing_partner_short_name,
                            effective_from,
                            effective_to,
                            mapping_status,
                            acting_user,
                            mapping_id,
                        ),
                    )

                    if cur.rowcount == 0:
                        conn.rollback()
                        flash("Mapping record not found.", "error")
                        return redirect(url_for("hospital_partner_mapping"))

                    success_message = (
                        "Hospital Partner Mapping updated successfully."
                    )

                # ---------------------------------------------
                # Create new mapping
                # ---------------------------------------------
                else:

                    cur.execute(
                        """
                        INSERT INTO hospital_partner_mapping
                        (
                            hospital_short_name,
                            coordination_partner_short_name,
                            billing_partner_short_name,
                            effective_from,
                            effective_to,
                            mapping_status,
                            entered_by
                        )
                        VALUES (%s, %s, %s, %s, %s, %s, %s)
                        """,
                        (
                            hospital_short_name,
                            coordination_partner_short_name,
                            billing_partner_short_name,
                            effective_from,
                            effective_to,
                            mapping_status,
                            acting_user,
                        ),
                    )

                    success_message = (
                        "Hospital Partner Mapping created successfully."
                    )

                conn.commit()

                flash(success_message, "success")

                return redirect(url_for("hospital_partner_mapping"))

            # -------------------------------------------------
            # GET: Hospital LOV
            # -------------------------------------------------
            cur.execute(
                """
                SELECT
                    short_name,
                    legal_name,
                    partner_city,
                    partner_state
                FROM partner_reg
                WHERE partner_type = 'Hospital'
                  AND direct_treating_hospital = 'Yes'
                  AND LOWER(partner_status) = 'active'
                ORDER BY legal_name, short_name
                """
            )

            hospital_lov = cur.fetchall() or []

            # -------------------------------------------------
            # GET: Coordination Partner LOV
            # -------------------------------------------------
            cur.execute(
                """
                SELECT
                    short_name,
                    legal_name,
                    partner_type,
                    partner_city,
                    partner_state
                FROM partner_reg
                WHERE hospital_coordinator = 'Yes'
                  AND LOWER(partner_status) = 'active'
                ORDER BY legal_name, short_name
                """
            )

            coordination_lov = cur.fetchall() or []

            # -------------------------------------------------
            # GET: Billing Partner LOV
            # -------------------------------------------------
            cur.execute(
                """
                SELECT
                    short_name,
                    legal_name,
                    partner_type,
                    partner_city,
                    partner_state
                FROM partner_reg
                WHERE invoice_eligible = 'Yes'
                  AND LOWER(partner_status) = 'active'
                ORDER BY legal_name, short_name
                """
            )

            billing_lov = cur.fetchall() or []

            # -------------------------------------------------
            # Existing mappings
            # -------------------------------------------------
            cur.execute(
                """
                SELECT
                    m.mapping_id,
                    m.hospital_short_name,
                    h.legal_name AS hospital_name,

                    m.coordination_partner_short_name,
                    c.legal_name AS coordination_partner_name,

                    m.billing_partner_short_name,
                    b.legal_name AS billing_partner_name,

                    m.effective_from,
                    m.effective_to,
                    m.mapping_status,
                    m.entered_by,
                    m.entered_date,
                    m.modified_by,
                    m.modified_date

                FROM hospital_partner_mapping m

                LEFT JOIN partner_reg h
                    ON h.short_name = m.hospital_short_name

                LEFT JOIN partner_reg c
                    ON c.short_name = m.coordination_partner_short_name

                LEFT JOIN partner_reg b
                    ON b.short_name = m.billing_partner_short_name

                ORDER BY
                    CASE
                        WHEN m.mapping_status = 'Active' THEN 0
                        ELSE 1
                    END,
                    m.hospital_short_name,
                    m.effective_from DESC,
                    m.mapping_id DESC
                """
            )

            mappings = cur.fetchall() or []

            # -------------------------------------------------
            # Modify mode
            # -------------------------------------------------
            edit_mapping = None

            edit_id = request.args.get("edit", type=int)

            if edit_id:
                cur.execute(
                    """
                    SELECT *
                    FROM hospital_partner_mapping
                    WHERE mapping_id = %s
                    """,
                    (edit_id,),
                )

                edit_mapping = cur.fetchone()

                if not edit_mapping:
                    flash("Mapping record not found.", "error")
                    return redirect(url_for("hospital_partner_mapping"))

            cur.close()

            return render_template(
                "hospitalpartnermapping.html",
                user=current_user,
                hospital_lov=hospital_lov,
                coordination_lov=coordination_lov,
                billing_lov=billing_lov,
                mappings=mappings,
                edit_mapping=edit_mapping,
                hpm_can_add=has_screen_permission(
                    "HOSPITAL_PARTNER_MAPPING", "add"
                ),
                hpm_can_edit=has_screen_permission(
                    "HOSPITAL_PARTNER_MAPPING", "edit"
                ),
                hpm_can_delete=has_screen_permission(
                    "HOSPITAL_PARTNER_MAPPING", "delete"
                ),
            )

        except Exception as e:
            print("hospital_partner_mapping error:", e)

            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass

            flash(
                "Hospital Partner Mapping Error: " + str(e),
                "error",
            )

            return redirect(url_for("dashboard"))

        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass


    @app.route("/partner-registration", methods=["GET", "POST"])
    @login_required
    def partner_registration_alias():
        mode = request.args.get("mode")
        key = request.args.get("key")
        if mode or key:
            return redirect(url_for("partner_creation", mode=mode, key=key))
        return redirect(url_for("partner_creation"))

    @app.route("/partner-creation", methods=["GET", "POST"])
    @login_required
    def partner_creation():
        conn = None

        mode = (request.args.get("mode") or request.form.get("mode") or "create").strip().lower()
        key = (request.args.get("key") or request.form.get("key") or "").strip()

        if mode == "edit":
            mode = "modify"

        if mode not in ("create", "view", "modify"):
            mode = "create"

        try:
            conn = get_db()

            countries = fetch_countries(conn)
            try:
                currencies = fetch_currencies(conn)
            except Exception as e:
                print("ccy_master read warning:", e)
                currencies = []

            partner = None
            partner_documents = []

            if request.method == "GET":
                if mode in ("view", "modify"):
                    if not key:
                        flash("Missing partner key (short name).", "error")
                        return redirect(url_for("partner_view"))

                    partner = fetch_partner_by_short_name(conn, key)
                    if not partner:
                        flash("Partner not found for short name: " + key, "error")
                        return redirect(url_for("partner_view"))

                    partner_documents = _load_partner_documents_for_render(conn, key)

                return render_template(
                    "partnercreation.html",
                    user=current_user,
                    mode=mode,
                    key=key,
                    partner=partner,
                    countries=countries,
                    currencies=currencies,
                    partner_documents=partner_documents,
                )

            posted_db = {}
            for dbcol in PARTNER_DB_COLS:
                aliases = PARTNER_FORM_ALIASES.get(dbcol, [dbcol])
                posted_db[dbcol] = clean(_first_form_value(*aliases))

            # Partner capability defaults.
            # This keeps the existing Partner screen backward-compatible
            # until the new capability controls are added to the HTML.
            partner_type = posted_db.get("partner_type")

            capability_defaults = {
                "Hospital": {
                    "invoice_eligible": "Yes",
                    "hospital_coordinator": "Yes",
                    "referral_partner": "No",
                    "direct_treating_hospital": "Yes",
                },
                "Agency": {
                    "invoice_eligible": "No",
                    "hospital_coordinator": "No",
                    "referral_partner": "Yes",
                    "direct_treating_hospital": "No",
                },
                "Service Partner": {
                    "invoice_eligible": "Yes",
                    "hospital_coordinator": "Yes",
                    "referral_partner": "No",
                    "direct_treating_hospital": "No",
                },
            }

            defaults = capability_defaults.get(
                partner_type,
                {
                    "invoice_eligible": "No",
                    "hospital_coordinator": "No",
                    "referral_partner": "No",
                    "direct_treating_hospital": "No",
                },
            )

            for capability_field, default_value in defaults.items():
                if posted_db.get(capability_field) not in ("Yes", "No"):
                    posted_db[capability_field] = default_value

            short_name = posted_db.get("short_name")
            original_short_name = (request.form.get("original_short_name") or "").strip() or key
            uploaded_documents = request.files.getlist("partner_documents")

            if not posted_db.get("partner_status"):
                if mode == "modify" and original_short_name:
                    existing = fetch_partner_by_short_name(conn, original_short_name)
                    posted_db["partner_status"] = (existing or {}).get("partner_status") or "Active"
                else:
                    posted_db["partner_status"] = "Active"

            missing = []
            if not short_name:
                missing.append("Short Name")
            if not posted_db.get("legal_name"):
                missing.append("Legal Name")
            if missing:
                flash("Please fill mandatory fields: " + ", ".join(missing), "error")
                if mode in ("view", "modify") and original_short_name:
                    partner = fetch_partner_by_short_name(conn, original_short_name)
                    partner_documents = _load_partner_documents_for_render(conn, original_short_name)
                return render_template(
                    "partnercreation.html",
                    user=current_user,
                    mode=mode,
                    key=key,
                    partner=partner,
                    countries=countries,
                    currencies=currencies,
                    partner_documents=partner_documents,
                )

            cur = conn.cursor()
            saved_document_paths = []
            try:
                acting_user = _get_logged_in_user_id()

                if mode == "modify":
                    if not original_short_name:
                        flash("Missing original short name for update.", "error")
                        return redirect(url_for("partner_view"))

                    set_parts = [f"{c} = %s" for c in PARTNER_DB_COLS]
                    values = [posted_db[c] for c in PARTNER_DB_COLS]
                    values.append(original_short_name)

                    sql = f"UPDATE {PARTNER_TABLE} SET " + ", ".join(set_parts) + f" WHERE {PARTNER_PK} = %s"
                    cur.execute(sql, tuple(values))

                    saved_document_paths = _save_partner_documents(
                        conn=conn,
                        partner_short_name=original_short_name,
                        uploaded_files=uploaded_documents,
                        uploaded_by=acting_user,
                    )

                    conn.commit()
                    document_count = len(saved_document_paths)
                    if document_count:
                        flash(
                            f"Partner updated successfully. {document_count} document(s) uploaded.",
                            "success",
                        )
                    else:
                        flash("Partner updated successfully.", "success")
                    return redirect(url_for("partner_view"))

                # Create mode:
                # Store the logged-in USERID in partner_reg.entered_by.
                cols = PARTNER_DB_COLS[:] + ["entered_by"]
                placeholders = ["%s"] * len(cols)
                values = [posted_db[c] for c in PARTNER_DB_COLS] + [acting_user]

                sql = (
                    f"INSERT INTO {PARTNER_TABLE} ("
                    + ", ".join(cols)
                    + ") VALUES ("
                    + ", ".join(placeholders)
                    + ")"
                )
                cur.execute(sql, tuple(values))

                saved_document_paths = _save_partner_documents(
                    conn=conn,
                    partner_short_name=short_name,
                    uploaded_files=uploaded_documents,
                    uploaded_by=acting_user,
                )

                conn.commit()
                document_count = len(saved_document_paths)
                if document_count:
                    flash(
                        f"Partner created successfully. {document_count} document(s) uploaded.",
                        "success",
                    )
                else:
                    flash("Partner created successfully.", "success")
                return redirect(url_for("partner_creation"))

            except Exception as e:
                conn.rollback()
                _remove_saved_document_paths(saved_document_paths)
                print("partner_creation write error:", e)
                flash(f"Partner save failed: {str(e)}", "error")
                if mode in ("view", "modify") and original_short_name:
                    partner = fetch_partner_by_short_name(conn, original_short_name)
                    partner_documents = _load_partner_documents_for_render(conn, original_short_name)
                return render_template(
                    "partnercreation.html",
                    user=current_user,
                    mode=mode,
                    key=key,
                    partner=partner,
                    countries=countries,
                    currencies=currencies,
                    partner_documents=partner_documents,
                )
            finally:
                try:
                    cur.close()
                except Exception:
                    pass

        except Exception as e:
            print("partner_creation error:", e)
            flash(f"Partner Error: {str(e)}", "error")
            return redirect(url_for("partner_creation"))
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/partner-document/<int:document_id>/view", methods=["GET"])
    @login_required
    def partner_document_view(document_id):
        conn = None
        try:
            conn = get_db()
            document = fetch_partner_document_by_id(conn, document_id)
            if not document or document.get("document_status") != "ACTIVE":
                abort(404)

            document_path = _resolve_partner_document_path(document.get("file_path"))
            if not document_path.is_file():
                abort(404)

            return send_file(
                document_path,
                mimetype=document.get("mime_type") or "application/octet-stream",
                as_attachment=False,
                download_name=document.get("original_file_name") or document_path.name,
                conditional=True,
            )
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/partner-document/<int:document_id>/download", methods=["GET"])
    @login_required
    def partner_document_download(document_id):
        conn = None
        try:
            conn = get_db()
            document = fetch_partner_document_by_id(conn, document_id)
            if not document or document.get("document_status") != "ACTIVE":
                abort(404)

            document_path = _resolve_partner_document_path(document.get("file_path"))
            if not document_path.is_file():
                abort(404)

            return send_file(
                document_path,
                mimetype=document.get("mime_type") or "application/octet-stream",
                as_attachment=True,
                download_name=document.get("original_file_name") or document_path.name,
                conditional=True,
            )
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/partner-document/<int:document_id>/delete", methods=["POST"])
    @login_required
    def partner_document_delete(document_id):
        conn = None
        document = None

        try:
            conn = get_db()
            document = fetch_partner_document_by_id(conn, document_id)

            if not document or document.get("document_status") != "ACTIVE":
                flash("Partner document was not found or was already deleted.", "warning")
                return redirect(url_for("partner_view"))

            affected = mark_partner_document_deleted(
                conn=conn,
                document_id=document_id,
                deleted_by=_get_logged_in_user_id(),
            )

            if affected != 1:
                raise Exception("Document could not be marked as deleted.")

            conn.commit()
            _remove_relative_document_paths([document.get("file_path")])
            flash("Partner document deleted successfully.", "success")

            return redirect(
                url_for(
                    "partner_creation",
                    mode="modify",
                    key=document.get("partner_short_name"),
                )
            )

        except Exception as exc:
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass
            print("partner_document_delete error:", exc)
            flash(f"Document delete failed: {exc}", "error")

            if document and document.get("partner_short_name"):
                return redirect(
                    url_for(
                        "partner_creation",
                        mode="modify",
                        key=document.get("partner_short_name"),
                    )
                )
            return redirect(url_for("partner_view"))

        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/partner-delete/<string:short_name>", methods=["POST"])
    @login_required
    def partner_delete(short_name):
        conn = None
        cur = None
        partner_document_paths = []

        try:
            short_name = (short_name or "").strip()

            if not short_name:
                flash("Partner Short Name is required for deletion.", "error")
                return redirect(url_for("partner_view"))

            conn = get_db()
            cur = conn.cursor()

            cur.execute(
                f"""
                SELECT COUNT(*)
                FROM {PARTNER_TABLE}
                WHERE {PARTNER_PK} = %s
                """,
                (short_name,),
            )

            row = cur.fetchone()
            partner_exists = int(row[0] or 0) if row else 0

            if partner_exists == 0:
                flash(f"Partner not found for short name: {short_name}", "error")
                return redirect(url_for("partner_view"))

            dependencies = _get_partner_dependencies(conn=conn, short_name=short_name)

            if dependencies:
                conn.rollback()
                flash(_format_partner_dependency_message(dependencies), "warning")
                return redirect(url_for("partner_view"))

            deleted_by = _get_logged_in_user_id()

            _archive_partner_before_delete(
                conn=conn,
                short_name=short_name,
                deleted_by=deleted_by,
            )

            partner_document_paths = fetch_partner_document_paths(conn, short_name)
            delete_partner_document_rows(conn, short_name)

            cur.execute(
                f"DELETE FROM {PARTNER_TABLE} WHERE {PARTNER_PK} = %s",
                (short_name,),
            )

            if cur.rowcount != 1:
                raise Exception("Partner record could not be deleted after audit creation.")

            conn.commit()
            _remove_relative_document_paths(partner_document_paths)
            flash("Partner Deleted Successfully", "success")

        except Exception as e:
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass
            print("partner_delete error:", e)
            flash(f"Delete failed: {e}", "error")

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

        return redirect(url_for("partner_view"))

