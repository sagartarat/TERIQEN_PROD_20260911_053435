import datetime
from flask import request


def clean(v):
    if v is None:
        return None
    if isinstance(v, datetime.datetime):
        return v
    if isinstance(v, datetime.date):
        return v
    v = str(v).strip()
    return v if v != "" else None


def clean_keep_empty(v):
    if v is None:
        return ""
    if isinstance(v, datetime.datetime):
        return v.date().isoformat()
    if isinstance(v, datetime.date):
        return v.isoformat()
    return str(v).strip()


def parse_date(v):
    if v is None or v == "":
        return None

    if isinstance(v, datetime.datetime):
        return v.date()

    if isinstance(v, datetime.date):
        return v

    v = str(v).strip()
    if not v:
        return None

    for fmt in ("%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%Y/%m/%d"):
        try:
            return datetime.datetime.strptime(v, fmt).date()
        except Exception:
            pass
    return None


def date_to_db(v):
    d = parse_date(v)
    return d if d else None


def calc_age_from_dob(dob_value):
    dob = parse_date(dob_value)
    if not dob:
        return None

    today = datetime.date.today()
    age = today.year - dob.year - ((today.month, today.day) < (dob.month, dob.day))
    return age if age >= 0 else None


def _iso(d):
    if d is None:
        return ""
    if isinstance(d, (datetime.datetime, datetime.date)):
        return d.date().isoformat() if isinstance(d, datetime.datetime) else d.isoformat()
    return str(d)


def first_form_value(*names, default=None):
    for name in names:
        if not name:
            continue
        val = request.form.get(name)
        if val is not None:
            return val
    return default


def first_form_list(*names):
    for name in names:
        if not name:
            continue
        vals = request.form.getlist(name)
        if vals:
            vals = [x.strip() for x in vals if (x or "").strip()]
            if vals:
                return vals
    return []