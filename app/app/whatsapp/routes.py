import hashlib
import hmac

from pathlib import Path

from flask import (
    abort,
    current_app,
    jsonify,
    make_response,
    render_template,
    request,
    send_file,
)
from flask_login import login_required

from app.common.logger import get_logger
from app.common.access_control import has_screen_permission
from app.extensions import csrf
from app.whatsapp import whatsapp_bp
from app.whatsapp.webhook_service import process_whatsapp_webhook
from app.whatsapp.consent_service import (
    cancel_privacy_review,
    confirm_privacy_review,
    register_privacy_review_activity,
    register_privacy_review_open,
    register_privacy_review_scrolled,
    send_privacy_not_confirmed_closure_message,
    send_submission_instructions,
    get_preferred_language,
    is_submission_gate_ready,
    mark_submission_gate_ready_after_instruction,
)

logger = get_logger("whatsapp_parser")


def _valid_meta_signature(raw_body: bytes) -> bool:
    require_signature = bool(current_app.config.get("WHATSAPP_REQUIRE_SIGNATURE"))
    app_secret = str(current_app.config.get("META_APP_SECRET") or "").strip()

    if not app_secret:
        return not require_signature

    supplied = request.headers.get("X-Hub-Signature-256", "")
    if not supplied.startswith("sha256="):
        return False

    expected = "sha256=" + hmac.new(
        app_secret.encode("utf-8"), raw_body, hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(expected, supplied)


def _privacy_no_cache_response(content, status=200):
    """
    Privacy review pages contain an opaque one-time token in the URL.
    Prevent browser/proxy caching and search-engine indexing.
    """

    response = make_response(content, status)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, private"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    response.headers["X-Robots-Tag"] = "noindex, nofollow, noarchive"
    response.headers["Referrer-Policy"] = "no-referrer"
    return response


def _as_true(value):
    """
    Accept only explicit affirmative values from the browser.
    """

    if value is True:
        return True

    return str(value or "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }


@whatsapp_bp.route("/webhook", methods=["GET", "POST"])
@csrf.exempt
def whatsapp_webhook():
    if not current_app.config.get("WHATSAPP_ENABLED"):
        return "WhatsApp integration disabled", 503

    if request.method == "GET":
        mode = request.args.get("hub.mode")
        token = request.args.get("hub.verify_token")
        challenge = request.args.get("hub.challenge")
        verify_token = str(current_app.config.get("WHATSAPP_VERIFY_TOKEN") or "")

        if mode == "subscribe" and token and hmac.compare_digest(token, verify_token):
            logger.info("WHATSAPP_WEBHOOK_VERIFIED")
            return challenge or "", 200

        logger.warning("WHATSAPP_WEBHOOK_VERIFICATION_FAILED")
        return "Verification failed", 403

    raw_body = request.get_data(cache=True)
    if not _valid_meta_signature(raw_body):
        logger.warning("WHATSAPP_WEBHOOK_SIGNATURE_REJECTED")
        return "Invalid signature", 401

    data = request.get_json(silent=True) or {}

    try:
        process_whatsapp_webhook(data)
    except Exception:
        current_app.logger.exception("WHATSAPP_WEBHOOK_PROCESS_ERROR")
        # Return 500 so Meta may retry transient failures instead of silently losing the event.
        return "PROCESSING_FAILED", 500

    return "EVENT_RECEIVED", 200


# ============================================================
# PUBLIC WHATSAPP PRIVACY REVIEW
# ============================================================

@whatsapp_bp.route("/privacy/<string:token>", methods=["GET"])
def whatsapp_privacy_review(token):
    """
    Open the Agency Privacy Information page.

    The raw token is an opaque one-time capability. Only its SHA-256
    hash is stored in the database by consent_service.py.
    """

    try:
        session = register_privacy_review_open(token)
    except Exception:
        current_app.logger.exception(
            "WHATSAPP_PRIVACY_PAGE_OPEN_ERROR"
        )
        session = None

    if not session:
        content = render_template(
            "privacy_information.html",
            token=None,
            review_session=None,
            privacy_text="",
            invalid_or_expired=True,
            already_confirmed=False,
            language=("BENGALI" if str(request.args.get("lang") or "").lower() == "bn" else "ENGLISH"),
        )
        return _privacy_no_cache_response(content, status=410)

    session_status = str(
        session.get("session_status") or ""
    ).strip().upper()

    language = get_preferred_language(session.get("sender_mobile"))

    content = render_template(
        "privacy_information.html",
        token=token,
        review_session=session,
        privacy_text=session.get("notice_text_snapshot") or "",
        invalid_or_expired=False,
        already_confirmed=(session_status == "CONFIRMED"),
        language=language,
    )

    return _privacy_no_cache_response(content, status=200)


@whatsapp_bp.route(
    "/privacy/<string:token>/activity",
    methods=["POST"],
)
@csrf.exempt
def whatsapp_privacy_review_activity(token):
    """Refresh the 5-minute Privacy page inactivity window."""

    try:
        session = register_privacy_review_activity(token)
    except Exception:
        current_app.logger.exception(
            "WHATSAPP_PRIVACY_ACTIVITY_ENDPOINT_ERROR"
        )
        return jsonify(
            {
                "ok": False,
                "message": "Unable to refresh this Privacy Information session.",
            }
        ), 500

    if not session:
        return jsonify(
            {
                "ok": False,
                "message": (
                    "This Privacy Information link is invalid, expired, "
                    "or is no longer active."
                ),
            }
        ), 410

    return jsonify(
        {
            "ok": True,
        }
    ), 200


@whatsapp_bp.route(
    "/privacy/<string:token>/scrolled",
    methods=["POST"],
)
@csrf.exempt
def whatsapp_privacy_review_scrolled(token):
    """
    Record that the browser reached the bottom of the complete
    Privacy Information.

    The checkbox is enabled by the browser only after this endpoint
    confirms that the server recorded the SCROLLED state.
    """

    try:
        session = register_privacy_review_scrolled(token)
    except Exception:
        current_app.logger.exception(
            "WHATSAPP_PRIVACY_SCROLL_ENDPOINT_ERROR"
        )
        return jsonify(
            {
                "ok": False,
                "message": "Unable to record the privacy review at this time.",
            }
        ), 500

    if not session:
        return jsonify(
            {
                "ok": False,
                "message": (
                    "This Privacy Information link is invalid, expired, "
                    "or is no longer active."
                ),
            }
        ), 410

    return jsonify(
        {
            "ok": True,
            "reviewed": True,
        }
    ), 200


@whatsapp_bp.route(
    "/privacy/<string:token>/cancel",
    methods=["POST"],
)
@csrf.exempt
def whatsapp_privacy_review_cancel(token):
    """
    Handle the Agency's "Do Not Agree" action.

    This endpoint intentionally does NOT create a consent-refusal audit row.
    It only cancels the temporary privacy-review session, closes the current
    WhatsApp interaction, and sends a short terminal Thank You message.
    """

    payload = request.get_json(silent=True) or {}

    # Requiring an explicit JSON action prevents accidental/simple-form POSTs
    # against this public capability URL.
    if str(payload.get("action") or "").strip().upper() != "DO_NOT_AGREE":
        return jsonify(
            {
                "ok": False,
                "message": "Invalid privacy action.",
            }
        ), 400

    try:
        result = cancel_privacy_review(token)
    except Exception:
        current_app.logger.exception(
            "WHATSAPP_PRIVACY_CANCEL_ENDPOINT_ERROR"
        )
        return jsonify(
            {
                "ok": False,
                "message": "Unable to close this Privacy Information session at this time.",
            }
        ), 500

    if not result:
        return jsonify(
            {
                "ok": False,
                "message": (
                    "This Privacy Information link is invalid, expired, "
                    "or is no longer active."
                ),
            }
        ), 410

    if result.get("already_confirmed"):
        return jsonify(
            {
                "ok": False,
                "message": (
                    "Privacy confirmation has already been recorded for this link."
                ),
            }
        ), 409

    # Cancellation is already committed. A WhatsApp delivery problem must not
    # reopen the session or create a consent/refusal record.
    if not result.get("already_cancelled"):
        try:
            send_privacy_not_confirmed_closure_message(
                result.get("sender_mobile")
            )
        except Exception:
            current_app.logger.exception(
                "WHATSAPP_PRIVACY_CANCEL_NOTIFICATION_FAILED "
                "session_id=%s",
                result.get("session_id"),
            )

    return jsonify(
        {
            "ok": True,
            "already_cancelled": bool(result.get("already_cancelled")),
            "message": (
                "Privacy confirmation was not provided. "
                "This session has been closed."
            ),
        }
    ), 200


@whatsapp_bp.route(
    "/privacy/<string:token>/confirm",
    methods=["POST"],
)
@csrf.exempt
def whatsapp_privacy_review_confirm(token):
    """
    Final Agency acknowledgement.

    The browser submits one acknowledgement covering:
    - Agency authority to provide patient information; and
    - where the patient is a child, appropriate parent/guardian/
      authorised-representative authority.

    consent_service.py independently requires the review session to
    already be in SCROLLED state, so the browser cannot bypass the
    server-side scroll gate by enabling the checkbox manually.
    """

    payload = request.get_json(silent=True) or request.form or {}

    if not _as_true(payload.get("acknowledged")):
        return jsonify(
            {
                "ok": False,
                "message": "Please select the acknowledgement checkbox.",
            }
        ), 400

    try:
        result = confirm_privacy_review(token)
    except Exception:
        current_app.logger.exception(
            "WHATSAPP_PRIVACY_CONFIRM_ENDPOINT_ERROR"
        )
        return jsonify(
            {
                "ok": False,
                "message": "Unable to record the confirmation at this time.",
            }
        ), 500

    if not result:
        return jsonify(
            {
                "ok": False,
                "message": (
                    "The Privacy Information review is incomplete, "
                    "invalid, expired, or no longer current."
                ),
            }
        ), 409

    already_confirmed = bool(
        result.get("already_confirmed")
    )

    # Consent is already safely committed. Keep the parser gate CLOSED until
    # the selected-language "You may now send..." + Download Format CTA succeeds.
    sender_mobile = result.get("sender_mobile")
    if not is_submission_gate_ready(sender_mobile):
        try:
            send_submission_instructions(sender_mobile)
            if not mark_submission_gate_ready_after_instruction(
                sender_mobile,
                consent_id=result.get("consent_id"),
            ):
                raise RuntimeError(
                    "Privacy confirmation was recorded but the patient-submission gate could not be opened."
                )
        except Exception:
            current_app.logger.exception(
                "WHATSAPP_PRIVACY_CONFIRM_NOTIFICATION_FAILED "
                "session_id=%s consent_id=%s",
                result.get("session_id"),
                result.get("consent_id"),
            )
            return jsonify(
                {
                    "ok": False,
                    "confirmation_recorded": True,
                    "message": (
                        "Privacy confirmation was recorded, but the WhatsApp submission instruction could not be sent. "
                        "Please return to WhatsApp and send a new message to continue."
                    ),
                }
            ), 503

    return jsonify(
        {
            "ok": True,
            "already_confirmed": already_confirmed,
            "message": (
                "Privacy confirmation recorded. The WhatsApp submission instruction and download button have been sent."
            ),
        }
    ), 200


@whatsapp_bp.route("/patient-details-format", methods=["GET"])
def whatsapp_patient_details_format():
    """Download the single approved English patient-details TXT template."""
    template_path = (
        Path(current_app.root_path)
        / "static"
        / "downloads"
        / "Cadupius_Patient_Details_Format.txt"
    ).resolve()

    if not template_path.is_file():
        current_app.logger.error(
            "WHATSAPP_PATIENT_DETAILS_TEMPLATE_MISSING path=%s",
            template_path,
        )
        abort(404)

    response = send_file(
        template_path,
        as_attachment=True,
        download_name="Cadupius_Patient_Details_Format.txt",
        mimetype="text/plain",
        max_age=0,
        conditional=True,
    )
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Cache-Control"] = "no-store, max-age=0"
    return response


@whatsapp_bp.route("/media/<path:relative_path>", methods=["GET"])
@login_required
def whatsapp_secure_media(relative_path):
    if not (
        has_screen_permission("OPERATION_QUEUE", "view")
        or has_screen_permission("MANUAL_REVIEW", "view")
    ):
        abort(403)

    root = (Path(current_app.instance_path) / "uploads" / "whatsapp_docs").resolve()
    requested = (root / relative_path).resolve()

    try:
        requested.relative_to(root)
    except ValueError:
        abort(404)

    if not requested.is_file():
        abort(404)

    return send_file(
        requested,
        as_attachment=False,
        download_name=requested.name,
        max_age=0,
        conditional=True,
    )
