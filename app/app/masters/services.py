import re


def fetch_countries(conn):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT country_name
            FROM country_master
            WHERE country_name IS NOT NULL
              AND TRIM(country_name) <> ''
            ORDER BY country_name ASC
            """
        )
        rows = cur.fetchall() or []
        return rows
    finally:
        try:
            cur.close()
        except Exception:
            pass


def fetch_currencies(conn):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute("SELECT ccy_code, ccy_name FROM ccy_master ORDER BY ccy_code ASC")
        rows = cur.fetchall() or []
        return rows
    finally:
        try:
            cur.close()
        except Exception:
            pass


def fetch_country_code(conn, country_name):
    country_name = (country_name or "").strip()
    if not country_name:
        return "NA"

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT country_code
            FROM country_master
            WHERE country_name = %s
            LIMIT 1
            """,
            (country_name,),
        )
        row = cur.fetchone()
    finally:
        try:
            cur.close()
        except Exception:
            pass

    code = ((row or {}).get("country_code") or "").strip().upper()
    if not code:
        return "NA"

    code = re.sub(r"[^A-Z0-9]", "", code)
    return code or "NA"


def fetch_states_by_country(conn, country_name):
    country_name = (country_name or "").strip()
    if not country_name:
        return []

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT DISTINCT state_name
            FROM state_master
            WHERE country_name = %s
            ORDER BY state_name ASC
            """,
            (country_name,),
        )
        rows = cur.fetchall() or []
        return [r["state_name"] for r in rows]
    finally:
        try:
            cur.close()
        except Exception:
            pass


def fetch_cities_by_country(conn, country_name):
    country_name = (country_name or "").strip()
    if not country_name:
        return []

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT DISTINCT city_name
            FROM city_master
            WHERE country_name = %s
            ORDER BY city_name ASC
            """,
            (country_name,),
        )
        rows = cur.fetchall() or []
        return [r["city_name"] for r in rows]
    finally:
        try:
            cur.close()
        except Exception:
            pass


def fetch_place_of_supply_by_country(conn, country_name):
    country_name = (country_name or "").strip()
    if not country_name:
        return []

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT DISTINCT state_code, state_name
            FROM state_master
            WHERE country_name = %s
            ORDER BY CAST(state_code AS UNSIGNED) ASC, state_name ASC
            """,
            (country_name,),
        )
        rows = cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass

    result = []
    for r in rows:
        code = str(r.get("state_code", "")).strip()
        name = str(r.get("state_name", "")).strip()
        result.append({
            "state_code": code,
            "state_name": name,
            "display": f"{code} - {name}"
        })

    return result


def fetch_ivacs(conn):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT DISTINCT ivac_centre
            FROM ivac_master
            WHERE ivac_centre IS NOT NULL
              AND TRIM(ivac_centre) <> ''
            ORDER BY ivac_centre ASC
            """
        )
        rows = cur.fetchall() or []
        return [(r.get("ivac_centre") or "").strip() for r in rows if (r.get("ivac_centre") or "").strip()]
    finally:
        try:
            cur.close()
        except Exception:
            pass


def fetch_hospitals(conn):
    return fetch_partners_by_type(conn, "Hospital")


def fetch_hospitals_with_city(conn):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT short_name, partner_city
            FROM partner_reg
            WHERE partner_type = %s
              AND short_name IS NOT NULL
              AND TRIM(short_name) <> ''
            ORDER BY short_name ASC
            """,
            ("Hospital",),
        )
        rows = cur.fetchall() or []
        out = []
        for row in rows:
            short_name = (row.get("short_name") or "").strip()
            if short_name:
                out.append({
                    "short_name": short_name,
                    "partner_city": (row.get("partner_city") or "").strip(),
                })
        return out
    finally:
        try:
            cur.close()
        except Exception:
            pass


def fetch_agents(conn):
    return fetch_partners_by_type(conn, "Agency")


def fetch_partners_by_type(conn, partner_type):
    partner_type = (partner_type or "").strip()
    if not partner_type:
        return []

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT DISTINCT short_name
            FROM partner_reg
            WHERE partner_type = %s
              AND short_name IS NOT NULL
              AND TRIM(short_name) <> ''
            ORDER BY short_name ASC
            """,
            (partner_type,),
        )
        rows = cur.fetchall() or []
        return [str(r["short_name"]).strip() for r in rows if r.get("short_name")]
    finally:
        try:
            cur.close()
        except Exception:
            pass


def fetch_departments(conn):
    """
    Return the canonical active Department LOV from department_master.

    department_master is the canonical Department source.
    hospdept_master remains the Hospital-to-Department mapping source.
    """
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT DISTINCT department_name
            FROM department_master
            WHERE department_name IS NOT NULL
              AND TRIM(department_name) <> ''
              AND UPPER(TRIM(department_status)) = 'ACTIVE'
            ORDER BY department_name ASC
            """
        )
        rows = cur.fetchall() or []
        return [
            (row.get("department_name") or "").strip()
            for row in rows
            if (row.get("department_name") or "").strip()
        ]
    finally:
        try:
            cur.close()
        except Exception:
            pass


def fetch_hospital_depts(conn, hospital_short_name):
    hospital_short_name = (hospital_short_name or "").strip()
    if not hospital_short_name:
        return []

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT hospital_dept
            FROM hospdept_master
            WHERE partner_shrtnm = %s
            ORDER BY hospital_dept ASC
            """,
            (hospital_short_name,),
        )
        rows = cur.fetchall() or []
    finally:
        try:
            cur.close()
        except Exception:
            pass

    out = []
    for r in rows:
        dept = (r.get("hospital_dept") or "").strip()
        if dept:
            out.append(dept)
    return out


def fetch_all_hospital_depts(conn):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT DISTINCT hospital_dept
            FROM hospdept_master
            WHERE hospital_dept IS NOT NULL
              AND TRIM(hospital_dept) <> ''
            ORDER BY hospital_dept ASC
            """
        )
        rows = cur.fetchall() or []
        out = []
        for row in rows:
            dept = (row.get("hospital_dept") or "").strip()
            if dept:
                out.append(dept)
        return out
    finally:
        try:
            cur.close()
        except Exception:
            pass


def fetch_hospital_city(conn, hospital_short_name):
    hospital_short_name = (hospital_short_name or "").strip()
    if not hospital_short_name:
        return ""

    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT partner_city
            FROM partner_reg
            WHERE partner_type = %s
              AND short_name = %s
            LIMIT 1
            """,
            ("Hospital", hospital_short_name),
        )
        row = cur.fetchone()
        return (row.get("partner_city") if row else "") or ""
    finally:
        try:
            cur.close()
        except Exception:
            pass


def fetch_active_users(conn):
    cur = conn.cursor(dictionary=True)
    try:
        cur.execute(
            """
            SELECT user_code, user_name
            FROM user_master
            WHERE user_code IS NOT NULL
              AND TRIM(user_code) <> ''
            ORDER BY user_name ASC, user_code ASC
            """
        )
        rows = cur.fetchall() or []
        users = []
        for row in rows:
            user_code = (row.get("user_code") or "").strip()
            user_name = (row.get("user_name") or "").strip()
            if user_code:
                users.append({
                    "user_code": user_code,
                    "user_name": user_name or user_code,
                })
        return users
    finally:
        try:
            cur.close()
        except Exception:
            pass
