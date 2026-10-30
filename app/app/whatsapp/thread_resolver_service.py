import re


CLOSED_STATUSES = (
    "CLOSED",
    "CANCEL",
    "NO PAYMENT",
    "NOT INTERESTED WITHOUT PAY"
)


def resolve_existing_thread(
    cursor,
    sender_mobile=None,
    message_body=None,
    channel_thread_id=None
):
    """
    Find existing open operation_queue_master record for:
    1. Follow-up text
    2. Attachment-only WhatsApp message
    3. Structured WhatsApp request messages

    Matching priority:
    1. channel_thread_id
    2. passport number from message text
    3. sender mobile against patient/attender contact numbers

    Important rule:
    - If the latest matching record is CLOSED, system should NOT reuse it.
    - Parser should then create a new separate operation_queue_master record.
    """

    if channel_thread_id:
        result = find_by_channel_thread_id(cursor, channel_thread_id)
        if result:
            return result

    passport_no = extract_passport_no(message_body)
    if passport_no:
        result = find_by_passport_no(cursor, passport_no)
        if result:
            return result

    if sender_mobile:
        result = find_latest_open_queue_by_mobile(cursor, sender_mobile)
        if result:
            return result

    return None


def extract_passport_no(text):
    """
    Extract probable passport number from WhatsApp text.

    Supported examples:
    - Patient Passport Number A000001
    - Patient Passport Number: A000001
    - Patient Passport No - A000001
    - Passport A000001
    - Pass No A000001
    """

    if not text:
        return None

    text_upper = str(text).upper()

    patterns = [
        r"PATIENT\s+PASSPORT\s*(NO|NUMBER|#)?\s*[:\-]?\s*([A-Z]{1,3}[A-Z0-9]{5,14})\b",
        r"PASSPORT\s*(NO|NUMBER|#)?\s*[:\-]?\s*([A-Z]{1,3}[A-Z0-9]{5,14})\b",
        r"PASS\s*(NO|NUMBER|#)?\s*[:\-]?\s*([A-Z]{1,3}[A-Z0-9]{5,14})\b",
    ]

    for pattern in patterns:
        match = re.search(pattern, text_upper)
        if match:
            return match.group(2).strip()

    return None


def is_closed_status(wrkflw_status):
    status = (wrkflw_status or "").strip().upper()
    return status == "CLOSED"


def find_by_channel_thread_id(cursor, channel_thread_id):
    """
    Reuse thread only if latest matching channel_thread_id is not CLOSED.
    If CLOSED, return None so new queue row can be created.
    """

    sql = """
        SELECT
            id AS operation_queue_id,
            channel_thread_id,
            patpassport_no,
            wrkflw_status
        FROM operation_queue_master
        WHERE UPPER(TRIM(channel_thread_id)) = UPPER(TRIM(%s))
        ORDER BY id DESC
        LIMIT 1
    """

    cursor.execute(sql, (channel_thread_id,))
    row = fetch_one_dict(cursor)

    if not row:
        return None

    if is_closed_status(row.get("wrkflw_status")):
        return None

    return row


def find_by_passport_no(cursor, passport_no):
    """
    Reuse same passport record only if latest matching record is not CLOSED.
    If latest same-passport record is CLOSED, return None so parser creates
    a new separate operation_queue_master record.
    """

    sql = """
        SELECT
            id AS operation_queue_id,
            channel_thread_id,
            patpassport_no,
            wrkflw_status
        FROM operation_queue_master
        WHERE UPPER(TRIM(patpassport_no)) = UPPER(TRIM(%s))
          AND COALESCE(channel_thread_id, '') <> ''
        ORDER BY id DESC
        LIMIT 1
    """

    cursor.execute(sql, (passport_no,))
    row = fetch_one_dict(cursor)

    if not row:
        return None

    if is_closed_status(row.get("wrkflw_status")):
        return None

    return row


def find_latest_open_queue_by_mobile(cursor, sender_mobile):
    """
    Reuse mobile-based existing thread only if latest matching record is not CLOSED.
    If CLOSED, return None so new queue row can be created.
    """

    sql = """
        SELECT
            id AS operation_queue_id,
            channel_thread_id,
            patpassport_no,
            wrkflw_status
        FROM operation_queue_master
        WHERE (
                REPLACE(REPLACE(REPLACE(patient_contno, '+', ''), '-', ''), ' ', '') =
                REPLACE(REPLACE(REPLACE(%s, '+', ''), '-', ''), ' ', '')

             OR REPLACE(REPLACE(REPLACE(attn1_contno, '+', ''), '-', ''), ' ', '') =
                REPLACE(REPLACE(REPLACE(%s, '+', ''), '-', ''), ' ', '')

             OR REPLACE(REPLACE(REPLACE(attn2_contno, '+', ''), '-', ''), ' ', '') =
                REPLACE(REPLACE(REPLACE(%s, '+', ''), '-', ''), ' ', '')

             OR REPLACE(REPLACE(REPLACE(attn3_contno, '+', ''), '-', ''), ' ', '') =
                REPLACE(REPLACE(REPLACE(%s, '+', ''), '-', ''), ' ', '')
          )
          AND COALESCE(channel_thread_id, '') <> ''
        ORDER BY id DESC
        LIMIT 1
    """

    cursor.execute(sql, (
        sender_mobile,
        sender_mobile,
        sender_mobile,
        sender_mobile
    ))

    row = fetch_one_dict(cursor)

    if not row:
        return None

    if is_closed_status(row.get("wrkflw_status")):
        return None

    return row


def fetch_one_dict(cursor):
    row = cursor.fetchone()

    if not row:
        return None

    if isinstance(row, dict):
        return row

    columns = cursor.column_names
    return dict(zip(columns, row))