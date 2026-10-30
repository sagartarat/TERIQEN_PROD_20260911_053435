import hashlib
import logging
import os
import secrets
from datetime import datetime, timedelta

from flask import current_app, url_for

from app.common.db import get_db_connection
from app.whatsapp.outbound_service import (
    extract_outbound_message_id,
    send_cta_url_button,
    send_reply_buttons,
    send_text,
)


logger = logging.getLogger(__name__)


# ============================================================
# USER CATEGORIES
# ============================================================

CATEGORY_AGENCY = "MEDICAL_TOURISM_AGENCY"
CATEGORY_PATIENT = "PATIENT"


# ============================================================
# CONVERSATION STATES
# ============================================================

STATE_NEW = "NEW"
STATE_AWAITING_CATEGORY = "AWAITING_CATEGORY"
STATE_AWAITING_CONSENT = "AWAITING_CONSENT"
STATE_READY_FOR_SUBMISSION = "READY_FOR_SUBMISSION"
STATE_RECONSENT_REQUIRED = "RECONSENT_REQUIRED"

# The legacy AWAITING_CATEGORY database state is reused as the language-choice
# gate so no existing conversation_state definition needs to be widened.
STATE_AWAITING_LANGUAGE = STATE_AWAITING_CATEGORY

LANGUAGE_ENGLISH = "ENGLISH"
LANGUAGE_BENGALI = "BENGALI"


# ============================================================
# WHATSAPP BUTTON ACTION IDS
# ============================================================

ACTION_ROLE_AGENCY = "ROLE_AGENCY"
ACTION_LANGUAGE_ENGLISH = "LANG_EN"
ACTION_LANGUAGE_BENGALI = "LANG_BN"

ACTION_CONSENT_AGREE = "CONSENT_AGREE"
ACTION_CONSENT_DECLINE = "CONSENT_DECLINE"


# ============================================================
# PRIVACY NOTICE CODES
# ============================================================

NOTICE_CODE_AGENCY = "WA_MEDICAL_TOURISM_AGENCY_PRIVACY"


# ============================================================
# HELPER: EXTRACT BUTTON / INTERACTIVE ACTION
# ============================================================

def extract_action_id(msg):
    """
    Extract the action selected by the WhatsApp user.

    Supports:
    - interactive button_reply
    - interactive list_reply
    - legacy button payload

    Returns:
        action id string, or None
    """

    if not isinstance(msg, dict):
        return None

    message_type = msg.get("type")

    # --------------------------------------------------------
    # Current interactive reply buttons
    # --------------------------------------------------------

    if message_type == "interactive":

        interactive = msg.get("interactive") or {}

        interactive_type = interactive.get("type")

        if interactive_type == "button_reply":

            button_reply = (
                interactive.get("button_reply")
                or {}
            )

            return button_reply.get("id")

        if interactive_type == "list_reply":

            list_reply = (
                interactive.get("list_reply")
                or {}
            )

            return list_reply.get("id")

    # --------------------------------------------------------
    # Legacy WhatsApp button object
    # --------------------------------------------------------

    if message_type == "button":

        button = msg.get("button") or {}

        return (
            button.get("payload")
            or button.get("text")
        )

    return None


# ============================================================
# LANGUAGE HELPERS
# ============================================================

def normalize_language(value):
    value = str(value or "").strip().upper()
    if value in {"BN", "BENGALI", "BANGLA", "বাংলা"}:
        return LANGUAGE_BENGALI
    return LANGUAGE_ENGLISH


def _language_from_state(state):
    return normalize_language((state or {}).get("preferred_language"))


def get_preferred_language(sender_mobile):
    """Return the currently selected language; English is the safe default."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        state = get_conversation_state(cursor, sender_mobile)
        return _language_from_state(state)
    finally:
        cursor.close()
        conn.close()


def _set_language_selection(cursor, state_id, language, channel_message_id=None):
    language = normalize_language(language)
    cursor.execute(
        """
        UPDATE whatsapp_conversation_state
        SET
            preferred_language = %s,
            language_selected_flag = 1,
            language_selected_on = NOW(),
            conversation_state = %s,
            last_channel_message_id = COALESCE(%s, last_channel_message_id),
            updated_on = NOW()
        WHERE id = %s
        """,
        (language, STATE_NEW, channel_message_id, state_id),
    )
    return language


def _reset_language_selection_sql_fragment():
    # Kept as documentation for every terminal reset in this module.
    return "language_selected_flag = 0, language_selected_on = NULL"


def send_language_selection_message(sender_mobile):
    body = (
        "👋 Welcome to Cadupius Healthassist!\n"
        "🌍 Medical Tourism Agency Support.\n\n"
        "🌐 Please select your preferred language from the buttons below.\n\n"
        "অনুগ্রহ করে নিচে দেওয়া বোতামগুলো থেকে আপনার পছন্দের ভাষাটি বেছে নিন।"
    )
    return send_reply_buttons(
        to_mobile=sender_mobile,
        body_text=body,
        buttons=[
            {"id": ACTION_LANGUAGE_ENGLISH, "title": "English"},
            {"id": ACTION_LANGUAGE_BENGALI, "title": "বাংলা"},
        ],
        header_text="Cadupius Healthassist",
    )


def send_language_confirmation_message(sender_mobile, language):
    language = normalize_language(language)
    if language == LANGUAGE_BENGALI:
        message = (
            "🌐 আপনার পছন্দের ভাষা হিসেবে বাংলা সেট করা হয়েছে। "
            "এখন থেকে পরবর্তী সমস্ত স্বয়ংক্রিয় বার্তা বাংলায় দেখানো হবে।"
        )
    else:
        message = (
            "🌐 Your preferred language has been set to English. "
            "All further automated messages will be shown in English."
        )
    return send_text(sender_mobile, message, preview_url=False)


def _get_patient_details_format_url():
    """
    Build the one common public download URL for the approved English TXT
    patient-details template.

    Both English and Bengali WhatsApp conversations intentionally download the
    same English template. No separate EN/BN file URL configuration is used.
    """
    configured_base = str(
        current_app.config.get("WHATSAPP_PUBLIC_BASE_URL")
        or os.getenv("WHATSAPP_PUBLIC_BASE_URL")
        or ""
    ).strip()

    if configured_base:
        return configured_base.rstrip("/") + "/whatsapp/patient-details-format"

    return url_for(
        "whatsapp.whatsapp_patient_details_format",
        _external=True,
    )

def is_submission_gate_ready(sender_mobile):
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        state = get_conversation_state(cursor, sender_mobile)
        return bool(
            state
            and str(state.get("conversation_state") or "").upper()
            == STATE_READY_FOR_SUBMISSION
        )
    finally:
        cursor.close()
        conn.close()


def mark_submission_gate_ready_after_instruction(sender_mobile, consent_id=None):
    """Open the parser gate only after the outbound 'You may now send' CTA succeeds."""
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)
    try:
        state = get_conversation_state(cursor, sender_mobile)
        if not state:
            conn.rollback()
            return False

        active_consent_id = consent_id or state.get("active_consent_id")
        if not active_consent_id:
            valid = get_valid_current_consent(cursor, sender_mobile, CATEGORY_AGENCY)
            active_consent_id = (valid or {}).get("id")
        if not active_consent_id:
            conn.rollback()
            return False

        update_conversation_state(
            cursor=cursor,
            state_id=state.get("id"),
            conversation_state=STATE_READY_FOR_SUBMISSION,
            user_category=CATEGORY_AGENCY,
            active_consent_id=active_consent_id,
            consent_required_flag=0,
        )
        conn.commit()
        logger.info("WHATSAPP_SUBMISSION_GATE_OPENED mobile=%s source=OUTBOUND_INSTRUCTION", sender_mobile)
        return True
    except Exception:
        conn.rollback()
        logger.exception("WHATSAPP_SUBMISSION_GATE_OPEN_FAILED mobile=%s", sender_mobile)
        raise
    finally:
        cursor.close()
        conn.close()


# ============================================================
# DATABASE: GET CURRENT CONVERSATION STATE
# ============================================================

def get_conversation_state(
    cursor,
    sender_mobile,
):
    """
    Return latest conversation-state row for this mobile number.

    Current rollout exposes Medical Tourism Agency only.

    PATIENT remains supported by database architecture for
    future activation.
    """

    cursor.execute(
        """
        SELECT
            id,
            sender_mobile,
            user_category,
            conversation_state,
            active_consent_id,
            last_notice_id,
            last_channel_message_id,
            consent_required_flag,
            preferred_language,
            language_selected_flag,
            language_selected_on,
            processing_message_status,
            processing_message_id,
            processing_message_sent_on,
            created_on,
            updated_on
        FROM whatsapp_conversation_state
        WHERE sender_mobile = %s
        ORDER BY updated_on DESC, id DESC
        LIMIT 1
        """,
        (sender_mobile,),
    )

    return cursor.fetchone()


# ============================================================
# DATABASE: CREATE INITIAL STATE
# ============================================================

def create_initial_state(
    cursor,
    sender_mobile,
    channel_message_id=None,
):
    """Create the first Agency state and wait for a clickable language choice."""
    cursor.execute(
        """
        INSERT INTO whatsapp_conversation_state
        (
            sender_mobile,
            user_category,
            conversation_state,
            active_consent_id,
            last_notice_id,
            last_channel_message_id,
            consent_required_flag,
            preferred_language,
            language_selected_flag,
            language_selected_on,
            processing_message_status,
            processing_message_id,
            processing_message_sent_on
        )
        VALUES
        (
            %s, %s, %s,
            NULL, NULL, %s,
            1,
            %s,
            0,
            NULL,
            'NOT_SENT', NULL, NULL
        )
        """,
        (
            sender_mobile,
            CATEGORY_AGENCY,
            STATE_AWAITING_LANGUAGE,
            channel_message_id,
            LANGUAGE_ENGLISH,
        ),
    )
    return cursor.lastrowid

def update_conversation_state(
    cursor,
    state_id,
    conversation_state,
    user_category=None,
    active_consent_id=None,
    last_notice_id=None,
    last_channel_message_id=None,
    consent_required_flag=None,
):
    """
    Update a WhatsApp conversation-state record.

    Only supplied values are updated.
    """

    fields = [
        "conversation_state = %s",
        "updated_on = NOW()",
    ]

    values = [
        conversation_state,
    ]

    if user_category is not None:
        fields.append(
            "user_category = %s"
        )

        values.append(
            user_category
        )

    if active_consent_id is not None:
        fields.append(
            "active_consent_id = %s"
        )

        values.append(
            active_consent_id
        )

    if last_notice_id is not None:
        fields.append(
            "last_notice_id = %s"
        )

        values.append(
            last_notice_id
        )

    if last_channel_message_id is not None:
        fields.append(
            "last_channel_message_id = %s"
        )

        values.append(
            last_channel_message_id
        )

    if consent_required_flag is not None:
        fields.append(
            "consent_required_flag = %s"
        )

        values.append(
            int(consent_required_flag)
        )

    values.append(
        state_id
    )

    sql = f"""
        UPDATE whatsapp_conversation_state
        SET {", ".join(fields)}
        WHERE id = %s
    """

    cursor.execute(
        sql,
        tuple(values),
    )


# ============================================================
# DATABASE: MARK PREVIOUS SUBMISSION COMPLETE
# ============================================================

def mark_conversation_reference_sent(
    sender_mobile,
):
    """
    Mark the Agency conversation as complete after the submission
    Reference Number has been successfully sent through WhatsApp.

    The next inbound WhatsApp message begins a new submission cycle.
    Existing valid consent is reused, but the Welcome message is still
    sent once for that new cycle.

    The processing-message state is also reset here so the next patient
    submission can receive one fresh "We are processing the records"
    message.

    Returns:
        True  -> a conversation-state row was updated.
        False -> no Agency conversation-state row was found.
    """

    conn = get_db_connection()
    cursor = conn.cursor()

    try:

        cursor.execute(
            """
            UPDATE whatsapp_conversation_state
            SET
                conversation_state = %s,
                consent_required_flag = 0,
                language_selected_flag = 0,
                language_selected_on = NULL,
                processing_message_status = 'NOT_SENT',
                processing_message_id = NULL,
                processing_message_sent_on = NULL,
                updated_on = NOW()
            WHERE sender_mobile = %s
              AND user_category = %s
            ORDER BY updated_on DESC, id DESC
            LIMIT 1
            """,
            (
                STATE_NEW,
                sender_mobile,
                CATEGORY_AGENCY,
            ),
        )

        updated = cursor.rowcount == 1

        conn.commit()

        if updated:
            logger.info(
                "WHATSAPP_CONVERSATION_REFERENCE_SENT "
                "mobile=%s processing_cycle_reset=1",
                sender_mobile,
            )
        else:
            logger.warning(
                "WHATSAPP_CONVERSATION_REFERENCE_SENT_STATE_NOT_FOUND "
                "mobile=%s",
                sender_mobile,
            )

        return updated

    except Exception:

        conn.rollback()

        logger.exception(
            "WHATSAPP_CONVERSATION_REFERENCE_SENT_UPDATE_ERROR "
            "mobile=%s",
            sender_mobile,
        )

        raise

    finally:

        cursor.close()
        conn.close()


# ============================================================
# DATABASE: GET CURRENT ACTIVE PRIVACY NOTICE
# ============================================================

def get_active_notice(
    cursor,
    notice_code,
):
    """
    Return the currently active Privacy Notice.
    """

    cursor.execute(
        """
        SELECT
            id,
            notice_code,
            notice_name,
            audience_type,
            notice_version,
            privacy_policy_version,
            notice_title,
            notice_text,
            full_privacy_policy_url,
            notice_hash_sha256,
            material_change_flag,
            reconfirmation_required,
            active_flag,
            effective_from,
            effective_to
        FROM privacy_notice_master
        WHERE notice_code = %s
          AND active_flag = 1
          AND (
                effective_from IS NULL
                OR effective_from <= NOW()
              )
          AND (
                effective_to IS NULL
                OR effective_to > NOW()
              )
        ORDER BY
            effective_from DESC,
            id DESC
        LIMIT 1
        """,
        (notice_code,),
    )

    return cursor.fetchone()


# ============================================================
# DATABASE: GET LATEST CONSENT EVENT
# ============================================================

def get_latest_consent_event(
    cursor,
    sender_mobile,
    user_category,
    notice_code,
):
    """
    Return the latest consent event for this mobile/category.

    We deliberately inspect the latest event instead of simply
    searching for any historical ACTIVE row.

    Example:
        Earlier ACTIVE
        Later WITHDRAWN

    Latest state must therefore be WITHDRAWN, not ACTIVE.
    """

    cursor.execute(
        """
        SELECT
            id,
            sender_mobile,
            user_category,
            party_identifier,
            notice_id,
            notice_code,
            notice_version,
            privacy_policy_version,
            notice_text_snapshot,
            notice_hash_sha256,
            consent_status,
            consent_action,
            patient_data_authority_confirmed,
            child_data_clause_confirmed,
            channel_code,
            channel_message_id,
            privacy_review_session_id,
            privacy_opened_on,
            privacy_scrolled_on,
            consented_on,
            declined_on,
            withdrawn_on,
            superseded_on,
            created_on
        FROM whatsapp_consent_audit
        WHERE sender_mobile = %s
          AND user_category = %s
          AND notice_code = %s
        ORDER BY created_on DESC, id DESC
        LIMIT 1
        """,
        (
            sender_mobile,
            user_category,
            notice_code,
        ),
    )

    return cursor.fetchone()


# ============================================================
# CONSENT VALIDITY
# ============================================================

def get_valid_current_consent(
    cursor,
    sender_mobile,
    user_category,
):
    """
    Determine whether this WhatsApp number has valid consent
    for the currently active Privacy Notice.

    Rules:

    1. No current notice -> no valid consent.
    2. No consent history -> no valid consent.
    3. Latest consent event must be ACTIVE.
    4. Same notice version -> valid.
    5. New notice version but reconfirmation_required = 0
       -> previous ACTIVE consent remains usable.
    6. New notice version and reconfirmation_required = 1
       -> fresh confirmation required.
    """

    if user_category == CATEGORY_AGENCY:

        notice_code = NOTICE_CODE_AGENCY

    else:

        # PATIENT flow not activated yet.
        return None

    notice = get_active_notice(
        cursor,
        notice_code,
    )

    if not notice:
        return None

    latest_consent = get_latest_consent_event(
        cursor,
        sender_mobile,
        user_category,
        notice_code,
    )

    if not latest_consent:
        return None

    if (
        latest_consent.get("consent_status")
        != "ACTIVE"
    ):
        return None

    # Same version = valid.
    if (
        latest_consent.get("notice_version")
        == notice.get("notice_version")
    ):
        return latest_consent

    # Different version, but current notice does not require
    # reconfirmation.
    if not bool(
        notice.get("reconfirmation_required")
    ):
        return latest_consent

    # New material notice requiring reconfirmation.
    return None


# ============================================================
# DATABASE: INSERT ACTIVE CONSENT
# ============================================================

def create_active_consent(
    cursor,
    sender_mobile,
    user_category,
    notice,
    channel_message_id=None,
    channel_code="WHATSAPP",
    privacy_review_session_id=None,
    privacy_opened_on=None,
    privacy_scrolled_on=None,
):
    """
    Store affirmative Agency consent.

    IMPORTANT:

    child_data_clause_confirmed = 1 means the Agency confirmed
    the child/minor authority clause presented in the Privacy
    Information.

    It does NOT mean Cadupius independently obtained parental
    or guardian consent.

    For the new web-based review flow, the permanent audit row
    records the review-session id plus opened/scrolled timestamps.
    """

    notice_text = (
        notice.get("notice_text")
        or ""
    )

    notice_hash = hashlib.sha256(
        notice_text.encode("utf-8")
    ).hexdigest()

    if user_category == CATEGORY_AGENCY:

        authority_confirmed = 1
        child_clause_confirmed = 1

    else:

        authority_confirmed = 0
        child_clause_confirmed = 0

    cursor.execute(
        """
        INSERT INTO whatsapp_consent_audit
        (
            sender_mobile,
            user_category,
            party_identifier,
            notice_id,
            notice_code,
            notice_version,
            privacy_policy_version,
            notice_text_snapshot,
            notice_hash_sha256,
            consent_status,
            consent_action,
            patient_data_authority_confirmed,
            child_data_clause_confirmed,
            channel_code,
            channel_message_id,
            privacy_review_session_id,
            privacy_opened_on,
            privacy_scrolled_on,
            consented_on
        )
        VALUES
        (
            %s,
            %s,
            NULL,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            'ACTIVE',
            'CONFIRM_CONTINUE',
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            NOW()
        )
        """,
        (
            sender_mobile,
            user_category,
            notice.get("id"),
            notice.get("notice_code"),
            notice.get("notice_version"),
            notice.get(
                "privacy_policy_version"
            ),
            notice_text,
            notice_hash,
            authority_confirmed,
            child_clause_confirmed,
            channel_code,
            channel_message_id,
            privacy_review_session_id,
            privacy_opened_on,
            privacy_scrolled_on,
        ),
    )

    return cursor.lastrowid


# ============================================================
# PRIVACY REVIEW SESSION HELPERS
# ============================================================

def _bounded_timeout_minutes(env_name, default_value, minimum=1, maximum=1440):
    """Return a safe integer timeout from environment or the approved default."""

    raw_value = os.getenv(env_name) or str(default_value)

    try:
        value = int(raw_value)
    except (TypeError, ValueError):
        value = int(default_value)

    return max(int(minimum), min(value, int(maximum)))


def _privacy_review_issued_ttl_minutes():
    """
    Initial Welcome / Privacy Information stage timeout.

    Approved default: 2 minutes from issue until the Privacy page is opened.
    """

    return _bounded_timeout_minutes(
        "WHATSAPP_PRIVACY_ISSUED_TIMEOUT_MINUTES",
        2,
    )


def _privacy_review_inactivity_minutes():
    """
    Privacy page inactivity timeout after the page has been opened.

    Approved default: 5 minutes. Browser activity refreshes expires_on.
    """

    return _bounded_timeout_minutes(
        "WHATSAPP_PRIVACY_INACTIVITY_TIMEOUT_MINUTES",
        5,
    )


def _ready_for_submission_timeout_minutes():
    """
    Privacy confirmed but no patient data received.

    Approved default: 5 minutes. Existing valid Agency consent is preserved.
    """

    return _bounded_timeout_minutes(
        "WHATSAPP_READY_FOR_SUBMISSION_TIMEOUT_MINUTES",
        5,
    )


def _hash_privacy_session_token(raw_token):
    return hashlib.sha256(
        str(raw_token).encode("utf-8")
    ).hexdigest()


def create_privacy_review_session(
    cursor,
    sender_mobile,
    notice,
    channel_message_id=None,
):
    """
    Create one opaque privacy-review session.

    Only the SHA-256 token hash is stored in the database. The raw
    token exists only long enough to build the URL sent to WhatsApp.
    """

    raw_token = secrets.token_urlsafe(32)
    token_hash = _hash_privacy_session_token(raw_token)

    notice_text = (
        notice.get("notice_text")
        or ""
    )

    notice_hash = hashlib.sha256(
        notice_text.encode("utf-8")
    ).hexdigest()

    expires_on = (
        datetime.now()
        + timedelta(
            minutes=_privacy_review_issued_ttl_minutes()
        )
    )

    # Any older unfinished review link for this Agency is cancelled.
    cursor.execute(
        """
        UPDATE whatsapp_privacy_review_session
        SET
            session_status = 'CANCELLED',
            updated_on = NOW()
        WHERE sender_mobile = %s
          AND user_category = %s
          AND session_status IN (
                'ISSUED',
                'OPENED',
                'SCROLLED'
              )
        """,
        (
            sender_mobile,
            CATEGORY_AGENCY,
        ),
    )

    cursor.execute(
        """
        INSERT INTO whatsapp_privacy_review_session
        (
            session_token_hash,
            sender_mobile,
            user_category,
            notice_id,
            notice_code,
            notice_version,
            privacy_policy_version,
            notice_text_snapshot,
            notice_hash_sha256,
            origin_channel_message_id,
            opened_on,
            scrolled_on,
            confirmed_on,
            patient_data_authority_confirmed,
            child_data_clause_confirmed,
            session_status,
            expires_on,
            consent_audit_id
        )
        VALUES
        (
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            %s,
            NULL,
            NULL,
            NULL,
            0,
            0,
            'ISSUED',
            %s,
            NULL
        )
        """,
        (
            token_hash,
            sender_mobile,
            CATEGORY_AGENCY,
            notice.get("id"),
            notice.get("notice_code"),
            notice.get("notice_version"),
            notice.get("privacy_policy_version"),
            notice_text,
            notice_hash,
            channel_message_id,
            expires_on,
        ),
    )

    return {
        "id": cursor.lastrowid,
        "raw_token": raw_token,
        "expires_on": expires_on,
    }


def get_privacy_review_session(
    cursor,
    raw_token,
    for_update=False,
):
    """
    Find an unexpired privacy-review session by its opaque raw token.
    """

    token_hash = _hash_privacy_session_token(
        raw_token
    )

    lock_clause = (
        " FOR UPDATE"
        if for_update
        else ""
    )

    cursor.execute(
        f"""
        SELECT
            id,
            session_token_hash,
            sender_mobile,
            user_category,
            notice_id,
            notice_code,
            notice_version,
            privacy_policy_version,
            notice_text_snapshot,
            notice_hash_sha256,
            origin_channel_message_id,
            opened_on,
            scrolled_on,
            confirmed_on,
            patient_data_authority_confirmed,
            child_data_clause_confirmed,
            session_status,
            expires_on,
            consent_audit_id,
            created_on,
            updated_on
        FROM whatsapp_privacy_review_session
        WHERE session_token_hash = %s
        LIMIT 1
        {lock_clause}
        """,
        (token_hash,),
    )

    return cursor.fetchone()


def _session_is_expired(session):
    if not session:
        return True

    expires_on = session.get("expires_on")

    if not expires_on:
        return True

    if isinstance(expires_on, str):
        parsed_value = None
        for date_format in (
            "%Y-%m-%d %H:%M:%S.%f",
            "%Y-%m-%d %H:%M:%S",
        ):
            try:
                parsed_value = datetime.strptime(
                    expires_on,
                    date_format,
                )
                break
            except Exception:
                continue
        expires_on = parsed_value

    if not isinstance(expires_on, datetime):
        return True

    return datetime.now() >= expires_on


def register_privacy_review_open(raw_token):
    """
    Record that the secure Privacy Information page was opened.

    Returns the session row after validation, or None when the token
    is invalid/expired/cancelled.
    """

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        session = get_privacy_review_session(
            cursor,
            raw_token,
            for_update=True,
        )

        if not session:
            conn.rollback()
            return None

        if _session_is_expired(session):
            cursor.execute(
                """
                UPDATE whatsapp_privacy_review_session
                SET
                    session_status = 'EXPIRED',
                    updated_on = NOW()
                WHERE id = %s
                  AND session_status <> 'CONFIRMED'
                """,
                (session.get("id"),),
            )
            conn.commit()
            return None

        status = str(
            session.get("session_status")
            or ""
        ).upper()

        if status in {
            "CANCELLED",
            "EXPIRED",
        }:
            conn.rollback()
            return None

        if status == "ISSUED":
            cursor.execute(
                """
                UPDATE whatsapp_privacy_review_session
                SET
                    opened_on = COALESCE(opened_on, NOW()),
                    session_status = 'OPENED',
                    expires_on = DATE_ADD(
                        NOW(),
                        INTERVAL %s MINUTE
                    ),
                    updated_on = NOW()
                WHERE id = %s
                """,
                (
                    _privacy_review_inactivity_minutes(),
                    session.get("id"),
                ),
            )
            conn.commit()

        else:
            conn.commit()

        cursor.execute(
            """
            SELECT *
            FROM whatsapp_privacy_review_session
            WHERE id = %s
            LIMIT 1
            """,
            (session.get("id"),),
        )

        return cursor.fetchone()

    except Exception:
        conn.rollback()
        logger.exception(
            "WHATSAPP_PRIVACY_REVIEW_OPEN_ERROR"
        )
        raise

    finally:
        cursor.close()
        conn.close()


def register_privacy_review_activity(raw_token):
    """
    Refresh the 5-minute Privacy page inactivity window after genuine
    browser activity (scroll/touch/click/key activity).

    No consent evidence is created here; this only extends expires_on for
    an already OPENED or SCROLLED review session.
    """

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        session = get_privacy_review_session(
            cursor,
            raw_token,
            for_update=True,
        )

        if not session:
            conn.rollback()
            return None

        if _session_is_expired(session):
            cursor.execute(
                """
                UPDATE whatsapp_privacy_review_session
                SET
                    session_status = 'EXPIRED',
                    updated_on = NOW()
                WHERE id = %s
                  AND session_status <> 'CONFIRMED'
                """,
                (session.get("id"),),
            )
            conn.commit()
            return None

        status = str(
            session.get("session_status")
            or ""
        ).upper()

        if status not in {
            "OPENED",
            "SCROLLED",
        }:
            conn.rollback()
            return None

        cursor.execute(
            """
            UPDATE whatsapp_privacy_review_session
            SET
                expires_on = DATE_ADD(
                    NOW(),
                    INTERVAL %s MINUTE
                ),
                updated_on = NOW()
            WHERE id = %s
              AND session_status IN ('OPENED', 'SCROLLED')
            """,
            (
                _privacy_review_inactivity_minutes(),
                session.get("id"),
            ),
        )
        conn.commit()

        cursor.execute(
            """
            SELECT *
            FROM whatsapp_privacy_review_session
            WHERE id = %s
            LIMIT 1
            """,
            (session.get("id"),),
        )
        return cursor.fetchone()

    except Exception:
        conn.rollback()
        logger.exception(
            "WHATSAPP_PRIVACY_REVIEW_ACTIVITY_ERROR"
        )
        raise

    finally:
        cursor.close()
        conn.close()


def register_privacy_review_scrolled(raw_token):
    """
    Record that browser JavaScript detected the user reaching the
    bottom of the complete Privacy Information.

    Server-side confirmation later requires SCROLLED status. The UI
    alone is therefore not enough to confirm consent.
    """

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        session = get_privacy_review_session(
            cursor,
            raw_token,
            for_update=True,
        )

        if (
            not session
            or _session_is_expired(session)
        ):
            if session:
                cursor.execute(
                    """
                    UPDATE whatsapp_privacy_review_session
                    SET
                        session_status = 'EXPIRED',
                        updated_on = NOW()
                    WHERE id = %s
                      AND session_status <> 'CONFIRMED'
                    """,
                    (session.get("id"),),
                )
                conn.commit()
            else:
                conn.rollback()
            return None

        status = str(
            session.get("session_status")
            or ""
        ).upper()

        if status not in {
            "OPENED",
            "SCROLLED",
        }:
            conn.rollback()
            return None

        cursor.execute(
            """
            UPDATE whatsapp_privacy_review_session
            SET
                opened_on = COALESCE(opened_on, NOW()),
                scrolled_on = COALESCE(scrolled_on, NOW()),
                session_status = 'SCROLLED',
                expires_on = DATE_ADD(
                    NOW(),
                    INTERVAL %s MINUTE
                ),
                updated_on = NOW()
            WHERE id = %s
            """,
            (
                _privacy_review_inactivity_minutes(),
                session.get("id"),
            ),
        )

        conn.commit()

        cursor.execute(
            """
            SELECT *
            FROM whatsapp_privacy_review_session
            WHERE id = %s
            LIMIT 1
            """,
            (session.get("id"),),
        )

        return cursor.fetchone()

    except Exception:
        conn.rollback()
        logger.exception(
            "WHATSAPP_PRIVACY_REVIEW_SCROLL_ERROR"
        )
        raise

    finally:
        cursor.close()
        conn.close()


def confirm_privacy_review(raw_token):
    """
    Confirm Agency privacy review only after the session has reached
    SCROLLED state.

    This function:
    - re-validates the review session;
    - creates the permanent whatsapp_consent_audit row;
    - records Agency authority and child/minor authority acknowledgement;
    - links the session to the consent audit;
    - keeps the parser gate closed until the outbound submission instruction succeeds.

    Returns a result dictionary, or None for invalid/expired/not-scrolled
    sessions.
    """

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        session = get_privacy_review_session(
            cursor,
            raw_token,
            for_update=True,
        )

        if not session:
            conn.rollback()
            return None

        if _session_is_expired(session):
            cursor.execute(
                """
                UPDATE whatsapp_privacy_review_session
                SET
                    session_status = 'EXPIRED',
                    updated_on = NOW()
                WHERE id = %s
                  AND session_status <> 'CONFIRMED'
                """,
                (session.get("id"),),
            )
            conn.commit()
            return None

        status = str(
            session.get("session_status")
            or ""
        ).upper()

        # Idempotent browser retry after a successful confirmation.
        if status == "CONFIRMED":
            conn.commit()
            return {
                "already_confirmed": True,
                "sender_mobile": session.get("sender_mobile"),
                "consent_id": session.get("consent_audit_id"),
                "session_id": session.get("id"),
            }

        if (
            status != "SCROLLED"
            or not session.get("scrolled_on")
        ):
            conn.rollback()
            return None

        if (
            session.get("user_category")
            != CATEGORY_AGENCY
        ):
            conn.rollback()
            return None

        notice = get_active_notice(
            cursor,
            NOTICE_CODE_AGENCY,
        )

        if not notice:
            conn.rollback()
            return None

        # Prevent confirming an obsolete privacy-review link if the
        # currently active notice/version has changed in the meantime.
        if (
            notice.get("id")
            != session.get("notice_id")
            or str(
                notice.get("notice_version")
                or ""
            )
            != str(
                session.get("notice_version")
                or ""
            )
        ):
            cursor.execute(
                """
                UPDATE whatsapp_privacy_review_session
                SET
                    session_status = 'CANCELLED',
                    updated_on = NOW()
                WHERE id = %s
                """,
                (session.get("id"),),
            )
            conn.commit()
            return None

        consent_id = create_active_consent(
            cursor=cursor,
            sender_mobile=session.get("sender_mobile"),
            user_category=CATEGORY_AGENCY,
            notice=notice,
            channel_message_id=session.get(
                "origin_channel_message_id"
            ),
            channel_code="WEB",
            privacy_review_session_id=session.get("id"),
            privacy_opened_on=session.get("opened_on"),
            privacy_scrolled_on=session.get("scrolled_on"),
        )

        cursor.execute(
            """
            UPDATE whatsapp_privacy_review_session
            SET
                confirmed_on = NOW(),
                patient_data_authority_confirmed = 1,
                child_data_clause_confirmed = 1,
                session_status = 'CONFIRMED',
                consent_audit_id = %s,
                updated_on = NOW()
            WHERE id = %s
              AND session_status = 'SCROLLED'
            """,
            (
                consent_id,
                session.get("id"),
            ),
        )

        state = get_conversation_state(
            cursor,
            session.get("sender_mobile"),
        )

        if state:
            update_conversation_state(
                cursor=cursor,
                state_id=state.get("id"),
                conversation_state=STATE_AWAITING_CONSENT,
                user_category=CATEGORY_AGENCY,
                active_consent_id=consent_id,
                last_notice_id=notice.get("id"),
                last_channel_message_id=session.get(
                    "origin_channel_message_id"
                ),
                consent_required_flag=0,
            )

        else:
            state_id = create_initial_state(
                cursor,
                session.get("sender_mobile"),
                session.get(
                    "origin_channel_message_id"
                ),
            )

            update_conversation_state(
                cursor=cursor,
                state_id=state_id,
                conversation_state=STATE_AWAITING_CONSENT,
                user_category=CATEGORY_AGENCY,
                active_consent_id=consent_id,
                last_notice_id=notice.get("id"),
                last_channel_message_id=session.get(
                    "origin_channel_message_id"
                ),
                consent_required_flag=0,
            )

        conn.commit()

        logger.info(
            "WHATSAPP_PRIVACY_REVIEW_CONFIRMED "
            "mobile=%s session_id=%s consent_id=%s notice_version=%s",
            session.get("sender_mobile"),
            session.get("id"),
            consent_id,
            notice.get("notice_version"),
        )

        return {
            "already_confirmed": False,
            "sender_mobile": session.get("sender_mobile"),
            "consent_id": consent_id,
            "session_id": session.get("id"),
            "notice_version": notice.get("notice_version"),
        }

    except Exception:
        conn.rollback()

        logger.exception(
            "WHATSAPP_PRIVACY_REVIEW_CONFIRM_ERROR"
        )

        raise

    finally:
        cursor.close()
        conn.close()


# ============================================================
# PRIVACY REVIEW: DO NOT AGREE / CANCEL CURRENT SESSION
# ============================================================

def cancel_privacy_review(raw_token):
    """
    Cancel the temporary Privacy Review session when the Agency selects
    "Do Not Agree" on the web page.

    IMPORTANT:
    - No whatsapp_consent_audit refusal/decline row is created.
    - No consent_status='DECLINED' event is written.
    - Only the temporary whatsapp_privacy_review_session is marked CANCELLED.
    - The current WhatsApp interaction is reset to NEW.
    - Any historical consent row remains untouched; only a currently valid
      consent may remain active.

    Returns a result dictionary, or None when the token is invalid/expired.
    """

    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        session = get_privacy_review_session(
            cursor,
            raw_token,
            for_update=True,
        )

        if not session:
            conn.rollback()
            return None

        status = str(
            session.get("session_status")
            or ""
        ).upper()

        # Consent, once affirmatively confirmed, must not be undone through
        # the Do Not Agree endpoint.
        if status == "CONFIRMED":
            conn.commit()
            return {
                "already_confirmed": True,
                "already_cancelled": False,
                "sender_mobile": session.get("sender_mobile"),
                "session_id": session.get("id"),
            }

        # Idempotent browser retry after a successful cancellation.
        if status == "CANCELLED":
            conn.commit()
            return {
                "already_confirmed": False,
                "already_cancelled": True,
                "sender_mobile": session.get("sender_mobile"),
                "session_id": session.get("id"),
            }

        if _session_is_expired(session) or status == "EXPIRED":
            cursor.execute(
                """
                UPDATE whatsapp_privacy_review_session
                SET
                    session_status = 'EXPIRED',
                    updated_on = NOW()
                WHERE id = %s
                  AND session_status <> 'CONFIRMED'
                """,
                (session.get("id"),),
            )
            conn.commit()
            return None

        if status not in {
            "ISSUED",
            "OPENED",
            "SCROLLED",
        }:
            conn.rollback()
            return None

        cursor.execute(
            """
            UPDATE whatsapp_privacy_review_session
            SET
                session_status = 'CANCELLED',
                updated_on = NOW()
            WHERE id = %s
              AND session_status IN ('ISSUED', 'OPENED', 'SCROLLED')
            """,
            (session.get("id"),),
        )

        sender_mobile = session.get("sender_mobile")

        # A valid current consent normally should not coexist with a new
        # privacy-review request, but preserve it defensively if it does.
        valid_consent = get_valid_current_consent(
            cursor,
            sender_mobile,
            CATEGORY_AGENCY,
        )

        if valid_consent:
            cursor.execute(
                """
                UPDATE whatsapp_conversation_state
                SET
                    conversation_state = %s,
                    active_consent_id = %s,
                    consent_required_flag = 0,
                    language_selected_flag = 0,
                    language_selected_on = NULL,
                    processing_message_status = 'NOT_SENT',
                    processing_message_id = NULL,
                    processing_message_sent_on = NULL,
                    updated_on = NOW()
                WHERE sender_mobile = %s
                  AND user_category = %s
                ORDER BY updated_on DESC, id DESC
                LIMIT 1
                """,
                (
                    STATE_NEW,
                    valid_consent.get("id"),
                    sender_mobile,
                    CATEGORY_AGENCY,
                ),
            )
            consent_required_flag = 0
        else:
            cursor.execute(
                """
                UPDATE whatsapp_conversation_state
                SET
                    conversation_state = %s,
                    active_consent_id = NULL,
                    consent_required_flag = 1,
                    language_selected_flag = 0,
                    language_selected_on = NULL,
                    processing_message_status = 'NOT_SENT',
                    processing_message_id = NULL,
                    processing_message_sent_on = NULL,
                    updated_on = NOW()
                WHERE sender_mobile = %s
                  AND user_category = %s
                ORDER BY updated_on DESC, id DESC
                LIMIT 1
                """,
                (
                    STATE_NEW,
                    sender_mobile,
                    CATEGORY_AGENCY,
                ),
            )
            consent_required_flag = 1

        conn.commit()

        logger.info(
            "WHATSAPP_PRIVACY_REVIEW_CANCELLED_BY_USER "
            "mobile=%s session_id=%s consent_audit_created=0",
            sender_mobile,
            session.get("id"),
        )

        return {
            "already_confirmed": False,
            "already_cancelled": False,
            "sender_mobile": sender_mobile,
            "session_id": session.get("id"),
            "consent_required_flag": consent_required_flag,
        }

    except Exception:
        conn.rollback()
        logger.exception(
            "WHATSAPP_PRIVACY_REVIEW_CANCEL_ERROR"
        )
        raise

    finally:
        cursor.close()
        conn.close()


def send_privacy_not_confirmed_closure_message(sender_mobile):
    language = get_preferred_language(sender_mobile)
    if language == LANGUAGE_BENGALI:
        message = (
            "ℹ️ গোপনীয়তার স্বীকৃতি প্রদান করা হয়নি।\n"
            "এই সেশনটি এখন বন্ধ করা হয়েছে।\n\n"
            "🙏 ক্যাডুপিয়াস হেলথঅ্যাসিস্ট-এর সাথে যোগাযোগ করার জন্য ধন্যবাদ।"
        )
    else:
        message = (
            "ℹ️ Privacy confirmation was not provided.\n"
            "This session has now been closed.\n\n"
            "🙏 Thank you for contacting Cadupius Healthassist."
        )
    return send_text(sender_mobile, message, preview_url=False)

def send_welcome_message(sender_mobile, language=None):
    language = normalize_language(language or get_preferred_language(sender_mobile))
    if language == LANGUAGE_BENGALI:
        message = (
            "👋 ক্যাডুপিয়াস হেলথঅ্যাসিস্ট-এ (Cadupius Healthassist) আপনাকে স্বাগত!\n"
            "🌍 মেডিকেল ট্যুরিজম এজেন্সি সাপোর্ট।"
        )
    else:
        message = (
            "👋 Welcome to Cadupius Healthassist!\n"
            "🌍 Medical Tourism Agency Support."
        )
    return send_text(sender_mobile, message, preview_url=False)

def send_existing_consent_ready_message(sender_mobile, language=None):
    language = normalize_language(language or get_preferred_language(sender_mobile))
    download_url = _get_patient_details_format_url()
    if language == LANGUAGE_BENGALI:
        body = (
            "✅ আপনার গোপনীয়তার স্বীকৃতি ইতিমধ্যেই আমাদের রেকর্ডে রয়েছে।\n\n"
            "📋 আপনি এখন রোগীর বিবরণ এবং মেডিকেল রিপোর্ট পাঠাতে পারেন। "
            "অনুগ্রহ করে নির্ধারিত ফরমেটে রোগীর বিবরণ পাঠান। "
            "নিচের লিংকটি ব্যবহার করে আপনি নির্ধারিত ফরমেটটি ডাউনলোড করতে পারবেন।"
        )
        button_text = "ফরমেট ডাউনলোড"
    else:
        body = (
            "✅ Your Privacy confirmation is already on record.\n\n"
            "📋 You may now send the patient's details and medical reports. "
            "Please send the patient details in the prescribed format. "
            "You can download the prescribed format using the link below."
        )
        button_text = "Download Format"
    return send_cta_url_button(
        to_mobile=sender_mobile,
        body_text=body,
        button_text=button_text,
        url=download_url,
        header_text="Cadupius Healthassist",
    )

def send_welcome_back_message(sender_mobile):
    return send_welcome_message(sender_mobile)

def _build_privacy_review_url(raw_token, language=None):
    """Build the public TERIQEN privacy-review URL with a display-language hint."""
    configured_base = str(
        current_app.config.get("WHATSAPP_PUBLIC_BASE_URL")
        or os.getenv("WHATSAPP_PUBLIC_BASE_URL")
        or ""
    ).strip()
    lang_code = "bn" if normalize_language(language) == LANGUAGE_BENGALI else "en"

    if configured_base:
        return (
            configured_base.rstrip("/")
            + "/whatsapp/privacy/"
            + str(raw_token)
            + "?lang="
            + lang_code
        )

    return url_for(
        "whatsapp.whatsapp_privacy_review",
        token=raw_token,
        lang=lang_code,
        _external=True,
    )

def send_privacy_notice(sender_mobile, notice, raw_token, language=None):
    language = normalize_language(language or get_preferred_language(sender_mobile))
    review_url = _build_privacy_review_url(raw_token, language=language)

    if language == LANGUAGE_BENGALI:
        body = (
            "🔐 গোপনীয়তা সংক্রান্ত তথ্য: রোগীর কোনো তথ্য শেয়ার করার আগে, "
            "অনুগ্রহ করে আমাদের 'গোপনীয়তা সংক্রান্ত তথ্য' (Privacy Information) দেখে নিন। "
            "সেই সাথে নিশ্চিত করুন যে, ক্যাডুপিয়াস হেলথঅ্যাসিস্ট-এর (Cadupius Healthassist) "
            "সাথে রোগীর তথ্য শেয়ার করার প্রয়োজনীয় অনুমতি আপনার আছে। এর মধ্যে প্রযোজ্য ক্ষেত্রে "
            "কোনো শিশু বা অপ্রাপ্তবয়স্কের তথ্যও অন্তর্ভুক্ত রয়েছে।"
        )
        button_text = "গোপনীয়তা দেখুন"
    else:
        body = (
            "🔐 Privacy Information: Before sharing patient information, please review our "
            "Privacy Information and confirm that you are authorised to share the patient's "
            "information with Cadupius Healthassist, including information relating to a child "
            "or minor, where applicable."
        )
        button_text = "View Privacy Info"

    return send_cta_url_button(
        to_mobile=sender_mobile,
        body_text=body,
        button_text=button_text,
        url=review_url,
        header_text="Cadupius Healthassist",
    )

def send_submission_instructions(sender_mobile, language=None):
    """Send the selected-language patient submission instruction + download CTA."""
    language = normalize_language(language or get_preferred_language(sender_mobile))
    download_url = _get_patient_details_format_url()

    if language == LANGUAGE_BENGALI:
        body = (
            "✅ গোপনীয়তার স্বীকৃতি রেকর্ড করা হয়েছে।\n\n"
            "📋 আপনি এখন রোগীর বিবরণ এবং মেডিকেল রিপোর্ট পাঠাতে পারেন। "
            "অনুগ্রহ করে নির্ধারিত ফরমেটে রোগীর বিবরণ পাঠান। "
            "নিচের লিংকটি ব্যবহার করে আপনি নির্ধারিত ফরমেটটি ডাউনলোড করতে পারবেন।"
        )
        button_text = "ফরমেট ডাউনলোড"
    else:
        body = (
            "✅ Privacy confirmation recorded.\n\n"
            "📋 You may now send the patient's details and medical reports. "
            "Please send the patient details in the prescribed format. "
            "You can download the prescribed format using the link below."
        )
        button_text = "Download Format"

    return send_cta_url_button(
        to_mobile=sender_mobile,
        body_text=body,
        button_text=button_text,
        url=download_url,
        header_text="Cadupius Healthassist",
    )

def _send_new_privacy_review(
    cursor,
    sender_mobile,
    notice,
    channel_message_id,
):
    """
    Create a secure review session and send its one-time URL.

    The caller owns the database transaction.
    """

    review_session = (
        create_privacy_review_session(
            cursor=cursor,
            sender_mobile=sender_mobile,
            notice=notice,
            channel_message_id=channel_message_id,
        )
    )

    return review_session


# ============================================================
# PROCESSING MESSAGE - ONE PER SUBMISSION
# ============================================================

def send_processing_message_once(
    sender_mobile,
    submission_log_id=None,
    received_on=None,
):
    """
    Compatibility wrapper for the new WA submission tracking flow.

    The actual registration / temporary WA Ref creation / one-time processing
    message is owned by submission_ack_service.py. The import is intentionally
    local to avoid a circular module import.

    submission_log_id must be the already-committed channel_message_log.id for
    the first patient-data event in the current submission cycle.
    """

    from app.whatsapp.submission_ack_service import (
        register_whatsapp_submission,
    )

    return register_whatsapp_submission(
        sender_mobile=sender_mobile,
        channel_message_log_id=submission_log_id,
        received_on=received_on,
    )


# ============================================================
# TERMINAL MESSAGES / SESSION TIMEOUTS
# ============================================================

def _reset_conversation_cycle(
    sender_mobile,
    consent_required_flag,
    log_event,
):
    """
    Reset only the current WhatsApp interaction/submission cycle.

    Permanent consent rows are never deleted here. active_consent_id is
    intentionally preserved so a returning Agency can reuse valid consent.
    """

    conn = get_db_connection()
    cursor = conn.cursor()

    try:
        cursor.execute(
            """
            UPDATE whatsapp_conversation_state
            SET
                conversation_state = %s,
                consent_required_flag = %s,
                language_selected_flag = 0,
                language_selected_on = NULL,
                processing_message_status = 'NOT_SENT',
                processing_message_id = NULL,
                processing_message_sent_on = NULL,
                updated_on = NOW()
            WHERE sender_mobile = %s
              AND user_category = %s
            ORDER BY updated_on DESC, id DESC
            LIMIT 1
            """,
            (
                STATE_NEW,
                int(consent_required_flag),
                sender_mobile,
                CATEGORY_AGENCY,
            ),
        )
        updated = cursor.rowcount == 1
        conn.commit()

        logger.info(
            "%s mobile=%s state_reset=%s",
            log_event,
            sender_mobile,
            int(updated),
        )
        return updated

    except Exception:
        conn.rollback()
        logger.exception(
            "%s_RESET_ERROR mobile=%s",
            log_event,
            sender_mobile,
        )
        raise

    finally:
        cursor.close()
        conn.close()


def send_no_patient_data_and_close(sender_mobile):
    """Close a Junk/non-patient submission and provide the prescribed-format download."""
    language = get_preferred_language(sender_mobile)
    download_url = _get_patient_details_format_url()
    if language == LANGUAGE_BENGALI:
        message = (
            "⚠️ আমরা আপনার পাঠানো রোগীর তথ্যটি যাচাই করতে পারিনি। "
            "অনুগ্রহ করে তথ্যগুলো পরীক্ষা করুন এবং নির্ধারিত ফরমেট ব্যবহার করে পুনরায় পাঠান।"
        )
        button_text = "ফরমেট ডাউনলোড"
    else:
        message = (
            "⚠️ We could not process the submitted patient information. "
            "Please check the information and resend it using the prescribed format."
        )
        button_text = "Download Format"

    send_error = None
    result = None
    try:
        result = send_cta_url_button(
            to_mobile=sender_mobile,
            body_text=message,
            button_text=button_text,
            url=download_url,
            header_text="Cadupius Healthassist",
        )
    except Exception as exc:
        send_error = exc
        logger.exception("WHATSAPP_JUNK_CLOSURE_MESSAGE_SEND_ERROR mobile=%s", sender_mobile)
    finally:
        _reset_conversation_cycle(
            sender_mobile=sender_mobile,
            consent_required_flag=0,
            log_event="WHATSAPP_JUNK_CYCLE_CLOSED",
        )
    if send_error is not None:
        raise send_error
    return result

def _send_timeout_message(sender_mobile, timeout_type):
    language = get_preferred_language(sender_mobile)
    if language == LANGUAGE_BENGALI:
        if timeout_type == "PRIVACY_CONFIRMED_WAITING_FOR_DATA":
            message = (
                "⏱️ নিষ্ক্রিয় থাকার কারণে এই সাবমিশন সেশনটি শেষ হয়েছে।\n"
                "আপনার গোপনীয়তার স্বীকৃতি কার্যকর রয়েছে।\n"
                "রোগীর তথ্য জমা দিতে চাইলে আপনি আবার আমাদের মেসেজ করতে পারেন।\n\n"
                "🙏 ক্যাডুপিয়াস হেলথঅ্যাসিস্ট বেছে নেওয়ার জন্য ধন্যবাদ।"
            )
        elif timeout_type == "PRIVACY_PAGE_INACTIVE":
            message = (
                "⏱️ নিষ্ক্রিয় থাকার কারণে এই গোপনীয়তা সংক্রান্ত তথ্যের সেশনটি শেষ হয়েছে।\n"
                "রিভিউ লিংকের মেয়াদ শেষ হয়েছে। চালিয়ে যেতে চাইলে আবার আমাদের মেসেজ করুন।\n\n"
                "🙏 ক্যাডুপিয়াস হেলথঅ্যাসিস্ট বেছে নেওয়ার জন্য ধন্যবাদ।"
            )
        else:
            message = (
                "⏱️ নিষ্ক্রিয় থাকার কারণে এই সেশনটি শেষ হয়েছে।\n"
                "চালিয়ে যেতে চাইলে আবার আমাদের মেসেজ করুন।\n\n"
                "🙏 ক্যাডুপিয়াস হেলথঅ্যাসিস্ট বেছে নেওয়ার জন্য ধন্যবাদ।"
            )
    else:
        if timeout_type == "PRIVACY_CONFIRMED_WAITING_FOR_DATA":
            message = (
                "⏱️ This submission session has ended due to inactivity.\n"
                "Your Privacy confirmation remains valid.\n"
                "You may message us again whenever you wish to submit patient information.\n\n"
                "🙏 Thank you for choosing Cadupius Healthassist."
            )
        elif timeout_type == "PRIVACY_PAGE_INACTIVE":
            message = (
                "⏱️ This Privacy Information session has ended due to inactivity.\n"
                "The review link has expired. You may message us again whenever you wish to continue.\n\n"
                "🙏 Thank you for choosing Cadupius Healthassist."
            )
        else:
            message = (
                "⏱️ This session has ended due to inactivity.\n"
                "You may message us again whenever you wish to continue.\n\n"
                "🙏 Thank you for choosing Cadupius Healthassist."
            )
    return send_text(sender_mobile, message, preview_url=False)

def process_whatsapp_session_timeouts():
    """
    Close idle WhatsApp stages using the approved rules:

    - Privacy link issued but unopened: 2 minutes.
    - Privacy page opened/scrolled but not confirmed: 5 minutes inactivity.
    - Privacy confirmed and waiting for patient data: 5 minutes.
    - Valid patient data under processing: no normal session timeout.

    This function is designed to run once per minute from scheduler.py.
    """

    summary = {
        "privacy_issued_expired": 0,
        "privacy_inactive_expired": 0,
        "ready_for_submission_expired": 0,
        "messages_sent": 0,
        "message_failures": 0,
    }

    # --------------------------------------------------------
    # 1) Privacy review session timeouts
    # --------------------------------------------------------
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        cursor.execute(
            """
            SELECT
                id,
                sender_mobile,
                session_status,
                expires_on
            FROM whatsapp_privacy_review_session
            WHERE session_status IN ('ISSUED', 'OPENED', 'SCROLLED')
              AND expires_on IS NOT NULL
              AND expires_on <= NOW()
            ORDER BY expires_on ASC, id ASC
            LIMIT 100
            """
        )
        candidates = cursor.fetchall() or []
    finally:
        cursor.close()
        conn.close()

    for row in candidates:
        session_id = row.get("id")
        sender_mobile = row.get("sender_mobile")
        old_status = str(row.get("session_status") or "").upper()

        update_conn = get_db_connection()
        update_cursor = update_conn.cursor()
        expired = False

        try:
            update_cursor.execute(
                """
                UPDATE whatsapp_privacy_review_session
                SET
                    session_status = 'EXPIRED',
                    updated_on = NOW()
                WHERE id = %s
                  AND session_status = %s
                  AND expires_on IS NOT NULL
                  AND expires_on <= NOW()
                """,
                (session_id, old_status),
            )
            expired = update_cursor.rowcount == 1
            update_conn.commit()
        except Exception:
            update_conn.rollback()
            logger.exception(
                "WHATSAPP_PRIVACY_TIMEOUT_UPDATE_ERROR session_id=%s",
                session_id,
            )
        finally:
            update_cursor.close()
            update_conn.close()

        if not expired:
            continue

        timeout_type = (
            "PRIVACY_LINK_ISSUED"
            if old_status == "ISSUED"
            else "PRIVACY_PAGE_INACTIVE"
        )

        if old_status == "ISSUED":
            summary["privacy_issued_expired"] += 1
        else:
            summary["privacy_inactive_expired"] += 1

        # Reset onboarding only when no newer active review session exists.
        active_conn = get_db_connection()
        active_cursor = active_conn.cursor(dictionary=True)
        try:
            active_cursor.execute(
                """
                SELECT id
                FROM whatsapp_privacy_review_session
                WHERE sender_mobile = %s
                  AND user_category = %s
                  AND session_status IN ('ISSUED', 'OPENED', 'SCROLLED')
                  AND expires_on > NOW()
                ORDER BY id DESC
                LIMIT 1
                """,
                (sender_mobile, CATEGORY_AGENCY),
            )
            newer_active = active_cursor.fetchone()
        finally:
            active_cursor.close()
            active_conn.close()

        if newer_active:
            continue

        try:
            _send_timeout_message(
                sender_mobile,
                timeout_type,
            )
            summary["messages_sent"] += 1
        except Exception:
            summary["message_failures"] += 1
            logger.exception(
                "WHATSAPP_PRIVACY_TIMEOUT_MESSAGE_ERROR mobile=%s session_id=%s",
                sender_mobile,
                session_id,
            )
        finally:
            _reset_conversation_cycle(
                sender_mobile=sender_mobile,
                consent_required_flag=1,
                log_event="WHATSAPP_PRIVACY_TIMEOUT_CLOSED",
            )

    # --------------------------------------------------------
    # 2) Privacy confirmed, waiting for patient data: 5 min
    # --------------------------------------------------------
    ready_minutes = _ready_for_submission_timeout_minutes()
    ready_conn = get_db_connection()
    ready_cursor = ready_conn.cursor(dictionary=True)

    try:
        ready_cursor.execute(
            """
            SELECT
                id,
                sender_mobile
            FROM whatsapp_conversation_state
            WHERE user_category = %s
              AND conversation_state = %s
              AND COALESCE(processing_message_status, 'NOT_SENT') = 'NOT_SENT'
              AND active_consent_id IS NOT NULL
              AND updated_on <= DATE_SUB(NOW(), INTERVAL %s MINUTE)
            ORDER BY updated_on ASC, id ASC
            LIMIT 100
            """,
            (
                CATEGORY_AGENCY,
                STATE_READY_FOR_SUBMISSION,
                ready_minutes,
            ),
        )
        ready_candidates = ready_cursor.fetchall() or []
    finally:
        ready_cursor.close()
        ready_conn.close()

    for row in ready_candidates:
        state_id = row.get("id")
        sender_mobile = row.get("sender_mobile")

        claim_conn = get_db_connection()
        claim_cursor = claim_conn.cursor()
        claimed = False

        try:
            claim_cursor.execute(
                """
                UPDATE whatsapp_conversation_state
                SET
                    conversation_state = %s,
                    consent_required_flag = 0,
                    language_selected_flag = 0,
                    language_selected_on = NULL,
                    processing_message_status = 'NOT_SENT',
                    processing_message_id = NULL,
                    processing_message_sent_on = NULL,
                    updated_on = NOW()
                WHERE id = %s
                  AND user_category = %s
                  AND conversation_state = %s
                  AND COALESCE(processing_message_status, 'NOT_SENT') = 'NOT_SENT'
                  AND active_consent_id IS NOT NULL
                  AND updated_on <= DATE_SUB(NOW(), INTERVAL %s MINUTE)
                """,
                (
                    STATE_NEW,
                    state_id,
                    CATEGORY_AGENCY,
                    STATE_READY_FOR_SUBMISSION,
                    ready_minutes,
                ),
            )
            claimed = claim_cursor.rowcount == 1
            claim_conn.commit()
        except Exception:
            claim_conn.rollback()
            logger.exception(
                "WHATSAPP_READY_TIMEOUT_UPDATE_ERROR state_id=%s mobile=%s",
                state_id,
                sender_mobile,
            )
        finally:
            claim_cursor.close()
            claim_conn.close()

        if not claimed:
            continue

        summary["ready_for_submission_expired"] += 1

        try:
            _send_timeout_message(
                sender_mobile,
                "PRIVACY_CONFIRMED_WAITING_FOR_DATA",
            )
            summary["messages_sent"] += 1
        except Exception:
            summary["message_failures"] += 1
            logger.exception(
                "WHATSAPP_READY_TIMEOUT_MESSAGE_ERROR mobile=%s",
                sender_mobile,
            )

    logger.info(
        "WHATSAPP_SESSION_TIMEOUT_JOB_COMPLETED "
        "privacy_issued=%s privacy_inactive=%s ready=%s sent=%s failures=%s",
        summary["privacy_issued_expired"],
        summary["privacy_inactive_expired"],
        summary["ready_for_submission_expired"],
        summary["messages_sent"],
        summary["message_failures"],
    )

    return summary


# ============================================================
# MAIN CONSENT / ONBOARDING HANDLER
# ============================================================

def handle_whatsapp_consent_flow(
    sender_mobile,
    channel_message_id,
    msg,
):
    """
    Agency-only bilingual WhatsApp onboarding/consent gate.

    Hard parser boundary:
    - the first inbound message is always consumed and shows language buttons;
    - the language-button reply is always consumed;
    - consent/onboarding messages are always consumed;
    - only an inbound message received while the state was ALREADY
      READY_FOR_SUBMISSION may reach the patient parser.
    """
    conn = get_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        action_id = extract_action_id(msg)
        state = get_conversation_state(cursor, sender_mobile)

        # ----------------------------------------------------
        # FIRST INBOUND MESSAGE: only show clickable language choice.
        # ----------------------------------------------------
        if not state:
            state_id = create_initial_state(cursor, sender_mobile, channel_message_id)
            conn.commit()
            send_language_selection_message(sender_mobile)
            logger.info(
                "WHATSAPP_LANGUAGE_SELECTION_SENT mobile=%s state_id=%s first_contact=1",
                sender_mobile,
                state_id,
            )
            return True

        state_id = state.get("id")
        current_state = state.get("conversation_state") or STATE_NEW
        language_selected = int(state.get("language_selected_flag") or 0) == 1

        # A terminal cycle reset sets state NEW + language_selected_flag=0.
        # Therefore every new interaction asks again, while English remains the
        # stored/default fallback until the user clicks a button.
        if not language_selected or current_state == STATE_AWAITING_LANGUAGE:
            selected_language = None
            if action_id == ACTION_LANGUAGE_ENGLISH:
                selected_language = LANGUAGE_ENGLISH
            elif action_id == ACTION_LANGUAGE_BENGALI:
                selected_language = LANGUAGE_BENGALI

            if not selected_language:
                cursor.execute(
                    """
                    UPDATE whatsapp_conversation_state
                    SET conversation_state = %s,
                        last_channel_message_id = %s,
                        updated_on = NOW()
                    WHERE id = %s
                    """,
                    (STATE_AWAITING_LANGUAGE, channel_message_id, state_id),
                )
                conn.commit()
                send_language_selection_message(sender_mobile)
                logger.info("WHATSAPP_LANGUAGE_SELECTION_REPROMPT mobile=%s", sender_mobile)
                return True

            selected_language = _set_language_selection(
                cursor,
                state_id,
                selected_language,
                channel_message_id=channel_message_id,
            )
            conn.commit()
            send_language_confirmation_message(sender_mobile, selected_language)

            # Refresh state after committing the choice.
            state = get_conversation_state(cursor, sender_mobile) or {}
            current_state = state.get("conversation_state") or STATE_NEW
            logger.info(
                "WHATSAPP_LANGUAGE_SELECTED mobile=%s language=%s",
                sender_mobile,
                selected_language,
            )
        else:
            selected_language = _language_from_state(state)

        notice = get_active_notice(cursor, NOTICE_CODE_AGENCY)
        if not notice:
            if selected_language == LANGUAGE_BENGALI:
                message = (
                    "⚠️ এই মুহূর্তে গোপনীয়তা সংক্রান্ত তথ্য লোড করা সম্ভব হচ্ছে না। "
                    "অনুগ্রহ করে পরে আবার চেষ্টা করুন।"
                )
            else:
                message = (
                    "⚠️ We are temporarily unable to load the Privacy Information. "
                    "Please try again later."
                )
            send_text(sender_mobile, message, preview_url=False)
            return True

        # Consume stale pre-deployment control buttons; never parse them.
        if action_id in {ACTION_ROLE_AGENCY, ACTION_CONSENT_AGREE, ACTION_CONSENT_DECLINE}:
            if action_id == ACTION_CONSENT_DECLINE:
                send_privacy_not_confirmed_closure_message(sender_mobile)
                _reset_conversation_cycle(
                    sender_mobile=sender_mobile,
                    consent_required_flag=1,
                    log_event="WHATSAPP_LEGACY_PRIVACY_DECLINE_CLOSED",
                )
                return True

            review_session = _send_new_privacy_review(
                cursor=cursor,
                sender_mobile=sender_mobile,
                notice=notice,
                channel_message_id=channel_message_id,
            )
            update_conversation_state(
                cursor=cursor,
                state_id=state_id,
                conversation_state=STATE_RECONSENT_REQUIRED,
                user_category=CATEGORY_AGENCY,
                last_notice_id=notice.get("id"),
                last_channel_message_id=channel_message_id,
                consent_required_flag=1,
            )
            conn.commit()
            send_privacy_notice(
                sender_mobile=sender_mobile,
                notice=notice,
                raw_token=review_session.get("raw_token"),
                language=selected_language,
            )
            return True

        valid_consent = get_valid_current_consent(
            cursor,
            sender_mobile,
            CATEGORY_AGENCY,
        )

        # ----------------------------------------------------
        # Existing/current privacy confirmation.
        # ----------------------------------------------------
        if valid_consent:
            if current_state != STATE_READY_FOR_SUBMISSION:
                # The selected-language Welcome follows the language choice (or
                # begins a reopened cycle after the language choice was made).
                send_welcome_message(sender_mobile, selected_language)

                # IMPORTANT: send CTA first; only then open parser gate.
                send_existing_consent_ready_message(sender_mobile, selected_language)

                update_conversation_state(
                    cursor=cursor,
                    state_id=state_id,
                    conversation_state=STATE_READY_FOR_SUBMISSION,
                    user_category=CATEGORY_AGENCY,
                    active_consent_id=valid_consent.get("id"),
                    last_notice_id=notice.get("id"),
                    last_channel_message_id=channel_message_id,
                    consent_required_flag=0,
                )
                conn.commit()
                logger.info(
                    "WHATSAPP_SUBMISSION_GATE_OPENED mobile=%s trigger_message_id=%s language=%s",
                    sender_mobile,
                    channel_message_id,
                    selected_language,
                )
                return True

            # Gate was already open before this inbound event.
            update_conversation_state(
                cursor=cursor,
                state_id=state_id,
                conversation_state=STATE_READY_FOR_SUBMISSION,
                user_category=CATEGORY_AGENCY,
                active_consent_id=valid_consent.get("id"),
                last_notice_id=notice.get("id"),
                last_channel_message_id=channel_message_id,
                consent_required_flag=0,
            )
            conn.commit()
            return False

        # ----------------------------------------------------
        # No valid consent: show selected-language Welcome + privacy CTA.
        # ----------------------------------------------------
        send_welcome_message(sender_mobile, selected_language)
        review_session = _send_new_privacy_review(
            cursor=cursor,
            sender_mobile=sender_mobile,
            notice=notice,
            channel_message_id=channel_message_id,
        )
        update_conversation_state(
            cursor=cursor,
            state_id=state_id,
            conversation_state=(
                STATE_RECONSENT_REQUIRED
                if current_state not in {STATE_NEW, STATE_AWAITING_LANGUAGE}
                else STATE_AWAITING_CONSENT
            ),
            user_category=CATEGORY_AGENCY,
            last_notice_id=notice.get("id"),
            last_channel_message_id=channel_message_id,
            consent_required_flag=1,
        )
        conn.commit()
        send_privacy_notice(
            sender_mobile=sender_mobile,
            notice=notice,
            raw_token=review_session.get("raw_token"),
            language=selected_language,
        )
        logger.info(
            "WHATSAPP_PRIVACY_REVIEW_ISSUED mobile=%s session_id=%s language=%s notice_version=%s",
            sender_mobile,
            review_session.get("id"),
            selected_language,
            notice.get("notice_version"),
        )
        return True

    except Exception:
        conn.rollback()
        logger.exception(
            "WHATSAPP_CONSENT_FLOW_ERROR mobile=%s channel_message_id=%s",
            sender_mobile,
            channel_message_id,
        )
        raise
    finally:
        cursor.close()
        conn.close()

