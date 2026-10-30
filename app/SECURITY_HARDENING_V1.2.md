# TERIQEN Security Hardening V1.2

Implemented before production use:
- Fixed-host password reset URLs and Flask TRUSTED_HOSTS allow-list.
- Mandatory email OTP MFA in production (MFA_MODE=ALL by default).
- Server-side RBAC for Email Template deletion.
- Stored-XSS sanitation for legacy Email Template Builder HTML.
- ClamAV fail-closed malware scanning for uploaded documents.
- CSP and additional browser security headers.
- Sensitive reset/privacy URL token redaction in application logs and Nginx access-log suppression.
- HTTPS-only Nginx with unknown-host rejection and TLS 1.2/1.3.
- Dedicated non-login `teriqen` systemd user and stronger service sandboxing.
- Fail2ban, unattended-upgrades and ClamAV packages added to server bootstrap.

## Required production values
Set SECRET_KEY and AUDIT_HASH_KEY to independent random values >= 32 chars; set DB credentials, mail/Graph credentials, TERIQEN_PUBLIC_BASE_URL and TRUSTED_HOSTS. Keep MFA_ENABLED, MALWARE_SCAN_ENABLED and MALWARE_SCAN_FAIL_CLOSED true.

## Deployment gate
Run `nginx -t`, verify `clamdscan --version`, restart ClamAV, TERIQEN and scheduler, then execute authenticated security regression tests. Do not store live patient data until these checks pass.
