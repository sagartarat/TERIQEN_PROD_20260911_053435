from flask import render_template, request, redirect, url_for, flash
from flask_login import login_required
from app.common.db import get_db


def register_hospital_department_mapping_routes(app):

    @app.route("/hospital-department-mapping", methods=["GET", "POST"])
    @login_required
    def hospital_department_mapping():
        hospitals = []
        departments = []
        mapped_departments = []
        selected_hospital = ""

        conn = None
        cursor = None

        try:
            conn = get_db()
            cursor = conn.cursor(dictionary=True)

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

            selected_hospital = (
                request.args.get("hospital_name", "")
                or request.form.get("hospital_name", "")
                or ""
            ).strip()

            if request.method == "POST":
                hospital_name = (request.form.get("hospital_name") or "").strip()
                department_names = request.form.getlist("department_names")
                mapping_status = (request.form.get("mapping_status") or "Active").strip()

                print("POST HOSPITAL:", hospital_name)
                print("POST DEPARTMENTS:", department_names)

                if not hospital_name:
                    flash("Please select Hospital.", "warning")
                    return redirect(url_for("hospital_department_mapping"))

                if mapping_status not in ["Active", "Inactive"]:
                    mapping_status = "Active"

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

                cursor.execute("""
                    DELETE FROM hospdept_master
                    WHERE UPPER(TRIM(partner_shrtnm)) = UPPER(TRIM(%s))
                """, (hospital_name,))

                deleted_count = cursor.rowcount if cursor.rowcount is not None else 0
                inserted_count = 0

                for dept_name in clean_departments:
                    cursor.execute("""
                        INSERT INTO hospdept_master
                            (
                                partner_shrtnm,
                                hospital_dept,
                                dept_status
                            )
                        VALUES
                            (%s, %s, %s)
                    """, (
                        hospital_name,
                        dept_name,
                        mapping_status
                    ))

                    inserted_count += 1

                conn.commit()

                flash(
                    f"Hospital Department Mapping saved successfully. "
                    f"Deleted Old: {deleted_count}, Saved Current: {inserted_count}",
                    "success"
                )

                return redirect(
                    url_for(
                        "hospital_department_mapping",
                        hospital_name=hospital_name
                    )
                )

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

            mapped_names = set()

            for row in mapped_departments:
                dept_name = row.get("department_name")
                if dept_name:
                    mapped_names.add(dept_name.strip().upper())

            departments = []

            for dept in all_departments:
                dept_name = (dept.get("department_name") or "").strip()

                if not dept_name:
                    continue

                if dept_name.upper() not in mapped_names:
                    departments.append(dept)

        except Exception as e:
            if conn:
                conn.rollback()

            print("Hospital Department Mapping Error:", e)
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
            selected_hospital=selected_hospital
        )