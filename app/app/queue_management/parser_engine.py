import re
from datetime import datetime
from app.common.db import get_db_connection
from app.common.logger import get_logger


whatsapp_parser_logger = get_logger("whatsapp_parser")
from app.queue_management.thread_engine import resolve_thread_context


# =========================================================
# Common helpers
# =========================================================
def _clean_text(value):
    if value is None:
        return ""

    value = str(value)
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    value = value.replace("\xa0", " ")
    value = re.sub(r"[ ]+", " ", value)
    value = re.sub(r"\n{2,}", "\n", value)
    return value.strip()


def _safe_str(value):
    if value is None:
        return None
    value = str(value).strip()
    return value if value else None


def _is_whatsapp_channel(channel_code):
    normalized_channel = _normalize_lookup_text(channel_code)
    return normalized_channel in {
        "WHATSAPP",
        "WHATSAPP CHANNEL",
        "WA",
    }


def _normalize_label(label):
    if not label:
        return ""
    label = str(label).strip().lower()
    label = label.replace("\xa0", " ")
    label = re.sub(r"[^a-z0-9]+", " ", label)
    return re.sub(r"\s+", " ", label).strip()


def _normalize_multiline_spaces(value):
    if not value:
        return None
    value = str(value).replace("\xa0", " ")
    value = re.sub(r"[ \t]+", " ", value)
    return value.strip()


def _normalize_mobile(value):
    if not value:
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits if digits else None


def _normalize_lookup_text(value):
    if value is None:
        return None
    value = str(value).replace("\xa0", " ").strip()
    value = re.sub(r"\s+", " ", value)
    return value.upper() if value else None


def _normalize_department_csv(value):
    """
    Normalize one or more department values into the existing comma-separated
    storage format without changing the underlying queue/request flow.

    Examples:
        " Oncology , Cardiology " -> "ONCOLOGY,CARDIOLOGY"
        "ONCOLOGY; CARDIOLOGY"    -> "ONCOLOGY,CARDIOLOGY"
        "ONCOLOGY|CARDIOLOGY"     -> "ONCOLOGY,CARDIOLOGY"

    The original order is preserved, blanks are removed, and duplicate
    departments are removed case-insensitively. Master-data validation is
    intentionally handled outside the parser.
    """
    raw = _safe_str(value)
    if not raw:
        return None

    normalized = raw.replace("|", ",").replace(";", ",")
    parts = [item.strip() for item in normalized.split(",") if item and item.strip()]

    seen = set()
    cleaned = []

    for item in parts:
        department = item.upper()
        if department in seen:
            continue
        seen.add(department)
        cleaned.append(department)

    return ",".join(cleaned) if cleaned else None


def _set_if_blank(data, key, value):
    if not data.get(key) and value is not None:
        data[key] = value
    return data


def _lookup_partner_short_name_by_type(partner_type, raw_short_name):
    normalized_input = _normalize_lookup_text(raw_short_name)
    if not partner_type or not normalized_input:
        return None

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT short_name
            FROM partner_reg
            WHERE partner_type = %s
              AND short_name IS NOT NULL
              AND TRIM(short_name) <> ''
            ORDER BY short_name ASC
            """,
            (partner_type,),
        )
        rows = cursor.fetchall() or []

        for row in rows:
            db_short_name = _safe_str(row.get("short_name"))
            if not db_short_name:
                continue

            if _normalize_lookup_text(db_short_name) == normalized_input:
                return db_short_name

        return None

    except Exception as e:
        print("_lookup_partner_short_name_by_type error:", e)
        return None

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _resolve_hospital_short_name(raw_hospital_name):
    resolved = _lookup_partner_short_name_by_type("Hospital", raw_hospital_name)
    if resolved:
        return _safe_str(resolved)
    return _safe_str(_normalize_multiline_spaces(raw_hospital_name))


def _lookup_agent_short_name_by_sender_mobile(sender_mobile):
    normalized_sender_mobile = _normalize_mobile(sender_mobile)
    if not normalized_sender_mobile:
        return None

    sender_last10 = normalized_sender_mobile[-10:] if len(normalized_sender_mobile) >= 10 else normalized_sender_mobile

    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        sql = """
            SELECT short_name, prim_contno
            FROM partner_reg
            WHERE partner_type = 'Agency'
              AND prim_contno IS NOT NULL
        """
        cursor.execute(sql)
        rows = cursor.fetchall() or []

        for row in rows:
            db_mobile = _normalize_mobile(row.get("prim_contno"))
            if not db_mobile:
                continue

            db_last10 = db_mobile[-10:] if len(db_mobile) >= 10 else db_mobile

            if db_mobile == normalized_sender_mobile or db_last10 == sender_last10:
                return _safe_str(row.get("short_name"))

        return None

    except Exception as e:
        print("_lookup_agent_short_name_by_sender_mobile error:", e)
        return None

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _extract_labeled_value(text, label_variants):
    if not text:
        return None

    for label in label_variants:
        pattern = rf"(?im)^\s*{re.escape(label)}\s*[:=\-]\s*(.+?)\s*$"
        match = re.search(pattern, text)
        if match:
            return _safe_str(match.group(1))

        pattern_tab = rf"(?im)^\s*{re.escape(label)}\s*\t+\s*(.+?)\s*$"
        match_tab = re.search(pattern_tab, text)
        if match_tab:
            return _safe_str(match_tab.group(1))

    return None


def _extract_by_regex(text, patterns):
    if not text:
        return None
    for pattern in patterns:
        match = re.search(pattern, text, re.IGNORECASE | re.MULTILINE)
        if match:
            return _safe_str(match.group(1))
    return None


def _extract_email(text):
    return _extract_by_regex(
        text,
        [
            r"\b([A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,})\b"
        ]
    )


def _extract_phone(text):
    return _extract_by_regex(
        text,
        [
            r"\b(\+?\d[\d\s\-\(\)]{7,20}\d)\b"
        ]
    )


def _extract_passport(text):
    return _extract_by_regex(
        text,
        [
            r"\b([A-Z]{1,3}\d{5,12})\b",
            r"\b([A-Z0-9]{6,15})\b"
        ]
    )


def _normalize_gender(value):
    if not value:
        return None
    v = value.strip().lower()
    mapping = {
        "m": "M",
        "male": "M",
        "f": "F",
        "female": "F",
        "other": "OTHER",
        "o": "OTHER"
    }
    return mapping.get(v, value.strip().upper())


def _normalize_country(value):
    if not value:
        return None
    return value.strip().upper()


def _normalize_name(value):
    if not value:
        return None
    value = re.sub(r"\s+", " ", value).strip()
    return value.upper()


def _split_name(full_name):
    if not full_name:
        return None, None

    name = re.sub(r"\s+", " ", full_name).strip()
    parts = name.split(" ")

    if len(parts) == 1:
        return parts[0].upper(), None

    given = " ".join(parts[:-1]).upper()
    sur = parts[-1].upper()
    return given, sur


def _normalize_date(value):
    if not value:
        return None

    raw = value.strip().upper()
    raw = raw.replace(",", " ")
    raw = re.sub(r"(\d)(ST|ND|RD|TH)\b", r"\1", raw)
    raw = re.sub(r"\s+", " ", raw).strip()

    date_formats = [
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d %Y",
        "%B %d %Y",
        "%d.%m.%Y",
    ]

    for fmt in date_formats:
        try:
            return datetime.strptime(raw, fmt).strftime("%Y-%m-%d")
        except Exception:
            continue

    return value.strip()


def _parse_date_for_business_validation(value):
    """
    Convert an already-extracted parser date into a Python date for
    business-rule validation. Returns None when the value is blank or
    cannot be interpreted as one of the date formats supported by the parser.
    """
    if not value:
        return None

    normalized = _normalize_date(str(value))
    if not normalized:
        return None

    try:
        return datetime.strptime(str(normalized).strip(), "%Y-%m-%d").date()
    except Exception:
        return None


def _validate_queue_identity_dates(data):
    """
    Validate patient/attender DOB and passport-expiry dates before the
    record is allowed into Operation Queue.

    Rules:
    - DOB must be strictly earlier than today.
    - Passport expiry must be strictly later than today.

    The same rules are applied to Patient and Attender 1/2/3.
    Returns a list of Manual Review reasons.
    """
    data = data or {}
    today = datetime.today().date()
    errors = []

    person_rules = [
        ("Patient", "patient_dob", "patpass_expirydt"),
        ("Attender1", "attn1_dob", "attn1pass_expirydt"),
        ("Attender2", "attn2_dob", "attn2pass_expirydt"),
        ("Attender3", "attn3_dob", "attn3pass_expirydt"),
    ]

    for person_label, dob_key, expiry_key in person_rules:
        dob_raw = _safe_str(data.get(dob_key))
        if dob_raw:
            dob_value = _parse_date_for_business_validation(dob_raw)
            if dob_value is None:
                errors.append(
                    f"{person_label} DOB '{dob_raw}' is not a valid supported date."
                )
            elif dob_value >= today:
                errors.append(
                    f"{person_label} DOB {dob_value.strftime('%Y-%m-%d')} must be earlier than today {today.strftime('%Y-%m-%d')}."
                )

        expiry_raw = _safe_str(data.get(expiry_key))
        if expiry_raw:
            expiry_value = _parse_date_for_business_validation(expiry_raw)
            if expiry_value is None:
                errors.append(
                    f"{person_label} Passport Expiry Date '{expiry_raw}' is not a valid supported date."
                )
            elif expiry_value <= today:
                errors.append(
                    f"{person_label} Passport Expiry Date {expiry_value.strftime('%Y-%m-%d')} must be later than today {today.strftime('%Y-%m-%d')}."
                )

    return errors


def _looks_like_date_text(value):
    if not value:
        return False

    raw = str(value).strip().upper()
    raw = raw.replace(",", " ")
    raw = re.sub(r"(\d)(ST|ND|RD|TH)\b", r"\1", raw)
    raw = re.sub(r"\s+", " ", raw).strip()

    date_formats = [
        "%Y-%m-%d",
        "%d-%m-%Y",
        "%d/%m/%Y",
        "%m/%d/%Y",
        "%d %b %Y",
        "%d %B %Y",
        "%b %d %Y",
        "%B %d %Y",
        "%d.%m.%Y",
    ]

    for fmt in date_formats:
        try:
            datetime.strptime(raw, fmt)
            return True
        except Exception:
            continue

    return False


def _to_int(value):
    if value is None:
        return None
    try:
        return int(str(value).strip())
    except Exception:
        return None


def _normalize_intent(value):
    if not value:
        return None

    v = str(value).strip().upper()

    mapping = {
        "VIL": "VIL",
        "APPOINTMENT": "APPOINTMENT",
        "APPT": "APPOINTMENT",
    }

    return mapping.get(v, v)


def _empty_operation_queue_payload():
    return {
        "channel_message_log_id": None,
        "channel_code": None,
        "channel_message_id": None,
        "channel_thread_id": None,

        "patpassport_no": None,
        "patgiven_nm": None,
        "patsur_nm": None,
        "patient_dob": None,
        "patient_country": None,
        "patient_gender": None,
        "patient_contno": None,
        "patient_age": None,
        "patpass_expirydt": None,
        "patient_address": None,
        "patient_email": None,

        "attn1passport_no": None,
        "attn1pat_relation": None,
        "attn1_givennm": None,
        "attn1_surnm": None,
        "attn1_dob": None,
        "attn1_country": None,
        "attn1_gender": None,
        "attn1_contno": None,
        "attn1_age": None,
        "attn1pass_expirydt": None,
        "attn1_address": None,
        "attn1_email": None,

        "attn2passport_no": None,
        "attn2pat_relation": None,
        "attn2_givennm": None,
        "attn2_surnm": None,
        "attn2_dob": None,
        "attn2_country": None,
        "attn2_gender": None,
        "attn2_contno": None,
        "attn2_age": None,
        "attn2pass_expirydt": None,
        "attn2_address": None,
        "attn2_email": None,

        "attn3passport_no": None,
        "attn3pat_relation": None,
        "attn3_givennm": None,
        "attn3_surnm": None,
        "attn3_dob": None,
        "attn3_country": None,
        "attn3_gender": None,
        "attn3_contno": None,
        "attn3_age": None,
        "attn3pass_expirydt": None,
        "attn3_address": None,
        "attn3_email": None,

        "wrkflw_status": None,
        "hospital_shrtnm": None,
        "hospital_city": None,
        "hospital_dept": None,
        "ivac_center": None,
        "agent_shrtnm": None,
        "doctor_nm": None,
        "vil_date": None,
        "appointment_dt": None,
        "intent_remarks": None,
        "wrkflw_remarks": None,
        "priority_status": None,
        "assigned_to": None,
        "assigned_by": None,
        "assigned_at": None,
        "attender_count": 0,
        "parse_status": "PENDING",
        "parse_remarks": None,
        "converted_flag": 0,
        "converted_on": None,
        "converted_by": None,
    }


# =========================================================
# Assignment note
# =========================================================
# New Round Robin assignment is intentionally performed inside
# operation_queue_service.insert_into_operation_queue_master so that queue
# insertion, state movement, and assignment logging use one DB transaction.
# Passport/thread continuity remains handled below by the parser.


# =========================================================
# Continuity helper
# =========================================================
def _apply_passport_continuity_before_assignment(data):
    passport_no = data.get("patpassport_no")
    if not passport_no:
        return {
            "continuity_applied": False,
            "assigned_to": None,
            "channel_thread_id": data.get("channel_thread_id"),
            "source": "NO_PASSPORT",
        }

    thread_context = resolve_thread_context(passport_no)

    resolved_thread_id = thread_context.get("channel_thread_id")
    continuity_user = thread_context.get("assigned_to")
    continuity_applied = bool(thread_context.get("continuity_applied"))

    if resolved_thread_id:
        data["channel_thread_id"] = resolved_thread_id

    return {
        "continuity_applied": continuity_applied,
        "assigned_to": continuity_user,
        "channel_thread_id": resolved_thread_id,
        "source": thread_context.get("source"),
    }


# =========================================================
# Exact label mapping
# =========================================================
def _build_attender_label_map():
    label_map = {}

    for idx in [1, 2, 3]:
        prefixes = [
            f"attender{idx}", f"attender {idx}",
            f"attendant{idx}", f"attendant {idx}",
            f"companion{idx}", f"companion {idx}",
        ]

        field_map = {
            "givennm": [
                "given name", "name", "first name"
            ],
            "surnm": [
                "short name", "surname", "sur name", "last name", "family name"
            ],
            "passport_no": [
                "passport number", "passport no", "passport no.", "passport"
            ],
            "gender": [
                "gender", "sex"
            ],
            "dob": [
                "dob", "date of birth", "birth date"
            ],
            "age": [
                "age"
            ],
            "address": [
                "address", "residential address"
            ],
            "contno": [
                "contact", "contact number", "contact no", "contact no.",
                "mobile", "mobile number", "phone", "phone number"
            ],
            "relation": [
                "relationship with patient", "relation with patient",
                "relationship", "relation"
            ],
            "passport_exp": [
                "passport expiry date", "passport expiry", "passport exp date"
            ],
            "country": [
                "country", "nationality"
            ],
            "email": [
                "email", "email id", "mail"
            ],
        }

        for prefix in prefixes:
            for key, labels in field_map.items():
                for label in labels:
                    label_map[_normalize_label(f"{prefix} {label}")] = (idx, key)

    return label_map


ATTENDER_LABEL_MAP = _build_attender_label_map()


WHATSAPP_LABEL_TO_COLUMN = {
    "patient given name": "patgiven_nm",
    "patient name": "patgiven_nm",

    "patient short name": "patsur_nm",
    "patient surname": "patsur_nm",
    "patient sur name": "patsur_nm",
    "patient last name": "patsur_nm",
    "patient family name": "patsur_nm",

    "patient passport number": "patpassport_no",
    "patient passport no": "patpassport_no",
    "patient passport no.": "patpassport_no",
    "patient passport": "patpassport_no",
    "patienr passport number": "patpassport_no",

    "patient gender": "patient_gender",
    "patient sex": "patient_gender",

    "patient dob": "patient_dob",
    "patient date of birth": "patient_dob",
    "patient birth date": "patient_dob",

    "patient age": "patient_age",

    "patient contact": "patient_contno",
    "patient contact no": "patient_contno",
    "patient contact no.": "patient_contno",
    "patient contact number": "patient_contno",
    "patient mobile": "patient_contno",
    "patient mobile number": "patient_contno",
    "patient phone": "patient_contno",
    "patient phone number": "patient_contno",

    "patient address": "patient_address",
    "patient residential address": "patient_address",

    "patient email": "patient_email",
    "patient email id": "patient_email",
    "patient mail": "patient_email",

    "patient country": "patient_country",
    "patient nationality": "patient_country",

    "patient passport expiry date": "patpass_expirydt",
    "patient passport expiry": "patpass_expirydt",
    "patient passport exp date": "patpass_expirydt",

    "hospital name": "hospital_shrtnm",
    "patient hospital name": "hospital_shrtnm",

    "department": "hospital_dept",
    "dept": "hospital_dept",
    "patient department": "hospital_dept",
    "hospital department": "hospital_dept",
    "hospital dept": "hospital_dept",

    "doctor's name": "doctor_nm",
    "doctor s name": "doctor_nm",
    "doctors name": "doctor_nm",
    "doctor name": "doctor_nm",
    "patient doctor's name": "doctor_nm",
    "patient doctor s name": "doctor_nm",
    "patient doctors name": "doctor_nm",

    "embassy": "ivac_center",
    "embassy ivac": "ivac_center",
    "embassy ivac center": "ivac_center",
    "ivac": "ivac_center",
    "ivac center": "ivac_center",

    "agency": "agent_shrtnm",
    "agent": "agent_shrtnm",
    "agency name": "agent_shrtnm",
    "agent name": "agent_shrtnm",

    "intent": "_patient_intent",
    "patient intent": "_patient_intent",

    "approximate date": "_patient_approximate_date",
    "patient approximate date": "_patient_approximate_date",
}

LABELS = {
    "patient_name": [
        "patient name", "name", "patient"
    ],
    "passport_no": [
        "passport number", "passport no", "passport no.", "passport"
    ],
    "gender": [
        "gender", "sex"
    ],
    "dob": [
        "dob", "date of birth", "birth date"
    ],
    "age": [
        "age"
    ],
    "contact_number": [
        "contact", "contact number", "contact no", "mobile",
        "phone", "phone number", "mobile number"
    ],
    "department": [
        "department", "dept"
    ],
    "doctor_name": [
        "doctor", "doctor name", "doctor's name"
    ],
    "approximate_date": [
        "approximate date", "travel date", "tentative date", "date"
    ],
    "appointment_date": [
        "appointment date", "appointment dt", "appointment"
    ],
    "nationality": [
        "nationality", "country"
    ],
    "address": [
        "address"
    ],
    "hospital_name": [
        "hospital name", "hospital"
    ],
    "hospital_city": [
        "hospital city", "city"
    ],
    "embassy": [
        "embassy", "ivac", "ivac center", "center"
    ],
    "email": [
        "email", "email id", "mail"
    ],
    "agent_name": [
        "agent", "agency", "agent name", "agency name"
    ],
    "passport_expiry": [
        "passport expiry", "passport expiry date", "passport exp date"
    ],
    "remarks": [
        "remarks", "notes", "message", "comment"
    ],
    "priority": [
        "priority", "urgency"
    ],
    "patient_intent": [
        "patient intent", "intent"
    ]
}


# =========================================================
# Structured parser helpers
# =========================================================
def _starts_with_known_label(line, known_labels):
    if not line:
        return None, None

    normalized_line = _normalize_label(line)
    sorted_labels = sorted(known_labels, key=len, reverse=True)

    for label in sorted_labels:
        if ("passport expiry" in normalized_line or "passport exp date" in normalized_line) and label.endswith("passport number"):
            continue

        if normalized_line == label:
            return label, None

        if normalized_line.startswith(label + " "):
            remainder = normalized_line[len(label):].strip()
            return label, remainder if remainder else None

    return None, None


def _find_embedded_next_label(remainder_text, known_labels):
    if not remainder_text:
        return None, None

    sorted_labels = sorted(known_labels, key=len, reverse=True)
    remainder_text = _normalize_label(remainder_text)

    for label in sorted_labels:
        if remainder_text == label:
            return label, None
        if remainder_text.startswith(label + " "):
            value = remainder_text[len(label):].strip()
            return label, value if value else None

    return None, None


def _parse_whatsapp_label_value_pairs(text):
    result = {}
    if not text:
        return result

    known_labels = set(WHATSAPP_LABEL_TO_COLUMN.keys()) | set(ATTENDER_LABEL_MAP.keys())
    lines = [str(line).replace("\xa0", " ").rstrip() for line in text.split("\n")]

    i = 0
    while i < len(lines):
        raw_line = lines[i]
        line = raw_line.strip()

        if not line:
            i += 1
            continue

        if "\t" in raw_line:
            parts = raw_line.split("\t", 1)
            raw_label = _normalize_label(parts[0])
            raw_value = _safe_str(parts[1]) if len(parts) > 1 else None

            if raw_label in known_labels:
                result[raw_label] = raw_value
                i += 1
                continue

        inline_match = re.match(r"^(.*?)\s*[:=\-]\s*(.*?)\s*$", line)
        if inline_match:
            raw_label = _normalize_label(inline_match.group(1))
            raw_value = _safe_str(inline_match.group(2))

            if raw_label in known_labels:
                result[raw_label] = raw_value
                i += 1
                continue

        matched_label, remainder_value = _starts_with_known_label(line, known_labels)
        if matched_label:
            if remainder_value is not None:
                next_label, next_value = _find_embedded_next_label(remainder_value, known_labels)
                if next_label:
                    result[matched_label] = None
                    result[next_label] = _safe_str(next_value)
                else:
                    result[matched_label] = _safe_str(remainder_value)

                i += 1
                continue

            value = None
            j = i + 1
            while j < len(lines):
                next_raw = lines[j].strip()

                if not next_raw:
                    j += 1
                    continue

                next_label, _ = _starts_with_known_label(next_raw, known_labels)
                if next_label:
                    value = None
                else:
                    value = next_raw
                break

            result[matched_label] = _safe_str(value)
            i += 1
            continue

        i += 1

    return result


def _apply_dynamic_attender_mapping(data, extracted_pairs):
    if not extracted_pairs:
        return data

    for norm_label, value in extracted_pairs.items():
        meta = ATTENDER_LABEL_MAP.get(norm_label)
        if not meta:
            continue

        idx, key = meta
        cleaned_value = _safe_str(_normalize_multiline_spaces(value))

        if key == "givennm":
            _set_if_blank(data, f"attn{idx}_givennm", _safe_str(cleaned_value.upper() if cleaned_value else None))
        elif key == "surnm":
            _set_if_blank(data, f"attn{idx}_surnm", _safe_str(cleaned_value.upper() if cleaned_value else None))
        elif key == "passport_no":
            _set_if_blank(data, f"attn{idx}passport_no", cleaned_value)
        elif key == "gender":
            _set_if_blank(data, f"attn{idx}_gender", _normalize_gender(cleaned_value))
        elif key == "dob":
            _set_if_blank(data, f"attn{idx}_dob", _normalize_date(cleaned_value))
        elif key == "age":
            _set_if_blank(data, f"attn{idx}_age", _to_int(cleaned_value))
        elif key == "address":
            _set_if_blank(data, f"attn{idx}_address", cleaned_value)
        elif key == "contno":
            _set_if_blank(data, f"attn{idx}_contno", cleaned_value)
        elif key == "relation":
            _set_if_blank(data, f"attn{idx}pat_relation", _safe_str(cleaned_value.upper() if cleaned_value else None))
        elif key == "passport_exp":
            _set_if_blank(data, f"attn{idx}pass_expirydt", _normalize_date(cleaned_value))
        elif key == "country":
            _set_if_blank(data, f"attn{idx}_country", _normalize_country(cleaned_value))
        elif key == "email":
            _set_if_blank(data, f"attn{idx}_email", cleaned_value)

    return data


def _apply_structured_whatsapp_mapping(data, extracted_pairs):
    if not extracted_pairs:
        return data

    patient_approximate_date = None
    patient_intent = None

    for norm_label, value in extracted_pairs.items():
        column = WHATSAPP_LABEL_TO_COLUMN.get(norm_label)
        if not column:
            continue

        cleaned_value = _safe_str(_normalize_multiline_spaces(value))

        if column == "_patient_approximate_date":
            patient_approximate_date = cleaned_value
            continue

        if column == "_patient_intent":
            patient_intent = _normalize_intent(cleaned_value)
            continue

        if column == "hospital_shrtnm":
            data[column] = _resolve_hospital_short_name(cleaned_value)
        elif column == "agent_shrtnm":
            validated_agent = _lookup_partner_short_name_by_type(
                "Agency",
                cleaned_value,
            )
            data[column] = _safe_str(
                validated_agent.upper() if validated_agent else None
            )
        elif column == "hospital_dept":
            data[column] = _normalize_department_csv(cleaned_value)
        elif column in ["patgiven_nm", "patsur_nm", "doctor_nm", "ivac_center"]:
            data[column] = _safe_str(cleaned_value.upper() if cleaned_value else None)
        elif column in ["patient_country"]:
            data[column] = _normalize_country(cleaned_value)
        elif column in ["patient_gender"]:
            data[column] = _normalize_gender(cleaned_value)
        elif column in ["patient_dob", "patpass_expirydt"]:
            data[column] = _normalize_date(cleaned_value)
        elif column in ["patient_age"]:
            data[column] = _to_int(cleaned_value)
        else:
            data[column] = cleaned_value

    normalized_patient_approx_date = _normalize_date(patient_approximate_date)

    data["intent_remarks"] = patient_intent

    if patient_intent == "VIL":
        data["vil_date"] = normalized_patient_approx_date
        data["appointment_dt"] = normalized_patient_approx_date
    elif patient_intent == "APPOINTMENT":
        data["appointment_dt"] = normalized_patient_approx_date
    else:
        data["vil_date"] = None
        data["appointment_dt"] = None

    return data


def _count_structured_attenders(data):
    count = 0
    for idx in [1, 2, 3]:
        if (
            data.get(f"attn{idx}_givennm")
            or data.get(f"attn{idx}_surnm")
            or data.get(f"attn{idx}passport_no")
            or data.get(f"attn{idx}_contno")
            or data.get(f"attn{idx}_dob")
            or data.get(f"attn{idx}_age")
            or data.get(f"attn{idx}_address")
            or data.get(f"attn{idx}_country")
            or data.get(f"attn{idx}_gender")
            or data.get(f"attn{idx}_email")
            or data.get(f"attn{idx}pass_expirydt")
            or data.get(f"attn{idx}pat_relation")
        ):
            count += 1
    return count


def _clear_orphan_attender_relations(data):
    for idx in [1, 2, 3]:
        has_real_attender_data = any([
            data.get(f"attn{idx}_givennm"),
            data.get(f"attn{idx}_surnm"),
            data.get(f"attn{idx}passport_no"),
            data.get(f"attn{idx}_contno"),
            data.get(f"attn{idx}_dob"),
            data.get(f"attn{idx}_age"),
            data.get(f"attn{idx}_address"),
            data.get(f"attn{idx}_country"),
            data.get(f"attn{idx}_gender"),
            data.get(f"attn{idx}_email"),
            data.get(f"attn{idx}pass_expirydt"),
        ])

        relation_key = f"attn{idx}pat_relation"
        relation_value = data.get(relation_key)

        if not has_real_attender_data and relation_value:
            suspicious_prefixes = [
                "HOSPITAL NAME",
                "DEPARTMENT",
                "DOCTOR",
                "NATIONALITY",
                "COUNTRY",
                "EMBASSY",
                "INTENT",
                "APPROXIMATE DATE",
                "AGENCY",
                "AGENT",
            ]
            if any(str(relation_value).upper().startswith(prefix) for prefix in suspicious_prefixes):
                data[relation_key] = None

    return data


def _fix_passport_expiry_misplacements(data):
    mappings = [
        ("patpassport_no", "patpass_expirydt"),
        ("attn1passport_no", "attn1pass_expirydt"),
        ("attn2passport_no", "attn2pass_expirydt"),
        ("attn3passport_no", "attn3pass_expirydt"),
    ]

    for passport_key, expiry_key in mappings:
        passport_val = data.get(passport_key)
        expiry_val = data.get(expiry_key)

        if passport_val and not expiry_val and _looks_like_date_text(passport_val):
            data[expiry_key] = _normalize_date(passport_val)
            data[passport_key] = None

    return data


# =========================================================
# Main parser
# =========================================================
def parse_operation_queue_message(raw_message_text, channel_meta=None):
    text = _clean_text(raw_message_text)
    data = _empty_operation_queue_payload()

    channel_meta = channel_meta or {}
    data["channel_message_log_id"] = channel_meta.get("channel_message_log_id")
    data["channel_code"] = channel_meta.get("channel_code")
    data["channel_message_id"] = channel_meta.get("channel_message_id")
    data["channel_thread_id"] = channel_meta.get("channel_thread_id")

    sender_mobile = channel_meta.get("sender_mobile")

    structured_pairs = _parse_whatsapp_label_value_pairs(text)
    whatsapp_parser_logger.info(
        "WHATSAPP_PARSE_STARTED channel_message_log_id=%s channel_code=%s structured_pair_count=%s text_length=%s",
        channel_meta.get("channel_message_log_id"),
        channel_meta.get("channel_code"),
        len(structured_pairs or {}),
        len(text or ""),
    )
    _apply_structured_whatsapp_mapping(data, structured_pairs)
    _apply_dynamic_attender_mapping(data, structured_pairs)

    patient_name = _extract_labeled_value(text, LABELS["patient_name"])
    passport_no = _extract_labeled_value(text, LABELS["passport_no"])
    gender = _extract_labeled_value(text, LABELS["gender"])
    dob = _extract_labeled_value(text, LABELS["dob"])
    age = _extract_labeled_value(text, LABELS["age"])
    contact_number = _extract_labeled_value(text, LABELS["contact_number"])
    department = _extract_labeled_value(text, LABELS["department"])
    doctor_name = _extract_labeled_value(text, LABELS["doctor_name"])
    approximate_date = _extract_labeled_value(text, LABELS["approximate_date"])
    appointment_date = _extract_labeled_value(text, LABELS["appointment_date"])
    nationality = _extract_labeled_value(text, LABELS["nationality"])
    address = _extract_labeled_value(text, LABELS["address"])
    hospital_name = _extract_labeled_value(text, LABELS["hospital_name"])
    hospital_city = _extract_labeled_value(text, LABELS["hospital_city"])
    embassy = _extract_labeled_value(text, LABELS["embassy"])
    email = _extract_labeled_value(text, LABELS["email"])
    agent_name = _extract_labeled_value(text, LABELS["agent_name"])
    passport_expiry = _extract_labeled_value(text, LABELS["passport_expiry"])
    remarks = _extract_labeled_value(text, LABELS["remarks"])
    priority = _extract_labeled_value(text, LABELS["priority"])
    patient_intent = _extract_labeled_value(text, LABELS["patient_intent"])

    if not email:
        email = _extract_email(text)

    if not contact_number:
        contact_number = _extract_phone(text)

    if not passport_no:
        passport_no = _extract_passport(text)

    if not data.get("patgiven_nm") and not data.get("patsur_nm") and patient_name:
        patgiven_nm, patsur_nm = _split_name(_normalize_name(patient_name))
        data["patgiven_nm"] = _safe_str(patgiven_nm)
        data["patsur_nm"] = _safe_str(patsur_nm)

    if not data.get("patpassport_no"):
        data["patpassport_no"] = _safe_str(passport_no)

    if not data.get("patient_dob"):
        data["patient_dob"] = _normalize_date(dob)

    if not data.get("patient_country"):
        data["patient_country"] = _normalize_country(nationality)

    if not data.get("patient_gender"):
        data["patient_gender"] = _normalize_gender(gender)

    if not data.get("patient_contno"):
        data["patient_contno"] = _safe_str(contact_number)

    if not data.get("patient_age"):
        data["patient_age"] = _to_int(age)

    if not data.get("patpass_expirydt"):
        data["patpass_expirydt"] = _normalize_date(passport_expiry)

    if not data.get("patient_address"):
        data["patient_address"] = _safe_str(address)

    if not data.get("patient_email"):
        data["patient_email"] = _safe_str(email)

    if not data.get("hospital_shrtnm"):
        data["hospital_shrtnm"] = _resolve_hospital_short_name(hospital_name)

    if not data.get("hospital_city"):
        data["hospital_city"] = _safe_str(hospital_city.upper() if hospital_city else None)

    if not data.get("hospital_dept"):
        data["hospital_dept"] = _normalize_department_csv(department)

    if not data.get("ivac_center"):
        data["ivac_center"] = _safe_str(embassy.upper() if embassy else None)

    if not data.get("agent_shrtnm"):
        agent_from_db = _lookup_agent_short_name_by_sender_mobile(sender_mobile)
        if agent_from_db:
            data["agent_shrtnm"] = _safe_str(agent_from_db.upper())

    if not data.get("agent_shrtnm") and agent_name:
        validated_agent = _lookup_partner_short_name_by_type(
            "Agency",
            agent_name,
        )
        if validated_agent:
            data["agent_shrtnm"] = _safe_str(validated_agent.upper())

    if not data.get("doctor_nm"):
        data["doctor_nm"] = _safe_str(doctor_name.upper() if doctor_name else None)

    normalized_patient_intent = _normalize_intent(patient_intent)
    if not data.get("intent_remarks"):
        data["intent_remarks"] = normalized_patient_intent

    if not data.get("vil_date") and not data.get("appointment_dt"):
        normalized_approximate_date = _normalize_date(approximate_date)

        if normalized_patient_intent == "VIL":
            data["vil_date"] = normalized_approximate_date
            data["appointment_dt"] = normalized_approximate_date
        elif normalized_patient_intent == "APPOINTMENT":
            data["appointment_dt"] = normalized_approximate_date
        else:
            data["vil_date"] = None
            data["appointment_dt"] = None

    if appointment_date and not data.get("appointment_dt"):
        data["appointment_dt"] = _normalize_date(appointment_date)

    data["wrkflw_remarks"] = _safe_str(remarks if remarks else text[:1000])
    data["priority_status"] = _safe_str(priority.upper() if priority else None)

    attender_sections = extract_attenders(text)

    if len(attender_sections) >= 1:
        _map_attender(data, attender_sections[0], 1)
    if len(attender_sections) >= 2:
        _map_attender(data, attender_sections[1], 2)
    if len(attender_sections) >= 3:
        _map_attender(data, attender_sections[2], 3)

    _clear_orphan_attender_relations(data)
    _fix_passport_expiry_misplacements(data)

    data["attender_count"] = _count_structured_attenders(data)

    parse_remarks_list = []

    essential_fields = [
        data["patgiven_nm"],
        data["patient_contno"] or data["patient_email"],
        data["hospital_shrtnm"] or data["hospital_dept"] or data["doctor_nm"]
    ]

    filled_count = sum(1 for item in essential_fields if item)

    structured_intent = None
    if structured_pairs:
        structured_intent = _normalize_intent(
            structured_pairs.get("intent") or structured_pairs.get("patient intent")
        )

    free_text_intent = normalized_patient_intent
    final_intent = structured_intent or free_text_intent

    if not final_intent:
        parse_remarks_list.append("Intent for request is missing")

    is_whatsapp = _is_whatsapp_channel(data.get("channel_code"))
    agent_shrtnm = _safe_str(data.get("agent_shrtnm"))

    # ---------------------------------------------------------
    # Business-date validation BEFORE Operation Queue routing.
    # Any invalid patient/attender DOB or passport expiry sends
    # the complete parsed record to Manual Review.
    # ---------------------------------------------------------
    date_validation_errors = _validate_queue_identity_dates(data)

    if date_validation_errors:
        data["parse_status"] = "MANUAL_REVIEW"
        parse_remarks_list.extend(date_validation_errors)
    elif is_whatsapp and not agent_shrtnm:
        data["parse_status"] = "MANUAL_REVIEW"
        parse_remarks_list.append(
            "WhatsApp sender could not be mapped to a valid Agency. "
            "Manual review is required for Agent assignment."
        )
    elif filled_count >= 2:
        data["parse_status"] = "PARSED"
    elif text:
        data["parse_status"] = "PARTIAL"
        parse_remarks_list.append("Parser extracted limited fields. Manual review recommended.")
    else:
        data["parse_status"] = "FAILED"
        parse_remarks_list.append("Empty message text.")

    if (
        not parse_remarks_list
        and filled_count >= 2
        and data.get("parse_status") == "PARSED"
    ):
        parse_remarks_list.append("Parser extracted key fields successfully.")

    data["parse_remarks"] = "; ".join(parse_remarks_list) if parse_remarks_list else None

    if data.get("parse_status") != "MANUAL_REVIEW":
        continuity_result = _apply_passport_continuity_before_assignment(data)
        continuity_user = continuity_result.get("assigned_to")

        if continuity_user:
            data["assigned_to"] = _safe_str(continuity_user)
            data["assigned_by"] = "SYSTEM_CONTINUITY"
            data["assigned_at"] = datetime.now()

            existing_parse_remarks = data.get("parse_remarks") or ""
            continuity_note = (
                f"Continuity applied: same passport mapped to existing open thread "
                f"{data.get('channel_thread_id')} and retained same owner {continuity_user}."
            )
            data["parse_remarks"] = f"{existing_parse_remarks}; {continuity_note}".strip("; ").strip()

        # For a genuinely new request, leave assigned_to blank here. The
        # pool-based Round Robin selection is performed by operation_queue_service
        # in the same transaction as the operation_queue_master insert.

    whatsapp_parser_logger.info(
        "WHATSAPP_PARSE_COMPLETED channel_message_log_id=%s parse_status=%s attender_count=%s assigned_to=%s",
        channel_meta.get("channel_message_log_id"),
        data.get("parse_status"),
        data.get("attender_count"),
        data.get("assigned_to"),
    )
    return data


# =========================================================
# Attender parser fallback
# =========================================================
def extract_attenders(text):
    if not text:
        return []

    lines = text.split("\n")
    sections = []
    current = None

    for line in lines:
        raw = line.strip()
        norm = _normalize_label(raw)

        is_attender_header = (
            norm.startswith("attender 1") or norm.startswith("attender1") or
            norm.startswith("attender 2") or norm.startswith("attender2") or
            norm.startswith("attender 3") or norm.startswith("attender3") or
            norm.startswith("attendant 1") or norm.startswith("attendant1") or
            norm.startswith("attendant 2") or norm.startswith("attendant2") or
            norm.startswith("attendant 3") or norm.startswith("attendant3") or
            norm.startswith("companion 1") or norm.startswith("companion1") or
            norm.startswith("companion 2") or norm.startswith("companion2") or
            norm.startswith("companion 3") or norm.startswith("companion3")
        )

        if is_attender_header:
            if current:
                sections.append("\n".join(current))
            current = [raw]
        elif current is not None:
            current.append(raw)

    if current:
        sections.append("\n".join(current))

    return sections[:3]


def _map_attender(data, section_text, idx):
    name = _extract_labeled_value(
        section_text,
        ["name", "attender name", "attendant name", "companion name", "given name", "first name"]
    )
    surname = _extract_labeled_value(
        section_text,
        ["short name", "surname", "sur name", "last name", "family name"]
    )
    passport_no = _extract_labeled_value(section_text, ["passport number", "passport no", "passport"])
    relation = _extract_labeled_value(section_text, ["relation", "relationship", "relationship with patient", "relation with patient"])
    dob = _extract_labeled_value(section_text, ["dob", "date of birth", "birth date"])
    country = _extract_labeled_value(section_text, ["country", "nationality"])
    gender = _extract_labeled_value(section_text, ["gender", "sex"])
    contact = _extract_labeled_value(section_text, ["contact", "contact number", "contact no", "mobile", "phone", "phone number", "mobile number"])
    age = _extract_labeled_value(section_text, ["age"])
    passport_exp = _extract_labeled_value(section_text, ["passport expiry", "passport expiry date", "passport exp date"])
    address = _extract_labeled_value(section_text, ["address"])
    email = _extract_labeled_value(section_text, ["email", "email id", "mail"])

    if not contact:
        contact = _extract_phone(section_text)

    if not email:
        email = _extract_email(section_text)

    givennm = None
    surnm = None

    if name:
        givennm, surnm = _split_name(_normalize_name(name))

    if surname and not surnm:
        surnm = _normalize_name(surname)

    _set_if_blank(data, f"attn{idx}passport_no", _safe_str(passport_no))
    _set_if_blank(data, f"attn{idx}pat_relation", _safe_str(relation.upper() if relation else None))
    _set_if_blank(data, f"attn{idx}_givennm", _safe_str(givennm))
    _set_if_blank(data, f"attn{idx}_surnm", _safe_str(surnm))
    _set_if_blank(data, f"attn{idx}_dob", _normalize_date(dob))
    _set_if_blank(data, f"attn{idx}_country", _normalize_country(country))
    _set_if_blank(data, f"attn{idx}_gender", _normalize_gender(gender))
    _set_if_blank(data, f"attn{idx}_contno", _safe_str(contact))
    _set_if_blank(data, f"attn{idx}_age", _to_int(age))
    _set_if_blank(data, f"attn{idx}pass_expirydt", _normalize_date(passport_exp))
    _set_if_blank(data, f"attn{idx}_address", _safe_str(address))
    _set_if_blank(data, f"attn{idx}_email", _safe_str(email))

    return data
