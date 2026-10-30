"""FAQ and ZIP coverage are deterministic, grounded, and caller-independent."""
from pathlib import Path

import pytest

from app.business import BusinessConfig
from app.knowledge import search_knowledge, service_area_check


async def tool(client, name, call_id, **args):
    return await client.post(
        f"/v1/retell/tools/{name}",
        json={"name": name, "call": {"call_id": call_id}, "args": args},
    )



@pytest.fixture
def demo_business():
    return BusinessConfig.load(Path(__file__).resolve().parents[1] / "app" / "business.sales-demo.json")


def test_faq_returns_only_approved_verbatim_answer(demo_business):
    output = search_knowledge(demo_business, "What are your business hours?")
    assert output["found"]
    assert any("Monday to Friday" in result["answer"] for result in output["matches"])
    assert all(result["source_id"].startswith("approved_faq:") for result in output["matches"])


def test_faq_does_not_guess_missing_answer(demo_business):
    output = search_knowledge(demo_business, "Do you give customers solar panels?")
    assert output["found"] is False
    assert output["matches"] == []


@pytest.mark.parametrize(
    ("state", "postal_code", "service_type", "expected"),
    [
        ("AZ", "85251", "inspection", "in_area"),
        ("AZ", "85252", "inspection", "outside_area"),
        ("CA", "85251", "inspection", "outside_area"),
        ("AZ", "not-a-zip", "inspection", "needs_review"),
        ("AZ", "85251", None, "needs_review"),
        ("AZ", "85251", "roof installation", "needs_review"),
    ],
)
def test_service_area_is_not_decided_by_language_model(demo_business, state, postal_code, service_type, expected):
    assert service_area_check(demo_business, state, postal_code, service_type)["status"] == expected


@pytest.mark.asyncio
async def test_new_retell_tools_through_signed_backend_contract(harness):
    client, app, _ = harness
    business = app.state.business
    object.__setattr__(
        business, "approved_faqs", [{"question": "What hours are you open?", "answer": "Nine to five."}]
    )
    found = await tool(client, "search_knowledge", "knowledge-call", question="What hours are you open?")
    assert found.json()["ok"] is True
    assert found.json()["data"]["matches"][0]["answer"] == "Nine to five."
    coverage = await tool(
        client, "check_service_area", "knowledge-call", state="AZ", postal_code="85001", service_type="inspection"
    )
    assert coverage.json()["ok"] is True
    assert coverage.json()["data"]["status"] == "in_area"
