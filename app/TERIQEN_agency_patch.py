"""Agency dropdown patch for the specified CADUPIUS production tree only."""
from pathlib import Path
import sys, re, shutil, tempfile, subprocess
from datetime import datetime

EXPECTED = 'TERIQEN_PROD_20260911_053435'
root = Path.cwd().resolve()
if root.name.lower() != 'app' or root.parent.name != EXPECTED:
    raise SystemExit(f'ERROR: run from ...\\{EXPECTED}\\app, not {root}')
files = {
 'oq_html':'app/templates/operationqueue.html',
 'oq_js':'app/static/js/operationqueue.js',
 'mr_html':'app/templates/manual_review.html',
 'mr_js':'app/static/js/pages/manual_review.js',
 'routes':'app/queue_management/routes.py',
 'service':'app/queue_management/operation_queue_service.py',
}
texts={}
newlines={}
for key, rel in files.items():
    p=root/rel
    raw=p.read_bytes()
    if raw.startswith((b'\xff\xfe',b'\xfe\xff')):
        raise SystemExit(f'Unsupported source encoding: {rel}')
    decoded=raw.decode('utf-8-sig')
    newlines[key]='\r\n' if b'\r\n' in raw else '\n'
    texts[key]=decoded.replace('\r\n','\n')

def change(key, old, new, n=1):
    count=texts[key].count(old)
    if count != n:
        raise RuntimeError(f'Anchor mismatch: {files[key]}, expected {n}, got {count}: {old[:100]!r}. No changes written.')
    texts[key]=texts[key].replace(old,new,n)

# One authoritative LOV and validator, both using partner_reg.
anchor='def _get_hospital_department_lov():'
insert='''def _get_agency_lov():
    conn = None
    cursor = None
    try:
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)
        cursor.execute("""
            SELECT DISTINCT short_name
            FROM partner_reg
            WHERE UPPER(TRIM(partner_type)) = 'AGENCY'
              AND short_name IS NOT NULL
              AND TRIM(short_name) <> ''
            ORDER BY short_name
        """)
        return cursor.fetchall() or []
    except Exception as exc:
        print("AGENCY_LOV_LOAD_ERROR:", str(exc), flush=True)
        raise
    finally:
        if cursor:
            cursor.close()
        if conn:
            conn.close()


def _canonical_agency(cursor, supplied):
    agency = _safe_str(supplied)
    if not agency:
        return None
    cursor.execute("""
        SELECT short_name FROM partner_reg
        WHERE UPPER(TRIM(partner_type)) = 'AGENCY'
          AND UPPER(TRIM(short_name)) = UPPER(TRIM(%s))
        LIMIT 1
    """, (agency,))
    row = cursor.fetchone()
    return _safe_str(row.get('short_name')) if row else None


'''
change('routes',anchor,insert+anchor)
# Both queue screens receive same LOV.
change('routes','        ivac_lov = _get_ivac_lov()\n        department_lov = _get_department_lov()\n        users_lov = _get_user_lov()\n        email_templates_lov = get_active_email_templates()', '        ivac_lov = _get_ivac_lov()\n        department_lov = _get_department_lov()\n        users_lov = _get_user_lov()\n        agency_lov = _get_agency_lov()\n        email_templates_lov = get_active_email_templates()',2)
change('routes','            department_lov=department_lov,\n            users_lov=users_lov,\n            request_for_lov=request_for_lov,','            department_lov=department_lov,\n            users_lov=users_lov,\n            agency_lov=agency_lov,\n            request_for_lov=request_for_lov,',2)
change('routes','            hospital_department_lov=hospital_department_lov,\n            users_lov=users_lov,\n            request_for_lov=request_for_lov,','            hospital_department_lov=hospital_department_lov,\n            users_lov=users_lov,\n            agency_lov=agency_lov,\n            request_for_lov=request_for_lov,')
change('routes','        hospital_department_lov = _get_hospital_department_lov()\n        users_lov = _get_user_lov()\n        email_templates_lov = get_active_email_templates()', '        hospital_department_lov = _get_hospital_department_lov()\n        users_lov = _get_user_lov()\n        agency_lov = _get_agency_lov()\n        email_templates_lov = get_active_email_templates()')
# Operation Queue payload and canonical validation, after the CLOSED check on existing source.
change('routes','        "assigned_to": _safe_str(data.get("assigned_to")),\n        "parse_remarks":', '        "assigned_to": _safe_str(data.get("assigned_to")),\n        "agent_shrtnm": _safe_str(data.get("agent_shrtnm")),\n        "parse_remarks":')
change('service','    payload["assigned_to"] = _safe_str(updates.get("assigned_to")) or payload.get("assigned_to")', '    payload["agent_shrtnm"] = _safe_str(updates.get("agent_shrtnm")) or payload.get("agent_shrtnm")\n    payload["assigned_to"] = _safe_str(updates.get("assigned_to")) or payload.get("assigned_to")')
change('service','''        if source_status == "CLOSED":
            return {"success": False, "message": "Closed records cannot be modified."}
''','''        if source_status == "CLOSED":
            return {"success": False, "message": "Closed records cannot be modified."}

        agency_candidate = _safe_str(payload.get("agent_shrtnm"))
        if agency_candidate:
            cursor.execute("""
                SELECT short_name FROM partner_reg
                WHERE UPPER(TRIM(partner_type)) = 'AGENCY'
                  AND UPPER(TRIM(short_name)) = UPPER(TRIM(%s))
                LIMIT 1
            """, (agency_candidate,))
            agency_row = cursor.fetchone()
            if not agency_row:
                return {"success": False, "message": "Invalid Agency. Select an Agency from Partner Master."}
            payload["agent_shrtnm"] = agency_row["short_name"]
''')
change('oq_html','<input type="text" id="detail_agent_shrtnm" readonly />','''<select id="detail_agent_shrtnm">
                  <option value="">Select Agency</option>
                  {% for agency_row in agency_lov %}
                  <option value="{{ agency_row.short_name }}">{{ agency_row.short_name }}</option>
                  {% endfor %}
                </select>''')
change('oq_js','''      "detail_assigned_at",
      "detail_agent_shrtnm"
''','''      "detail_assigned_at"
''')
change('oq_js','''if(el.id === "detail_wrkflw_status" || el.id === "detail_assigned_at" || el.id === "detail_agent_shrtnm")''','''if(el.id === "detail_wrkflw_status" || el.id === "detail_assigned_at")''')
change('oq_js','''    const saveBtn = document.getElementById("opqSaveBtn");''','''    const agencySelect = document.getElementById("detail_agent_shrtnm");
    if(agencySelect){
      agencySelect.disabled = isClosed;
      agencySelect.classList.toggle("opq-force-readonly", isClosed);
    }

    const saveBtn = document.getElementById("opqSaveBtn");''')
change('oq_js','setInputValue("detail_agent_shrtnm", row.agent_shrtnm);','setSelectValue("detail_agent_shrtnm", row.agent_shrtnm);')
change('oq_js','''      assigned_to: safeValue(document.getElementById("detail_assigned_to")?.value),
      parse_remarks:''','''      assigned_to: safeValue(document.getElementById("detail_assigned_to")?.value),
      agent_shrtnm: safeValue(document.getElementById("detail_agent_shrtnm")?.value),
      parse_remarks:''')
# Manual Review field: include selected agency in structured message (parser already knows label).
change('mr_html','''          <div class="mrq-detail-item">
            <label>Hospital Name</label>''','''          <div class="mrq-detail-item">
            <label>Agency</label>
            <select id="detail_agent_shrtnm">
              <option value="">Select Agency</option>
              {% for agency_row in agency_lov %}
              <option value="{{ agency_row.short_name }}">{{ agency_row.short_name }}</option>
              {% endfor %}
            </select>
          </div>
          <div class="mrq-detail-item">
            <label>Hospital Name</label>''')
change('mr_js','''    const selectedHospitalName = getFirstValue(row, ["possible_hospital", "hospital_name", "hospital_shrtnm"]);''','''    setInputValue("detail_agent_shrtnm", getFirstValue(row, ["agent_shrtnm", "possible_agency"]));
    const selectedHospitalName = getFirstValue(row, ["possible_hospital", "hospital_name", "hospital_shrtnm"]);''')
change('mr_js','''      hospital_name: document.getElementById("detail_hospital_name").value,''','''      agent_shrtnm: document.getElementById("detail_agent_shrtnm").value,
      hospital_name: document.getElementById("detail_hospital_name").value,''')
change('mr_js','''    const payload = collectManualReviewPayload();
''','''    const payload = collectManualReviewPayload();
    if(!payload.agent_shrtnm){
      alert("Please select an Agency from Partner Master.");
      document.getElementById("detail_agent_shrtnm").focus();
      return;
    }
''')
change('routes','''    ("Hospital / Request Details", [
        ("hospital_name", "Hospital Name"),''','''    ("Hospital / Request Details", [
        ("agent_shrtnm", "Agency"),
        ("hospital_name", "Hospital Name"),''')
change('routes','''                    IFNULL(oq.assigned_to, '') AS assigned_to,
''','''                    IFNULL(oq.assigned_to, '') AS assigned_to,
                    IFNULL(oq.agent_shrtnm, '') AS agent_shrtnm,
''')
# Protect Manual Review against stale/reprocessed submissions, and validate selected Agency in transaction.
start_marker = '    def manual_review_save_review():'
end_marker = '    @app.route("/manual-review/mark-junk/'
start = texts['routes'].index(start_marker)
end = texts['routes'].index(end_marker, start)
subsection = texts['routes'][start:end]
old_sql = """                    id,
                    parse_status
                FROM channel_message_log
                WHERE id = %s
                LIMIT 1"""
assert subsection.count(old_sql) == 1, 'Manual Review query anchor missing'
subsection = subsection.replace(old_sql, old_sql + "\n                FOR UPDATE", 1)
texts['routes'] = texts['routes'][:start] + subsection + texts['routes'][end:]
change('routes','''            merged_attachment_json, merged_attachment_count = (
''','''            if _safe_str(existing_row.get("parse_status")).upper() != "MANUAL_REVIEW":
                return jsonify({"success": False, "message": "This record is no longer pending Manual Review."}), 409

            canonical_agency = _canonical_agency(cursor, data.get("agent_shrtnm"))
            if not canonical_agency:
                return jsonify({"success": False, "message": "Select a valid Agency from Partner Master."}), 400
            data["agent_shrtnm"] = canonical_agency
            corrected_message_text = _build_manual_review_structured_text(data)

            merged_attachment_json, merged_attachment_count = (
''')
# Ensure no SQL schema migrations and no edits outside allowlist.
# Basic sanity before writing and backup.
for key in ('routes','service'):
    compile(texts[key],files[key],'exec')
if shutil.which('node'):
    for key in ('oq_js','mr_js'):
        with tempfile.NamedTemporaryFile(mode='w',encoding='utf-8',suffix='.js',delete=False) as f:
            f.write(texts[key]); tempname=f.name
        try:
            subprocess.run(['node','--check',tempname],check=True,capture_output=True,text=True)
        finally:
            Path(tempname).unlink(missing_ok=True)

stamp=datetime.now().strftime('%Y%m%d_%H%M%S')
backup=root / ('_agency_patch_backup_'+stamp)
backup.mkdir(exist_ok=False)
try:
    for key, rel in files.items():
        destination=backup/rel
        destination.parent.mkdir(parents=True,exist_ok=True)
        shutil.copy2(root/rel,destination)
    for key, rel in files.items():
        content=texts[key].replace('\n',newlines[key]).encode('utf-8')
        target=root/rel
        temporary=target.with_name(target.name+'.agency_patch_tmp')
        temporary.write_bytes(content)
        temporary.replace(target)
except Exception:
    for key, rel in files.items():
        original=backup/rel
        if original.exists(): shutil.copy2(original,root/rel)
    raise
print('SUCCESS: Agency patch applied to 6 files. Parser source unchanged.')
print('Backup:',backup)
print('Rollback command (PowerShell):')
print(f'''Get-ChildItem -Path "{backup / 'app'}" -Recurse -File | ForEach-Object {{ $relative = $_.FullName.Substring({len(str(backup))+1}); Copy-Item -LiteralPath $_.FullName -Destination (Join-Path (Get-Location) $relative) -Force }}''')
