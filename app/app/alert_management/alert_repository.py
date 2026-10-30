"""
Alert repository.

All Alert Module SQL is kept in this file so the scheduler and engine
remain independent from database implementation details. Patient workflow,
operation queue and invoice sources are evaluated through dedicated adapters.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any

from app.common.db import get_db_connection


def _close(connection=None, cursor=None) -> None:
    if cursor is not None:
        try:
            cursor.close()
        except Exception:
            pass
    if connection is not None:
        try:
            connection.close()
        except Exception:
            pass


def get_due_active_rules(limit: int = 100) -> list[dict[str, Any]]:
    """
    Return active rules due for evaluation.

    Compatibility:
    - If scheduler columns exist, they are used.
    - If they do not exist yet, every active rule is returned and the
      5-minute APScheduler heartbeat becomes the evaluation interval.
    """
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SHOW COLUMNS FROM alert_rule_master")
        columns = {row["Field"] for row in (cursor.fetchall() or [])}

        scheduler_columns_exist = {
            "scheduler_interval_value",
            "scheduler_interval_unit",
            "last_evaluated_on",
            "next_evaluation_on",
        }.issubset(columns)

        scheduler_select = """
            scheduler_interval_value,
            scheduler_interval_unit,
            last_evaluated_on,
            next_evaluation_on,
        """ if scheduler_columns_exist else """
            5 AS scheduler_interval_value,
            'MINUTES' AS scheduler_interval_unit,
            NULL AS last_evaluated_on,
            NULL AS next_evaluation_on,
        """

        due_filter = """
          AND (
                next_evaluation_on IS NULL
                OR next_evaluation_on <= NOW()
              )
        """ if scheduler_columns_exist else ""

        cursor.execute(
            f"""
            SELECT
                id,
                alert_name,
                alert_description,
                module_code,
                trigger_type,
                workflow_status,
                ageing_basis,
                warning_after_minutes,
                critical_after_minutes,
                repeat_interval_minutes,
                maximum_repeat_count,
                business_hours_only,
                exclude_weekends,
                exclude_holidays,
                auto_resolve,
                active_flag,
                {scheduler_select}
                created_on,
                updated_on
            FROM alert_rule_master
            WHERE UPPER(COALESCE(active_flag, 'N')) = 'Y'
              {due_filter}
            ORDER BY
                COALESCE(next_evaluation_on, created_on) ASC,
                id ASC
            LIMIT %s
            """,
            (int(limit),),
        )
        return cursor.fetchall() or []
    finally:
        _close(connection, cursor)


def mark_rule_evaluated(rule: dict[str, Any]) -> None:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute("SHOW COLUMNS FROM alert_rule_master")
        columns = {row["Field"] for row in (cursor.fetchall() or [])}
        if not {"last_evaluated_on", "next_evaluation_on"}.issubset(columns):
            return

        interval_value = max(int(rule.get("scheduler_interval_value") or 5), 1)
        interval_unit = str(rule.get("scheduler_interval_unit") or "MINUTES").upper()

        if interval_unit == "DAYS":
            expression = "DATE_ADD(NOW(), INTERVAL %s DAY)"
        elif interval_unit == "HOURS":
            expression = "DATE_ADD(NOW(), INTERVAL %s HOUR)"
        else:
            expression = "DATE_ADD(NOW(), INTERVAL %s MINUTE)"

        cursor.execute(
            f"""
            UPDATE alert_rule_master
            SET
                last_evaluated_on = NOW(),
                next_evaluation_on = {expression},
                updated_on = updated_on
            WHERE id = %s
            """,
            (interval_value, rule["id"]),
        )
        connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        _close(connection, cursor)



def _normalise_module_code(value: Any) -> str:
    return str(value or "").strip().upper()


def get_reference_type_for_rule(rule: dict[str, Any]) -> str:
    """
    Return the alert_instance.reference_type used for a rule.

    Source mapping:
      PATIENT_PROCESS -> PATIENT_WORKFLOW / patient_wrkflw
      INVOICE         -> INVOICE / invoice_master
      Other modules   -> OPERATION_QUEUE / operation_queue_master
    """
    module_code = _normalise_module_code(rule.get("module_code"))
    trigger_type = str(rule.get("trigger_type") or "").strip().upper()

    if module_code == "PARTNER" and trigger_type == "DATE_EXPIRY":
        return "PARTNER"

    if module_code == "PATIENT_PROCESS":
        return "PATIENT_WORKFLOW"

    if module_code in {"INVOICE", "INVOICE_MANAGEMENT"}:
        return "INVOICE"

    return "OPERATION_QUEUE"


def _get_table_columns(cursor, table_name: str) -> set[str]:
    cursor.execute(f"SHOW COLUMNS FROM `{table_name}`")
    return {row["Field"] for row in (cursor.fetchall() or [])}


def _first_existing_column(columns: set[str], candidates: tuple[str, ...]) -> str | None:
    for column in candidates:
        if column in columns:
            return column
    return None


def get_matching_records(
    rule: dict[str, Any],
    limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Route each alert module to its correct source table.
    """
    module_code = _normalise_module_code(rule.get("module_code"))
    trigger_type = str(rule.get("trigger_type") or "").strip().upper()

    if module_code == "PARTNER" and trigger_type == "DATE_EXPIRY":
        return get_matching_partner_contract_records(rule, limit=limit)

    if module_code == "PATIENT_PROCESS":
        return get_matching_patient_workflow_records(rule, limit=limit)

    if module_code in {"INVOICE", "INVOICE_MANAGEMENT"}:
        return get_matching_invoice_records(rule, limit=limit)

    return get_matching_operation_queue_records(rule, limit=limit)



def get_matching_partner_contract_records(
    rule: dict[str, Any],
    limit: int = 500,
) -> list[dict[str, Any]]:
    """Return active partners that are inside the configured expiry window.

    partner_reg.contend_date is date-only. It is evaluated at 23:59:59 in the
    database/application timezone so Days, Hours and Minutes all have precise
    and consistent semantics. Expired active contracts continue to match.
    """
    warning_minutes = int(rule.get("warning_after_minutes") or 0)
    if warning_minutes <= 0:
        return []

    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        columns = _get_table_columns(cursor, "partner_reg")

        missing = sorted({"short_name", "contend_date"} - columns)
        if missing:
            raise RuntimeError(
                "partner_reg is missing required Contract End Date alert "
                "column(s): " + ", ".join(missing)
            )

        def value_expr(column: str, alias: str) -> str:
            if column in columns:
                return f"pr.`{column}` AS `{alias}`"
            return f"NULL AS `{alias}`"

        entered_by_expr = (
            "pr.`entered_by`" if "entered_by" in columns else "NULL"
        )
        status_filter = ""
        if "partner_status" in columns:
            status_filter = """
              AND (
                    pr.partner_status IS NULL
                    OR TRIM(pr.partner_status) = ''
                    OR UPPER(TRIM(pr.partner_status)) = 'ACTIVE'
                  )
            """

        expiry_expr = (
            "DATE_SUB(DATE_ADD(DATE(pr.contend_date), INTERVAL 1 DAY), "
            "INTERVAL 1 SECOND)"
        )

        cursor.execute(
            f"""
            SELECT
                pr.short_name AS id,
                pr.short_name AS partner_short_name,
                {value_expr('legal_name', 'partner_name')},
                {value_expr('partner_type', 'partner_type')},
                {value_expr('partner_status', 'partner_status')},
                {value_expr('contstart_date', 'contract_start_date')},
                pr.contend_date AS contract_end_date,
                {value_expr('partner_email', 'partner_email')},
                {value_expr('prim_contper', 'primary_contact_person')},
                {value_expr('prim_contno', 'primary_contact_number')},
                {entered_by_expr} AS entered_by,
                {entered_by_expr} AS assigned_user,
                {entered_by_expr} AS assigned_to,
                {expiry_expr} AS contract_expiry_on,
                DATE_SUB({expiry_expr}, INTERVAL %s MINUTE)
                    AS alert_ageing_started_on,
                TIMESTAMPDIFF(MINUTE, NOW(), {expiry_expr})
                    AS remaining_minutes,
                GREATEST(
                    %s - TIMESTAMPDIFF(MINUTE, NOW(), {expiry_expr}),
                    0
                ) AS ageing_minutes
            FROM partner_reg pr
            WHERE pr.contend_date IS NOT NULL
              AND pr.short_name IS NOT NULL
              AND TRIM(pr.short_name) <> ''
              {status_filter}
              AND TIMESTAMPDIFF(MINUTE, NOW(), {expiry_expr}) <= %s
            ORDER BY pr.contend_date ASC, pr.short_name ASC
            LIMIT %s
            """,
            (warning_minutes, warning_minutes, warning_minutes, int(limit)),
        )
        return cursor.fetchall() or []
    finally:
        _close(connection, cursor)


def get_matching_invoice_records(
    rule: dict[str, Any],
    limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Evaluate one alert record per invoice_number.

    Invoice Alert rules use:
      reference id       -> invoice_master.invoice_number
      workflow status    -> invoice_master.wrkflw_status
      mandatory state    -> invoice_master.print_status = PRINTED
      ageing start       -> invoice_master.prntentered_at

    invoice_master contains one row per invoice line/patient. Values are
    grouped by invoice_number so only one Alert Instance is created for an
    invoice. Monetary line values are summed across the invoice rows.
    """
    connection = cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                im.invoice_number AS id,
                im.invoice_number,

                MIN(im.hospital_name) AS hospital_name,
                MIN(im.hospital_address) AS hospital_address,
                MIN(im.hospital_gst) AS hospital_gst,
                MIN(im.hospital_pan) AS hospital_pan,
                MIN(im.hospital_country) AS hospital_country,
                MIN(im.hospital_state) AS hospital_state,
                MIN(im.hospital_city) AS hospital_city,
                MIN(im.hospital_pincode) AS hospital_pincode,

                GROUP_CONCAT(
                    DISTINCT NULLIF(TRIM(im.ld_internalno), '')
                    ORDER BY im.ld_internalno
                    SEPARATOR ', '
                ) AS ld_internalno,

                GROUP_CONCAT(
                    DISTINCT NULLIF(TRIM(im.patient_passport), '')
                    ORDER BY im.patient_passport
                    SEPARATOR ', '
                ) AS patient_passport,

                GROUP_CONCAT(
                    DISTINCT NULLIF(TRIM(im.patient_name), '')
                    ORDER BY im.patient_name
                    SEPARATOR ', '
                ) AS patient_name,

                MAX(im.wrkflw_status) AS wrkflw_status,
                MAX(im.status_date) AS status_date,
                MAX(im.print_status) AS print_status,

                MAX(im.prntentered_at) AS prntentered_at,
                MAX(im.prntentered_at) AS printentered_at,
                MAX(im.prntentered_by) AS prntentered_by,
                MAX(im.inventered_at) AS inventered_at,
                MAX(im.inventered_by) AS inventered_by,

                MIN(im.inv_currency) AS inv_currency,
                SUM(COALESCE(im.inv_amount, 0)) AS inv_amount,
                SUM(COALESCE(im.igst_amount, 0)) AS igst_amount,
                SUM(COALESCE(im.cgst_amount, 0)) AS cgst_amount,
                SUM(COALESCE(im.totalgst_amount, 0)) AS totalgst_amount,
                SUM(COALESCE(im.totaltds_amount, 0)) AS totaltds_amount,
                MAX(COALESCE(im.netgst_amount, 0)) AS netgst_amount,
                MAX(COALESCE(im.nettds_amount, 0)) AS nettds_amount,
                MAX(COALESCE(im.netinv_amount, 0)) AS netinv_amount,
                SUM(COALESCE(im.totalinv_amount, 0)) AS totalinv_amount,
                COUNT(*) AS invoice_row_count,

                COALESCE(
                    MAX(NULLIF(TRIM(im.prntentered_by), '')),
                    MAX(NULLIF(TRIM(im.inventered_by), ''))
                ) AS assigned_user,
                COALESCE(
                    MAX(NULLIF(TRIM(im.prntentered_by), '')),
                    MAX(NULLIF(TRIM(im.inventered_by), ''))
                ) AS assigned_to,

                MAX(im.prntentered_at) AS alert_ageing_started_on,
                GREATEST(
                    TIMESTAMPDIFF(
                        MINUTE,
                        MAX(im.prntentered_at),
                        NOW()
                    ),
                    0
                ) AS ageing_minutes

            FROM invoice_master im

            WHERE UPPER(TRIM(COALESCE(im.wrkflw_status, '')))
                  = UPPER(TRIM(%s))
              AND UPPER(TRIM(COALESCE(im.print_status, ''))) = 'PRINTED'
              AND im.invoice_number IS NOT NULL
              AND TRIM(im.invoice_number) <> ''
              AND im.prntentered_at IS NOT NULL

            GROUP BY im.invoice_number

            HAVING TIMESTAMPDIFF(
                       MINUTE,
                       MAX(im.prntentered_at),
                       NOW()
                   ) >= %s

            ORDER BY MAX(im.prntentered_at) ASC, im.invoice_number ASC
            LIMIT %s
            """,
            (
                rule.get("workflow_status"),
                int(rule.get("warning_after_minutes") or 0),
                int(limit),
            ),
        )

        return cursor.fetchall() or []

    finally:
        _close(connection, cursor)


def get_matching_patient_workflow_records(
    rule: dict[str, Any],
    limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Evaluate PATIENT_PROCESS / WORKFLOW_STATUS rules against patient_wrkflw.

    Exact patient_wrkflw schema mapping:
      reference id       -> patient_wrkflw.ld_seqno
      lead id            -> patient_wrkflw.ld_internalno
      workflow status    -> patient_wrkflw.wrkflw_status
      record/status time -> patient_wrkflw.entered_date
      patient identifier -> patient_wrkflw.patpassport_no
      entered user       -> patient_wrkflw.entered_by

    Channel linkage:
      patient_wrkflw does not store channel_thread_id or channel_message_id.
      For newly evaluated alerts, those values are derived from the exact
      operation_queue_master record that created the workflow, matched by:

        operation_queue_master.patpassport_no = patient_wrkflw.patpassport_no
        operation_queue_master.workflow_created_on = patient_wrkflw.entered_date

    ld_internalno always comes directly from patient_wrkflw. No fallback to a
    queue id or channel id is used.

    Important:
    patient_wrkflw does not contain a dedicated status_changed_on or
    last_activity_on column. Because each workflow row carries entered_date,
    entered_date is used as the ageing start for all three configured ageing
    bases until a dedicated workflow history timestamp is introduced.
    """
    ageing_basis = str(rule.get("ageing_basis") or "").strip().upper()

    # Reserved for future ageing-basis-specific workflow timestamps. At present
    # patient_wrkflw has only entered_date as a reliable ageing start.
    _ = ageing_basis
    ageing_column = "pw.entered_date"

    connection = cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            f"""
            SELECT
                pw.ld_seqno AS id,
                pw.ld_internalno AS ld_internalno,
                pw.vil_internalno,
                pw.ayush_no,
                pw.external_refno,
                pw.patient_uhid,

                pw.patpassport_no,
                pw.patgiven_nm,
                pw.patsur_nm,
                pw.patient_dob,
                pw.patient_country,
                pw.patient_gender,
                pw.patient_contno,
                pw.patient_age,
                pw.patpass_expirydt,
                pw.patient_address,
                pw.patient_email,

                pw.hospital_shrtnm,
                pw.hospital_city,
                pw.hospital_dept,
                pw.ivac_center,
                pw.agent_shrtnm,
                pw.doctor_nm,

                pw.wrkflw_status,
                pw.wrkflw_remarks,
                pw.patient_status,

                pw.entered_by,
                pw.entered_by AS assigned_user,
                pw.entered_by AS assigned_to,
                pw.entered_by AS assigned_by,
                pw.entered_date AS assigned_at,
                pw.entered_date AS created_on,
                pw.entered_date AS updated_on,

                oq.channel_thread_id,
                oq.channel_message_id,
                oq.id AS operation_queue_id,

                {ageing_column} AS alert_ageing_started_on,
                GREATEST(
                    TIMESTAMPDIFF(MINUTE, {ageing_column}, NOW()),
                    0
                ) AS ageing_minutes
            FROM patient_wrkflw pw
            LEFT JOIN operation_queue_master oq
              ON oq.id = (
                    SELECT oq_match.id
                    FROM operation_queue_master oq_match
                    WHERE CONVERT(TRIM(COALESCE(oq_match.patpassport_no, '')) USING utf8mb4)
                              COLLATE utf8mb4_unicode_ci
                          = CONVERT(TRIM(COALESCE(pw.patpassport_no, '')) USING utf8mb4)
                              COLLATE utf8mb4_unicode_ci
                      AND oq_match.workflow_created_on = pw.entered_date
                    ORDER BY oq_match.id DESC
                    LIMIT 1
                 )
            WHERE UPPER(TRIM(COALESCE(pw.wrkflw_status, '')))
                  = UPPER(TRIM(%s))
              AND {ageing_column} IS NOT NULL
              AND TIMESTAMPDIFF(MINUTE, {ageing_column}, NOW()) >= %s
            ORDER BY {ageing_column} ASC, pw.ld_seqno ASC
            LIMIT %s
            """,
            (
                rule.get("workflow_status"),
                int(rule.get("warning_after_minutes") or 0),
                int(limit),
            ),
        )

        return cursor.fetchall() or []

    finally:
        _close(connection, cursor)

def get_matching_operation_queue_records(
    rule: dict[str, Any],
    limit: int = 500,
) -> list[dict[str, Any]]:
    """
    Initial source adapter for WORKFLOW_STATUS rules.

    operation_queue_master does not currently contain status_changed_on or
    last_activity_on. Therefore:
      RECORD_CREATED_ON -> created_on
      LAST_ACTIVITY_ON  -> updated_on
      STATUS_CHANGED_ON -> updated_on (temporary fallback)
    """
    ageing_basis = str(rule.get("ageing_basis") or "").upper()
    if ageing_basis == "RECORD_CREATED_ON":
        ageing_column = "created_on"
    else:
        ageing_column = "updated_on"

    cursor = connection = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        cursor.execute(
            f"""
            SELECT
                id,
                channel_thread_id,
                channel_message_id,
                patpassport_no,
                patgiven_nm,
                patsur_nm,
                patient_country,
                patient_email,
                patient_contno,
                hospital_shrtnm,
                hospital_city,
                hospital_dept,
                agent_shrtnm,
                wrkflw_status,
                assigned_to,
                assigned_by,
                assigned_at,
                created_on,
                updated_on,
                {ageing_column} AS alert_ageing_started_on,
                GREATEST(
                    TIMESTAMPDIFF(MINUTE, {ageing_column}, NOW()),
                    0
                ) AS ageing_minutes
            FROM operation_queue_master
            WHERE UPPER(TRIM(COALESCE(wrkflw_status, '')))
                  = UPPER(TRIM(%s))
              AND {ageing_column} IS NOT NULL
              AND TIMESTAMPDIFF(MINUTE, {ageing_column}, NOW()) >= %s
            ORDER BY {ageing_column} ASC, id ASC
            LIMIT %s
            """,
            (
                rule.get("workflow_status"),
                int(rule.get("warning_after_minutes") or 0),
                int(limit),
            ),
        )
        return cursor.fetchall() or []
    finally:
        _close(connection, cursor)


def get_rule_recipients(rule_id: int, severity: str) -> list[dict[str, Any]]:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT
                id,
                rule_id,
                severity,
                recipient_type,
                recipient_value,
                in_app_flag,
                email_flag,
                whatsapp_flag,
                sms_flag,
                active_flag
            FROM alert_rule_recipient
            WHERE rule_id = %s
              AND UPPER(severity) = UPPER(%s)
              AND UPPER(COALESCE(active_flag, 'N')) = 'Y'
            ORDER BY id
            """,
            (rule_id, severity),
        )
        return cursor.fetchall() or []
    finally:
        _close(connection, cursor)


def get_existing_alert_instance(
    rule_id: int,
    reference_type: str,
    reference_id: Any,
) -> dict[str, Any] | None:
    normalised_reference_id = str(reference_id or "").strip()
    if not normalised_reference_id:
        return None

    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT *
            FROM alert_instance
            WHERE rule_id = %s
              AND reference_type = %s
              AND reference_id = %s
            LIMIT 1
            """,
            (rule_id, reference_type, normalised_reference_id),
        )
        return cursor.fetchone()
    finally:
        _close(connection, cursor)


def create_alert_instance(
    rule: dict[str, Any],
    record: dict[str, Any],
    severity: str,
) -> int:
    reference_type = get_reference_type_for_rule(rule)

    patient_name = str(record.get("patient_name") or "").strip()
    if not patient_name:
        patient_name = " ".join(
            part
            for part in [
                str(record.get("patgiven_nm") or "").strip(),
                str(record.get("patsur_nm") or "").strip(),
            ]
            if part
        )

    patient_passport = (
        record.get("patient_passport")
        or record.get("patpassport_no")
        or record.get("passport_no")
    )

    printentered_at = (
        record.get("printentered_at")
        or record.get("prntentered_at")
    )

    context = {
        "reference_type": reference_type,
        "partner_short_name": record.get("partner_short_name"),
        "partner_name": record.get("partner_name"),
        "partner_type": record.get("partner_type"),
        "partner_status": record.get("partner_status"),
        "contract_start_date": record.get("contract_start_date"),
        "contract_end_date": record.get("contract_end_date"),
        "contract_expiry_on": record.get("contract_expiry_on"),
        "remaining_minutes": record.get("remaining_minutes"),
        "patient_id": record.get("patient_id"),
        "ld_internalno": record.get("ld_internalno"),
        "vil_internalno": record.get("vil_internalno"),
        "ayush_no": record.get("ayush_no"),
        "external_refno": record.get("external_refno"),
        "patient_uhid": record.get("patient_uhid"),
        "channel_thread_id": record.get("channel_thread_id"),
        "channel_message_id": record.get("channel_message_id"),
        "patient_passport": patient_passport,
        "patpassport_no": patient_passport,
        "passport_no": patient_passport,
        "patient_name": patient_name,
        "workflow_status": record.get("wrkflw_status"),
        "wrkflw_status": record.get("wrkflw_status"),
        "assigned_to": (
            record.get("assigned_user")
            or record.get("assigned_to")
            or record.get("entered_by")
        ),
        "hospital": (
            record.get("hospital_name")
            or record.get("hospital_shrtnm")
        ),
        "hospital_name": record.get("hospital_name"),
        "agency": record.get("agent_shrtnm"),
        "invoice_number": record.get("invoice_number"),
        "print_status": record.get("print_status"),
        "prntentered_at": printentered_at,
        "printentered_at": printentered_at,
        "prntentered_by": record.get("prntentered_by"),
        "inv_currency": record.get("inv_currency"),
        "inv_amount": record.get("inv_amount"),
        "totalgst_amount": record.get("totalgst_amount"),
        "totaltds_amount": record.get("totaltds_amount"),
        "netgst_amount": record.get("netgst_amount"),
        "nettds_amount": record.get("nettds_amount"),
        "netinv_amount": record.get("netinv_amount"),
        "totalinv_amount": record.get("totalinv_amount"),
        "ageing_started_on": (
            record.get("alert_ageing_started_on").isoformat(sep=" ")
            if isinstance(record.get("alert_ageing_started_on"), datetime)
            else str(record.get("alert_ageing_started_on") or "")
        ),
    }

    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT INTO alert_instance
            (
                rule_id,
                reference_type,
                reference_id,
                alert_name,
                module_code,
                severity,
                alert_status,
                ageing_minutes,
                warning_triggered_on,
                critical_triggered_on,
                last_notified_on,
                next_notification_on,
                notification_count,
                alert_context_json,
                created_on,
                updated_on
            )
            VALUES
            (
                %s, %s, %s, %s, %s, %s, 'OPEN', %s,
                CASE WHEN %s = 'WARNING' THEN NOW() ELSE NULL END,
                CASE WHEN %s = 'CRITICAL' THEN NOW() ELSE NULL END,
                NULL, NULL, 0, %s, NOW(), NOW()
            )
            """,
            (
                rule["id"],
                reference_type,
                str(record["id"]).strip(),
                rule["alert_name"],
                rule["module_code"],
                severity,
                int(record.get("ageing_minutes") or 0),
                severity,
                severity,
                json.dumps(context, default=str),
            ),
        )
        instance_id = int(cursor.lastrowid)
        connection.commit()
        return instance_id
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        _close(connection, cursor)

def update_alert_instance(
    alert_instance_id: int,
    severity: str,
    ageing_minutes: int,
    *,
    escalate: bool = False,
) -> None:
    """
    Refresh an existing alert instance.

    Behaviour preserved:
    - RESOLVED alerts reopen when the source record still matches.
    - WARNING alerts can escalate to CRITICAL.

    Snooze fix:
    - An expired timed snooze is changed back to OPEN.
    - If its repeat timestamp is missing or still in the future, it is made due
      immediately so the normal repeat rules can evaluate it once.
    """
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            UPDATE alert_instance
            SET
                severity = %s,
                ageing_minutes = %s,
                critical_triggered_on =
                    CASE
                        WHEN %s = 'CRITICAL'
                         AND critical_triggered_on IS NULL
                        THEN NOW()
                        ELSE critical_triggered_on
                    END,
                next_notification_on =
                    CASE
                        WHEN alert_status = 'SNOOZED'
                         AND snoozed_until IS NOT NULL
                         AND snoozed_until <= NOW()
                        THEN
                            CASE
                                WHEN next_notification_on IS NULL
                                  OR next_notification_on > NOW()
                                THEN NOW()
                                ELSE next_notification_on
                            END
                        ELSE next_notification_on
                    END,
                alert_status =
                    CASE
                        WHEN alert_status = 'RESOLVED' THEN 'OPEN'
                        WHEN alert_status = 'SNOOZED'
                         AND snoozed_until IS NOT NULL
                         AND snoozed_until <= NOW()
                        THEN 'OPEN'
                        ELSE alert_status
                    END,
                updated_on = NOW()
            WHERE id = %s
            """,
            (severity, int(ageing_minutes), severity, alert_instance_id),
        )
        connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        _close(connection, cursor)



def mark_alert_notified(
    alert_instance_id: int,
    repeat_interval_minutes: int,
    notification_anchor: datetime | None = None,
    *,
    is_repeat: bool = False,
) -> None:
    """
    Mark one completed notification cycle.

    notification_count counts every cycle:
    - initial WARNING
    - initial CRITICAL escalation
    - repeat cycles

    repeat_count counts only cycles created because should_repeat() returned
    True. The initial WARNING and initial CRITICAL notification are therefore
    not counted as repeats.

    The next repeat is calculated from the scheduler-cycle minute captured
    before channel delivery begins. This prevents synchronous email delivery
    from shifting a one-minute repeat beyond the next scheduler heartbeat.
    """
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        repeat_increment = 1 if is_repeat else 0

        if repeat_interval_minutes > 0:
            schedule_anchor = (
                notification_anchor
                or datetime.now().replace(second=0, microsecond=0)
            )

            cursor.execute(
                """
                UPDATE alert_instance
                SET
                    last_notified_on = NOW(),
                    next_notification_on =
                        DATE_ADD(%s, INTERVAL %s MINUTE),
                    notification_count =
                        COALESCE(notification_count, 0) + 1,
                    repeat_count =
                        COALESCE(repeat_count, 0) + %s,
                    updated_on = NOW()
                WHERE id = %s
                """,
                (
                    schedule_anchor,
                    int(repeat_interval_minutes),
                    repeat_increment,
                    alert_instance_id,
                ),
            )
        else:
            cursor.execute(
                """
                UPDATE alert_instance
                SET
                    last_notified_on = NOW(),
                    next_notification_on = NULL,
                    notification_count =
                        COALESCE(notification_count, 0) + 1,
                    repeat_count =
                        COALESCE(repeat_count, 0) + %s,
                    updated_on = NOW()
                WHERE id = %s
                """,
                (repeat_increment, alert_instance_id),
            )

        connection.commit()
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        _close(connection, cursor)



def should_repeat(instance: dict[str, Any], rule: dict[str, Any]) -> bool:
    repeat_interval = int(rule.get("repeat_interval_minutes") or 0)
    if repeat_interval <= 0:
        return False

    now = datetime.now()
    status = str(instance.get("alert_status") or "OPEN").strip().upper()

    if status == "RESOLVED":
        return False

    # Active/indefinite snoozes suppress repeats. update_alert_instance()
    # normally reopens an expired timed snooze before this method is called;
    # this check is retained as a defensive safeguard.
    if status == "SNOOZED":
        snoozed_until = instance.get("snoozed_until")
        if snoozed_until is None or snoozed_until > now:
            return False

    maximum = int(rule.get("maximum_repeat_count") or 0)

    stored_repeat_count = instance.get("repeat_count")
    if stored_repeat_count is not None:
        repeat_count = int(stored_repeat_count or 0)
    else:
        # Compatibility for legacy rows that pre-date repeat_count tracking:
        # remove the initial WARNING and/or CRITICAL notification cycles from
        # notification_count; only the remaining cycles are repeats.
        notification_count = int(instance.get("notification_count") or 0)
        initial_cycles = 0
        if instance.get("warning_triggered_on") is not None:
            initial_cycles += 1
        if instance.get("critical_triggered_on") is not None:
            initial_cycles += 1
        if notification_count > 0 and initial_cycles == 0:
            initial_cycles = 1
        repeat_count = max(notification_count - initial_cycles, 0)

    if maximum > 0 and repeat_count >= maximum:
        return False

    next_on = instance.get("next_notification_on")
    return next_on is not None and next_on <= now



def get_next_notification_sequence(alert_instance_id: int) -> int:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)
        cursor.execute(
            """
            SELECT COALESCE(MAX(notification_sequence), 0) + 1 AS next_sequence
            FROM alert_delivery_log
            WHERE alert_instance_id = %s
            """,
            (alert_instance_id,),
        )
        row = cursor.fetchone() or {}
        return int(row.get("next_sequence") or 1)
    finally:
        _close(connection, cursor)


def insert_delivery_log(
    *,
    alert_instance_id: int,
    rule_id: int,
    severity: str,
    notification_sequence: int,
    recipient_type: str,
    configured_recipient_value: str,
    resolved_user_code: str | None,
    resolved_recipient_name: str | None,
    delivery_channel: str,
    destination: str | None,
    subject_text: str,
    message_text: str,
    delivery_status: str,
) -> bool:
    connection = cursor = None
    try:
        connection = get_db_connection()
        cursor = connection.cursor()
        cursor.execute(
            """
            INSERT IGNORE INTO alert_delivery_log
            (
                alert_instance_id,
                rule_id,
                severity,
                notification_sequence,
                recipient_type,
                configured_recipient_value,
                resolved_user_code,
                resolved_recipient_name,
                delivery_channel,
                destination,
                subject_text,
                message_text,
                delivery_status,
                attempt_count,
                attempted_on,
                sent_on,
                delivered_on,
                read_flag,
                created_on,
                updated_on
            )
            VALUES
            (
                %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,
                CASE WHEN %s IN ('DELIVERED','SENT') THEN 1 ELSE 0 END,
                CASE WHEN %s IN ('DELIVERED','SENT') THEN NOW() ELSE NULL END,
                CASE WHEN %s IN ('DELIVERED','SENT') THEN NOW() ELSE NULL END,
                CASE WHEN %s = 'DELIVERED' THEN NOW() ELSE NULL END,
                'N', NOW(), NOW()
            )
            """,
            (
                alert_instance_id, rule_id, severity, notification_sequence,
                recipient_type, configured_recipient_value,
                resolved_user_code, resolved_recipient_name,
                delivery_channel, destination, subject_text, message_text,
                delivery_status, delivery_status, delivery_status,
                delivery_status, delivery_status,
            ),
        )
        inserted = int(cursor.rowcount or 0) == 1
        connection.commit()
        return inserted
    except Exception:
        if connection is not None:
            connection.rollback()
        raise
    finally:
        _close(connection, cursor)



def auto_resolve_nonmatching_alerts(rule: dict[str, Any]) -> int:
    if str(rule.get("auto_resolve") or "N").upper() != "Y":
        return 0

    reference_type = get_reference_type_for_rule(rule)
    connection = cursor = None

    try:
        connection = get_db_connection()
        cursor = connection.cursor(dictionary=True)

        if reference_type == "PATIENT_WORKFLOW":
            cursor.execute(
                """
                UPDATE alert_instance ai
                LEFT JOIN patient_wrkflw pw
                  ON ai.reference_type = 'PATIENT_WORKFLOW'
                 AND ai.reference_id = pw.ld_seqno
                SET
                    ai.alert_status = 'RESOLVED',
                    ai.resolved_by = 'SYSTEM',
                    ai.resolved_on = NOW(),
                    ai.resolution_reason =
                        'Workflow status no longer matches the alert rule',
                    ai.next_notification_on = NULL,
                    ai.updated_on = NOW()
                WHERE ai.rule_id = %s
                  AND ai.reference_type = 'PATIENT_WORKFLOW'
                  AND ai.alert_status IN ('OPEN','ACKNOWLEDGED','SNOOZED')
                  AND (
                        pw.ld_seqno IS NULL
                        OR UPPER(TRIM(COALESCE(pw.wrkflw_status, '')))
                           <> UPPER(TRIM(%s))
                      )
                """,
                (rule["id"], rule.get("workflow_status")),
            )

        elif reference_type == "PARTNER":
            warning_minutes = int(rule.get("warning_after_minutes") or 0)
            expiry_expr = (
                "DATE_SUB(DATE_ADD(DATE(pr.contend_date), INTERVAL 1 DAY), "
                "INTERVAL 1 SECOND)"
            )
            cursor.execute(
                f"""
                UPDATE alert_instance ai
                LEFT JOIN partner_reg pr
                  ON CONVERT(TRIM(COALESCE(ai.reference_id, '')) USING utf8mb4)
                         COLLATE utf8mb4_unicode_ci
                     = CONVERT(TRIM(COALESCE(pr.short_name, '')) USING utf8mb4)
                         COLLATE utf8mb4_unicode_ci
                SET
                    ai.alert_status = 'RESOLVED',
                    ai.resolved_by = 'SYSTEM',
                    ai.resolved_on = NOW(),
                    ai.resolution_reason =
                        'Partner contract is no longer within the alert window',
                    ai.next_notification_on = NULL,
                    ai.updated_on = NOW()
                WHERE ai.rule_id = %s
                  AND ai.reference_type = 'PARTNER'
                  AND ai.alert_status IN ('OPEN','ACKNOWLEDGED','SNOOZED')
                  AND (
                        pr.short_name IS NULL
                        OR pr.contend_date IS NULL
                        OR (
                            pr.partner_status IS NOT NULL
                            AND TRIM(pr.partner_status) <> ''
                            AND UPPER(TRIM(pr.partner_status)) <> 'ACTIVE'
                        )
                        OR TIMESTAMPDIFF(MINUTE, NOW(), {expiry_expr}) > %s
                      )
                """,
                (rule["id"], warning_minutes),
            )

        elif reference_type == "INVOICE":
            cursor.execute(
                """
                UPDATE alert_instance ai
                SET
                    ai.alert_status = 'RESOLVED',
                    ai.resolved_by = 'SYSTEM',
                    ai.resolved_on = NOW(),
                    ai.resolution_reason =
                        'Invoice workflow status no longer matches the alert rule',
                    ai.next_notification_on = NULL,
                    ai.updated_on = NOW()
                WHERE ai.rule_id = %s
                  AND ai.reference_type = 'INVOICE'
                  AND ai.alert_status IN ('OPEN','ACKNOWLEDGED','SNOOZED')
                  AND NOT EXISTS
                  (
                      SELECT 1
                      FROM invoice_master im
                      WHERE CONVERT(
                                TRIM(COALESCE(im.invoice_number, ''))
                                USING utf8mb4
                            ) COLLATE utf8mb4_unicode_ci
                            = CONVERT(
                                  TRIM(COALESCE(ai.reference_id, ''))
                                  USING utf8mb4
                              ) COLLATE utf8mb4_unicode_ci
                        AND UPPER(TRIM(COALESCE(im.wrkflw_status, '')))
                            = UPPER(TRIM(%s))
                        AND UPPER(TRIM(COALESCE(im.print_status, '')))
                            = 'PRINTED'
                        AND im.prntentered_at IS NOT NULL
                  )
                """,
                (rule["id"], rule.get("workflow_status")),
            )

        else:
            cursor.execute(
                """
                UPDATE alert_instance ai
                LEFT JOIN operation_queue_master oq
                  ON ai.reference_type = 'OPERATION_QUEUE'
                 AND ai.reference_id = oq.id
                SET
                    ai.alert_status = 'RESOLVED',
                    ai.resolved_by = 'SYSTEM',
                    ai.resolved_on = NOW(),
                    ai.resolution_reason =
                        'Workflow status no longer matches the alert rule',
                    ai.next_notification_on = NULL,
                    ai.updated_on = NOW()
                WHERE ai.rule_id = %s
                  AND ai.reference_type = 'OPERATION_QUEUE'
                  AND ai.alert_status IN ('OPEN','ACKNOWLEDGED','SNOOZED')
                  AND (
                        oq.id IS NULL
                        OR UPPER(TRIM(COALESCE(oq.wrkflw_status, '')))
                           <> UPPER(TRIM(%s))
                      )
                """,
                (rule["id"], rule.get("workflow_status")),
            )

        count = int(cursor.rowcount or 0)
        connection.commit()
        return count

    except Exception:
        if connection is not None:
            connection.rollback()
        raise

    finally:
        _close(connection, cursor)
