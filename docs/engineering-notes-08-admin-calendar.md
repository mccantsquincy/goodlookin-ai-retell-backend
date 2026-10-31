# Engineering Notes #08 — Staging calendar admin console

## Goal

Launch the **first fictional roofing receptionist calendar** without executing a Python script locally. The existing version-controlled `summit-roofing-demo.json` stays `approved:false`; an authorized, reviewed browser action supplies one-time approval.

## What is implemented

- `GET /admin/calendar`: staging-only browser preview, separated from the read-only `/demo` dashboard
- `POST /admin/calendar/provision`: explicit confirmation, only for our checked-in fictional Summit Roofing draft
- HTTP Basic authentication with fixed username `provisioner`, and a **separate, unique 32+-character** `PROVISIONING_ADMIN_PASSWORD`
- Defense-in-depth: no admin routes in production, hidden when not configured, HTTPS origin enforcement in staging, HMAC-bound draft approval token, exact approval phrase, max request size, no-store HTML, frame blocking, and escaped output
- Optional injected Cal.com mock client for full HTTP tests. No provider calls during preview.
- Reuses the audited PR #07 backend `CalComProvisioner` (schedule discovery, event type discovery, idempotent create, no automatic retry after ambiguous failures)
- Never activates `CALENDAR_PROVIDER=calcom`, sends booking invites, or changes Retell functions

## Deployment: stage and review

1. Merge this PR after green CI.
2. Add **only** to Railway staging API: `PROVISIONING_ADMIN_PASSWORD` = a unique random 32+ character secret; do not share it with the worker. Railway already references your `CALCOM_PROVISIONING_API_KEY` and has `CALCOM_SCHEDULES_API_VERSION` / `CALCOM_EVENT_TYPES_API_VERSION`.
3. Deploy staging API. Visit `https://api-staging-dce8.up.railway.app/admin/calendar`.
4. Browser Basic Auth: username `provisioner`, password from the Railway secret you just configured.
5. Review the displayed fictional company, 60-minute inspection, Arizona timezone, Monday–Friday 09:00–17:00 and **Goodlookin Demo** destination reference.
6. Tick approval and type `PROVISION summit-roofing-demo` exactly. Press **Create or verify Cal.com resources**.
7. Inspect the returned `schedule_id` and `event_type_id`, then independently verify the event type inside Cal.com. If error/timeout, inspect Cal.com **before trying again** because an earlier create may have succeeded.

**Important:** The Google destination reference is informational, not a verified link. Select and verify the existing **Goodlookin Demo** Google Calendar as the event destination in Cal.com; mere presence in Google Calendar is not enough. `bookings_enabled=false` is intentional.

## Security assumptions / limits

This is explicitly a **single-demo staging** console. It handles only an allowlisted, checked-in fixture; no arbitrary instance key, external URL, HTTP proxy, or customer-supplied API key can reach provisioning. The separate admin password must be strong and rotated if exposed. Keep it out of chat and GitHub.

The in-process request lock prevents duplicate requests within one API worker, but is **not a distributed lock across replicas**. Do not scale the staging API above one replica while using this demo-only write action. Full multi-client launch will need database locking, tenant authorization, immutable approval/audit records, cross-tenant tests, per-client OAuth and scoped credentials.

## Engineering checkpoint

Why is it unsafe to use the existing read-only dashboard password for a route that can create Cal.com resources? Because it upgrades a previously read-only permission to a write capability; the provisioning role needs separate authorization.

## Next verification

After successful creation, wire the numeric ID into `CALCOM_EVENT_TYPE_ID`, use a demo-only booking key in `CALCOM_API_KEY`, confirm Google destination, then enable the fictional booking flow **only after** an actual end-to-end Retell test. New calendar event types are NOT automatically fully functional customer agents.

## Origin validation fix (PR #9)

A staging submission returned `{"detail":"Origin not allowed"}` before the Cal.com adapter was invoked. HTTP logs confirmed `GET /admin/calendar` succeeded while `POST /admin/calendar/provision` returned 403.

The original check required an `Origin` header on **every** browser form POST. Some browser/privacy/proxy combinations omit it. The corrected policy:

- If `Origin` is provided, it **must exactly match** the same HTTPS host (or HTTP in isolated tests).
- Explicitly foreign, `null`, cross-site and same-site requests remain rejected.
- An omitted `Origin` is accepted **only if** authentication, the password-bound HMAC approval token, draft fingerprint, checkbox and exact typed phrase are all valid; this retains the application's CSRF protection.
- No secret values or caller data are logged; no scheduling resources are created by this fix.
- Tests cover absent Origin, allowed same-origin Fetch Metadata, rejected foreign/null/cross-site, and replay-safe Cal.com resource discovery.

This does not create a public provisioning API or relax the dedicated administrator password requirement.

**Reverse proxy note:** Staging sets `PROVISIONING_ADMIN_ORIGIN=https://api-staging-dce8.up.railway.app` to compare browser Origin against the configured public domain rather than potentially internal proxy Host headers. An explicit foreign Origin is always rejected.

## PR #10 — Session-cookie CSRF fix (supersedes Origin-header checks)

A second live 403 after PR #9 confirmed our staging form was still blocked by browser/proxy header validation. We replaced header-dependent allow/deny rules with a **first-party, time-limited, signed approval session**.

- `GET /admin/calendar` after Basic authentication issues a random 256-bit nonce in an `HttpOnly; Secure; SameSite=Strict` cookie (Secure in staging HTTPS, test mode omits Secure for the isolated HTTP test client). The cookie expires after 15 minutes.
- The hidden form token is HMAC-SHA256 over the Git-reviewed draft hash, instance key and session nonce using the separate admin password.
- The POST must present that exact first-party cookie and valid HMAC, checked again against the server timestamp and all existing explicit approvals.
- Browser Origin and Fetch Metadata headers are no longer used as sole proof of origin because some privacy modes and reverse proxies alter them. **Cross-site browser POSTs cannot carry the Strict cookie**, and copied form fields without the cookie cannot create Cal.com resources.
- The hardcoded `PROVISIONING_ADMIN_ORIGIN` Railway variable is no longer needed by this route. It may remain until the deployment is verified, then can be removed. All role/authorization protections remain staging-only.
- This is intentionally a one-tenant demo workflow, not a multi-tenant authorization solution.

After merging, open the admin page again (fresh GET) to receive a new session cookie and try the reviewed operation. If browser cookie restrictions block it, the error is now explicitly `Approval session missing or expired`, not an ambiguous origin check.
