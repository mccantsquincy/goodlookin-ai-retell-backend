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
