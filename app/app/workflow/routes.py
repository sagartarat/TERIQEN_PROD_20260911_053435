import io
import os
import mimetypes
import traceback
import mysql.connector

from flask import render_template, redirect, url_for, flash, request, abort, send_file
from flask_login import login_required, current_user
from werkzeug.exceptions import HTTPException

from app.common.db import get_db
from app.common.db_metadata import get_table_schema_rows
from app.file_upload.patient_file_upload import process_patient_upload
from app.file_upload.patient_workflow_upload import process_patient_upload as process_patient_workflow_upload
from app.workflow.helpers import (
    fetch_workflow_records_for_status_change,
    build_lead_options_from_rows,
    build_patient_grid_rows_from_workflow_rows,
    fetch_workflow_record_row_by_ld_internalno,
    build_lead_view_model,
    build_patient_view_model,
    build_attender_view_model,
    fetch_vil_documents_by_passport_no,
    fetch_workflow_status_options_from_status_master,
    fetch_status_color_map,
    fetch_ivacs,
    update_patient_workflow_status,
    view_vil_doc_file,
)


def _get_file_upload_schema_rows(conn):
    try:
        return get_table_schema_rows(conn, "file_upload")
    except Exception:
        return []


def _get_file_upload_columns(conn):
    columns = set()
    schema_rows = _get_file_upload_schema_rows(conn)
    for row in schema_rows:
        field_name = (row.get("Field") or "").strip()
        if field_name:
            columns.add(field_name)
    return columns


def _get_file_upload_key_column(conn):
    schema_rows = _get_file_upload_schema_rows(conn)

    for row in schema_rows:
        field_name = (row.get("Field") or "").strip()
        key_type = (row.get("Key") or "").strip().upper()
        if field_name and key_type == "PRI":
            return field_name

    for row in schema_rows:
        field_name = (row.get("Field") or "").strip()
        extra = (row.get("Extra") or "").strip().lower()
        if field_name and "auto_increment" in extra:
            return field_name

    candidate_columns = ["id", "fileupld_id", "upload_id", "seqno", "file_upload_id"]
    available_columns = {(row.get("Field") or "").strip() for row in schema_rows if (row.get("Field") or "").strip()}

    for col in candidate_columns:
        if col in available_columns:
            return col

    return None


def _build_file_upload_filter_parts(columns, request_args):
    where_clauses = ["WHERE 1=1"]
    params = []

    file_name = (request_args.get("file_name") or "").strip()
    file_status = (request_args.get("fileupld_status") or "").strip()
    fileupld_by = (request_args.get("fileupld_by") or "").strip()

    if file_name:
        where_clauses.append("AND file_name LIKE %s")
        params.append(f"%{file_name}%")

    if file_status and "fileupld_status" in columns:
        where_clauses.append("AND UPPER(TRIM(fileupld_status)) = %s")
        params.append(file_status.upper())

    if fileupld_by and "fileupld_by" in columns:
        where_clauses.append("AND fileupld_by LIKE %s")
        params.append(f"%{fileupld_by}%")

    return "\n".join(where_clauses), params


def _fetch_file_upload_total_count(conn, request_args):
    cursor = None
    try:
        columns = _get_file_upload_columns(conn)
        where_sql, params = _build_file_upload_filter_parts(columns, request_args)

        query = f"""
            SELECT COUNT(*) AS total_count
            FROM file_upload
            {where_sql}
        """

        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, tuple(params))
        row = cursor.fetchone() or {}
        return int(row.get("total_count") or 0)

    except Exception:
        return 0
    finally:
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass


def _fetch_file_upload_rows(conn, request_args, page=1, per_page=10):
    rows = []
    cursor = None
    try:
        columns = _get_file_upload_columns(conn)
        key_column = _get_file_upload_key_column(conn)

        select_parts = []

        if key_column:
            select_parts.append(f"{key_column} AS id")
        else:
            select_parts.append("NULL AS id")

        if "file_name" in columns:
            select_parts.append("file_name")
        else:
            select_parts.append("NULL AS file_name")

        if "upload_type" in columns:
            select_parts.append("upload_type")
        else:
            select_parts.append("NULL AS upload_type")

        if "fileupld_status" in columns:
            select_parts.append("fileupld_status")
        else:
            select_parts.append("NULL AS fileupld_status")

        if "fileupld_by" in columns:
            select_parts.append("fileupld_by")
        else:
            select_parts.append("NULL AS fileupld_by")

        if "fileupld_at" in columns:
            select_parts.append("fileupld_at")
        elif "processed_on" in columns:
            select_parts.append("processed_on AS fileupld_at")
        else:
            select_parts.append("NULL AS fileupld_at")

        if "total_records" in columns:
            select_parts.append("total_records")
        else:
            select_parts.append("NULL AS total_records")

        if "success_records" in columns:
            select_parts.append("success_records")
        else:
            select_parts.append("NULL AS success_records")

        if "rejected_records" in columns:
            select_parts.append("rejected_records")
        else:
            select_parts.append("NULL AS rejected_records")

        if "reject_reasons" in columns:
            select_parts.append("reject_reasons AS rejection_remarks")
        elif "rejection_remarks" in columns:
            select_parts.append("rejection_remarks")
        else:
            select_parts.append("NULL AS rejection_remarks")

        where_sql, params = _build_file_upload_filter_parts(columns, request_args)

        query = f"""
            SELECT {", ".join(select_parts)}
            FROM file_upload
            {where_sql}
        """

        if "fileupld_at" in columns:
            query += " ORDER BY fileupld_at DESC"
        elif "processed_on" in columns:
            query += " ORDER BY processed_on DESC"
        elif key_column:
            query += f" ORDER BY {key_column} DESC"
        else:
            query += " ORDER BY file_name ASC"

        try:
            page = int(page)
        except Exception:
            page = 1

        try:
            per_page = int(per_page)
        except Exception:
            per_page = 10

        if page < 1:
            page = 1

        allowed_page_sizes = {5, 10, 20, 50, 100}
        if per_page not in allowed_page_sizes:
            per_page = 10

        offset = (page - 1) * per_page
        query += " LIMIT %s OFFSET %s"
        params.extend([per_page, offset])

        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, tuple(params))
        rows = cursor.fetchall() or []

    except Exception:
        rows = []
    finally:
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass

    return rows


def _guess_mime_type(file_name, file_ext, db_mime_type):
    mime_type = (db_mime_type or "").strip()
    if mime_type:
        return mime_type

    ext = (file_ext or "").strip().lower()
    if ext and not ext.startswith("."):
        ext = f".{ext}"

    if not ext and file_name:
        _, ext = os.path.splitext(file_name)

    guessed, _ = mimetypes.guess_type(file_name or f"file{ext or ''}")
    return guessed or "application/octet-stream"


def _normalize_file_blob(file_blob):
    if file_blob is None:
        return None

    if isinstance(file_blob, memoryview):
        return file_blob.tobytes()

    if isinstance(file_blob, bytearray):
        return bytes(file_blob)

    if isinstance(file_blob, bytes):
        return file_blob

    try:
        return bytes(file_blob)
    except Exception:
        return None


def _normalize_download_name(file_name, file_ext, upload_id):
    final_name = (file_name or "").strip()
    ext = (file_ext or "").strip().lower()

    if ext and not ext.startswith("."):
        ext = f".{ext}"

    if not final_name:
        final_name = f"upload_{upload_id}{ext or ''}"
    elif ext and not final_name.lower().endswith(ext.lower()):
        final_name = f"{final_name}{ext}"

    return final_name


def _fetch_file_upload_record_by_id(conn, upload_id):
    cursor = None
    try:
        columns = _get_file_upload_columns(conn)
        key_column = _get_file_upload_key_column(conn)

        if not key_column:
            return None

        select_parts = [f"{key_column} AS id"]

        if "file_name" in columns:
            select_parts.append("file_name")
        else:
            select_parts.append("NULL AS file_name")

        if "fileupld_content" in columns:
            select_parts.append("fileupld_content")
        else:
            select_parts.append("NULL AS fileupld_content")

        if "mime_type" in columns:
            select_parts.append("mime_type")
        else:
            select_parts.append("NULL AS mime_type")

        if "file_ext" in columns:
            select_parts.append("file_ext")
        else:
            select_parts.append("NULL AS file_ext")

        query = f"""
            SELECT {", ".join(select_parts)}
            FROM file_upload
            WHERE {key_column} = %s
            LIMIT 1
        """

        cursor = conn.cursor(dictionary=True)
        cursor.execute(query, (upload_id,))
        row = cursor.fetchone()

        if not row:
            return None

        row["fileupld_content"] = _normalize_file_blob(row.get("fileupld_content"))
        row["file_name"] = _normalize_download_name(
            row.get("file_name"),
            row.get("file_ext"),
            upload_id,
        )
        row["mime_type"] = _guess_mime_type(
            row.get("file_name"),
            row.get("file_ext"),
            row.get("mime_type"),
        )

        return row

    except Exception:
        return None
    finally:
        try:
            if cursor:
                cursor.close()
        except Exception:
            pass


def register_workflow_routes(app):
    @app.route("/wrkflwstatuschange", methods=["GET", "POST"])
    @login_required
    def wrkflwstatuschange():
        conn = None
        lead_documents = []
        try:
            conn = get_db()

            if request.method == "POST":
                try:
                    result = update_patient_workflow_status(conn, current_user.user_code)
                    conn.commit()

                    flash(
                        f"Workflow updated successfully. "
                        f"Lead No: {result['ld_internalno']} | "
                        f"New Status: {result['wrkflw_status']} | "
                        f"Updated By: {result['entered_by']} | "
                        f"Updated On: {result['entered_date']}",
                        "success"
                    )
                    return redirect(url_for("wrkflwstatuschange", lead_selector=result["ld_internalno"]))

                except mysql.connector.Error as e:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    print("wrkflwstatuschange DB update error:", e)
                    flash(f"Workflow update failed: {str(e)}", "error")

                except Exception as e:
                    try:
                        conn.rollback()
                    except Exception:
                        pass
                    print("wrkflwstatuschange update error:", e)
                    flash(f"Workflow update failed: {str(e)}", "error")

            selected_lead_id = (request.args.get("lead_selector") or "").strip()

            workflow_rows = fetch_workflow_records_for_status_change(conn)
            lead_options = build_lead_options_from_rows(workflow_rows)
            patient_grid_rows = build_patient_grid_rows_from_workflow_rows(workflow_rows)

            selected_row = None
            if selected_lead_id:
                selected_row = fetch_workflow_record_row_by_ld_internalno(conn, selected_lead_id)

            lead = build_lead_view_model(selected_row) if selected_row else None
            patient = build_patient_view_model(selected_row) if selected_row else None
            attender1 = build_attender_view_model(selected_row, 1) if selected_row else None
            attender2 = build_attender_view_model(selected_row, 2) if selected_row else None
            attender3 = build_attender_view_model(selected_row, 3) if selected_row else None

            if lead and lead.get("passport_no"):
                lead_documents = fetch_vil_documents_by_passport_no(conn, lead.get("passport_no"))
            else:
                lead_documents = []

            workflow_status_options = fetch_workflow_status_options_from_status_master(conn)
            status_color_map = fetch_status_color_map(conn)

            ivac_values = fetch_ivacs(conn)
            ivac_options = [{"value": x, "label": x} for x in ivac_values]

            return render_template(
                "wrkflwstatuschange.html",
                user=current_user,
                lead_options=lead_options,
                patient_grid_rows=patient_grid_rows,
                selected_lead_id=selected_lead_id,
                lead=lead,
                patient=patient,
                attender1=attender1,
                attender2=attender2,
                attender3=attender3,
                workflow_status_options=workflow_status_options,
                status_color_map=status_color_map,
                ivac_options=ivac_options,
                lead_documents=lead_documents,
            )

        except Exception as e:
            print("wrkflwstatuschange error:", e)
            traceback.print_exc()
            flash(f"Workflow Status Change Error: {str(e)}", "error")
            return redirect(url_for("dashboard"))
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/view-vil-doc/<int:doc_id>", methods=["GET"])
    @login_required
    def view_vil_doc(doc_id):
        return view_vil_doc_file(doc_id)

    @app.route("/file-upload", methods=["GET", "POST"])
    @login_required
    def file_upload():
        conn = None
        form_data = {}

        try:
            if request.method == "POST":
                form_data = request.form.to_dict()

                upload_type = (request.form.get("upload_type") or "").strip()
                remarks = (request.form.get("remarks") or "").strip() or None
                upload_file = request.files.get("upload_file")

                if not upload_type:
                    error_msg = "Please select Upload Type."
                    flash(error_msg, "error")
                    return render_template(
                        "file_upload.html",
                        user=current_user,
                        upload_success=None,
                        upload_error=error_msg,
                        form_data=form_data,
                    )

                if not upload_file or not upload_file.filename:
                    error_msg = "Please choose a file to upload."
                    flash(error_msg, "error")
                    return render_template(
                        "file_upload.html",
                        user=current_user,
                        upload_success=None,
                        upload_error=error_msg,
                        form_data=form_data,
                    )

                allowed_ext = {"csv", "xlsx", "xls"}
                ext = os.path.splitext(upload_file.filename)[1].lower().replace(".", "")

                if ext not in allowed_ext:
                    error_msg = "Unsupported file type. Allowed: .csv, .xlsx, .xls"
                    flash(error_msg, "error")
                    return render_template(
                        "file_upload.html",
                        user=current_user,
                        upload_success=None,
                        upload_error=error_msg,
                        form_data=form_data,
                    )

                conn = get_db()
                user_code = getattr(current_user, "user_code", None) or "SYSTEM"

                if upload_type == "PATIENT_UPLOAD":
                    result = process_patient_upload(
                        conn,
                        uploaded_file=upload_file,
                        original_filename=upload_file.filename,
                        uploaded_by=user_code,
                        remarks=remarks,
                    )
                elif upload_type == "PATIENT_WORKFLOW_UPLOAD":
                    result = process_patient_workflow_upload(
                        conn,
                        uploaded_file=upload_file,
                        original_filename=upload_file.filename,
                        uploaded_by=user_code,
                        remarks=remarks,
                    )
                else:
                    error_msg = f"Upload type '{upload_type}' is not implemented yet."
                    flash(error_msg, "error")
                    return render_template(
                        "file_upload.html",
                        user=current_user,
                        upload_success=None,
                        upload_error=error_msg,
                        form_data=form_data,
                    )

                if result.get("success"):
                    success_msg = (
                        f"{result.get('message', 'Upload completed successfully.')} "
                        f"Total: {result.get('total_records', 0)}, "
                        f"Success: {result.get('success_records', 0)}, "
                        f"Rejected: {result.get('rejected_records', 0)}"
                    )
                    flash(success_msg, "success")
                    return render_template(
                        "file_upload.html",
                        user=current_user,
                        upload_success=success_msg,
                        upload_error=None,
                        form_data={},
                    )

                error_msg = result.get("message", "Upload failed.")

                flash(error_msg, "error")
                return render_template(
                    "file_upload.html",
                    user=current_user,
                    upload_success=None,
                    upload_error=error_msg,
                    form_data=form_data,
                )

            return render_template(
                "file_upload.html",
                user=current_user,
                upload_success=None,
                upload_error=None,
                form_data={},
            )

        except Exception as e:
            print("file_upload route error:", e)
            traceback.print_exc()

            error_msg = f"Upload failed: {str(e)}"
            flash(error_msg, "error")

            return render_template(
                "file_upload.html",
                user=current_user,
                upload_success=None,
                upload_error=error_msg,
                form_data=form_data,
            )

        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/file-upload-status", methods=["GET"])
    @login_required
    def file_upload_status():
        conn = None
        upload_rows = []
        try:
            conn = get_db()

            try:
                page = int((request.args.get("page") or "1").strip())
            except Exception:
                page = 1

            try:
                per_page = int((request.args.get("per_page") or "10").strip())
            except Exception:
                per_page = 10

            if page < 1:
                page = 1

            if per_page not in {5, 10, 20, 50, 100}:
                per_page = 10

            total_records = _fetch_file_upload_total_count(conn, request.args)
            upload_rows = _fetch_file_upload_rows(conn, request.args, page=page, per_page=per_page)

            total_pages = (total_records + per_page - 1) // per_page if total_records > 0 else 1
            if page > total_pages:
                page = total_pages

            start_record = ((page - 1) * per_page) + 1 if total_records > 0 else 0
            end_record = min(page * per_page, total_records) if total_records > 0 else 0

            return render_template(
                "file_upload_status.html",
                user=current_user,
                upload_rows=upload_rows,
                page=page,
                per_page=per_page,
                total_records=total_records,
                total_pages=total_pages,
                start_record=start_record,
                end_record=end_record,
            )

        except Exception as e:
            print("file_upload_status error:", e)
            traceback.print_exc()
            flash(f"File Upload Status Error: {str(e)}", "error")
            return render_template(
                "file_upload_status.html",
                user=current_user,
                upload_rows=[],
                page=1,
                per_page=10,
                total_records=0,
                total_pages=1,
                start_record=0,
                end_record=0,
            )
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/file-upload/view/<path:upload_id>", methods=["GET"])
    @login_required
    def view_uploaded_file(upload_id):
        return redirect(url_for("download_uploaded_file", upload_id=upload_id))

    @app.route("/file-upload/download/<path:upload_id>", methods=["GET"])
    @login_required
    def download_uploaded_file(upload_id):
        conn = None
        try:
            conn = get_db()
            record = _fetch_file_upload_record_by_id(conn, upload_id)

            if not record:
                abort(404, description="Uploaded file record not found.")

            file_blob = record.get("fileupld_content")
            file_name = (record.get("file_name") or f"upload_{upload_id}").strip()
            mime_type = (record.get("mime_type") or "").strip() or "application/octet-stream"

            if not file_blob:
                abort(404, description="Uploaded file content is empty.")

            return send_file(
                io.BytesIO(file_blob),
                mimetype=mime_type,
                as_attachment=True,
                download_name=file_name,
            )

        except HTTPException:
            raise
        except Exception as e:
            print("download_uploaded_file error:", e)
            traceback.print_exc()
            abort(500, description=f"Download failed: {str(e)}")
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
