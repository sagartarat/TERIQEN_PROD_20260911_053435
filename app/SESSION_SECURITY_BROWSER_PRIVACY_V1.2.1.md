# TERIQEN Session Security & Browser Privacy V1.2.1

## Purpose
This focused hardening release closes the remaining logout/session privacy gap found during local security testing.

## Changes
- Default rolling inactivity timeout reduced from 60 minutes to **5 minutes**.
- Explicit Logout now calls both `logout_user()` and `session.clear()` for immediate full session destruction.
- Added an authenticated `/session/heartbeat` endpoint. The browser calls it only while there is recent user activity, so users actively typing or working in a form are not logged out merely because no navigation request occurred.
- User activity is shared across TERIQEN tabs using BroadcastChannel/localStorage.
- After 5 minutes without activity, the browser initiates Logout and sibling TERIQEN windows receive the existing logout broadcast.
- Dynamic TERIQEN responses now send `Cache-Control: no-store, no-cache, must-revalidate, private, max-age=0`, `Pragma: no-cache`, and `Expires: 0`. Static CSS/JS/images and public broadcast media retain their cache policy.
- BFCache-restored authenticated pages are forcibly reloaded so Flask can revalidate the session.
- MFA verification is explicitly no-cache and also reloads if restored from BFCache.

## Expected behavior
1. Clicking Logout invalidates the session immediately.
2. A protected URL after Logout redirects to Login.
3. Another open TERIQEN tab is unusable after Logout on its next interaction/reload.
4. Five minutes of real user inactivity triggers Logout.
5. Active users remain signed in because recent activity permits a lightweight heartbeat.
6. Browser Back/Forward cannot restore a usable stale dashboard, patient page, broadcast screen, or MFA challenge.

## Production configuration
`.env.production.example` now contains:

```env
SESSION_LIFETIME_MINUTES=5
```

The code default is also 5 minutes, so the hardened behavior remains in force if the setting is accidentally omitted. Explicitly keeping the variable in production is recommended for auditability.

## Local development
This setting also works over local HTTP. Keep `SESSION_COOKIE_SECURE=False` locally and `True` behind HTTPS in production.
