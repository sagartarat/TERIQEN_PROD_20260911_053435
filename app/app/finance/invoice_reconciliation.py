import datetime

from flask import render_template, flash, request, jsonify
from flask_login import login_required, current_user

from app.common.db import get_db_connection


def parse_recon_filter_date(date_text):
    try:
        if not date_text:
            return None
        return datetime.datetime.strptime(str(date_text), "%Y-%m-%d").date()
    except Exception:
        return None


def validate_recon_date_range(from_date, to_date, max_days=365):
    if from_date and to_date:
        if from_date > to_date:
            return False, "From Date cannot be greater than To Date."
        if (to_date - from_date).days > max_days:
            return False, "Date range cannot exceed one year."
    return True, ""


def fetch_invoice_payment_summary(cursor):
    cursor.execute("""
        SELECT
            invoice_number,
            COALESCE(SUM(
                CASE
                    WHEN payment_status = 'POSTED'
                    THEN received_amount
                    ELSE 0
                END
            ), 0) AS total_received,
            COALESCE(SUM(
                CASE
                    WHEN payment_status = 'POSTED'
                    THEN 1
                    ELSE 0
                END
            ), 0) AS payment_count,
            MAX(
                CASE
                    WHEN payment_status = 'POSTED'
                    THEN payment_date
                    ELSE NULL
                END
            ) AS last_payment_date
        FROM invrecon_payment_detail
        GROUP BY invoice_number
    """)

    rows = cursor.fetchall() or []

    return {
        row["invoice_number"]: row
        for row in rows
        if row.get("invoice_number")
    }


def fetch_invoice_payment_history(conn, invoice_number):
    invoice_number = (invoice_number or "").strip()

    if not invoice_number:
        return []

    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                id,
                invoice_number,
                payment_seq,
                received_amount,
                payment_date,
                utrref_no,
                payment_remarks,
                payment_status,
                entered_by,
                entered_on,
                cancelled_by,
                cancelled_on
            FROM invrecon_payment_detail
            WHERE invoice_number = %s
            ORDER BY payment_seq ASC, id ASC
        """, (invoice_number,))

        return cursor.fetchall() or []

    finally:
        cursor.close()


def fetch_invoice_payment_totals(conn, invoice_number):
    invoice_number = (invoice_number or "").strip()

    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute("""
            SELECT
                COALESCE(SUM(
                    CASE
                        WHEN payment_status = 'POSTED'
                        THEN received_amount
                        ELSE 0
                    END
                ), 0) AS total_received,

                COALESCE(SUM(
                    CASE
                        WHEN payment_status = 'POSTED'
                        THEN 1
                        ELSE 0
                    END
                ), 0) AS payment_count,

                MAX(
                    CASE
                        WHEN payment_status = 'POSTED'
                        THEN payment_date
                        ELSE NULL
                    END
                ) AS last_payment_date

            FROM invrecon_payment_detail
            WHERE invoice_number = %s
        """, (invoice_number,))

        row = cursor.fetchone() or {}

        return {
            "total_received": row.get("total_received") or 0,
            "payment_count": int(row.get("payment_count") or 0),
            "last_payment_date": row.get("last_payment_date"),
        }

    finally:
        cursor.close()


def load_invoice_reconciliation_workbench(cursor, from_date=None, to_date=None):
    reconciliation_rows = []

    invoice_query = """
        SELECT
            invoice_number,
            MAX(status_date) AS status_date,
            MAX(hospital_name) AS hospital_name,
            MAX(netinv_amount) AS netinv_amount,
            MAX(wrkflw_status) AS wrkflw_status,
            MAX(print_status) AS print_status
        FROM invoice_master
        WHERE
        (
            (
                wrkflw_status = 'INVOICE GENERATED'
                AND print_status = 'Printed'
            )
            OR
            (
                wrkflw_status = 'RECONCILED'
            )
        )
    """

    invoice_filters = []
    invoice_params = []

    if from_date:
        invoice_filters.append("DATE(status_date) >= %s")
        invoice_params.append(from_date)

    if to_date:
        invoice_filters.append("DATE(status_date) <= %s")
        invoice_params.append(to_date)

    if invoice_filters:
        invoice_query += " AND " + " AND ".join(invoice_filters)

    invoice_query += """
        GROUP BY invoice_number
        ORDER BY MAX(status_date) DESC, invoice_number DESC
    """

    cursor.execute(invoice_query, tuple(invoice_params))
    invoice_rows = cursor.fetchall() or []

    cursor.execute("""
        SELECT
            invoice_number,
            MAX(recvd_amount) AS received_amount,
            MAX(diff_amount) AS difference_amount,
            MAX(utrref_no) AS utr_ref_no,
            MAX(pay_date) AS payment_date,
            MAX(recon_status) AS recon_status,
            MAX(recon_remarks) AS remarks
        FROM invrecon_master
        GROUP BY invoice_number
    """)

    recon_rows = cursor.fetchall() or []

    recon_map = {
        row["invoice_number"]: row
        for row in recon_rows
        if row.get("invoice_number")
    }

    payment_map = fetch_invoice_payment_summary(cursor)

    matched = 0
    partial_matches = 0
    payment_full = 0
    payment_partial = 0
    payment_unpaid = 0
    payment_overpaid = 0

    for inv in invoice_rows:
        invoice_number = inv.get("invoice_number")
        recon = recon_map.get(invoice_number, {})
        payment = payment_map.get(invoice_number, {})

        current_recon_status = (
            recon.get("recon_status") or "Pending"
        ).strip()

        already_reconciled = current_recon_status in [
            "MATCHED",
            "PARTIAL MATCH",
        ]

        if current_recon_status == "MATCHED":
            matched += 1
        elif current_recon_status == "PARTIAL MATCH":
            partial_matches += 1

        invoice_amount = inv.get("netinv_amount") or 0

        payment_count = int(
            payment.get("payment_count") or 0
        )

        total_received = (
            payment.get("total_received") or 0
        )

        # Backward-compatible fallback for historical rows.
        if payment_count == 0 and already_reconciled:
            total_received = recon.get("received_amount") or 0

        outstanding_amount = invoice_amount - total_received

        if total_received <= 0:
            payment_status = "UNPAID"
            payment_unpaid += 1

        elif total_received < invoice_amount:
            payment_status = "PARTIALLY RECEIVED"
            payment_partial += 1

        elif total_received == invoice_amount:
            payment_status = "FULLY RECEIVED"
            payment_full += 1

        else:
            payment_status = "OVERPAID"
            payment_overpaid += 1

        reconciliation_rows.append({
            "invoice_number": invoice_number,
            "invoice_date": inv.get("status_date"),
            "hospital_name": inv.get("hospital_name"),
            "netinv_amount": invoice_amount,

            "received_amount": total_received,
            "total_received": total_received,
            "outstanding_amount": outstanding_amount,
            "payment_count": payment_count,
            "last_payment_date": payment.get("last_payment_date"),
            "payment_status": payment_status,

            "difference_amount": (
                recon.get("difference_amount")
                if already_reconciled
                else ""
            ),
            "utr_ref_no": (
                recon.get("utr_ref_no")
                if already_reconciled
                else ""
            ),
            "payment_date": (
                recon.get("payment_date")
                if already_reconciled
                else payment.get("last_payment_date") or ""
            ),
            "recon_status": current_recon_status,
            "workflow_status": (
                inv.get("wrkflw_status")
                or "INVOICE GENERATED"
            ),
            "remarks": (
                recon.get("remarks")
                if already_reconciled
                else ""
            ),
            "already_reconciled": already_reconciled,
        })

    total_invoices = len(reconciliation_rows)

    pending = total_invoices - (
        matched + partial_matches
    )

    if pending < 0:
        pending = 0

    return {
        "reconciliation_rows": reconciliation_rows,
        "total_invoices": total_invoices,
        "matched": matched,
        "partial_matches": partial_matches,
        "pending": pending,

        "payment_full": payment_full,
        "payment_partial": payment_partial,
        "payment_unpaid": payment_unpaid,
        "payment_overpaid": payment_overpaid,

        "invoice_rows_count": len(invoice_rows),
        "recon_rows_count": len(recon_rows),
    }




def insert_invoice_payment(conn, data, entered_by):
    """
    Register one hospital receipt/payment against an invoice.

    Partial payments are stored only in invrecon_payment_detail.
    This function DOES NOT mark the invoice as RECONCILED.
    """
    from decimal import Decimal, InvalidOperation

    invoice_number = (data.get("invoice_number") or "").strip()

    payment_date_text = (
        data.get("payment_date")
        or data.get("pay_date")
        or ""
    ).strip()

    utrref_no = (
        data.get("utr_ref_no")
        or data.get("utrref_no")
        or ""
    ).strip()

    payment_remarks = (
        data.get("payment_remarks")
        or data.get("remarks")
        or ""
    ).strip()

    if not invoice_number:
        raise ValueError("Invoice Number is required.")

    try:
        received_amount = Decimal(
            str(data.get("received_amount") or "0")
        ).quantize(Decimal("0.01"))
    except (InvalidOperation, ValueError):
        raise ValueError("Received Amount is invalid.")

    if received_amount <= 0:
        raise ValueError(
            "Received Amount must be greater than zero."
        )

    payment_date = parse_recon_filter_date(payment_date_text)

    if not payment_date:
        raise ValueError("Valid Payment Date is required.")

    if not utrref_no:
        raise ValueError(
            "UTR / Reference Number is required."
        )

    cursor = conn.cursor(dictionary=True)

    try:
        # Lock an invoice row while assigning the next payment sequence.
        cursor.execute("""
            SELECT invoice_number
            FROM invoice_master
            WHERE invoice_number = %s
            LIMIT 1
            FOR UPDATE
        """, (invoice_number,))

        if not cursor.fetchone():
            raise ValueError("Invoice not found.")

        cursor.execute("""
            SELECT
                MAX(netinv_amount) AS invoice_amount,
                MAX(hospital_name) AS hospital_name,
                MAX(wrkflw_status) AS workflow_status,
                MAX(print_status) AS print_status
            FROM invoice_master
            WHERE invoice_number = %s
        """, (invoice_number,))

        invoice_row = cursor.fetchone() or {}

        invoice_amount = (
            invoice_row.get("invoice_amount") or 0
        )

        if not invoice_amount:
            raise ValueError(
                "Invoice Amount could not be determined."
            )

        workflow_status = (
            invoice_row.get("workflow_status") or ""
        ).strip().upper()

        if workflow_status == "RECONCILED":
            raise ValueError(
                "This invoice is already reconciled. "
                "Additional payment cannot be posted."
            )

        cursor.execute("""
            SELECT recon_status
            FROM invrecon_master
            WHERE invoice_number = %s
            LIMIT 1
        """, (invoice_number,))

        recon_row = cursor.fetchone()

        if recon_row:
            recon_status = (
                recon_row.get("recon_status") or ""
            ).strip().upper()

            if recon_status in (
                "MATCHED",
                "PARTIAL MATCH",
            ):
                raise ValueError(
                    "This invoice is already finally reconciled. "
                    "Additional payment cannot be posted."
                )

        cursor.execute("""
            SELECT
                COALESCE(MAX(payment_seq), 0) + 1
                    AS next_payment_seq
            FROM invrecon_payment_detail
            WHERE invoice_number = %s
        """, (invoice_number,))

        seq_row = cursor.fetchone() or {}
        payment_seq = int(
            seq_row.get("next_payment_seq") or 1
        )

        cursor.execute("""
            INSERT INTO invrecon_payment_detail (
                invoice_number,
                payment_seq,
                received_amount,
                payment_date,
                utrref_no,
                payment_remarks,
                payment_status,
                entered_by,
                entered_on
            )
            VALUES (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                'POSTED',
                %s,
                NOW()
            )
        """, (
            invoice_number,
            payment_seq,
            received_amount,
            payment_date,
            utrref_no,
            payment_remarks or None,
            (entered_by or "SYSTEM").strip(),
        ))

        payment_id = cursor.lastrowid

    finally:
        cursor.close()

    totals = fetch_invoice_payment_totals(
        conn,
        invoice_number
    )

    total_received = totals["total_received"]
    outstanding_amount = (
        invoice_amount - total_received
    )

    if total_received <= 0:
        payment_status = "UNPAID"

    elif total_received < invoice_amount:
        payment_status = "PARTIALLY RECEIVED"

    elif total_received == invoice_amount:
        payment_status = "FULLY RECEIVED"

    else:
        payment_status = "OVERPAID"

    return {
        "payment_id": payment_id,
        "payment_seq": payment_seq,
        "invoice_number": invoice_number,
        "hospital_name": invoice_row.get(
            "hospital_name"
        ),
        "invoice_amount": invoice_amount,
        "received_now": received_amount,
        "total_received": total_received,
        "outstanding_amount": outstanding_amount,
        "payment_count": totals["payment_count"],
        "last_payment_date": totals[
            "last_payment_date"
        ],
        "payment_status": payment_status,
    }


def insert_invoice_reconciliation(conn, data):
    cursor = conn.cursor()

    sql = """
        INSERT INTO invrecon_master (
            invoice_number,
            status_date,
            hospital_name,
            netinv_amount,
            recvd_amount,
            diff_amount,
            utrref_no,
            pay_date,
            recon_status,
            wrkflw_status,
            recon_remarks
        )
        VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)
    """

    values = (
        data.get("invoice_number"),
        data.get("invoice_date"),
        data.get("hospital_name"),
        data.get("invoice_amount"),
        data.get("received_amount"),
        data.get("difference"),
        data.get("utr_ref_no"),
        data.get("payment_date"),
        data.get("recon_status"),
        data.get("workflow_status"),
        data.get("remarks")
    )

    cursor.execute(sql, values)
    cursor.close()


def register_invoice_reconciliation_routes(app):
    @app.route("/invoice-reconciliation", methods=["GET"])
    @login_required
    def invoice_reconciliation():
        today_dt = datetime.date.today()
        today_date = today_dt.strftime("%Y-%m-%d")
        min_filter_date = (today_dt - datetime.timedelta(days=365)).strftime("%Y-%m-%d")

        from_date_str = (request.args.get("from_date") or "").strip()
        to_date_str = (request.args.get("to_date") or "").strip()

        from_date = parse_recon_filter_date(from_date_str)
        to_date = parse_recon_filter_date(to_date_str)

        reconciliation_rows = []
        total_invoices = 0
        matched = 0
        partial_matches = 0
        pending = 0

        conn = None
        cursor = None

        try:
            if from_date_str or to_date_str:
                if not from_date or not to_date:
                    flash("Please select valid From Date and To Date.", "danger")
                    from_date = None
                    to_date = None
                    from_date_str = ""
                    to_date_str = ""
                else:
                    is_valid_range, validation_message = validate_recon_date_range(from_date, to_date)
                    if not is_valid_range:
                        flash(validation_message, "danger")
                        from_date = None
                        to_date = None
                        from_date_str = ""
                        to_date_str = ""

            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            workbench_data = load_invoice_reconciliation_workbench(
                cursor=cursor,
                from_date=from_date,
                to_date=to_date
            )

            reconciliation_rows = workbench_data["reconciliation_rows"]
            total_invoices = workbench_data["total_invoices"]
            matched = workbench_data["matched"]
            partial_matches = workbench_data["partial_matches"]
            pending = workbench_data["pending"]

        except Exception as e:
            print("Invoice reconciliation error:", e)

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

        return render_template(
            "invoicereconciliation.html",
            user=current_user,
            today_date=today_date,
            min_filter_date=min_filter_date,
            from_date=from_date_str,
            to_date=to_date_str,
            reconciliation_rows=reconciliation_rows,
            total_invoices=total_invoices,
            matched=matched,
            partial_matches=partial_matches,
            pending=pending
        )
