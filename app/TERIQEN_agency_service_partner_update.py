"""Extend previously applied Agency dropdown patch to include Service Partner.
Run from the existing TERIQEN_PROD_20260911_053435/app directory.
"""
from pathlib import Path
from datetime import datetime
import shutil, subprocess, sys

root = Path.cwd().resolve()
if root.name.lower() != 'app' or root.parent.name != 'TERIQEN_PROD_20260911_053435':
    sys.exit('ERROR: Must run from TERIQEN_PROD_20260911_053435\\app.')
files = {
    'routes': root / 'app/queue_management/routes.py',
    'service': root / 'app/queue_management/operation_queue_service.py',
    'parser': root / 'app/queue_management/parser_engine.py',
}
originals = {k: p.read_bytes() for k,p in files.items()}
texts = {k: raw.decode('utf-8-sig').replace('\r\n','\n') for k,raw in originals.items()}

def replace_exact(key, old, new, expected=1):
    actual = texts[key].count(old)
    if actual != expected:
        raise RuntimeError(f'ABORT: {key} expected {expected} occurrence(s), found {actual} for {old[:90]!r}; no files changed.')
    texts[key] = texts[key].replace(old,new)

try:
    # Only modify the two Agency validators/lookup methods from the prior patch.
    replace_exact('routes', "WHERE UPPER(TRIM(partner_type)) = 'AGENCY'", "WHERE UPPER(TRIM(partner_type)) IN ('AGENCY', 'SERVICE PARTNER')", 2)
    replace_exact('service', "WHERE UPPER(TRIM(partner_type)) = 'AGENCY'\n                  AND UPPER(TRIM(short_name))", "WHERE UPPER(TRIM(partner_type)) IN ('AGENCY', 'SERVICE PARTNER')\n                  AND UPPER(TRIM(short_name))")
    # Manual Review's structured Agency field must validate Service Partners too.
    needle = '''            validated_agent = _lookup_partner_short_name_by_type(
                "Agency",
                cleaned_value,
            )'''
    expanded = '''            validated_agent = (
                _lookup_partner_short_name_by_type("Agency", cleaned_value)
                or _lookup_partner_short_name_by_type("Service Partner", cleaned_value)
            )'''
    replace_exact('parser', needle, expanded)
    # Also support a Service Partner in the parser's fallback from an explicit Agent Name.
    needle = '''        validated_agent = _lookup_partner_short_name_by_type(
            "Agency",
            agent_name,
        )'''
    expanded = '''        validated_agent = (
            _lookup_partner_short_name_by_type("Agency", agent_name)
            or _lookup_partner_short_name_by_type("Service Partner", agent_name)
        )'''
    replace_exact('parser', needle, expanded)
    # When an Agency and Service Partner share a short name, the name is ambiguous.
    # Current storage only retains short_name; avoid changing that schema in this patch.
    for key in texts:
        compile(texts[key], str(files[key]), 'exec')
except Exception as exc:
    sys.exit(str(exc))

backup = root / ('_agency_service_partner_backup_' + datetime.now().strftime('%Y%m%d_%H%M%S'))
backup.mkdir(exist_ok=False)
try:
    for key, path in files.items():
        relative = path.relative_to(root)
        dest = backup / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
    for key, path in files.items():
        raw = originals[key]
        newline = '\r\n' if b'\r\n' in raw else '\n'
        bom = b'\xef\xbb\xbf' if raw.startswith(b'\xef\xbb\xbf') else b''
        path.write_bytes(bom + texts[key].replace('\n',newline).encode('utf-8'))
except Exception as exc:
    for key, path in files.items():
        dest = backup / path.relative_to(root)
        if dest.exists():
            shutil.copy2(dest, path)
    sys.exit('ERROR: Writing failed; original files restored: ' + str(exc))
print('SUCCESS: Agency + Service Partner changes applied to 3 files.')
print('Backup:', backup)
print('Python syntax check: PASS')
print('Restart the sandbox app to load changes; test both partner types and CLOSED records.')
