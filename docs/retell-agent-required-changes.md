# Retell Composer release checklist — v2 export

This checklist reflects the privately shared *Summit Roofing Receptionist v2* export, not the initial Composer draft. For full context, see [Engineering Notes #02](engineering-notes-02-composer-agent-architecture.md). Do not commit a raw agent export or tokens to this public repository.

## Already present in v2

- All seven custom tools use the existing Railway staging API host.
- Webhooks are directed to the staging `/v1/retell/webhooks` endpoint.
- The global prompt correctly disallows unapproved services, coverage and booking claims.
- Fallback intake states the backend is unavailable and **does not** claim an unsaved callback was received.
- Human handoff is a **callback offer**, not an actual phone transfer.

## For a safe, working voice demo

- [ ] Confirm which agent version is actually active. Export v2 is `is_published: false` with no assigned tags.
- [ ] Verify signed `get_business_context` request and response-variable mappings through an actual Retell test call.
- [ ] Verify `save_lead` and `request_callback` with fictional contact details; do not promise an office notification.
- [ ] Test unknown FAQ, urgent hazard, human request, and unavailable backend.
- [ ] Confirm staging does not book, transfer, or invent service areas / operating hours.
- [ ] Bring GitHub CI and Railway deployment freshness into a known good state.

## For a booking demo

- [ ] Add the currently missing `collect_lead_details → check_eligibility` transition.
- [ ] Require a saved lead and complete confirmed intake before eligibility is checked.
- [ ] Require `ok: true`, eligible result, and `booking_enabled: true` before availability.
- [ ] If still simulated, **tell the caller explicitly** it is not a real appointment.
- [ ] Test stale slot and uncertain booking responses through `get_booking_status`.
- [ ] Map `data.transfer_phone_number` before implementing real handoff.

## For a real-business pilot

- [ ] Configure owner-approved coverage, hours, supported services, intake, FAQ and transfer rules.
- [ ] Replace simulated calendar availability with an actual provider-backed adapter.
- [ ] Add lead/callback notifications and delivery monitoring.
- [ ] Review consent, retention and PII treatment; v2 stores everything, has no PII categories selected and has memory reading on.
- [ ] Verify retries, no duplicate bookings, signature validation and end-to-end customer outcomes.
