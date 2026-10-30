import mysql.connector

from flask import render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.common.db import get_db
from .helpers import (
    fetch_patients_for_lead,
    fetch_hospitals,
    fetch_agents,
    fetch_ivacs,
    fetch_lead_records_for_generation,
    insert_patient_workflow,
    save_lead_documents,
)


def register_lead_routes(app):

    @app.route("/lead-generation", methods=["GET", "POST"])
    @login_required
    def lead_generation():

        conn = None

        try:
            conn = get_db()

            if request.method == "POST":

                try:
                    result = insert_patient_workflow(conn, current_user.user_code)

                    lead_doc_count = save_lead_documents(conn, result["patpassport_no"])

                    conn.commit()

                    extra_msg = f" | Docs saved: {lead_doc_count}" if lead_doc_count else ""
                    vil_msg = f" | VIL No: {result['vil_internalno']}" if result.get("vil_internalno") else ""

                    flash(
                        f"Lead saved successfully. Status: {result['wrkflw_status']} | "
                        f"LD No: {result['ld_internalno']}{vil_msg} | "
                        f"Entered By: {result['entered_by']} | "
                        f"Entered Date: {result['entered_date']}{extra_msg}",
                        "success"
                    )

                    return redirect(url_for("lead_generation"))

                except mysql.connector.Error as e:

                    try:
                        conn.rollback()
                    except Exception:
                        pass

                    print("lead_generation DB insert error:", e)
                    flash(f"Lead save failed: {str(e)}", "error")

                except Exception as e:

                    try:
                        conn.rollback()
                    except Exception:
                        pass

                    print("lead_generation insert error:", e)
                    flash(f"Lead save failed: {str(e)}", "error")

            patients = fetch_patients_for_lead(conn)
            hospitals = fetch_hospitals(conn)
            agents = fetch_agents(conn)
            ivacs = fetch_ivacs(conn)
            lead_records = fetch_lead_records_for_generation(conn)

            return render_template(
                "leadgeneration.html",
                user=current_user,
                patients=patients,
                hospitals=hospitals,
                agents=agents,
                ivacs=ivacs,
                lead_records=lead_records,
                doctor_hint="Please enter multiple Doctors name with comma separated",
            )

        except Exception as e:

            print("lead_generation error:", e)
            flash(f"Lead Generation Error: {str(e)}", "error")

            return redirect(url_for("dashboard"))

        finally:

            try:
                if conn:
                    conn.close()
            except Exception:
                pass