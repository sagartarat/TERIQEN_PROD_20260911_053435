# TERIQEN Branding & Production Readiness V1.0

Release date: 2026-09-01

## Brand standard
- Product/platform name: **TERIQEN**
- Company/legal brand: **Cadupius Healthassist Pvt. Ltd.**
- Footer: **© 2026 Cadupius Healthassist Pvt. Ltd. · Powered by TERIQEN**
- Logo asset: `app/static/images/cadupius_healthassist_logo.png`

## Included
- Replaced user-visible previous-product branding with TERIQEN across templates, page titles, messages, email/reset content, broadcast screens, monitoring, and supporting UI strings.
- Added the supplied Cadupius Healthassist logo to the central application chrome and standalone authentication/privacy/broadcast/email pages.
- Centralized product/company/tagline/logo values through Flask brand configuration/context variables.
- Rebranded browser logout/session synchronization keys to TERIQEN.
- Rebranded Linux service, scheduler, Nginx, runtime socket, and deployment path names to TERIQEN.
- Added compatibility handling for previously generated unsubscribe links and old WhatsApp header-preview metadata.
- Strengthened the production environment example so production mode requires secure session/proxy/password behavior.

## Intentionally retained internal compatibility identifiers
The populated MySQL schema name `healtheasy` is retained to avoid an unsafe implicit database migration. Legacy unsubscribe signing salt/header metadata/environment aliases are read-only compatibility fallbacks so already-issued links and saved templates continue to work. The old weak verify-token string remains only in the security deny-list so it continues to be rejected.

These identifiers are not shown as TERIQEN product branding in the user interface. A database-schema rename, if ever desired, should be handled as a separate backed-up migration with full regression testing.
