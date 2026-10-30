from pathlib import Path

path = Path("app/templates/wrkflwstatuschange.html")
text = path.read_text(encoding="utf-8")

old = '''          <div class="wf-qv-item"><div class="cap">Hospital Name</div><div class="val">{{ lead.hospital_name or '-' }}</div></div>
          <div class="wf-qv-item"><div class="cap">Doctor Name</div><div class="val">{{ lead.doctor_name or '-' }}</div></div>'''

new = '''          <div class="wf-qv-item"><div class="cap">Hospital Name</div><div class="val">{{ lead.hospital_name or '-' }}</div></div>
          <div class="wf-qv-item"><div class="cap">Hospital Department</div><div class="val">{{ lead.hospital_department or '-' }}</div></div>
          <div class="wf-qv-item"><div class="cap">Doctor Name</div><div class="val">{{ lead.doctor_name or '-' }}</div></div>'''

if old not in text:
    raise RuntimeError(
        "Could not locate Quick View Hospital / Doctor fields."
    )

text = text.replace(old, new, 1)

path.write_text(text, encoding="utf-8")

print("QUICK VIEW HOSPITAL DEPARTMENT ADDED SUCCESSFULLY")
