from pathlib import Path
from shutil import copy2

ROOT = Path(".")

template_file = ROOT / "app/templates/operationqueue.html"
js_file = ROOT / "app/static/js/operationqueue.js"
routes_file = ROOT / "app/queue_management/routes.py"
conversion_file = ROOT / "app/queue_management/conversion_service.py"
oq_service_file = ROOT / "app/queue_management/operation_queue_service.py"
queue_conversion_file = ROOT / "app/queue_management/queue_conversion_email_service.py"


def read_file(path):
    return path.read_bytes().decode("utf-8")


def write_file(path, text):
    path.write_bytes(text.encode("utf-8"))


def newline_for(text):
    return "\r\n" if "\r\n" in text else "\n"


# ============================================================
# 1. OPERATION QUEUE HTML
# ============================================================

text = read_file(template_file)
nl = newline_for(text)

if 'id="opqProcessWithoutEmailBtn"' not in text:

    old = nl.join([
        '                  <button',
        '                    class="opq-btn opq-btn-primary opq-btn-sm"',
        '                    type="button"',
        '                    id="opqOpenEmailWindowBtn"',
        '                    onclick="openEmailCommunicationWindow(getSelectedQueueId())"',
        '                  >Open Email Communication Window</button>',
    ])

    new = old + nl + nl.join([
        '                  <button',
        '                    class="opq-btn opq-btn-sm"',
        '                    type="button"',
        '                    id="opqProcessWithoutEmailBtn"',
        '                    onclick="processWithoutEmail(getSelectedQueueId())"',
        '                  >Process Without Email</button>',
    ])

    if old not in text:
        raise RuntimeError("Could not locate existing email button in operationqueue.html")

    text = text.replace(old, new, 1)
    write_file(template_file, text)


# ============================================================
# 2. JAVASCRIPT
# ============================================================

text = read_file(js_file)
nl = newline_for(text)

if "window.processWithoutEmail" not in text:

    js_code = r'''
window.processWithoutEmail = async function(queueId) {
  if (!queueId) {
    alert("Please select a queue row first.");
    return;
  }

  const confirmed = window.confirm(
    "Process Without Email?\n\n" +
    "No email will be sent. CADUPIUS will validate the case and proceed " +
    "with Patient, Lead and Workflow creation."
  );

  if (!confirmed) {
    return;
  }

  const button = document.getElementById("opqProcessWithoutEmailBtn");
  const originalText = button ? button.textContent : "";

  if (button) {
    button.disabled = true;
    button.textContent = "Processing...";
  }

  try {
    const requestIdEl = document.getElementById("request_id");

    const requestId =
      (requestIdEl && requestIdEl.value
        ? String(requestIdEl.value).trim()
        : "") ||
      `NOEMAIL-${queueId}-${Date.now()}`;

    const response = await fetch(
      "/api/operation-queue/process-without-email",
      {
        method: "POST",
        headers: {
          "Content-Type": "application/json",
          "X-Requested-With": "XMLHttpRequest"
        },
        body: JSON.stringify({
          queue_id: queueId,
          request_id: requestId
        })
      }
    );

    const data = await response.json().catch(() => ({}));

    if (!response.ok || !data.success) {
      alert(
        data.message ||
        "Unable to process the case without email."
      );
      return;
    }

    alert(
      data.message ||
      "Case processed successfully. Patient, Lead and Workflow processing completed. Email communication was skipped."
    );

    window.location.reload();

  } catch (error) {
    console.error("PROCESS_WITHOUT_EMAIL_ERROR", error);

    alert(
      "Unable to process the case without email. Please try again."
    );

  } finally {
    if (button) {
      button.disabled = false;
      button.textContent = originalText || "Process Without Email";
    }
  }
};
'''

    text = text.rstrip() + nl + nl + js_code.strip().replace("\n", nl) + nl
    write_file(js_file, text)


# ============================================================
# 3. CONVERSION SERVICE WRAPPER
# ============================================================

text = read_file(conversion_file)
nl = newline_for(text)

if "def process_without_email_service(" not in text:

    code = r'''

def process_without_email_service(
    queue_id: int,
    request_id: str,
    user_code: str,
) -> Dict[str, Any]:
    try:
        return oq_service.process_without_email(
            queue_id=queue_id,
            request_id=request_id,
            user_code=user_code,
        )

    except Exception as exc:
        return {
            "success": False,
            "status": "failed",
            "message": str(exc),
        }
'''

    text = text.rstrip() + nl + nl + code.strip().replace("\n", nl) + nl
    write_file(conversion_file, text)


# ============================================================
# 4. CORE PROCESS-WITHOUT-EMAIL SERVICE
# ============================================================

text = read_file(queue_conversion_file)
nl = newline_for(text)

if "def process_without_email(" not in text:

    code = r'''

# ================================
# PROCESS WITHOUT EMAIL
# ================================
def process_without_email(queue_id, request_id, user_code):
    """
    Convert an Operation Queue record into the existing Patient / Lead /
    Workflow business structure without sending an email.

    This is intentionally separate from send_email_with_conversion()
    so the existing email flow remains unchanged.
    """

    conn = _oq_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        queue_row = _fetch_latest_queue_row_by_id(conn, queue_id)

        if not queue_row:
            return {
                "success": False,
                "status": "not_found",
                "message": "Operation queue row not found.",
            }

        workflow_status = _safe_str(
            queue_row.get("wrkflw_status")
        ).upper()

        if workflow_status == "CLOSED":
            return {
                "success": False,
                "status": "validation_failed",
                "message": (
                    "Process Without Email is disabled because "
                    "the workflow status is CLOSED."
                ),
            }

        converted_flag = _safe_str(
            queue_row.get("converted_flag")
        ).lower()

        if converted_flag in ("1", "true", "yes"):
            return {
                "success": True,
                "status": "already_completed",
                "message": "This case has already been processed.",
                "queue_id": queue_id,
                "refresh_parent": True,
            }

    finally:
        cursor.close()
        conn.close()

    # Use the exact same conversion engine as the existing email flow.
    conversion_result = convert_queue_to_business_records(
        queue_id=queue_id,
        request_id=request_id,
        user_code=user_code,
    )

    if not conversion_result.get("success"):
        return conversion_result

    conn = _oq_db_connection()
    cursor = conn.cursor(dictionary=True)

    try:
        queue_row = _fetch_latest_queue_row_by_id(conn, queue_id)

        if not queue_row:
            raise Exception(
                "Operation queue row could not be reloaded after conversion."
            )

        cursor.execute(
            """
            UPDATE operation_queue_master
            SET converted_flag = 1,
                converted_on = COALESCE(converted_on, NOW()),
                converted_by = %s,
                conversion_status = 'COMPLETED',
                updated_on = NOW()
            WHERE id = %s
            """,
            (user_code, queue_id),
        )

        _close_active_sla_logs_for_thread(
            conn=conn,
            channel_thread_id=queue_row.get("channel_thread_id"),
            close_status="MET",
            remarks=(
                "SLA closed due to successful processing "
                "without email."
            ),
        )

        conn.commit()

        return {
            "success": True,
            "status": "completed_without_email",
            "message": (
                "Case processed successfully. Patient, Lead and Workflow "
                "processing completed. Email communication was skipped."
            ),
            "queue_id": queue_id,
            "passport_no": conversion_result.get("passport_no"),
            "patient_created": bool(
                conversion_result.get("patient_created")
            ),
            "patient_action": conversion_result.get("patient_action"),
            "workflow_action": conversion_result.get("workflow_action"),
            "wrkflw_status": conversion_result.get("wrkflw_status"),
            "email_skipped": True,
            "refresh_parent": True,
        }

    except Exception as exc:
        conn.rollback()

        return {
            "success": False,
            "status": "failed",
            "message": str(exc),
        }

    finally:
        cursor.close()
        conn.close()
'''

    text = text.rstrip() + nl + nl + code.strip().replace("\n", nl) + nl
    write_file(queue_conversion_file, text)


# ============================================================
# 5. EXPORT THROUGH operation_queue_service
# ============================================================

text = read_file(oq_service_file)
nl = newline_for(text)

marker = "from app.queue_management.queue_conversion_email_service import ("

if marker not in text:
    raise RuntimeError(
        "Could not locate queue_conversion_email_service import block."
    )

start = text.index(marker)
end = text.index(")", start)

block = text[start:end]

if "process_without_email" not in block:

    target = "    send_email_with_conversion,"

    if target not in block:
        raise RuntimeError(
            "Could not locate send_email_with_conversion import."
        )

    new_block = block.replace(
        target,
        "    process_without_email," + nl + target,
        1,
    )

    text = text[:start] + new_block + text[end:]
    write_file(oq_service_file, text)


# ============================================================
# 6. ROUTE IMPORT
# ============================================================

text = read_file(routes_file)
nl = newline_for(text)

old_import = (
    "from app.queue_management.conversion_service import "
    "send_with_conversion"
)

if "process_without_email_service" not in text:

    if old_import not in text:
        raise RuntimeError(
            "Could not locate conversion_service import in routes.py"
        )

    new_import = nl.join([
        "from app.queue_management.conversion_service import (",
        "    process_without_email_service,",
        "    send_with_conversion,",
        ")",
    ])

    text = text.replace(old_import, new_import, 1)


# ============================================================
# 7. NEW LOGIN-PROTECTED API ROUTE
# ============================================================

if '"/api/operation-queue/process-without-email"' not in text:

    anchor = '    @app.route("/manual_review")'

    if anchor not in text:
        raise RuntimeError(
            "Could not locate manual_review route insertion point."
        )

    route_code = r'''
    @app.route(
        "/api/operation-queue/process-without-email",
        methods=["POST"],
    )
    @login_required
    def operation_queue_process_without_email():
        try:
            payload = (
                request.get_json(silent=True)
                or request.form.to_dict()
            )

            queue_id = payload.get("queue_id")
            request_id = payload.get("request_id")

            if not queue_id:
                return jsonify({
                    "success": False,
                    "status": "validation_failed",
                    "message": "queue_id is required.",
                }), 400

            if not request_id:
                return jsonify({
                    "success": False,
                    "status": "validation_failed",
                    "message": "request_id is required.",
                }), 400

            try:
                queue_id_int = int(queue_id)
            except Exception:
                return jsonify({
                    "success": False,
                    "status": "validation_failed",
                    "message": f"Invalid queue_id: {queue_id}",
                }), 400

            user_code = (
                getattr(current_user, "user_code", None)
                or getattr(current_user, "id", None)
                or "SYSTEM"
            )

            current_app.logger.info(
                "Operation queue process without email requested "
                "queue_id=%s request_id=%s user=%s",
                queue_id,
                request_id,
                user_code,
            )

            result = process_without_email_service(
                queue_id=queue_id_int,
                request_id=str(request_id),
                user_code=str(user_code),
            )

            if result.get("success"):
                return jsonify(result), 200

            status_map = {
                "already_completed": 200,
                "not_found": 404,
                "validation_failed": 400,
                "already_processing": 409,
                "conversion_failed": 500,
                "failed": 500,
            }

            http_status = status_map.get(
                result.get("status"),
                500,
            )

            return jsonify(result), http_status

        except Exception:
            current_app.logger.exception(
                "Operation queue process without email failed "
                "queue_id=%s request_id=%s",
                locals().get("queue_id"),
                locals().get("request_id"),
            )

            return jsonify({
                "success": False,
                "status": "failed",
                "message": (
                    "Unable to process the case without email."
                ),
            }), 500


'''

    route_code = route_code.replace("\n", nl)

    text = text.replace(
        anchor,
        route_code + anchor,
        1,
    )

write_file(routes_file, text)

print("PROCESS WITHOUT EMAIL PATCH APPLIED SUCCESSFULLY")
