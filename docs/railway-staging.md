# Railway staging deployment

This is a staging-only deployment of the FastAPI service, PostgreSQL, and a durable webhook worker. The local CRM and calendar substitutes remain in use; the staging business configuration disables booking and transfer. Keep Retell on a staging tag and use test calls only.

## Services

Deploy this directory to one Railway project with three services:

- `Postgres`: Railway PostgreSQL database, private networking only.
- `api`: Build from `Dockerfile`, default `SERVICE_ROLE=api`. It applies `alembic upgrade head` then starts Uvicorn on Railway's `PORT`. Generate a Railway public domain for this service and set its health check to `/ready`.
- `worker`: Build from the same `Dockerfile` with `SERVICE_ROLE=worker`. It runs `python -m app.cli worker` and needs no public domain.

Set these variables on `api` and `worker`:

```text
APP_ENV=staging
RETELL_SIGNATURE_BYPASS=false
BUSINESS_CONFIG_PATH=app/business.staging.json
DATABASE_URL=${{Postgres.DATABASE_URL}}
RETELL_API_KEY=<webhook-enabled Retell API key stored as a secret>
```

Set `SERVICE_ROLE=worker` only on the worker. Do not enable Railway Serverless or app sleeping for the API: the agent's custom functions have eight-second timeouts, so a cold start can break calls. Watch Railway usage and configure a billing limit appropriate for the staging budget. The service will not become ready until the database URL and Retell key are configured.

## Verification

1. Check `https://<railway-domain>/health` and `https://<railway-domain>/ready` both return 200.
2. Confirm an unsigned tool request returns 401. Verify a signed test call from Retell returns the stable tool envelope.
3. Set Retell's webhook URL to `https://<railway-domain>/v1/retell/webhooks` and enable `call_started`, `call_ended`, and `call_analyzed`.
4. Point the seven custom tools to `https://<railway-domain>/v1/retell/tools/<tool-name>`.
5. Run the release flow tests from `docs/retell-agent-required-changes.md` before removing the staging tag.

Never paste the Retell API key into a prompt, README, agent JSON, or source file. Replace the staging business configuration with owner-approved values and real CRM/calendar adapters before production.
