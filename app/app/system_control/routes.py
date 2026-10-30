from flask import render_template, request, redirect, url_for, jsonify, flash, session
from flask_login import login_required, current_user

from app.common.db import get_db_connection

from app.system_control.sla_rule_builder.sla_rule_builder_service import (
    get_sla_rule_builder_context,
    save_sla_rule,
)

from app.system_control.invoice_template_builder.invoice_template_builder_service import (
    get_invoice_template_builder_context,
    save_invoice_template_builder_payload,
    get_invoice_template_preview_payload,
)

from app.system_control.user_management.user_management_service import (
    get_user_management_context,
    save_user,
)

from app.system_control.user_management.user_screen_access_service import (
    get_user_screen_access_context,
)

from app.system_control.round_robin_assignment.round_robin_assignment_service import (
    build_page_context,
    save_pool,
    save_rule,
)


def get_active_users_for_screen_access():
    conn = None
    cursor = None

    try:
        conn = get_db_connection()
        if not conn:
            return []

        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                user_code,
                user_name
            FROM user_master
            WHERE UPPER(TRIM(user_status)) = 'ACTIVE'
            ORDER BY user_name
        """)

        return cursor.fetchall()

    except Exception:
        return []

    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _clean_department_list(department_names):
    clean_departments = []
    seen = set()

    for dept_name in department_names:
        dept_name = (dept_name or "").strip()

        if not dept_name:
            continue

        dept_key = dept_name.upper()

        if dept_key not in seen:
            clean_departments.append(dept_name)
            seen.add(dept_key)

    return clean_departments


def _replace_hospital_department_mapping(cursor, hospital_name, department_names, dept_status):
    clean_departments = _clean_department_list(department_names)

    cursor.execute("""
        DELETE FROM hospdept_master
        WHERE UPPER(TRIM(partner_shrtnm)) = UPPER(TRIM(%s))
    """, (hospital_name,))

    deleted_count = cursor.rowcount if cursor.rowcount is not None else 0

    inserted_count = 0

    for department_name in clean_departments:
        cursor.execute("""
            INSERT INTO hospdept_master
            (
                partner_shrtnm,
                hospital_dept,
                dept_status
            )
            VALUES
            (
                %s, %s, %s
            )
        """, (hospital_name, department_name, dept_status))

        inserted_count += 1

    return {
        "deleted_count": deleted_count,
        "inserted_count": inserted_count,
        "saved_departments": clean_departments,
    }


def register_system_control_routes(app):

    # =====================================================
    # OPERATIONAL REPORTS
    # =====================================================
    @app.route("/operational-reports", methods=["GET"])
    @login_required
    def operational_reports():
        return render_template("operational_reports.html")

    # =====================================================
    # INVOICE MANAGEMENT REPORTS
    # =====================================================
    @app.route("/invoice-management-reports", methods=["GET"])
    @login_required
    def invoice_management_reports():
        return render_template("invoice_management_reports.html")

    # =====================================================
    # SLA REPORTS
    # =====================================================
    @app.route("/sla-reports", methods=["GET"])
    @login_required
    def sla_reports():
        return render_template("sla_reports.html")

    # =====================================================
    # HOSPITAL DEPARTMENT MAPPING
    # =====================================================
    @app.route("/hospital-department-mapping", methods=["GET", "POST"])
    @login_required
    def hospital_department_mapping():

        conn = None
        cursor = None

        hospitals = []
        departments = []
        mapped_departments = []
        selected_hospital = ""

        try:
            conn = get_db_connection()

            if not conn:
                flash("Database connection failed.", "danger")
                return render_template(
                    "hospital_department_mapping.html",
                    hospitals=hospitals,
                    departments=departments,
                    mapped_departments=mapped_departments,
                    selected_hospital=selected_hospital,
                )

            cursor = conn.cursor(dictionary=True)

            # =====================================================
            # LOAD HOSPITAL LOV
            # =====================================================
            cursor.execute("""
                SELECT DISTINCT
                    TRIM(short_name) AS short_name,
                    TRIM(partner_city) AS partner_city
                FROM partner_reg
                WHERE UPPER(TRIM(partner_type)) = 'HOSPITAL'
                  AND short_name IS NOT NULL
                  AND TRIM(short_name) <> ''
                ORDER BY TRIM(short_name)
            """)
            hospitals = cursor.fetchall()

            # =====================================================
            # SELECTED HOSPITAL
            # GET: View button sends ?hospital_name=...
            # POST: Save button sends form hospital_name
            # =====================================================
            selected_hospital = (
                request.args.get("hospital_name")
                or request.form.get("hospital_name")
                or ""
            ).strip()

            # =====================================================
            # SAVE MAPPING
            # HTML posts to url_for('hospital_department_mapping')
            # =====================================================
            if request.method == "POST":

                hospital_name = (request.form.get("hospital_name") or "").strip()
                department_names = request.form.getlist("department_names")
                mapping_status = (request.form.get("mapping_status") or "Active").strip()

                print("POST HOSPITAL:", hospital_name)
                print("POST DEPARTMENTS:", department_names)

                if mapping_status not in ["Active", "Inactive"]:
                    mapping_status = "Active"

                if not hospital_name:
                    flash("Please select Hospital.", "warning")
                    return redirect(url_for("hospital_department_mapping"))

                save_result = _replace_hospital_department_mapping(
                    cursor=cursor,
                    hospital_name=hospital_name,
                    department_names=department_names,
                    dept_status=mapping_status,
                )

                conn.commit()

                flash(
                    f"Hospital Department Mapping saved successfully. "
                    f"Deleted Old: {save_result['deleted_count']}, "
                    f"Saved Current: {save_result['inserted_count']}",
                    "success"
                )

                return redirect(
                    url_for(
                        "hospital_department_mapping",
                        hospital_name=hospital_name
                    )
                )

            # =====================================================
            # LOAD MAPPED DEPARTMENTS FOR SELECTED HOSPITAL
            # This feeds the right box: SELECTED FOR NEW SAVE
            # =====================================================
            if selected_hospital:
                cursor.execute("""
                    SELECT
                        TRIM(partner_shrtnm) AS hospital_name,
                        TRIM(hospital_dept) AS department_name,
                        TRIM(dept_status) AS dept_status,
                        '' AS remarks
                    FROM hospdept_master
                    WHERE UPPER(TRIM(partner_shrtnm)) = UPPER(TRIM(%s))
                      AND hospital_dept IS NOT NULL
                      AND TRIM(hospital_dept) <> ''
                    ORDER BY TRIM(hospital_dept)
                """, (selected_hospital,))

                mapped_departments = cursor.fetchall()

                print("SELECTED HOSPITAL:", selected_hospital)
                print("MAPPED DEPARTMENTS COUNT:", len(mapped_departments))
                print("MAPPED DEPARTMENTS:", mapped_departments)

            # =====================================================
            # LOAD ACTIVE DEPARTMENT LOV
            # =====================================================
            cursor.execute("""
                SELECT DISTINCT
                    TRIM(department_name) AS department_name
                FROM department_master
                WHERE UPPER(TRIM(department_status)) = 'ACTIVE'
                  AND department_name IS NOT NULL
                  AND TRIM(department_name) <> ''
                ORDER BY TRIM(department_name)
            """)
            all_departments = cursor.fetchall()

            # =====================================================
            # REMOVE ALREADY MAPPED FROM LEFT SIDE
            # =====================================================
            mapped_names = set()

            for row in mapped_departments:
                dept_name = row.get("department_name")
                if dept_name:
                    mapped_names.add(dept_name.strip().upper())

            for dept in all_departments:
                dept_name = (dept.get("department_name") or "").strip()

                if not dept_name:
                    continue

                if dept_name.upper() not in mapped_names:
                    departments.append(dept)

            print("HOSPITAL LOV COUNT:", len(hospitals))
            print("AVAILABLE DEPARTMENTS COUNT:", len(departments))

        except Exception as e:
            if conn:
                conn.rollback()

            print("Hospital Department Mapping Error:", str(e))
            flash(f"Hospital Department Mapping Error: {e}", "danger")

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

        return render_template(
            "hospital_department_mapping.html",
            hospitals=hospitals,
            departments=departments,
            mapped_departments=mapped_departments,
            selected_hospital=selected_hospital,
        )

    # =====================================================
    # HOSPITAL DEPARTMENT MAPPING - SAVE
    # Kept for backward compatibility if any old form still uses this endpoint
    # =====================================================
    @app.route("/hospital-department-mapping/save", methods=["POST"])
    @login_required
    def hospital_department_mapping_save():

        hospital_name = (
            request.form.get("hospital_name")
            or request.form.get("partner_shrtnm")
            or request.form.get("hospital")
            or ""
        ).strip()

        selected_departments = (
            request.form.getlist("department_names")
            or request.form.getlist("departments")
            or request.form.getlist("department_name")
            or request.form.getlist("hospital_dept")
        )

        dept_status = (
            request.form.get("mapping_status")
            or request.form.get("dept_status")
            or request.form.get("status")
            or "Active"
        ).strip()

        if dept_status not in ["Active", "Inactive"]:
            dept_status = "Active"

        if not hospital_name:
            flash("Hospital is required.", "warning")
            return redirect(url_for("hospital_department_mapping"))

        conn = None
        cursor = None

        try:
            conn = get_db_connection()

            if not conn:
                flash("Database connection failed.", "danger")
                return redirect(url_for("hospital_department_mapping"))

            cursor = conn.cursor(dictionary=True)

            print("POST SAVE HOSPITAL:", hospital_name)
            print("POST SAVE DEPARTMENTS:", selected_departments)

            save_result = _replace_hospital_department_mapping(
                cursor=cursor,
                hospital_name=hospital_name,
                department_names=selected_departments,
                dept_status=dept_status,
            )

            conn.commit()

            flash(
                f"Hospital department mapping saved successfully. "
                f"Deleted Old: {save_result['deleted_count']}, "
                f"Saved Current: {save_result['inserted_count']}",
                "success"
            )

            return redirect(
                url_for(
                    "hospital_department_mapping",
                    hospital_name=hospital_name
                )
            )

        except Exception as e:
            if conn:
                conn.rollback()

            print("Hospital Department Mapping Save Error:", str(e))
            flash(f"Hospital Department Mapping Save Error: {e}", "danger")

            return redirect(
                url_for(
                    "hospital_department_mapping",
                    hospital_name=hospital_name
                )
            )

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    # =====================================================
    # SLA RULE BUILDER
    # =====================================================
    @app.route("/sla-rule-builder", methods=["GET", "POST"])
    @login_required
    def sla_rule_builder():

        if request.method == "POST":

            user_code = getattr(current_user, "user_code", None) or "SYSTEM"

            save_result = save_sla_rule(
                request.form,
                user_code=user_code
            )

            if save_result.get("success"):

                saved_rule_id = save_result.get("sla_rule_id")

                success_message = save_result.get(
                    "message",
                    "Saved successfully."
                )

                redirect_mode = "edit" if saved_rule_id else "add"

                return redirect(
                    url_for(
                        "sla_rule_builder",
                        rule_id=saved_rule_id,
                        mode=redirect_mode,
                        success_message=success_message
                    )
                )

            selected_rule_id = (
                request.form.get("rule_id")
                or request.form.get("id")
            )

            context = get_sla_rule_builder_context(
                form_data=request.form,
                selected_rule_id=selected_rule_id,
                mode=request.args.get("mode") or "add",
                save_error=save_result.get(
                    "error",
                    "Unable to save SLA rule."
                ),
                success_message=None,
            )

            return render_template(
                "sla_rule_builder.html",
                **context
            )

        context = get_sla_rule_builder_context(
            selected_rule_id=request.args.get("rule_id"),
            mode=request.args.get("mode"),
            save_error=None,
            success_message=request.args.get("success_message"),
        )

        return render_template(
            "sla_rule_builder.html",
            **context
        )

    # =====================================================
    # ROUND ROBIN ASSIGNMENT
    # =====================================================
    @app.route("/round-robin-assignment", methods=["GET", "POST"])
    @login_required
    def round_robin_assignment():

        current_user_code = (
            getattr(current_user, "user_code", None)
            or session.get("user_code")
            or session.get("username")
            or session.get("logged_in_user")
            or "SYSTEM"
        )

        if request.method == "POST":

            form_section = (
                request.form.get("form_section") or ""
            ).strip().lower()

            # =================================================
            # SAVE ASSIGNMENT POOL
            # =================================================
            if form_section == "pool":

                result = save_pool(
                    request.form,
                    current_user_code,
                )

                if result.get("success"):
                    return redirect(
                        url_for(
                            "round_robin_assignment",
                            tab="pools",
                            pool_id=result.get("pool_id"),
                            mode="edit",
                            pool_success=result.get("message"),
                        )
                    )

                context = build_page_context(
                    active_tab="pools",
                    pool_form=(
                        result.get("pool_form")
                        or dict(request.form)
                    ),
                    pool_members=(
                        result.get("pool_members")
                        or []
                    ),
                    pool_error=(
                        result.get("error")
                        or "Unable to save Assignment Pool."
                    ),
                )

                return render_template(
                    "round_robin_assignment.html",
                    **context
                )

            # =================================================
            # SAVE ASSIGNMENT RULE
            # =================================================
            if form_section == "rule":

                result = save_rule(
                    request.form,
                    current_user_code,
                )

                if result.get("success"):
                    return redirect(
                        url_for(
                            "round_robin_assignment",
                            tab="rules",
                            rule_id=result.get("rule_id"),
                            mode="edit",
                            rule_success=result.get("message"),
                        )
                    )

                context = build_page_context(
                    active_tab="rules",
                    rule_form=(
                        result.get("rule_form")
                        or dict(request.form)
                    ),
                    rule_error=(
                        result.get("error")
                        or "Unable to save Assignment Rule."
                    ),
                )

                return render_template(
                    "round_robin_assignment.html",
                    **context
                )

            context = build_page_context(
                active_tab=(
                    request.form.get("active_tab")
                    or request.args.get("tab")
                    or "rules"
                ),
                rule_error="Invalid Round Robin form submission.",
            )

            return render_template(
                "round_robin_assignment.html",
                **context
            )

        # =====================================================
        # PAGE LOAD / VIEW / EDIT
        # =====================================================
        active_tab = (
            request.args.get("tab") or "rules"
        ).strip().lower()

        if active_tab not in {"rules", "pools", "status"}:
            active_tab = "rules"

        context = build_page_context(
            active_tab=active_tab,
            rule_id=request.args.get("rule_id"),
            pool_id=request.args.get("pool_id"),
            rule_success=request.args.get("rule_success"),
            pool_success=request.args.get("pool_success"),
        )

        return render_template(
            "round_robin_assignment.html",
            **context
        )

    # =====================================================
    # INVOICE TEMPLATE BUILDER
    # =====================================================
    @app.route("/invoice-template-builder", methods=["GET"])
    @login_required
    def invoice_template_builder():

        context = get_invoice_template_builder_context(
            selected_template_code=request.args.get("template_code"),
            success_message=request.args.get("success_message"),
            error_message=request.args.get("error_message"),
        )

        return render_template(
            "invoice_template_builder.html",
            **context
        )

    @app.route("/invoice-template-builder/save", methods=["POST"])
    @login_required
    def invoice_template_builder_save():

        payload = request.get_json(silent=True) or {}

        user_code = getattr(current_user, "user_code", None) or "SYSTEM"

        result = save_invoice_template_builder_payload(
            payload,
            user_code=user_code
        )

        status_code = 200 if result.get("success") else 400

        return jsonify(result), status_code

    @app.route("/invoice-template-builder/preview", methods=["POST"])
    @login_required
    def invoice_template_builder_preview():

        payload = request.get_json(silent=True) or {}

        preview_payload = get_invoice_template_preview_payload(payload)

        return jsonify({
            "success": True,
            "preview": preview_payload
        })

    # =====================================================
    # USER MANAGEMENT
    # =====================================================
    @app.route("/user-management", methods=["GET", "POST"])
    @login_required
    def user_management():

        if request.method == "POST":

            result = save_user(request.form)

            if result.get("success"):

                return redirect(
                    url_for(
                        "user_management",
                        user_code=result.get("user_code"),
                        success_message=result.get("message")
                    )
                )

            context = get_user_management_context(
                selected_user_code=request.form.get("user_code"),
                success_message=None,
                error_message=result.get("error")
                or "Unable to save user."
            )

            context["selected_user"] = {
                "user_name": request.form.get("user_name"),
                "user_code": request.form.get("user_code"),
                "user_dob": request.form.get("user_dob"),
                "user_gender": request.form.get("user_gender"),
                "user_contno": request.form.get("user_contno"),
                "user_status": request.form.get("user_status"),
                "user_type": request.form.get("user_type"),
            }

            return render_template(
                "user_management.html",
                **context
            )

        context = get_user_management_context(
            selected_user_code=request.args.get("user_code"),
            success_message=request.args.get("success_message"),
            error_message=request.args.get("error_message"),
        )

        return render_template(
            "user_management.html",
            **context
        )

    # =====================================================
    # USER SCREEN ACCESS
    # =====================================================
    @app.route("/user-screen-access", methods=["GET"])
    @login_required
    def user_screen_access():

        selected_user_code = request.args.get("user_code")

        context = get_user_screen_access_context(
            selected_user_code=selected_user_code,
            success_message=request.args.get("success_message"),
            error_message=request.args.get("error_message"),
        )

        return render_template(
            "user_screen_access.html",
            **context
        )

    # =====================================================
    # USER SCREEN ACCESS API - SAVE
    # =====================================================
    @app.route("/api/system-control/user-screen-access/save", methods=["POST"])
    @login_required
    def api_save_user_screen_access():

        payload = request.get_json(silent=True) or {}

        user_code = payload.get("user_code")
        access_rows = payload.get("access") or []

        if not user_code:
            return jsonify({
                "success": False,
                "message": "User code is required."
            }), 400

        conn = None
        cursor = None

        try:
            conn = get_db_connection()

            if not conn:
                return jsonify({
                    "success": False,
                    "message": "Database connection failed."
                }), 500

            cursor = conn.cursor(dictionary=True)

            cursor.execute(
                """
                DELETE FROM user_screen_access
                WHERE user_code = %s
                """,
                (user_code,)
            )

            for row in access_rows:
                screen_code = row.get("screen_code")

                if not screen_code:
                    continue

                cursor.execute(
                    """
                    INSERT INTO user_screen_access
                    (
                        user_code,
                        screen_code,
                        can_view,
                        can_add,
                        can_edit,
                        can_delete,
                        is_active,
                        created_on
                    )
                    VALUES
                    (
                        %s, %s, %s, %s, %s, %s, 'Y', NOW()
                    )
                    """,
                    (
                        user_code,
                        screen_code,
                        row.get("can_view", "N"),
                        row.get("can_add", "N"),
                        row.get("can_edit", "N"),
                        row.get("can_delete", "N"),
                    )
                )

            conn.commit()

            return jsonify({
                "success": True,
                "message": "User screen access saved successfully."
            })

        except Exception as e:
            if conn:
                conn.rollback()

            return jsonify({
                "success": False,
                "message": str(e)
            }), 500

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()

    # =====================================================
    # USER SCREEN ACCESS API - LOAD
    # =====================================================
    @app.route("/api/system-control/user-screen-access/<user_code>", methods=["GET"])
    @login_required
    def api_get_user_screen_access(user_code):

        conn = None
        cursor = None

        try:
            conn = get_db_connection()

            if not conn:
                return jsonify({
                    "success": False,
                    "message": "Database connection failed.",
                    "access": []
                }), 500

            cursor = conn.cursor(dictionary=True)

            cursor.execute(
                """
                SELECT
                    screen_code,
                    can_view,
                    can_add,
                    can_edit,
                    can_delete
                FROM user_screen_access
                WHERE user_code = %s
                  AND IFNULL(is_active, 'Y') = 'Y'
                """,
                (user_code,)
            )

            rows = cursor.fetchall()

            return jsonify({
                "success": True,
                "access": rows
            })

        except Exception as e:
            return jsonify({
                "success": False,
                "message": str(e),
                "access": []
            }), 500

        finally:
            if cursor:
                cursor.close()
            if conn:
                conn.close()