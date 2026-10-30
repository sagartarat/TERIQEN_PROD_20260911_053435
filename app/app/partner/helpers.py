from app.common.utils import clean


PARTNER_TABLE = "partner_reg"
PARTNER_PK = "short_name"
PARTNER_DOCUMENT_TABLE = "partner_document"

PARTNER_DB_COLS = [
    "partner_type",
    "invoice_eligible",
    "hospital_coordinator",
    "referral_partner",
    "direct_treating_hospital",
    "contstart_date",
    "contend_date",
    "legal_name",
    "short_name",
    "partner_address",
    "partner_country",
    "partner_state",
    "partner_city",
    "partner_pincode",
    "partner_ccy",
    "partner_email",
    "gst_required",
    "gst_number",
    "pan_number",
    "placeof_supply",
    "tax_preference",
    "tds_percentage",
    "agent_percentage",
    "prim_contper",
    "prim_contno",
    "alt_contper",
    "alt_contno",
    "partner_status",
]

PARTNER_RENDER_COMPAT = {
    "partnerType": "partner_type",
    "contract_start": "contstart_date",
    "contract_end": "contend_date",
    "address": "partner_address",
    "country": "partner_country",
    "state": "partner_state",
    "city": "partner_city",
    "pincode": "partner_pincode",
    "currency": "partner_ccy",
    "email": "partner_email",
    "gst": "gst_required",
    "tds": "tds_percentage",
    "agentPercentage": "agent_percentage",
    "agency_percentage": "agent_percentage",
    "agencyPercentage": "agent_percentage",
    "primary_contact_person": "prim_contper",
    "primary_contact_no": "prim_contno",
    "secondary_contact_person": "alt_contper",
    "secondary_contact_no": "alt_contno",
    "tds_percentage": "tds_percentage",
    "agent_percentage": "agent_percentage",
    "partner_name": "legal_name",
}


def fetch_partner_by_short_name(conn, short_name: str):
    cur = conn.cursor(dictionary=True)
    select_cols = ", ".join(PARTNER_DB_COLS)
    cur.execute(
        f"SELECT {select_cols} FROM {PARTNER_TABLE} WHERE {PARTNER_PK} = %s",
        (short_name,),
    )
    row = cur.fetchone()
    cur.close()

    if not row:
        return None

    for k in list(row.keys()):
        if row[k] is None:
            row[k] = ""

    for compat_key, db_key in PARTNER_RENDER_COMPAT.items():
        if compat_key not in row:
            row[compat_key] = row.get(db_key, "")

    return row


def fetch_partners_list(conn):
    cur = conn.cursor(dictionary=True)
    cur.execute(
        f"""
        SELECT
            partner_type      AS partner_type,
            short_name        AS short_name,
            partner_country   AS partner_country,
            prim_contno       AS prim_contno,
            contend_date      AS contend_date,
            gst_required      AS gst_required,
            agent_percentage  AS agent_percentage,
            partner_status    AS partner_status
        FROM {PARTNER_TABLE}
        ORDER BY {PARTNER_PK} ASC
        """
    )
    rows = cur.fetchall() or []
    cur.close()

    for r in rows:
        for k in list(r.keys()):
            if r[k] is None:
                r[k] = ""

    return rows


def fetch_partner_documents(conn, partner_short_name: str):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            f"""
            SELECT
                id,
                partner_short_name,
                original_file_name,
                stored_file_name,
                file_path,
                file_extension,
                mime_type,
                file_size_bytes,
                document_status,
                uploaded_by,
                uploaded_on
            FROM {PARTNER_DOCUMENT_TABLE}
            WHERE partner_short_name = %s
              AND document_status = 'ACTIVE'
            ORDER BY uploaded_on DESC, id DESC
            """,
            (partner_short_name,),
        )
        return cur.fetchall() or []
    finally:
        cur.close()


def fetch_partner_document_by_id(conn, document_id: int):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            f"""
            SELECT
                id,
                partner_short_name,
                original_file_name,
                stored_file_name,
                file_path,
                file_extension,
                mime_type,
                file_size_bytes,
                document_status,
                uploaded_by,
                uploaded_on,
                deleted_by,
                deleted_on
            FROM {PARTNER_DOCUMENT_TABLE}
            WHERE id = %s
            """,
            (document_id,),
        )
        return cur.fetchone()
    finally:
        cur.close()


def insert_partner_document(
    conn,
    partner_short_name: str,
    original_file_name: str,
    stored_file_name: str,
    file_path: str,
    file_extension: str,
    mime_type: str,
    file_size_bytes: int,
    uploaded_by: str,
):
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            INSERT INTO {PARTNER_DOCUMENT_TABLE} (
                partner_short_name,
                original_file_name,
                stored_file_name,
                file_path,
                file_extension,
                mime_type,
                file_size_bytes,
                document_status,
                uploaded_by,
                uploaded_on
            ) VALUES (%s, %s, %s, %s, %s, %s, %s, 'ACTIVE', %s, NOW())
            """,
            (
                partner_short_name,
                original_file_name,
                stored_file_name,
                file_path,
                file_extension,
                mime_type,
                int(file_size_bytes),
                uploaded_by,
            ),
        )
        return cur.lastrowid
    finally:
        cur.close()


def mark_partner_document_deleted(conn, document_id: int, deleted_by: str):
    cur = conn.cursor()
    try:
        cur.execute(
            f"""
            UPDATE {PARTNER_DOCUMENT_TABLE}
            SET document_status = 'DELETED',
                deleted_by = %s,
                deleted_on = NOW()
            WHERE id = %s
              AND document_status = 'ACTIVE'
            """,
            (deleted_by, document_id),
        )
        return cur.rowcount
    finally:
        cur.close()


def fetch_partner_document_paths(conn, partner_short_name: str):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            f"""
            SELECT file_path
            FROM {PARTNER_DOCUMENT_TABLE}
            WHERE partner_short_name = %s
            """,
            (partner_short_name,),
        )
        return [
            str(row.get("file_path") or "").strip()
            for row in (cur.fetchall() or [])
            if str(row.get("file_path") or "").strip()
        ]
    finally:
        cur.close()


def delete_partner_document_rows(conn, partner_short_name: str):
    cur = conn.cursor()
    try:
        cur.execute(
            f"DELETE FROM {PARTNER_DOCUMENT_TABLE} WHERE partner_short_name = %s",
            (partner_short_name,),
        )
        return cur.rowcount
    finally:
        cur.close()
