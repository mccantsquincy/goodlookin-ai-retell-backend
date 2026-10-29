"""Staging operations preview must not expose caller records or permit public access."""

import base64

import pytest
from pydantic import SecretStr

from app.config import Settings
PASSWORD = "this-is-a-strong-test-password"


async def tool(client, tool_name: str, call_id: str, **args):
    return await client.post(
        f"/v1/retell/tools/{tool_name}",
        json={"name": tool_name, "call": {"call_id": call_id}, "args": args},
    )


def basic(user: str, password: str) -> dict[str, str]:
    token = base64.b64encode(f"{user}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.mark.asyncio
async def test_dashboard_disabled_by_default(harness):
    client, _, _ = harness
    assert (await client.get("/demo")).status_code == 404


@pytest.mark.asyncio
async def test_dashboard_rejects_bad_auth_and_stays_out_of_production(harness):
    client, app, _ = harness
    settings = app.state.settings
    settings.demo_dashboard_username = "owner"
    settings.demo_dashboard_password = SecretStr(PASSWORD)

    response = await client.get("/demo")
    assert response.status_code == 401
    assert response.headers["www-authenticate"].startswith("Basic")
    assert response.headers["cache-control"] == "no-store"
    assert (await client.get("/demo", headers=basic("owner", "wrong"))).status_code == 401
    assert (await client.get("/demo", headers={"Authorization": "Basic !invalid"})).status_code == 401

    settings.app_env = "production"
    assert (await client.get("/demo", headers=basic("owner", PASSWORD))).status_code == 404


@pytest.mark.asyncio
async def test_dashboard_only_shows_aggregates_and_anonymous_activity(harness):
    client, app, _ = harness
    app.state.settings.demo_dashboard_username = "owner"
    app.state.settings.demo_dashboard_password = SecretStr(PASSWORD)

    response = await client.get("/demo", headers=basic("owner", PASSWORD))
    assert response.status_code == 200
    assert "No call activity recorded yet" in response.text
    assert response.headers["cache-control"] == "no-store"

    lead_response = await tool(
        client,
        "save_lead",
        "pii-call-reference",
        name="Sensitive Customer",
        callback_phone="6025550101",
        email="private@example.com",
        street_address="123 Secret Address",
        issue_summary="Never display this roof problem",
    )
    assert lead_response.json()["ok"] is True
    lead_id = lead_response.json()["data"]["lead_id"]
    callback_response = await tool(
        client, "request_callback", "pii-call-reference", lead_id=lead_id,
        reason="Do not display private reason", urgency="routine"
    )
    assert callback_response.json()["ok"] is True

    result = await client.get("/demo", headers=basic("owner", PASSWORD))
    assert result.status_code == 200
    assert "Saved leads" in result.text
    assert "Callback requests queued" in result.text
    assert "Queued" in result.text
    assert "Simulated appointments" in result.text
    assert result.headers["x-content-type-options"] == "nosniff"
    for sensitive in (
        "Sensitive Customer", "6025550101", "private@example.com",
        "123 Secret Address", "Never display this roof problem",
        "Do not display private reason", "pii-call-reference", lead_id,
    ):
        assert sensitive not in result.text


def test_dashboard_credentials_require_both_sides_and_long_password():
    with pytest.raises(ValueError, match="DEMO_DASHBOARD"):
        Settings(
            app_env="test", database_url="sqlite+aiosqlite:///:memory:",
            demo_dashboard_username="owner", demo_dashboard_password="",
        )
    with pytest.raises(ValueError, match="DEMO_DASHBOARD"):
        Settings(
            app_env="test", database_url="sqlite+aiosqlite:///:memory:",
            demo_dashboard_username="owner", demo_dashboard_password="short",
        )
