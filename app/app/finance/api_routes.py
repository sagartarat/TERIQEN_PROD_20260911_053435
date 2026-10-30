import mysql.connector

from flask import jsonify, request, render_template
from flask_login import login_required, current_user

from app.common.db import get_db, get_db_connection
from app.common.utils import parse_date
from .invoice_generation import (
    fetch_invoice_candidate_records,
    fetch_invoice_hospital_details,
    insert_invoice_master_rows,
)
from .invoice_printing import (
    fetch_invoice_filter_options,
    fetch_invoice_list,
    fetch_hospital_print_summary,
    mark_invoice_printed,
    fetch_invoice_template_narration_status,
    save_invoice_custom_narration,
)
from .invoice_reconciliation import (
    parse_recon_filter_date,
    validate_recon_date_range,
    load_invoice_reconciliation_workbench,
    insert_invoice_reconciliation,
    fetch_invoice_payment_history,
    fetch_invoice_payment_totals,
    insert_invoice_payment,
)
from .agent_payment_service import (
    fetch_agent_payment_agents,
    fetch_agent_payment_invoices,
    fetch_agent_payment_invoice_lov,
    save_agent_payment,
)


def register_finance_api_routes(app):

    @app.route("/agent-payment", methods=["GET"])
    @login_required
    def agentpayment():
        return render_template("agent_payment.html")

    @app.route("/api/agent-payment/agents", methods=["GET"])
    @login_required
    def api_agent_payment_agents():
        conn = None
        try:
            conn = get_db()
            agents = fetch_agent_payment_agents(conn)

            return jsonify({
                "success": True,
                "agents": agents
            })

        except Exception as e:
            print("api_agent_payment_agents error:", str(e))
            return jsonify({
                "success": False,
                "agents": [],
                "message": str(e)
            }), 500

        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass


    @app.route("/api/agent-payment/invoice-lov", methods=["GET"])
    @login_required
    def api_agent_payment_invoice_lov():
        conn = None
        try:
            conn = get_db()
            invoices = fetch_agent_payment_invoice_lov(conn)

            return jsonify({
                "success": True,
                "invoices": invoices
            })

        except Exception as e:
            print("api_agent_payment_invoice_lov error:", str(e))
            return jsonify({
                "success": False,
                "invoices": [],
                "message": str(e)
            }), 500

        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/agent-payment/invoices", methods=["GET"])
    @login_required
    def api_agent_payment_invoices():
        conn = None
        try:
            agent = (request.args.get("agent") or "").strip()
            invoice_no = (request.args.get("invoice_no") or "").strip()
            from_date = (request.args.get("from_date") or "").strip()
            to_date = (request.args.get("to_date") or "").strip()
            status = (request.args.get("status") or "").strip()
            like = (request.args.get("like") or "").strip()

            conn = get_db()

            rows = fetch_agent_payment_invoices(
                conn=conn,
                agent=agent,
                invoice_no=invoice_no,
                from_date=from_date,
                to_date=to_date,
                status=status,
                like=like
            )

            total_payable = sum(float(r.get("outstanding_amount") or 0) for r in rows)
            paid_amount = sum(float(r.get("paid_amount") or 0) for r in rows)

            return jsonify({
                "success": True,
                "rows": rows,
                "summary": {
                    "total_invoices": len(rows),
                    "total_payable": total_payable,
                    "paid_amount": paid_amount,
                    "pending_amount": total_payable
                }
            })

        except Exception as e:
            print("api_agent_payment_invoices error:", str(e))
            return jsonify({
                "success": False,
                "rows": [],
                "summary": {
                    "total_invoices": 0,
                    "total_payable": 0,
                    "paid_amount": 0,
                    "pending_amount": 0
                },
                "message": str(e)
            }), 500

        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/agent-payment/save", methods=["POST"])
    @login_required
    def api_agent_payment_save():
        conn = None
        try:
            payload = request.get_json(silent=True) or {}

            conn = get_db()

            result = save_agent_payment(
                conn=conn,
                payload=payload,
                entered_by=getattr(current_user, "user_code", "SYSTEM")
            )

            conn.commit()

            return jsonify({
                "success": True,
                "message": f"Agent payment saved successfully. Payment No: {result['payment_no']}",
                "payment_no": result["payment_no"],
                "inserted_details": result["inserted_details"],
                "payment_amount": result["payment_amount"],
                "net_paid_amount": result["net_paid_amount"]
            })

        except Exception as e:
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass

            print("api_agent_payment_save error:", str(e))

            return jsonify({
                "success": False,
                "message": str(e) or "Unable to save agent payment."
            }), 500

        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/invoices/filter-options", methods=["GET"])
    @login_required
    def api_invoice_filter_options():
        conn = None
        try:
            conn = get_db()
            data = fetch_invoice_filter_options(conn)
            return jsonify(data)
        except Exception as e:
            print("api_invoice_filter_options error:", str(e))
            return jsonify({
                "invoice_numbers": [],
                "hospital_names": [],
                "message": str(e)
            }), 500
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/invoices/list", methods=["GET"])
    @login_required
    def api_invoices_list():
        conn = None
        try:
            invoice_number = (request.args.get("invoice_number") or "").strip()
            hospital_name = (request.args.get("hospital_name") or "").strip()
            from_date = (request.args.get("from_date") or "").strip()
            to_date = (request.args.get("to_date") or "").strip()
            print_status = (request.args.get("print_status") or "ALL").strip().upper()

            if from_date and to_date:
                fd = parse_date(from_date)
                td = parse_date(to_date)
                if fd and td and fd > td:
                    return jsonify({
                        "items": [],
                        "summary": {
                            "total_invoices": 0,
                            "printed_invoices": 0,
                            "pending_invoices": 0,
                        },
                        "message": "From Date cannot be greater than To Date."
                    }), 400

            conn = get_db()
            result = fetch_invoice_list(
                conn=conn,
                invoice_number=invoice_number,
                hospital_name=hospital_name,
                from_date=from_date,
                to_date=to_date,
                print_status=print_status,
            )
            return jsonify(result)

        except Exception as e:
            print("api_invoices_list error:", str(e))
            return jsonify({
                "items": [],
                "summary": {
                    "total_invoices": 0,
                    "printed_invoices": 0,
                    "pending_invoices": 0,
                },
                "message": str(e)
            }), 500
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/invoices/template-narration-status", methods=["GET"])
    @login_required
    def api_invoice_template_narration_status():
        conn = None
        try:
            invoice_number = (request.args.get("invoice_number") or "").strip()

            conn = get_db()
            result = fetch_invoice_template_narration_status(
                conn=conn,
                invoice_number=invoice_number,
            )

            return jsonify({
                "success": True,
                **result,
            })

        except Exception as e:
            print("api_invoice_template_narration_status error:", str(e))
            return jsonify({
                "success": False,
                "message": str(e) or "Unable to read invoice template narration status.",
                "narration_enabled": False,
                "custom_narration": "",
                "narration_locked": False,
            }), 500
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/invoices/save-custom-narration", methods=["POST"])
    @login_required
    def api_invoice_save_custom_narration():
        conn = None
        try:
            payload = request.get_json(silent=True) or {}
            invoice_number = (payload.get("invoice_number") or "").strip()
            custom_narration = payload.get("custom_narration") or ""

            conn = get_db()
            result = save_invoice_custom_narration(
                conn=conn,
                invoice_number=invoice_number,
                custom_narration=custom_narration,
                updated_by=getattr(current_user, "user_code", "SYSTEM"),
            )
            conn.commit()

            return jsonify({
                "success": True,
                "message": "Narration saved successfully.",
                **result,
            })

        except Exception as e:
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass

            print("api_invoice_save_custom_narration error:", str(e))
            return jsonify({
                "success": False,
                "message": str(e) or "Unable to save narration."
            }), 500
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/invoices/hospital-print-summary", methods=["GET"])
    @login_required
    def api_invoices_hospital_print_summary():
        conn = None
        try:
            invoice_number = (request.args.get("invoice_number") or "").strip()
            hospital_name = (request.args.get("hospital_name") or "").strip()
            from_date = (request.args.get("from_date") or "").strip()
            to_date = (request.args.get("to_date") or "").strip()
            print_status = (request.args.get("print_status") or "ALL").strip().upper()

            conn = get_db()
            rows = fetch_hospital_print_summary(
                conn=conn,
                invoice_number=invoice_number,
                hospital_name=hospital_name,
                from_date=from_date,
                to_date=to_date,
                print_status=print_status,
            )
            return jsonify({"items": rows})

        except Exception as e:
            print("api_invoices_hospital_print_summary error:", str(e))
            return jsonify({
                "items": [],
                "message": str(e)
            }), 500
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/invoice/mark-printed/<string:invoice_number>", methods=["POST"])
    @login_required
    def api_invoice_mark_printed(invoice_number):
        conn = None
        try:
            conn = get_db()
            result = mark_invoice_printed(conn, invoice_number, current_user.user_code)
            conn.commit()

            return jsonify({
                "success": True,
                "message": f"Invoice {result['invoice_number']} marked as PRINTED.",
                "invoice_number": result["invoice_number"],
                "printed_at": result["printed_at"],
                "printed_by": result["printed_by"],
                "updated_count": result["updated_count"],
            })

        except Exception as e:
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass

            print("api_invoice_mark_printed error:", str(e))
            return jsonify({
                "success": False,
                "message": str(e) or "Unable to update print status."
            }), 500
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/invoicegeneration/search", methods=["GET"])
    @login_required
    def api_invoicegeneration_search():
        conn = None
        try:
            hospital_short_name = (request.args.get("hospital_short_name") or "").strip()
            from_date = (request.args.get("from_date") or "").strip()
            to_date = (request.args.get("to_date") or "").strip()
            search_text = (request.args.get("search_text") or "").strip()

            if not hospital_short_name:
                return jsonify({
                    "success": True,
                    "message": "Please select Hospital Short Name.",
                    "records": [],
                    "hospital_details": {
                        "hospital_name": "",
                        "hospital_address": "",
                        "hospital_gst": "",
                        "hospital_pan": "",
                        "hospital_country": "",
                        "hospital_state": "",
                        "placeof_supply": "",
                        "hospital_city": "",
                        "hospital_pincode": "",
                        "inv_currency": "INR",
                    },
                    "gst_lov": ["18%"],
                    "sac_code": "999723",
                })

            conn = get_db()
            records = fetch_invoice_candidate_records(
                conn=conn,
                hospital_short_name=hospital_short_name,
                from_date=from_date,
                to_date=to_date,
                search_text=search_text,
            )

            hospital_details = fetch_invoice_hospital_details(conn, hospital_short_name)

            return jsonify({
                "success": True,
                "records": records,
                "hospital_details": hospital_details,
                "gst_lov": ["18%"],
                "sac_code": "999723",
            })
        except Exception as e:
            print("api_invoicegeneration_search error:", e)
            return jsonify({
                "success": False,
                "message": "Unable to load invoice candidate records.",
                "records": [],
                "hospital_details": {
                    "hospital_name": "",
                    "hospital_address": "",
                    "hospital_gst": "",
                    "hospital_pan": "",
                    "hospital_country": "",
                    "hospital_state": "",
                    "placeof_supply": "",
                    "hospital_city": "",
                    "hospital_pincode": "",
                    "inv_currency": "INR",
                },
                "gst_lov": ["18%"],
                "sac_code": "999723",
            }), 500
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/invoicegeneration/generate", methods=["POST"])
    @login_required
    def api_invoicegeneration_generate():
        conn = None
        try:
            payload = request.get_json(silent=True) or {}

            conn = get_db()
            result = insert_invoice_master_rows(conn, payload, current_user.user_code)
            conn.commit()

            return jsonify({
                "success": True,
                "message": (
                    f"Invoice generated successfully. "
                    f"Invoice No: {result['invoice_number']} | "
                    f"Invoice rows created: {result['inserted_count']}"
                ),
                "invoice_number": result["invoice_number"],
                "inserted_count": result["inserted_count"],
                "workflow_updated_count": result.get("workflow_updated_count", 0),
                "invoice_date": result["invoice_date"],
                "rows": result["rows"],
            })
        except mysql.connector.Error as e:
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass
            print("api_invoicegeneration_generate DB error:", e)
            return jsonify({
                "success": False,
                "message": f"Invoice generation failed: {str(e)}"
            }), 500
        except Exception as e:
            try:
                if conn:
                    conn.rollback()
            except Exception:
                pass
            print("api_invoicegeneration_generate error:", e)
            return jsonify({
                "success": False,
                "message": str(e) or "Invoice generation failed."
            }), 400
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass


    @app.route(
        "/api/invoice-reconciliation/payment-history/<string:invoice_number>",
        methods=["GET"]
    )
    @login_required
    def api_invoice_reconciliation_payment_history(invoice_number):
        conn = None
        cursor = None

        try:
            invoice_number = (invoice_number or "").strip()

            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            cursor.execute("""
                SELECT
                    MAX(invoice_number) AS invoice_number,
                    MAX(hospital_name) AS hospital_name,
                    MAX(netinv_amount) AS invoice_amount,
                    MAX(invoice_date) AS invoice_date,
                    MAX(wrkflw_status) AS workflow_status
                FROM invoice_master
                WHERE invoice_number = %s
            """, (invoice_number,))

            invoice = cursor.fetchone() or {}

            if not invoice.get("invoice_number"):
                return jsonify({
                    "success": False,
                    "message": "Invoice not found."
                }), 404

            history = fetch_invoice_payment_history(
                conn,
                invoice_number
            )

            totals = fetch_invoice_payment_totals(
                conn,
                invoice_number
            )

            invoice_amount = float(
                invoice.get("invoice_amount") or 0
            )

            total_received = float(
                totals.get("total_received") or 0
            )

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

            payments = []

            for row in history:
                payments.append({
                    "id": row.get("id"),
                    "payment_seq": row.get("payment_seq"),
                    "received_amount": float(
                        row.get("received_amount") or 0
                    ),
                    "payment_date": (
                        row["payment_date"].isoformat()
                        if row.get("payment_date")
                        else None
                    ),
                    "utr_ref_no": row.get("utrref_no"),
                    "remarks": row.get("payment_remarks"),
                    "payment_status": row.get("payment_status"),
                    "entered_by": row.get("entered_by"),
                    "entered_on": (
                        row["entered_on"].isoformat()
                        if row.get("entered_on")
                        else None
                    ),
                    "cancelled_by": row.get("cancelled_by"),
                    "cancelled_on": (
                        row["cancelled_on"].isoformat()
                        if row.get("cancelled_on")
                        else None
                    ),
                })

            return jsonify({
                "success": True,

                "invoice": {
                    "invoice_number": invoice_number,
                    "hospital_name": invoice.get("hospital_name"),
                    "invoice_amount": invoice_amount,

                    "invoice_date": (
                        invoice["invoice_date"].isoformat()
                        if invoice.get("invoice_date")
                        else None
                    ),

                    "workflow_status": invoice.get(
                        "workflow_status"
                    ),

                    "total_received": total_received,

                    "outstanding_amount": outstanding_amount,

                    "payment_count": int(
                        totals.get("payment_count") or 0
                    ),

                    "last_payment_date": (
                        totals["last_payment_date"].isoformat()
                        if totals.get("last_payment_date")
                        else None
                    ),

                    "payment_status": payment_status,
                },

                "payments": payments,
            })

        except Exception as e:
            print(
                "Payment history API error:",
                str(e)
            )

            return jsonify({
                "success": False,
                "message": str(e)
            }), 500

        finally:
            if cursor:
                cursor.close()

            if conn:
                conn.close()


    @app.route(
        "/api/invoice-reconciliation/payment",
        methods=["POST"]
    )
    @login_required
    def api_invoice_reconciliation_payment():
        conn = None

        try:
            data = request.get_json(
                silent=True
            ) or {}

            conn = get_db_connection()

            result = insert_invoice_payment(
                conn=conn,
                data=data,
                entered_by=(
                    getattr(
                        current_user,
                        "user_code",
                        None
                    )
                    or "SYSTEM"
                ),
            )

            conn.commit()

            last_payment_date = result.get(
                "last_payment_date"
            )

            return jsonify({
                "success": True,

                "message": (
                    "Hospital payment registered successfully."
                ),

                "payment": {
                    "payment_id": result["payment_id"],
                    "payment_seq": result["payment_seq"],

                    "invoice_number": result[
                        "invoice_number"
                    ],

                    "hospital_name": result.get(
                        "hospital_name"
                    ),

                    "invoice_amount": float(
                        result.get("invoice_amount") or 0
                    ),

                    "received_now": float(
                        result.get("received_now") or 0
                    ),

                    "total_received": float(
                        result.get("total_received") or 0
                    ),

                    "outstanding_amount": float(
                        result.get("outstanding_amount") or 0
                    ),

                    "payment_count": int(
                        result.get("payment_count") or 0
                    ),

                    "last_payment_date": (
                        last_payment_date.isoformat()
                        if last_payment_date
                        else None
                    ),

                    "payment_status": result.get(
                        "payment_status"
                    ),
                }
            })

        except ValueError as e:
            if conn:
                conn.rollback()

            return jsonify({
                "success": False,
                "message": str(e)
            }), 400

        except Exception as e:
            if conn:
                conn.rollback()

            print(
                "Register payment API error:",
                str(e)
            )

            return jsonify({
                "success": False,
                "message": str(e)
            }), 500

        finally:
            if conn:
                conn.close()


    @app.route("/api/invoice-reconciliation/save", methods=["POST"])
    @login_required
    def save_invoice_reconciliation():
        conn = None
        cursor = None

        try:
            data = request.get_json(silent=True) or {}

            invoice_number = (
                data.get("invoice_number") or ""
            ).strip()

            if not invoice_number:
                return jsonify({
                    "success": False,
                    "message": "Invoice Number is required."
                }), 400

            from_date_str = (
                data.get("from_date") or ""
            ).strip()

            to_date_str = (
                data.get("to_date") or ""
            ).strip()

            from_date = (
                parse_recon_filter_date(from_date_str)
                if from_date_str
                else None
            )

            to_date = (
                parse_recon_filter_date(to_date_str)
                if to_date_str
                else None
            )

            if from_date_str and to_date_str:
                (
                    is_valid_range,
                    validation_message
                ) = validate_recon_date_range(
                    from_date,
                    to_date
                )

                if not is_valid_range:
                    return jsonify({
                        "success": False,
                        "message": validation_message
                    }), 400
            else:
                from_date = None
                to_date = None

            conn = get_db_connection()
            cursor = conn.cursor(dictionary=True)

            # -------------------------------------------------
            # 1. Read invoice values from DB.
            #    Never trust invoice amount from the browser.
            # -------------------------------------------------
            cursor.execute("""
                SELECT
                    COUNT(*) AS row_count,
                    MAX(invoice_date) AS invoice_date,
                    MAX(hospital_name) AS hospital_name,
                    MAX(netinv_amount) AS invoice_amount,
                    MAX(
                        CASE
                            WHEN UPPER(
                                TRIM(
                                    COALESCE(
                                        print_status,
                                        ''
                                    )
                                )
                            ) = 'PRINTED'
                            THEN 1
                            ELSE 0
                        END
                    ) AS is_printed
                FROM invoice_master
                WHERE invoice_number = %s
            """, (invoice_number,))

            invoice_row = cursor.fetchone() or {}

            if int(invoice_row.get("row_count") or 0) == 0:
                return jsonify({
                    "success": False,
                    "message": "Invoice not found."
                }), 404

            if not int(invoice_row.get("is_printed") or 0):
                return jsonify({
                    "success": False,
                    "message": (
                        "Invoice must be printed before "
                        "final reconciliation."
                    )
                }), 400

            invoice_amount = float(
                invoice_row.get("invoice_amount") or 0
            )

            if invoice_amount <= 0:
                return jsonify({
                    "success": False,
                    "message": (
                        "Invoice Amount could not be determined."
                    )
                }), 400

            # -------------------------------------------------
            # 2. Prevent duplicate final reconciliation.
            # -------------------------------------------------
            cursor.execute("""
                SELECT recon_status
                FROM invrecon_master
                WHERE invoice_number = %s
                LIMIT 1
            """, (invoice_number,))

            existing_recon = cursor.fetchone()

            if (
                existing_recon
                and (
                    existing_recon.get("recon_status")
                    in ("MATCHED", "PARTIAL MATCH")
                )
            ):
                return jsonify({
                    "success": False,
                    "message": (
                        "This invoice is already finally "
                        "reconciled."
                    )
                }), 400

            # -------------------------------------------------
            # 3. Derive cumulative hospital receipts directly
            #    from invrecon_payment_detail.
            # -------------------------------------------------
            cursor.execute("""
                SELECT
                    COALESCE(
                        SUM(received_amount),
                        0
                    ) AS total_received,
                    COUNT(*) AS payment_count,
                    MAX(payment_date) AS last_payment_date
                FROM invrecon_payment_detail
                WHERE invoice_number = %s
                  AND payment_status = 'POSTED'
            """, (invoice_number,))

            payment_totals = cursor.fetchone() or {}

            total_received = float(
                payment_totals.get(
                    "total_received"
                ) or 0
            )

            payment_count = int(
                payment_totals.get(
                    "payment_count"
                ) or 0
            )

            if payment_count <= 0 or total_received <= 0:
                return jsonify({
                    "success": False,
                    "message": (
                        "No posted hospital payment exists "
                        "for this invoice."
                    )
                }), 400

            cursor.execute("""
                SELECT
                    payment_date,
                    utrref_no
                FROM invrecon_payment_detail
                WHERE invoice_number = %s
                  AND payment_status = 'POSTED'
                ORDER BY
                    payment_date DESC,
                    payment_seq DESC,
                    id DESC
                LIMIT 1
            """, (invoice_number,))

            last_payment = cursor.fetchone() or {}

            difference = (
                total_received - invoice_amount
            )

            difference_abs = abs(difference)

            # -------------------------------------------------
            # 4. Apply existing TERIQEN tolerance rules.
            # -------------------------------------------------
            cursor.execute("""
                SELECT
                    rule_name,
                    partner_tolerance_applicable,
                    partner_tolerance_amount,
                    global_tolerance_applicable,
                    global_tolerance_amount
                FROM invoice_reconciliation_rules
                WHERE rule_status = 'Active'
                LIMIT 1
            """)

            rule = cursor.fetchone()
            rule_satisfied = False

            cursor.execute("""
                SELECT
                    COALESCE(
                        SUM(ABS(diff_amount)),
                        0
                    ) AS previous_cumulative_difference
                FROM invrecon_master
                WHERE recon_status
                    IN ('MATCHED', 'PARTIAL MATCH')
            """)

            cumulative_row = cursor.fetchone() or {}

            previous_cumulative_difference = float(
                cumulative_row.get(
                    "previous_cumulative_difference"
                ) or 0
            )

            current_cumulative_difference = (
                previous_cumulative_difference
                + difference_abs
            )

            if rule:
                rule_name = (
                    rule.get("rule_name") or ""
                ).strip()

                partner_tol_app = rule.get(
                    "partner_tolerance_applicable"
                )

                partner_tol_amt = float(
                    rule.get(
                        "partner_tolerance_amount"
                    ) or 0
                )

                global_tol_app = rule.get(
                    "global_tolerance_applicable"
                )

                global_tol_amt = float(
                    rule.get(
                        "global_tolerance_amount"
                    ) or 0
                )

                if rule_name == "Cumulative":
                    partner_ok = True
                    global_ok = True

                    if partner_tol_app == "Yes":
                        partner_ok = (
                            difference_abs
                            <= partner_tol_amt
                        )

                    if global_tol_app == "Yes":
                        global_ok = (
                            current_cumulative_difference
                            <= global_tol_amt
                        )

                    if partner_ok and global_ok:
                        rule_satisfied = True

                elif rule_name == "Partner":
                    if (
                        partner_tol_app == "Yes"
                        and difference_abs
                        <= partner_tol_amt
                    ):
                        rule_satisfied = True

                elif rule_name == "Global":
                    if (
                        global_tol_app == "Yes"
                        and current_cumulative_difference
                        <= global_tol_amt
                    ):
                        rule_satisfied = True

            # Exact settlement always matches.
            if difference_abs == 0:
                rule_satisfied = True

            if not rule_satisfied:
                outstanding_amount = (
                    invoice_amount - total_received
                )

                return jsonify({
                    "success": False,
                    "message": (
                        "Invoice cannot be finally reconciled. "
                        "The remaining difference exceeds the "
                        "configured tolerance. "
                        f"Invoice Amount: {invoice_amount:.2f}, "
                        f"Total Received: {total_received:.2f}, "
                        f"Outstanding: {outstanding_amount:.2f}, "
                        f"Difference: {difference_abs:.2f}"
                    )
                }), 400

            # -------------------------------------------------
            # 5. Determine final reconciliation status.
            #
            # DB compatibility:
            # MATCHED       = exact settlement
            # PARTIAL MATCH = accepted tolerance settlement
            # UI will display PARTIAL MATCH as TOLERANCE MATCH.
            # -------------------------------------------------
            if difference_abs == 0:
                recon_status = "MATCHED"
            else:
                recon_status = "PARTIAL MATCH"

            last_utr = (
                last_payment.get("utrref_no") or ""
            ).strip()

            # invrecon_master.utrref_no is legacy VARCHAR(20).
            # Complete UTR history remains in payment_detail.
            if payment_count > 1:
                master_utr = "MULTIPLE"
            elif len(last_utr) <= 20:
                master_utr = last_utr
            else:
                master_utr = "SEE PAYMENT DETAIL"

            remarks = (
                data.get("remarks") or ""
            ).strip()

            if not remarks:
                remarks = (
                    f"Final reconciliation; "
                    f"{payment_count} payment(s)"
                )

            if len(remarks) > 50:
                return jsonify({
                    "success": False,
                    "message": (
                        "Final reconciliation remarks "
                        "cannot exceed 50 characters."
                    )
                }), 400

            recon_data = {
                "invoice_number": invoice_number,
                "invoice_date": invoice_row.get(
                    "invoice_date"
                ),
                "hospital_name": invoice_row.get(
                    "hospital_name"
                ),
                "invoice_amount": invoice_amount,
                "received_amount": total_received,
                "difference": difference,
                "utr_ref_no": master_utr,
                "payment_date": last_payment.get(
                    "payment_date"
                ),
                "recon_status": recon_status,
                "workflow_status": "RECONCILED",
                "remarks": remarks,
            }

            insert_invoice_reconciliation(
                conn,
                recon_data
            )

            cursor.execute("""
                UPDATE invoice_master
                SET wrkflw_status = 'RECONCILED'
                WHERE invoice_number = %s
            """, (invoice_number,))

            conn.commit()

            refreshed = (
                load_invoice_reconciliation_workbench(
                    cursor=cursor,
                    from_date=from_date,
                    to_date=to_date
                )
            )

            outstanding_amount = (
                invoice_amount - total_received
            )

            return jsonify({
                "success": True,
                "message": (
                    "Invoice final reconciliation "
                    "completed successfully."
                ),
                "reconciliation": {
                    "invoice_number": invoice_number,
                    "invoice_amount": invoice_amount,
                    "total_received": total_received,
                    "outstanding_amount": (
                        outstanding_amount
                    ),
                    "difference": difference,
                    "payment_count": payment_count,
                    "recon_status": recon_status,
                    "display_recon_status": (
                        "MATCHED"
                        if recon_status == "MATCHED"
                        else "TOLERANCE MATCH"
                    ),
                    "workflow_status": "RECONCILED",
                },
                "summary_data": {
                    "total_invoices": refreshed[
                        "total_invoices"
                    ],
                    "matched_count": refreshed[
                        "matched"
                    ],
                    "partial_count": refreshed[
                        "partial_matches"
                    ],
                    "pending_count": refreshed[
                        "pending"
                    ],
                }
            })

        except ValueError as e:
            if conn:
                conn.rollback()

            return jsonify({
                "success": False,
                "message": str(e)
            }), 400

        except Exception as e:
            if conn:
                conn.rollback()

            print(
                "Invoice reconciliation save error:",
                e
            )

            return jsonify({
                "success": False,
                "message": str(e)
            }), 500

        finally:
            if cursor:
                cursor.close()

            if conn:
                conn.close()
