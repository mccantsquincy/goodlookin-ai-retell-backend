# Engineering Notes #05 — The one-instance roofing sales demo

Our priority is **one fully working fictional roofer** before website-driven multi-client provisioning.

## Design rule

The LLM interprets the caller's question. Python decides facts, policies and writes.

- FAQs: approved document/question retrieval. This first stage uses simple lexical matching, not embeddings or semantic vector RAG yet. It returns verbatim approved answers plus source IDs. Never claim something missing from the knowledge base.
- Service area: a deterministic ZIP-code and state allowlist, **never RAG**. ZIPs are business policy and must come from approved configuration, not from an inferred radius.
- Booking: the current backend offers simulated slots through FakeCalendar. Real calendar-backed confirmation is NOT implemented by this PR and should not be claimed in the sales demo.

## Added endpoints (Retell custom tools)

Both use the existing signed envelope `{"name":"…","call":{"call_id":"…"},"args":{…}}` and return `{"ok":true,"code":"OK","data":{…},"request_id":"…"}`.

- `search_knowledge` arguments: `{"question":"What are your business hours?"}`
- `check_service_area` arguments: `{"state":"AZ","postal_code":"85251","service_type":"inspection"}`

The latter returns `in_area`, `outside_area` or `needs_review`, never a made-up service guarantee.

## Demo fixture

`app/business.sales-demo.json` contains **fictional** roofing services, hours, 6 example FAQs and an explicit ZIP allowlist. It is not a real company policy. Booking remains OFF because confirmation would otherwise happen only in a simulated calendar.

Do not change Railway's current `BUSINESS_CONFIG_PATH` automatically; the active Retell agent is still aligned with the original staging context. This feature is only exposed after a reviewed configuration change.

## Test checkpoints

1. A caller asks about pricing. What approved answer is returned? Does it make a binding promise?
2. A caller asks about coverage of 85252. Why must Python say outside area rather than an LLM decide based on distance?
3. When no FAQ matches, why must Alex offer follow-up instead of inventing a response?
4. Why can we not truthfully say a real inspection was booked with the current FakeCalendar?

## Next engineering PRs

1. Upgrade knowledge search to embeddings + cited chunks (semantic RAG). Keep retrieval read-only and allow only approved sources.
2. Integrate a real appointment provider (Cal.com or Google Calendar) with authoritative availability, conflict checking, immutable idempotency keys and reconciliation on timeouts.
3. Wire the two new tools into Retell Composer and test full audio: FAQ → coverage → inspection availability → confirmed booking.
4. After one demo works, ingest a website into **draft** business knowledge with an agent, owner review, then versioned Retell provisioning and tenant isolation.

**No unreviewed website content should become live business policy.**
