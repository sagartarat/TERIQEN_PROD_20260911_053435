
import copy
import json
from typing import Any, Dict, List, Optional

from app.common.db import get_db


DEFAULT_TEMPLATE_CODE = "INV_STD_DEFAULT"

_DEFAULT_ITEM_COLUMNS = [
    {
        "display_order": 1,
        "column_label": "Sl",
        "field_key": "{{loop.index}}",
        "column_width": "8%",
        "alignment": "Center",
        "data_type": "NUMBER",
        "is_visible": "Yes",
        "wrap_text": "No",
        "sortable": "Yes",
        "default_value": "",
    },
    {
        "display_order": 2,
        "column_label": "Description",
        "field_key": "{{item.description}}",
        "column_width": "48%",
        "alignment": "Left",
        "data_type": "TEXT",
        "is_visible": "Yes",
        "wrap_text": "Yes",
        "sortable": "No",
        "default_value": "",
    },
    {
        "display_order": 3,
        "column_label": "Qty",
        "field_key": "{{item.qty}}",
        "column_width": "12%",
        "alignment": "Center",
        "data_type": "NUMBER",
        "is_visible": "Yes",
        "wrap_text": "No",
        "sortable": "No",
        "default_value": "",
    },
    {
        "display_order": 4,
        "column_label": "Rate",
        "field_key": "{{item.rate}}",
        "column_width": "16%",
        "alignment": "Right",
        "data_type": "NUMBER",
        "is_visible": "Yes",
        "wrap_text": "No",
        "sortable": "No",
        "default_value": "",
    },
    {
        "display_order": 5,
        "column_label": "Amount",
        "field_key": "{{item.amount}}",
        "column_width": "16%",
        "alignment": "Right",
        "data_type": "NUMBER",
        "is_visible": "Yes",
        "wrap_text": "No",
        "sortable": "No",
        "default_value": "",
    },
]

_DEFAULT_BUILDER_CONFIG = {
    "paper_size": "A4 Portrait",
    "applicable_module": "Invoice Management",
    "default_currency": "",
    "logo_path": "",
    "scope_type": "DEFAULT",
    "hospital_name": "",
    "customer_narration": False,
    "company_name": "",
    "company_address": "",
    "company_gstin": "",
    "company_email": "",
    "company_phone": "",
    "notes_text": "",
    "show_company_logo": True,
    "show_company_meta": True,
    "show_document_title": True,
    "show_invoice_number": True,
    "show_notes_section": True,
    "show_tax_breakup": True,
    "enable_print_footer": True,
    "allow_multiple_item_rows": True,
    "combine_selected_records_into_single_row": False,
    "repeat_header_on_new_page": True,
    "show_serial_number": True,
    "auto_wrap_description": True,
    "item_columns": copy.deepcopy(_DEFAULT_ITEM_COLUMNS),
    "document_title": "Invoice",
    "proto_bill_to_name": "",
    "proto_bill_to_body": "",
    "proto_invoice_date": "",
    "proto_due_date": "",
    "proto_currency": "",
    "proto_invoice_number": "",
    "proto_total_invoice_amount": "",
    "proto_total_tax_amount": "",
    "proto_total_tds_amount": "",
    "proto_net_amount": "",
}

_DEFAULT_TEMPLATE = {
    "template_code": DEFAULT_TEMPLATE_CODE,
    "combine_row": "N",
    "template_name": "",
    "template_category": "Invoice Print",
    "template_body": "",
    "template_type": "INVOICE",
    "template_status": "DRAFT",
    "template_language": "English",
    "output_format": "HTML",
    "template_description": "",
    "header_html": "",
    "footer_html": "",
    "css_content": "",
    "page_size": "A4",
    "page_orientation": "Portrait",
    "version_no": 1,
    "remarks": "",
    "builder_config": copy.deepcopy(_DEFAULT_BUILDER_CONFIG),
}


def _safe_close(cursor=None, conn=None) -> None:
    try:
        if cursor is not None:
            cursor.close()
    except Exception:
        pass

    try:
        if conn is not None and conn.is_connected():
            conn.close()
    except Exception:
        pass


def _blank_template() -> Dict[str, Any]:
    return copy.deepcopy(_DEFAULT_TEMPLATE)


def _normalize_text(value: Any, default: str = "") -> str:
    if value is None:
        return default
    return str(value).strip()


def _normalize_bool(value: Any, default: bool = False) -> bool:
    if value is None:
        return default

    if isinstance(value, bool):
        return value

    if isinstance(value, (int, float)):
        return bool(value)

    value_str = str(value).strip().lower()
    if value_str in {"1", "true", "yes", "y", "on"}:
        return True
    if value_str in {"0", "false", "no", "n", "off"}:
        return False
    return default


def _normalize_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except Exception:
        return default


def _page_size_from_paper_size(paper_size: str) -> str:
    normalized = _normalize_text(paper_size, "A4 Portrait").upper()
    if normalized.startswith("A5"):
        return "A5"
    if normalized.startswith("LETTER"):
        return "Letter"
    return "A4"


def _page_orientation_from_paper_size(paper_size: str) -> str:
    normalized = _normalize_text(paper_size, "A4 Portrait").upper()
    return "Landscape" if "LANDSCAPE" in normalized else "Portrait"


def _paper_size_from_db(page_size: str, page_orientation: str) -> str:
    size = _normalize_text(page_size, "A4")
    orientation = _normalize_text(page_orientation, "Portrait")
    return f"{size} {orientation}".strip()


def _safe_json_loads(text_value: Any, fallback: Any) -> Any:
    raw = _normalize_text(text_value)
    if not raw:
        return copy.deepcopy(fallback)
    try:
        return json.loads(raw)
    except Exception:
        return copy.deepcopy(fallback)


def _safe_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2)


def _normalize_item_columns(item_columns: Any) -> List[Dict[str, Any]]:
    normalized_columns: List[Dict[str, Any]] = []

    if isinstance(item_columns, list):
        for idx, row in enumerate(item_columns, start=1):
            if not isinstance(row, dict):
                continue
            normalized_columns.append(
                {
                    "display_order": _normalize_int(row.get("display_order"), idx),
                    "column_label": _normalize_text(row.get("column_label")) or f"Column {idx}",
                    "field_key": _normalize_text(row.get("field_key")),
                    "column_width": _normalize_text(row.get("column_width")),
                    "alignment": _normalize_text(row.get("alignment"), "Left") or "Left",
                    "data_type": _normalize_text(row.get("data_type"), "TEXT") or "TEXT",
                    "is_visible": _normalize_text(row.get("is_visible"), "Yes") or "Yes",
                    "wrap_text": _normalize_text(row.get("wrap_text"), "No") or "No",
                    "sortable": _normalize_text(row.get("sortable"), "No") or "No",
                    "default_value": _normalize_text(row.get("default_value")),
                }
            )

    if not normalized_columns:
        normalized_columns = copy.deepcopy(_DEFAULT_ITEM_COLUMNS)

    normalized_columns.sort(key=lambda x: x.get("display_order", 0))
    return normalized_columns


def _normalize_proto_field(value: Any, fallback: Any = "") -> str:
    if value is None:
        value = fallback
    return _normalize_text(value)


def _normalize_builder_config(builder_config: Any) -> Dict[str, Any]:
    config = copy.deepcopy(_DEFAULT_BUILDER_CONFIG)

    if isinstance(builder_config, dict):
        config.update(builder_config)

    config["paper_size"] = _normalize_text(config.get("paper_size"), "A4 Portrait") or "A4 Portrait"
    config["applicable_module"] = _normalize_text(config.get("applicable_module"), "Invoice Management") or "Invoice Management"
    config["default_currency"] = _normalize_text(config.get("default_currency"))
    config["logo_path"] = _normalize_text(config.get("logo_path"))
    config["scope_type"] = _normalize_text(config.get("scope_type"), "DEFAULT").upper() or "DEFAULT"
    config["hospital_name"] = _normalize_text(config.get("hospital_name"))
    config["customer_narration"] = (
        _normalize_bool(config.get("customer_narration"), False)
        if config.get("scope_type") == "HOSPITAL"
        else False
    )
    config["company_name"] = _normalize_text(config.get("company_name"))
    config["company_address"] = _normalize_text(config.get("company_address"))
    config["company_gstin"] = _normalize_text(config.get("company_gstin"))
    config["company_email"] = _normalize_text(config.get("company_email"))
    config["company_phone"] = _normalize_text(config.get("company_phone"))
    config["notes_text"] = _normalize_text(config.get("notes_text"))
    config["document_title"] = _normalize_text(config.get("document_title"), "Invoice") or "Invoice"

    config["show_company_logo"] = _normalize_bool(config.get("show_company_logo"), True)
    config["show_company_meta"] = _normalize_bool(config.get("show_company_meta"), True)
    config["show_document_title"] = _normalize_bool(config.get("show_document_title"), True)
    config["show_invoice_number"] = _normalize_bool(config.get("show_invoice_number"), True)
    config["show_notes_section"] = _normalize_bool(config.get("show_notes_section"), True)
    config["show_tax_breakup"] = _normalize_bool(config.get("show_tax_breakup"), True)
    config["enable_print_footer"] = _normalize_bool(config.get("enable_print_footer"), True)
    config["allow_multiple_item_rows"] = _normalize_bool(config.get("allow_multiple_item_rows"), True)
    config["combine_selected_records_into_single_row"] = _normalize_bool(config.get("combine_selected_records_into_single_row"), False)
    config["repeat_header_on_new_page"] = _normalize_bool(config.get("repeat_header_on_new_page"), True)
    config["show_serial_number"] = _normalize_bool(config.get("show_serial_number"), True)
    config["auto_wrap_description"] = _normalize_bool(config.get("auto_wrap_description"), True)

    legacy_header_invoice_no = _normalize_proto_field(config.get("proto_header_invoice_no"))
    config["proto_bill_to_name"] = _normalize_proto_field(config.get("proto_bill_to_name"), config.get("hospital_name"))
    config["proto_bill_to_body"] = _normalize_proto_field(config.get("proto_bill_to_body"))
    config["proto_invoice_date"] = _normalize_proto_field(config.get("proto_invoice_date"))
    config["proto_due_date"] = _normalize_proto_field(config.get("proto_due_date"))
    config["proto_currency"] = _normalize_proto_field(config.get("proto_currency"), config.get("default_currency"))
    config["proto_invoice_number"] = _normalize_proto_field(
        config.get("proto_invoice_number"),
        legacy_header_invoice_no
    )
    config["proto_total_invoice_amount"] = _normalize_proto_field(config.get("proto_total_invoice_amount"))
    config["proto_total_tax_amount"] = _normalize_proto_field(config.get("proto_total_tax_amount"))
    config["proto_total_tds_amount"] = _normalize_proto_field(config.get("proto_total_tds_amount"))
    config["proto_net_amount"] = _normalize_proto_field(config.get("proto_net_amount"))

    config["item_columns"] = _normalize_item_columns(config.get("item_columns"))
    return config


def _flatten_builder_fields(model: Dict[str, Any], builder_config: Dict[str, Any]) -> Dict[str, Any]:
    model["paper_size"] = builder_config.get("paper_size")
    model["applicable_module"] = builder_config.get("applicable_module")
    model["default_currency"] = builder_config.get("default_currency")
    model["logo_path"] = builder_config.get("logo_path")
    model["scope_type"] = builder_config.get("scope_type")
    model["hospital_name"] = builder_config.get("hospital_name")
    model["customer_narration"] = builder_config.get("customer_narration")
    model["company_name"] = builder_config.get("company_name")
    model["company_address"] = builder_config.get("company_address")
    model["company_gstin"] = builder_config.get("company_gstin")
    model["company_email"] = builder_config.get("company_email")
    model["company_phone"] = builder_config.get("company_phone")
    model["notes_text"] = builder_config.get("notes_text")
    model["document_title"] = builder_config.get("document_title")
    model["show_company_logo"] = builder_config.get("show_company_logo")
    model["show_company_meta"] = builder_config.get("show_company_meta")
    model["show_document_title"] = builder_config.get("show_document_title")
    model["show_invoice_number"] = builder_config.get("show_invoice_number")
    model["show_notes_section"] = builder_config.get("show_notes_section")
    model["show_tax_breakup"] = builder_config.get("show_tax_breakup")
    model["enable_print_footer"] = builder_config.get("enable_print_footer")
    model["allow_multiple_item_rows"] = builder_config.get("allow_multiple_item_rows")
    model["combine_selected_records_into_single_row"] = builder_config.get("combine_selected_records_into_single_row")
    model["repeat_header_on_new_page"] = builder_config.get("repeat_header_on_new_page")
    model["show_serial_number"] = builder_config.get("show_serial_number")
    model["auto_wrap_description"] = builder_config.get("auto_wrap_description")
    model["item_columns"] = builder_config.get("item_columns", copy.deepcopy(_DEFAULT_ITEM_COLUMNS))

    model["proto_bill_to_name"] = builder_config.get("proto_bill_to_name")
    model["proto_bill_to_body"] = builder_config.get("proto_bill_to_body")
    model["proto_invoice_date"] = builder_config.get("proto_invoice_date")
    model["proto_due_date"] = builder_config.get("proto_due_date")
    model["proto_currency"] = builder_config.get("proto_currency")
    model["proto_invoice_number"] = builder_config.get("proto_invoice_number")
    model["proto_total_invoice_amount"] = builder_config.get("proto_total_invoice_amount")
    model["proto_total_tax_amount"] = builder_config.get("proto_total_tax_amount")
    model["proto_total_tds_amount"] = builder_config.get("proto_total_tds_amount")
    model["proto_net_amount"] = builder_config.get("proto_net_amount")
    return model


def _template_row_to_model(row: Dict[str, Any]) -> Dict[str, Any]:
    model = _blank_template()
    model.update(
        {
            "id": row.get("id"),
            "template_code": _normalize_text(row.get("template_code"), DEFAULT_TEMPLATE_CODE),
            "template_name": _normalize_text(row.get("template_name")),
            "template_category": _normalize_text(row.get("template_category"), "Invoice Print"),
            "template_body": _normalize_text(row.get("template_body")),
            "template_type": _normalize_text(row.get("template_type"), "INVOICE"),
            "template_status": (_normalize_text(row.get("template_status"), "DRAFT") or "DRAFT").upper(),
            "template_language": _normalize_text(row.get("template_language"), "English"),
            "output_format": _normalize_text(row.get("output_format"), "HTML"),
            "template_description": "",
            "applicable_module": _normalize_text(row.get("applicable_module")),
            "default_currency": _normalize_text(row.get("default_currency")),
            "scope_type": _normalize_text(row.get("scope_type"), "DEFAULT") or "DEFAULT",
            "hospital_name": _normalize_text(row.get("hospital_name")),
            "combine_row": _normalize_text(row.get("combine_row"), "N") or "N",
            "header_html": _normalize_text(row.get("header_html")),
            "footer_html": _normalize_text(row.get("footer_html")),
            "css_content": _normalize_text(row.get("css_content")),
            "page_size": _normalize_text(row.get("page_size"), "A4"),
            "page_orientation": _normalize_text(row.get("page_orientation"), "Portrait"),
            "version_no": _normalize_int(row.get("version_no"), 1),
            "remarks": _normalize_text(row.get("remarks")),
            "created_by": row.get("created_by"),
            "created_on": row.get("created_on"),
            "updated_by": row.get("updated_by"),
            "updated_on": row.get("updated_on"),
        }
    )

    builder_config = _normalize_builder_config(
        _safe_json_loads(model.get("template_body"), _DEFAULT_BUILDER_CONFIG)
    )
    if _normalize_text(row.get("applicable_module")):
        builder_config["applicable_module"] = _normalize_text(row.get("applicable_module"))
    if _normalize_text(row.get("default_currency")):
        builder_config["default_currency"] = _normalize_text(row.get("default_currency"))
    if _normalize_text(row.get("scope_type")):
        builder_config["scope_type"] = _normalize_text(row.get("scope_type")).upper()
    if _normalize_text(row.get("hospital_name")):
        builder_config["hospital_name"] = _normalize_text(row.get("hospital_name"))
    if row.get("combine_row") in ("Y", "N"):
        builder_config["combine_selected_records_into_single_row"] = (row.get("combine_row") == "Y")
    if not builder_config.get("paper_size"):
        builder_config["paper_size"] = _paper_size_from_db(model.get("page_size"), model.get("page_orientation"))

    model["builder_config"] = builder_config
    model["combine_row"] = "Y" if builder_config.get("combine_selected_records_into_single_row") else "N"
    return _flatten_builder_fields(model, builder_config)


def _fetchall_dict(sql: str, params: tuple = ()) -> List[Dict[str, Any]]:
    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(sql, params)
        return cursor.fetchall() or []
    finally:
        _safe_close(cursor, conn)


def _fetchone_dict(sql: str, params: tuple = ()) -> Optional[Dict[str, Any]]:
    rows = _fetchall_dict(sql, params)
    return rows[0] if rows else None


def _ensure_default_template_exists() -> None:
    return


def list_invoice_templates() -> List[Dict[str, Any]]:
    rows = _fetchall_dict(
        """
        SELECT *
        FROM invoice_template_master
        ORDER BY
            CASE UPPER(COALESCE(scope_type, 'DEFAULT'))
                WHEN 'DEFAULT' THEN 0
                WHEN 'HOSPITAL' THEN 1
                ELSE 2
            END,
            template_name ASC,
            template_code ASC
        """
    )

    template_list: List[Dict[str, Any]] = []
    for row in rows:
        builder_config = _normalize_builder_config(
            _safe_json_loads(row.get("template_body"), _DEFAULT_BUILDER_CONFIG)
        )
        template_list.append(
            {
                "id": row.get("id"),
                "template_code": row.get("template_code"),
                "template_name": row.get("template_name"),
                "template_category": row.get("template_category"),
                "template_status": (_normalize_text(row.get("template_status"), "DRAFT") or "DRAFT").upper(),
                "template_type": row.get("template_type"),
                "output_format": row.get("output_format"),
                "version_no": row.get("version_no"),
                "applicable_module": _normalize_text(row.get("applicable_module")) or builder_config.get("applicable_module"),
                "default_currency": _normalize_text(row.get("default_currency")) or builder_config.get("default_currency"),
                "scope_type": _normalize_text(row.get("scope_type")) or builder_config.get("scope_type"),
                "hospital_name": _normalize_text(row.get("hospital_name")) or builder_config.get("hospital_name"),
            }
        )

    return template_list


def get_invoice_template_by_code(template_code: Optional[str]) -> Dict[str, Any]:
    template_code = _normalize_text(template_code)

    if template_code:
        row = _fetchone_dict(
            """
            SELECT *
            FROM invoice_template_master
            WHERE template_code = %s
            LIMIT 1
            """,
            (template_code,),
        )
        if row:
            return _template_row_to_model(row)

    row = _fetchone_dict(
        """
        SELECT *
        FROM invoice_template_master
        WHERE UPPER(COALESCE(scope_type, 'DEFAULT')) = 'DEFAULT'
        ORDER BY UPPER(COALESCE(template_status, '')) = 'ACTIVE' DESC, id ASC
        LIMIT 1
        """
    )
    if row:
        return _template_row_to_model(row)

    row = _fetchone_dict(
        """
        SELECT *
        FROM invoice_template_master
        ORDER BY id ASC
        LIMIT 1
        """
    )
    if row:
        return _template_row_to_model(row)

    return _blank_template()


def _load_placeholder_master_rows() -> List[Dict[str, Any]]:
    return _fetchall_dict(
        """
        SELECT
            id,
            placeholder_key,
            placeholder_label,
            placeholder_category,
            source_table,
            source_column,
            source_alias,
            data_type,
            format_mask,
            is_computed,
            expression_rule,
            line_item_scope,
            sort_order,
            sample_value,
            status,
            remarks
        FROM invoice_placeholder_master
        WHERE status = 'ACTIVE'
        ORDER BY placeholder_category ASC, sort_order ASC, placeholder_label ASC
        """
    )


def _build_field_library_from_placeholder_master() -> Dict[str, List[str]]:
    rows = _load_placeholder_master_rows()

    category_map = {
        "HEADER": "invoice_details",
        "PATIENT": "patient_workflow",
        "HOSPITAL": "hospital_details",
        "AGENCY": "agency_details",
        "FINANCE": "financials",
        "TAX": "financials",
        "PAYMENT": "financials",
        "ADDRESS": "address_details",
        "LINE_ITEM": "item_fields",
        "SYSTEM": "system_footer",
        "CUSTOM": "custom_fields",
    }

    field_library: Dict[str, List[str]] = {
        "invoice_details": [],
        "hospital_details": [],
        "patient_workflow": [],
        "agency_details": [],
        "address_details": [],
        "item_fields": [],
        "financials": [],
        "system_footer": [],
        "custom_fields": [],
    }

    for row in rows:
        placeholder_key = _normalize_text(row.get("placeholder_key"))
        if not placeholder_key:
            continue
        bucket = category_map.get(_normalize_text(row.get("placeholder_category")).upper(), "custom_fields")
        field_library.setdefault(bucket, [])
        if placeholder_key not in field_library[bucket]:
            field_library[bucket].append(placeholder_key)

    if "{{loop.index}}" not in field_library["item_fields"]:
        field_library["item_fields"].insert(0, "{{loop.index}}")

    return field_library


def get_invoice_template_builder_context(
    selected_template_code: Optional[str] = None,
    success_message: Optional[str] = None,
    error_message: Optional[str] = None,
) -> Dict[str, Any]:
    selected_template = get_invoice_template_by_code(selected_template_code)
    return {
        "template_data": selected_template,
        "template_list": list_invoice_templates(),
        "field_library": _build_field_library_from_placeholder_master(),
        "success_message": success_message,
        "error_message": error_message,
        "page_mode": "builder",
    }


def delete_invoice_template_by_code(template_code: Optional[str], user_code: str = "SYSTEM") -> Dict[str, Any]:
    template_code = _normalize_text(template_code)
    if not template_code:
        return {"success": False, "message": "Template Code is required for delete."}

    existing_row = _fetchone_dict(
        """
        SELECT id, template_code
        FROM invoice_template_master
        WHERE template_code = %s
        LIMIT 1
        """,
        (template_code,),
    )
    if not existing_row:
        return {"success": False, "message": "Invoice Template not found."}

    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            DELETE FROM invoice_template_master
            WHERE template_code = %s
            """,
            (template_code,),
        )

        redirect_template_code = ""
        cursor.execute(
            """
            SELECT template_code
            FROM invoice_template_master
            ORDER BY
                CASE UPPER(COALESCE(scope_type, 'DEFAULT'))
                    WHEN 'DEFAULT' THEN 0
                    WHEN 'HOSPITAL' THEN 1
                    ELSE 2
                END,
                id ASC
            LIMIT 1
            """
        )
        first_remaining = cursor.fetchone() or {}
        redirect_template_code = _normalize_text(first_remaining.get("template_code"))

        conn.commit()
        return {
            "success": True,
            "message": "Invoice Template deleted successfully.",
            "template_code": template_code,
            "redirect_template_code": redirect_template_code,
            "deleted_by": user_code,
        }
    except Exception as exc:
        if conn:
            conn.rollback()
        return {
            "success": False,
            "message": f"Unable to delete Invoice Template: {exc}",
        }
    finally:
        _safe_close(cursor, conn)


def save_invoice_template_builder_payload(payload: Dict[str, Any], user_code: str = "SYSTEM") -> Dict[str, Any]:
    payload = payload or {}
    action = _normalize_text(payload.get("action")).lower()

    if action == "delete":
        return delete_invoice_template_by_code(payload.get("template_code"), user_code=user_code)

    template_code = _normalize_text(payload.get("template_code"))
    template_name = _normalize_text(payload.get("template_name"))

    if not template_code:
        return {"success": False, "message": "Template Code is required."}
    if not template_name:
        return {"success": False, "message": "Template Name is required."}

    template_category = _normalize_text(payload.get("template_category"), "Invoice Print") or "Invoice Print"
    template_type = _normalize_text(payload.get("template_type"), "INVOICE").upper() or "INVOICE"
    template_status = _normalize_text(payload.get("template_status"), "DRAFT").upper() or "DRAFT"
    template_language = _normalize_text(payload.get("template_language"), "English") or "English"
    output_format = _normalize_text(payload.get("output_format"), "HTML").upper() or "HTML"
    template_description = ""
    header_html = _normalize_text(payload.get("header_html"))
    footer_html = _normalize_text(payload.get("footer_html"))
    css_content = _normalize_text(payload.get("css_content"))
    remarks = _normalize_text(payload.get("remarks"))

    applicable_module = _normalize_text(payload.get("applicable_module"))
    default_currency = _normalize_text(payload.get("default_currency"))
    scope_type = _normalize_text(payload.get("scope_type"), "DEFAULT").upper() or "DEFAULT"
    hospital_name = _normalize_text(payload.get("hospital_name"))

    if scope_type == "HOSPITAL" and not hospital_name:
        return {"success": False, "message": "Hospital Name is required when Scope Type is HOSPITAL."}
    if scope_type != "HOSPITAL":
        hospital_name = ""

    paper_size = _normalize_text(payload.get("paper_size"), "A4 Portrait") or "A4 Portrait"
    page_size = _page_size_from_paper_size(paper_size)
    page_orientation = _page_orientation_from_paper_size(paper_size)

    existing_row = _fetchone_dict(
        """
        SELECT id, version_no
        FROM invoice_template_master
        WHERE template_code = %s
        LIMIT 1
        """,
        (template_code,),
    )

    builder_config = _normalize_builder_config(
        {
            "paper_size": paper_size,
            "applicable_module": payload.get("applicable_module"),
            "default_currency": payload.get("default_currency"),
            "logo_path": payload.get("logo_path"),
            "scope_type": payload.get("scope_type"),
            "hospital_name": payload.get("hospital_name"),
            "customer_narration": payload.get("customer_narration", False),
            "company_name": payload.get("company_name"),
            "company_address": payload.get("company_address"),
            "company_gstin": payload.get("company_gstin"),
            "company_email": payload.get("company_email"),
            "company_phone": payload.get("company_phone"),
            "notes_text": payload.get("notes_text"),
            "show_company_logo": payload.get("show_company_logo", True),
            "show_company_meta": payload.get("show_company_meta", True),
            "show_document_title": payload.get("show_document_title", True),
            "show_invoice_number": payload.get("show_invoice_number", True),
            "show_notes_section": payload.get("show_notes_section", True),
            "show_tax_breakup": payload.get("show_tax_breakup", True),
            "enable_print_footer": payload.get("enable_print_footer", True),
            "allow_multiple_item_rows": payload.get("allow_multiple_item_rows", True),
            "combine_selected_records_into_single_row": payload.get("combine_selected_records_into_single_row", False),
            "repeat_header_on_new_page": payload.get("repeat_header_on_new_page", True),
            "show_serial_number": payload.get("show_serial_number", True),
            "auto_wrap_description": payload.get("auto_wrap_description", True),
            "item_columns": payload.get("item_columns"),
            "document_title": payload.get("document_title"),
            "proto_bill_to_name": payload.get("proto_bill_to_name"),
            "proto_bill_to_body": payload.get("proto_bill_to_body"),
            "proto_invoice_date": payload.get("proto_invoice_date"),
            "proto_due_date": payload.get("proto_due_date"),
            "proto_currency": payload.get("proto_currency"),
            "proto_invoice_number": payload.get("proto_invoice_number"),
            "proto_total_invoice_amount": payload.get("proto_total_invoice_amount"),
            "proto_total_tax_amount": payload.get("proto_total_tax_amount"),
            "proto_total_tds_amount": payload.get("proto_total_tds_amount"),
            "proto_net_amount": payload.get("proto_net_amount"),
        }
    )

    version_no = 1
    if existing_row:
        version_no = _normalize_int(existing_row.get("version_no"), 1)

    combine_row = "Y" if _normalize_bool(builder_config.get("combine_selected_records_into_single_row"), False) else "N"
    template_body = _safe_json_dumps(builder_config)
    combine_row = "Y" if builder_config.get("combine_selected_records_into_single_row") else "N"

    conn = None
    cursor = None
    try:
        conn = get_db()
        cursor = conn.cursor(dictionary=True)

        if existing_row:
            cursor.execute(
                """
                UPDATE invoice_template_master
                SET
                    template_name = %s,
                    template_category = %s,
                    template_body = %s,
                    template_type = %s,
                    template_status = %s,
                    template_language = %s,
                    output_format = %s,
                    template_description = %s,
                    applicable_module = %s,
                    default_currency = %s,
                    scope_type = %s,
                    hospital_name = %s,
                    combine_row = %s,
                    header_html = %s,
                    footer_html = %s,
                    css_content = %s,
                    page_size = %s,
                    page_orientation = %s,
                    remarks = %s,
                    updated_by = %s,
                    updated_on = CURRENT_TIMESTAMP
                WHERE template_code = %s
                """,
                (
                    template_name,
                    template_category,
                    template_body,
                    template_type,
                    template_status,
                    template_language,
                    output_format,
                    template_description,
                    applicable_module,
                    default_currency,
                    scope_type,
                    hospital_name,
                    combine_row,
                    header_html,
                    footer_html,
                    css_content,
                    page_size,
                    page_orientation,
                    remarks,
                    user_code,
                    template_code,
                ),
            )
        else:
            cursor.execute(
                """
                INSERT INTO invoice_template_master
                (
                    template_code,
                    template_name,
                    template_category,
                    template_body,
                    template_type,
                    template_status,
                    template_language,
                    output_format,
                    template_description,
                    applicable_module,
                    default_currency,
                    scope_type,
                    hospital_name,
                    combine_row,
                    header_html,
                    footer_html,
                    css_content,
                    page_size,
                    page_orientation,
                    version_no,
                    remarks,
                    created_by,
                    updated_by
                )
                VALUES
                (
                    %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
                )
                """,
                (
                    template_code,
                    template_name,
                    template_category,
                    template_body,
                    template_type,
                    template_status,
                    template_language,
                    output_format,
                    template_description,
                    applicable_module,
                    default_currency,
                    scope_type,
                    hospital_name,
                    combine_row,
                    header_html,
                    footer_html,
                    css_content,
                    page_size,
                    page_orientation,
                    version_no,
                    remarks,
                    user_code,
                    user_code,
                ),
            )

        conn.commit()
        return {
            "success": True,
            "message": "Invoice Template saved successfully.",
            "template_code": template_code,
            "saved_by": user_code,
        }
    except Exception as exc:
        if conn:
            conn.rollback()
        return {
            "success": False,
            "message": f"Unable to save Invoice Template: {exc}",
        }
    finally:
        _safe_close(cursor, conn)


def get_invoice_template_preview_payload(payload: Dict[str, Any]) -> Dict[str, Any]:
    payload = payload or {}
    normalized_payload = _normalize_builder_config(payload)
    item_columns = _normalize_item_columns(normalized_payload.get("item_columns"))

    return {
        "document_title": _normalize_text(normalized_payload.get("document_title"), "Invoice") or "Invoice",
        "customer_narration": _normalize_bool(normalized_payload.get("customer_narration"), False),
        "company_name": _normalize_text(normalized_payload.get("company_name")),
        "company_address": _normalize_text(normalized_payload.get("company_address")),
        "company_gstin": _normalize_text(normalized_payload.get("company_gstin")),
        "company_email": _normalize_text(normalized_payload.get("company_email")),
        "company_phone": _normalize_text(normalized_payload.get("company_phone")),
        "invoice_no": _normalize_text(normalized_payload.get("proto_invoice_number")),
        "invoice_date": _normalize_text(normalized_payload.get("proto_invoice_date")),
        "due_date": _normalize_text(normalized_payload.get("proto_due_date")),
        "currency": _normalize_text(normalized_payload.get("proto_currency"), normalized_payload.get("default_currency")),
        "hospital_name": _normalize_text(normalized_payload.get("proto_bill_to_name"), normalized_payload.get("hospital_name")),
        "bill_address_1": _normalize_text(normalized_payload.get("proto_bill_to_body")),
        "bill_address_2": "",
        "bill_address_3": "",
        "bill_gstin": "",
        "notes_text": _normalize_text(normalized_payload.get("notes_text")),
        "item_columns": item_columns,
        "items": [],
        "total_invoice_amount": _normalize_text(normalized_payload.get("proto_total_invoice_amount")),
        "total_tax_amount": _normalize_text(normalized_payload.get("proto_total_tax_amount")),
        "total_tds_amount": _normalize_text(normalized_payload.get("proto_total_tds_amount")),
        "net_amount": _normalize_text(normalized_payload.get("proto_net_amount")),
        "header_html": _normalize_text(payload.get("header_html")),
        "footer_html": _normalize_text(payload.get("footer_html")),
        "css_content": _normalize_text(payload.get("css_content")),
        "show_company_logo": _normalize_bool(normalized_payload.get("show_company_logo"), True),
        "show_company_meta": _normalize_bool(normalized_payload.get("show_company_meta"), True),
        "show_document_title": _normalize_bool(normalized_payload.get("show_document_title"), True),
        "show_invoice_number": _normalize_bool(normalized_payload.get("show_invoice_number"), True),
        "show_notes_section": _normalize_bool(normalized_payload.get("show_notes_section"), True),
        "show_tax_breakup": _normalize_bool(normalized_payload.get("show_tax_breakup"), True),
        "enable_print_footer": _normalize_bool(normalized_payload.get("enable_print_footer"), True),
        "combine_selected_records_into_single_row": _normalize_bool(normalized_payload.get("combine_selected_records_into_single_row"), False),
        "repeat_header_on_new_page": _normalize_bool(normalized_payload.get("repeat_header_on_new_page"), True),
        "show_serial_number": _normalize_bool(normalized_payload.get("show_serial_number"), True),
        "auto_wrap_description": _normalize_bool(normalized_payload.get("auto_wrap_description"), True),
        "proto_bill_to_name": _normalize_text(normalized_payload.get("proto_bill_to_name")),
        "proto_bill_to_body": _normalize_text(normalized_payload.get("proto_bill_to_body")),
        "proto_invoice_date": _normalize_text(normalized_payload.get("proto_invoice_date")),
        "proto_due_date": _normalize_text(normalized_payload.get("proto_due_date")),
        "proto_currency": _normalize_text(normalized_payload.get("proto_currency")),
        "proto_invoice_number": _normalize_text(normalized_payload.get("proto_invoice_number")),
        "proto_total_invoice_amount": _normalize_text(normalized_payload.get("proto_total_invoice_amount")),
        "proto_total_tax_amount": _normalize_text(normalized_payload.get("proto_total_tax_amount")),
        "proto_total_tds_amount": _normalize_text(normalized_payload.get("proto_total_tds_amount")),
        "proto_net_amount": _normalize_text(normalized_payload.get("proto_net_amount")),
    }


def resolve_invoice_template_for_runtime(hospital_name: Optional[str] = None) -> Dict[str, Any]:
    """
    Runtime template resolution for invoice printing.

    Priority:
    1. Active HOSPITAL scope template matching the invoice hospital name.
    2. Latest Active DEFAULT scope template.
    3. Any latest Active template.
    4. Blank/default system template.

    Important for DEFAULT scope:
    The print flow must use the saved builder_config.item_columns from the
    applicable DEFAULT template. This allows Label = Description and its
    Placeholder / Formula value such as 'Healthcare Referral' to print exactly
    as maintained in Invoice Template Builder.
    """
    hospital_name_normalized = _normalize_text(hospital_name).upper()

    if hospital_name_normalized:
        hospital_row = _fetchone_dict(
            """
            SELECT *
            FROM invoice_template_master
            WHERE UPPER(COALESCE(template_status, '')) = 'ACTIVE'
              AND UPPER(COALESCE(scope_type, '')) = 'HOSPITAL'
              AND UPPER(TRIM(COALESCE(hospital_name, ''))) = %s
            ORDER BY COALESCE(updated_on, created_on) DESC, id DESC
            LIMIT 1
            """,
            (hospital_name_normalized,),
        )
        if hospital_row:
            model = _template_row_to_model(hospital_row)
            return model

    default_row = _fetchone_dict(
        """
        SELECT *
        FROM invoice_template_master
        WHERE UPPER(COALESCE(template_status, '')) = 'ACTIVE'
          AND UPPER(COALESCE(scope_type, 'DEFAULT')) = 'DEFAULT'
        ORDER BY COALESCE(updated_on, created_on) DESC, id DESC
        LIMIT 1
        """
    )
    if default_row:
        model = _template_row_to_model(default_row)
        return model

    any_row = _fetchone_dict(
        """
        SELECT *
        FROM invoice_template_master
        WHERE UPPER(COALESCE(template_status, '')) = 'ACTIVE'
        ORDER BY COALESCE(updated_on, created_on) DESC, id DESC
        LIMIT 1
        """
    )
    if any_row:
        model = _template_row_to_model(any_row)
        return model

    model = _blank_template()
    return model
