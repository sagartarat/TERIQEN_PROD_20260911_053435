# TERIQEN — Production Deployment

This package is sanitized for production deployment. It intentionally does **not** contain `.env`, passwords, tokens, a local virtual environment, Git history, runtime logs, or runtime patient/upload data.

## Branding
- Product/platform: **TERIQEN**
- Company: **Cadupius Healthassist Pvt. Ltd.**
- Application footer: **© 2026 Cadupius Healthassist Pvt. Ltd. · Powered by TERIQEN**
- Cadupius Healthassist logo: `app/static/images/cadupius_healthassist_logo.png`

## Target layout
- Application: `/var/www/teriqen`
- Python venv: `/var/www/teriqen/.venv`
- Public URL: `https://app.cadupiushealthassist.com`
- Web process: Gunicorn under systemd (`teriqen.service`)
- Background jobs: separate systemd scheduler process (`teriqen-scheduler.service`)
- Reverse proxy/TLS: Nginx + Certbot

## Installation sequence
1. Copy this folder to `/var/www/teriqen`.
2. Create `.venv` with the server Python and install `requirements.txt`.
3. Run `bash deploy/install_server_packages.sh` to install Nginx, Certbot, Python venv support, MySQL client, and the Linux libraries required by WeasyPrint.
4. Copy `.env.production.example` to `.env`, replace every blank secret/value required by your environment, and run `chmod 600 .env`.
5. Create a dedicated MySQL application account using `deploy/create_mysql_user.sql` (do not use MySQL root).
6. Back up and import the existing application database before first production start. The legacy schema name `healtheasy` is retained intentionally to avoid a destructive database migration; this is an internal database identifier, not application branding.
7. Create writable folders: `logs`, `instance/uploads`, and `app/static/uploads/whatsapp_docs`; ownership should be the deployment user and web group (for example `deploy:www-data`).
8. Install `deploy/teriqen.service` and `deploy/teriqen-scheduler.service` into `/etc/systemd/system/`.
9. Install `deploy/nginx-teriqen.conf` as `/etc/nginx/sites-available/teriqen`, enable it, and remove/disable any obsolete site entry for this application.
10. Start the app, verify `/healthz` and `/readyz`, then issue SSL with Certbot for `app.cadupiushealthassist.com`.
11. Only after Meta configuration is complete, populate fresh WhatsApp secrets and set `WHATSAPP_ENABLED=True`.

## Validation commands
```bash
sudo nginx -t
sudo systemctl daemon-reload
sudo systemctl enable --now teriqen
sudo systemctl enable --now teriqen-scheduler
sudo systemctl status teriqen --no-pager
sudo systemctl status teriqen-scheduler --no-pager
curl -I http://127.0.0.1/healthz
curl -I https://app.cadupiushealthassist.com/healthz
```

## Production security controls
- Live secrets are excluded from the production ZIP.
- Flask debug defaults to off.
- Production startup rejects a weak/missing secret key, root DB user, and missing DB password.
- Secure/HttpOnly/SameSite session cookies and proxy-aware HTTPS URL generation are configured.
- CSRF protection is enabled for application forms and same-origin API writes; the Meta webhook is exempted as required for webhook delivery.
- Uploaded WhatsApp documents under `/static/uploads/` require an authenticated session.
- Alert Management and System Monitoring require login and corresponding screen access.
- Legacy internal WhatsApp webhook access requires login.
- Meta webhook uses environment-based verification token and production HMAC signature validation.
- WhatsApp payload debug printing is disabled by default.
- Plaintext legacy user passwords are upgraded to password hashes on successful login.
- Password-reset raw tokens are not stored in the reset-token table.
- Scheduler runs separately from Gunicorn workers to prevent duplicate background jobs.
- Health/readiness endpoints are available for deployment checks.

## Before go-live
Set strong, unique values for `SECRET_KEY`, `AUDIT_HASH_KEY`, database credentials, mail/Graph credentials, WhatsApp credentials, and `BROADCAST_UNSUBSCRIBE_SECRET`. Keep `.env` server-only with restrictive permissions. Verify backup/restore procedures before deployment.
