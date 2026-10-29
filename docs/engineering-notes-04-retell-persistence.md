# Engineering Notes #04 — Retell tool responses and database persistence

## The symptom

Retell Test LLM reached the staging API. Railway logged HTTP 200 for `get_business_context`, two calls to `save_lead`, and `request_callback`. But no change was observed in the protected `/demo` dashboard.

**HTTP 200 only means our API handled the request.** The Python business contract also uses HTTP 200 with `{"ok": false, "code": "..."}` when a valid request cannot be accepted. We have not inspected the actual Retell tool response, so the precise cause remains unconfirmed.

## One confirmed compatibility problem in the code

Retell Composer can send fields it hasn't collected yet as explicit JSON null:

```json
{"callback_phone": "6025550101", "email": null, "street_address": null}
```

Previously, `app/crm.py` included these explicitly submitted null values in updates and then rejected them as `INVALID_FIELD_VALUE`. Now `exclude_none=True` treats an optional null value as "not provided." This also prevents a later tool call from erasing already confirmed values. A missing/invalid required phone number is still rejected, and blank strings remain invalid.

## What to check in the Test LLM interface

Open the **Tool response** sections for both `save_lead` and `request_callback`.

A saved lead response must contain:
- `ok: true`
- `data.saved: true`
- `data.lead_id` with a value

A queued callback response must contain:
- `ok: true`
- `data.status: queued`

If either one contains `ok: false`, the `code` identifies the failure. For example, `INVALID_PHONE`, `INVALID_FIELD_VALUE` or `LEAD_NOT_FOUND`.

**Do not confuse "queued" with "delivered":** no email or SMS is sent to a real roofing office by this release.

## Why the dashboard may not increment

There is one lead per Retell `call_id`; re-saving that lead updates its existing row, not the total count. A callback request with the same reason is also deduplicated. Retell's Test LLM may reuse a call ID during repeated tests. Check the last activity row and both tool responses, not only the total number.

## Improving diagnosis without sharing personal information

`app/main.py` now emits a warning log with a business failure **code** when a tool returns a non-OK result. It logs request metadata rather than caller name, phone, address, or lead fields.

In Railway, open the **api** service logs and search for:
- `INVALID_FIELD_VALUE` — a blank or invalid optional value
- `INVALID_PHONE` — failed phone normalization
- `LEAD_NOT_FOUND` — the callback referenced no persisted lead for this call
- `MALFORMED_REQUEST` — invalid Retell tool arguments

Inspect the code; do not copy customer contact information, Retell API keys, or dashboard credentials into debugging notes.

## Follow-up verification

After this PR passes CI, is merged, and the staging deployment succeeds, use fresh fictional caller data. Confirm both response bodies report success and the dashboard shows the lead and queued callback. Booking and human transfers remain disabled.

### Engineering checkpoint

**Why is `HTTP 200` different from `ok: true`?** HTTP 200 means the transport succeeded. `ok: true` means our deterministic business operation succeeded.
