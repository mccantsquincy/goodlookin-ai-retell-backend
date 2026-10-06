import os

os.environ["APP_ENV"] = "test"
os.environ["DATABASE_URL"] = "sqlite+aiosqlite:///:memory:"

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy.ext.asyncio import create_async_engine

from app.business import BusinessConfig
from app.calendar import FakeCalendar
from app.config import Settings
from app.db import Base
from app.main import create_app


@pytest.fixture
def business():
    return BusinessConfig(
        company_name="TEST ROOFING",
        business_hours={},
        services=["inspection"],
        approved_faqs=[],
        booking_enabled=True,
        transfer_available=False,
        transfer_phone_number=None,
        callback_expectation="The test office will review the request.",
        eligible_states=["AZ"],
        eligible_postal_codes=["85001"],
        eligible_services=["inspection"],
        appointment_type="inspection",
    )


@pytest.fixture
async def harness(tmp_path, business):
    url = f"sqlite+aiosqlite:///{tmp_path / 'test.db'}"
    engine = create_async_engine(url)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    await engine.dispose()
    calendar = FakeCalendar()
    app = create_app(
        Settings(app_env="test", database_url=url, retell_signature_bypass=True),
        business,
        calendar,
    )
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        yield client, app, calendar


async def tool(client, tool_name, call_id="call-1", **args):
    response = await client.post(
        f"/v1/retell/tools/{tool_name}",
        json={
            "name": tool_name,
            "call": {"call_id": call_id},
            "args": args,
        },
    )
    return response


async def full_lead(client, call_id="call-1", **overrides):
    args = dict(
        name="Pat",
        callback_phone="(602) 555-0101",
        street_address="123 Test St",
        city="Phoenix",
        state="AZ",
        postal_code="85001",
        property_type="residential",
        service_type="inspection",
        issue_summary="roof inspection",
        authorized_contact=True,
    )
    args.update(overrides)
    return (await tool(client, "save_lead", call_id, **args)).json()["data"]["lead_id"]
