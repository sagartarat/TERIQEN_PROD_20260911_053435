from flask import jsonify, request
from flask_login import login_required

from app.common.db import get_db
from .helpers import (
    fetch_hospitals,
    fetch_agents,
    fetch_ivacs,
    fetch_hospital_city,
    fetch_hospital_depts,
    check_patient_workflow_duplicate,
)


def register_lead_api_routes(app):
    @app.route("/api/hospitals", methods=["GET"])
    @login_required
    def api_hospitals():
        conn = None
        try:
            conn = get_db()
            return jsonify(fetch_hospitals(conn))
        except Exception as e:
            print("api_hospitals error:", e)
            return jsonify([])
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/agents", methods=["GET"])
    @login_required
    def api_agents():
        conn = None
        try:
            conn = get_db()
            return jsonify(fetch_agents(conn))
        except Exception as e:
            print("api_agents error:", e)
            return jsonify([])
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/ivacs", methods=["GET"])
    @login_required
    def api_ivacs():
        conn = None
        try:
            conn = get_db()
            return jsonify(fetch_ivacs(conn))
        except Exception as e:
            print("api_ivacs error:", e)
            return jsonify([])
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/hospital-city", methods=["GET"])
    @login_required
    def api_hospital_city():
        hospital = (request.args.get("hospital") or "").strip()
        if not hospital:
            return jsonify({"partner_city": ""})

        conn = None
        try:
            conn = get_db()
            city = fetch_hospital_city(conn, hospital)
            return jsonify({"partner_city": city})
        except Exception as e:
            print("api_hospital_city error:", e)
            return jsonify({"partner_city": ""})
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/hospital-depts", methods=["GET"])
    @login_required
    def api_hospital_depts():
        hospital = (request.args.get("hospital") or "").strip()
        if not hospital:
            return jsonify([])

        conn = None
        try:
            conn = get_db()
            return jsonify(fetch_hospital_depts(conn, hospital))
        except Exception as e:
            print("api_hospital_depts error:", e)
            return jsonify([])
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/check-patient-workflow-duplicate", methods=["GET"])
    @login_required
    def api_check_patient_workflow_duplicate():
        passport = (request.args.get("passport") or "").strip()
        if not passport:
            return jsonify({
                "allowed": False,
                "message": "Patient Passport No is required."
            })

        conn = None
        try:
            conn = get_db()
            allowed, message = check_patient_workflow_duplicate(conn, passport)
            return jsonify({
                "allowed": allowed,
                "message": message
            })
        except Exception as e:
            print("api_check_patient_workflow_duplicate error:", e)
            return jsonify({
                "allowed": False,
                "message": "Unable to validate duplicate workflow record."
            }), 500
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass