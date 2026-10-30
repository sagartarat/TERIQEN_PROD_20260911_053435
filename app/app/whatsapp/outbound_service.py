import logging
import os

import requests


logger = logging.getLogger(__name__)


class WhatsAppOutboundError(Exception):
    """Raised when an outbound WhatsApp message cannot be sent."""
    pass


def _get_required_env(name):
    """
    Read a required environment variable.

    We deliberately do not hard-code:
    - Meta access token
    - Phone Number ID
    - Graph API version
    """

    value = os.getenv(name)

    if not value:
        raise WhatsAppOutboundError(
            f"Required WhatsApp configuration is missing: {name}"
        )

    return value.strip()


def _get_messages_url():
    """
    Build the Meta WhatsApp Cloud API Messages endpoint.

    Example shape:
    https://graph.facebook.com/<VERSION>/<PHONE_NUMBER_ID>/messages
    """

    graph_version = _get_required_env(
        "WHATSAPP_GRAPH_VERSION"
    )

    phone_number_id = _get_required_env(
        "WHATSAPP_PHONE_NUMBER_ID"
    )

    return (
        f"https://graph.facebook.com/"
        f"{graph_version}/"
        f"{phone_number_id}/messages"
    )


def _get_headers():
    """
    Build authorization headers securely from environment variables.
    """

    access_token = _get_required_env(
        "WHATSAPP_ACCESS_TOKEN"
    )

    return {
        "Authorization": f"Bearer {access_token}",
        "Content-Type": "application/json",
    }


def _post_message(payload):
    """
    Common function used by all outbound WhatsApp messages.

    Returns:
        Full Meta response JSON.

    Raises:
        WhatsAppOutboundError if Meta rejects the request,
        connection fails, or response JSON is invalid.
    """

    try:
        response = requests.post(
            _get_messages_url(),
            headers=_get_headers(),
            json=payload,
            timeout=20,
        )

    except requests.RequestException as exc:
        logger.exception(
            "WHATSAPP_OUTBOUND_CONNECTION_ERROR"
        )

        raise WhatsAppOutboundError(
            f"Unable to connect to Meta WhatsApp API: {exc}"
        ) from exc

    if not response.ok:

        # Do not print access token.
        # Meta error body can help us diagnose failures.
        error_body = response.text[:2000]

        logger.error(
            "WHATSAPP_OUTBOUND_META_ERROR "
            "status_code=%s response=%s",
            response.status_code,
            error_body,
        )

        raise WhatsAppOutboundError(
            "Meta WhatsApp API rejected the outbound message. "
            f"HTTP status: {response.status_code}"
        )

    try:
        result = response.json()

    except ValueError as exc:
        logger.exception(
            "WHATSAPP_OUTBOUND_INVALID_JSON"
        )

        raise WhatsAppOutboundError(
            "Meta WhatsApp API returned an invalid JSON response."
        ) from exc

    logger.info(
        "WHATSAPP_OUTBOUND_SUCCESS "
        "message_id=%s",
        extract_outbound_message_id(result),
    )

    return result


def extract_outbound_message_id(meta_response):
    """
    Extract Meta's outbound WhatsApp message ID.

    Expected response usually contains:

    {
        "messages": [
            {
                "id": "wamid...."
            }
        ]
    }

    Returns None if not available.
    """

    if not isinstance(meta_response, dict):
        return None

    messages = meta_response.get("messages") or []

    if not messages:
        return None

    first_message = messages[0]

    if not isinstance(first_message, dict):
        return None

    return first_message.get("id")


def send_text(to_mobile, text, preview_url=True):
    """
    Send an ordinary WhatsApp text message.
    """

    if not to_mobile:
        raise ValueError("Recipient mobile number is required.")

    if not text:
        raise ValueError("WhatsApp message text is required.")

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": str(to_mobile),
        "type": "text",
        "text": {
            "preview_url": bool(preview_url),
            "body": str(text),
        },
    }

    return _post_message(payload)


def send_reply_buttons(
    to_mobile,
    body_text,
    buttons,
    header_text=None,
    footer_text=None,
):
    """
    Send an interactive WhatsApp reply-button message.

    This function is retained because other TERIQEN WhatsApp flows
    may still use ordinary reply buttons.

    buttons example:

    [
        {
            "id": "ROLE_AGENCY",
            "title": "Medical Tourism Agency"
        }
    ]

    or:

    [
        {
            "id": "CONSENT_AGREE",
            "title": "I Confirm & Continue"
        },
        {
            "id": "CONSENT_DECLINE",
            "title": "I Do Not Agree"
        }
    ]
    """

    if not to_mobile:
        raise ValueError(
            "Recipient mobile number is required."
        )

    if not body_text:
        raise ValueError(
            "Interactive message body is required."
        )

    if not buttons:
        raise ValueError(
            "At least one reply button is required."
        )

    if len(buttons) > 3:
        raise ValueError(
            "WhatsApp reply-button messages support "
            "a maximum of 3 buttons."
        )

    interactive_buttons = []

    for button in buttons:

        button_id = str(
            button.get("id", "")
        ).strip()

        button_title = str(
            button.get("title", "")
        ).strip()

        if not button_id:
            raise ValueError(
                "Every reply button must have an id."
            )

        if not button_title:
            raise ValueError(
                "Every reply button must have a title."
            )

        interactive_buttons.append(
            {
                "type": "reply",
                "reply": {
                    "id": button_id,
                    "title": button_title,
                },
            }
        )

    interactive = {
        "type": "button",
        "body": {
            "text": str(body_text),
        },
        "action": {
            "buttons": interactive_buttons,
        },
    }

    if header_text:
        interactive["header"] = {
            "type": "text",
            "text": str(header_text),
        }

    if footer_text:
        interactive["footer"] = {
            "text": str(footer_text),
        }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": str(to_mobile),
        "type": "interactive",
        "interactive": interactive,
    }

    return _post_message(payload)


def send_cta_url_button(
    to_mobile,
    body_text,
    button_text,
    url,
    header_text=None,
    footer_text=None,
):
    """
    Send an interactive Call-To-Action URL message.

    This is used by the new TERIQEN Agency privacy-review flow.

    The button opens a TERIQEN-controlled privacy review URL.
    The URL contains a one-time opaque session token; it must not
    contain patient data or the Meta access token.

    Example:
        send_cta_url_button(
            to_mobile="919999999999",
            body_text="Please review the Privacy Information.",
            button_text="View Privacy Info",
            url="https://app.example.com/whatsapp/privacy/<token>",
        )
    """

    if not to_mobile:
        raise ValueError(
            "Recipient mobile number is required."
        )

    if not body_text:
        raise ValueError(
            "CTA message body is required."
        )

    if not button_text:
        raise ValueError(
            "CTA button text is required."
        )

    if not url:
        raise ValueError(
            "CTA URL is required."
        )

    clean_url = str(url).strip()

    if not (
        clean_url.startswith("https://")
        or clean_url.startswith("http://")
    ):
        raise ValueError(
            "CTA URL must start with http:// or https://."
        )

    interactive = {
        "type": "cta_url",
        "body": {
            "text": str(body_text),
        },
        "action": {
            "name": "cta_url",
            "parameters": {
                "display_text": str(button_text),
                "url": clean_url,
            },
        },
    }

    if header_text:
        interactive["header"] = {
            "type": "text",
            "text": str(header_text),
        }

    if footer_text:
        interactive["footer"] = {
            "text": str(footer_text),
        }

    payload = {
        "messaging_product": "whatsapp",
        "recipient_type": "individual",
        "to": str(to_mobile),
        "type": "interactive",
        "interactive": interactive,
    }

    return _post_message(payload)