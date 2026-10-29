# Engineering Notes #03 — Private staging operations dashboard

## What we built

A **read-only view** of the receptionist's existing database. The page is at `/demo`, and it is **disabled until the staging API has credentials configured**.

It answers the questions we'd otherwise ask by opening Retell and Railway separately:

- Has the system saved any leads?
- Are callback requests actually being queued?
- Did a test booking get stored (simulated only)?
- Which recent calls stopped after saving a lead, versus queuing a callback?

## Where the data comes from

```text
Retell Composer
     |
     | verified POST request
     v
FastAPI custom tools
     |
     v
PostgreSQL: call_sessions, leads, callback_requests, appointments
     |
     | read-only SQL aggregates, no PII fields selected
     v
FastAPI /demo endpoint
     |
     | HTTP Basic authentication, server-generated HTML
     v
Private browser dashboard
```

There is **no new database schema** or new Retell function. The dashboard reads the tables we already created.

### Why we don't show names or phone numbers

This is intended for early engineering and sales demonstrations, **not** a CRM interface. The rendered page uses aggregate counts, anonymized call references, timestamps, statuses and yes/no activity indicators. It does not select or render names, contact information, addresses, issue summaries or transcripts.

It displays the **20 most recent calls**, with all-time totals. It does not poll or automatically refresh. A lead may exist even if the corresponding Retell webhook has not been processed.

### What “queued” means

When `request_callback` succeeds, it creates a `CallbackRequest` in PostgreSQL. It does **not** notify a roofer. Our UI therefore says **Callback requests queued**, not **Office contacted**.

Likewise, the app currently uses `FakeCalendar` in staging. We explicitly label all appointments as **simulated**.

## Security boundary

- The dashboard route is **404 by default**. It only opens when both `DEMO_DASHBOARD_USERNAME` and `DEMO_DASHBOARD_PASSWORD` are provided.
- The password must contain at least **16 characters**.
- The route is disabled in `APP_ENV=production`, even if credentials are set.
- Browser authentication uses HTTP Basic over HTTPS. Passwords are checked in memory and are never displayed.
- Responses disable browser/proxy caching and set restrictive content-security headers.
- This is a **single-business staging view**. It is NOT a production multi-tenant or customer-facing dashboard.

## How to enable it in Railway staging (after PR merge and deliberate redeploy)

1. In Railway, open `goodlookin-roofing-staging` → **staging** → `api`.
2. Set these secrets in the Railway **Variables** tab (do not commit them):

   ```text
   DEMO_DASHBOARD_USERNAME=your_private_username
   DEMO_DASHBOARD_PASSWORD=a_unique_random_secret_of_at_least_16_characters
   ```

3. Ensure the API actually deploys the merged GitHub revision. The existing Railway staging service was not automatically redeployed by recent GitHub merges; review its GitHub source attachment, build and startup configuration before changing it.
4. After a healthy release, visit `https://api-staging-dce8.up.railway.app/demo` and log in with your own secret.
5. Make a **fictional Retell Test LLM call** with a verified callback number, and ask Alex to record a callback.
6. Refresh the page. Verify the lead count, callback count, and latest activity indicator increase.

**Never paste dashboard credentials into ChatGPT, GitHub issues, Composer prompts, client screenshots or logs.** Use Railway secret variables.

## Engineering breakdown

`app/demo_dashboard.py` contains three important responsibilities:

1. `valid_basic_auth()` verifies the browser's owner credentials.
2. `dashboard_data()` uses SQLAlchemy to aggregate database facts, without reading PII.
3. `dashboard_html()` converts the safe summary into a simple, server-rendered HTML page.

`app/main.py` registers the route. `app/config.py` defines the environment configuration. `tests/test_demo_dashboard.py` verifies access controls and privacy.

### Checkpoints

1. Why do we return 404 when the dashboard is not configured?
2. Why do we use database aggregates instead of returning full `Lead` objects?
3. Why does a queued callback not mean the office received an email?
4. Why is this interface explicitly disabled in production?

## Next engineering changes

- Verify Retell's `save_lead` response and its **Speak After Execution** behavior in the active Composer version.
- Route the caller to `request_callback` and verify `data.status == "queued"`.
- Add actual office notifications with retries, delivery status, and alerts.
- Replace simulated slots with authoritative external calendar availability.
- Build the production multi-tenant dashboard after the first working pilot, not before.
