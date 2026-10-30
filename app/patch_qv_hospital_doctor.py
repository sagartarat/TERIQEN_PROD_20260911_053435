from pathlib import Path

path = Path("app/templates/wrkflwstatuschange.html")
text = path.read_text(encoding="utf-8")

old = '''          <div class="wf-qv-item"><div class="cap">DOB</div><div class="val">{{ patient.dob or '' }}</div></div>'''

new = '''          <div class="wf-qv-item"><div class="cap">DOB</div><div class="val">{{ patient.dob or '' }}</div></div>
          <div class="wf-qv-item"><div class="cap">Hospital Name</div><div class="val">{{ lead.hospital_name or '-' }}</div></div>
          <div class="wf-qv-item"><div class="cap">Doctor Name</div><div class="val">{{ lead.doctor_name or '-' }}</div></div>'''

if old not in text:
    raise RuntimeError("Could not locate Quick View Patient DOB field.")

text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")

print("QUICK VIEW HOSPITAL / DOCTOR PATCH APPLIED SUCCESSFULLY")
