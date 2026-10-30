import json
from datetime import datetime

from mysql.connector.errors import IntegrityError

from app.common.db import get_db_connection
from app.whatsapp.media_service import download_whatsapp_media
from app.whatsapp.submission_ack_service import (
    register_whatsapp_submission,
    send_processing_wait_message,
)
from app.whatsapp.consent_service import (
    CATEGORY_AGENCY,
    get_valid_current_consent,
    handle_whatsapp_consent_flow,
)



def _debug_print(*args, **kwargs):
    import os
    if str(os.getenv("WHATSAPP_DEBUG_LOGGING", "false")).strip().lower() in {"1", "true", "yes", "on"}:
        print(*args, **kwargs)


def process_whatsapp_webhook(data):
    """
    Process Meta WhatsApp Cloud API webhook payload.
    Handles duplicate Meta webhook retries safely.

    Sender name logic:
    1. First read WhatsApp profile name from webhook contact profile.
    2. Then check partner_reg using sender_mobile = partner_reg.prim_contno.
    3. If partner exists, use partner_reg.legal_name / short_name as sender_name.
    """

    if not data:
        _debug_print("No JSON payload received", flush=True)
        return

    entries = data.get("entry", [])

    if not entries:
        _debug_print("No entry found in webhook payload", flush=True)
        return

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    inserted_count = 0
    duplicate_count = 0
    processed_count = 0

    try:
        for entry in entries:
            changes = entry.get("changes", [])

            for change in changes:
                field = change.get("field")
                value = change.get("value", {})

                _debug_print("Webhook Field:", field, flush=True)

                metadata = value.get("metadata", {})
                display_phone_number = metadata.get("display_phone_number")
                phone_number_id = metadata.get("phone_number_id")

                _debug_print("Display Phone Number:", display_phone_number, flush=True)
                _debug_print("Phone Number ID:", phone_number_id, flush=True)

                contacts = value.get("contacts", [])
                whatsapp_sender_name = ""

                if contacts:
                    whatsapp_sender_name = contacts[0].get("profile", {}).get("name", "")

                messages = value.get("messages", [])

                if not messages:
                    _debug_print("No messages found in webhook payload", flush=True)
                    continue

                for msg in messages:
                    message_log_id = None
                    message_id = msg.get("id")
                    sender_mobile = msg.get("from")
                    message_timestamp = msg.get("timestamp")
                    whatsapp_message_type = msg.get("type")

                    if not message_id:
                        _debug_print("WhatsApp message id missing. Skipping message.", flush=True)
                        continue

                    received_on = get_received_on(message_timestamp)

                    # =====================================================
                    # ATOMIC INBOUND EVENT CLAIM
                    #
                    # Reserve and COMMIT this Meta wamid BEFORE any
                    # customer-facing WhatsApp side effect.
                    #
                    # Database UNIQUE key:
                    #   (channel_code, channel_message_id)
                    #
                    # therefore guarantees that the same inbound Meta event
                    # cannot execute onboarding / parsing twice.
                    # =====================================================
                    message_log_id = claim_channel_message_log(
                        cursor=cursor,
                        message_id=message_id,
                        sender_mobile=sender_mobile,
                        sender_name=whatsapp_sender_name,
                        received_on=received_on,
                    )

                    if message_log_id is None:
                        duplicate_count += 1
                        conn.rollback()
                        _debug_print(
                            "Duplicate WhatsApp webhook skipped after atomic claim. "
                            f"channel_message_id={message_id}",
                            flush=True,
                        )
                        continue

                    # Make the idempotency claim durable before any
                    # consent/onboarding/outbound WhatsApp operation.
                    conn.commit()

                    # =====================================================
                    # DPDP / WHATSAPP CONSENT GATE
                    #
                    # This gate intentionally runs BEFORE:
                    # - patient message content is stored
                    # - WhatsApp medical media is downloaded
                    # - the existing classifier / parser is called
                    #
                    # Existing duplicate protection above remains unchanged.
                    # =====================================================
                    consent_handled = handle_whatsapp_consent_flow(
                        sender_mobile=sender_mobile,
                        channel_message_id=message_id,
                        msg=msg,
                    )

                    # -----------------------------------------------------
                    # Consent / onboarding control message.
                    #
                    # The consent service returns True when this message has
                    # been consumed by the onboarding/consent state machine.
                    # Such a message must not enter the patient parser.
                    # -----------------------------------------------------
                    if consent_handled:
                        message_log_id = insert_consent_control_message_log(
                            cursor=cursor,
                            message_id=message_id,
                            sender_mobile=sender_mobile,
                            sender_name=whatsapp_sender_name,
                            received_on=received_on,
                            whatsapp_message_type=whatsapp_message_type,
                            message_log_id=message_log_id,
                        )

                        inserted_count += 1
                        processed_count += 1
                        conn.commit()

                        _debug_print(
                            "WHATSAPP CONSENT/ONBOARDING MESSAGE PROCESSED | "
                            f"channel_message_log_id={message_log_id} "
                            f"channel_message_id={message_id}",
                            flush=True,
                        )
                        continue

                    # =====================================================
                    # DEFENCE-IN-DEPTH CONSENT CHECK
                    #
                    # If the consent handler returned False, a valid current
                    # Agency consent should exist. Re-check it before allowing
                    # patient data/media processing. If not, fail closed.
                    # =====================================================
                    valid_consent = get_valid_current_consent(
                        cursor=cursor,
                        sender_mobile=sender_mobile,
                        user_category=CATEGORY_AGENCY,
                    )

                    if not valid_consent:
                        message_log_id = insert_consent_control_message_log(
                            cursor=cursor,
                            message_id=message_id,
                            sender_mobile=sender_mobile,
                            sender_name=whatsapp_sender_name,
                            received_on=received_on,
                            whatsapp_message_type=whatsapp_message_type,
                            remarks=(
                                "Patient processing blocked because no valid "
                                "current WhatsApp consent was available."
                            ),
                            message_log_id=message_log_id,
                        )

                        inserted_count += 1
                        processed_count += 1
                        conn.commit()

                        _debug_print(
                            "WHATSAPP PATIENT PROCESSING BLOCKED - "
                            "NO VALID CONSENT",
                            flush=True,
                        )
                        continue

                    # =====================================================
                    # EXISTING BUSINESS FLOW CONTINUES BELOW
                    #
                    # Partner-name resolution remains unchanged.
                    # Message-body extraction remains unchanged.
                    # Media download remains unchanged, but now occurs only
                    # after a valid consent check.
                    # =====================================================
                    sender_name = get_sender_name_from_partner(
                        cursor=cursor,
                        sender_mobile=sender_mobile,
                        fallback_sender_name=whatsapp_sender_name,
                    )

                    message_body = get_message_body(msg, whatsapp_message_type)

                    attachment_count, attachment_json = build_attachment_json(
                        msg,
                        whatsapp_message_type,
                        message_id=message_id,
                    )

                    _debug_print("----------------------------------------", flush=True)
                    _debug_print("WhatsApp Sender Name:", whatsapp_sender_name, flush=True)
                    _debug_print("Final Sender Name:", sender_name, flush=True)
                    _debug_print("Sender Mobile:", sender_mobile, flush=True)
                    _debug_print("Message ID:", message_id, flush=True)
                    _debug_print("Message Timestamp:", message_timestamp, flush=True)
                    _debug_print("WhatsApp Message Type:", whatsapp_message_type, flush=True)
                    _debug_print("Message Body:", message_body, flush=True)
                    _debug_print("Attachment Count:", attachment_count, flush=True)
                    _debug_print("Attachment JSON:", attachment_json, flush=True)
                    _debug_print("----------------------------------------", flush=True)

                    message_log_id = insert_channel_message_log(
                        cursor=cursor,
                        message_id=message_id,
                        sender_mobile=sender_mobile,
                        sender_name=sender_name,
                        message_body=message_body,
                        raw_payload=data,
                        received_on=received_on,
                        attachment_count=attachment_count,
                        attachment_json=attachment_json,
                        message_log_id=message_log_id,
                    )

                    # Link the normal patient/submission message to the exact
                    # consent evidence under which it was processed.
                    cursor.execute(
                        """
                        UPDATE channel_message_log
                        SET consent_id = %s
                        WHERE id = %s
                        """,
                        (
                            valid_consent.get("id"),
                            message_log_id,
                        ),
                    )

                    inserted_count += 1

                    # Commit the safely received WhatsApp event first.  The
                    # submission-window registrar uses its own short transaction
                    # and therefore must be able to see this channel_message_log.
                    conn.commit()

                    # =====================================================
                    # SUBMISSION WINDOW REGISTRATION
                    #
                    # IMPORTANT:
                    # - NO WA Ref is sent here.
                    # - NO parser/classifier is called here.
                    # - First event opens one submission root/window.
                    # - Later events join that same root through linked_triage_id.
                    # - The scheduler waits for the configured quiet window,
                    #   combines all text/files, then classifies/parses once.
                    # =====================================================
                    try:
                        window_result = register_whatsapp_submission(
                            sender_mobile=sender_mobile,
                            channel_message_log_id=message_log_id,
                            received_on=received_on,
                        ) or {}

                        _debug_print(
                            "WHATSAPP SUBMISSION WINDOW RESULT | "
                            f"sender_mobile={sender_mobile} "
                            f"reason={window_result.get('reason')} "
                            f"tracking_id={window_result.get('tracking_id')} "
                            f"root_id={window_result.get('channel_message_log_id')} "
                            f"wa_ref={window_result.get('wa_submission_id')}",
                            flush=True,
                        )

                        # -------------------------------------------------
                        # IMMEDIATE PROCESSING / WAIT MESSAGE
                        # -------------------------------------------------
                        # Send this acknowledgement only when the FIRST event
                        # opens the submission window. Later text/media events
                        # only extend the same window and must not send it again.
                        #
                        # This remains best-effort: an outbound Meta failure
                        # must not roll back the safely committed inbound event.
                        if window_result.get("reason") == "WINDOW_OPENED":
                            wait_result = send_processing_wait_message(
                                sender_mobile=sender_mobile,
                            ) or {}
                            _debug_print(
                                "WHATSAPP PROCESSING WAIT RESULT | "
                                f"sender_mobile={sender_mobile} "
                                f"sent={wait_result.get('sent')} "
                                f"reason={wait_result.get('reason')} "
                                f"message_id={wait_result.get('message_id')}",
                                flush=True,
                            )
                        else:
                            _debug_print(
                                "WHATSAPP PROCESSING WAIT SKIPPED | "
                                f"sender_mobile={sender_mobile} "
                                f"reason={window_result.get('reason')} "
                                f"tracking_id={window_result.get('tracking_id')}",
                                flush=True,
                            )

                    except Exception as submission_window_error:
                        # The inbound message has already been committed and must
                        # not be lost. Surface the error loudly so the scheduler/
                        # operator can repair the tracking row rather than silently
                        # parsing this event as an independent patient submission.
                        _debug_print(
                            "WHATSAPP SUBMISSION WINDOW REGISTER FAILED | "
                            f"sender_mobile={sender_mobile} "
                            f"channel_message_log_id={message_log_id} "
                            f"error={submission_window_error}",
                            flush=True,
                        )
                        raise

                    processed_count += 1

        conn.commit()

        if inserted_count > 0:
            _debug_print(
                f"WHATSAPP MESSAGE INSERTED AND GROUPED | "
                f"inserted={inserted_count} processed={processed_count} duplicates_skipped={duplicate_count}",
                flush=True,
            )
        elif duplicate_count > 0:
            _debug_print(
                f"WHATSAPP DUPLICATE WEBHOOK IGNORED | duplicates_skipped={duplicate_count}",
                flush=True,
            )
        else:
            _debug_print("WHATSAPP WEBHOOK RECEIVED BUT NO MESSAGE INSERTED", flush=True)

    except Exception as e:
        conn.rollback()
        _debug_print("WHATSAPP DB INSERT / SUBMISSION WINDOW ERROR:", str(e), flush=True)
        raise e

    finally:
        cursor.close()
        conn.close()


def normalize_mobile_no(value):
    """
    Normalize mobile number for matching.
    Keeps only digits.
    This helps compare WhatsApp sender number with partner_reg.prim_contno
    even if one side has +, spaces, or hyphen.
    """

    return "".join(ch for ch in str(value or "") if ch.isdigit())


def get_sender_name_from_partner(cursor, sender_mobile, fallback_sender_name=""):
    """
    Find partner name from partner_reg where sender_mobile matches prim_contno.

    Preferred sender_name:
    1. partner_reg.legal_name
    2. partner_reg.short_name
    3. WhatsApp profile name
    4. sender_mobile
    """

    normalized_sender_mobile = normalize_mobile_no(sender_mobile)

    if not normalized_sender_mobile:
        return fallback_sender_name or sender_mobile or ""

    try:
        sql = """
            SELECT
                legal_name,
                short_name,
                prim_contno
            FROM partner_reg
            WHERE partner_status = 'Active'
              AND REPLACE(REPLACE(REPLACE(REPLACE(COALESCE(prim_contno, ''), '+', ''), '-', ''), ' ', ''), CHAR(9), '') = %s
            LIMIT 1
        """

        cursor.execute(sql, (normalized_sender_mobile,))
        partner = cursor.fetchone()

        if partner:
            partner_name = (
                partner.get("legal_name")
                or partner.get("short_name")
                or fallback_sender_name
                or sender_mobile
                or ""
            )

            _debug_print(
                "Partner sender matched:",
                f"sender_mobile={sender_mobile}",
                f"prim_contno={partner.get('prim_contno')}",
                f"sender_name={partner_name}",
                flush=True,
            )

            return partner_name

    except Exception as e:
        _debug_print("Partner sender lookup failed:", str(e), flush=True)

    return fallback_sender_name or sender_mobile or ""


def claim_channel_message_log(
    cursor,
    message_id,
    sender_mobile,
    sender_name,
    received_on,
):
    """
    Atomically claim one inbound Meta WhatsApp message.

    The UNIQUE database key on:
        (channel_code, channel_message_id)

    is the authoritative idempotency boundary.

    The claim is committed before any outbound WhatsApp side effect.
    Later processing updates this SAME channel_message_log row.
    """

    safe_payload = {
        "source": "WHATSAPP",
        "channel_message_id": message_id,
        "receipt_claim": True,
    }

    sql = """
        INSERT INTO channel_message_log
        (
            channel_code,
            channel_message_id,
            channel_thread_id,
            sender_id,
            sender_name,
            sender_mobile,
            sender_email,
            subject_line,
            raw_message_text,
            raw_payload_json,
            attachment_count,
            attachment_json,
            received_on,
            parsed_flag,
            parse_status,
            spam_flag,
            junk_flag,
            duplicate_flag,
            remarks
        )
        VALUES
        (
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
    """

    values = (
        "WHATSAPP",
        message_id,
        None,
        sender_mobile,
        sender_name,
        sender_mobile,
        None,
        None,
        "[WhatsApp inbound event claimed]",
        json.dumps(safe_payload, ensure_ascii=False),
        0,
        None,
        received_on,

        # Fail-closed while business processing is incomplete.
        # This prevents a partially processed consent event being
        # picked up by the normal patient parser.
        1,
        "PARSED",

        0,
        0,
        0,
        "WhatsApp inbound event claimed before business processing.",
    )

    try:
        cursor.execute(sql, values)
        return cursor.lastrowid

    except IntegrityError as exc:
        if getattr(exc, "errno", None) == 1062:
            return None
        raise


def get_existing_channel_message_log(cursor, message_id):
    """
    Return existing channel_message_log row for same WhatsApp message id.
    Prevents duplicate processing when Meta retries same webhook payload.
    """

    sql = """
        SELECT id, channel_message_id, linked_queue_id, parse_status
        FROM channel_message_log
        WHERE channel_code = %s
          AND channel_message_id = %s
        LIMIT 1
    """

    cursor.execute(sql, ("WHATSAPP", message_id))
    return cursor.fetchone()


def get_message_body(msg, message_type):
    """
    Extract readable message body from WhatsApp message object.
    For document/image/video, caption is stored as message body.
    """

    if message_type == "text":
        return msg.get("text", {}).get("body", "")

    if message_type == "document":
        document = msg.get("document", {})
        caption = document.get("caption", "")
        return caption if caption else "[Document message received]"

    if message_type == "image":
        image = msg.get("image", {})
        caption = image.get("caption", "")
        return caption if caption else "[Image message received]"

    if message_type == "video":
        video = msg.get("video", {})
        caption = video.get("caption", "")
        return caption if caption else "[Video message received]"

    if message_type == "audio":
        return "[Audio message received]"

    if message_type == "button":
        return msg.get("button", {}).get("text", "")

    if message_type == "interactive":
        return json.dumps(msg.get("interactive", {}), ensure_ascii=False)

    return f"[{message_type} message received]"


def _guess_extension_from_mime(mime_type):
    mime_type = (mime_type or "").lower().strip()

    mapping = {
        "image/jpeg": "jpg",
        "image/jpg": "jpg",
        "image/png": "png",
        "image/webp": "webp",
        "application/pdf": "pdf",
        "video/mp4": "mp4",
        "audio/ogg": "ogg",
        "audio/mpeg": "mp3",
        "audio/mp4": "m4a",
    }

    return mapping.get(mime_type, "bin")


def _build_fallback_filename(message_type, message_id, mime_type):
    ext = _guess_extension_from_mime(mime_type)
    safe_message_id = str(
        message_id or datetime.now().strftime("%Y%m%d%H%M%S")
    ).replace(":", "_")

    return f"whatsapp_{message_type}_{safe_message_id}.{ext}"


def build_attachment_json(msg, message_type, message_id=None):
    """
    Download WhatsApp media and build attachment_json with local file path.
    """

    if message_type not in ("image", "document", "audio", "video"):
        return 0, None

    media_obj = msg.get(message_type, {})

    if not media_obj:
        return 0, None

    media_id = media_obj.get("id")
    filename = media_obj.get("filename")
    mime_type = media_obj.get("mime_type")

    if not filename:
        filename = _build_fallback_filename(
            message_type=message_type,
            message_id=message_id or msg.get("id"),
            mime_type=mime_type,
        )

    attachment_data = {
        "media_type": message_type,
        "media_id": media_id,
        "filename": filename,
        "mime_type": mime_type,
        "sha256": media_obj.get("sha256"),
        "caption": media_obj.get("caption"),
        "url": media_obj.get("url"),
        "file_name": filename,
        "file_ext": filename.split(".")[-1].lower()
        if "." in filename
        else _guess_extension_from_mime(mime_type),
        "file_size": None,
        "file_path": None,
        "remarks": None,
    }

    try:
        if not media_id:
            raise Exception("WhatsApp media id missing in payload.")

        saved_file = download_whatsapp_media(
            media_id=media_id,
            filename=filename,
            mime_type=mime_type,
        )

        if saved_file:
            attachment_data["file_name"] = (
                saved_file.get("file_name")
                or saved_file.get("filename")
                or filename
            )
            attachment_data["file_ext"] = (
                saved_file.get("file_ext")
                or attachment_data.get("file_ext")
            )
            attachment_data["file_size"] = saved_file.get("file_size")
            attachment_data["file_path"] = (
                saved_file.get("file_path")
                or saved_file.get("path")
                or saved_file.get("local_path")
            )
            attachment_data["mime_type"] = saved_file.get("mime_type") or mime_type
            attachment_data["remarks"] = "WhatsApp media downloaded successfully."

        if not attachment_data.get("file_path"):
            attachment_data["remarks"] = (
                "WhatsApp media received but local file_path was not returned by media_service."
            )

        _debug_print("WHATSAPP MEDIA DOWNLOADED:", attachment_data.get("file_path"), flush=True)

    except Exception as e:
        attachment_data["remarks"] = f"WhatsApp media download failed: {str(e)}"
        _debug_print("WHATSAPP MEDIA DOWNLOAD ERROR:", str(e), flush=True)

    return 1, json.dumps([attachment_data], ensure_ascii=False)


def get_received_on(message_timestamp):
    """
    Convert WhatsApp Unix timestamp to Python datetime.
    """

    try:
        if message_timestamp:
            return datetime.fromtimestamp(int(message_timestamp))
    except Exception:
        pass

    return datetime.now()



def insert_consent_control_message_log(
    cursor,
    message_id,
    sender_mobile,
    sender_name,
    received_on,
    whatsapp_message_type=None,
    remarks=None,
    message_log_id=None,
):
    """
    Store a minimal technical/audit record for a WhatsApp consent or
    onboarding control message.

    Before valid consent, this helper deliberately does not persist:
    - patient message text
    - medical attachment files
    - attachment content
    - the complete raw WhatsApp webhook payload

    The row is marked PARSED so the existing patient parser/scheduler
    does not process it.
    """

    safe_payload = {
        "source": "WHATSAPP",
        "channel_message_id": message_id,
        "message_type": whatsapp_message_type,
        "consent_control_message": True,
    }

    log_remarks = (
        remarks
        or
        "WhatsApp consent/onboarding control message. "
        "Excluded from patient parser."
    )

    if message_log_id is not None:
        sql = """
            UPDATE channel_message_log
            SET sender_id = %s,
                sender_name = %s,
                sender_mobile = %s,
                raw_message_text = %s,
                raw_payload_json = %s,
                attachment_count = 0,
                attachment_json = NULL,
                received_on = %s,
                parsed_flag = 1,
                parse_status = 'PARSED',
                spam_flag = 0,
                junk_flag = 0,
                duplicate_flag = 0,
                remarks = %s
            WHERE id = %s
              AND channel_code = 'WHATSAPP'
              AND channel_message_id = %s
        """

        cursor.execute(
            sql,
            (
                sender_mobile,
                sender_name,
                sender_mobile,
                "[WhatsApp consent/onboarding control message]",
                json.dumps(safe_payload, ensure_ascii=False),
                received_on,
                log_remarks,
                message_log_id,
                message_id,
            ),
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                "Failed to finalize claimed WhatsApp consent message "
                f"id={message_log_id} wamid={message_id}"
            )

        return message_log_id

    sql = """
        INSERT INTO channel_message_log
        (
            channel_code,
            channel_message_id,
            channel_thread_id,
            sender_id,
            sender_name,
            sender_mobile,
            sender_email,
            subject_line,
            raw_message_text,
            raw_payload_json,
            attachment_count,
            attachment_json,
            received_on,
            parsed_flag,
            parse_status,
            spam_flag,
            junk_flag,
            duplicate_flag,
            remarks
        )
        VALUES
        (
            %s, %s, %s, %s, %s, %s, %s, %s, %s,
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
    """

    values = (
        "WHATSAPP",
        message_id,
        None,
        sender_mobile,
        sender_name,
        sender_mobile,
        None,
        None,
        "[WhatsApp consent/onboarding control message]",
        json.dumps(safe_payload, ensure_ascii=False),
        0,
        None,
        received_on,
        1,
        "PARSED",
        0,
        0,
        0,
        log_remarks,
    )

    cursor.execute(sql, values)
    return cursor.lastrowid


def insert_channel_message_log(
    cursor,
    message_id,
    sender_mobile,
    sender_name,
    message_body,
    raw_payload,
    received_on,
    attachment_count=0,
    attachment_json=None,
    message_log_id=None,
):
    """
    Insert one WhatsApp message into channel_message_log.
    Returns inserted channel_message_log.id.
    """

    if message_log_id is not None:
        sql = """
            UPDATE channel_message_log
            SET sender_id = %s,
                sender_name = %s,
                sender_mobile = %s,
                raw_message_text = %s,
                raw_payload_json = %s,
                attachment_count = %s,
                attachment_json = %s,
                received_on = %s,
                parsed_flag = 0,
                parse_status = 'PENDING',
                spam_flag = 0,
                junk_flag = 0,
                duplicate_flag = 0,
                remarks = %s
            WHERE id = %s
              AND channel_code = 'WHATSAPP'
              AND channel_message_id = %s
        """

        cursor.execute(
            sql,
            (
                sender_mobile,
                sender_name,
                sender_mobile,
                message_body,
                json.dumps(raw_payload, ensure_ascii=False),
                attachment_count,
                attachment_json,
                received_on,
                "Received from WhatsApp Cloud API",
                message_log_id,
                message_id,
            ),
        )

        if cursor.rowcount != 1:
            raise RuntimeError(
                "Failed to finalize claimed WhatsApp patient message "
                f"id={message_log_id} wamid={message_id}"
            )

        return message_log_id

    sql = """
        INSERT INTO channel_message_log
        (
            channel_code,
            channel_message_id,
            channel_thread_id,
            sender_id,
            sender_name,
            sender_mobile,
            sender_email,
            subject_line,
            raw_message_text,
            raw_payload_json,
            attachment_count,
            attachment_json,
            received_on,
            parsed_flag,
            parse_status,
            spam_flag,
            junk_flag,
            duplicate_flag,
            remarks
        )
        VALUES
        (
            %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s
        )
    """

    values = (
        "WHATSAPP",
        message_id,
        None,
        sender_mobile,
        sender_name,
        sender_mobile,
        None,
        None,
        message_body,
        json.dumps(raw_payload, ensure_ascii=False),
        attachment_count,
        attachment_json,
        received_on,
        0,
        "PENDING",
        0,
        0,
        0,
        "Received from WhatsApp Cloud API",
    )

    cursor.execute(sql, values)
    return cursor.lastrowid