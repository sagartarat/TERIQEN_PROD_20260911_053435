from __future__ import annotations

from flask import abort, redirect, request, url_for
from flask_login import current_user

from app.common.db import get_db_connection


_ACTION_COLUMNS = {
    "view": "can_view",
    "add": "can_add",
    "edit": "can_edit",
    "delete": "can_delete",
}


def _current_user_code() -> str:
    if not current_user or not current_user.is_authenticated:
        return ""
    return str(
        getattr(current_user, "user_code", None)
        or getattr(current_user, "id", None)
        or ""
    ).strip()


def has_screen_permission(screen_code: str, action: str = "view") -> bool:
    """Server-side RBAC check against the database.

    Admin users receive full access. Non-admin users must have an active
    user_screen_access row for the requested screen/action. This deliberately
    does not rely only on sidebar visibility or the session cache.
    """
    user_code = _current_user_code()
    screen_code = str(screen_code or "").strip().upper()
    action = str(action or "view").strip().lower()

    if not user_code or not screen_code:
        return False

    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        if not conn:
            return False
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT user_type, user_status
            FROM user_master
            WHERE user_code = %s
            LIMIT 1
            """,
            (user_code,),
        )
        user = cursor.fetchone() or {}

        if str(user.get("user_status") or "").strip().upper() != "ACTIVE":
            return False

        if (
            str(user.get("user_type") or "").strip().upper() == "ADMIN"
            and screen_code != "HOSPITAL_PARTNER_MAPPING"
        ):
            return True

        cursor.execute(
            """
            SELECT can_view, can_add, can_edit, can_delete
            FROM user_screen_access
            WHERE user_code = %s
              AND screen_code = %s
              AND COALESCE(is_active, 'Y') = 'Y'
            LIMIT 1
            """,
            (user_code, screen_code),
        )
        permission = cursor.fetchone() or {}

        if action == "write":
            return (
                str(permission.get("can_add") or "N").upper() == "Y"
                or str(permission.get("can_edit") or "N").upper() == "Y"
            )

        column = _ACTION_COLUMNS.get(action, "can_view")
        return str(permission.get(column) or "N").upper() == "Y"

    except Exception:
        # Fail closed. The application logger will capture the 403 path.
        return False
    finally:
        if cursor:
            try:
                cursor.close()
            except Exception:
                pass
        if conn:
            try:
                conn.close()
            except Exception:
                pass


def enforce_screen_permission(screen_code: str, action: str = "view"):
    if not current_user or not current_user.is_authenticated:
        return redirect(url_for("login", next=request.url))
    if not has_screen_permission(screen_code, action):
        abort(403)
    return None


# Critical/sensitive endpoints that must be protected independently of UI/menu
# visibility. Routes can be added here without changing their business logic.
_ENDPOINT_RULES = {
    # Reports
    "reports_operational_page": ("OPERATIONAL_REPORTS", "view"),
    "api_queue_ageing_report": ("OPERATIONAL_REPORTS", "view"),
    "api_queue_ageing_export": ("OPERATIONAL_REPORTS", "view"),
    "api_queue_ageing_export_excel": ("OPERATIONAL_REPORTS", "view"),
    "api_queue_ageing_export_pdf": ("OPERATIONAL_REPORTS", "view"),
    "reports_invoice_management_page": ("INVOICE_MANAGEMENT_REPORTS", "view"),
    "api_invoice_management_dashboard": ("INVOICE_MANAGEMENT_REPORTS", "view"),
    "api_invoice_management_rows": ("INVOICE_MANAGEMENT_REPORTS", "view"),
    "api_invoice_management_export": ("INVOICE_MANAGEMENT_REPORTS", "view"),
    "api_invoice_management_export_excel": ("INVOICE_MANAGEMENT_REPORTS", "view"),
    "api_invoice_management_export_pdf": ("INVOICE_MANAGEMENT_REPORTS", "view"),
    "reports_agent_payment_page": ("AGENT_PAYMENT_REPORTS", "view"),
    "agent_payment_reports": ("AGENT_PAYMENT_REPORTS", "view"),
    "api_agent_payment_report_dashboard": ("AGENT_PAYMENT_REPORTS", "view"),
    "api_agent_payment_report_rows": ("AGENT_PAYMENT_REPORTS", "view"),
    "api_agent_payment_report_export": ("AGENT_PAYMENT_REPORTS", "view"),
    "api_agent_payment_report_export_excel": ("AGENT_PAYMENT_REPORTS", "view"),
    "api_agent_payment_report_export_pdf": ("AGENT_PAYMENT_REPORTS", "view"),
    "reports_sla_page": ("SLA_REPORTS", "view"),
    "api_sla_report_dashboard": ("SLA_REPORTS", "view"),
    "api_sla_report_rows": ("SLA_REPORTS", "view"),
    "api_sla_report_export": ("SLA_REPORTS", "view"),
    "api_sla_report_export_excel": ("SLA_REPORTS", "view"),
    "api_sla_report_export_pdf": ("SLA_REPORTS", "view"),

    # Sensitive System Control
    "operational_reports": ("OPERATIONAL_REPORTS", "view"),
    "invoice_management_reports": ("INVOICE_MANAGEMENT_REPORTS", "view"),
    "sla_reports": ("SLA_REPORTS", "view"),
    "user_management": ("USER_MANAGEMENT", "write"),
    "user_screen_access": ("USER_SCREEN_ACCESS", "view"),
    "api_save_user_screen_access": ("USER_SCREEN_ACCESS", "edit"),
    "api_get_user_screen_access": ("USER_SCREEN_ACCESS", "view"),
    "system_control_email_template_builder": ("EMAIL_TEMPLATE", "view"),
    "system_control_save_email_template": ("EMAIL_TEMPLATE", "edit"),
    "system_control_delete_email_template": ("EMAIL_TEMPLATE", "delete"),
    "sla_rule_builder": ("SLA_RULE_BUILDER", "write"),
    "round_robin_assignment": ("ROUND_ROBIN_ASSIGNMENT", "write"),
    "invoice_template_builder": ("INVOICE_TEMPLATE_BUILDER", "view"),
    "invoice_template_builder_save": ("INVOICE_TEMPLATE_BUILDER", "edit"),
    "invoice_template_builder_preview": ("INVOICE_TEMPLATE_BUILDER", "view"),
    "hospital_department_mapping": ("HOSPITAL_DEPARTMENT_MAPPING", "write"),
    "hospital_department_mapping_save": ("HOSPITAL_DEPARTMENT_MAPPING", "edit"),

    # Patient PII / documents / deletion
    "patient_creation": ("PATIENT_CREATION", "write"),
    "patient_view": ("PATIENT_VIEW_MODIFY", "view"),
    "patient_doc_open": ("PATIENT_VIEW_MODIFY", "view"),
    "patient_delete": ("PATIENT_VIEW_MODIFY", "delete"),
    "api_patient_docs": ("PATIENT_VIEW_MODIFY", "view"),

    # Partner PII / documents
    "partner_view": ("PARTNER_VIEW_MODIFY", "view"),
    "partner_document_view": ("PARTNER_VIEW_MODIFY", "view"),
    "partner_document_download": ("PARTNER_VIEW_MODIFY", "view"),
    "partner_document_delete": ("PARTNER_VIEW_MODIFY", "delete"),
    "partner_delete": ("PARTNER_VIEW_MODIFY", "delete"),

    # Lead / workflow and uploaded bulk data
    "api_hospitals": ("LEAD_GENERATION", "view"),
    "api_agents": ("LEAD_GENERATION", "view"),
    "api_ivacs": ("LEAD_GENERATION", "view"),
    "api_hospital_city": ("LEAD_GENERATION", "view"),
    "api_hospital_depts": ("LEAD_GENERATION", "view"),
    "api_check_patient_workflow_duplicate": ("LEAD_GENERATION", "view"),
    "api_wrkflwstatuschange_lead_records": ("WORKFLOW_STATUS_CHANGE", "view"),
    "api_wrkflwstatuschange_record": ("WORKFLOW_STATUS_CHANGE", "view"),
    "api_whatsapp_stub_push": ("WHATSAPP_STUB", "edit"),
    "api_whatsapp_webhook": ("WHATSAPP_STUB", "edit"),
    "view_vil_doc": ("WORKFLOW_STATUS_CHANGE", "view"),
    "file_upload_status": ("FILE_UPLOAD_STATUS", "view"),
    "view_uploaded_file": ("FILE_UPLOAD_STATUS", "view"),
    "download_uploaded_file": ("FILE_UPLOAD_STATUS", "view"),

    # Queue and email communication
    "operation_queue": ("OPERATION_QUEUE", "view"),
    "operation_queue_hospital_email": ("OPERATION_QUEUE", "view"),
    "operation_queue_email_communication": ("OPERATION_QUEUE", "view"),
    "email_communication_page": ("OPERATION_QUEUE", "view"),
    "operation_queue_save": ("OPERATION_QUEUE", "edit"),
    "operation_queue_resolve": ("OPERATION_QUEUE", "edit"),
    "operation_queue_save_selected": ("OPERATION_QUEUE", "edit"),
    "operation_queue_resolve_selected": ("OPERATION_QUEUE", "edit"),
    "operation_queue_apply_email_template": ("OPERATION_QUEUE", "edit"),
    "start_operation_queue_email": ("OPERATION_QUEUE", "edit"),
    "operation_queue_send_email": ("OPERATION_QUEUE", "edit"),
    "manual_review": ("MANUAL_REVIEW", "view"),
    "manual_review_save_review": ("MANUAL_REVIEW", "edit"),
    "mark_manual_review_junk": ("MANUAL_REVIEW", "edit"),

    # Finance
    "agentpayment": ("AGENT_PAYMENT", "view"),
    "api_agent_payment_agents": ("AGENT_PAYMENT", "view"),
    "api_agent_payment_invoice_lov": ("AGENT_PAYMENT", "view"),
    "api_agent_payment_invoices": ("AGENT_PAYMENT", "view"),
    "api_agent_payment_save": ("AGENT_PAYMENT", "edit"),
    "invoicegeneration": ("INVOICE_GENERATION", "view"),
    "api_invoicegeneration_search": ("INVOICE_GENERATION", "view"),
    "api_invoicegeneration_generate": ("INVOICE_GENERATION", "add"),
    "invoiceprinting": ("INVOICE_PRINTING", "view"),
    "invoice_print": ("INVOICE_PRINTING", "view"),
    "invoice_print_pdf": ("INVOICE_PRINTING", "view"),
    "api_invoice_filter_options": ("INVOICE_PRINTING", "view"),
    "api_invoices_list": ("INVOICE_PRINTING", "view"),
    "api_invoice_template_narration_status": ("INVOICE_PRINTING", "view"),
    "api_invoices_hospital_print_summary": ("INVOICE_PRINTING", "view"),
    "api_invoice_save_custom_narration": ("INVOICE_PRINTING", "edit"),
    "api_invoice_mark_printed": ("INVOICE_PRINTING", "edit"),
    "invoice_reconciliation": ("INVOICE_RECONCILIATION", "view"),
    "save_invoice_reconciliation": ("INVOICE_RECONCILIATION", "edit"),

    # Test/stub tooling must not be generally reachable by authenticated users.
    "whatsapp_stub": ("WHATSAPP_STUB", "view"),
    "whatsapp_stub_push": ("WHATSAPP_STUB", "edit"),
}


def _dynamic_endpoint_rule(endpoint: str):
    """Resolve routes whose required permission depends on method/mode."""
    method = request.method.upper()

    if endpoint == "patient_creation":
        mode = str(request.values.get("mode") or "create").strip().lower()
        if mode in {"view", "modify"}:
            return ("PATIENT_VIEW_MODIFY", "edit" if method == "POST" else "view")
        return ("PATIENT_CREATION", "add" if method == "POST" else "view")

    if endpoint in {"partner_creation", "partner_registration_alias"}:
        mode = str(request.values.get("mode") or "create").strip().lower()
        if mode in {"view", "modify"}:
            return ("PARTNER_VIEW_MODIFY", "edit" if method == "POST" else "view")
        return ("PARTNER_REGISTRATION", "add" if method == "POST" else "view")

    if endpoint == "lead_generation":
        return ("LEAD_GENERATION", "add" if method == "POST" else "view")

    if endpoint == "wrkflwstatuschange":
        return ("WORKFLOW_STATUS_CHANGE", "edit" if method == "POST" else "view")

    if endpoint == "file_upload":
        return ("FILE_UPLOAD", "add" if method == "POST" else "view")

    if endpoint == "user_management":
        return ("USER_MANAGEMENT", "edit" if method == "POST" else "view")

    if endpoint == "hospital_department_mapping":
        return ("HOSPITAL_DEPARTMENT_MAPPING", "edit" if method == "POST" else "view")

    if endpoint == "hospital_partner_mapping":
        if method == "POST":
            action = (request.form.get("action") or "save").strip().lower()

            if action == "deactivate":
                return ("HOSPITAL_PARTNER_MAPPING", "delete")

            mapping_id = (request.form.get("mapping_id") or "").strip()

            if mapping_id:
                return ("HOSPITAL_PARTNER_MAPPING", "edit")

            return ("HOSPITAL_PARTNER_MAPPING", "add")

        return ("HOSPITAL_PARTNER_MAPPING", "view")

    if endpoint == "sla_rule_builder":
        return ("SLA_RULE_BUILDER", "edit" if method == "POST" else "view")

    if endpoint == "round_robin_assignment":
        return ("ROUND_ROBIN_ASSIGNMENT", "edit" if method == "POST" else "view")

    return None


def register_rbac_guard(app):
    @app.before_request
    def _rbac_guard():
        endpoint = request.endpoint or ""
        rule = _dynamic_endpoint_rule(endpoint) or _ENDPOINT_RULES.get(endpoint)
        if not rule:
            return None
        screen_code, action = rule
        return enforce_screen_permission(screen_code, action)
