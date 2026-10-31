"""Calendar admin access is separate from the public receptionist and demo dashboard."""

import base64
import json

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
    encoded = base64.b64encode(f"provisioner:{password}".encode()).decode()
    return {"Authorization": f"Basic {encoded}"}


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
    assert (await client.post(ACTION, headers={"Origin": "http://test"})).status_code == 404


def build_app(harness, provider_client=None):
    _, app, _ = harness
    config = Settings(
        app_env="test",
        database_url=app.state.settings.database_url,
        retell_signature_bypass=True,
        provisioning_admin_password=SecretStr(SECRET),
        calcom_provisioning_api_key=SecretStr("mock-api-token"),
        calcom_schedules_api_version="2024-06-11",
        calcom_event_types_api_version="2026-06-12",
    )
    return create_app(config, app.state.business, app.state.calendar, provisioning_client=provider_client)


@pytest.mark.asyncio
async def test_review_is_authenticated_and_offline(harness):
    api = build_app(harness)
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        unauth = await client.get(PATH)
        assert unauth.status_code == 401
        assert "WWW-Authenticate" in unauth.headers
        wrong = await client.get(PATH, headers=auth("wrong"))
        assert wrong.status_code == 401
        review = await client.get(PATH, headers=auth())
        assert review.status_code == 200
        assert "summit-roofing-demo" in review.text
        assert "PROVISION summit-roofing-demo" in review.text
        assert "mock-api-token" not in review.text
        assert "goodlookinOperationId" not in review.text
        assert review.headers["Cache-Control"] == "no-store"
        assert "frame-ancestors" in review.headers["Content-Security-Policy"]


def extract_form_values(html):
    import re

    def get(name):
        found = re.search(r'name="' + name + r'" value="([^"]+)"', html)
        assert found is not None
        return found.group(1)

    return {
        "draft_sha256": get("draft_sha256"),
        "csrf_token": get("csrf_token"),
        "approve": "yes",
        "confirmation": "PROVISION summit-roofing-demo",
    }


@pytest.mark.asyncio
async def test_rejects_csrf_origin_missing_approval_and_stale_draft(harness):
    called = []

    def handler(request):
        called.append(request)
        raise AssertionError("No Cal.com calls should be made")

    async with httpx.AsyncClient(base_url="https://api.cal.com", transport=httpx.MockTransport(handler)) as cal:
        api = build_app(harness, cal)
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
            review = await client.get(PATH, headers=auth())
            fields = extract_form_values(review.text)
            no_origin_with_bad_csrf = await client.post(
                ACTION, headers=auth(), data={**fields, "csrf_token": "invalid"}
            )
            assert no_origin_with_bad_csrf.status_code == 400
            wrong_origin = await client.post(
                ACTION, headers={**auth(), "Origin": "http://attacker.invalid"}, data=fields
            )
            assert wrong_origin.status_code == 403
            null_origin = await client.post(
                ACTION, headers={**auth(), "Origin": "null"}, data=fields
            )
            assert null_origin.status_code == 403
            cross_site = await client.post(
                ACTION, headers={**auth(), "Sec-Fetch-Site": "cross-site"}, data=fields
            )
            assert cross_site.status_code == 403
            same_site = await client.post(
                ACTION, headers={**auth(), "Sec-Fetch-Site": "same-site"}, data=fields
            )
            assert same_site.status_code == 403
            no_approval = await client.post(
                ACTION,
                headers={**auth(), "Origin": "http://test"},
                data={**fields, "confirmation": "wrong"},
            )
            assert no_approval.status_code == 400
            stale_draft = await client.post(
                ACTION,
                headers={**auth(), "Origin": "http://test"},
                data={**fields, "draft_sha256": "0" * 64},
            )
            assert stale_draft.status_code == 400
        assert called == []


@pytest.mark.asyncio
async def test_admin_provisions_only_after_confirmed_review_and_does_not_double_create(harness):
    schedules = []
    event_types = []
    posts = []

    def handler(request):
        payload = json.loads(request.content) if request.method == "POST" else None
        if request.method == "GET" and request.url.path == "/v2/schedules":
            return httpx.Response(200, json={"status": "success", "data": schedules})
        if request.method == "GET" and request.url.path == "/v2/event-types":
            return httpx.Response(200, json={"status": "success", "data": event_types})
        if request.method == "POST" and request.url.path == "/v2/schedules":
            assert request.headers["cal-api-version"] == "2024-06-11"
            posts.append("schedule")
            schedules.append({"id": 322, **payload})
            return httpx.Response(201, json={"status": "success", "data": schedules[-1]})
        if request.method == "POST" and request.url.path == "/v2/event-types":
            assert request.headers["cal-api-version"] == "2026-06-12"
            posts.append("event")
            event_types.append({"id": 655, **payload})
            return httpx.Response(201, json={"status": "success", "data": event_types[-1]})
        raise AssertionError(f"Unexpected URL: {request.url.path}")

    async with httpx.AsyncClient(base_url="https://api.cal.com", transport=httpx.MockTransport(handler)) as cal:
        api = build_app(harness, cal)
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
            review = await client.get(PATH, headers=auth())
            fields = extract_form_values(review.text)
            # Browser privacy settings may omit Origin entirely. Its reviewed,
            # HMAC-signed form still works and cannot be forged cross-site.
            headers = {**auth(), "Sec-Fetch-Site": "same-origin"}
            first = await client.post(ACTION, headers=headers, data=fields)
            assert first.status_code == 200
            assert "&quot;event_type_id&quot;: 655" in first.text
            assert "Bookings remain disabled" in first.text
            second = await client.post(
                ACTION, headers={**auth(), "Origin": "http://test"}, data=fields
            )
            assert second.status_code == 200
            assert "&quot;event_action&quot;: &quot;existing&quot;" in second.text
    assert posts == ["schedule", "event"]


@pytest.mark.asyncio
async def test_production_never_exposes_admin_even_with_secret(harness):
    api = build_app(harness)
    api.state.settings.app_env = "production"
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
        assert (await client.get(PATH, headers=auth())).status_code == 404


@pytest.mark.asyncio
async def test_no_admin_secret_reflected_in_error_response(harness):
    def handler(request):
        if request.method == "GET":
            return httpx.Response(503, json={"status": "error", "message": "private data"})
        raise AssertionError("POST should not occur")

    async with httpx.AsyncClient(base_url="https://api.cal.com", transport=httpx.MockTransport(handler)) as cal:
        api = build_app(harness, cal)
        async with AsyncClient(transport=ASGITransport(app=api), base_url="http://test") as client:
            review = await client.get(PATH, headers=auth())
            fields = extract_form_values(review.text)
            response = await client.post(ACTION, headers={**auth(), "Origin": "http://test"}, data=fields)
            assert response.status_code == 200
            assert "could not be verified" in response.text
            assert "private data" not in response.text
            assert "mock-api-token" not in response.text


@pytest.mark.asyncio
async def test_trusted_railway_origin_accepts_public_browser_origin_with_private_proxy_host(harness):
    api = build_app(harness)
    api.state.settings.provisioning_admin_origin = "https://api-staging-dce8.up.railway.app"
    async with AsyncClient(transport=ASGITransport(app=api), base_url="http://private-proxy.internal") as client:
        review = await client.get(PATH, headers=auth())
        fields = extract_form_values(review.text)
        # Valid public Origin must get past origin-check to the (intentionally
        # invalid) signed draft validation, rather than produce a 403.
        valid_public_origin = await client.post(
            ACTION,
            headers={**auth(), "Origin": "https://api-staging-dce8.up.railway.app"},
            data={**fields, "csrf_token": "incorrect"},
        )
        assert valid_public_origin.status_code == 400
        foreign_origin = await client.post(
            ACTION,
            headers={**auth(), "Origin": "https://attacker.example"},
            data=fields,
        )
        assert foreign_origin.status_code == 403


def test_rejects_invalid_provisioning_admin_origin():
    with pytest.raises(ValueError, match="exact HTTPS origin"):
        Settings(
            app_env="test",
            database_url="sqlite+aiosqlite:///:memory:",
            provisioning_admin_origin="https://attacker.example/callback",
        )
