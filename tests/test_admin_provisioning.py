"""Calendar admin writes require authenticated, short-lived, signed browser sessions."""

import base64
import json
import time

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from pydantic import SecretStr

from app.config import Settings
from app.main import create_app

SECRET = "A" * 40
PATH = "/admin/calendar"
ACTION = "/admin/calendar/provision"


def auth(password=SECRET):
    token = base64.b64encode(f"provisioner:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


def form_data(html):
    import re

    def get(name):
        match = re.search(r'name="' + name + r'" value="([^"]+)"', html)
        assert match is not None
        return match.group(1)

    return {
        "draft_sha256": get("draft_sha256"),
        "csrf_token": get("csrf_token"),
        "approve": "yes",
        "confirmation": "PROVISION summit-roofing-demo",
    }


def build_app(harness, provider_client=None, environment="test"):
    _, app, _ = harness
    config = Settings(
        app_env=environment,
        database_url=app.state.settings.database_url,
        retell_signature_bypass=environment == "test",
        provisioning_admin_password=SecretStr(SECRET),
        calcom_provisioning_api_key=SecretStr("mock-api-token"),
        calcom_schedules_api_version="2024-06-11",
        calcom_event_types_api_version="2026-06-12",
    )
    return create_app(config, app.state.business, app.state.calendar, provisioning_client=provider_client)


def test_short_admin_password_rejected():
    with pytest.raises(ValueError, match="at least 32"):
        Settings(
            app_env="test",
            database_url="sqlite+aiosqlite:///:memory:",
            provisioning_admin_password=SecretStr("1234"),
        )


@pytest.mark.asyncio
async def test_admin_hidden_when_not_configured(harness):
    client, _, _ = harness
    assert (await client.get(PATH)).status_code == 404
    assert (await client.post(ACTION)).status_code == 404


@pytest.mark.asyncio
async def test_review_requires_auth_and_issues_strict_cookie(harness):
    api = build_app(harness)
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        assert (await client.get(PATH)).status_code == 401
        assert (await client.get(PATH, headers=auth("wrong"))).status_code == 401
        response = await client.get(PATH, headers=auth())
        assert response.status_code == 200
        assert "summit-roofing-demo" in response.text
        assert "mock-api-token" not in response.text
        assert response.headers["Cache-Control"] == "no-store"
        cookie = response.headers["Set-Cookie"]
        assert "gl_calendar_approval=" in cookie
        assert "httponly" in cookie.lower()
        assert "samesite=strict" in cookie.lower()
        assert "max-age=900" in cookie.lower()
        assert "path=/admin/calendar" in cookie.lower()
        assert "secure" not in cookie.lower()

    # Staging sets Secure cookies only for HTTPS.
    with pytest.raises(ValueError, match="RETELL_API_KEY"):
        Settings(
            app_env="staging",
            database_url="sqlite+aiosqlite:///:memory:",
            provisioning_admin_password=SecretStr(SECRET),
        )


@pytest.mark.asyncio
async def test_forged_cross_site_form_lacks_required_session_cookie(harness):
    calls = []

    def handler(request):
        calls.append(request)
        raise AssertionError("Provider request must never run")

    async with httpx.AsyncClient(base_url="https://api.cal.com", transport=httpx.MockTransport(handler)) as cal:
        api = build_app(harness, cal)
        transport = ASGITransport(app=api)
        async with AsyncClient(transport=transport, base_url="http://test") as browser:
            review = await browser.get(PATH, headers=auth())
            fields = form_data(review.text)
            missing_confirmation = await browser.post(ACTION, headers=auth(), data={**fields, "confirmation": "WRONG"})
            assert missing_confirmation.status_code == 400
            invalid_signature = await browser.post(ACTION, headers=auth(), data={**fields, "csrf_token": "bad"})
            assert invalid_signature.status_code == 400

        # Simulate a cross-site attacker who has somehow copied form fields but
        # cannot send the victim's first-party, SameSite=Strict session cookie.
        async with AsyncClient(transport=transport, base_url="http://test") as attacker:
            blocked = await attacker.post(
                ACTION,
                headers={**auth(), "Origin": "https://attacker.invalid", "Sec-Fetch-Site": "cross-site"},
                data=fields,
            )
            assert blocked.status_code == 403
            assert "Approval session" in blocked.text
        assert calls == []


@pytest.mark.asyncio
async def test_expired_or_tampered_cookie_is_denied(harness):
    api = build_app(harness)
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        review = await client.get(PATH, headers=auth())
        fields = form_data(review.text)
        original = client.cookies["gl_calendar_approval"]
        client.cookies.set("gl_calendar_approval", "0." + "a" * 64, path="/admin/calendar")
        tampered = await client.post(ACTION, headers=auth(), data=fields)
        assert tampered.status_code == 403
        client.cookies.set("gl_calendar_approval", original, path="/admin/calendar")
        # Real expiration enforcement is server side, not just browser max-age.
        import unittest.mock as mock

        with mock.patch("app.admin_provisioning.time.time", return_value=time.time() + 901):
            expired = await client.post(ACTION, headers=auth(), data=fields)
        assert expired.status_code == 403


@pytest.mark.asyncio
async def test_admin_provisions_after_session_approval_and_is_idempotent(harness):
    schedules, event_types, posts = [], [], []

    def handler(request):
        payload = json.loads(request.content) if request.method == "POST" else None
        if request.method == "GET" and request.url.path == "/v2/schedules":
            return httpx.Response(200, json={"status": "success", "data": schedules})
        if request.method == "GET" and request.url.path == "/v2/event-types":
            return httpx.Response(200, json={"status": "success", "data": event_types})
        if request.method == "POST" and request.url.path == "/v2/schedules":
            posts.append("schedule")
            schedules.append({"id": 322, **payload})
            return httpx.Response(201, json={"status": "success", "data": schedules[-1]})
        if request.method == "POST" and request.url.path == "/v2/event-types":
            posts.append("event")
            event_types.append({"id": 655, **payload})
            return httpx.Response(201, json={"status": "success", "data": event_types[-1]})
        raise AssertionError(f"Unexpected provider URL: {request.url.path}")

    async with httpx.AsyncClient(base_url="https://api.cal.com", transport=httpx.MockTransport(handler)) as cal:
        api = build_app(harness, cal)
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as browser:
            review = await browser.get(PATH, headers=auth())
            fields = form_data(review.text)
            # Simulate origin behavior that triggered the staging failure.
            for browser_headers in [
                {**auth(), "Origin": "null", "Sec-Fetch-Site": "none"},
                {**auth(), "Origin": "http://test", "Sec-Fetch-Site": "same-origin"},
            ]:
                result = await browser.post(ACTION, headers=browser_headers, data=fields)
                assert result.status_code == 200
                assert "&quot;event_type_id&quot;: 655" in result.text
                assert "Bookings remain disabled" in result.text
    assert posts == ["schedule", "event"]


@pytest.mark.asyncio
async def test_production_hides_admin_and_provider_errors_are_sanitized(harness):
    api = build_app(harness)
    api.state.settings.app_env = "production"
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        assert (await client.get(PATH, headers=auth())).status_code == 404
    api.state.settings.app_env = "test"

    def handler(request):
        return httpx.Response(503, json={"status": "error", "secret": "must not leak"})

    async with httpx.AsyncClient(base_url="https://api.cal.com", transport=httpx.MockTransport(handler)) as cal:
        guarded = build_app(harness, cal)
        async with AsyncClient(transport=ASGITransport(app=guarded), base_url="http://test") as client:
            review = await client.get(PATH, headers=auth())
            result = await client.post(ACTION, headers=auth(), data=form_data(review.text))
            assert result.status_code == 200
            assert "could not be verified" in result.text
            assert "must not leak" not in result.text
            assert "mock-api-token" not in result.text
