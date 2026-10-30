import re
import datetime

from flask import render_template, flash, redirect, url_for, make_response
from flask_login import login_required, current_user

from app.common.db import get_db
from app.common.utils import clean, clean_keep_empty
from app.system_control.invoice_template_builder.invoice_template_builder_service import (
    resolve_invoice_template_for_runtime,
)

try:
    from weasyprint import HTML
except Exception:
    HTML = None


def normalize_print_status(status_value):
    val = (status_value or "").strip().upper()
    val = re.sub(r"\s+", " ", val)

    if val == "PRINTED":
        return "PRINTED"

    if val in ("PENDING", "YET TO PRINT", "", "NULL", "NONE"):
        return "PENDING"

    return val


def normalize_yes_no(value, default="N"):
    val = clean_keep_empty(value).strip().upper()
    if val in ("Y", "YES", "TRUE", "1", "ON"):
        return "Y"
    if val in ("N", "NO", "FALSE", "0", "OFF"):
        return "N"
    return default


def is_template_narration_enabled(template_data):
    if not template_data:
        return False

    narration_value = (
        template_data.get("customer_narration")
        or template_data.get("custom_narration")
        or template_data.get("narration_enabled")
        or template_data.get("template_customer_narration")
        or template_data.get("template_narration_enabled")
    )

    return normalize_yes_no(narration_value, "N") == "Y"


def format_invoice_date(value):
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()


def format_printed_datetime(value):
    if value is None:
        return ""
    if isinstance(value, datetime.datetime):
        return value.strftime("%Y-%m-%d %H:%M:%S")
    if isinstance(value, datetime.date):
        return value.strftime("%Y-%m-%d")
    return str(value).strip()


def to_float(value, default=0.0):
    try:
        if value is None:
            return float(default)
        if isinstance(value, (int, float)):
            return float(value)
        txt = str(value).strip()
        txt = re.sub(r"[^0-9.\-]", "", txt)
        return float(txt) if txt else float(default)
    except Exception:
        return float(default)


def format_money(value, decimals=2):
    return f"{to_float(value):,.{decimals}f}"


def format_invoice_display_date(value):
    if value is None:
        return ""

    if isinstance(value, datetime.datetime):
        return value.strftime("%d-%m-%Y")

    if isinstance(value, datetime.date):
        return value.strftime("%d-%m-%Y")

    text = str(value).strip()
    if not text:
        return ""

    date_formats = [
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%Y/%m/%d",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%d-%m-%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
    ]

    for fmt in date_formats:
        try:
            dt = datetime.datetime.strptime(text, fmt)
            return dt.strftime("%d-%m-%Y")
        except ValueError:
            continue

    return text


def format_printed_datetime_display(value):
    if value is None:
        return ""

    if isinstance(value, datetime.datetime):
        return value.strftime("%d-%m-%Y %I:%M %p")

    if isinstance(value, datetime.date):
        dt = datetime.datetime.combine(value, datetime.time.min)
        return dt.strftime("%d-%m-%Y %I:%M %p")

    text = str(value).strip()
    if not text:
        return ""

    datetime_formats = [
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M:%S.%f",
        "%d-%m-%Y %H:%M:%S",
        "%d/%m/%Y %H:%M:%S",
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
    ]

    for fmt in datetime_formats:
        try:
            dt = datetime.datetime.strptime(text, fmt)
            return dt.strftime("%d-%m-%Y %I:%M %p")
        except ValueError:
            continue

    return text


def _normalize_loop_index_references(value):
    """
    Saved invoice template cells may contain {{loop.index}}.

    At runtime, item rows already carry:
    - sl_no
    - loop_index
    - loop_index0
    - loop_revindex

    Do NOT convert to {{item.sl_no}} because the current invoice builder
    renderer treats cell values as placeholder text and resolves simple
    placeholders like {{patient_name}}, {{inv_amount}}, {{sl_no}}.
    """
    if isinstance(value, str):
        value = re.sub(r"\{\{\s*loop\.index\s*\}\}", "{{sl_no}}", value)
        value = re.sub(r"\{\{\s*loop\.index0\s*\}\}", "{{loop_index0}}", value)
        value = re.sub(r"\{\{\s*loop\.revindex\s*\}\}", "{{loop_revindex}}", value)
        return value

    if isinstance(value, list):
        return [_normalize_loop_index_references(v) for v in value]

    if isinstance(value, tuple):
        return tuple(_normalize_loop_index_references(v) for v in value)

    if isinstance(value, dict):
        return {k: _normalize_loop_index_references(v) for k, v in value.items()}

    return value


def fetch_invoice_filter_options(conn):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT DISTINCT invoice_number
            FROM invoice_master
            WHERE invoice_number IS NOT NULL
              AND TRIM(invoice_number) <> ''
            ORDER BY invoice_number ASC
            """
        )
        invoice_rows = cur.fetchall() or []

        cur.execute(
            """
            SELECT DISTINCT hospital_name
            FROM invoice_master
            WHERE hospital_name IS NOT NULL
              AND TRIM(hospital_name) <> ''
            ORDER BY hospital_name ASC
            """
        )
        hospital_rows = cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass

    invoice_numbers = []
    for row in invoice_rows:
        val = clean_keep_empty(row.get("invoice_number"))
        if val:
            invoice_numbers.append(val)

    hospital_names = []
    for row in hospital_rows:
        val = clean_keep_empty(row.get("hospital_name"))
        if val:
            hospital_names.append(val)

    return {
        "invoice_numbers": invoice_numbers,
        "hospital_names": hospital_names
    }


def build_invoice_search_where_clause(
    invoice_number=None,
    hospital_name=None,
    from_date=None,
    to_date=None,
    print_status=None
):
    from app.common.utils import date_to_db

    sql = " WHERE 1=1 "
    params = []

    invoice_number = clean(invoice_number)
    hospital_name = clean(hospital_name)
    from_date = date_to_db(from_date)
    to_date = date_to_db(to_date)
    print_status = (print_status or "ALL").strip().upper()

    if invoice_number:
        sql += " AND invoice_number = %s"
        params.append(invoice_number)

    if hospital_name:
        sql += " AND hospital_name = %s"
        params.append(hospital_name)

    if from_date:
        sql += " AND DATE(status_date) >= %s"
        params.append(from_date)

    if to_date:
        sql += " AND DATE(status_date) <= %s"
        params.append(to_date)

    if print_status == "PRINTED":
        sql += " AND UPPER(COALESCE(print_status, 'PENDING')) = 'PRINTED'"
    elif print_status == "PENDING":
        sql += """
            AND (
                print_status IS NULL
                OR TRIM(print_status) = ''
                OR UPPER(TRIM(print_status)) IN ('PENDING', 'YET TO PRINT')
            )
        """

    return sql, params


def _fetch_distinct_invoice_template_flags(conn, invoice_numbers):
    invoice_numbers = [
        clean_keep_empty(x)
        for x in (invoice_numbers or [])
        if clean_keep_empty(x)
    ]

    if not invoice_numbers:
        return {}

    placeholders = ", ".join(["%s"] * len(invoice_numbers))
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            f"""
            SELECT
                invoice_number,
                MIN(hospital_name) AS hospital_name,
                MAX(CASE WHEN UPPER(COALESCE(print_status, 'PENDING')) = 'PRINTED' THEN 1 ELSE 0 END) AS is_printed,
                MAX(COALESCE(custom_narration, '')) AS custom_narration
            FROM invoice_master
            WHERE invoice_number IN ({placeholders})
            GROUP BY invoice_number
            """,
            tuple(invoice_numbers),
        )
        rows = cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass

    out = {}
    for row in rows:
        invoice_no = clean_keep_empty(row.get("invoice_number"))
        hospital_name = clean_keep_empty(row.get("hospital_name"))
        resolved_template = resolve_invoice_template_for_runtime(hospital_name=hospital_name) or {}
        out[invoice_no] = {
            "hospital_name": hospital_name,
            "narration_enabled": is_template_narration_enabled(resolved_template),
            "custom_narration": clean_keep_empty(row.get("custom_narration")),
            "is_printed": bool(row.get("is_printed")),
        }

    return out


def fetch_invoice_list(
    conn,
    invoice_number=None,
    hospital_name=None,
    from_date=None,
    to_date=None,
    print_status=None
):
    where_sql, params = build_invoice_search_where_clause(
        invoice_number=invoice_number,
        hospital_name=hospital_name,
        from_date=from_date,
        to_date=to_date,
        print_status=print_status,
    )

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            f"""
            SELECT
                invoice_number,
                DATE(status_date) AS invoice_date,
                hospital_name,
                inv_amount,
                totalgst_amount,
                totaltds_amount,
                totalinv_amount,
                print_status,
                prntentered_at,
                prntentered_by,
                ld_internalno,
                custom_narration
            FROM invoice_master
            {where_sql}
            ORDER BY DATE(status_date) DESC, invoice_number DESC, ld_internalno ASC
            """,
            tuple(params),
        )
        rows = cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass

    invoice_numbers = []
    for row in rows:
        invoice_no = clean_keep_empty(row.get("invoice_number"))
        if invoice_no and invoice_no not in invoice_numbers:
            invoice_numbers.append(invoice_no)

    template_flags = _fetch_distinct_invoice_template_flags(conn, invoice_numbers)

    items = []
    for row in rows:
        status_norm = normalize_print_status(row.get("print_status"))
        invoice_no = clean_keep_empty(row.get("invoice_number"))
        flag_data = template_flags.get(invoice_no, {})

        items.append({
            "invoice_number": invoice_no,
            "invoice_date": format_invoice_date(row.get("invoice_date")),
            "hospital_name": clean_keep_empty(row.get("hospital_name")),
            "netinv_amount": to_float(row.get("inv_amount")),
            "netgst_amount": to_float(row.get("totalgst_amount")),
            "nettds_amount": to_float(row.get("totaltds_amount")),
            "totalinv_amount": to_float(row.get("totalinv_amount")),
            "print_status": status_norm,
            "printed_at": format_printed_datetime(row.get("prntentered_at")) or "-",
            "printed_by": clean_keep_empty(row.get("prntentered_by")) or "-",
            "narration_enabled": bool(flag_data.get("narration_enabled")),
            "custom_narration": clean_keep_empty(flag_data.get("custom_narration") or row.get("custom_narration")),
            "narration_locked": status_norm == "PRINTED" or bool(flag_data.get("is_printed")),
        })

    unique_invoice_numbers = {
        clean_keep_empty(x.get("invoice_number"))
        for x in items
        if clean_keep_empty(x.get("invoice_number"))
    }

    printed_invoice_numbers = {
        clean_keep_empty(x.get("invoice_number"))
        for x in items
        if x.get("print_status") == "PRINTED"
    }

    summary = {
        "total_invoices": len(unique_invoice_numbers),
        "printed_invoices": len(printed_invoice_numbers),
        "pending_invoices": len(unique_invoice_numbers - printed_invoice_numbers),
    }

    return {
        "items": items,
        "summary": summary,
    }


def fetch_hospital_print_summary(
    conn,
    invoice_number=None,
    hospital_name=None,
    from_date=None,
    to_date=None,
    print_status=None
):
    where_sql, params = build_invoice_search_where_clause(
        invoice_number=invoice_number,
        hospital_name=hospital_name,
        from_date=from_date,
        to_date=to_date,
        print_status=print_status,
    )

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            f"""
            SELECT
                hospital_name,
                SUM(
                    CASE
                        WHEN UPPER(COALESCE(print_status, 'PENDING')) = 'PRINTED' THEN 1
                        ELSE 0
                    END
                ) AS printed_count,
                SUM(
                    CASE
                        WHEN print_status IS NULL
                             OR TRIM(print_status) = ''
                             OR UPPER(TRIM(print_status)) IN ('PENDING', 'YET TO PRINT')
                        THEN 1
                        ELSE 0
                    END
                ) AS pending_count,
                COUNT(*) AS total_count
            FROM invoice_master
            {where_sql}
            GROUP BY hospital_name
            ORDER BY hospital_name ASC
            """,
            tuple(params),
        )
        rows = cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass

    out = []
    for row in rows:
        out.append({
            "hospital_name": clean_keep_empty(row.get("hospital_name")),
            "printed_count": int(row.get("printed_count") or 0),
            "pending_count": int(row.get("pending_count") or 0),
            "total_count": int(row.get("total_count") or 0),
        })
    return out


def fetch_invoice_template_narration_status(conn, invoice_number):
    invoice_number = clean(invoice_number)

    if not invoice_number:
        raise ValueError("Invoice Number is required.")

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT
                invoice_number,
                MIN(hospital_name) AS hospital_name,
                MAX(CASE WHEN UPPER(COALESCE(print_status, 'PENDING')) = 'PRINTED' THEN 1 ELSE 0 END) AS is_printed,
                MAX(COALESCE(custom_narration, '')) AS custom_narration
            FROM invoice_master
            WHERE invoice_number = %s
            GROUP BY invoice_number
            """,
            (invoice_number,),
        )
        row = cur.fetchone() or {}
    finally:
        try:
            cur.close()
        except Exception:
            pass

    if not row:
        raise ValueError("Invoice not found.")

    hospital_name = clean_keep_empty(row.get("hospital_name"))
    resolved_template = resolve_invoice_template_for_runtime(hospital_name=hospital_name) or {}
    narration_enabled = is_template_narration_enabled(resolved_template)

    return {
        "invoice_number": invoice_number,
        "hospital_name": hospital_name,
        "narration_enabled": narration_enabled,
        "custom_narration": clean_keep_empty(row.get("custom_narration")),
        "narration_locked": bool(row.get("is_printed")),
        "template_code": clean_keep_empty(resolved_template.get("template_code")),
        "template_name": clean_keep_empty(resolved_template.get("template_name")),
    }


def save_invoice_custom_narration(conn, invoice_number, custom_narration, updated_by=None):
    invoice_number = clean(invoice_number)
    custom_narration = clean_keep_empty(custom_narration)
    updated_by = clean(updated_by) or "SYSTEM"

    if not invoice_number:
        raise ValueError("Invoice Number is required.")

    status = fetch_invoice_template_narration_status(conn, invoice_number)

    if status.get("narration_locked"):
        raise ValueError("Narration cannot be amended because this invoice is already printed.")

    if status.get("narration_enabled") and not custom_narration:
        raise ValueError("Narration is mandatory for this invoice template. Please enter narration before printing.")

    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE invoice_master
            SET custom_narration = %s
            WHERE invoice_number = %s
              AND (
                    print_status IS NULL
                    OR TRIM(print_status) = ''
                    OR UPPER(TRIM(print_status)) IN ('PENDING', 'YET TO PRINT')
                  )
            """,
            (custom_narration, invoice_number),
        )
        updated_count = cur.rowcount or 0
    finally:
        try:
            cur.close()
        except Exception:
            pass

    if updated_count <= 0:
        raise ValueError("Narration could not be saved. Invoice may already be printed.")

    return {
        "invoice_number": invoice_number,
        "custom_narration": custom_narration,
        "updated_by": updated_by,
        "updated_count": updated_count,
    }


def validate_invoice_narration_before_print(conn, invoice_number):
    status = fetch_invoice_template_narration_status(conn, invoice_number)

    if status.get("narration_enabled") and not clean_keep_empty(status.get("custom_narration")):
        raise ValueError("Narration is mandatory for this invoice template. Please enter narration before printing.")

    return status


def mark_invoice_printed(conn, invoice_number, printed_by):
    invoice_number = clean(invoice_number)
    printed_by = clean(printed_by) or "SYSTEM"

    if not invoice_number:
        raise ValueError("Invoice Number is required.")

    validate_invoice_narration_before_print(conn, invoice_number)

    now_dt = datetime.datetime.now()

    cur = conn.cursor()
    try:
        cur.execute(
            """
            UPDATE invoice_master
            SET
                print_status = 'PRINTED',
                prntentered_at = %s,
                prntentered_by = %s
            WHERE invoice_number = %s
            """,
            (now_dt, printed_by, invoice_number),
        )
        updated_count = cur.rowcount or 0
    finally:
        try:
            cur.close()
        except Exception:
            pass

    if updated_count <= 0:
        raise ValueError("No invoice found for update.")

    return {
        "invoice_number": invoice_number,
        "printed_at": now_dt.strftime("%Y-%m-%d %H:%M:%S"),
        "printed_by": printed_by,
        "updated_count": updated_count,
    }


def _update_invoice_print_audit(invoice_number, printed_by):
    conn = None
    try:
        conn = get_db()
        result = mark_invoice_printed(conn, invoice_number, printed_by)
        conn.commit()
        return result
    except Exception:
        try:
            if conn:
                conn.rollback()
        except Exception:
            pass
        raise
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass


def _is_meaningful_invoice_value(value):
    """
    Treat blank, NULL-like text, and '-' as empty for invoice runtime data.
    This is required because DEFAULT scope templates depend on invoice runtime
    hospital values, but invoice_master may store '-' for address/GST/PAN.
    """
    text = clean_keep_empty(value)
    if not text:
        return False
    return text.strip().upper() not in ("-", "--", "NULL", "NONE", "N/A", "NA")


def _first_meaningful(*values, default=""):
    for value in values:
        if _is_meaningful_invoice_value(value):
            return clean_keep_empty(value)
    return default


def _partner_value(row, *keys):
    row = row or {}
    for key in keys:
        if key in row and _is_meaningful_invoice_value(row.get(key)):
            return clean_keep_empty(row.get(key))
    return ""


def _fetch_partner_reg_columns(conn):
    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute("SHOW COLUMNS FROM partner_reg")
        rows = cur.fetchall() or []
        return {clean_keep_empty(row.get("Field")) for row in rows if clean_keep_empty(row.get("Field"))}
    except Exception:
        return set()
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass


def _fetch_hospital_partner_details(conn, hospital_name):
    """
    Fetch hospital master details from partner_reg in a defensive way.
    Supports exact hospital name as well as invoice names like:
        "FMRI-GRG - FORTIS HOSPITALS LIMITED"
    where partner_reg.short_name may be only:
        "FMRI-GRG"
    """
    hospital_name = clean_keep_empty(hospital_name)
    if not hospital_name:
        return {}

    available_columns = _fetch_partner_reg_columns(conn)
    if not available_columns:
        return {}

    candidate_columns = [
        "short_name", "legal_name", "partner_name", "hospital_name", "partner_type", "partner_status",
        "partner_address", "address", "partner_addr", "addr1", "address1", "billing_address",
        "partner_city", "city", "partner_state", "state", "partner_country", "country",
        "partner_pincode", "pincode", "pin_code", "postal_code",
        "partner_gst", "partner_gstin", "gst_no", "gstin", "gst_number", "partner_gst_no",
        "partner_pan", "pan_no", "pan", "partner_pan_no",
        "partner_email", "email", "partner_emailid", "email_id",
    ]
    select_columns = [col for col in candidate_columns if col in available_columns]
    if not select_columns:
        return {}

    match_columns = [col for col in ("short_name", "partner_name", "hospital_name") if col in available_columns]
    if not match_columns:
        return {}

    # Split only on the first separator so names like
    # "FMRI-GRG - FORTIS HOSPITALS LIMITED" can still match "FMRI-GRG".
    hospital_short = hospital_name
    if " - " in hospital_name:
        hospital_short = hospital_name.split(" - ", 1)[0].strip()

    where_parts = []
    params = []

    for col in match_columns:
        where_parts.append(f"UPPER(TRIM(COALESCE({col}, ''))) = UPPER(TRIM(%s))")
        params.append(hospital_name)

        where_parts.append(f"UPPER(TRIM(COALESCE({col}, ''))) = UPPER(TRIM(%s))")
        params.append(hospital_short)

        where_parts.append(f"UPPER(TRIM(%s)) LIKE CONCAT('%', UPPER(TRIM(COALESCE({col}, ''))), '%')")
        params.append(hospital_name)

        where_parts.append(f"UPPER(TRIM(COALESCE({col}, ''))) LIKE CONCAT('%', UPPER(TRIM(%s)), '%')")
        params.append(hospital_short)

    partner_type_filter = ""
    if "partner_type" in available_columns:
        partner_type_filter = " AND UPPER(TRIM(COALESCE(partner_type, ''))) IN ('HOSPITAL', 'HOSPITALS') "

    status_order = ""
    if "partner_status" in available_columns:
        status_order = "CASE WHEN UPPER(TRIM(COALESCE(partner_status, ''))) = 'ACTIVE' THEN 0 ELSE 1 END,"

    short_name_order = "1"
    order_params = []
    if "short_name" in available_columns:
        short_name_order = """
            CASE
                WHEN UPPER(TRIM(COALESCE(short_name, ''))) = UPPER(TRIM(%s)) THEN 0
                WHEN UPPER(TRIM(COALESCE(short_name, ''))) = UPPER(TRIM(%s)) THEN 1
                ELSE 2
            END
        """
        order_params = [hospital_name, hospital_short]

    sql = f"""
        SELECT {', '.join(select_columns)}
        FROM partner_reg
        WHERE ({' OR '.join(where_parts)})
        {partner_type_filter}
        ORDER BY {status_order} {short_name_order}
        LIMIT 1
    """

    cur = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, tuple(params + order_params))
        return cur.fetchone() or {}
    except Exception:
        return {}
    finally:
        try:
            if cur:
                cur.close()
        except Exception:
            pass

def _normalize_hospital_master_details(row):
    row = row or {}
    return {
        "name": _partner_value(row, "short_name", "partner_name", "hospital_name"),
        "legal_name": _partner_value(row, "legal_name"),
        "address": _partner_value(row, "partner_address", "address", "partner_addr", "addr1", "address1", "billing_address"),
        "city": _partner_value(row, "partner_city", "city"),
        "state": _partner_value(row, "partner_state", "state"),
        "country": _partner_value(row, "partner_country", "country"),
        "pincode": _partner_value(row, "partner_pincode", "pincode", "pin_code", "postal_code"),
        "gst_no": _partner_value(row, "partner_gst", "partner_gstin", "gst_no", "gstin", "gst_number", "partner_gst_no"),
        "pan_no": _partner_value(row, "partner_pan", "pan_no", "pan", "partner_pan_no"),
        "email": _partner_value(row, "partner_email", "email", "partner_emailid", "email_id"),
    }



def _get_template_item_field_key(resolved_template, label_name=None, display_order=None):
    """
    Read an item column field_key from the saved invoice template.
    This keeps the printing logic controlled by Invoice Template Builder.
    """
    item_columns = (resolved_template or {}).get("item_columns") or []
    label_name_norm = clean_keep_empty(label_name).strip().upper() if label_name else ""

    for col in item_columns:
        col_label = clean_keep_empty((col or {}).get("column_label")).strip().upper()
        col_order = clean_keep_empty((col or {}).get("display_order")).strip()

        if label_name_norm and col_label == label_name_norm:
            return clean_keep_empty((col or {}).get("field_key"))

        if display_order is not None and col_order == str(display_order):
            return clean_keep_empty((col or {}).get("field_key"))

    return ""


def _is_dynamic_template_expression(value):
    text = clean_keep_empty(value).strip()
    if not text:
        return False
    return "{{" in text and "}}" in text


def _resolve_template_description_value(resolved_template, fallback_description):
    """
    If the Description column in Invoice Template Builder is fixed text
    like 'Healthcare Referral', print that fixed text.

    If the Description column is a dynamic placeholder like {{item.description}}
    or {{patient_name}}, keep the existing runtime description behaviour.
    """
    template_description = _get_template_item_field_key(
        resolved_template,
        label_name="Description",
        display_order=2,
    )

    if template_description and not _is_dynamic_template_expression(template_description):
        return template_description

    return fallback_description

def _build_runtime_items_from_rows(rows, combine_selected_records_into_single_row=False, resolved_template=None):
    items = []

    if combine_selected_records_into_single_row and rows:
        patient_names = []
        patient_passports = []
        lead_numbers = []
        workflow_statuses = []

        for row in rows:
            patient_name = clean_keep_empty(row.get("patient_name"))
            patient_passport = clean_keep_empty(row.get("patient_passport"))
            ld_internalno = clean_keep_empty(row.get("ld_internalno"))
            wrkflw_status = clean_keep_empty(row.get("wrkflw_status"))

            if patient_name and patient_name not in patient_names:
                patient_names.append(patient_name)
            if patient_passport and patient_passport not in patient_passports:
                patient_passports.append(patient_passport)
            if ld_internalno and ld_internalno not in lead_numbers:
                lead_numbers.append(ld_internalno)
            if wrkflw_status and wrkflw_status not in workflow_statuses:
                workflow_statuses.append(wrkflw_status)

        description_parts = []
        if patient_names:
            description_parts.append(", ".join(patient_names))
        if patient_passports:
            description_parts.append("Passport: " + ", ".join(patient_passports))
        if lead_numbers:
            description_parts.append("Lead No: " + ", ".join(lead_numbers))

        description = " | ".join(description_parts) if description_parts else "Healthcare / Service Charges"
        description = _resolve_template_description_value(resolved_template, description)
        total_inv_amount = sum(to_float(row.get("inv_amount")) for row in rows)
        total_gst_amount = sum(to_float(row.get("totalgst_amount")) for row in rows)
        total_tds_amount = sum(to_float(row.get("totaltds_amount")) for row in rows)
        grand_total_amount = sum(to_float(row.get("totalinv_amount")) for row in rows)
        first_row = rows[0] or {}

        items.append({
            "sl_no": 1,
            "loop_index": 1,
            "loop_index0": 0,
            "loop_revindex": 1,
            "loop": {"index": 1, "index0": 0, "revindex": 1},
            "description": description,
            "qty": 1,
            "rate": total_inv_amount,
            "amount": total_inv_amount,
            "inv_amount": total_inv_amount,
            "gst_amount": total_gst_amount,
            "tds_amount": total_tds_amount,
            "total_amount": grand_total_amount,
            "sac_code": clean_keep_empty(first_row.get("sac_code")) or "999723",
            "remarks": ", ".join(workflow_statuses),
            "ld_internalno": ", ".join(lead_numbers),
            "patient_name": ", ".join(patient_names),
            "patient_passport": ", ".join(patient_passports),
            "gst_percentage": to_float(first_row.get("gst_percentage"), 0),
            "tds_percentage": to_float(first_row.get("tds_percentage"), 0),
            "selected_records": len(rows),
            "is_combined_row": True,
        })

    else:
        total_rows = len(rows or [])
        for idx, row in enumerate(rows, start=1):
            description_parts = []

            ld_internalno = clean_keep_empty(row.get("ld_internalno"))
            patient_name = clean_keep_empty(row.get("patient_name"))
            patient_passport = clean_keep_empty(row.get("patient_passport"))

            if patient_name:
                description_parts.append(patient_name)
            if patient_passport:
                description_parts.append(f"Passport: {patient_passport}")
            if ld_internalno:
                description_parts.append(f"Lead No: {ld_internalno}")

            description = " | ".join(description_parts) if description_parts else "Healthcare / Service Charges"
            description = _resolve_template_description_value(resolved_template, description)

            inv_amount = to_float(row.get("inv_amount"))
            gst_amount = to_float(row.get("totalgst_amount"))
            tds_amount = to_float(row.get("totaltds_amount"))
            total_amount = to_float(row.get("totalinv_amount"))
            sac_code = clean_keep_empty(row.get("sac_code")) or "999723"

            items.append({
                "sl_no": idx,
                "loop_index": idx,
                "loop_index0": idx - 1,
                "loop_revindex": total_rows - idx + 1,
                "loop": {
                    "index": idx,
                    "index0": idx - 1,
                    "revindex": total_rows - idx + 1,
                },
                "description": description,
                "qty": 1,
                "rate": inv_amount,
                "amount": inv_amount,
                "inv_amount": inv_amount,
                "gst_amount": gst_amount,
                "tds_amount": tds_amount,
                "total_amount": total_amount,
                "sac_code": sac_code,
                "remarks": clean_keep_empty(row.get("wrkflw_status")) or "",
                "ld_internalno": ld_internalno,
                "patient_name": patient_name,
                "patient_passport": patient_passport,
                "gst_percentage": to_float(row.get("gst_percentage"), 0),
                "tds_percentage": to_float(row.get("tds_percentage"), 0),
                "selected_records": int(row.get("selected_records") or 1),
                "is_combined_row": False,
            })

    if not items:
        items.append({
            "sl_no": 1,
            "loop_index": 1,
            "loop_index0": 0,
            "loop_revindex": 1,
            "loop": {"index": 1, "index0": 0, "revindex": 1},
            "description": _resolve_template_description_value(resolved_template, "Healthcare / Service Charges"),
            "qty": 1,
            "rate": 0.00,
            "amount": 0.00,
            "inv_amount": 0.00,
            "gst_amount": 0.00,
            "tds_amount": 0.00,
            "total_amount": 0.00,
            "sac_code": "999723",
            "remarks": "",
            "ld_internalno": "",
            "patient_name": "",
            "patient_passport": "",
            "gst_percentage": 0.0,
            "tds_percentage": 0.0,
            "selected_records": 0,
            "is_combined_row": False,
        })

    return items


def _build_runtime_placeholder_map(invoice_data, bill_to, company, items, resolved_template):
    total_invoice_amount = to_float(invoice_data.get("totalinv_amount"))
    net_amount = to_float(invoice_data.get("netinv_amount"))
    total_gst_amount = to_float(invoice_data.get("netgst_amount"))

    # IMPORTANT:
    # TDS is a deduction, but the minus sign must come ONLY from the saved
    # invoice template design. Example:
    #   Saved template value "-{{totaltds_amount}}" -> prints "-500.00"
    #   Saved template value "{{totaltds_amount}}"  -> prints "500.00"
    # Therefore the runtime placeholder value must remain unsigned/positive.
    total_tds_amount = abs(to_float(invoice_data.get("nettds_amount")))

    first_item = items[0] if items else {}
    patient_name = clean_keep_empty(first_item.get("patient_name"))
    patient_passport = clean_keep_empty(first_item.get("patient_passport"))
    ld_internalno = clean_keep_empty(first_item.get("ld_internalno"))

    selected_records = int(invoice_data.get("selected_records") or 0)
    custom_narration = clean_keep_empty(invoice_data.get("custom_narration"))

    placeholders = {
        "invoice_number": invoice_data.get("invoice_number", ""),
        "status_date": invoice_data.get("invoice_date", ""),
        "invoice_date": invoice_data.get("invoice_date", ""),
        "due_date": invoice_data.get("due_date", ""),
        "placeof_supply": invoice_data.get("place_of_supply", ""),
        "currency": invoice_data.get("currency", "INR"),
        "print_status": invoice_data.get("print_status", ""),
        "printed_on": invoice_data.get("printed_on", ""),
        "printed_by": invoice_data.get("printed_by", ""),
        "sac_code": invoice_data.get("sac_code", ""),
        "selected_records": selected_records,
        "custom_narration": custom_narration,
        "cutom_narration": custom_narration,

        "hospital_name": bill_to.get("short_name", ""),
        "hospital_legal_name": bill_to.get("legal_name", ""),
        "partner_legal_name": bill_to.get("legal_name", ""),
        "bill_to_name": bill_to.get("legal_name", "") or bill_to.get("name", ""),
        "hospital_address": bill_to.get("address", ""),
        "hospital_city": bill_to.get("city", ""),
        "hospital_state": bill_to.get("state", ""),
        "hospital_country": bill_to.get("country", ""),
        "hospital_pincode": bill_to.get("pincode", ""),
        "hospital_gst": bill_to.get("gst_no", ""),
        "hospital_gst_no": bill_to.get("gst_no", ""),
        "hospital_gstin": bill_to.get("gst_no", ""),
        "hospital_pan": bill_to.get("pan_no", ""),
        "hospital_pan_no": bill_to.get("pan_no", ""),
        "partner_email": bill_to.get("email", ""),

        "company_name": company.get("company_name", ""),
        "company_address": company.get("address", ""),
        "company_gstin": company.get("gstin", ""),
        "company_email": company.get("email", ""),
        "company_phone": company.get("phone", ""),

        "patient_name": patient_name,
        "patient_passport": patient_passport,
        "ld_internalno": ld_internalno,

        "inv_amount": net_amount,
        "sumof_invamnt": net_amount,
        "totalgst_amount": total_gst_amount,
        "totaltds_amount": total_tds_amount,
        "totalinv_amount": total_invoice_amount,

        "inv_amount_display": format_money(net_amount),
        "sumof_invamnt_display": format_money(net_amount),
        "totalgst_amount_display": format_money(total_gst_amount),
        "totaltds_amount_display": format_money(total_tds_amount),
        "totalinv_amount_display": format_money(total_invoice_amount),

        "gst_percentage": first_item.get("gst_percentage", 0),
        "tds_percentage": first_item.get("tds_percentage", 0),
        "notes_text": resolved_template.get("notes_text") or invoice_data.get("notes", ""),

        "bill_to_name": bill_to.get("name", ""),
        "bill_to_address": bill_to.get("address", ""),
        "bill_to_city": bill_to.get("city", ""),
        "bill_to_state": bill_to.get("state", ""),
        "bill_to_country": bill_to.get("country", ""),
        "bill_to_pincode": bill_to.get("pincode", ""),
        "bill_to_gst": bill_to.get("gst_no", ""),
        "bill_to_gst_no": bill_to.get("gst_no", ""),
        "bill_to_gstin": bill_to.get("gst_no", ""),
        "bill_to_pan": bill_to.get("pan_no", ""),
        "bill_to_pan_no": bill_to.get("pan_no", ""),
        "bill_to_email": bill_to.get("email", ""),

        "combine_selected_records_into_single_row": bool(invoice_data.get("combine_selected_records_into_single_row")),
        "selected_records_label": f"{selected_records} Record(s)" if selected_records else "",

        "sl_no": first_item.get("sl_no", 1),
        "loop_index": first_item.get("loop_index", 1),
        "loop_index0": first_item.get("loop_index0", 0),
        "loop_revindex": first_item.get("loop_revindex", 1),
        "loop.index": first_item.get("loop_index", 1),
    }

    return placeholders


def _enrich_runtime_items_with_common_placeholders(items, runtime_placeholders):
    """
    Invoice template row cells are resolved mainly against each item row.
    Therefore header/common placeholders used inside an item formula, such as
    {{custom_narration}}, must also be available on every runtime item.
    """
    if not items:
        return items

    common_keys = (
        "invoice_number",
        "invoice_date",
        "status_date",
        "due_date",
        "currency",
        "placeof_supply",
        "custom_narration",
        "cutom_narration",
        "hospital_name",
        "hospital_legal_name",
        "partner_legal_name",
        "bill_to_name",
        "selected_records",
        "selected_records_label",
        "totalinv_amount",
        "totalgst_amount",
        "totaltds_amount",
        "inv_amount",
        "totalinv_amount_display",
        "totalgst_amount_display",
        "totaltds_amount_display",
        "inv_amount_display",
    )

    enriched_items = []
    for item in items:
        enriched = dict(item or {})
        for key in common_keys:
            if key in runtime_placeholders and (key not in enriched or clean_keep_empty(enriched.get(key)) == ""):
                enriched[key] = runtime_placeholders.get(key)
        enriched_items.append(enriched)

    return enriched_items


def _build_print_template_data(resolved_template, invoice_data, runtime_placeholders):
    template_data = dict(resolved_template or {})

    template_data["company_name"] = invoice_data.get("company_name") or template_data.get("company_name", "")
    template_data["company_address"] = invoice_data.get("company_address") or template_data.get("company_address", "")
    template_data["company_gstin"] = invoice_data.get("company_gstin") or template_data.get("company_gstin", "")
    template_data["company_email"] = invoice_data.get("company_email") or template_data.get("company_email", "")
    template_data["company_phone"] = invoice_data.get("company_phone") or template_data.get("company_phone", "")
    template_data["notes_text"] = runtime_placeholders.get("notes_text", template_data.get("notes_text", ""))

    return _normalize_loop_index_references(template_data)


def _resolve_company_logo_url(resolved_template):
    logo_path = clean_keep_empty((resolved_template or {}).get("logo_path"))

    if not logo_path:
        logo_path = "images/Cadupius_Logo_V1.1.jpg"

    logo_path = logo_path.replace("\\", "/").lstrip("/")

    if logo_path.startswith("static/"):
        logo_path = logo_path[len("static/"):]

    return url_for("static", filename=logo_path, _external=True)


def _build_invoice_print_context(invoice_number):
    conn = None
    cur = None

    try:
        conn = get_db()
        cur = conn.cursor(dictionary=True)

        cur.execute(
            """
            SELECT
                invoice_number,
                DATE(status_date) AS invoice_date,
                hospital_name,
                hospital_legal_name,
                hospital_address,
                hospital_gst,
                hospital_pan,
                hospital_country,
                hospital_state,
                placeof_supply,
                hospital_city,
                hospital_pincode,
                ld_internalno,
                patient_passport,
                patient_name,
                inv_currency,
                sac_code,
                wrkflw_status,
                print_status,
                prntentered_at,
                prntentered_by,
                inv_amount,
                totalgst_amount,
                totaltds_amount,
                totalinv_amount,
                netinv_amount,
                netgst_amount,
                nettds_amount,
                gst_percentage,
                tds_percentage,
                selected_records,
                custom_narration
            FROM invoice_master
            WHERE invoice_number = %s
            ORDER BY ld_internalno
            """,
            (invoice_number,),
        )

        rows = cur.fetchall() or []

        if not rows:
            return None

        header = rows[0]

        snapshot_net_invoice_amount = sum(to_float(r.get("inv_amount")) for r in rows)
        snapshot_gst_amount = sum(to_float(r.get("totalgst_amount")) for r in rows)
        snapshot_tds_amount = sum(to_float(r.get("totaltds_amount")) for r in rows)
        snapshot_total_invoice_amount = sum(to_float(r.get("totalinv_amount")) for r in rows)

        formatted_invoice_date = format_invoice_display_date(header.get("invoice_date")) or "-"

        resolved_template = resolve_invoice_template_for_runtime(
            hospital_name=clean_keep_empty(header.get("hospital_name"))
        )
        resolved_template = _normalize_loop_index_references(resolved_template or {})

        combine_selected_records_into_single_row = bool(
            (resolved_template or {}).get("combine_selected_records_into_single_row")
        )

        # DEFAULT scope templates are shared across hospitals. In that flow,
        # hospital address/GST/PAN may not be stored on invoice_master, so fetch
        # the hospital master from partner_reg and use it as a fallback.
        hospital_master = _normalize_hospital_master_details(
            _fetch_hospital_partner_details(conn, header.get("hospital_name"))
        )

        bill_to = {
            "short_name": _first_meaningful(
                header.get("hospital_name"),
                hospital_master.get("name"),
                default="-"
            ),
            "legal_name": _first_meaningful(
                header.get("hospital_legal_name"),
                hospital_master.get("legal_name"),
                default=""
            ),
            "name": _first_meaningful(
                header.get("hospital_legal_name"),
                hospital_master.get("legal_name"),
                header.get("hospital_name"),
                hospital_master.get("name"),
                default="-"
            ),
            "address": _first_meaningful(header.get("hospital_address"), hospital_master.get("address"), default="-"),
            "city": _first_meaningful(header.get("hospital_city"), hospital_master.get("city"), default="-"),
            "state": _first_meaningful(header.get("hospital_state"), hospital_master.get("state"), default="-"),
            "country": _first_meaningful(header.get("hospital_country"), hospital_master.get("country"), default="India"),
            "pincode": _first_meaningful(header.get("hospital_pincode"), hospital_master.get("pincode"), default="-"),
            "gst_no": _first_meaningful(header.get("hospital_gst"), hospital_master.get("gst_no"), default="-"),
            "pan_no": _first_meaningful(header.get("hospital_pan"), hospital_master.get("pan_no"), default="-"),
            "email": _first_meaningful(hospital_master.get("email"), default=""),
        }

        company = {
            "company_name": resolved_template.get("company_name") or "TERIQEN",
            "address": resolved_template.get("company_address") or "",
            "gstin": resolved_template.get("company_gstin") or "",
            "email": resolved_template.get("company_email") or "",
            "phone": resolved_template.get("company_phone") or "",
        }

        custom_narration = clean_keep_empty(header.get("custom_narration"))

        invoice_data = {
            "invoice_number": clean_keep_empty(header.get("invoice_number")),
            "invoice_date": formatted_invoice_date,
            "due_date": formatted_invoice_date,
            "currency": clean_keep_empty(header.get("inv_currency")) or "INR",
            "place_of_supply": _first_meaningful(header.get("placeof_supply"), bill_to.get("state"), default="-"),
            "hospital_name": bill_to.get("short_name") or "-",
            "hospital_legal_name": bill_to.get("legal_name") or "-",
            "partner_legal_name": bill_to.get("legal_name") or "-",
            "hospital_address": bill_to.get("address") or "-",
            "hospital_city": bill_to.get("city") or "-",
            "hospital_state": bill_to.get("state") or "-",
            "hospital_country": bill_to.get("country") or "India",
            "hospital_pincode": bill_to.get("pincode") or "-",
            "gst_no": bill_to.get("gst_no") or "-",
            "hospital_gst": bill_to.get("gst_no") or "-",
            "hospital_gst_no": bill_to.get("gst_no") or "-",
            "hospital_gstin": bill_to.get("gst_no") or "-",
            "hospital_pan": bill_to.get("pan_no") or "-",
            "hospital_pan_no": bill_to.get("pan_no") or "-",
            "printed_on": format_printed_datetime_display(header.get("prntentered_at")) or "-",
            "printed_by": clean_keep_empty(header.get("prntentered_by")) or "-",
            "sac_code": clean_keep_empty(header.get("sac_code")) or "999723",
            "print_status": clean_keep_empty(header.get("print_status")) or "Yet to Print",
            "totalinv_amount": snapshot_total_invoice_amount,
            "netinv_amount": snapshot_net_invoice_amount,
            "netgst_amount": snapshot_gst_amount,
            "nettds_amount": snapshot_tds_amount,
            "selected_records": int(header.get("selected_records") or len(rows) or 0),
            "notes": resolved_template.get("notes_text") or "Thank you for your business.",
            "company_name": company.get("company_name", ""),
            "company_address": company.get("address", ""),
            "company_gstin": company.get("gstin", ""),
            "company_email": company.get("email", ""),
            "company_phone": company.get("phone", ""),
            "combine_selected_records_into_single_row": combine_selected_records_into_single_row,
            "custom_narration": custom_narration,
            "cutom_narration": custom_narration,
        }

        items = _build_runtime_items_from_rows(
            rows,
            combine_selected_records_into_single_row=combine_selected_records_into_single_row,
            resolved_template=resolved_template,
        )

        runtime_placeholders = _build_runtime_placeholder_map(
            invoice_data=invoice_data,
            bill_to=bill_to,
            company=company,
            items=items,
            resolved_template=resolved_template,
        )

        items = _enrich_runtime_items_with_common_placeholders(
            items=items,
            runtime_placeholders=runtime_placeholders,
        )

        template_data = _build_print_template_data(
            resolved_template=resolved_template,
            invoice_data=invoice_data,
            runtime_placeholders=runtime_placeholders,
        )

        return {
            "template_data": template_data,
            "invoice_data": invoice_data,
            "items": items,
            "bill_to": bill_to,
            "company": company,
            "resolved_template": resolved_template,
            "runtime_placeholders": runtime_placeholders,
        }

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


def register_invoice_printing_routes(app):
    @app.route("/invoiceprinting", methods=["GET"])
    @login_required
    def invoiceprinting():
        return render_template("invoiceprinting.html")

    @app.route("/invoice-print/<string:invoice_number>", methods=["GET"])
    @app.route("/invoice/print/<string:invoice_number>", methods=["GET"])
    @login_required
    def invoice_print(invoice_number):
        try:
            printed_by = getattr(current_user, "user_code", None) or "SYSTEM"
            _update_invoice_print_audit(invoice_number, printed_by)

            context = _build_invoice_print_context(invoice_number)

            if not context:
                flash("Invoice not found.", "error")
                return redirect(url_for("invoiceprinting"))

            company_logo_url = _resolve_company_logo_url(context["resolved_template"])

            return render_template(
                "invoice_template_builder.html",
                page_mode="print",
                template_data=context["template_data"],
                template_list=[],
                template_grid_rows=[],
                field_library={},
                success_message="",
                error_message="",
                invoice=context["invoice_data"],
                items=context["items"],
                bill_to=context["bill_to"],
                company=context["company"],
                company_logo_url=company_logo_url,
                invoice_template_config=context["resolved_template"],
                runtime_placeholders=context["runtime_placeholders"],
                invoice_runtime_data={
                    "invoice": context["invoice_data"],
                    "items": context["items"],
                    "bill_to": context["bill_to"],
                    "company": context["company"],
                    "placeholders": context["runtime_placeholders"],
                },
                print_mode=True,
                hide_builder_sections=True,
            )

        except Exception as e:
            flash(str(e), "error")
            return redirect(url_for("invoiceprinting"))

    @app.route("/invoice-print-pdf/<string:invoice_number>", methods=["GET"])
    @app.route("/invoice/print-pdf/<string:invoice_number>", methods=["GET"])
    @login_required
    def invoice_print_pdf(invoice_number):
        if HTML is None:
            flash("PDF engine is not installed. Please install weasyprint.", "error")
            return redirect(url_for("invoice_print", invoice_number=invoice_number))

        try:
            printed_by = getattr(current_user, "user_code", None) or "SYSTEM"
            _update_invoice_print_audit(invoice_number, printed_by)

            context = _build_invoice_print_context(invoice_number)

            if not context:
                flash("Invoice not found.", "error")
                return redirect(url_for("invoiceprinting"))

            company_logo_url = _resolve_company_logo_url(context["resolved_template"])

            html = render_template(
                "invoice_template_builder.html",
                page_mode="print",
                template_data=context["template_data"],
                template_list=[],
                template_grid_rows=[],
                field_library={},
                success_message="",
                error_message="",
                invoice=context["invoice_data"],
                items=context["items"],
                bill_to=context["bill_to"],
                company=context["company"],
                company_logo_url=company_logo_url,
                invoice_template_config=context["resolved_template"],
                runtime_placeholders=context["runtime_placeholders"],
                invoice_runtime_data={
                    "invoice": context["invoice_data"],
                    "items": context["items"],
                    "bill_to": context["bill_to"],
                    "company": context["company"],
                    "placeholders": context["runtime_placeholders"],
                },
                print_mode=True,
                hide_builder_sections=True,
            )

            pdf_document = HTML(
                string=html,
                base_url=url_for("invoice_print", invoice_number=invoice_number, _external=True),
            ).render()

            def _page_has_real_invoice_body(page):
                """
                Return True only when a rendered PDF page contains real
                invoice body content.

                Running header/footer elements alone do not count.

                Fail open: if the WeasyPrint page structure cannot be
                inspected, keep the page rather than risk deleting content.
                """
                page_box = getattr(page, "_page_box", None)

                if page_box is None:
                    return True

                body_classes = {
                    "itb-proto-table",
                    "itb-final-page-cluster",
                    "itb-final-closing",
                    "itb-proto-bottom",
                    "itb-approved-terms",
                }

                stack = [page_box]

                while stack:
                    box = stack.pop()

                    element = getattr(box, "element", None)

                    if element is not None:
                        classes = set(
                            (element.get("class") or "").split()
                        )

                        if classes.intersection(body_classes):
                            return True

                    tag = getattr(box, "element_tag", None)

                    # A genuine transaction table page contains
                    # table / row / cell boxes.
                    if tag in {
                        "table",
                        "thead",
                        "tbody",
                        "tr",
                        "td",
                        "th",
                    }:
                        return True

                    children = getattr(box, "children", None)

                    if children:
                        stack.extend(children)

                return False

            output_pages = list(pdf_document.pages)

            # Remove ONLY empty trailing physical pages.
            # Real continuation pages containing transaction rows or
            # closing invoice content are never removed.
            while (
                len(output_pages) > 1
                and not _page_has_real_invoice_body(output_pages[-1])
            ):
                output_pages.pop()

            if len(output_pages) != len(pdf_document.pages):
                pdf_document = pdf_document.copy(output_pages)

            pdf_bytes = pdf_document.write_pdf()

            response = make_response(pdf_bytes)
            response.headers["Content-Type"] = "application/pdf"
            response.headers["Content-Disposition"] = f'attachment; filename="{invoice_number}.pdf"'
            return response

        except Exception as e:
            flash(str(e), "error")
            return redirect(url_for("invoiceprinting"))
