# TERIQEN — Cadupius Healthassist Production Build V1.0

**TERIQEN** is the software/platform brand. **Cadupius Healthassist Pvt. Ltd.** is the company brand shown with the supplied Cadupius Healthassist logo.

Application footer standard:

> © 2026 Cadupius Healthassist Pvt. Ltd. · Powered by TERIQEN

The application is a modular Flask system prepared for Linux/Nginx/Gunicorn deployment. For production deployment, read `PRODUCTION_DEPLOYMENT.md` first. Copy `.env.production.example` to `.env` on the server and insert **newly rotated** secrets. Never commit or package `.env`.

Local development can use `.env.example`.

## Branding compatibility note

All user-visible previous-product branding has been replaced by TERIQEN. A small number of **legacy internal compatibility identifiers** are intentionally retained where changing them would invalidate existing data or signed links (for example, the existing MySQL schema name and legacy unsubscribe-token/header metadata fallbacks). They are not displayed as product branding in the application UI.
