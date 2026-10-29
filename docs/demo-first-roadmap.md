# Goodlookin AI: demo-first build and study guide

This guide is written for someone learning backend engineering while building a product.
**Read it in order.** The goal of this PR is a **repeatable local demo**, not a live
customer-ready receptionist.

## Our goal

A person calls a roofing receptionist. The receptionist collects details, checks the
business rules, offers an inspection, tries to book, and arranges follow-up if needed.
Retell supplies the speaking and listening. Python owns the data and rules.

A **demo** can use fake contact details, a simulated appointment calendar and a
fictional company. A **pilot** cannot pretend a simulated booking is a real appointment.

## Diagram: what talks to what?

~~~text
Caller
  |
  v
Retell voice agent          <- The conversation / AI
  |
  | signed HTTPS custom tool request
  v
FastAPI (app/main.py)       <- The HTTP front door
  |
  | validate the request (app/contracts.py)
  | check caller's call_id
  v
Business service (app/service.py) <- The rules and operations
  |
  +---- BusinessConfig (app/business.py) -> eligibility
  +---- CRM adapter (app/crm.py) --------> saved lead
  +---- Calendar adapter (app/calendar.py) -> offered/booked slot
  +---- PostgreSQL (app/db.py) ----------> durable records
  |
  v
Result back to Retell -> agent explains it to caller

Separately: Retell event -> app/webhooks.py -> worker (app/cli.py)
~~~

### Five important vocabulary words

- **Endpoint**: a URL FastAPI listens to, like /v1/retell/tools/save_lead.
- **Schema**: a contract saying which fields and types are permitted.
- **Adapter**: a Python interface for swapping a fake service for a real calendar or CRM.
- **Idempotency**: retrying a booking without creating a duplicate appointment.
- **Worker**: a separate process doing event processing after the HTTP request ends.

## What this PR changes

1. An explicitly fictional business configuration is added at app/business.demo.json.
   It permits ONLY simulated inspection bookings for test ZIP code 85001.
2. scripts/demo_smoke.py walks through all seven tool endpoints with made-up caller data.
   It **refuses remote URLs** so it cannot accidentally run on a client server.
3. tests/test_demo_smoke.py checks that safety boundary and the fixture.
4. GitHub Actions runs tests/lint and checks PostgreSQL migrations on pull requests.

This does **not** create a real Retell voice agent, real calendar events, human callbacks,
a CRM integration, or a production deployment.

## Run the local backend

Prerequisites: Python 3.12, Docker, and Docker Compose. In your repo folder:

~~~sh
python3.12 -m venv .venv
.venv/bin/python -m pip install -e '.[dev]'
cp .env.example .env
~~~

Edit the ignored .env file with your own local database values. Example values below
are **local development examples** only; never reuse these passwords in a hosted system.

~~~dotenv
APP_ENV=development
DATABASE_URL=postgresql+asyncpg://demo_user:choose_a_local_password@localhost:5432/demo_db
POSTGRES_USER=demo_user
POSTGRES_PASSWORD=choose_a_local_password
POSTGRES_DB=demo_db
BUSINESS_CONFIG_PATH=app/business.demo.json
RETELL_SIGNATURE_BYPASS=true
RETELL_API_KEY=
~~~

Why the bypass? A local script cannot generate genuine Retell signatures. The app only
permits this bypass in development/test. **Never enable it in staging or production.**

Start PostgreSQL and initialize database tables:

~~~sh
docker compose up -d db
.venv/bin/python -m alembic upgrade head
~~~

In **Terminal 1**, run the API:

~~~sh
.venv/bin/uvicorn app.main:app --reload --host 127.0.0.1
~~~

In **Terminal 2**, run the synthetic flow:

~~~sh
.venv/bin/python -m scripts.demo_smoke
~~~

Expected output starts with "PASS: local Goodlookin AI demo smoke test" and shows:

- business context loaded
- synthetic lead saved
- eligibility checked
- simulated appointment offered
- simulated booking confirmed
- callback request persisted (NOT delivered)

Each successful run writes synthetic records into your local PostgreSQL database.

If it fails, check that /ready reports ready, your .env path is correct, and
you have restarted Uvicorn since changing .env.

## See the API while you learn

Open http://127.0.0.1:8000/docs to view FastAPI's endpoint schemas.
Do not use the public Retell webhook URL for the unsigned local smoke script.

Read code in this order:

1. app/contracts.py: What does a valid call look like?
2. app/main.py: How is a tool name routed to the right function?
3. app/service.py: What logic checks the lead and commits a booking?
4. app/calendar.py: How do simulated offers and expiration work?
5. app/db.py: Which records survive a restart?
6. app/webhooks.py: Why is call-end event handling separate?

### Trace exercise

When the script asks for an appointment:

1. Which schema validates the preferred_date field?
2. What code checks whether the lead is eligible?
3. What makes the slot offer expire?
4. If a calendar call times out after writing, how can get_booking_status recover?
5. Why is a queued callback **not** proof that someone was notified?

Answer key: AvailabilityArgs; eligibility() in app/business.py, called from
app/service.py; offer_expiration(); calendar.lookup() through reconcile(); and because
request_callback() persists a database record but has no notification dispatcher.

## How to show a real VOICE demo later

The command-line smoke test proves the backend path, not Retell call audio.
For a voice demo, use a dedicated staging environment with genuine Retell signature
verification, the demo business identity clearly disclosed, and test-only call data.
Do not connect actual customer phone numbers or promise actual appointments.

The Retell dashboard configuration still needs the changes described in
docs/retell-agent-required-changes.md, notably the fallback intake, handoff routing,
and confirmed field collection. Check each tool URL against the hosted staging API.

Retell tool and webhook requests must use valid signatures on any public URL.
Your staging backend must remain isolated from production traffic.

## The next three product gates

### Gate A: backend demo (this PR begins it)

- Automated checks pass in GitHub.
- A developer can run the seven-tool synthetic flow consistently.
- No real callbacks, appointments or transfers are promised.
- Staging Retell conversation changes are still pending.

### Gate B: usable roofing sales demo (next)

- Retell staged voice agent completes an entire call with a fictional persona.
- No false promise of a real booking or human callback.
- A simple, access-controlled way to inspect demo outcomes.
- A documented five-minute sales walkthrough.
- Record failures and improvements from at least ten varied test calls.

### Gate C: first real-business pilot

- Replace FakeCalendar with a real adapter. Crucially, availability must come
  from the external provider, not the fixed proposed_slots() function.
- Add actual delivery of callback/lead notifications with retries and alerts.
- Verify customer-approved hours, eligibility, transfer routes and calendar rules.
- Use business-specific credentials and access controls.
- Add privacy, retention and recording-consent processes appropriate to the pilot.
- Add pending-booking reconciliation in a background worker.
- Monitor live success, failure and handoff rates.

### Then: onboarding at scale

Only after the pilot works should we build automated website ingestion,
fact extraction, human approval, versioned business configurations and
Retell-agent provisioning. **Agentic** work should draft facts and FAQs;
**deterministic** code must validate, book, deduplicate and publish.

## Pull request habit

For every change: create a branch, write or update tests, run pytest,
run Ruff, open a PR, inspect GitHub Actions results, review the diff, and
merge only when you understand what changed. Small PRs make debugging easier.
