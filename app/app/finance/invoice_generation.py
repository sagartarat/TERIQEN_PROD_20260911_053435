import re
import datetime

from flask import render_template, flash, request
from flask_login import login_required, current_user

from app.common.db import get_db
from app.common.utils import clean, clean_keep_empty, date_to_db, _iso


def _build_partner_hospital_match_sql(column_expr):
    """
    Match partner_reg.short_name with either:
    1. exact value
    2. short_name followed by ' - ...'
    """
    return f"""
        (
            TRIM(UPPER(pr.short_name)) = TRIM(UPPER({column_expr}))
            OR TRIM(UPPER({column_expr})) LIKE CONCAT(TRIM(UPPER(pr.short_name)), ' -%')
        )
    """


def fetch_invoice_hospitals(conn):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT DISTINCT hospital_shrtnm
            FROM patient_wrkflw
            WHERE hospital_shrtnm IS NOT NULL
              AND TRIM(hospital_shrtnm) <> ''
            ORDER BY hospital_shrtnm ASC
            """
        )
        rows = cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass

    return [
        clean_keep_empty(r.get("hospital_shrtnm"))
        for r in rows
        if clean_keep_empty(r.get("hospital_shrtnm"))
    ]


def resolve_invoice_billing_partner(
    conn,
    hospital_short_name,
    invoice_date=None,
):
    """
    Resolve the Partner whose legal / tax details must be used
    for invoice generation.

    Priority:
    1. Active Hospital -> Billing Partner mapping valid on invoice date.
    2. If no mapping exists, use the Hospital itself only when
       invoice_eligible = 'Yes'.
    3. Otherwise block invoice generation.
    """
    hospital_short_name = clean(hospital_short_name)

    if not hospital_short_name:
        raise ValueError("Hospital Short Name is required.")

    as_of_date = invoice_date

    if not isinstance(as_of_date, datetime.date):
        as_of_date = date_to_db(as_of_date)

    as_of_date = as_of_date or datetime.date.today()

    cur = conn.cursor(dictionary=True)

    try:
        # ----------------------------------------------------
        # Resolve the actual Hospital Partner record first.
        # Supports exact short name and existing
        # "SHORTNAME - description" format.
        # ----------------------------------------------------
        sql = f"""
            SELECT
                pr.short_name,
                pr.invoice_eligible,
                pr.partner_status
            FROM partner_reg pr
            WHERE
                {_build_partner_hospital_match_sql("%s")}
                AND TRIM(UPPER(pr.partner_type)) = 'HOSPITAL'
            ORDER BY
                CASE
                    WHEN TRIM(UPPER(pr.short_name))
                         = TRIM(UPPER(%s))
                    THEN 0
                    ELSE 1
                END,
                pr.short_name
            LIMIT 1
        """

        cur.execute(
            sql,
            (
                hospital_short_name,
                hospital_short_name,
                hospital_short_name,
            ),
        )

        hospital_row = cur.fetchone() or {}

        canonical_hospital = clean_keep_empty(
            hospital_row.get("short_name")
        )

        if not canonical_hospital:
            raise ValueError(
                f"Hospital is not maintained in partner_reg: "
                f"{hospital_short_name}"
            )

        # ----------------------------------------------------
        # First priority: active Billing Partner mapping
        # valid for invoice date.
        # ----------------------------------------------------
        cur.execute(
            """
            SELECT
                m.billing_partner_short_name
            FROM hospital_partner_mapping m
            INNER JOIN partner_reg bp
                ON bp.short_name = m.billing_partner_short_name
            WHERE TRIM(UPPER(m.hospital_short_name))
                    = TRIM(UPPER(%s))
              AND m.mapping_status = 'Active'
              AND m.effective_from <= %s
              AND (
                    m.effective_to IS NULL
                    OR m.effective_to >= %s
                  )
              AND bp.invoice_eligible = 'Yes'
              AND LOWER(TRIM(bp.partner_status)) = 'active'
            ORDER BY
                m.effective_from DESC,
                m.mapping_id DESC
            LIMIT 1
            """,
            (
                canonical_hospital,
                as_of_date,
                as_of_date,
            ),
        )

        mapping_row = cur.fetchone() or {}

        mapped_billing_partner = clean_keep_empty(
            mapping_row.get("billing_partner_short_name")
        )

        if mapped_billing_partner:
            return {
                "hospital_short_name": canonical_hospital,
                "billing_partner_short_name": mapped_billing_partner,
                "billing_resolution_source": "MAPPING",
                "billing_resolution_date": as_of_date,
            }

        # ----------------------------------------------------
        # Fallback: Hospital itself can be billing entity only
        # when Invoice Eligible = Yes and Hospital is Active.
        # ----------------------------------------------------
        if (
            clean_keep_empty(hospital_row.get("invoice_eligible")).lower()
            == "yes"
            and clean_keep_empty(hospital_row.get("partner_status")).lower()
            == "active"
        ):
            return {
                "hospital_short_name": canonical_hospital,
                "billing_partner_short_name": canonical_hospital,
                "billing_resolution_source": "HOSPITAL",
                "billing_resolution_date": as_of_date,
            }

        raise ValueError(
            f"Billing Partner mapping required for hospital: "
            f"{canonical_hospital}"
        )

    finally:
        try:
            cur.close()
        except Exception:
            pass


def fetch_invoice_hospital_details(
    conn,
    hospital_short_name,
    invoice_date=None,
):
    """
    Return invoice header / tax details.

    The treating Hospital remains hospital_name.
    Legal, GST, PAN, address, Place of Supply, TDS and Currency
    come from the resolved Billing Partner.
    """
    hospital_short_name = clean(hospital_short_name)

    if not hospital_short_name:
        return {
            "hospital_name": "",
            "hospital_legal_name": "",
            "hospital_address": "",
            "hospital_gst": "",
            "hospital_pan": "",
            "hospital_country": "",
            "hospital_state": "",
            "placeof_supply": "",
            "hospital_city": "",
            "hospital_pincode": "",
            "tds_percentage": "0",
            "inv_currency": "INR",
            "billing_partner_short_name": "",
            "billing_resolution_source": "",
        }

    resolution = resolve_invoice_billing_partner(
        conn,
        hospital_short_name,
        invoice_date,
    )

    treating_hospital = clean_keep_empty(
        resolution.get("hospital_short_name")
    )

    billing_partner = clean_keep_empty(
        resolution.get("billing_partner_short_name")
    )

    cur = conn.cursor(dictionary=True)

    try:
        cur.execute(
            """
            SELECT
                short_name,
                legal_name,
                partner_address,
                gst_number,
                pan_number,
                partner_country,
                partner_state,
                placeof_supply,
                partner_city,
                partner_pincode,
                tds_percentage,
                partner_ccy
            FROM partner_reg
            WHERE TRIM(UPPER(short_name)) = TRIM(UPPER(%s))
              AND invoice_eligible = 'Yes'
              AND LOWER(TRIM(partner_status)) = 'active'
            LIMIT 1
            """,
            (billing_partner,),
        )

        row = cur.fetchone() or {}

    finally:
        try:
            cur.close()
        except Exception:
            pass

    if not row:
        raise ValueError(
            f"Billing Partner is not available or Invoice Eligible: "
            f"{billing_partner}"
        )

    return {
        # Keep treating hospital identity.
        "hospital_name": treating_hospital,

        # Invoice / tax identity comes from Billing Partner.
        "hospital_legal_name": clean_keep_empty(
            row.get("legal_name")
        ),
        "hospital_address": clean_keep_empty(
            row.get("partner_address")
        ),
        "hospital_gst": clean_keep_empty(
            row.get("gst_number")
        ),
        "hospital_pan": clean_keep_empty(
            row.get("pan_number")
        ),
        "hospital_country": clean_keep_empty(
            row.get("partner_country")
        ),
        "hospital_state": clean_keep_empty(
            row.get("partner_state")
        ),
        "placeof_supply": clean_keep_empty(
            row.get("placeof_supply")
        ),
        "hospital_city": clean_keep_empty(
            row.get("partner_city")
        ),
        "hospital_pincode": clean_keep_empty(
            row.get("partner_pincode")
        ),
        "tds_percentage": clean_keep_empty(
            row.get("tds_percentage")
        ) or "0",
        "inv_currency": clean_keep_empty(
            row.get("partner_ccy")
        ) or "INR",

        "billing_partner_short_name": billing_partner,
        "billing_resolution_source": clean_keep_empty(
            resolution.get("billing_resolution_source")
        ),
    }


def fetch_hospital_legal_name(
    conn,
    hospital_short_name,
    invoice_date=None,
):
    """
    Compatibility function.

    Returns the legal name of the resolved Billing Partner,
    not necessarily the treating Hospital.
    """
    details = fetch_invoice_hospital_details(
        conn,
        hospital_short_name,
        invoice_date,
    )

    return clean_keep_empty(
        details.get("hospital_legal_name")
    )


def fetch_invoice_candidate_records(
    conn,
    hospital_short_name=None,
    from_date=None,
    to_date=None,
    search_text=None,
):
    hospital_short_name = clean(hospital_short_name)
    from_date = date_to_db(from_date)
    to_date = date_to_db(to_date)
    search_text = clean(search_text)

    # Invoice Generation currently uses today's date as the
    # invoice date. Candidate suppression therefore applies
    # to the current billing month.
    candidate_invoice_date = datetime.date.today()
    billing_month_start = candidate_invoice_date.replace(day=1)

    if billing_month_start.month == 12:
        billing_month_end = datetime.date(
            billing_month_start.year + 1,
            1,
            1,
        )
    else:
        billing_month_end = datetime.date(
            billing_month_start.year,
            billing_month_start.month + 1,
            1,
        )

    if not hospital_short_name:
        return []

    billing_details = fetch_invoice_hospital_details(
        conn,
        hospital_short_name,
        datetime.date.today(),
    )

    cur = conn.cursor(dictionary=True)
    try:
        sql = f"""
            SELECT
                pw.hospital_shrtnm AS hospital_short_name,
                pw.ld_internalno AS lead_no,
                pw.patpassport_no AS passport_no,
                CONCAT_WS(' ', pw.patgiven_nm, pw.patsur_nm) AS patient_name,
                pw.hospital_shrtnm AS hospital,
                pw.wrkflw_status AS status,
                DATE(pw.entered_date) AS status_date,
                '999723' AS sac_code,
                '18%' AS gst_percentage,
                COALESCE(pr.tds_percentage, 0) AS tds_percentage,
                COALESCE(pr.partner_ccy, 'INR') AS inv_currency
            FROM patient_wrkflw pw

            INNER JOIN wrkflwrule_master wr
                ON UPPER(TRIM(wr.status_name))
                 = UPPER(TRIM(pw.wrkflw_status))
               AND COALESCE(
                    wr.invoice_candidate_eligible,
                    0
               ) = 1

            LEFT JOIN partner_reg pr
                ON {_build_partner_hospital_match_sql("pw.hospital_shrtnm")}
               AND TRIM(UPPER(pr.partner_type)) = 'HOSPITAL'

            WHERE pw.hospital_shrtnm = %s

              AND NOT EXISTS (
                  SELECT 1
                  FROM invoice_master im
                  WHERE TRIM(UPPER(im.hospital_name))
                        = TRIM(UPPER(pw.hospital_shrtnm))
                    AND TRIM(im.ld_internalno)
                        = TRIM(pw.ld_internalno)
                    AND im.invoice_date >= %s
                    AND im.invoice_date < %s
              )
        """
        params = [
            hospital_short_name,
            billing_month_start,
            billing_month_end,
        ]

        if from_date:
            sql += " AND DATE(pw.entered_date) >= %s"
            params.append(from_date)

        if to_date:
            sql += " AND DATE(pw.entered_date) <= %s"
            params.append(to_date)

        if search_text:
            like_val = f"%{search_text}%"
            sql += """
                AND (
                    pw.ld_internalno LIKE %s
                    OR pw.patpassport_no LIKE %s
                    OR CONCAT_WS(' ', pw.patgiven_nm, pw.patsur_nm) LIKE %s
                )
            """
            params.extend([like_val, like_val, like_val])

        sql += """
            ORDER BY pw.entered_date DESC, pw.ld_internalno DESC
        """

        cur.execute(sql, tuple(params))
        rows = cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass

    out = []
    for r in rows:
        out.append({
            "hospital_short_name": clean_keep_empty(r.get("hospital_short_name")),
            "lead_no": clean_keep_empty(r.get("lead_no")),
            "passport_no": clean_keep_empty(r.get("passport_no")),
            "patient_name": clean_keep_empty(r.get("patient_name")),
            "hospital": clean_keep_empty(r.get("hospital")),
            "status": clean_keep_empty(r.get("status")),
            "status_date": _iso(r.get("status_date")),
            "sac_code": clean_keep_empty(r.get("sac_code")) or "999723",
            "gst_percentage": clean_keep_empty(r.get("gst_percentage")) or "18%",
            "tds_percentage": clean_keep_empty(
                billing_details.get("tds_percentage")
            ) or "0",
            "inv_currency": clean_keep_empty(
                billing_details.get("inv_currency")
            ) or "INR",
        })
    return out


def _to_decimal(value, default=0.0):
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


def get_invoice_month_short(dt_value=None):
    dt_value = dt_value or datetime.date.today()
    return dt_value.strftime("%b").upper()


def build_invoice_prefix(hospital_name, dt_value=None):
    hospital_name = clean_keep_empty(hospital_name)
    dt_value = dt_value or datetime.date.today()

    hosp = re.sub(r"[^A-Za-z0-9]", "", hospital_name.replace(" ", "").upper())
    if not hosp:
        hosp = "HOSPITAL"

    return f"{hosp}-{get_invoice_month_short(dt_value)}-{dt_value.year}"


def get_next_invoice_running_seq(conn, hospital_name, dt_value=None):
    dt_value = dt_value or datetime.date.today()
    prefix = build_invoice_prefix(hospital_name, dt_value)

    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT invoice_number
            FROM invoice_master
            WHERE invoice_number LIKE %s
            ORDER BY invoice_number DESC
            """,
            (prefix + "-%",),
        )
        rows = cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass

    max_seq = 0
    for row in rows:
        inv_no = clean_keep_empty(row[0] if isinstance(row, (list, tuple)) else row)
        m = re.search(r"-(\d+)$", inv_no)
        if m:
            try:
                seq = int(m.group(1))
                if seq > max_seq:
                    max_seq = seq
            except Exception:
                pass

    return max_seq + 1


def build_invoice_number(conn, hospital_name, dt_value=None):
    dt_value = dt_value or datetime.date.today()
    prefix = build_invoice_prefix(hospital_name, dt_value)
    seq = get_next_invoice_running_seq(conn, hospital_name, dt_value)
    return f"{prefix}-{seq:02d}"


def update_patient_wrkflw_after_invoice_generation(conn, invoice_rows, updated_by):
    if not invoice_rows:
        return 0

    updated_by = clean(updated_by) or "SYSTEM"
    updated_on = datetime.datetime.now()
    updated_count = 0

    cur = conn.cursor()
    try:
        for row in invoice_rows:
            ld_internalno = clean(row.get("ld_internalno"))
            patient_passport = clean(row.get("patient_passport"))

            if not ld_internalno:
                continue

            if patient_passport:
                cur.execute(
                    """
                    UPDATE patient_wrkflw
                    SET
                        wrkflw_status = %s,
                        entered_by = %s,
                        entered_date = %s
                    WHERE ld_internalno = %s
                      AND patpassport_no = %s
                    """,
                    (
                        "INVOICE GENERATED",
                        updated_by,
                        updated_on,
                        ld_internalno,
                        patient_passport,
                    ),
                )
            else:
                cur.execute(
                    """
                    UPDATE patient_wrkflw
                    SET
                        wrkflw_status = %s,
                        entered_by = %s,
                        entered_date = %s
                    WHERE ld_internalno = %s
                    """,
                    (
                        "INVOICE GENERATED",
                        updated_by,
                        updated_on,
                        ld_internalno,
                    ),
                )

            updated_count += cur.rowcount or 0
    finally:
        try:
            cur.close()
        except Exception:
            pass

    return updated_count


def insert_invoice_master_rows(conn, payload, created_by):
    hospital_short_name = clean((payload or {}).get("hospital_short_name"))
    selected_rows = (payload or {}).get("selected_rows") or []
    header = (payload or {}).get("header") or {}

    if not hospital_short_name:
        raise ValueError("Hospital Short Name is required.")

    if not isinstance(selected_rows, list) or not selected_rows:
        raise ValueError("At least one selected row is required for invoice generation.")

    invoice_date = (
        date_to_db((payload or {}).get("invoice_date"))
        or datetime.date.today()
    )

    hospital_details = fetch_invoice_hospital_details(
        conn,
        hospital_short_name,
        invoice_date,
    )

    # Treating Hospital continues to drive the Hospital identity
    # and existing invoice-number series.
    hospital_name = clean(
        hospital_details.get("hospital_name")
        or hospital_short_name
    ) or hospital_short_name

    # Legal / tax / address identity is always resolved
    # server-side from the Billing Partner.
    hospital_legal_name = clean(
        hospital_details.get("hospital_legal_name")
    )

    if not hospital_legal_name:
        raise ValueError(
            "Legal Name is not maintained in partner_reg for "
            f"Billing Partner: "
            f"{hospital_details.get('billing_partner_short_name') or hospital_short_name}"
        )

    hospital_address = clean(
        hospital_details.get("hospital_address")
    )
    hospital_gst = clean(
        hospital_details.get("hospital_gst")
    )
    hospital_pan = clean(
        hospital_details.get("hospital_pan")
    )
    hospital_country = clean(
        hospital_details.get("hospital_country")
    )
    hospital_state = clean(
        hospital_details.get("hospital_state")
    )
    placeof_supply = clean(
        hospital_details.get("placeof_supply")
    )
    hospital_city = clean(
        hospital_details.get("hospital_city")
    )
    hospital_pincode = clean(
        hospital_details.get("hospital_pincode")
    )
    inv_currency = clean(
        hospital_details.get("inv_currency")
        or "INR"
    ) or "INR"
    invoice_number = build_invoice_number(conn, hospital_name, invoice_date)

    billing_month_start = invoice_date.replace(day=1)

    if billing_month_start.month == 12:
        billing_month_end = datetime.date(
            billing_month_start.year + 1,
            1,
            1,
        )
    else:
        billing_month_end = datetime.date(
            billing_month_start.year,
            billing_month_start.month + 1,
            1,
        )

    selected_records = len(selected_rows)
    invoice_total_gst_amount = 0.0
    invoice_total_tds_amount = 0.0
    invoice_total_net_amount = 0.0

    for row in selected_rows:
        row_gst_amount = _to_decimal(row.get("gst_amount") or row.get("totalgst_amount"))
        row_tds_amount = _to_decimal(row.get("tds_amount") or row.get("totaltds_amount"))
        row_net_amount = _to_decimal(row.get("net_amount") or row.get("netinv_amount"))

        invoice_total_gst_amount += row_gst_amount
        invoice_total_tds_amount += row_tds_amount
        invoice_total_net_amount += row_net_amount

    inserted_count = 0
    inserted_rows = []

    cur = conn.cursor()
    try:
        sql = """
            INSERT INTO invoice_master (
                invoice_number,
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
                wrkflw_status,
                status_date,
                invoice_date,
                inv_amount,
                inv_currency,
                totalinv_amount,
                sac_code,
                gst_percentage,
                tds_percentage,
                totaltds_amount,
                totalgst_amount,
                netinv_amount,
                netgst_amount,
                nettds_amount,
                selected_records,
                custom_narration
            ) VALUES (
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
            )
        """

        for row in selected_rows:
            ld_internalno = clean(row.get("lead_no") or row.get("ld_internalno"))
            patient_passport = clean(row.get("passport_no") or row.get("patient_passport"))
            patient_name = clean(row.get("patient_name"))

            wrkflw_status = "INVOICE GENERATED"
            status_date = date_to_db(row.get("status_date")) or invoice_date

            inv_amount = _to_decimal(row.get("invoice_amount") or row.get("inv_amount"))
            row_gst_amount = _to_decimal(row.get("gst_amount") or row.get("totalgst_amount"))
            row_tds_amount = _to_decimal(row.get("tds_amount") or row.get("totaltds_amount"))
            row_net_amount = _to_decimal(row.get("net_amount") or row.get("netinv_amount"))

            totalinv_amount = row_net_amount

            sac_code = clean(row.get("sac_code")) or "999723"
            gst_percentage = _to_decimal(row.get("gst_percentage"), 18)
            tds_percentage = _to_decimal(row.get("tds_percentage") or row.get("tds_percentage"), 0)

            if not ld_internalno:
                raise ValueError("Lead No is missing in one of the selected rows.")
            if not patient_passport:
                raise ValueError("Passport No is missing in one of the selected rows.")
            if inv_amount <= 0:
                raise ValueError(
                    f"Invoice Amount must be greater than zero "
                    f"for Lead No: {ld_internalno}"
                )

            # Final server-side safety check.
            #
            # The same LD may be invoiced again in a later month,
            # but not twice for the same treating hospital in the
            # same billing month.
            cur.execute(
                """
                SELECT invoice_number
                FROM invoice_master
                WHERE TRIM(UPPER(hospital_name))
                      = TRIM(UPPER(%s))
                  AND TRIM(ld_internalno) = TRIM(%s)
                  AND invoice_date >= %s
                  AND invoice_date < %s
                LIMIT 1
                """,
                (
                    hospital_name,
                    ld_internalno,
                    billing_month_start,
                    billing_month_end,
                ),
            )

            duplicate_invoice = cur.fetchone()

            if duplicate_invoice:
                existing_invoice = (
                    duplicate_invoice[0]
                    if isinstance(duplicate_invoice, (tuple, list))
                    else duplicate_invoice
                )

                raise ValueError(
                    f"Lead No {ld_internalno} has already been "
                    f"invoiced for {hospital_name} in "
                    f"{billing_month_start.strftime('%b-%Y')} "
                    f"under Invoice No: {existing_invoice}."
                )

            cur.execute(
                sql,
                (
                    invoice_number,
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
                    wrkflw_status,
                    status_date,
                    invoice_date,
                    inv_amount,
                    inv_currency,
                    totalinv_amount,
                    sac_code,
                    gst_percentage,
                    tds_percentage,
                    row_tds_amount,
                    row_gst_amount,
                    invoice_total_net_amount,
                    invoice_total_gst_amount,
                    invoice_total_tds_amount,
                    selected_records,
                    None,
                ),
            )

            inserted_count += 1
            inserted_rows.append({
                "invoice_number": invoice_number,
                "ld_internalno": ld_internalno,
                "patient_passport": patient_passport,
            })
    finally:
        try:
            cur.close()
        except Exception:
            pass

    # Invoice generation is a Finance event only.
    #
    # Do NOT change patient_wrkflw here.
    # The patient's operational workflow remains independent
    # from the invoice / reconciliation lifecycle.
    workflow_updated_count = 0

    return {
        "invoice_number": invoice_number,
        "inserted_count": inserted_count,
        "workflow_updated_count": workflow_updated_count,
        "rows": inserted_rows,
        "entered_by": clean(created_by) or "SYSTEM",
        "invoice_date": _iso(invoice_date),
        "selected_records": selected_records,
        "netinv_amount": invoice_total_net_amount,
        "netgst_amount": invoice_total_gst_amount,
        "nettds_amount": invoice_total_tds_amount,
    }


def register_invoice_generation_routes(app):
    @app.route("/invoicegeneration", methods=["GET"])
    @login_required
    def invoicegeneration():
        conn = None
        try:
            conn = get_db()
            hospitals = fetch_invoice_hospitals(conn)
            gst_lov = ["18%"]
            records = []

            return render_template(
                "invoicegeneration.html",
                user=current_user,
                hospitals=hospitals,
                gst_lov=gst_lov,
                records=records,
            )
        except Exception as e:
            print("invoicegeneration error:", e)
            flash(f"Invoice Generation Error: {str(e)}", "error")
            return render_template(
                "invoicegeneration.html",
                user=current_user,
                hospitals=[],
                gst_lov=["18%"],
                records=[],
            )
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass
