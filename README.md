# Goodlookin AI roofing receptionist backend

FastAPI MVP for the seven Retell custom functions and post-call webhook. The source Retell agent JSON in Downloads remains untouched. Tool requests use its wrapped `name`, `call.call_id`, and `args` shape. Tool responses use `{ok, code, data, request_id}`. Business failures return `ok: false` with HTTP 200; malformed requests use 422, bad signatures use 401, and unexpected failures use 500.

## Architecture

`app/main.py` verifies the raw body before parsing and exposes the routes. `app/contracts.py` isolates Retell request parsing. `app/service.py` owns lead, booking, and callback operations. `app/crm.py` defines the CRM adapter and local database-backed substitute. `app/business.py` holds configurable eligibility rules. `app/calendar.py` defines a calendar adapter and database-backed fake. SQLAlchemy models are in `app/db.py`; Alembic has the initial migration. `app/webhooks.py` stores a receipt and the separate `python -m app.cli worker` command processes it. Only event type and call ID are retained from webhooks; transcripts are not stored.

The fake calendar proposes 09:00, 11:00, and 14:00 Arizona time on the requested date. It creates opaque, expiring offers tied to a call and lead. Reservation uniqueness prevents the same underlying slot from being booked twice. A reservation is persisted before the fake may simulate a timeout. `get_booking_status` reconciles that pending operation without rebooking. A production adapter must implement `book` and `lookup` against an actual calendar with an external idempotency key (`operation_id`) and authoritative status lookup.

## Setup

Python 3.12 and Docker are required for the PostgreSQL path:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[dev]'
cp .env.example .env
.venv/bin/python -m app.cli bootstrap
docker compose up -d db
.venv/bin/alembic upgrade head
.venv/bin/uvicorn app.main:app --reload
```

Before starting Docker or the API, edit the ignored `.env`: replace every placeholder with local-only values. Set `DATABASE_URL` to your local PostgreSQL connection URL using the same user, password, and database name as `POSTGRES_USER`, `POSTGRES_PASSWORD`, and `POSTGRES_DB`. The application has no committed database password or connection default.

Open `/docs` for OpenAPI. In another terminal run the durable webhook processor:

```sh
.venv/bin/python -m app.cli worker
```

The sample business configuration is intentionally labeled `LOCAL DEVELOPMENT ROOFING DEMO`, with booking and transfer disabled, no service or coverage claims, and no phone number. To exercise booking locally, edit ignored `business.local.json` with approved **test** values such as `booking_enabled: true`, `eligible_states: ["AZ"]`, `eligible_postal_codes: ["85001"]`, `eligible_services: ["inspection"]`, `services: ["inspection"]`, and `appointment_type: "inspection"`. Those values are fixtures, not Summit Roofing policy. The config file must be replaced with owner-approved hours, services, FAQs, eligibility, callback wording, and transfer settings before production. Production startup rejects the fake calendar and local CRM adapter; implement and inject real adapters before release.

`RETELL_API_KEY` is used by the official Retell Python SDK to verify the exact body with its timestamped `X-Retell-Signature`. `RETELL_SIGNATURE_BYPASS=true` is allowed only with `APP_ENV=development` or `test`, and is off by default. Production rejects bypass or a missing API key at startup. Use a webhook-enabled Retell key. The local smoke test below uses bypass only on localhost.

## Local smoke test

With the API running, edit `.env` to set `RETELL_SIGNATURE_BYPASS=true` and restart it. Use a test business config with booking enabled as described above. The following short script performs intake, eligibility, availability, booking, status, and callback; it prints IDs and state but no caller contact fields:

```sh
.venv/bin/python - <<'PY'
import datetime
import json
import urllib.request
from zoneinfo import ZoneInfo

base = "http://127.0.0.1:8000/v1/retell/tools/"
call_id = "local-smoke-1"
def tool(name, args):
    body = json.dumps({"name": name, "call": {"call_id": call_id}, "args": args}).encode()
    req = urllib.request.Request(base + name, body, {"Content-Type": "application/json"})
    with urllib.request.urlopen(req) as response:
        result = json.load(response)
    assert result["ok"], result["code"]
    return result["data"]

lead = tool("save_lead", {"name": "Test Caller", "callback_phone": "6025550101",
    "street_address": "123 Test Street", "city": "Phoenix", "state": "AZ",
    "postal_code": "85001", "property_type": "residential", "service_type": "inspection",
    "issue_summary": "test inspection", "authorized_contact": True})["lead_id"]
assert tool("check_service_eligibility", {"lead_id": lead})["eligibility"] == "eligible"
day = (datetime.datetime.now(ZoneInfo("America/Phoenix")) + datetime.timedelta(days=1)).date().isoformat()
slot = tool("get_available_appointments", {"lead_id": lead, "preferred_date": day})["slots"][0]["slot_id"]
booking = tool("book_appointment", {"lead_id": lead, "slot_id": slot, "caller_confirmed": True})
status = tool("get_booking_status", {"lead_id": lead, "operation_id": booking["operation_id"]})
callback = tool("request_callback", {"lead_id": lead, "reason": "test follow-up", "urgency": "routine"})
print({"booking_status": status["status"], "operation_id": status["operation_id"],
       "callback_status": callback["status"]})
PY
```

Run quality checks and migrations:

```sh
.venv/bin/pytest -q
.venv/bin/ruff check .
.venv/bin/ruff format --check .
.venv/bin/alembic upgrade head
.venv/bin/alembic downgrade base
.venv/bin/alembic upgrade head
```

## Environment variables

- `APP_ENV`: `development`, `test`, `staging`, or `production`.
- `DATABASE_URL`: required async SQLAlchemy URL, supplied through the environment or ignored `.env` file.
- `RETELL_API_KEY`: webhook-enabled API key for signature verification.
- `RETELL_SIGNATURE_BYPASS`: local and test only; defaults to `false`.
- `BUSINESS_CONFIG_PATH`: local JSON business configuration path.

Timestamps are timezone-aware ISO 8601 and business scheduling uses `America/Phoenix`. Phone numbers are conservatively normalized to E.164. The database stores confirmed lead fields and status history; deployers must set appropriate PII access and retention controls. The remaining production decisions are the CRM adapter, calendar adapter, approved business configuration, transfer destination, callback workflow, and Retell dashboard changes in [`docs/retell-agent-required-changes.md`](docs/retell-agent-required-changes.md).

## Hosted staging deployment

The included `Dockerfile` starts the API and applies Alembic migrations before binding to the platform's `PORT`. Set `APP_ENV=staging`, `RETELL_SIGNATURE_BYPASS=false`, `BUSINESS_CONFIG_PATH=app/business.staging.json`, `DATABASE_URL` to a managed PostgreSQL URL, and `RETELL_API_KEY` to a webhook-enabled Retell key. Plain `postgresql://` database URLs are converted to the asyncpg driver internally. Run a second service from the same image with `SERVICE_ROLE=worker` for durable webhook processing; it retries while the database or migration is unavailable. The staging business config uses the agent's existing company name but makes no claims about services, hours, coverage, booking, or transfers. Use only staging calls until owner-approved business configuration and production integrations exist.

The public webhook URL is `https://<assigned-host>/v1/retell/webhooks`; each custom tool is under `https://<assigned-host>/v1/retell/tools/<tool-name>`. Test `/ready` over HTTPS before connecting Retell. Do not use the local development signature bypass on the hosted service.

For Railway's three-service staging setup and verification steps, see [`docs/railway-staging.md`](docs/railway-staging.md).

## Demo-first development

For the **fictional, local-only** backend demo, follow
[docs/demo-first-roadmap.md](docs/demo-first-roadmap.md). It includes the
seven-tool smoke test, setup instructions and the distinction between a demo
and a pilot. For beginner-friendly code tracing, see
[docs/lesson-01-lead-intake.md](docs/lesson-01-lead-intake.md).

The demonstration uses a fake calendar and only stores callback requests.
It **does not** place real appointments or notify a roofing business.

## Retell Composer architecture

See [Engineering Notes #02](docs/engineering-notes-02-composer-agent-architecture.md) for
the exported v2 conversation flow, reachable tools, integration gaps and demo
acceptance criteria. To statically inspect a **private** Composer JSON export
without uploading it to GitHub, run `python -m scripts.audit_retell_export --help`.

## Private operations demo preview

The staging-only read-only dashboard at `/demo` is disabled until
`DEMO_DASHBOARD_USERNAME` and a unique 16+-character `DEMO_DASHBOARD_PASSWORD`
are configured. It shows anonymous counts and call states, **not contact
information, real bookings or delivered office notifications**. See
[Engineering Notes #03](docs/engineering-notes-03-demo-operations.md) for setup,
security boundaries and the next demo-to-pilot milestones.

## Optional Cal.com calendar-backed demo

See [Engineering Notes #06](docs/engineering-notes-06-calcom-booking.md). The adapter is opt-in and
uses a dedicated demo Cal.com event type to fetch authoritative slots and create confirmed bookings.
Keep `CALENDAR_PROVIDER=fake` and `booking_enabled=false` in staging until a separate demo account,
its required attendee fields, and Retell test flow are configured and verified.

## Secure browser-based demo calendar provisioning

See [Engineering Notes #08](docs/engineering-notes-08-admin-calendar.md). This staging-only console at
`/admin/calendar` stays inaccessible until a unique 32+-character
`PROVISIONING_ADMIN_PASSWORD` is set on the API service. It uses the already configured
Cal.com provisioning key and endpoint versions, and **does not** turn on bookings.
