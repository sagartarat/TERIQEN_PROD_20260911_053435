from datetime import datetime
from decimal import Decimal, ROUND_HALF_UP

from app.common.db_metadata import get_table_columns


AGENCY_BASE_FACTOR = Decimal("0.925926")  # 92.5926% 


def _to_decimal(value):
    try:
        return Decimal(str(value or 0)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    except Exception:
        return Decimal("0.00")


def _table_columns(conn, table_name):
    try:
        return get_table_columns(conn, table_name)
    except Exception:
        return set()


def _coalesce_expr(parts, default="''"):
    clean = [p for p in parts if p]
    if not clean:
        return default
    return "COALESCE(" + ", ".join(clean + [default]) + ")"


def _partner_name_expr(pr_cols, alias="pr"):
    if "partner_name" in pr_cols:
        return f"{alias}.partner_name"
    if "partner_fullname" in pr_cols:
        return f"{alias}.partner_fullname"
    if "full_name" in pr_cols:
        return f"{alias}.full_name"
    if "legal_name" in pr_cols:
        return f"{alias}.legal_name"
    if "prim_contper" in pr_cols:
        return f"{alias}.prim_contper"
    if "partner_desc" in pr_cols:
        return f"{alias}.partner_desc"
    return f"{alias}.short_name"


def generate_agent_payment_no(conn):
    today = datetime.now()
    prefix = f"AGTPAY-{today.strftime('%b').upper()}-{today.year}-"

    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT payment_no
        FROM agent_payment_master
        WHERE payment_no LIKE %s
        ORDER BY id DESC
        LIMIT 1
    """, (prefix + "%",))

    row = cursor.fetchone()
    cursor.close()

    if not row:
        return prefix + "0001"

    last_no = row["payment_no"].split("-")[-1]
    next_no = int(last_no) + 1
    return prefix + str(next_no).zfill(4)


def fetch_agent_payment_agents(conn):
    pr_cols = _table_columns(conn, "partner_reg")
    name_expr = _partner_name_expr(pr_cols, "partner_reg")
    percentage_expr = "COALESCE(agent_percentage, 0)" if "agent_percentage" in pr_cols else "0"

    cursor = conn.cursor(dictionary=True)
    sql = f"""
        SELECT
            short_name AS agent_code,
            COALESCE({name_expr}, short_name, '') AS agent_name,
            {percentage_expr} AS agent_percentage
        FROM partner_reg
        WHERE UPPER(TRIM(partner_type)) = 'AGENCY'
          AND short_name IS NOT NULL
          AND TRIM(short_name) <> ''
        ORDER BY COALESCE({name_expr}, short_name, '')
    """
    cursor.execute(sql)
    rows = cursor.fetchall()
    cursor.close()
    return rows


def fetch_agent_payment_invoice_lov(conn):
    cursor = conn.cursor(dictionary=True)
    cursor.execute("""
        SELECT DISTINCT invoice_number
        FROM invrecon_master
        WHERE invoice_number IS NOT NULL
          AND TRIM(invoice_number) <> ''
        ORDER BY invoice_number DESC
    """)
    rows = cursor.fetchall()
    cursor.close()
    return rows


def _build_patient_workflow_join(conn):
    im_cols = _table_columns(conn, "invoice_master")
    pw_cols = _table_columns(conn, "patient_wrkflw")

    # IMPORTANT FIX:
    # Your invoice_master has ld_internalno and patient_wrkflw also has ld_internalno.
    # Without this join, pw.agent_shrtnm remains NULL and partner_reg.agent_percentage becomes 0.
    if "ld_internalno" in im_cols and "ld_internalno" in pw_cols:
        return "LEFT JOIN patient_wrkflw pw ON UPPER(TRIM(pw.ld_internalno)) = UPPER(TRIM(im.ld_internalno))"

    if "invoice_number" in pw_cols:
        return "LEFT JOIN patient_wrkflw pw ON UPPER(TRIM(pw.invoice_number)) = UPPER(TRIM(irm.invoice_number))"
    if "wrkflw_id" in im_cols and "id" in pw_cols:
        return "LEFT JOIN patient_wrkflw pw ON pw.id = im.wrkflw_id"
    if "patient_wrkflw_id" in im_cols and "id" in pw_cols:
        return "LEFT JOIN patient_wrkflw pw ON pw.id = im.patient_wrkflw_id"
    if "wrkflw_no" in im_cols and "wrkflw_no" in pw_cols:
        return "LEFT JOIN patient_wrkflw pw ON UPPER(TRIM(pw.wrkflw_no)) = UPPER(TRIM(im.wrkflw_no))"
    if "patient_wrkflw_no" in im_cols and "wrkflw_no" in pw_cols:
        return "LEFT JOIN patient_wrkflw pw ON UPPER(TRIM(pw.wrkflw_no)) = UPPER(TRIM(im.patient_wrkflw_no))"
    if "patient_passport" in im_cols and "patpassport_no" in pw_cols:
        return "LEFT JOIN patient_wrkflw pw ON UPPER(TRIM(pw.patpassport_no)) = UPPER(TRIM(im.patient_passport))"
    if "patpassport_no" in im_cols and "patpassport_no" in pw_cols:
        return "LEFT JOIN patient_wrkflw pw ON UPPER(TRIM(pw.patpassport_no)) = UPPER(TRIM(im.patpassport_no))"
    if "passport_no" in im_cols and "patpassport_no" in pw_cols:
        return "LEFT JOIN patient_wrkflw pw ON UPPER(TRIM(pw.patpassport_no)) = UPPER(TRIM(im.passport_no))"

    return "LEFT JOIN patient_wrkflw pw ON 1 = 0"


def fetch_agent_payment_invoices(
    conn,
    agent="",
    invoice_no="",
    from_date="",
    to_date="",
    status="",
    like="",
):
    """
    Agent Payment Workbench.

    One row represents one Invoice + Agent combination.

    Important:
      * invoice_master contains one row per patient/case.
      * netinv_amount is an invoice-level total repeated on those rows.
      * therefore netinv_amount must never be treated as a patient-row
        payable amount.

    Allocation logic:
      Agent Patient Value
        = SUM(invoice_master.totalinv_amount) for that agent

      Invoice Total
        = MAX(invoice_master.netinv_amount)

      Actual Reconciled Receipt
        = invrecon_master.netinv_amount + invrecon_master.diff_amount

      Agent Allocated Receipt
        = Actual Reconciled Receipt
          * Agent Patient Value / Invoice Total

      Agency Pay
        = Agent Allocated Receipt
          * 92.5926%
          * Agent Percentage
    """

    cursor = conn.cursor(dictionary=True)

    pr_cols = _table_columns(conn, "partner_reg")
    partner_name_expr = _partner_name_expr(
        pr_cols,
        "pr",
    )

    where = []
    params = []

    if agent:
        where.append("""
            (
                UPPER(TRIM(ia.agent_code)) = UPPER(TRIM(%s))
                OR UPPER(TRIM(ia.agent_name)) = UPPER(TRIM(%s))
            )
        """)
        params.extend([agent, agent])

    if invoice_no:
        where.append(
            "UPPER(TRIM(ia.invoice_number)) = UPPER(TRIM(%s))"
        )
        params.append(invoice_no)

    if from_date:
        where.append("DATE(ia.invoice_date) >= %s")
        params.append(from_date)

    if to_date:
        where.append("DATE(ia.invoice_date) <= %s")
        params.append(to_date)

    if like:
        like_param = f"%{like}%"
        where.append("""
            (
                ia.invoice_number LIKE %s
                OR ia.agent_code LIKE %s
                OR ia.agent_name LIKE %s
                OR ia.patient_case LIKE %s
            )
        """)
        params.extend([
            like_param,
            like_param,
            like_param,
            like_param,
        ])

    where_sql = (
        "WHERE " + " AND ".join(where)
        if where
        else ""
    )

    query = f"""
        SELECT
            ia.invoice_number AS invoice_no,
            DATE_FORMAT(
                ia.invoice_date,
                '%Y-%m-%d'
            ) AS invoice_date,

            ia.agent_code AS agent_code,
            ia.agent_code AS agent_shrtnm,
            ia.agent_name AS agent_name,

            ia.patient_case AS patient_name,
            ia.case_count AS case_count,

            ia.currency AS currency,

            ia.agent_patient_value AS invoice_amount,
            ia.invoice_total AS full_invoice_amount,

            ia.agent_percentage AS agent_percentage,

            ROUND(
                (
                    (
                        COALESCE(irm.netinv_amount, 0)
                        + COALESCE(irm.diff_amount, 0)
                    )
                    *
                    CASE
                        WHEN ia.invoice_total > 0
                        THEN
                            ia.agent_patient_value
                            / ia.invoice_total
                        ELSE 0
                    END
                ),
                2
            ) AS allocated_received_amount,

            ROUND(
                (
                    (
                        (
                            COALESCE(irm.netinv_amount, 0)
                            + COALESCE(irm.diff_amount, 0)
                        )
                        *
                        CASE
                            WHEN ia.invoice_total > 0
                            THEN
                                ia.agent_patient_value
                                / ia.invoice_total
                            ELSE 0
                        END
                    )
                    * 0.925926
                )
                *
                (
                    COALESCE(
                        ia.agent_percentage,
                        0
                    ) / 100
                ),
                2
            ) AS agency_pay_amount,

            COALESCE(
                (
                    SELECT SUM(apd.paid_now_amount)
                    FROM agent_payment_detail apd
                    INNER JOIN agent_payment_master apm
                        ON apm.payment_no = apd.payment_no
                    WHERE
                        UPPER(TRIM(apd.invoice_number))
                        = UPPER(TRIM(ia.invoice_number))
                        AND UPPER(TRIM(apm.agent_code))
                        = UPPER(TRIM(ia.agent_code))
                        AND apm.payment_status <> 'CANCELLED'
                ),
                0
            ) AS paid_amount,

            ROUND(
                (
                    (
                        (
                            (
                                COALESCE(irm.netinv_amount, 0)
                                + COALESCE(irm.diff_amount, 0)
                            )
                            *
                            CASE
                                WHEN ia.invoice_total > 0
                                THEN
                                    ia.agent_patient_value
                                    / ia.invoice_total
                                ELSE 0
                            END
                        )
                        * 0.925926
                    )
                    *
                    (
                        COALESCE(
                            ia.agent_percentage,
                            0
                        ) / 100
                    )
                )
                -
                COALESCE(
                    (
                        SELECT SUM(apd2.paid_now_amount)
                        FROM agent_payment_detail apd2
                        INNER JOIN agent_payment_master apm2
                            ON apm2.payment_no = apd2.payment_no
                        WHERE
                            UPPER(TRIM(apd2.invoice_number))
                            = UPPER(TRIM(ia.invoice_number))
                            AND UPPER(TRIM(apm2.agent_code))
                            = UPPER(TRIM(ia.agent_code))
                            AND apm2.payment_status <> 'CANCELLED'
                    ),
                    0
                ),
                2
            ) AS outstanding_amount,

            COALESCE(
                (
                    SELECT apm3.utr_reference
                    FROM agent_payment_detail apd3
                    INNER JOIN agent_payment_master apm3
                        ON apm3.payment_no = apd3.payment_no
                    WHERE
                        UPPER(TRIM(apd3.invoice_number))
                        = UPPER(TRIM(ia.invoice_number))
                        AND UPPER(TRIM(apm3.agent_code))
                        = UPPER(TRIM(ia.agent_code))
                        AND apm3.payment_status <> 'CANCELLED'
                    ORDER BY
                        apm3.entered_on DESC,
                        apm3.id DESC
                    LIMIT 1
                ),
                ''
            ) AS utr_reference

        FROM
        (
            SELECT
                im.invoice_number,

                MAX(
                    COALESCE(
                        im.invoice_date,
                        DATE(im.status_date)
                    )
                ) AS invoice_date,

                TRIM(
                    COALESCE(
                        pw.agent_shrtnm,
                        ''
                    )
                ) AS agent_code,

                COALESCE(
                    MAX(
                        NULLIF(
                            TRIM({partner_name_expr}),
                            ''
                        )
                    ),
                    MAX(
                        TRIM(
                            COALESCE(
                                pw.agent_shrtnm,
                                ''
                            )
                        )
                    )
                ) AS agent_name,

                CASE
                    WHEN COUNT(*) = 1
                    THEN MAX(im.patient_name)
                    ELSE CONCAT(
                        COUNT(*),
                        ' Cases'
                    )
                END AS patient_case,

                COUNT(*) AS case_count,

                MAX(
                    COALESCE(
                        im.inv_currency,
                        'INR'
                    )
                ) AS currency,

                SUM(
                    COALESCE(
                        im.totalinv_amount,
                        0
                    )
                ) AS agent_patient_value,

                MAX(
                    COALESCE(
                        im.netinv_amount,
                        0
                    )
                ) AS invoice_total,

                MAX(
                    COALESCE(
                        pr.agent_percentage,
                        0
                    )
                ) AS agent_percentage

            FROM invoice_master im

            LEFT JOIN patient_wrkflw pw
                ON pw.ld_internalno
                 = im.ld_internalno

            LEFT JOIN partner_reg pr
                ON UPPER(TRIM(pr.short_name))
                 = UPPER(TRIM(pw.agent_shrtnm))
               AND UPPER(TRIM(pr.partner_type))
                 = 'AGENCY'

            GROUP BY
                im.invoice_number,
                TRIM(
                    COALESCE(
                        pw.agent_shrtnm,
                        ''
                    )
                )
        ) ia

        INNER JOIN invrecon_master irm
            ON UPPER(TRIM(irm.invoice_number))
             = UPPER(TRIM(ia.invoice_number))

        {where_sql}

        ORDER BY
            ia.invoice_date DESC,
            ia.invoice_number DESC,
            ia.agent_code
    """

    cursor.execute(query, params)
    rows = cursor.fetchall()
    cursor.close()

    # Status is derived after authoritative amounts are loaded.
    for row in rows:
        agency_pay = _to_decimal(
            row.get("agency_pay_amount")
        )
        paid = _to_decimal(
            row.get("paid_amount")
        )

        outstanding = agency_pay - paid

        if outstanding < 0:
            outstanding = Decimal("0.00")

        row["outstanding_amount"] = float(outstanding)

        if outstanding <= 0:
            row["payment_status"] = "PAID"
        elif paid > 0:
            row["payment_status"] = "PARTIAL"
        else:
            row["payment_status"] = "PENDING"

    if status:
        status_upper = status.upper()

        rows = [
            row
            for row in rows
            if (
                row.get("payment_status") or ""
            ).upper() == status_upper
        ]

    return rows

def save_agent_payment(conn, payload, entered_by):
    """
    Save Agent Payment using authoritative database values.

    Browser-supplied agency_pay_amount and already_paid_amount
    are NOT trusted.

    One Agent Payment Master record represents one Agent.
    Detail rows may contain one or more invoices for that same Agent.
    """

    invoices = payload.get("invoices") or []

    if not invoices:
        raise ValueError(
            "Please select at least one invoice."
        )

    payment_date = payload.get("payment_date")
    payment_mode = (
        payload.get("payment_mode")
        or "BANK TRANSFER"
    )
    bank_reference = (
        payload.get("bank_reference")
        or ""
    )
    utr_reference = (
        payload.get("utr_reference")
        or bank_reference
    )
    remarks = payload.get("remarks") or ""

    if not payment_date:
        raise ValueError(
            "Payment Date is required."
        )

    if not utr_reference:
        raise ValueError(
            "UTR Reference is required."
        )

    cursor = conn.cursor(dictionary=True)

    authoritative_items = []
    master_agent_code = None
    master_agent_name = None
    master_currency = None

    try:

        # --------------------------------------------------
        # Validate every requested Invoice + Agent against
        # the database before creating any payment record.
        # --------------------------------------------------
        for inv in invoices:

            invoice_number = (
                inv.get("invoice_no")
                or inv.get("invoice_number")
                or ""
            ).strip()

            requested_agent = (
                inv.get("agent_code")
                or inv.get("agent_shrtnm")
                or ""
            ).strip()

            if not invoice_number:
                raise ValueError(
                    "Invoice Number is required."
                )

            if not requested_agent:
                raise ValueError(
                    f"Agent is missing for invoice: "
                    f"{invoice_number}"
                )

            db_rows = fetch_agent_payment_invoices(
                conn,
                invoice_no=invoice_number,
            )

            authoritative = None

            for row in db_rows:
                db_agent = (
                    row.get("agent_code")
                    or row.get("agent_shrtnm")
                    or ""
                ).strip()

                if (
                    db_agent.upper()
                    == requested_agent.upper()
                ):
                    authoritative = row
                    break

            if authoritative is None:
                raise ValueError(
                    f"Invoice {invoice_number} is not "
                    f"payable for Agent {requested_agent}."
                )

            authoritative_agent = (
                authoritative.get("agent_code")
                or authoritative.get("agent_shrtnm")
                or ""
            ).strip()

            authoritative_agent_name = (
                authoritative.get("agent_name")
                or authoritative_agent
            )

            authoritative_currency = (
                authoritative.get("currency")
                or "INR"
            ).strip() or "INR"

            # One master payment must belong to only one Agent.
            if master_agent_code is None:
                master_agent_code = authoritative_agent
                master_agent_name = (
                    authoritative_agent_name
                )
                master_currency = (
                    authoritative_currency
                )

            elif (
                authoritative_agent.upper()
                != master_agent_code.upper()
            ):
                raise ValueError(
                    "One Agent Payment transaction cannot "
                    "contain invoices belonging to "
                    "different Agents."
                )

            if (
                authoritative_currency.upper()
                != master_currency.upper()
            ):
                raise ValueError(
                    "One Agent Payment transaction cannot "
                    "contain different currencies."
                )

            agency_pay_amount = _to_decimal(
                authoritative.get(
                    "agency_pay_amount"
                )
            )

            already_paid = _to_decimal(
                authoritative.get(
                    "paid_amount"
                )
            )

            invoice_amount = _to_decimal(
                authoritative.get(
                    "invoice_amount"
                )
            )

            paid_now = _to_decimal(
                inv.get("paid_now_amount")
                or inv.get("paid_amount")
                or 0
            )

            outstanding = (
                agency_pay_amount
                - already_paid
            )

            if outstanding < 0:
                outstanding = Decimal("0.00")

            if paid_now <= 0:
                continue

            if agency_pay_amount <= 0:
                raise ValueError(
                    f"Agency Pay Amount is zero or "
                    f"missing for invoice: "
                    f"{invoice_number}"
                )

            if paid_now > outstanding:
                raise ValueError(
                    f"Paid amount cannot be greater "
                    f"than Outstanding Payment for "
                    f"invoice: {invoice_number}"
                )

            balance_amount = (
                outstanding
                - paid_now
            )

            authoritative_items.append({
                "invoice_number":
                    invoice_number,

                "invoice_amount":
                    invoice_amount,

                "agency_pay_amount":
                    agency_pay_amount,

                "already_paid":
                    already_paid,

                "paid_now":
                    paid_now,

                "balance_amount":
                    balance_amount,

                "utr_reference":
                    inv.get("utr_reference")
                    or utr_reference,
            })


        if not authoritative_items:
            raise ValueError(
                "No paid amount entered for "
                "selected invoice records."
            )


        # --------------------------------------------------
        # Payment amount is derived from validated detail
        # rows ? NOT trusted from the browser.
        # --------------------------------------------------
        payment_amount = sum(
            (
                item["paid_now"]
                for item in authoritative_items
            ),
            Decimal("0.00"),
        )

        tds_amount = _to_decimal(
            payload.get("tds_amount")
        )

        if tds_amount < 0:
            raise ValueError(
                "TDS Amount cannot be negative."
            )

        if tds_amount > payment_amount:
            raise ValueError(
                "TDS Amount cannot be greater "
                "than Payment Amount."
            )

        net_paid_amount = (
            payment_amount
            - tds_amount
        )


        # --------------------------------------------------
        # Create payment master only after all validation
        # has successfully completed.
        # --------------------------------------------------
        payment_no = generate_agent_payment_no(
            conn
        )

        cursor.execute("""
            INSERT INTO agent_payment_master (
                payment_no,
                payment_date,
                agent_code,
                agent_name,
                payment_mode,
                payment_amount,
                tds_amount,
                net_paid_amount,
                bank_reference,
                utr_reference,
                payment_status,
                currency_code,
                remarks,
                entered_by,
                entered_on
            )
            VALUES (
                %s, %s, %s, %s, %s,
                %s, %s, %s, %s, %s,
                'POSTED',
                %s, %s, %s, NOW()
            )
        """, (
            payment_no,
            payment_date,
            master_agent_code,
            master_agent_name,
            payment_mode,
            payment_amount,
            tds_amount,
            net_paid_amount,
            bank_reference,
            utr_reference,
            master_currency,
            remarks,
            entered_by,
        ))


        inserted_details = 0

        for item in authoritative_items:

            row_utr = (
                item["utr_reference"]
                or utr_reference
            )

            cursor.execute("""
                INSERT INTO agent_payment_detail (
                    payment_no,
                    invoice_number,
                    invoice_amount,
                    already_paid_amount,
                    payable_amount,
                    paid_now_amount,
                    balance_amount,
                    remarks,
                    created_on
                )
                VALUES (
                    %s, %s, %s, %s,
                    %s, %s, %s, %s,
                    NOW()
                )
            """, (
                payment_no,
                item["invoice_number"],
                item["invoice_amount"],
                item["already_paid"],
                item["agency_pay_amount"],
                item["paid_now"],
                item["balance_amount"],
                (
                    f"UTR: {row_utr} | {remarks}"
                    if row_utr
                    else remarks
                ),
            ))

            inserted_details += 1


        return {
            "payment_no":
                payment_no,

            "inserted_details":
                inserted_details,

            "payment_amount":
                float(payment_amount),

            "net_paid_amount":
                float(net_paid_amount),

            "agent_code":
                master_agent_code,
        }

    finally:
        cursor.close()
