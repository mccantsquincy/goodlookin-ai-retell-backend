# Engineering Notes #09 — Summit Roofing live-calendar demo activation

## Verified foundation

- Cal.com event type ID: `7716972` (fictional **Summit Roofing — Roof Inspection (Demo)**)
- Duration: 60 minutes; timezone `America/Phoenix`
- User has selected **Goodlookin Demo** as the Cal.com event destination. This still requires **one real calendar event** to verify end-to-end synchronization.
- Staging API now uses `CALENDAR_PROVIDER=calcom`, event type `7716972`, a secret reference to the Cal.com demo-account provisioning key, and `BUSINESS_CONFIG_PATH=app/business.sales-demo.json` (booking **disabled**).

## Controlled activation (separate from merging)

This PR adds `app/business.sales-demo.bookable.json` with `booking_enabled=true`. It is **not selected** by default. After reviewing the business facts and confirming a fictional customer test, a staging operator may change **only** the API service variable:

```
BUSINESS_CONFIG_PATH=app/business.sales-demo.bookable.json
```

Deploy staging and verify `/ready`. This enables **real calendar booking creation** through the agent's existing `book_appointment` tool; such calls can send attendee notifications. Do not activate until the caller identity and target event are a controlled test.

**Never use this profile for an actual roofing customer or production.** It contains invented FAQs and ZIP code policies. The pilot needs approved customer data, authenticated tenant-scoped calendar credentials, callback notification delivery and further security review.

## End-to-end conversation test

1. Call fictional Summit Roofing agent using a controlled telephone or simulator. Alex must explain it is a demo and collect an authorized fictional test identity (name, reachable test email, phone, property ZIP, issue and service type exactly `inspection`).
2. `get_business_context` / `search_knowledge`: only approved fictional FAQ facts, never hallucinated pricing or coverage.
3. `check_service_area`: exact allowlist. Example 85251 is allowed; 85252 is outside.
4. `save_lead` → `check_service_eligibility`; inspect response `ok` and `data.eligibility`.
5. `get_available_appointments` for an eligible future date; review actual Cal.com slots.
6. After user explicitly confirms one offered time: `book_appointment`. Only say "confirmed" after `data.status=confirmed` or `get_booking_status` confirms.
7. Inspect Cal.com booking UID and the **Goodlookin Demo** Google Calendar event for that exact slot; check the Goodlookin dashboard for a new appointment record.
8. Run a repeat same-call booking request; ensure no second Cal.com booking is created. Test unavailable ZIP and calendar timeout fail-closed.
9. Cancel the fictional booking after verification to keep the calendar clean if appropriate, using an authorized cancel action.

## Retell Composer wiring

Confirm that the agent has custom tools for all of:

- `get_business_context`
- `search_knowledge`
- `check_service_area`
- `save_lead`
- `check_service_eligibility`
- `get_available_appointments`
- `book_appointment`
- `get_booking_status`
- `request_callback`

The Python endpoints exist but **Composer configuration is independent**. Agent should not offer times from memory, book without explicit confirmation, or promise callback delivery.

## Dashboard labeling

Since historical staging data may include simulated appointments, `/demo` now displays **Appointment records (mixed history)** when configured for Cal.com. This is not a provider-confirmed bookings report. Provider/calendar verification remains the authoritative test.

## Remaining work

After first successful call and calendar sync:
- Add semantic RAG with citations, source review and refusal on unsupported answers.
- Build a provisioner that drafts business facts from an actual roofer website, requests approval and creates versioned, tenant-isolated agents and calendars.
- Separate demo provisioning secrets from per-client runtime booking credentials.
