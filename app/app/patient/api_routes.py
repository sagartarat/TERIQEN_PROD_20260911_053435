from flask import jsonify, request
from flask_login import login_required

from app.common.db import get_db
from .helpers import fetch_patient_docs


def register_patient_api_routes(app):

    @app.route("/api/patient-docs", methods=["GET"])
    @login_required
    def api_patient_docs():
        passport = (request.args.get("passport") or "").strip()

        if not passport:
            return jsonify([])

        conn = None

        try:
            conn = get_db()
            docs = fetch_patient_docs(conn, passport)
            return jsonify(docs)

        except Exception as e:
            print("api_patient_docs error:", e)
            return jsonify([])

        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass