from flask import jsonify, request
from flask_login import login_required

from app.common.db import get_db
from .helpers import (
    fetch_workflow_records_for_status_change,
    build_lead_options_from_rows,
    fetch_workflow_record_row_by_ld_internalno,
    build_lead_view_model,
    build_patient_view_model,
    build_attender_view_model,
    normalize_whatsapp_stub_payload,
    normalize_real_whatsapp_payload,
    insert_channel_message_log,
)


def register_workflow_api_routes(app):
    @app.route("/api/wrkflwstatuschange/lead-records", methods=["GET"])
    @login_required
    def api_wrkflwstatuschange_lead_records():
        conn = None
        try:
            conn = get_db()
            workflow_rows = fetch_workflow_records_for_status_change(conn)
            return jsonify(build_lead_options_from_rows(workflow_rows))
        except Exception as e:
            print("api_wrkflwstatuschange_lead_records error:", e)
            return jsonify([])
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/wrkflwstatuschange/record/<string:ld_internalno>", methods=["GET"])
    @login_required
    def api_wrkflwstatuschange_record(ld_internalno):
        conn = None
        try:
            conn = get_db()
            row = fetch_workflow_record_row_by_ld_internalno(conn, ld_internalno)
            if not row:
                return jsonify({"success": False, "message": "Record not found."}), 404

            return jsonify({
                "success": True,
                "lead": build_lead_view_model(row),
                "patient": build_patient_view_model(row),
                "attender1": build_attender_view_model(row, 1),
                "attender2": build_attender_view_model(row, 2),
                "attender3": build_attender_view_model(row, 3),
            })
        except Exception as e:
            print("api_wrkflwstatuschange_record error:", e)
            return jsonify({"success": False, "message": "Unable to load record."}), 500
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/channel/whatsapp/push", methods=["POST"])
    @login_required
    def api_whatsapp_stub_push():
        conn = None
        try:
            data = request.get_json(silent=True) or {}

            message_text = (data.get("message_text") or "").strip()
            if not message_text:
                return jsonify({
                    "success": False,
                    "message": "message_text is required."
                }), 400

            conn = get_db()
            normalized = normalize_whatsapp_stub_payload(data)
            inserted_id = insert_channel_message_log(conn, normalized)

            return jsonify({
                "success": True,
                "message": "WhatsApp stub message saved successfully.",
                "channel_message_log_id": inserted_id,
                "channel_message_id": normalized["channel_message_id"]
            })
        except Exception as e:
            print("api_whatsapp_stub_push error:", e)
            return jsonify({
                "success": False,
                "message": f"Unable to save WhatsApp stub message: {str(e)}"
            }), 500
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass

    @app.route("/api/channel/whatsapp/webhook", methods=["POST"])
    @login_required
    def api_whatsapp_webhook():
        conn = None
        try:
            payload = request.get_json(silent=True) or {}

            conn = get_db()
            normalized = normalize_real_whatsapp_payload(payload)

            if not normalized["raw_message_text"] and int(normalized["attachment_count"] or 0) == 0:
                return jsonify({
                    "success": False,
                    "message": "No message text or attachment received."
                }), 400

            inserted_id = insert_channel_message_log(conn, normalized)

            return jsonify({
                "success": True,
                "message": "WhatsApp webhook message saved successfully.",
                "channel_message_log_id": inserted_id,
                "channel_message_id": normalized["channel_message_id"]
            })
        except Exception as e:
            print("api_whatsapp_webhook error:", e)
            return jsonify({
                "success": False,
                "message": f"Unable to save WhatsApp webhook message: {str(e)}"
            }), 500
        finally:
            try:
                if conn:
                    conn.close()
            except Exception:
                pass