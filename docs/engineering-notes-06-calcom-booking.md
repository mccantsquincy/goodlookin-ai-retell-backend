# Engineering Notes #06 — Calendar-backed inspection booking

## Why this feature matters

A realistic receptionist must **read actual free slots** from a calendar and **confirm a booking in that calendar**. Our original FakeCalendar returned fixed test slots (9 AM, 11 AM, 2 PM). A caller could only receive a simulated confirmation.

This PR adds an **opt-in Cal.com v2 adapter**. It does not configure your Cal.com account, turn on booking, or modify the active Retell Composer flow.

## Architecture

```text
Retell: caller asks for inspection
    |
    v
FastAPI: check_service_eligibility
    |
    v
FastAPI: get_available_appointments
    |
    +--> Cal.com GET /v2/slots (actual availability)
    +--> PostgreSQL slot_offers (expires after 10 minutes)
    |
    v
Retell: read back a slot and request explicit confirmation
    |
    v
FastAPI: book_appointment
    |
    +--> validate caller owns lead + slot, verify required name/email
    +--> PostgreSQL booking_operations (durable pending intent)
    +--> Cal.com GET /v2/bookings (look for existing operation metadata)
    +--> Cal.com POST /v2/bookings (only if no prior match)
    +--> Cal.com GET /v2/bookings/{uid} (verify accepted)
    +--> PostgreSQL appointments (confirmed only after provider accepts)
```

## How uncertain outcomes work

Network timeouts can occur **after** Cal.com creates an appointment. Retrying POST blindly could double-book. Instead, the backend stores an operation ID before the external call, includes it in booking metadata, and checks Cal.com for existing bookings on retries.

If the provider cannot prove acceptance, the operation stays `pending`. The agent **must not say "confirmed"** until `get_booking_status` returns `status: confirmed`.

The initial metadata reconciliation is bounded to ten pages (up to 1,000 attendee-filtered bookings). If it cannot search the full result, the operation stays pending for manual investigation. This is adequate for a dedicated fictional demo; before real-client pilots we should add monitoring, automated reconciliation and stronger provider idempotency safeguards.

## Setup — dedicated demo calendar only

1. Create a separate Cal.com account or separate event type for your fictional **Summit Roofing**.
2. Configure a **60-minute roof inspection consultation** event with known opening hours, timezone `America/Phoenix`, an email-required attendee form, and no confirmation-approval step (only accepted bookings are confirmed).
3. Record the numeric **event type ID**. Generate a Cal.com API key with permission to read slots and create bookings.
4. In Railway **staging API → Variables**, set `CALENDAR_PROVIDER=calcom`, `CALCOM_API_KEY` and `CALCOM_EVENT_TYPE_ID`. Do **not** change the worker unless it needs provider access later.
5. In the fictional business configuration, only then enable `booking_enabled=true` after confirming the desired service area, intake fields, email collection, and approval of all FAQ facts.
6. Change Retell Composer so Alex collects name, callback phone, email, full address, ZIP, service type, and explicit contact authorization. Follow eligibility → get availability → caller confirmation → book → booking status.
7. Test with **fictional identity and a controlled email inbox**. Do not use actual roofing leads for this stage.

**Crucial:** enabling the provider and booking flag creates real bookings *in that Cal.com demo calendar*. Do not enable them before the test calendar is isolated.

## Provider API versions

Cal.com's API documentation uses **different required version headers by endpoint**:

- GET /v2/slots: `2024-09-04`
- POST /v2/bookings: `2026-02-25`
- GET /v2/bookings: `2026-05-01`
- GET /v2/bookings/{uid}: `2026-02-25`

Docs: https://cal.com/docs/api-reference/v2/slots/get-available-time-slots-for-an-event-type
and https://cal.com/docs/api-reference/v2/bookings/create-a-booking
and https://cal.com/docs/api-reference/v2/bookings/get-all-bookings

We pin these values instead of relying on older API defaults.

## Engineering checkpoints

1. Why do we ask Cal.com for slots instead of using the LLM's preferred time?
2. Why is `booking_operations` inserted **before** the Cal.com POST request?
3. If Cal.com times out, should Alex say the booking succeeded? Why?
4. Why does Cal.com require an attendee email even though our existing callback flow does not?

## Next steps

After a green PR and isolated demo account:
- Verify provider availability and booking with HTTP mock tests and one controlled staging test.
- Switch the fictional demo config and Retell Composer flow only after explicit inspection.
- Rework dashboard booking labels to distinguish simulated and real calendar confirmations.
- Then strengthen FAQ retrieval into semantic RAG and work on agentic website onboarding.
