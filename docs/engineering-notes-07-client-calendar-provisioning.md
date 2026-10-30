# Engineering Notes #07 — Client calendar provisioning

## Goal

Goodlookin AI should **plan → review → provision → verify → activate** a calendar for each roofer. The same template should work for one fictional demo now and separately authorized client accounts later.

```text
Website / intake → AI-generated DRAFT facts + scheduling settings
         ↓
Human review of services, ZIPs, hours and calendar owner
         ↓
Approved ClientCalendarDraft  (no credentials)
         ↓
Management-only Cal.com provisioner  (specific account-scoped credential)
         ├── GET schedules → POST only when absent
         └── GET event-types → POST only when absent
         ↓
Event type ID + schedule ID persisted in an activation record (future)
         ↓
Human confirms Google destination calendar + booking configuration
         ↓
Enable booking for this tenant only (future runtime tenant isolation)
```

## Current PR scope

- `app/calendar_provisioning.py`: strict per-instance draft schema, validation and approved-only Cal.com create logic.
- `scripts/provision_calendar.py`: local **preview by default**; requires `--apply --confirm-instance ...` to mutate.
- `app/instances/summit-roofing-demo.json`: a fictional example; **approved=false**, so it cannot trigger writes.
- Mocked tests prove the preview is offline, repeated provisioning does not create duplicate resources, collisions fail safely, and unapproved drafts cannot write.
- It does **not** change the public FastAPI routes, Retell agents, Railway variables, booking settings, or calendars.

## Credentials and ownership

The API key must never be added to a committed JSON file, console output, chat message, or a website analysis result. For a single demo account, a staging-only `CALCOM_PROVISIONING_API_KEY` can be set in a private environment. For real customers, **do not reuse this master key**. Use a per-client OAuth connection or separately authorized credential with access limited to that client's account, stored in an encrypted secret manager.

The `calendar_account_ref` field is an internal reference, not a credential or proof of authorization. Cross-tenant account identity and access controls are future work before onboarding real clients.

## Dry-run now

```sh
python -m scripts.provision_calendar
```

This prints a safe preview of the proposed schedule and event type; makes **zero HTTP requests**.

## Applying to a dedicated demo account later

1. Review the draft, then set `approved` to true in a reviewed local or PR-controlled draft. Leave `demo_only` true.
2. Confirm that Cal.com is linked to the **Goodlookin Demo** secondary Google Calendar, and set it as the event type's *destination calendar*. Merely seeing that calendar in Google Calendar does not mean it is connected to Cal.com.
3. Set `CALCOM_PROVISIONING_API_KEY`, `CALCOM_SCHEDULES_API_VERSION` and `CALCOM_EVENT_TYPES_API_VERSION` in your private execution environment. Cal.com's API docs expose these version headers; verify their supported values against the live API documentation before applying.
4. Run `python -m scripts.provision_calendar --apply --confirm-instance summit-roofing-demo`.
5. Inspect the response IDs and event configuration in Cal.com. Provisioning deliberately reports `destination_calendar_verified=false` and `bookings_enabled=false`; do not switch on bookings based on resource creation alone.
6. After review, copy the returned **numeric event type ID** into staging configuration and separately enable `CALENDAR_PROVIDER=calcom` with a booking-scoped API credential. Enable the fictional business `booking_enabled` only after call flow, ownership and calendar checks.

**No automatic repeats after uncertain Cal.com POST outcomes.** Re-run discovery only after inspecting which resources already exist.

## Known limits — not yet a full multi-tenant platform

This phase provisions schedule and event type under whichever Cal.com account the credential authorizes. It does *not* programmatically attach or verify the Google destination calendar, manage OAuth, persist a tenant registry, scope Retell call IDs to tenant identities, or switch runtime booking providers per call. Those are essential before using this with actual clients.

Cal.com API references:
- https://cal.com/docs/api-reference/v2/schedules/create-a-schedule
- https://cal.com/docs/api-reference/v2/schedules/get-all-schedules
- https://cal.com/docs/api-reference/v2/event-types/create-an-event-type
- https://cal.com/docs/api-reference/v2/event-types/list-event-types

## Engineering checkpoint

**What prevents one roofer's appointment from appearing on another roofer's calendar?**
This PR prepares per-instance definitions, but it does *not* solve that at runtime yet. A real multi-tenant release needs authenticated tenant→calendar bindings, isolated credentials, scoped request handling and tests that cross-tenant access is rejected.
