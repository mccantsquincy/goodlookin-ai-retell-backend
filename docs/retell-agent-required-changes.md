# Required Retell dashboard changes before release

The source export in Downloads is reference data and has not been modified. Its seven custom tools use the wrapped `{name, call: {call_id}, args}` request. Update the staged agent configuration as follows:

1. Replace every `REPLACE-WITH-BACKEND-BASE-URL` tool and webhook URL with the deployed HTTPS backend URL.
2. Map `transfer_phone_number` from `data.transfer_phone_number` in `get_business_context` response variables. The export currently maps `transfer_available` but omits the number.
3. Gate the global human handoff node on `transfer_available == true` and a configured transfer number.
4. Route unavailable and failed transfers to `request_callback`; verify a durable callback response before promising follow-up.
5. Change fallback intake to call `save_lead` and, when appropriate, `request_callback`, or remove wording that implies information was saved. The present fallback ending can claim follow-up without persistence.
6. Require confirmed callback phone, property address, service, and `save_lead` success before the intake completion transition to eligibility. Use `data.missing_fields` and `data.saved`, not only a prompt transition.
7. Keep the agent on a staging tag until signed endpoint, booking, timeout, callback, and transfer release tests pass. The export shows no assigned tags and is published.
8. Review the current `everything` data retention setting, empty PII categories, contact memory read setting, empty fallback voices, and 60-minute maximum call duration with the owner and privacy team.

The eligibility configuration, services, hours, transfer number, and callback wording must be approved and replaced before any production use. The backend defaults deliberately contain no actual Summit Roofing facts.
