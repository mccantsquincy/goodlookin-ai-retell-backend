"""Management-only calendar provisioning must be safe to preview and repeat."""

import json
from pathlib import Path

import httpx
import pytest

from app.calendar_provisioning import CalComProvisioner, ClientCalendarDraft, ProvisioningError

FIXTURE = Path(__file__).resolve().parents[1] / "app" / "instances" / "summit-roofing-demo.json"


def draft():
    return ClientCalendarDraft.model_validate_json(FIXTURE.read_text())


def test_preview_is_safe_and_requires_approval():
    plan = draft().preview()
    assert plan["mutates_provider"] is False
    assert plan["approval_recorded"] is False
    assert plan["event_type"]["scheduleId"] == 0
    assert plan["calendar_account_ref"] == "goodlookin-demo-account"
    assert "API_KEY" not in json.dumps(plan)


@pytest.mark.parametrize("updates", [{"time_zone": "Invalid/TimeZone"}, {"event_slug": "wrong-slug"}])
def test_rejects_bad_proposals(updates):
    data = draft().model_dump()
    data.update(updates)
    with pytest.raises(ValueError):
        ClientCalendarDraft.model_validate(data)


@pytest.mark.asyncio
async def test_requires_approval_before_any_network_call():
    called = []

    def handler(request):
        called.append(request)
        raise AssertionError("Must not reach the network")

    async with httpx.AsyncClient(base_url="https://api.cal.com", transport=httpx.MockTransport(handler)) as c:
        p = CalComProvisioner("fake-key", "2024-06-11", "2024-06-14", c)
        with pytest.raises(ProvisioningError, match="approved draft"):
            await p.apply(draft(), "summit-roofing-demo")
        assert called == []


@pytest.mark.asyncio
async def test_approved_provisioning_is_idempotent_and_scoped():
    settings = draft().model_copy(update={"approved": True})
    schedule_id, event_id = 987, 654
    schedules = []
    events = []
    writes = []

    def handler(request):
        assert request.headers["Authorization"] == "Bearer fake-key"
        payload = json.loads(request.content) if request.method == "POST" else None
        if request.method == "GET" and request.url.path == "/v2/schedules":
            return httpx.Response(200, json={"status": "success", "data": schedules})
        if request.method == "POST" and request.url.path == "/v2/schedules":
            writes.append("schedule")
            assert payload == settings.schedule_payload()
            schedules.append({"id": schedule_id, **payload})
            return httpx.Response(201, json={"status": "success", "data": schedules[-1]})
        if request.method == "GET" and request.url.path == "/v2/event-types":
            return httpx.Response(200, json={"status": "success", "data": events})
        if request.method == "POST" and request.url.path == "/v2/event-types":
            writes.append("event")
            assert payload == settings.event_payload(schedule_id)
            events.append({"id": event_id, **payload})
            return httpx.Response(201, json={"status": "success", "data": events[-1]})
        raise AssertionError("Unexpected endpoint")

    async with httpx.AsyncClient(base_url="https://api.cal.com", transport=httpx.MockTransport(handler)) as c:
        p = CalComProvisioner("fake-key", "2024-06-11", "2024-06-14", c)
        first = await p.apply(settings, settings.instance_key)
        second = await p.apply(settings, settings.instance_key)
    assert first["event_type_id"] == event_id
    assert first["schedule_action"] == "created"
    assert second["event_action"] == "existing"
    assert second["bookings_enabled"] is False
    assert writes == ["schedule", "event"]


@pytest.mark.asyncio
async def test_refuses_existing_slug_collision():
    settings = draft().model_copy(update={"approved": True})
    events = [{"id": 12, "slug": settings.event_slug, "title": "Unrelated event"}]

    def handler(request):
        data = [{"id": 10, **settings.schedule_payload()}] if request.url.path.endswith("/schedules") else events
        if request.method != "GET":
            raise AssertionError("Never mutate on slug collision")
        return httpx.Response(200, json={"status": "success", "data": data})

    async with httpx.AsyncClient(base_url="https://api.cal.com", transport=httpx.MockTransport(handler)) as c:
        p = CalComProvisioner("fake-key", "2024-06-11", "2024-06-14", c)
        with pytest.raises(ProvisioningError, match="collides"):
            await p.apply(settings, settings.instance_key)


@pytest.mark.asyncio
async def test_uncertain_create_is_not_retried():
    settings = draft().model_copy(update={"approved": True})
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json={"status": "success", "data": []})
        raise httpx.ReadTimeout("request may have committed", request=request)

    async with httpx.AsyncClient(base_url="https://api.cal.com", transport=httpx.MockTransport(handler)) as c:
        p = CalComProvisioner("fake-key", "2024-06-11", "2024-06-14", c)
        with pytest.raises(ProvisioningError, match="outcome uncertain"):
            await p.apply(settings, settings.instance_key)
        assert calls == ["GET", "POST"]
