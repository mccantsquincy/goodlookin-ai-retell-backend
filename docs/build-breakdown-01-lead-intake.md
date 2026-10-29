# Build Breakdown #01 — How one caller becomes one saved lead

Our first engineering breakdown starts in app/main.py, in the function handle_tool().

## In everyday language

Retell is like a receptionist at the front counter. FastAPI is the secure door
into the office. The business service is the office manager deciding what can
happen. PostgreSQL is the filing cabinet that survives a restart.

When the voice agent hears a customer ask for a roof inspection, it can call
save_lead with structured fields. Python does **not** blindly trust them.

## The exact route

1. Retell sends POST /v1/retell/tools/save_lead.
2. app/security.py checks the signed raw HTTP body in hosted environments.
3. app/contracts.py parses ToolRequest and SaveLeadArgs.
4. app/main.py passes the request to service.save_lead().
5. app/crm.py normalizes and saves the caller in the database.
6. service.save_lead() returns saved, lead_id and missing_fields.
7. Retell can use that result before moving to eligibility.

## Why not let the AI write the database directly?

The AI might omit a required field or repeat a call. The schema can reject
bad input. The service can check ownership by call_id. The database can
prevent certain duplicates. These controls are reliable precisely because
they are not generated anew for each conversation.

## The difference between an outcome and an action

request_callback returning 'queued' means the **database accepted the request**.
It does NOT mean the roofing office received a text, an email, or a task.
An actual delivery service is a separate feature we must implement for the pilot.

Likewise, the demo's FakeCalendar returning 'confirmed' means a **test slot**
was stored. It is not proof that an external calendar was updated.

## Engineering checkpoints

1. Where is the model for the save_lead tool's fields defined?
2. Which function verifies the Retell signature?
3. What happens if the caller provides an invalid phone number?
4. Why do we store a booking operation before talking to the calendar?
5. What still must happen before we can say, 'Your appointment is confirmed'
   to a real roofing lead?

Discussion notes: app/contracts.py; verify_retell_request() in app/security.py; the
phone normalizer rejects it; so a retry can reconcile rather than duplicate;
a real calendar adapter must confirm the booking and return an authoritative ID.

Next Build Breakdown: trace check_service_eligibility and
get_available_appointments in the actual code.
