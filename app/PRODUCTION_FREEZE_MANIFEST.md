# TERIQEN Production Freeze V1.0

Freeze date: 2026-09-02  
Freeze status: **Code/package frozen — database data cleanup pending separate approval**

## Source

- Source archive SHA-256: `6f3b07d9f35ab2e6343a84c07a36566a6331ffa49d4150edd7b67e1a988cd05e`
- The uploaded source archive was preserved unchanged.
- This release was created as a separate sanitized deployment package.

## Removed from the production package

| Category | Files removed | Reason |
|---|---:|---|
| Windows local virtual environment (`.venv`) | 9,301 | Platform-specific, reproducible from `requirements.txt`, and unsuitable for Linux deployment |
| Patch backups (`_patch_backups`) | 216 | Development history; not runtime code |
| Compiled Python caches outside the venv | 110 | Reproducible cache files |
| Runtime logs | 22 | May contain operational or personal data |
| Runtime instance data/uploads | 41 | Included repeated WhatsApp documents and local/test broadcast media |
| Local secret/development environment files | 3 | `.env`, its backup, and `.env.example` must not ship with live/local values |
| Database inventory workbook | 1 | Development audit artifact; not needed at runtime |
| Unused zero-byte stubs | 7 | Unreferenced empty files with no runtime function |

Total excluded: **9,702 files / 282,308,390 bytes** before ZIP compression.

Empty runtime directories are retained with `.gitkeep` files so deployment ownership and write permissions can be configured explicitly.

## Preserved intentionally

- Application source, templates, static assets, and permanent Cadupius branding
- `requirements.txt`
- Linux/Nginx/Gunicorn/systemd deployment files
- Sanitized `.env.production.example`
- Production/security/deployment documentation
- Database migration scripts

Preview/sample text embedded in template-builder JavaScript remains intentionally. It is UI preview content, not persisted patient or transaction data.

## Database boundary

No database dump or database file was present in the source archive. Therefore, this freeze does **not** claim that rows in the external MySQL database were deleted or reset.

Run `deploy/production_database_cleanup_precheck.sql` against the database first. It is read-only and lists counts plus likely dummy/test candidates. Review its output before authorizing a separate transaction-based cleanup.

Do not delete required master/configuration records such as countries, currencies, statuses, workflow rules, screen access, privacy notices, alert rules, templates, or scheduler job definitions merely because they pre-date production.

## Freeze rules

1. Treat this ZIP as the production release candidate.
2. Do not add features to this package.
3. Permit only production-blocking fixes, each with a new version and checksum.
4. Create a fresh server-side `.env` from `.env.production.example`; never restore the excluded `.env` files.
5. Use newly rotated secrets for production.
6. Keep the local scheduler off when the production scheduler is enabled.
7. Complete database cleanup, backup, deployment configuration, and smoke testing before go-live.

## Validation recorded for this release

- Archive path-safety check: passed
- Python syntax compilation: passed for 113 files
- Production-mode Flask application creation: passed with isolated validation settings
- `/healthz`: HTTP 200 with `{"status":"ok"}`
- Registered Flask routes: 176
- Session controls: Secure, HttpOnly, SameSite=Lax, 5-minute lifetime
- Embedded web scheduler during app test: disabled
- JavaScript syntax check: passed for 31 files
- Deployment shell syntax check: passed
- Secret-file exclusion: required
- Runtime log/upload exclusion: required
- Production template retained: `.env.production.example`
- Final file manifest/checksums: `SHA256SUMS.txt`
