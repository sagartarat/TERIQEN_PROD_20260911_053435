import re
import datetime
from typing import Any, Dict, Optional

from mysql.connector.connection import MySQLConnection

from app.common.db import get_db
from app.common.db_metadata import get_table_columns
from app.common.utils import (
    clean,
    clean_keep_empty,
    date_to_db,
    _iso,
    calc_age_from_dob,
    first_form_value,
    first_form_list,
)
from app.common.file_helpers import collect_uploaded_files, save_vil_docs
from app.masters.services import (
    fetch_agents as fetch_master_agents,
    fetch_country_code,
    fetch_hospital_city as fetch_master_hospital_city,
    fetch_hospital_depts as fetch_master_hospital_depts,
    fetch_hospitals_with_city,
    fetch_ivacs as fetch_master_ivacs,
)


LEAD_DOC_UPLOAD_BYTES = 10 * 1024 * 1024


def _safe_str(value: Any) -> str:
    return "" if value is None else str(value).strip()


def normalize_status_name(status_value):
    val = (status_value or "").strip().upper()
    val = re.sub(r"\s+", " ", val)

    if val == "FULL PAYMENT RECIEVED":
        val = "FULL PAYMENT RECEIVED"
    return val


def fetch_patients_for_lead(conn):
    cur = conn.cursor(dictionary=True)
    cur.execute(
        """
        SELECT
            passport_no,
            patient_givennm,
            patient_surnm,
            patient_dob,
            patient_country,
            patient_gender,
            passport_expirydt,
            patient_contno,
            patient_email,
            patient_address
        FROM patient_create
        ORDER BY entered_date DESC, passport_no ASC
        """
    )
    rows = cur.fetchall() or []
    cur.close()

    out = []
    for r in rows:
        out.append({
            "passport": (r.get("passport_no") or "").strip(),
            "given": (r.get("patient_givennm") or "").strip(),
            "sur": (r.get("patient_surnm") or "").strip(),
            "dob": _iso(r.get("patient_dob")),
            "country": (r.get("patient_country") or "").strip(),
            "gender": (r.get("patient_gender") or "").strip(),
            "passport_expiry": _iso(r.get("passport_expirydt")),
            "contact": (r.get("patient_contno") or "").strip(),
            "email": (r.get("patient_email") or "").strip(),
            "address": (r.get("patient_address") or "").strip(),
        })
    return out


def fetch_hospitals(conn):
    return fetch_hospitals_with_city(conn)


def fetch_agents(conn):
    return fetch_master_agents(conn)


def fetch_ivacs(conn):
    return fetch_master_ivacs(conn)


def fetch_hospital_city(conn, hospital_short_name: str):
    return fetch_master_hospital_city(conn, hospital_short_name)


def fetch_hospital_depts(conn, hospital_short_name: str):
    return fetch_master_hospital_depts(conn, hospital_short_name)


def fetch_lead_records_for_generation(conn):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT
                ld_internalno,
                ld_seqno,
                vil_internalno,
                patpassport_no,
                patgiven_nm,
                patsur_nm,
                patient_dob,
                patient_country,
                patient_gender,
                patient_contno,
                patient_age,
                patpass_expirydt,
                patient_address,
                patient_email,

                attn1passport_no,
                attn1pat_relation,
                attn1_givennm,
                attn1_surnm,
                attn1_dob,
                attn1_country,
                attn1_gender,
                attn1_contno,
                attn1_age,
                attn1pass_expirydt,
                attn1_address,
                attn1_email,

                attn2passport_no,
                attn2pat_relation,
                attn2_givennm,
                attn2_surnm,
                attn2_dob,
                attn2_country,
                attn2_gender,
                attn2_contno,
                attn2_age,
                attn2pass_expirydt,
                attn2_address,
                attn2_email,

                attn3passport_no,
                attn3pat_relation,
                attn3_givennm,
                attn3_surnm,
                attn3_dob,
                attn3_country,
                attn3_gender,
                attn3_contno,
                attn3_age,
                attn3pass_expirydt,
                attn3_address,
                attn3_email,

                wrkflw_status,
                hospital_shrtnm,
                hospital_city,
                hospital_dept,
                ivac_center,
                agent_shrtnm,
                doctor_nm,
                vil_date,
                appointment_dt,
                wrkflw_remarks,
                ayush_no,
                external_refno,
                patient_uhid,
                entered_by,
                entered_date
            FROM patient_wrkflw
            ORDER BY entered_date DESC, ld_seqno DESC, ld_internalno DESC
            """
        )
        rows = cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass

    out = []
    for r in rows:
        patient_given = clean_keep_empty(r.get("patgiven_nm"))
        patient_sur = clean_keep_empty(r.get("patsur_nm"))
        patient_name = " ".join([x for x in [patient_given, patient_sur] if x]).strip()

        out.append({
            "lead_no": clean_keep_empty(r.get("ld_internalno")),
            "lead_internalno": clean_keep_empty(r.get("ld_internalno")),
            "ld_seqno": clean_keep_empty(r.get("ld_seqno")),
            "vil_internalno": clean_keep_empty(r.get("vil_internalno")),

            "patient_passport": clean_keep_empty(r.get("patpassport_no")),
            "patient_given": patient_given,
            "patient_sur": patient_sur,
            "patient_name": patient_name,
            "patient_dob": _iso(r.get("patient_dob")),
            "patient_country": clean_keep_empty(r.get("patient_country")),
            "patient_gender": clean_keep_empty(r.get("patient_gender")),
            "patient_contact": clean_keep_empty(r.get("patient_contno")),
            "patient_age": clean_keep_empty(r.get("patient_age")),
            "patient_passport_expiry": _iso(r.get("patpass_expirydt")),
            "patient_address": clean_keep_empty(r.get("patient_address")),
            "patient_email": clean_keep_empty(r.get("patient_email")),

            "attn1passport_no": clean_keep_empty(r.get("attn1passport_no")),
            "attn1pat_relation": clean_keep_empty(r.get("attn1pat_relation")),
            "attn1_givennm": clean_keep_empty(r.get("attn1_givennm")),
            "attn1_surnm": clean_keep_empty(r.get("attn1_surnm")),
            "attn1_dob": _iso(r.get("attn1_dob")),
            "attn1_country": clean_keep_empty(r.get("attn1_country")),
            "attn1_gender": clean_keep_empty(r.get("attn1_gender")),
            "attn1_contno": clean_keep_empty(r.get("attn1_contno")),
            "attn1_age": clean_keep_empty(r.get("attn1_age")),
            "attn1pass_expirydt": _iso(r.get("attn1pass_expirydt")),
            "attn1_address": clean_keep_empty(r.get("attn1_address")),
            "attn1_email": clean_keep_empty(r.get("attn1_email")),

            "attn2passport_no": clean_keep_empty(r.get("attn2passport_no")),
            "attn2pat_relation": clean_keep_empty(r.get("attn2pat_relation")),
            "attn2_givennm": clean_keep_empty(r.get("attn2_givennm")),
            "attn2_surnm": clean_keep_empty(r.get("attn2_surnm")),
            "attn2_dob": _iso(r.get("attn2_dob")),
            "attn2_country": clean_keep_empty(r.get("attn2_country")),
            "attn2_gender": clean_keep_empty(r.get("attn2_gender")),
            "attn2_contno": clean_keep_empty(r.get("attn2_contno")),
            "attn2_age": clean_keep_empty(r.get("attn2_age")),
            "attn2pass_expirydt": _iso(r.get("attn2pass_expirydt")),
            "attn2_address": clean_keep_empty(r.get("attn2_address")),
            "attn2_email": clean_keep_empty(r.get("attn2_email")),

            "attn3passport_no": clean_keep_empty(r.get("attn3passport_no")),
            "attn3pat_relation": clean_keep_empty(r.get("attn3pat_relation")),
            "attn3_givennm": clean_keep_empty(r.get("attn3_givennm")),
            "attn3_surnm": clean_keep_empty(r.get("attn3_surnm")),
            "attn3_dob": _iso(r.get("attn3_dob")),
            "attn3_country": clean_keep_empty(r.get("attn3_country")),
            "attn3_gender": clean_keep_empty(r.get("attn3_gender")),
            "attn3_contno": clean_keep_empty(r.get("attn3_contno")),
            "attn3_age": clean_keep_empty(r.get("attn3_age")),
            "attn3pass_expirydt": _iso(r.get("attn3pass_expirydt")),
            "attn3_address": clean_keep_empty(r.get("attn3_address")),
            "attn3_email": clean_keep_empty(r.get("attn3_email")),

            "status": clean_keep_empty(r.get("wrkflw_status")),
            "wrkflw_status": clean_keep_empty(r.get("wrkflw_status")),
            "hospital": clean_keep_empty(r.get("hospital_shrtnm")),
            "hospital_shrtnm": clean_keep_empty(r.get("hospital_shrtnm")),
            "hospital_city": clean_keep_empty(r.get("hospital_city")),
            "hospital_dept": clean_keep_empty(r.get("hospital_dept")),
            "ivac": clean_keep_empty(r.get("ivac_center")),
            "ivac_center": clean_keep_empty(r.get("ivac_center")),
            "agent": clean_keep_empty(r.get("agent_shrtnm")),
            "agent_shrtnm": clean_keep_empty(r.get("agent_shrtnm")),
            "doctor": clean_keep_empty(r.get("doctor_nm")),
            "doctor_nm": clean_keep_empty(r.get("doctor_nm")),
            "vil_date": _iso(r.get("vil_date")),
            "appointment_dt": _iso(r.get("appointment_dt")),
            "remarks": clean_keep_empty(r.get("wrkflw_remarks")),
            "wrkflw_remarks": clean_keep_empty(r.get("wrkflw_remarks")),
            "ayush_no": clean_keep_empty(r.get("ayush_no")),
            "external_refno": clean_keep_empty(r.get("external_refno")),
            "patient_uhid": clean_keep_empty(r.get("patient_uhid")),
            "entered_by": clean_keep_empty(r.get("entered_by")),
            "entered_date": _iso(r.get("entered_date")),
        })

    return out


def get_country_code(conn, country_name):
    return fetch_country_code(conn, clean(country_name))


def generate_next_lead_seqno(conn: MySQLConnection) -> int:
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT COALESCE(MAX(ld_seqno), 0) AS max_seq
            FROM patient_wrkflw
            """
        )
        row = cur.fetchone() or {}
        max_seq = row.get("max_seq") or 0
        return int(max_seq) + 1
    finally:
        try:
            cur.close()
        except Exception:
            pass


def generate_next_vil_seq(conn: MySQLConnection) -> int:
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT vil_internalno
            FROM patient_wrkflw
            WHERE vil_internalno IS NOT NULL
              AND TRIM(vil_internalno) <> ''
            """
        )
        rows = cur.fetchall() or []

        max_seq = 0
        for row in rows:
            vil_no = (row.get("vil_internalno") or "").strip()
            if not vil_no:
                continue

            parts = vil_no.split("-")
            if not parts:
                continue

            last_part = (parts[-1] or "").strip()
            if last_part.isdigit():
                seq_val = int(last_part)
                if seq_val > max_seq:
                    max_seq = seq_val

        return max_seq + 1
    finally:
        try:
            cur.close()
        except Exception:
            pass


def get_next_ld_seqno(conn):
    return generate_next_lead_seqno(conn)


def get_next_vil_running_seq(conn):
    return generate_next_vil_seq(conn)


def build_ld_internalno(country_code, ld_seqno):
    return f"LD-{country_code}-{int(ld_seqno):05d}"


def build_vil_internalno(agent_shrtnm, country_code, vil_seq):
    agent = re.sub(r"\s+", "", (agent_shrtnm or "").strip())
    if not agent:
        agent = "NA"

    ccode = re.sub(r"[^A-Z0-9]", "", (country_code or "").strip().upper())
    if not ccode:
        ccode = "NA"

    return f"VIL-{agent}-{ccode}-{int(vil_seq):06d}"


def collect_attender(prefix):
    passport = clean(first_form_value(
        f"{prefix}_passport_no", f"{prefix}passport_no", f"{prefix}_passport", f"{prefix}passport"
    ))
    relation = clean(first_form_value(
        f"{prefix}_relation", f"{prefix}pat_relation", f"{prefix}_pat_relation"
    ))
    given = clean(first_form_value(
        f"{prefix}_givennm", f"{prefix}_given_name", f"{prefix}_given"
    ))
    sur = clean(first_form_value(
        f"{prefix}_surnm", f"{prefix}_sur_name", f"{prefix}_sur", f"{prefix}_short_name"
    ))
    dob = date_to_db(first_form_value(f"{prefix}_dob"))
    country = clean(first_form_value(f"{prefix}_country"))
    gender = clean(first_form_value(f"{prefix}_gender"))
    contno = clean(first_form_value(f"{prefix}_contno", f"{prefix}_contact", f"{prefix}_contact_no"))
    pass_exp = date_to_db(first_form_value(
        f"{prefix}pass_expirydt", f"{prefix}_pass_expirydt", f"{prefix}_passport_expiry", f"{prefix}_passport_expirydt"
    ))
    address = clean(first_form_value(f"{prefix}_address"))
    email = clean(first_form_value(f"{prefix}_email"))
    age = calc_age_from_dob(dob)

    has_any = any([
        passport, relation, given, sur, dob, country, gender, contno, pass_exp, address, email
    ])

    if not has_any:
        return {
            "passport_no": None,
            "relation": None,
            "givennm": None,
            "surnm": None,
            "dob": None,
            "country": None,
            "gender": None,
            "contno": None,
            "age": None,
            "pass_expirydt": None,
            "address": None,
            "email": None,
        }

    return {
        "passport_no": passport,
        "relation": relation,
        "givennm": given,
        "surnm": sur,
        "dob": dob,
        "country": country,
        "gender": gender,
        "contno": contno,
        "age": age,
        "pass_expirydt": pass_exp,
        "address": address,
        "email": email,
    }


def check_patient_workflow_duplicate(conn, passport_no):
    passport_no = clean(passport_no)
    if not passport_no:
        return False, "Patient Passport No is required."

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT
                pw.wrkflw_status,
                wr.allow_new_case_same_passport
            FROM patient_wrkflw pw
            LEFT JOIN wrkflwrule_master wr
              ON UPPER(TRIM(wr.status_name)) =
                 UPPER(TRIM(pw.wrkflw_status))
            WHERE pw.patpassport_no = %s
            ORDER BY pw.entered_date DESC, pw.ld_seqno DESC
            LIMIT 1
            """,
            (passport_no,),
        )
        row = cur.fetchone()
    finally:
        cur.close()

    if not row:
        return True, ""

    latest_status = normalize_status_name(
        (row or {}).get("wrkflw_status")
    )

    allow_new_case = (
        int(
            (row or {}).get(
                "allow_new_case_same_passport"
            ) or 0
        ) == 1
    )

    if allow_new_case:
        return True, ""

    return (
        False,
        "Already a Request is available for the patient. "
        f"Current workflow status '{latest_status or 'UNKNOWN'}' "
        "does not permit creation of a new case."
    )


def _get_table_columns(conn, table_name):
    try:
        return get_table_columns(conn, table_name)
    except Exception:
        return set()


def _clean_payload_value(payload, key):
    return clean(payload.get(key))


def _date_payload_value(payload, key):
    return date_to_db(payload.get(key))


def _derive_workflow_status(explicit_status, vil_date):
    explicit_status = normalize_status_name(explicit_status)
    if explicit_status:
        return explicit_status
    return "VIL REQUESTED" if vil_date else "LEAD GENERATED"


def insert_patient_workflow_from_payload(conn, payload, created_by):
    patpassport_no = _clean_payload_value(payload, "patpassport_no")
    patgiven_nm = _clean_payload_value(payload, "patgiven_nm")
    patsur_nm = _clean_payload_value(payload, "patsur_nm")
    patient_dob = _date_payload_value(payload, "patient_dob")
    patient_country = _clean_payload_value(payload, "patient_country")
    patient_gender = _clean_payload_value(payload, "patient_gender")
    patient_contno = _clean_payload_value(payload, "patient_contno")
    patient_age = payload.get("patient_age")
    if patient_age in ("", None):
        patient_age = calc_age_from_dob(patient_dob)
    patpass_expirydt = _date_payload_value(payload, "patpass_expirydt")
    patient_address = _clean_payload_value(payload, "patient_address")
    patient_email = _clean_payload_value(payload, "patient_email")

    attn1 = {
        "passport_no": _clean_payload_value(payload, "attn1passport_no"),
        "relation": _clean_payload_value(payload, "attn1pat_relation"),
        "givennm": _clean_payload_value(payload, "attn1_givennm"),
        "surnm": _clean_payload_value(payload, "attn1_surnm"),
        "dob": _date_payload_value(payload, "attn1_dob"),
        "country": _clean_payload_value(payload, "attn1_country"),
        "gender": _clean_payload_value(payload, "attn1_gender"),
        "contno": _clean_payload_value(payload, "attn1_contno"),
        "age": payload.get("attn1_age") if payload.get("attn1_age") not in ("", None) else calc_age_from_dob(_date_payload_value(payload, "attn1_dob")),
        "pass_expirydt": _date_payload_value(payload, "attn1pass_expirydt"),
        "address": _clean_payload_value(payload, "attn1_address"),
        "email": _clean_payload_value(payload, "attn1_email"),
    }

    attn2 = {
        "passport_no": _clean_payload_value(payload, "attn2passport_no"),
        "relation": _clean_payload_value(payload, "attn2pat_relation"),
        "givennm": _clean_payload_value(payload, "attn2_givennm"),
        "surnm": _clean_payload_value(payload, "attn2_surnm"),
        "dob": _date_payload_value(payload, "attn2_dob"),
        "country": _clean_payload_value(payload, "attn2_country"),
        "gender": _clean_payload_value(payload, "attn2_gender"),
        "contno": _clean_payload_value(payload, "attn2_contno"),
        "age": payload.get("attn2_age") if payload.get("attn2_age") not in ("", None) else calc_age_from_dob(_date_payload_value(payload, "attn2_dob")),
        "pass_expirydt": _date_payload_value(payload, "attn2pass_expirydt"),
        "address": _clean_payload_value(payload, "attn2_address"),
        "email": _clean_payload_value(payload, "attn2_email"),
    }

    attn3 = {
        "passport_no": _clean_payload_value(payload, "attn3passport_no"),
        "relation": _clean_payload_value(payload, "attn3pat_relation"),
        "givennm": _clean_payload_value(payload, "attn3_givennm"),
        "surnm": _clean_payload_value(payload, "attn3_surnm"),
        "dob": _date_payload_value(payload, "attn3_dob"),
        "country": _clean_payload_value(payload, "attn3_country"),
        "gender": _clean_payload_value(payload, "attn3_gender"),
        "contno": _clean_payload_value(payload, "attn3_contno"),
        "age": payload.get("attn3_age") if payload.get("attn3_age") not in ("", None) else calc_age_from_dob(_date_payload_value(payload, "attn3_dob")),
        "pass_expirydt": _date_payload_value(payload, "attn3pass_expirydt"),
        "address": _clean_payload_value(payload, "attn3_address"),
        "email": _clean_payload_value(payload, "attn3_email"),
    }

    hospital_shrtnm = _clean_payload_value(payload, "hospital_shrtnm")
    hospital_city = _clean_payload_value(payload, "hospital_city")
    if not hospital_city and hospital_shrtnm:
        hospital_city = clean(fetch_hospital_city(conn, hospital_shrtnm))

    hospital_dept = _clean_payload_value(payload, "hospital_dept")
    ivac_center = _clean_payload_value(payload, "ivac_center")
    agent_shrtnm = _clean_payload_value(payload, "agent_shrtnm")
    doctor_nm = _clean_payload_value(payload, "doctor_nm")
    vil_date = _date_payload_value(payload, "vil_date")
    appointment_dt = _date_payload_value(payload, "appointment_dt")
    wrkflw_remarks = _clean_payload_value(payload, "wrkflw_remarks")
    ayush_no = _clean_payload_value(payload, "ayush_no")
    external_refno = _clean_payload_value(payload, "external_refno")
    patient_uhid = _clean_payload_value(payload, "patient_uhid")

    required_fields = [
        ("Patient Passport No", patpassport_no),
        ("Patient Given Name", patgiven_nm),
        ("Patient DOB", patient_dob),
        ("Patient Country", patient_country),
        ("Patient Gender", patient_gender),
        ("Hospital", hospital_shrtnm),
        ("IVAC", ivac_center),
        ("Agent", agent_shrtnm),
        ("Doctor", doctor_nm),
    ]
    missing = [label for label, val in required_fields if not val]
    if missing:
        raise ValueError("Please fill mandatory fields: " + ", ".join(missing))

    is_allowed, duplicate_message = check_patient_workflow_duplicate(conn, patpassport_no)
    if not is_allowed:
        raise ValueError(duplicate_message)

    wrkflw_status = _derive_workflow_status(payload.get("wrkflw_status"), vil_date)

    ld_seqno = get_next_ld_seqno(conn)
    country_code = get_country_code(conn, patient_country)
    ld_internalno = build_ld_internalno(country_code, ld_seqno)

    vil_internalno = None
    if wrkflw_status in {"VIL REQUESTED", "VIL RE-REQUESTED"}:
        vil_seq = get_next_vil_running_seq(conn)
        vil_internalno = build_vil_internalno(agent_shrtnm, country_code, vil_seq)

    entered_by = clean(created_by) or "SYSTEM"
    entered_date = datetime.datetime.now()

    full_payload = {
        "patpassport_no": patpassport_no,
        "patgiven_nm": patgiven_nm,
        "patsur_nm": patsur_nm,
        "patient_dob": patient_dob,
        "patient_country": patient_country,
        "patient_gender": patient_gender,
        "patient_contno": patient_contno,
        "patient_age": patient_age,
        "patpass_expirydt": patpass_expirydt,
        "patient_address": patient_address,
        "patient_email": patient_email,

        "attn1passport_no": attn1["passport_no"],
        "attn1pat_relation": attn1["relation"],
        "attn1_givennm": attn1["givennm"],
        "attn1_surnm": attn1["surnm"],
        "attn1_dob": attn1["dob"],
        "attn1_country": attn1["country"],
        "attn1_gender": attn1["gender"],
        "attn1_contno": attn1["contno"],
        "attn1_age": attn1["age"],
        "attn1pass_expirydt": attn1["pass_expirydt"],
        "attn1_address": attn1["address"],
        "attn1_email": attn1["email"],

        "attn2passport_no": attn2["passport_no"],
        "attn2pat_relation": attn2["relation"],
        "attn2_givennm": attn2["givennm"],
        "attn2_surnm": attn2["surnm"],
        "attn2_dob": attn2["dob"],
        "attn2_country": attn2["country"],
        "attn2_gender": attn2["gender"],
        "attn2_contno": attn2["contno"],
        "attn2_age": attn2["age"],
        "attn2pass_expirydt": attn2["pass_expirydt"],
        "attn2_address": attn2["address"],
        "attn2_email": attn2["email"],

        "attn3passport_no": attn3["passport_no"],
        "attn3pat_relation": attn3["relation"],
        "attn3_givennm": attn3["givennm"],
        "attn3_surnm": attn3["surnm"],
        "attn3_dob": attn3["dob"],
        "attn3_country": attn3["country"],
        "attn3_gender": attn3["gender"],
        "attn3_contno": attn3["contno"],
        "attn3_age": attn3["age"],
        "attn3pass_expirydt": attn3["pass_expirydt"],
        "attn3_address": attn3["address"],
        "attn3_email": attn3["email"],

        "wrkflw_status": wrkflw_status,
        "hospital_shrtnm": hospital_shrtnm,
        "hospital_city": hospital_city,
        "hospital_dept": hospital_dept,
        "ivac_center": ivac_center,
        "agent_shrtnm": agent_shrtnm,
        "doctor_nm": doctor_nm,
        "vil_date": vil_date,
        "appointment_dt": appointment_dt,
        "wrkflw_remarks": wrkflw_remarks,
        "ayush_no": ayush_no,
        "external_refno": external_refno,
        "patient_uhid": patient_uhid,
        "ld_seqno": ld_seqno,
        "ld_internalno": ld_internalno,
        "vil_internalno": vil_internalno,
        "entered_by": entered_by,
        "entered_date": entered_date,
    }

    table_columns = _get_table_columns(conn, "patient_wrkflw")
    insert_payload = {k: v for k, v in full_payload.items() if k in table_columns}

    if "ld_seqno" not in insert_payload or "ld_internalno" not in insert_payload:
        raise ValueError("patient_wrkflw table is missing ld_seqno / ld_internalno columns.")

    columns_sql = ", ".join(insert_payload.keys())
    placeholders = ", ".join(["%s"] * len(insert_payload))
    values = list(insert_payload.values())

    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            INSERT INTO patient_wrkflw ({columns_sql})
            VALUES ({placeholders})
            """,
            values,
        )
    finally:
        cur.close()

    return {
        "patpassport_no": patpassport_no,
        "ld_seqno": ld_seqno,
        "ld_internalno": ld_internalno,
        "vil_internalno": vil_internalno,
        "wrkflw_status": wrkflw_status,
        "entered_by": entered_by,
        "entered_date": entered_date.strftime("%Y-%m-%d %H:%M:%S"),
    }


def get_existing_lead_for_queue(
    conn: MySQLConnection,
    queue_id: int,
) -> Optional[Dict[str, Any]]:
    _ = conn
    _ = queue_id
    return None


def create_lead_from_queue(
    conn: MySQLConnection,
    queue_row: Dict[str, Any],
    user_code: str,
) -> Dict[str, Any]:
    _ = conn
    _ = queue_row
    _ = user_code

    return {
        "success": True,
        "created": False,
        "lead_id": None,
        "ld_seqno": None,
        "vil_seq": None,
        "message": "lead_create table related code has been removed. No lead row created from queue.",
    }


def insert_patient_workflow(conn, created_by):
    patpassport_no = clean(first_form_value(
        "patpassport_no", "patient_passport_no", "patient_passport", "passport_no", "selected_patient_passport"
    ))
    patgiven_nm = clean(first_form_value(
        "patgiven_nm", "patient_given_name", "patient_given", "given_name", "patient_givennm"
    ))
    patsur_nm = clean(first_form_value(
        "patsur_nm", "patient_sur_name", "patient_surname", "sur_name", "patient_surnm", "short_name"
    ))
    patient_dob = date_to_db(first_form_value("patient_dob", "dob"))
    patient_country = clean(first_form_value("patient_country", "country"))
    patient_gender = clean(first_form_value("patient_gender", "gender"))
    patient_contno = clean(first_form_value("patient_contno", "contact_no", "patient_contact", "contact"))
    patient_age = calc_age_from_dob(patient_dob)
    patpass_expirydt = date_to_db(first_form_value(
        "patpass_expirydt", "patient_passport_expiry", "passport_expiry", "passport_expirydt"
    ))
    patient_address = clean(first_form_value("patient_address", "address"))
    patient_email = clean(first_form_value("patient_email", "email"))

    attn1 = collect_attender("attn1")
    attn2 = collect_attender("attn2")
    attn3 = collect_attender("attn3")

    hospital_shrtnm = clean(first_form_value("hospital_shrtnm", "hospital"))
    hospital_city = clean(first_form_value("hospital_city"))
    if not hospital_city and hospital_shrtnm:
        hospital_city = clean(fetch_hospital_city(conn, hospital_shrtnm))

    hospital_dept_list = first_form_list("hospital_dept", "hospital_dept[]", "departments", "dept")
    if hospital_dept_list:
        hospital_dept = ", ".join(hospital_dept_list)
    else:
        hospital_dept = clean(first_form_value("hospital_dept", "departments", "dept"))

    ivac_center = clean(first_form_value("ivac_center", "ivac"))
    agent_shrtnm = clean(first_form_value("agent_shrtnm", "agent"))
    doctor_nm = clean(first_form_value("doctor_nm", "doctor"))
    vil_date = date_to_db(first_form_value("vil_date"))
    appointment_dt = date_to_db(first_form_value("appointment_dt", "appointment_date"))
    wrkflw_remarks = clean(first_form_value("wrkflw_remarks", "remarks"))

    payload = {
        "patpassport_no": patpassport_no,
        "patgiven_nm": patgiven_nm,
        "patsur_nm": patsur_nm,
        "patient_dob": patient_dob,
        "patient_country": patient_country,
        "patient_gender": patient_gender,
        "patient_contno": patient_contno,
        "patient_age": patient_age,
        "patpass_expirydt": patpass_expirydt,
        "patient_address": patient_address,
        "patient_email": patient_email,

        "attn1passport_no": attn1["passport_no"],
        "attn1pat_relation": attn1["relation"],
        "attn1_givennm": attn1["givennm"],
        "attn1_surnm": attn1["surnm"],
        "attn1_dob": attn1["dob"],
        "attn1_country": attn1["country"],
        "attn1_gender": attn1["gender"],
        "attn1_contno": attn1["contno"],
        "attn1_age": attn1["age"],
        "attn1pass_expirydt": attn1["pass_expirydt"],
        "attn1_address": attn1["address"],
        "attn1_email": attn1["email"],

        "attn2passport_no": attn2["passport_no"],
        "attn2pat_relation": attn2["relation"],
        "attn2_givennm": attn2["givennm"],
        "attn2_surnm": attn2["surnm"],
        "attn2_dob": attn2["dob"],
        "attn2_country": attn2["country"],
        "attn2_gender": attn2["gender"],
        "attn2_contno": attn2["contno"],
        "attn2_age": attn2["age"],
        "attn2pass_expirydt": attn2["pass_expirydt"],
        "attn2_address": attn2["address"],
        "attn2_email": attn2["email"],

        "attn3passport_no": attn3["passport_no"],
        "attn3pat_relation": attn3["relation"],
        "attn3_givennm": attn3["givennm"],
        "attn3_surnm": attn3["surnm"],
        "attn3_dob": attn3["dob"],
        "attn3_country": attn3["country"],
        "attn3_gender": attn3["gender"],
        "attn3_contno": attn3["contno"],
        "attn3_age": attn3["age"],
        "attn3pass_expirydt": attn3["pass_expirydt"],
        "attn3_address": attn3["address"],
        "attn3_email": attn3["email"],

        "hospital_shrtnm": hospital_shrtnm,
        "hospital_city": hospital_city,
        "hospital_dept": hospital_dept,
        "ivac_center": ivac_center,
        "agent_shrtnm": agent_shrtnm,
        "doctor_nm": doctor_nm,
        "vil_date": vil_date,
        "appointment_dt": appointment_dt,
        "wrkflw_remarks": wrkflw_remarks,
    }

    return insert_patient_workflow_from_payload(conn, payload, created_by)


def save_lead_documents(conn, passport_no):
    lead_doc_rows = collect_uploaded_files(
        field_names=["lead_documents", "docFiles"],
        total_limit_bytes=LEAD_DOC_UPLOAD_BYTES,
    )
    return save_vil_docs(conn, passport_no, lead_doc_rows)


def get_patient_docs(passport):
    if not passport:
        return []

    conn = None
    try:
        conn = get_db()
        cur = conn.cursor(dictionary=True)
        cur.execute(
            """
            SELECT file_name, file_type, file_size
            FROM patientcreate_docs
            WHERE passport_no = %s
            ORDER BY file_name ASC
            """,
            (passport,),
        )
        rows = cur.fetchall() or []
        cur.close()

        for r in rows:
            r["file_name"] = r.get("file_name") or ""
            r["file_type"] = r.get("file_type") or ""
            r["file_size"] = r.get("file_size") or 0

        return rows
    finally:
        try:
            if conn:
                conn.close()
        except Exception:
            pass
