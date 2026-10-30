"""Cal.com integration is tested against a local mock, never a real calendar."""

from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.calcom_calendar import CalComCalendar, CalendarUnavailable
from app.config import Settings
from app.main import create_app

ZONE = ZoneInfo("America/Phoenix")


def endpoint_payload(name, call_id, **args):
    return {"name": name, "call": {"call_id": call_id}, "args": args}


def make_provider(day, *, conflict=False, timeout=False):
    start = datetime.combine(day, time(11), ZONE)
    end = start + timedelta(hours=1)
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request))
        if request.url.path == "/v2/slots":
            assert request.headers["cal-api-version"] == "2024-09-04"
            assert request.url.params["format"] == "range"
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        day.isoformat(): [
                            {"start": start.isoformat(), "end": end.isoformat()}
                        ]
                    },
                },
            )
        if request.url.path == "/v2/bookings" and request.method == "GET":
            assert request.headers["cal-api-version"] == "2026-05-01"
            return httpx.Response(200, json={"status": "success", "data": [], "pagination": {"hasMore": False}})
        if request.url.path == "/v2/bookings" and request.method == "POST":
            assert request.headers["cal-api-version"] == "2026-02-25"
            if timeout:
                raise httpx.ConnectError("Test timeout", request=request)
            if conflict:
                return httpx.Response(409, json={"status": "error"})
            import json

            details = json.loads(request.content)
            assert details["eventTypeId"] == 123
            assert details["attendee"]["email"] == "demo@example.com"
            assert details["metadata"]["goodlookinOperationId"]
            assert "allowConflicts" not in details
            assert "allowBookingOutOfBounds" not in details
            return httpx.Response(
                201,
                json={
                    "status": "success",
                    "data": {
                        "uid": "demo-booking-123",
                        "status": "accepted",
                        "start": start.astimezone(UTC).isoformat(),
                        "end": end.astimezone(UTC).isoformat(),
                    },
                },
            )
        if request.url.path == "/v2/bookings/demo-booking-123":
            assert request.headers["cal-api-version"] == "2026-05-01"
            return httpx.Response(
                200,
                json={
                    "status": "success",
                    "data": {
                        "uid": "demo-booking-123",
                        "status": "accepted",
                        "start": start.astimezone(UTC).isoformat(),
                        "end": end.astimezone(UTC).isoformat(),
                    },
                },
            )
        raise AssertionError("Unexpected Cal.com API request")

    client = httpx.AsyncClient(base_url="https://api.cal.com", transport=httpx.MockTransport(handler))
    return CalComCalendar("fake-test-api-key", 123, client), calls


async def setup_lead(client, call_id, *, include_email=True):
    data = {
        "callback_phone": "6025550101",
        "name": "Demo Caller",
        "street_address": "123 Test Street",
        "city": "Phoenix",
        "state": "AZ",
        "postal_code": "85001",
        "property_type": "residential",
        "service_type": "inspection",
        "issue_summary": "Request a roof inspection",
        "authorized_contact": True,
    }
    if include_email:
        data["email"] = "demo@example.com"
    response = await client.post(
        "/v1/retell/tools/save_lead", json=endpoint_payload("save_lead", call_id, **data)
    )
    assert response.json()["ok"] is True
    return response.json()["data"]["lead_id"]


@pytest.mark.asyncio
async def test_live_slots_and_confirmed_booking_are_idempotent(harness):
    _, app, _ = harness
    day = (datetime.now(ZONE) + timedelta(days=3)).date()
    provider, calls = make_provider(day)
    demo_app = create_app(app.state.settings, app.state.business, provider)
    async with AsyncClient(transport=ASGITransport(app=demo_app), base_url="http://test") as client:
        lead_id = await setup_lead(client, "calcom-demo")
        offer = await client.post(
            "/v1/retell/tools/get_available_appointments",
            json=endpoint_payload(
                "get_available_appointments", "calcom-demo", lead_id=lead_id, preferred_date=day.isoformat()
            ),
        )
        assert offer.json()["ok"] is True
        slots = offer.json()["data"]["slots"]
        assert len(slots) == 1
        assert slots[0]["starts_at"].startswith(day.isoformat())

        booking = await client.post(
            "/v1/retell/tools/book_appointment",
            json=endpoint_payload(
                "book_appointment", "calcom-demo", lead_id=lead_id,
                slot_id=slots[0]["slot_id"], caller_confirmed=True
            ),
        )
        assert booking.json()["ok"] is True
        assert booking.json()["data"]["status"] == "confirmed"
        assert booking.json()["data"]["booking_id"] == "demo-booking-123"

        repeat = await client.post(
            "/v1/retell/tools/book_appointment",
            json=endpoint_payload(
                "book_appointment", "calcom-demo", lead_id=lead_id,
                slot_id=slots[0]["slot_id"], caller_confirmed=True
            ),
        )
        assert repeat.json()["data"]["status"] == "confirmed"
        assert len([x for x in calls if x[0] == "POST"]) == 1
    await provider.client.aclose()


@pytest.mark.asyncio
async def test_conflict_returns_slot_unavailable(harness):
    _, app, _ = harness
    day = (datetime.now(ZONE) + timedelta(days=3)).date()
    provider, _ = make_provider(day, conflict=True)
    demo_app = create_app(app.state.settings, app.state.business, provider)
    async with AsyncClient(transport=ASGITransport(app=demo_app), base_url="http://test") as client:
        lead_id = await setup_lead(client, "conflict")
        available = await client.post(
            "/v1/retell/tools/get_available_appointments",
            json=endpoint_payload(
                "get_available_appointments", "conflict", lead_id=lead_id, preferred_date=day.isoformat()
            ),
        )
        slot = available.json()["data"]["slots"][0]["slot_id"]
        response = await client.post(
            "/v1/retell/tools/book_appointment",
            json=endpoint_payload(
                "book_appointment", "conflict", lead_id=lead_id, slot_id=slot, caller_confirmed=True
            ),
        )
        assert response.json()["code"] == "SLOT_UNAVAILABLE"
    await provider.client.aclose()


@pytest.mark.asyncio
async def test_ambiguous_provider_timeout_is_pending_and_not_reposted(harness):
    _, app, _ = harness
    day = (datetime.now(ZONE) + timedelta(days=3)).date()
    provider, calls = make_provider(day, timeout=True)
    demo_app = create_app(app.state.settings, app.state.business, provider)
    async with AsyncClient(transport=ASGITransport(app=demo_app), base_url="http://test") as client:
        lead_id = await setup_lead(client, "timeout")
        available = await client.post(
            "/v1/retell/tools/get_available_appointments",
            json=endpoint_payload(
                "get_available_appointments", "timeout", lead_id=lead_id, preferred_date=day.isoformat()
            ),
        )
        slot = available.json()["data"]["slots"][0]["slot_id"]
        booking = await client.post(
            "/v1/retell/tools/book_appointment",
            json=endpoint_payload(
                "book_appointment", "timeout", lead_id=lead_id, slot_id=slot, caller_confirmed=True
            ),
        )
        assert booking.json()["data"]["status"] == "pending"
        repeat = await client.post(
            "/v1/retell/tools/book_appointment",
            json=endpoint_payload(
                "book_appointment", "timeout", lead_id=lead_id, slot_id=slot, caller_confirmed=True
            ),
        )
        assert repeat.json()["data"]["status"] == "pending"
        assert len([x for x in calls if x[0] == "POST"]) == 1
    await provider.client.aclose()


@pytest.mark.asyncio
async def test_missing_email_is_refused_before_booking_post(harness):
    _, app, _ = harness
    day = (datetime.now(ZONE) + timedelta(days=3)).date()
    provider, calls = make_provider(day)
    demo_app = create_app(app.state.settings, app.state.business, provider)
    async with AsyncClient(transport=ASGITransport(app=demo_app), base_url="http://test") as client:
        lead_id = await setup_lead(client, "no-email", include_email=False)
        available = await client.post(
            "/v1/retell/tools/get_available_appointments",
            json=endpoint_payload(
                "get_available_appointments", "no-email", lead_id=lead_id, preferred_date=day.isoformat()
            ),
        )
        slot = available.json()["data"]["slots"][0]["slot_id"]
        booking = await client.post(
            "/v1/retell/tools/book_appointment",
            json=endpoint_payload(
                "book_appointment", "no-email", lead_id=lead_id, slot_id=slot, caller_confirmed=True
            ),
        )
        assert booking.json()["code"] == "BOOKING_CONTACT_DETAILS_REQUIRED"
        assert not [item for item in calls if item[0] == "POST"]
    await provider.client.aclose()


def test_calcom_config_requires_opt_in_credentials():
    with pytest.raises(ValueError, match="CALCOM_API_KEY"):
        Settings(
            app_env="test", database_url="sqlite+aiosqlite:///:memory:",
            calendar_provider="calcom", calcom_api_key=SecretStr(""), calcom_event_type_id=None
        )
