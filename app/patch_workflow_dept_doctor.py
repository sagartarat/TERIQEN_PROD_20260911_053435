from pathlib import Path

helper_file = Path("app/workflow/helpers.py")
template_file = Path("app/templates/wrkflwstatuschange.html")


# ============================================================
# 1. HELPERS.PY
# ============================================================

text = helper_file.read_text(encoding="utf-8")

# Add hospital_dept to BOTH patient_wrkflw SELECTs used by this screen.
old = '''                hospital_shrtnm,
                hospital_city,
                wrkflw_status,'''

new = '''                hospital_shrtnm,
                hospital_city,
                hospital_dept,
                wrkflw_status,'''

count = text.count(old)

if count < 2:
    raise RuntimeError(
        f"Expected at least 2 workflow SELECT blocks, found {count}."
    )

text = text.replace(old, new, 2)


# Add department to the existing Lead view model.
old = '''        "hospital_name": clean_keep_empty(row.get("hospital_shrtnm")),
        "city": clean_keep_empty(row.get("hospital_city")),
        "wrkflw_status": clean_keep_empty(row.get("wrkflw_status")),
        "doctor_name": clean_keep_empty(row.get("doctor_nm")),'''

new = '''        "hospital_name": clean_keep_empty(row.get("hospital_shrtnm")),
        "city": clean_keep_empty(row.get("hospital_city")),
        "hospital_department": clean_keep_empty(row.get("hospital_dept")),
        "wrkflw_status": clean_keep_empty(row.get("wrkflw_status")),
        "doctor_name": clean_keep_empty(row.get("doctor_nm")),'''

if old not in text:
    raise RuntimeError(
        "Could not locate build_lead_view_model hospital/doctor block."
    )

text = text.replace(old, new, 1)

helper_file.write_text(text, encoding="utf-8")


# ============================================================
# 2. WORKFLOW STATUS CHANGE TEMPLATE
# ============================================================

text = template_file.read_text(encoding="utf-8")

old = '''        <div class="wf-field">
          <label>DOB</label>
          <input class="wf-input" value="{{ patient.dob or '' }}" readonly>
        </div>
      </div>
    </div>
  </section>'''

new = '''        <div class="wf-field">
          <label>DOB</label>
          <input class="wf-input" value="{{ patient.dob or '' }}" readonly>
        </div>
      </div>

      <div style="height:14px;"></div>

      <div class="wf-grid-4">
        <div class="wf-field">
          <label>Hospital Department</label>
          <input
            class="wf-input"
            value="{{ lead.hospital_department or '-' }}"
            readonly
          >
        </div>

        <div class="wf-field">
          <label>Doctor Name</label>
          <input
            class="wf-input"
            value="{{ lead.doctor_name or '-' }}"
            readonly
          >
        </div>
      </div>
    </div>
  </section>'''

if old not in text:
    raise RuntimeError(
        "Could not locate Patient Details insertion point."
    )

text = text.replace(old, new, 1)

template_file.write_text(text, encoding="utf-8")

print("WORKFLOW DEPARTMENT / DOCTOR PATCH APPLIED SUCCESSFULLY")
