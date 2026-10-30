from flask import render_template, request, jsonify
from flask_login import login_required, current_user

from app.system_control.email_templates.services import (
    delete_email_template_record,
    get_email_template_builder_context,
    save_email_template_record,
)


def register_email_template_routes(app):
    if "system_control_email_template_builder" not in app.view_functions:
        @app.route("/email-template-builder", endpoint="system_control_email_template_builder")
        @login_required
        def email_template_builder():
            context = get_email_template_builder_context()
            return render_template(
                "email_template_builder.html",
                user=current_user,
                **context
            )

    if "system_control_save_email_template" not in app.view_functions:
        @app.route("/api/email-template-builder/save", methods=["POST"], endpoint="system_control_save_email_template")
        @login_required
        def save_email_template():
            data = request.get_json(silent=True) or request.form.to_dict() or {}
            result, status_code = save_email_template_record(data)
            return jsonify(result), status_code

    # EMAIL_TEMPLATE_DELETE_V1:
    # Explicit server-side delete endpoint for saved email templates.
    if "system_control_delete_email_template" not in app.view_functions:
        @app.route("/api/email-template-builder/delete", methods=["POST"], endpoint="system_control_delete_email_template")
        @login_required
        def delete_email_template():
            data = request.get_json(silent=True) or request.form.to_dict() or {}
            result, status_code = delete_email_template_record(data)
            return jsonify(result), status_code