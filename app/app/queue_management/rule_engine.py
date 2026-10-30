from app.queue_management.thread_engine import resolve_thread_context
from app.queue_management.parser_engine import parse_operation_queue_message


HIGH_PRIORITY_WORDS = [
    "urgent", "asap", "emergency", "critical", "immediately", "today", "tomorrow",
]
CARDIO_DEPTS = ["CARDIOLOGY", "CARDIAC", "HEART"]
NEURO_DEPTS = ["NEUROLOGY", "NEURO", "BRAIN"]
ONCO_DEPTS = ["ONCOLOGY", "CANCER"]
ORTHO_DEPTS = ["ORTHOPEDIC", "ORTHO", "SPINE SURGEON", "SPINE"]
GASTRO_DEPTS = ["GASTRO", "GASTROENTEROLOGY"]

VALID_PARSE_STATUSES = {
    "PENDING", "READY_FOR_PARSE", "PARSING", "PARSED", "PARTIAL",
    "MANUAL_REVIEW", "LINKED", "FAILED", "JUNK",
}
VALID_WORKFLOW_STATUSES = {"OPEN", "ASSIGNED", "IN PROGRESS", "MANUAL REVIEW", "CLOSED"}
VALID_PRIORITY_STATUSES = {"HIGH", "MEDIUM", "LOW", "NORMAL"}


def _contains_any(text, words):
    if not text:
        return False
    normalized_text = str(text).lower()
    return any(str(word).lower() in normalized_text for word in words)


def _safe_upper(value):
    if value is None:
        return ""
    return str(value).strip().upper()


def _safe_int(value, default=0):
    try:
        return int(value)
    except Exception:
        return default


def _normalize_parse_status(parsed_data):
    existing = _safe_upper(parsed_data.get("parse_status"))
    if existing in VALID_PARSE_STATUSES:
        return existing
    if existing in ("SUCCESS", "NEW"):
        return "PARSED"
    if existing == "PENDING_REVIEW":
        return "PARTIAL"
    if existing in ("FOLLOWUP_LINKED", "ATTACHMENT_LINKED"):
        return "LINKED"
    return "PENDING"


def _derive_priority(parsed_data, raw_message_text):
    existing = _safe_upper(parsed_data.get("priority_status"))
    if existing in VALID_PRIORITY_STATUSES:
        return existing
    text = raw_message_text or ""
    if _contains_any(text, HIGH_PRIORITY_WORDS):
        return "HIGH"
    if parsed_data.get("appointment_dt"):
        return "HIGH"
    if parsed_data.get("vil_date"):
        return "MEDIUM"
    return "NORMAL"


def _derive_workflow_status(parsed_data, existing_thread_status=None):
    existing = str(parsed_data.get("wrkflw_status") or "").strip().upper()
    parse_status = _normalize_parse_status(parsed_data)
    assigned_to = str(parsed_data.get("assigned_to") or "").strip()
    carried_status = str(existing_thread_status or "").strip().upper()
    if carried_status in VALID_WORKFLOW_STATUSES:
        return carried_status
    if existing in VALID_WORKFLOW_STATUSES:
        return existing
    if parse_status in ("FAILED", "PARTIAL", "MANUAL_REVIEW"):
        return "MANUAL REVIEW"
    if assigned_to:
        return "ASSIGNED"
    return "OPEN"


def _derive_department_from_doctor_or_text(parsed_data, raw_message_text):
    if parsed_data.get("hospital_dept"):
        return parsed_data["hospital_dept"]
    text = (raw_message_text or "").upper()
    if _contains_any(text, CARDIO_DEPTS): return "CARDIOLOGY"
    if _contains_any(text, NEURO_DEPTS): return "NEUROLOGY"
    if _contains_any(text, ONCO_DEPTS): return "ONCOLOGY"
    if _contains_any(text, ORTHO_DEPTS): return "ORTHOPEDICS"
    if _contains_any(text, GASTRO_DEPTS): return "GASTROENTEROLOGY"
    return None


def _derive_attender_count(parsed_data):
    count = 0
    for prefix in ("attn1", "attn2", "attn3"):
        if (
            parsed_data.get(f"{prefix}_givennm")
            or parsed_data.get(f"{prefix}_surnm")
            or parsed_data.get(f"{prefix}passport_no")
            or parsed_data.get(f"{prefix}_contno")
        ):
            count += 1
    return count


def _derive_parse_remarks(parsed_data):
    existing_parse_remarks = str(parsed_data.get("parse_remarks") or "").strip()
    if _normalize_parse_status(parsed_data) == "MANUAL_REVIEW" and existing_parse_remarks:
        return existing_parse_remarks
    remarks = []
    if not parsed_data.get("patgiven_nm"): remarks.append("Patient given name missing")
    if not parsed_data.get("patient_contno") and not parsed_data.get("patient_email"):
        remarks.append("Patient contact missing")
    if not parsed_data.get("hospital_shrtnm"): remarks.append("Hospital missing")
    if not parsed_data.get("doctor_nm"): remarks.append("Doctor missing")
    if not parsed_data.get("hospital_dept"): remarks.append("Department missing")
    if not remarks:
        return existing_parse_remarks or "All key fields parsed."
    if existing_parse_remarks:
        remarks.insert(0, existing_parse_remarks)
    return "; ".join(remarks)


def should_create_operation_queue_row(parsed_data):
    if not parsed_data:
        return False
    parse_status = _normalize_parse_status(parsed_data)
    return parse_status not in ("FAILED", "JUNK", "LINKED")


def apply_operation_queue_rules(raw_message_text, channel_meta=None):
    parsed_data = parse_operation_queue_message(
        raw_message_text=raw_message_text,
        channel_meta=channel_meta or {},
    ) or {}
    parsed_data["parse_status"] = _normalize_parse_status(parsed_data)
    parse_status = parsed_data.get("parse_status")

    thread_context = {
        "channel_thread_id": None,
        "assigned_to": None,
        "wrkflw_status": None,
        "continuity_applied": False,
        "source": "MANUAL_REVIEW" if parse_status == "MANUAL_REVIEW" else "NO_PASSPORT",
    }

    # Revised requirement: Manual Review records remain unresolved. They may be
    # inserted into operation_queue_master, but must not receive a thread or the
    # inbound channel_message_id until the review is completed.
    if parse_status == "MANUAL_REVIEW":
        parsed_data["channel_thread_id"] = None
        parsed_data["channel_message_id"] = None
        parsed_data["assigned_to"] = None
        parsed_data["assigned_by"] = None
        parsed_data["assigned_at"] = None
    else:
        passport_no = parsed_data.get("patpassport_no")
        if passport_no:
            thread_context = resolve_thread_context(passport_no) or {}
            resolved_thread_id = thread_context.get("channel_thread_id")
            if resolved_thread_id:
                parsed_data["channel_thread_id"] = resolved_thread_id
            if not parsed_data.get("assigned_to") and thread_context.get("assigned_to"):
                parsed_data["assigned_to"] = thread_context.get("assigned_to")

    parsed_data["priority_status"] = _derive_priority(parsed_data, raw_message_text)
    parsed_data["hospital_dept"] = _derive_department_from_doctor_or_text(parsed_data, raw_message_text)
    parsed_data["attender_count"] = _derive_attender_count(parsed_data)
    parsed_data["parse_remarks"] = _derive_parse_remarks(parsed_data)

    if parse_status == "MANUAL_REVIEW":
        reason = str(
            parsed_data.get("manual_review_reason")
            or parsed_data.get("classifier_reason")
            or parsed_data.get("parse_remarks")
            or ""
        ).strip()
        if not reason or reason.upper() in {"MANUAL REVIEW", "MANUAL_REVIEW"}:
            reason = (
                "WhatsApp sender could not be mapped to a valid Agency. "
                "Manual review is required for Agent assignment."
            )
        parsed_data["manual_review_reason"] = reason
        parsed_data["parse_remarks"] = reason

    parsed_data["wrkflw_status"] = _derive_workflow_status(
        parsed_data,
        existing_thread_status=thread_context.get("wrkflw_status"),
    )
    if not parsed_data.get("wrkflw_remarks"):
        parsed_data["wrkflw_remarks"] = raw_message_text[:1000] if raw_message_text else None
    return parsed_data


def build_operation_queue_insert_payload(raw_message_text, channel_meta=None):
    return apply_operation_queue_rules(raw_message_text, channel_meta or {})


def get_operation_queue_insert_columns():
    sample_payload = build_operation_queue_insert_payload("", {})
    return list(sample_payload.keys())


def get_operation_queue_insert_values(payload):
    return [payload.get(column) for column in get_operation_queue_insert_columns()]
