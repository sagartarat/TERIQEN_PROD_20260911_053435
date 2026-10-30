# TERIQEN DPDP & Security Audit + Remediation — Phase 1 (V1.1)

**Assessment date:** 15 August 2026  
**Baseline:** `0691f897-1c5d-440c-aaf8-8b7204f01ed6.zip`  
**Release decision:** **NO-GO for live patient data until the residual blockers below are completed and verified in UAT/live infrastructure.**

## 1. Scope and method

This Phase 1 review covers source code, application configuration, authentication/session controls, authorization, report/API access, uploads/documents, WhatsApp media, client-side output encoding, template rendering, error/logging exposure, deletion audit design, and sample Nginx production configuration.

This was a **static source/configuration security review plus syntax/static validation**. It was not a live penetration test, DAST scan, cloud/VPS configuration audit, database privilege audit, or end-to-end production runtime test.

Security remediation is aligned to OWASP ASVS 5.0.x principles, especially trusted server-side access control, authentication/session controls, input/output validation, secure file handling, logging, and data protection.

## 2. Phase 1 remediation completed

| Area | Risk before remediation | Remediation implemented | Status |
|---|---|---|---|
| Report/API authorization | Critical | Added fresh database-backed RBAC enforcement for reports and operational APIs | REMEDIATED IN CODE |
| Privilege escalation / stale permissions | Critical | Server-side permission checks query current user status and `user_screen_access`; sensitive write operations use add/edit/delete permissions | REMEDIATED IN CODE |
| Inactive-user access | High | User loader/login/reset-token validation reject inactive users | REMEDIATED IN CODE |
| Login brute force | High | Added per-user/IP login throttling plus Nginx login rate limiting | REMEDIATED IN CODE |
| Legacy plaintext passwords | Critical/High | Disabled by default and production configuration fails closed if enabled | REMEDIATED IN CODE |
| Session security | High | Production secure/HttpOnly/SameSite cookies and 60-minute lifetime; production validation added | REMEDIATED IN CODE |
| Jinja template injection | Critical | Replaced direct `jinja2.Template` usage in dynamic email rendering with sandboxed Jinja environment | REMEDIATED IN CODE |
| Stored XSS (confirmed paths) | Critical | Added HTML escaping and safer URL handling in manual-review/report rendering paths | REMEDIATED IN CODE; BROADER REGRESSION REQUIRED |
| WhatsApp patient media exposure | Critical | New downloads are stored outside `/static`; authenticated private media endpoint with RBAC and traversal protection | REMEDIATED IN CODE |
| CSRF scope | High | Removed whole-WhatsApp-blueprint exemption; only Meta webhook route is exempt | REMEDIATED IN CODE |
| File upload validation | High | Added extension, size, signature/magic and OOXML ZIP validation with decompression-size guard | REMEDIATED IN CODE |
| Sensitive logs / debug payloads | High | Production fails if unsafe payload/SQL/WhatsApp logging flags are enabled; sensitive queue email debug prints removed; sensitive-key redaction expanded | PARTIALLY REMEDIATED; FULL APP LOG REVIEW REQUIRED |
| Error leakage | High | Added generic global error handlers and genericized sensitive report/queue error responses | PARTIALLY REMEDIATED; LEGACY ROUTES REMAIN |
| Patient deletion audit | DPDP/Privacy Critical | Replaced full-PII deletion archive pattern for new patient deletion audit with HMAC fingerprint + masked identifier + actor/time metadata | REMEDIATED FOR NEW AUDIT EVENTS |
| Security headers | Medium/High | Added HSTS in production, nosniff, frame protection, referrer policy, permissions policy and cache restrictions for sensitive responses | REMEDIATED IN CODE |
| Production fail-closed secrets/config | High | Requires strong app secret, audit HMAC key, DB password/non-root DB user, secure session/CSRF/proxy flags and WhatsApp secrets where enabled | REMEDIATED IN CODE |

## 3. DPDP controls still required before compliance sign-off

Phase 1 reduces technical security exposure but **does not constitute full DPDP compliance**. TERIQEN still needs the following functional/governance controls:

1. **Versioned privacy notice + consent evidence** — capture purpose, data categories, notice/policy version, affirmative action, timestamp, channel/source and proof of consent.
2. **Consent withdrawal workflow** — comparable ease to granting consent; stop consent-based processing subject to another lawful basis/retention obligation.
3. **Data Principal rights module** — access, correction/completion/updating, erasure and grievance/request tracking with identity verification, SLA, action history and closure evidence.
4. **Policy-aware erasure across dependencies** — patient delete currently cannot be treated as a complete privacy-erasure engine when dependent operational/financial/communication records exist. A data map and lawful-retention matrix must control deletion/anonymisation across all related tables/files/backups.
5. **Retention and disposal engine** — configurable retention classes, scheduled review/deletion/anonymisation, legal hold/exception handling and auditable execution. Retention should follow the approved business/legal policy rather than an indiscriminate hard-coded duration.
6. **Breach/incident register and response workflow** — incident discovery, containment, affected data/people, actions, approvals, notification decision and evidence.
7. **Processor/vendor register** — Meta/WhatsApp, hosting, email, Microsoft/other vendors, purpose, data shared, contractual/standard terms, security/privacy review, locations and exit/deletion controls.
8. **Historic deletion-audit cleanup decision** — legacy `patient_delete_audit` may contain full patient PII. Back it up and review legal/retention requirements before an approved purge; do not automatically destroy evidence.

## 4. Cybersecurity blockers still required before Production GO

1. Implement MFA for administrator and other privileged accounts.
2. Add malware scanning/quarantine for uploaded patient/partner/WhatsApp documents (for example ClamAV or equivalent) and define handling for rejected files.
3. Complete an application-wide stored-XSS/output-encoding regression, not only the confirmed vulnerable screens patched in Phase 1.
4. Complete legacy route review for caught exceptions that still expose `str(e)`/database detail to clients and remove unnecessary `print()` debugging across the application.
5. Validate Nginx TLS configuration, HTTPS-only behavior, firewall/UFW, SSH keys/root-login policy, OS patching, file permissions, service user permissions and secret-file permissions on the actual Hostinger VPS.
6. Validate database least-privilege accounts, remote-access restrictions, encryption/backup protection, backup restore and recovery procedures.
7. Run UAT security tests and a live authenticated/unauthenticated penetration/DAST test before real patient data is accepted.
8. Perform dependency vulnerability/SBOM scanning in the deployment pipeline and patch high/critical vulnerable dependencies before release.
9. Define CSP after legacy inline JavaScript is made compatible; current security-header set does not yet enforce a production Content-Security-Policy.

## 5. Database migration included

Run the included migration only after taking a database backup:

`deploy/dpdp_security_migration_v1_1.sql`

It creates `privacy_deletion_audit`, designed to record a privacy-preserving deletion event without retaining the complete deleted patient record.

**Do not automatically truncate the legacy `patient_delete_audit` table.** Any purge must follow approved retention/legal review and a verified backup.

## 6. Production configuration included

Review and populate:

- `.env.production.example`
- `deploy/nginx-teriqen.conf`

Generate unique high-entropy values for `SECRET_KEY` and `AUDIT_HASH_KEY`. Never reuse the example placeholders or commit production secrets to source control.

Before go-live, confirm the RBAC matrix in `user_screen_access` is correct for every role/user because the remediated server-side enforcement now actively relies on those permissions.

## 7. Validation performed on this remediation build

- Python source compilation: **PASS** (`python -m compileall -q app`)
- JavaScript syntax checks with Node: **PASS** for application JS files
- Static authorization-route review: operational routes are covered by login/RBAC/blueprint controls in the remediated design
- Targeted hardening assertions: RBAC registration, selective webhook CSRF exemption, private WhatsApp storage, sandboxed email templates, plaintext-password production block, privacy deletion audit, file-signature validation and confirmed XSS-path escaping: **PASS**

A complete Flask runtime smoke test was **not** executed in this analysis container because the application dependency/runtime/database environment was not provisioned here. This must be done in a clean UAT environment using `requirements.txt` and a non-production database copy/test dataset.

## 8. Recommended release sequence

1. Take code and database backups.
2. Deploy this build to UAT, not Production.
3. Apply `deploy/dpdp_security_migration_v1_1.sql` to UAT.
4. Configure production-style secrets/security flags in UAT.
5. Verify the complete user/role permission matrix.
6. Build Phase 2 DPDP controls: Notice → Consent Evidence → Withdrawal → Data Principal Requests → Retention/Erasure → Incident/Breach Register.
7. Add MFA and malware scanning, then complete residual XSS/error/logging review.
8. Perform functional regression, security test/DAST, database backup-restore test and actual VPS hardening review.
9. Remediate findings and repeat verification.
10. Issue Production GO only after the residual Critical/High items are closed or formally risk-accepted by accountable management.

## 9. Overall conclusion

**Phase 1 materially improves TERIQEN's security posture and removes several critical code-level weaknesses, but the application should still be treated as NO-GO for live patient/medical data until Phase 2 privacy controls and infrastructure/runtime security verification are completed.**
