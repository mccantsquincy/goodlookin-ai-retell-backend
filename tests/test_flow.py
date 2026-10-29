import json
import logging
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from sqlalchemy import func, select

from app.config import Settings
from app.db import Appointment, BookingOperation, CallbackRequest, Lead, WebhookReceipt
from app.main import create_app
from app.webhooks import process_receipts
from tests.conftest import full_lead, tool

ZONE = ZoneInfo("America/Phoenix")


@pytest.mark.asyncio
async def test_happy_booking_and_duplicate(harness):
    client, app, _ = harness
    lead_id = await full_lead(client)
    eligible = (await tool(client, "check_service_eligibility", lead_id=lead_id)).json()
    assert eligible["data"]["eligibility"] == "eligible"
    day = (datetime.now(ZONE) + timedelta(days=1)).date().isoformat()
    available = (await tool(client, "get_available_appointments", lead_id=lead_id, preferred_date=day)).json()
    slot = available["data"]["slots"][0]
    assert slot["starts_at"].endswith("-07:00")
    first = (
        await tool(client, "book_appointment", lead_id=lead_id, slot_id=slot["slot_id"], caller_confirmed=True)
    ).json()
    second = (
        await tool(client, "book_appointment", lead_id=lead_id, slot_id=slot["slot_id"], caller_confirmed=True)
    ).json()
    assert first["data"]["status"] == "confirmed"
    assert first["data"]["operation_id"] == second["data"]["operation_id"]
    assert first["data"]["booking_id"] == second["data"]["booking_id"]
    async with app.state.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Appointment)) == 1
        assert await session.scalar(select(func.count()).select_from(BookingOperation)) == 1
        operation = await session.get(BookingOperation, first["data"]["operation_id"])
        history = json.loads(operation.status_history)
        assert [event["status"] for event in history] == ["pending", "confirmed"]
        assert all(event["at"].endswith("+00:00") for event in history)


@pytest.mark.asyncio
async def test_corrections_update_one_lead(harness):
    client, app, _ = harness
    first = await full_lead(client)
    second = (
        await tool(client, "save_lead", lead_id=first, callback_phone="6025550199", street_address="456 Corrected Ave")
    ).json()
    assert second["data"]["lead_id"] == first
    async with app.state.sessions() as session:
        lead = await session.get(Lead, first)
        assert lead.callback_phone == "+16025550199"
        assert lead.street_address == "456 Corrected Ave"
        assert await session.scalar(select(func.count()).select_from(Lead)) == 1


@pytest.mark.asyncio
async def test_full_state_name_on_lead_update(harness):
    client, app, _ = harness
    first = (await tool(client, "save_lead", callback_phone="6025550101")).json()["data"]["lead_id"]
    updated = await tool(
        client,
        "save_lead",
        lead_id=first,
        callback_phone="6025550101",
        street_address="123 Test Street",
        city="Phoenix",
        state="Arizona",
        postal_code="85001",
    )
    assert updated.status_code == 200
    assert updated.json()["ok"] is True
    async with app.state.sessions() as session:
        lead = await session.get(Lead, first)
        assert lead.state == "AZ"


@pytest.mark.asyncio
async def test_missing_and_ineligible_have_no_booking_path(harness):
    client, _, _ = harness
    partial = (await tool(client, "save_lead", callback_phone="6025550101")).json()["data"]["lead_id"]
    result = (await tool(client, "check_service_eligibility", lead_id=partial)).json()["data"]
    assert result["eligibility"] == "needs_review"
    denied = await tool(
        client,
        "get_available_appointments",
        lead_id=partial,
        preferred_date=(datetime.now(ZONE) + timedelta(days=1)).date().isoformat(),
    )
    assert denied.json()["code"] == "NOT_ELIGIBLE"
    other = await full_lead(client, "call-2", postal_code="99999")
    result = (await tool(client, "check_service_eligibility", "call-2", lead_id=other)).json()["data"]
    assert result["eligibility"] == "ineligible"
    denied = await tool(
        client,
        "get_available_appointments",
        "call-2",
        lead_id=other,
        preferred_date=(datetime.now(ZONE) + timedelta(days=1)).date().isoformat(),
    )
    assert denied.json()["code"] == "NOT_ELIGIBLE"


@pytest.mark.asyncio
async def test_slot_unavailable_and_timeout_reconciliation(harness):
    client, app, calendar = harness
    lead_id = await full_lead(client)
    day = (datetime.now(ZONE) + timedelta(days=1)).date().isoformat()
    slot = (await tool(client, "get_available_appointments", lead_id=lead_id, preferred_date=day)).json()["data"][
        "slots"
    ][0]["slot_id"]
    calendar.timeout_after_commit = True
    pending = (await tool(client, "book_appointment", lead_id=lead_id, slot_id=slot, caller_confirmed=True)).json()
    assert pending["data"]["status"] == "pending"
    resolved = (
        await tool(client, "get_booking_status", lead_id=lead_id, operation_id=pending["data"]["operation_id"])
    ).json()
    assert resolved["data"]["status"] == "confirmed"
    other = await full_lead(client, "call-2")
    other_slot = (await tool(client, "get_available_appointments", "call-2", lead_id=other, preferred_date=day)).json()[
        "data"
    ]["slots"]
    assert all(x["slot_id"] != slot for x in other_slot)
    assert len(other_slot) == 2
    async with app.state.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Appointment)) == 1


@pytest.mark.asyncio
async def test_callback_dedup(harness):
    client, app, _ = harness
    lead_id = await full_lead(client)
    args = dict(lead_id=lead_id, reason="Please call back", urgency="routine")
    first = (await tool(client, "request_callback", **args)).json()
    second = (await tool(client, "request_callback", **args)).json()
    assert first["data"]["callback_request_id"] == second["data"]["callback_request_id"]
    async with app.state.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(CallbackRequest)) == 1


@pytest.mark.asyncio
async def test_signature_and_malformed(harness, business):
    client, app, _ = harness
    app.state.settings.retell_signature_bypass = False
    assert (await tool(client, "get_business_context")).status_code == 401
    app.state.settings.retell_signature_bypass = True
    assert (await client.post("/v1/retell/tools/get_business_context", json={"args": {}})).status_code == 422
    with pytest.raises(ValueError):
        Settings(
            app_env="production",
            database_url="sqlite+aiosqlite:///:memory:",
            retell_signature_bypass=True,
            retell_api_key="test-only-key",
        )
    with pytest.raises(ValueError):
        Settings(app_env="production", database_url="sqlite+aiosqlite:///:memory:", retell_api_key="")
    with pytest.raises(ValueError):
        Settings(
            app_env="staging",
            database_url="sqlite+aiosqlite:///:memory:",
            retell_signature_bypass=True,
            retell_api_key="test-only-key",
        )
    with pytest.raises(ValueError, match="RETELL_API_KEY"):
        Settings(
            app_env="staging",
            database_url="sqlite+aiosqlite:///:memory:",
            retell_api_key="your_retell_api_key_here",
        )
    assert (
        Settings(
            app_env="staging",
            retell_api_key="test-only-key",
            database_url="postgresql://user@localhost/db",
        ).database_url
        == "postgresql+asyncpg://user@localhost/db"
    )
    with pytest.raises(ValueError, match="Production calendar and CRM adapters"):
        create_app(
            Settings(
                app_env="production",
                database_url="sqlite+aiosqlite:///:memory:",
                retell_api_key="test-only-key",
            ),
            business,
        )


@pytest.mark.asyncio
async def test_webhook_dedup_and_unknown(harness):
    client, app, _ = harness
    payload = {"event": "call_started", "call": {"call_id": "call-webhook", "transcript": "secret"}}
    assert (await client.post("/v1/retell/webhooks", json=payload)).status_code == 204
    assert (await client.post("/v1/retell/webhooks", json=payload)).status_code == 204
    assert (await client.post("/v1/retell/webhooks", json={"event": "future_event"})).status_code == 204
    async with app.state.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(WebhookReceipt)) == 2
        assert await process_receipts(session) == 2
        statuses = (await session.scalars(select(WebhookReceipt.status))).all()
        assert sorted(statuses) == ["ignored", "processed"]
    assert "transcript" not in json.dumps(statuses)


@pytest.mark.asyncio
async def test_logs_exclude_sensitive_fields(harness, caplog):
    client, _, _ = harness
    with caplog.at_level(logging.INFO, logger="roofing.api"):
        await tool(
            client,
            "save_lead",
            callback_phone="6025550101",
            email="sensitive@example.com",
            street_address="123 Secret Street",
        )
    text = caplog.text
    assert "6025550101" not in text
    assert "sensitive@example.com" not in text
    assert "123 Secret Street" not in text


@pytest.mark.asyncio
async def test_signed_raw_body_and_expired_signature(harness):
    from retell.lib.webhook_auth import symmetric

    client, app, _ = harness
    app.state.settings.retell_signature_bypass = False
    app.state.settings.retell_api_key = "test-secret"
    raw = b'{ "name":"get_business_context", "call":{"call_id":"signed"}, "args":{} }'
    valid = await client.post(
        "/v1/retell/tools/get_business_context",
        content=raw,
        headers={"X-Retell-Signature": symmetric["sign"](raw.decode(), "test-secret")},
    )
    assert valid.status_code == 200 and valid.json()["ok"]
    bad = await client.post(
        "/v1/retell/tools/get_business_context",
        content=raw,
        headers={"X-Retell-Signature": symmetric["sign"](raw.decode(), "wrong-secret")},
    )
    assert bad.status_code == 401 and bad.json()["code"] == "INVALID_SIGNATURE"


@pytest.mark.asyncio
async def test_retell_full_call_object_is_accepted(harness):
    client, _, _ = harness
    response = await client.post(
        "/v1/retell/tools/get_business_context",
        json={
            "name": "get_business_context",
            "args": {},
            "call": {
                "call_id": "call-with-context",
                "call_type": "web_call",
                "agent_id": "agent-staging",
                "transcript": "Caller: Hello",
                "metadata": {"source": "simulation"},
            },
        },
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True


@pytest.mark.asyncio
async def test_two_callers_cannot_book_same_underlying_slot(harness):
    client, app, _ = harness
    lead_one = await full_lead(client, "call-one")
    lead_two = await full_lead(client, "call-two")
    day = (datetime.now(ZONE) + timedelta(days=1)).date().isoformat()
    offer_one = (
        await tool(client, "get_available_appointments", "call-one", lead_id=lead_one, preferred_date=day)
    ).json()["data"]["slots"][0]
    offer_two = (
        await tool(client, "get_available_appointments", "call-two", lead_id=lead_two, preferred_date=day)
    ).json()["data"]["slots"][0]
    assert offer_one["slot_id"] != offer_two["slot_id"]
    assert offer_one["starts_at"] == offer_two["starts_at"]
    booked = await tool(
        client, "book_appointment", "call-one", lead_id=lead_one, slot_id=offer_one["slot_id"], caller_confirmed=True
    )
    assert booked.json()["data"]["status"] == "confirmed"
    assert booked.json()["data"]["starts_at"].endswith("-07:00")
    denied = await tool(
        client, "book_appointment", "call-two", lead_id=lead_two, slot_id=offer_two["slot_id"], caller_confirmed=True
    )
    assert denied.json()["code"] == "SLOT_UNAVAILABLE"
    async with app.state.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Appointment)) == 1


@pytest.mark.asyncio
async def test_explicit_null_optional_fields_allow_lead_and_callback(harness):
    """Composer can send null for facts not yet collected."""
    client, app, _ = harness
    call_id = "nullable-intake-call"
    result = await tool(
        client,
        "save_lead",
        call_id,
        callback_phone="6025550101",
        name=None,
        email=None,
        street_address=None,
        city=None,
        state=None,
        postal_code=None,
        issue_summary=None,
        authorized_contact=None,
    )
    body = result.json()
    assert result.status_code == 200
    assert body["ok"] is True
    assert body["data"]["saved"] is True
    lead_id = body["data"]["lead_id"]
    assert "street_address" in body["data"]["missing_fields"]

    callback = await tool(
        client,
        "request_callback",
        call_id,
        lead_id=lead_id,
        reason="Please follow up about an inspection",
        urgency="routine",
        preferred_callback_window=None,
    )
    assert callback.json()["ok"] is True
    assert callback.json()["data"]["status"] == "queued"
    async with app.state.sessions() as session:
        assert await session.scalar(select(func.count()).select_from(Lead)) == 1
        assert await session.scalar(select(func.count()).select_from(CallbackRequest)) == 1


@pytest.mark.asyncio
async def test_null_update_never_erases_confirmed_details(harness):
    client, app, _ = harness
    call_id = "nullable-update-call"
    first = await tool(
        client,
        "save_lead",
        call_id,
        callback_phone="6025550101",
        name="Test Caller",
        street_address="123 Test Street",
    )
    assert first.json()["ok"] is True
    lead_id = first.json()["data"]["lead_id"]

    update = await tool(
        client,
        "save_lead",
        call_id,
        lead_id=lead_id,
        callback_phone="6025550101",
        name=None,
        street_address=None,
        email=None,
    )
    assert update.json()["ok"] is True
    async with app.state.sessions() as session:
        lead = await session.get(Lead, lead_id)
        assert lead.name == "Test Caller"
        assert lead.street_address == "123 Test Street"


@pytest.mark.asyncio
async def test_invalid_blank_field_has_http_200_but_warning_business_code(harness, caplog):
    client, _, _ = harness
    with caplog.at_level(logging.WARNING, logger="roofing.api"):
        response = await tool(
            client,
            "save_lead",
            "blank-email-call",
            callback_phone="6025550101",
            email="",
        )
    assert response.status_code == 200
    assert response.json()["ok"] is False
    assert response.json()["code"] == "INVALID_EMAIL"
    assert "INVALID_EMAIL" in caplog.text
    assert "6025550101" not in caplog.text
