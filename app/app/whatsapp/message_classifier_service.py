import re
import json

from app.whatsapp.thread_resolver_service import resolve_existing_thread, extract_passport_no


WHATSAPP_ATTACHMENT_GROUPING_SECONDS = 60




def _debug_print(*args, **kwargs):
    import os
    if str(os.getenv("WHATSAPP_DEBUG_LOGGING", "false")).strip().lower() in {"1", "true", "yes", "on"}:
        print(*args, **kwargs)



def _extract_patient_passport_for_ingestion_rule(message_body):
    """
    Extract the Patient Passport Number used by the same-passport gate.

    IMPORTANT:
    - Parse the labelled WhatsApp field FIRST so short values such as P0005 are
      handled deterministically.
    - Only fall back to thread_resolver_service.extract_passport_no afterwards.
    - Return only uppercase alphanumeric passport text.
    """
    text = str(message_body or "")
    if not text.strip():
        return None

    text_upper = text.upper()

    patterns = (
        r"(?:^|[\r\n])\s*PATIENT\s+PASSPORT\s*(?:NO|NUMBER|#)?\s*[:\-\t ]*\s*([A-Z0-9]{4,20})\b",
        r"(?:^|[\r\n])\s*PASSPORT\s*(?:NO|NUMBER|#)?\s*[:\-\t ]*\s*([A-Z0-9]{4,20})\b",
    )
    for pattern in patterns:
        match = re.search(pattern, text_upper, flags=re.MULTILINE)
        if match:
            value = re.sub(r"[^A-Z0-9]", "", str(match.group(1) or "").upper())
            if value:
                return value

    existing = extract_passport_no(message_body)
    if existing:
        value = re.sub(r"[^A-Z0-9]", "", str(existing).upper())
        return value or None

    return None


def _get_same_passport_ingestion_decision(cursor, passport_no):
    """
    Decide whether a NEW structured patient case may be created for the same
    passport number.

    IMPORTANT BUSINESS BOUNDARY
    ---------------------------
    Duplicate judgement is made ONLY from patient_wrkflw, because that table
    represents an established patient workflow/case.

    The following are intake/work-in-progress records and MUST NOT, by
    themselves, make a submission a duplicate:
    - channel_message_log
    - operation_queue_master
    - Manual Review queue/rows

    Why:
    The Agency may send corrected patient details, additional information or
    new medical reports while the original submission is still being collected,
    reviewed or processed. Those events must remain eligible for normal
    grouping/follow-up/thread-resolution logic.

    wrkflwrule_master.allow_new_case_same_passport is evaluated only when a
    patient_wrkflw row already exists:
    - 0 -> reject a genuinely new structured case for the same passport;
    - 1 -> allow a fresh case/thread;
    - missing rule -> reject fail-closed.

    No workflow status names are hard-coded in Python.
    """
    passport_no = re.sub(
        r"[^A-Z0-9]",
        "",
        str(passport_no or "").strip().upper(),
    )
    if not passport_no:
        return {
            "passport_no": "",
            "has_existing_workflow": False,
            "rule_found": False,
            "allow_new_case": True,
            "latest_workflow_status": "",
            "workflow_status_source": "",
            "existing_queue_id": None,
            "existing_thread_id": None,
        }

    # patient_wrkflw is the ONLY source that can activate the duplicate rule.
    cursor.execute(
        """
        SELECT
            pw.wrkflw_status AS latest_workflow_status
        FROM patient_wrkflw pw
        WHERE UPPER(TRIM(COALESCE(pw.patpassport_no, '')))
            = UPPER(TRIM(%s))
        ORDER BY
            pw.entered_date DESC,
            COALESCE(pw.ld_seqno, 0) DESC,
            pw.ld_internalno DESC
        LIMIT 1
        """,
        (passport_no,),
    )
    workflow_row = cursor.fetchone() or {}

    # No established patient workflow means NO duplicate judgement here.
    # Existing channel/Operation Queue/Manual Review records are deliberately
    # left to the normal resolver/grouping/follow-up flow below.
    if not workflow_row:
        return {
            "passport_no": passport_no,
            "has_existing_workflow": False,
            "rule_found": False,
            "allow_new_case": True,
            "latest_workflow_status": "",
            "workflow_status_source": "",
            "existing_queue_id": None,
            "existing_thread_id": None,
        }

    effective_status = str(
        workflow_row.get("latest_workflow_status") or ""
    ).strip().upper()

    # Existing patient workflow with no usable status is a data-integrity issue.
    # Fail closed rather than creating another established case silently.
    if not effective_status:
        return {
            "passport_no": passport_no,
            "has_existing_workflow": True,
            "rule_found": False,
            "allow_new_case": False,
            "latest_workflow_status": "UNKNOWN",
            "workflow_status_source": "PATIENT_WORKFLOW",
            "existing_queue_id": None,
            "existing_thread_id": None,
        }

    try:
        cursor.execute(
            """
            SELECT
                status_name,
                allow_new_case_same_passport
            FROM wrkflwrule_master
            WHERE UPPER(TRIM(COALESCE(status_name, '')))
                = UPPER(TRIM(%s))
            LIMIT 1
            """,
            (effective_status,),
        )
        rule_row = cursor.fetchone() or {}
    except Exception as exc:
        if "allow_new_case_same_passport" in str(exc):
            raise RuntimeError(
                "Workflow Rule Master migration is missing: "
                "wrkflwrule_master.allow_new_case_same_passport"
            ) from exc
        raise

    rule_found = bool(str(rule_row.get("status_name") or "").strip())

    try:
        allow_new_case = (
            rule_found
            and int(rule_row.get("allow_new_case_same_passport") or 0) == 1
        )
    except (TypeError, ValueError):
        allow_new_case = False

    return {
        "passport_no": passport_no,
        "has_existing_workflow": True,
        "rule_found": rule_found,
        "allow_new_case": bool(allow_new_case),
        "latest_workflow_status": effective_status,
        "workflow_status_source": "PATIENT_WORKFLOW",
        "existing_queue_id": None,
        "existing_thread_id": None,
    }

def _duplicate_rejection_text(decision):
    decision = decision or {}
    passport_no = str(decision.get("passport_no") or "").strip().upper()
    status = str(
        decision.get("latest_workflow_status") or "UNKNOWN"
    ).strip().upper()

    if not decision.get("rule_found"):
        classifier_reason = (
            f"Duplicate structured patient submission rejected for Passport "
            f"{passport_no}. Existing workflow status is {status}; "
            "Workflow Rule Master re-ingestion rule is missing (fail-closed)."
        )
        remarks = (
            f"Duplicate ingestion rejected. Passport {passport_no} already has "
            f"workflow status '{status}'. No matching same-passport re-ingestion "
            "rule is configured in wrkflwrule_master; record rejected fail-closed."
        )
    else:
        classifier_reason = (
            f"Duplicate structured patient submission rejected for Passport "
            f"{passport_no}. Existing workflow status is {status}; "
            "wrkflwrule_master does not permit a new same-passport case."
        )
        remarks = (
            f"Duplicate ingestion rejected. Passport {passport_no} already has "
            f"workflow status '{status}'. New same-passport case is not permitted "
            "by Workflow Rule Master."
        )

    return classifier_reason[:500], remarks[:500]


def find_recent_structured_message_context(
    cursor,
    message_log_id,
    sender_mobile,
    window_seconds=WHATSAPP_ATTACHMENT_GROUPING_SECONDS,
):
    """
    Find the immediately preceding structured WhatsApp request from the same
    sender when Meta delivers additional media as separate message events.

    This lookup is intentionally limited to a 60-second event-time window and
    does not alter the existing passport, mobile, or channel-thread resolution
    flow. It is used only when an attachment-only message could not yet resolve
    an operation queue row.
    """
    if not message_log_id or not sender_mobile:
        return None

    try:
        cursor.execute(
            """
            SELECT
                current_msg.id AS current_message_log_id,
                current_msg.received_on AS current_received_on,
                previous_msg.id AS previous_message_log_id,
                previous_msg.channel_message_id AS previous_channel_message_id,
                previous_msg.channel_thread_id,
                previous_msg.linked_queue_id,
                previous_msg.parse_status,
                previous_msg.message_type,
                previous_msg.received_on AS previous_received_on
            FROM channel_message_log current_msg
            INNER JOIN channel_message_log previous_msg
                ON previous_msg.id <> current_msg.id
               AND UPPER(TRIM(COALESCE(previous_msg.channel_code, '')))
                   = UPPER(TRIM(COALESCE(current_msg.channel_code, '')))
               AND REPLACE(
                       REPLACE(
                           REPLACE(
                               REPLACE(COALESCE(previous_msg.sender_mobile, ''), '+', ''),
                               '-',
                               ''
                           ),
                           ' ',
                           ''
                       ),
                       CHAR(9),
                       ''
                   )
                   = REPLACE(
                       REPLACE(
                           REPLACE(
                               REPLACE(COALESCE(current_msg.sender_mobile, ''), '+', ''),
                               '-',
                               ''
                           ),
                           ' ',
                           ''
                       ),
                       CHAR(9),
                       ''
                   )
               AND ABS(TIMESTAMPDIFF(
                       SECOND,
                       previous_msg.received_on,
                       current_msg.received_on
                   )) <= %s
            WHERE current_msg.id = %s
              AND UPPER(TRIM(COALESCE(current_msg.channel_code, ''))) = 'WHATSAPP'
              AND REPLACE(
                      REPLACE(
                          REPLACE(
                              REPLACE(COALESCE(current_msg.sender_mobile, ''), '+', ''),
                              '-',
                              ''
                          ),
                          ' ',
                          ''
                      ),
                      CHAR(9),
                      ''
                  )
                  = REPLACE(
                      REPLACE(
                          REPLACE(
                              REPLACE(COALESCE(%s, ''), '+', ''),
                              '-',
                              ''
                          ),
                          ' ',
                          ''
                      ),
                      CHAR(9),
                      ''
                  )
              AND UPPER(TRIM(COALESCE(previous_msg.message_type, '')))
                  = 'STRUCTURED_REQUEST'
              AND COALESCE(previous_msg.junk_flag, 0) = 0
              AND COALESCE(previous_msg.duplicate_flag, 0) = 0
            ORDER BY
                ABS(TIMESTAMPDIFF(
                    SECOND,
                    previous_msg.received_on,
                    current_msg.received_on
                )) ASC,
                previous_msg.id ASC
            LIMIT 1
            """,
            (
                int(window_seconds or WHATSAPP_ATTACHMENT_GROUPING_SECONDS),
                message_log_id,
                sender_mobile,
            ),
        )
        row = cursor.fetchone()
        if not row:
            return None

        if isinstance(row, dict):
            return row

        columns = cursor.column_names
        return dict(zip(columns, row))

    except Exception as exc:
        _debug_print("find_recent_structured_message_context error:", str(exc), flush=True)
        return None


def group_nearby_attachment_messages(
    cursor,
    root_message_log_id,
    sender_mobile,
    window_seconds=WHATSAPP_ATTACHMENT_GROUPING_SECONDS,
):
    """
    Attach pending/incorrectly reviewed media rows to a structured root even
    when Meta delivered the media event before the caption/message event.

    No channel_thread_id is assigned here.  This is only the pre-parse
    linked_triage_id grouping required for the configurable 60-second window.
    """
    if not root_message_log_id or not sender_mobile:
        return 0

    try:
        cursor.execute(
            """
            UPDATE channel_message_log attachment_msg
            INNER JOIN channel_message_log root_msg
                ON root_msg.id = %s
               AND attachment_msg.id <> root_msg.id
               AND UPPER(TRIM(COALESCE(attachment_msg.channel_code, '')))
                   = UPPER(TRIM(COALESCE(root_msg.channel_code, '')))
               AND REPLACE(
                       REPLACE(
                           REPLACE(
                               REPLACE(COALESCE(attachment_msg.sender_mobile, ''), '+', ''),
                               '-',
                               ''
                           ),
                           ' ',
                           ''
                       ),
                       CHAR(9),
                       ''
                   )
                   = REPLACE(
                       REPLACE(
                           REPLACE(
                               REPLACE(COALESCE(root_msg.sender_mobile, ''), '+', ''),
                               '-',
                               ''
                           ),
                           ' ',
                           ''
                       ),
                       CHAR(9),
                       ''
                   )
               AND ABS(TIMESTAMPDIFF(
                       SECOND,
                       attachment_msg.received_on,
                       root_msg.received_on
                   )) <= %s
            SET
                attachment_msg.parsed_flag = 0,
                attachment_msg.parse_status = 'PENDING',
                attachment_msg.linked_triage_id = root_msg.id,
                attachment_msg.linked_queue_id = NULL,
                attachment_msg.channel_thread_id = NULL,
                attachment_msg.classifier_status = 'CLASSIFIED',
                attachment_msg.classifier_reason = %s,
                attachment_msg.thread_resolution_status = 'NO_THREAD_FOUND',
                attachment_msg.requires_manual_review = 0,
                attachment_msg.junk_flag = 0,
                attachment_msg.remarks = %s,
                attachment_msg.updated_on = NOW()
            WHERE UPPER(TRIM(COALESCE(attachment_msg.message_type, '')))
                    = 'ATTACHMENT_ONLY'
              AND UPPER(TRIM(COALESCE(attachment_msg.parse_status, '')))
                    IN ('PENDING', 'MANUAL_REVIEW')
              AND COALESCE(attachment_msg.junk_flag, 0) = 0
              AND COALESCE(attachment_msg.duplicate_flag, 0) = 0
              AND COALESCE(attachment_msg.channel_thread_id, '') = ''
            """,
            (
                root_message_log_id,
                int(window_seconds or WHATSAPP_ATTACHMENT_GROUPING_SECONDS),
                (
                    "Attachment grouped with structured WhatsApp request from "
                    "the same sender within 60 seconds"
                ),
                (
                    "Waiting for grouped structured WhatsApp request to finish "
                    "parsing."
                ),
            ),
        )
        return int(cursor.rowcount or 0)
    except Exception as exc:
        _debug_print("group_nearby_attachment_messages error:", str(exc), flush=True)
        return 0


def classify_and_process_message(
    cursor,
    message_log_id,
    sender_mobile,
    message_body,
    attachment_count=0,
    channel_thread_id=None
):
    """
    Classifies WhatsApp message after it is inserted into channel_message_log.

    Corrected Flow:
    1. Junk messages are marked as JUNK.
    2. Attachment-only messages try to link with existing queue.
    3. New patient/request messages are allowed as NEW_THREAD even if passport is not found in DB.
    4. Follow-up modification messages must match existing Patient Passport Number.
    5. Normal follow-up text links to existing queue if possible.
    6. Unknown messages go to manual review.
    """

    text = (message_body or "").strip()
    text_lower = normalize_text(text)

    try:
        if is_junk_message(text_lower):
            update_classification(
                cursor=cursor,
                message_log_id=message_log_id,
                message_type="JUNK",
                parse_status="JUNK",
                classifier_reason="Non-actionable greeting/conversational message",
                junk_flag=1,
                requires_manual_review=0,
                thread_resolution_status="NO_THREAD_FOUND",
                linked_queue_id=None,
                channel_thread_id=None
            )
            return

        if is_attachment_only_message(text, attachment_count):
            # The 60-second WhatsApp group takes priority over the general
            # existing-thread resolver.  Without this ordering an attachment
            # belonging to a newly received structured message can be linked
            # to an older queue row merely because the sender mobile matches.
            recent_structured_message = find_recent_structured_message_context(
                cursor=cursor,
                message_log_id=message_log_id,
                sender_mobile=sender_mobile,
                window_seconds=WHATSAPP_ATTACHMENT_GROUPING_SECONDS,
            )

            if recent_structured_message:
                update_classification(
                    cursor=cursor,
                    message_log_id=message_log_id,
                    message_type="ATTACHMENT_ONLY",
                    parse_status="PENDING",
                    classifier_reason=(
                        "Attachment-only message is grouped with the preceding "
                        "structured WhatsApp request received within 60 seconds"
                    ),
                    junk_flag=0,
                    requires_manual_review=0,
                    thread_resolution_status="NO_THREAD_FOUND",
                    linked_queue_id=None,
                    channel_thread_id=None,
                    linked_triage_id=recent_structured_message.get(
                        "previous_message_log_id"
                    ),
                )
                return

            # Do not resolve an attachment-only event against an older queue
            # while the new 60-second group is still forming.  A structured
            # message may arrive immediately after the media event, as shown by
            # channel_message_log ids 129 and 130.
            update_classification(
                cursor=cursor,
                message_log_id=message_log_id,
                message_type="ATTACHMENT_ONLY",
                parse_status="PENDING",
                classifier_reason=(
                    "Attachment is waiting for a structured WhatsApp "
                    "message from the same sender within 60 seconds"
                ),
                junk_flag=0,
                requires_manual_review=0,
                thread_resolution_status="NO_THREAD_FOUND",
                linked_queue_id=None,
                channel_thread_id=None
            )
            return

        # IMPORTANT:
        # A complete/structured patient submission is a NEW-CASE attempt.
        # Before resolving it as an existing thread, enforce the workflow-rule
        # gate using the Patient Passport Number.
        #
        # Genuine follow-up messages are handled by the dedicated follow-up
        # branches below and are not blocked by this new-case gate.
        if is_new_request_candidate(text_lower, attachment_count=attachment_count) or is_structured_request(text_lower):
            # Meta may deliver attachments before the structured text/caption.
            # Claim both earlier and later attachment rows into this root group.
            group_nearby_attachment_messages(
                cursor=cursor,
                root_message_log_id=message_log_id,
                sender_mobile=sender_mobile,
                window_seconds=WHATSAPP_ATTACHMENT_GROUPING_SECONDS,
            )

            passport_no = _extract_patient_passport_for_ingestion_rule(
                message_body
            )
            ingestion_decision = _get_same_passport_ingestion_decision(
                cursor=cursor,
                passport_no=passport_no,
            )

            # Existing patient workflow + rule denies re-ingestion (or the rule
            # is missing): reject at classifier level.  No Operation Queue row,
            # thread, assignment, or parser ingestion is created.
            if (
                ingestion_decision.get("has_existing_workflow")
                and not ingestion_decision.get("allow_new_case")
            ):
                classifier_reason, duplicate_remarks = _duplicate_rejection_text(
                    ingestion_decision
                )
                existing_queue_id = ingestion_decision.get(
                    "existing_queue_id"
                )

                update_classification(
                    cursor=cursor,
                    message_log_id=message_log_id,
                    message_type="STRUCTURED_REQUEST",
                    # PARSED is used as the existing terminal parser state.
                    # duplicate_flag=1 is the business outcome discriminator.
                    parse_status="PARSED",
                    classifier_reason=classifier_reason,
                    junk_flag=0,
                    requires_manual_review=0,
                    thread_resolution_status=(
                        "EXISTING_THREAD"
                        if existing_queue_id
                        else "NO_THREAD_FOUND"
                    ),
                    linked_queue_id=existing_queue_id,
                    channel_thread_id=None,
                    parsed_flag=1,
                    duplicate_flag=1,
                    remarks=duplicate_remarks,
                )
                return

            # If the latest workflow rule explicitly allows another case for the
            # same passport, this must be a NEW THREAD.  Never attach the new case
            # to the prior queue/thread merely because the passport already exists.
            if (
                ingestion_decision.get("has_existing_workflow")
                and ingestion_decision.get("allow_new_case")
            ):
                latest_status = str(
                    ingestion_decision.get("latest_workflow_status") or ""
                ).strip().upper()

                update_classification(
                    cursor=cursor,
                    message_log_id=message_log_id,
                    message_type="STRUCTURED_REQUEST",
                    parse_status="READY_FOR_PARSE",
                    classifier_reason=(
                        "New same-passport case permitted by Workflow Rule Master "
                        f"after workflow status {latest_status}"
                    )[:500],
                    junk_flag=0,
                    requires_manual_review=0,
                    thread_resolution_status="NEW_THREAD",
                    linked_queue_id=None,
                    channel_thread_id=None,
                    duplicate_flag=0,
                )
                return

            # No established patient workflow was found. Do NOT call this a duplicate.
            # Let the normal resolver decide whether this belongs to an existing
            # Operation Queue/Manual Review thread or is a genuinely new intake.
            resolved = resolve_existing_thread(
                cursor=cursor,
                sender_mobile=sender_mobile,
                message_body=message_body,
                channel_thread_id=channel_thread_id
            )

            if resolved:
                linked_queue_id = resolved.get("operation_queue_id")
                resolved_thread_id = resolved.get("channel_thread_id")

                if not resolved_thread_id and linked_queue_id:
                    resolved_thread_id = get_channel_thread_id_for_queue(
                        cursor=cursor,
                        operation_queue_id=linked_queue_id,
                    )

                update_classification(
                    cursor=cursor,
                    message_log_id=message_log_id,
                    message_type="STRUCTURED_REQUEST",
                    parse_status="READY_FOR_PARSE",
                    classifier_reason="Structured/new request detected and matched existing open queue/thread",
                    junk_flag=0,
                    requires_manual_review=0,
                    thread_resolution_status="EXISTING_THREAD",
                    linked_queue_id=linked_queue_id,
                    # Keep channel_message_log.thread_id NULL until parsing and
                    # queue creation complete successfully.
                    channel_thread_id=None,
                    duplicate_flag=0,
                )
            else:
                update_classification(
                    cursor=cursor,
                    message_log_id=message_log_id,
                    message_type="STRUCTURED_REQUEST",
                    parse_status="READY_FOR_PARSE",
                    classifier_reason=(
                        "New patient/request details detected and sent to parser; "
                        f"same-passport check found no existing case for {passport_no or 'UNKNOWN'}"
                    )[:500],
                    junk_flag=0,
                    requires_manual_review=0,
                    thread_resolution_status="NEW_THREAD",
                    linked_queue_id=None,
                    channel_thread_id=None,
                    duplicate_flag=0,
                )
            return

        # Follow-up modification:
        # This is only for changing existing patient data.
        # Therefore passport must exist in operation_queue_master.
        if is_followup_modification_message(text_lower):
            passport_no = extract_passport_no(text)

            if not passport_no:
                update_classification(
                    cursor=cursor,
                    message_log_id=message_log_id,
                    message_type="JUNK",
                    parse_status="JUNK",
                    classifier_reason="Follow-up modification rejected because Patient Passport Number is missing",
                    junk_flag=1,
                    requires_manual_review=0,
                    thread_resolution_status="NO_THREAD_FOUND",
                    linked_queue_id=None,
                    channel_thread_id=None
                )
                return

            resolved = resolve_existing_thread(
                cursor=cursor,
                sender_mobile=sender_mobile,
                message_body=message_body,
                channel_thread_id=channel_thread_id
            )

            if resolved:
                linked_queue_id = resolved.get("operation_queue_id")
                resolved_thread_id = resolved.get("channel_thread_id")

                if not resolved_thread_id and linked_queue_id:
                    resolved_thread_id = get_channel_thread_id_for_queue(
                        cursor=cursor,
                        operation_queue_id=linked_queue_id,
                    )

                update_classification(
                    cursor=cursor,
                    message_log_id=message_log_id,
                    message_type="FOLLOWUP_TEXT",
                    parse_status="READY_FOR_PARSE",
                    classifier_reason="Follow-up modification matched existing queue and sent to parser",
                    junk_flag=0,
                    requires_manual_review=0,
                    thread_resolution_status="EXISTING_THREAD",
                    linked_queue_id=linked_queue_id,
                    channel_thread_id=None
                )
            else:
                update_classification(
                    cursor=cursor,
                    message_log_id=message_log_id,
                    message_type="JUNK",
                    parse_status="JUNK",
                    classifier_reason="Follow-up modification rejected because Patient Passport Number did not match any existing open queue",
                    junk_flag=1,
                    requires_manual_review=0,
                    thread_resolution_status="NO_THREAD_FOUND",
                    linked_queue_id=None,
                    channel_thread_id=None
                )
            return

        if is_possible_followup_text(text_lower):
            resolved = resolve_existing_thread(
                cursor=cursor,
                sender_mobile=sender_mobile,
                message_body=message_body,
                channel_thread_id=channel_thread_id
            )

            if resolved:
                linked_queue_id = resolved.get("operation_queue_id")
                resolved_thread_id = resolved.get("channel_thread_id")

                if not resolved_thread_id and linked_queue_id:
                    resolved_thread_id = get_channel_thread_id_for_queue(
                        cursor=cursor,
                        operation_queue_id=linked_queue_id,
                    )

                update_classification(
                    cursor=cursor,
                    message_log_id=message_log_id,
                    message_type="FOLLOWUP_TEXT",
                    parse_status="READY_FOR_PARSE",
                    classifier_reason="Follow-up text linked to existing queue and sent to parser",
                    junk_flag=0,
                    requires_manual_review=0,
                    thread_resolution_status="EXISTING_THREAD",
                    linked_queue_id=linked_queue_id,
                    channel_thread_id=None
                )
            else:
                update_classification(
                    cursor=cursor,
                    message_log_id=message_log_id,
                    message_type="UNKNOWN",
                    parse_status="MANUAL_REVIEW",
                    classifier_reason="Possible follow-up text but no existing queue/thread found",
                    junk_flag=0,
                    requires_manual_review=1,
                    thread_resolution_status="NO_THREAD_FOUND",
                    linked_queue_id=None,
                    channel_thread_id=None
                )
            return

        update_classification(
            cursor=cursor,
            message_log_id=message_log_id,
            message_type="UNKNOWN",
            parse_status="MANUAL_REVIEW",
            classifier_reason="Unable to classify WhatsApp message",
            junk_flag=0,
            requires_manual_review=1,
            thread_resolution_status="NO_THREAD_FOUND",
            linked_queue_id=None,
            channel_thread_id=None
        )

    except Exception as e:
        mark_classification_failed(
            cursor=cursor,
            message_log_id=message_log_id,
            error_message=str(e)
        )



def classify_submission_window(
    cursor,
    root_message_log_id,
    sender_mobile,
    combined_message_body,
    attachment_count=0,
    channel_thread_id=None,
):
    """
    Classify ONE completed WhatsApp submission window.

    The webhook no longer classifies each Meta message independently. Instead,
    the scheduler combines every text/media event linked to the same root and
    calls this function after the 60-second quiet window closes.

    Result rules:
    - root row carries the submission-level classifier/parser state;
    - child rows are marked LINKED so the queue scheduler never parses them as
      independent patient submissions;
    - an attachment-only window that is still PENDING after classification is
      converted to MANUAL_REVIEW once the collection window has closed.
    """
    root_message_log_id = int(root_message_log_id)

    classify_and_process_message(
        cursor=cursor,
        message_log_id=root_message_log_id,
        sender_mobile=sender_mobile,
        message_body=combined_message_body,
        attachment_count=attachment_count,
        channel_thread_id=channel_thread_id,
    )

    cursor.execute(
        """
        SELECT
            id,
            message_type,
            parse_status,
            classifier_reason,
            junk_flag,
            duplicate_flag,
            requires_manual_review,
            linked_queue_id,
            channel_thread_id,
            thread_resolution_status
        FROM channel_message_log
        WHERE id = %s
        LIMIT 1
        """,
        (root_message_log_id,),
    )
    root = cursor.fetchone() or {}

    root_parse_status = str(root.get("parse_status") or "").strip().upper()
    root_message_type = str(root.get("message_type") or "").strip().upper()

    # Once the collection window is closed, an attachment-only root can no
    # longer remain indefinitely PENDING waiting for structured text.
    if (
        root_parse_status == "PENDING"
        and root_message_type == "ATTACHMENT_ONLY"
    ):
        update_classification(
            cursor=cursor,
            message_log_id=root_message_log_id,
            message_type="ATTACHMENT_ONLY",
            parse_status="MANUAL_REVIEW",
            classifier_reason=(
                "Attachment-only WhatsApp submission window closed without "
                "sufficient structured patient information"
            ),
            junk_flag=0,
            requires_manual_review=1,
            thread_resolution_status="NO_THREAD_FOUND",
            linked_queue_id=None,
            channel_thread_id=None,
            linked_triage_id=None,
        )
        root_parse_status = "MANUAL_REVIEW"
        root_message_type = "ATTACHMENT_ONLY"

    root_duplicate_flag = int(root.get("duplicate_flag") or 0)

    if root_duplicate_flag == 1:
        child_reason = "Grouped with duplicate-rejected WhatsApp submission window."
    elif root_parse_status == "MANUAL_REVIEW":
        child_reason = "Grouped with one Manual Review WhatsApp submission window."
    elif root_parse_status == "JUNK":
        child_reason = "Grouped with one JUNK WhatsApp submission window."
    else:
        child_reason = "Grouped with root WhatsApp submission window for one parse."

    # Every child stays auditable in channel_message_log but is excluded from
    # independent parsing. Do NOT write a new thread_resolution_status here.
    # That column is constrained by the existing database ENUM; the final
    # business thread state is applied later by operation_queue_service after
    # parsing succeeds (or Manual Review is resolved).
    cursor.execute(
        """
        UPDATE channel_message_log
        SET
            parsed_flag = 1,
            parse_status = 'LINKED',
            classifier_status = 'CLASSIFIED',
            classifier_reason = %s,
            requires_manual_review = 0,
            junk_flag = 0,
            duplicate_flag = %s,
            linked_queue_id = %s,
            channel_thread_id = NULL,
            linked_triage_id = %s,
            remarks = %s,
            updated_on = NOW()
        WHERE linked_triage_id = %s
          AND id <> %s
        """,
        (
            child_reason,
            root_duplicate_flag,
            root.get("linked_queue_id"),
            root_message_log_id,
            child_reason,
            root_message_log_id,
            root_message_log_id,
        ),
    )

    cursor.execute(
        """
        SELECT
            id,
            message_type,
            parse_status,
            classifier_reason,
            junk_flag,
            duplicate_flag,
            requires_manual_review,
            linked_queue_id,
            channel_thread_id,
            thread_resolution_status
        FROM channel_message_log
        WHERE id = %s
        LIMIT 1
        """,
        (root_message_log_id,),
    )
    return cursor.fetchone() or {}

def get_channel_message_log_context(cursor, message_log_id):
    """
    Read the freshly inserted WhatsApp log row before sending it to
    operation_queue_service. This preserves the existing intake flow and passes
    message/attachment metadata to the parser layer.
    """
    try:
        cursor.execute(
            """
            SELECT
                id,
                channel_code,
                channel_message_id,
                channel_thread_id,
                linked_queue_id,
                attachment_count,
                attachment_json,
                message_type
            FROM channel_message_log
            WHERE id = %s
            LIMIT 1
            """,
            (message_log_id,),
        )
        return cursor.fetchone() or {}
    except Exception:
        return {}


def process_ready_for_parse_message(
    cursor,
    message_log_id,
    message_body,
    message_type,
    linked_queue_id=None,
    channel_thread_id=None,
):
    """
    After classifier marks a message READY_FOR_PARSE, immediately call the
    operation queue parser and then force channel_message_log to reflect the
    real parser result.

    Existing business flow is not changed:
    - Junk/manual_review/attachment-only paths remain untouched.
    - operation_queue_service still owns queue row creation and attachment insert.
    - This function only prevents a successful queue insert from being left as
      FAILED in channel_message_log.
    """
    log_row = get_channel_message_log_context(cursor, message_log_id)

    channel_meta = {
        "channel_message_log_id": message_log_id,
        "channel_code": log_row.get("channel_code") or "WHATSAPP",
        "channel_message_id": log_row.get("channel_message_id"),
        # IMPORTANT:
        # Use only the explicit thread id passed by classifier.
        # Do not fallback to log_row.channel_thread_id for NEW_THREAD, because
        # an old CLOSED thread id may already be present in channel_message_log.
        "channel_thread_id": channel_thread_id,
        "linked_queue_id": linked_queue_id,
        "attachment_count": log_row.get("attachment_count") or 0,
        "attachment_json": log_row.get("attachment_json"),
        "message_type": message_type,
    }

    try:
        # Import inside the function to avoid circular import issues during Flask startup.
        from app.queue_management.operation_queue_service import insert_into_operation_queue_master

        result = insert_into_operation_queue_master(
            raw_message_text=message_body,
            channel_meta=channel_meta,
        ) or {}

        # Revised requirement: even when the Manual Review queue row is
        # successfully created, channel_message_log must remain MANUAL_REVIEW
        # with no channel_thread_id. Keep linked_queue_id for traceability and
        # store the detailed reason in classifier_reason and remarks.
        if result.get("manual_review") and result.get("queue_created"):
            payload = result.get("payload") or {}
            operation_queue_id = (
                result.get("operation_queue_master_id")
                or result.get("inserted_id")
                or result.get("linked_queue_id")
                or linked_queue_id
                or log_row.get("linked_queue_id")
            )
            manual_review_reason = str(
                payload.get("manual_review_reason")
                or payload.get("classifier_reason")
                or payload.get("parse_remarks")
                or result.get("message")
                or (
                    "WhatsApp sender could not be mapped to a valid "
                    "Agency. Manual review is required for Agent assignment."
                )
            ).strip()
            if manual_review_reason.upper() in {"MANUAL REVIEW", "MANUAL_REVIEW"}:
                manual_review_reason = (
                    "WhatsApp sender could not be mapped to a valid "
                    "Agency. Manual review is required for Agent assignment."
                )

            mark_parse_manual_review(
                cursor=cursor,
                message_log_id=message_log_id,
                remarks=manual_review_reason,
                operation_queue_id=operation_queue_id,
            )
            return result

        # Preserve the existing manual-review path for messages that were not
        # parsed into an operation_queue_master row at all.
        if result.get("manual_review"):
            payload = result.get("payload") or {}
            mark_parse_manual_review(
                cursor=cursor,
                message_log_id=message_log_id,
                remarks=(
                    payload.get("parse_remarks")
                    or result.get("message")
                    or "WhatsApp sender could not be mapped to a valid Agency. Manual review is required for Agent assignment."
                ),
            )
            return result

        success = bool(result.get("success"))
        skipped = bool(result.get("skipped"))
        operation_queue_id = result.get("operation_queue_master_id") or linked_queue_id or log_row.get("linked_queue_id")
        resolved_thread_id = result.get("channel_thread_id") or channel_thread_id or log_row.get("channel_thread_id")

        # If operation_queue_service succeeded, or if it skipped because there was
        # no material change, the WhatsApp message was still validly parsed.
        if success:
            if not operation_queue_id:
                operation_queue_id = find_operation_queue_id_for_message_log(cursor, message_log_id)

            mark_parse_success(
                cursor=cursor,
                message_log_id=message_log_id,
                operation_queue_id=operation_queue_id,
                channel_thread_id=resolved_thread_id,
                remarks=result.get("message") or "WhatsApp message parsed successfully.",
            )
            return result

        # Final safety: if a queue row exists for this message, do not mark FAILED.
        existing_queue_id = find_operation_queue_id_for_message_log(cursor, message_log_id)
        if existing_queue_id:
            existing_thread_id = get_channel_thread_id_for_queue(cursor, existing_queue_id)
            mark_parse_success(
                cursor=cursor,
                message_log_id=message_log_id,
                operation_queue_id=existing_queue_id,
                channel_thread_id=existing_thread_id or resolved_thread_id,
                remarks="Queue row exists for this WhatsApp message; parse status corrected to PARSED.",
            )
            return result

        mark_parse_failed(
            cursor=cursor,
            message_log_id=message_log_id,
            error_message=result.get("error") or result.get("message") or "Parser returned failure.",
        )
        return result

    except Exception as e:
        # Do not set FAILED if operation_queue_master was already inserted.
        existing_queue_id = find_operation_queue_id_for_message_log(cursor, message_log_id)
        if existing_queue_id:
            existing_thread_id = get_channel_thread_id_for_queue(cursor, existing_queue_id)
            mark_parse_success(
                cursor=cursor,
                message_log_id=message_log_id,
                operation_queue_id=existing_queue_id,
                channel_thread_id=existing_thread_id,
                remarks="Queue row exists although parser raised an exception; parse status corrected to PARSED.",
            )
            return {"success": True, "operation_queue_master_id": existing_queue_id}

        mark_parse_failed(cursor, message_log_id, str(e))
        return {"success": False, "error": str(e)}


def find_operation_queue_id_for_message_log(cursor, message_log_id):
    try:
        cursor.execute(
            """
            SELECT id
            FROM operation_queue_master
            WHERE channel_message_log_id = %s
            ORDER BY id DESC
            LIMIT 1
            """,
            (message_log_id,),
        )
        row = cursor.fetchone()
        if not row:
            return None
        if isinstance(row, dict):
            return row.get("id")
        if isinstance(row, (list, tuple)) and len(row) > 0:
            return row[0]
        return None
    except Exception:
        return None


def mark_parse_manual_review(
    cursor,
    message_log_id,
    remarks=None,
    operation_queue_id=None,
):
    reason = str(
        remarks
        or "WhatsApp sender could not be mapped to a valid Agency. Manual review is required for Agent assignment."
    )[:500]

    sql = """
        UPDATE channel_message_log
        SET
            parsed_flag = 0,
            parse_status = 'MANUAL_REVIEW',
            requires_manual_review = 1,
            junk_flag = 0,
            linked_queue_id = %s,
            channel_thread_id = NULL,
            classifier_status = 'CLASSIFIED',
            classifier_reason = %s,
            thread_resolution_status = 'NO_THREAD_FOUND',
            remarks = %s,
            updated_on = NOW()
        WHERE id = %s
    """
    cursor.execute(
        sql,
        (
            operation_queue_id,
            reason,
            reason,
            message_log_id,
        ),
    )


def mark_parse_success(
    cursor,
    message_log_id,
    operation_queue_id=None,
    channel_thread_id=None,
    remarks=None,
    requires_manual_review=0,
    thread_resolution_status=None,
):
    """
    Mark a WhatsApp message as successfully parsed.

    Existing callers continue to use the normal successful-parse defaults:
    requires_manual_review = 0 and no forced thread-resolution-status change.

    Successful Manual Review routing can additionally preserve:
    - requires_manual_review = 1
    - the resolved/generated channel_thread_id
    - linked_queue_id
    - the actual Manual Review reason in remarks
    """
    sql = """
        UPDATE channel_message_log
        SET
            parsed_flag = 1,
            parse_status = 'PARSED',
            requires_manual_review = %s,
            junk_flag = 0,
            linked_queue_id = COALESCE(%s, linked_queue_id),
            channel_thread_id = COALESCE(%s, channel_thread_id),
            thread_resolution_status = COALESCE(
                %s,
                thread_resolution_status
            ),
            remarks = COALESCE(%s, remarks),
            updated_on = NOW()
        WHERE id = %s
    """
    cursor.execute(
        sql,
        (
            requires_manual_review,
            operation_queue_id,
            channel_thread_id,
            thread_resolution_status,
            remarks,
            message_log_id,
        ),
    )


def mark_parse_failed(cursor, message_log_id, error_message):
    existing_queue_id = find_operation_queue_id_for_message_log(cursor, message_log_id)
    if existing_queue_id:
        existing_thread_id = get_channel_thread_id_for_queue(cursor, existing_queue_id)
        mark_parse_success(
            cursor=cursor,
            message_log_id=message_log_id,
            operation_queue_id=existing_queue_id,
            channel_thread_id=existing_thread_id,
            remarks="Queue row exists; parse failure ignored and status set to PARSED.",
        )
        return

    sql = """
        UPDATE channel_message_log
        SET
            parse_status = 'FAILED',
            requires_manual_review = 1,
            remarks = %s,
            updated_on = NOW()
        WHERE id = %s
    """
    cursor.execute(sql, (
        str(error_message or "Parser failed")[:500],
        message_log_id,
    ))


def get_channel_thread_id_for_queue(cursor, operation_queue_id):
    if not operation_queue_id:
        return None

    try:
        cursor.execute(
            """
            SELECT channel_thread_id
            FROM operation_queue_master
            WHERE id = %s
            LIMIT 1
            """,
            (operation_queue_id,),
        )

        row = cursor.fetchone()

        if not row:
            return None

        if isinstance(row, dict):
            return row.get("channel_thread_id")

        if isinstance(row, (list, tuple)) and len(row) > 0:
            return row[0]

        return None

    except Exception:
        return None


def normalize_text(text):
    if not text:
        return ""

    text = text.strip().lower()
    text = re.sub(r"\s+", " ", text)
    return text


def is_junk_message(text_lower):
    """
    Return True only for clearly non-actionable conversational messages.

    A greeting that also contains patient/request information is never treated
    as junk. For example:
        "Hi, please arrange an appointment for patient P013"
    continues through the normal business-classification flow.
    """
    if not text_lower:
        return True

    raw_text = str(text_lower or "").strip().lower()

    business_keywords = [
        "passport", "patient", "hospital", "appointment", "visa", "vil",
        "invoice", "payment", "doctor", "treatment", "estimate",
        "estimation", "attender", "request", "report", "medical",
        "department", "embassy", "nationality", "approximate date",
        "ivac", "agent", "agency", "date", "dob", "email", "contact",
        "mobile", "phone", "address"
    ]

    # Business content always takes priority over greeting detection.
    if any(keyword in raw_text for keyword in business_keywords):
        return False

    emoji_only_messages = {
        "👍", "🙏", "👌", "🙂", "😊", "✅", "☑️", "❤️", "❤"
    }
    if raw_text in emoji_only_messages:
        return True

    normalized = re.sub(r"[^a-z0-9\s]", " ", raw_text)
    normalized = re.sub(r"\s+", " ", normalized).strip()

    conversational_messages = {
        "hi",
        "hii",
        "hiii",
        "hello",
        "helo",
        "hey",
        "hi there",
        "hello there",
        "hey there",
        "good morning",
        "good afternoon",
        "good evening",
        "good night",
        "how are you",
        "how are you doing",
        "how are you doing today",
        "how r you",
        "how r u",
        "how are u",
        "hi how are you",
        "hi how are you doing",
        "hello how are you",
        "hello how are you doing",
        "hey how are you",
        "hey how are you doing",
        "hope you are well",
        "hope you are doing well",
        "thanks",
        "thank you",
        "thankyou",
        "many thanks",
        "tnx",
        "thx",
        "welcome",
        "you are welcome",
        "ok",
        "okay",
        "k",
        "yes",
        "no",
        "noted",
        "received",
        "understood",
        "fine",
        "i am fine",
        "test",
        "testing"
    }

    if normalized in conversational_messages:
        return True

    # Handle natural greeting combinations without enumerating every punctuation
    # or spelling variation. These checks remain safe because business keywords
    # were already excluded above.
    greeting_pattern = re.compile(
        r"^(hi+|hello+|helo+|hey+)"
        r"( there)?"
        r"( how (are|r) (you|u)( doing)?( today)?)?$"
    )
    if greeting_pattern.fullmatch(normalized):
        return True

    wellbeing_pattern = re.compile(
        r"^(how (are|r) (you|u)( doing)?( today)?|"
        r"hope (you|u) (are|r) (well|doing well))$"
    )
    if wellbeing_pattern.fullmatch(normalized):
        return True

    if re.fullmatch(r"h+i+", normalized):
        return True
    if re.fullmatch(r"he+l+o+", normalized):
        return True
    if re.fullmatch(r"he+y+", normalized):
        return True

    if len(normalized) <= 3:
        return True

    return False


def is_attachment_only_message(text, attachment_count):
    if attachment_count <= 0:
        return False

    if not text:
        return True

    placeholder_texts = [
        "[Document message received]",
        "[Image message received]",
        "[Video message received]",
        "[Audio message received]"
    ]

    return text.strip() in placeholder_texts


def is_structured_request(text_lower):
    if not text_lower:
        return False

    has_passport = any(keyword in text_lower for keyword in [
        "passport",
        "passport no",
        "passport number",
        "pass no",
        "patient passport"
    ])

    has_patient = any(keyword in text_lower for keyword in [
        "patient",
        "patient name",
        "patient given name",
        "patient short name",
        "pat name"
    ])

    has_hospital = any(keyword in text_lower for keyword in [
        "hospital",
        "hospital name",
        "hosp"
    ])

    has_request = any(keyword in text_lower for keyword in [
        "request",
        "request for",
        "purpose",
        "vil",
        "visa",
        "appointment",
        "estimation",
        "estimate",
        "intent"
    ])

    return has_passport and has_patient and has_hospital and has_request


def is_new_request_candidate(text_lower, attachment_count=0):
    """
    New request should not require existing passport in DB.

    Additional safe case:
    If WhatsApp message has an attachment and caption contains Patient Passport Number,
    allow it as NEW_THREAD / READY_FOR_PARSE. This supports messages like:
        Patient Passport Number P00000001
    with jpg/pdf attachment.
    """

    if not text_lower:
        return False

    has_passport_label = any(keyword in text_lower for keyword in [
        "passport no",
        "passport number",
        "passport:",
        "passport -",
        "pass no",
        "patient passport"
    ])

    has_passport_like_value = bool(
        re.search(r"\b[a-z]{1,3}\d{5,12}\b", text_lower, re.IGNORECASE)
    )

    has_patient_indicator = any(keyword in text_lower for keyword in [
        "patient",
        "patient name",
        "pat name",
        "given name",
        "surname",
        "short name"
    ])

    has_hospital_or_doctor_indicator = any(keyword in text_lower for keyword in [
        "hospital",
        "hosp",
        "doctor",
        "department",
        "dept",
        "embassy",
        "ivac"
    ])

    has_request_indicator = any(keyword in text_lower for keyword in [
        "request",
        "request for",
        "purpose",
        "vil",
        "visa",
        "appointment",
        "estimation",
        "estimate",
        "treatment",
        "medical",
        "intent",
        "approximate date"
    ])

    has_contact_indicator = any(keyword in text_lower for keyword in [
        "contact",
        "mobile",
        "phone",
        "email",
        "mail"
    ])

    has_attender_indicator = any(keyword in text_lower for keyword in [
        "attender",
        "attender1",
        "attender2",
        "attender3"
    ])

    has_passport = has_passport_label or has_passport_like_value

    # NEW SAFE ADDITION:
    # Caption with passport + attachment should be parsed as a new request,
    # not UNKNOWN / MANUAL_REVIEW.
    if attachment_count > 0 and has_passport:
        return True

    if has_passport and has_patient_indicator and (
        has_hospital_or_doctor_indicator
        or has_request_indicator
        or has_contact_indicator
        or has_attender_indicator
    ):
        return True

    if has_patient_indicator and has_hospital_or_doctor_indicator and has_request_indicator:
        return True

    return False

def is_followup_modification_message(text_lower):
    """
    Follow-up modification means:
    Existing patient data amendment.

    It should NOT classify every passport message as follow-up.
    It must have passport indicator plus at least one editable field
    or correction/reschedule/update wording.
    """

    if not text_lower:
        return False

    has_passport_indicator = any(keyword in text_lower for keyword in [
        "patient passport number",
        "patient passport no",
        "passport number",
        "passport no",
        "passport",
        "pass no",
    ])

    if not has_passport_indicator:
        return False

    editable_field_keywords = [
        "patient given name",
        "patient short name",
        "patient passport expiry date",
        "patient gender",
        "patient dob",
        "patient age",
        "patient contact number",
        "patient address",
        "patient email",
        "patient nationality",

        "attender1 given name",
        "attender1 short name",
        "attender1 passport number",
        "attender1 passport expiry date",
        "attender1 gender",
        "attender1 dob",
        "attender1 age",
        "attender1 address",
        "attender1 nationality",
        "attender1 contact number",
        "attender1 relationship",

        "attender2 given name",
        "attender2 short name",
        "attender2 passport number",
        "attender2 passport expiry date",
        "attender2 gender",
        "attender2 dob",
        "attender2 age",
        "attender2 address",
        "attender2 nationality",
        "attender2 contact number",
        "attender2 relationship",

        "attender3 given name",
        "attender3 short name",
        "attender3 passport number",
        "attender3 passport expiry date",
        "attender3 gender",
        "attender3 dob",
        "attender3 age",
        "attender3 address",
        "attender3 nationality",
        "attender3 contact number",
        "attender3 relationship",

        "hospital name",
        "department",
        "doctor",
        "doctor's name",
        "nationality",
        "embassy",
        "intent",
        "approximate date",
    ]

    followup_action_keywords = [
        "change",
        "changed",
        "modify",
        "modified",
        "correction",
        "correct",
        "update",
        "updated",
        "revised",
        "revision",
        "reschedule",
        "rescheduled",
        "amend",
        "amended",
        "replace",
        "new date",
        "date changed",
    ]

    has_editable_field = any(keyword in text_lower for keyword in editable_field_keywords)
    has_followup_action = any(keyword in text_lower for keyword in followup_action_keywords)

    return has_editable_field or has_followup_action


def is_possible_followup_text(text_lower):
    if not text_lower:
        return False

    followup_phrases = [
        "any update",
        "please update",
        "kindly update",
        "status update",
        "what is the status",
        "current status",
        "follow up",
        "following up",
        "please process",
        "kindly process",
        "process this",
        "please check",
        "kindly check",
        "already sent",
        "sent earlier",
        "as discussed",
        "as per discussion",
        "same patient",
        "same case",
        "same passport",
        "same request",
        "changed",
        "change the date",
        "date changed",
        "reschedule",
        "rescheduled",
        "revised",
        "revision",
        "updated document",
        "updated report",
        "new report attached",
        "additional report",
        "additional document",
        "missing document",
        "pending document"
    ]

    return any(phrase in text_lower for phrase in followup_phrases)


def update_classification(
    cursor,
    message_log_id,
    message_type,
    parse_status,
    classifier_reason,
    junk_flag=0,
    requires_manual_review=0,
    thread_resolution_status=None,
    linked_queue_id=None,
    channel_thread_id=None,
    linked_triage_id=None,
    parsed_flag=None,
    duplicate_flag=None,
    remarks=None,
):
    sql = """
        UPDATE channel_message_log
        SET
            message_type = %s,
            classifier_status = 'CLASSIFIED',
            classifier_reason = %s,
            thread_resolution_status = %s,
            requires_manual_review = %s,
            parse_status = %s,
            junk_flag = %s,
            linked_queue_id = %s,
            channel_thread_id = %s,
            linked_triage_id = %s,
            parsed_flag = COALESCE(%s, parsed_flag),
            duplicate_flag = COALESCE(%s, duplicate_flag),
            remarks = COALESCE(%s, remarks),
            updated_on = NOW()
        WHERE id = %s
    """

    normalized_remarks = (
        None if remarks is None else str(remarks)[:500]
    )

    cursor.execute(sql, (
        message_type,
        classifier_reason,
        thread_resolution_status,
        requires_manual_review,
        parse_status,
        junk_flag,
        linked_queue_id,
        channel_thread_id,
        linked_triage_id,
        parsed_flag,
        duplicate_flag,
        normalized_remarks,
        message_log_id
    ))


def mark_classification_failed(cursor, message_log_id, error_message):
    sql = """
        UPDATE channel_message_log
        SET
            classifier_status = 'FAILED',
            classifier_reason = %s,
            parse_status = 'FAILED',
            requires_manual_review = 1,
            updated_on = NOW()
        WHERE id = %s
    """

    cursor.execute(sql, (
        error_message[:500],
        message_log_id
    ))
