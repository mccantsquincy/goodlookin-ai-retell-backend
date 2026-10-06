from types import SimpleNamespace

import pytest

from app.business import eligibility


@pytest.mark.parametrize(
    ("updates", "expected", "reason"),
    [
        ({}, "eligible", "CONFIGURED_MATCH"),
        ({"street_address": None}, "needs_review", "INTAKE_INCOMPLETE"),
        ({"authorized_contact": False}, "needs_review", "CONTACT_AUTHORIZATION_REVIEW"),
        ({"state": "NM"}, "ineligible", "STATE_OUTSIDE_CONFIGURED_AREA"),
        ({"postal_code": "85002"}, "ineligible", "POSTAL_CODE_OUTSIDE_CONFIGURED_AREA"),
        ({"service_type": "repair"}, "ineligible", "SERVICE_NOT_CONFIGURED"),
    ],
)
def test_eligibility_rules(business, updates, expected, reason):
    fields = dict(
        callback_phone="+16025550101",
        street_address="123 Test",
        city="Phoenix",
        state="AZ",
        postal_code="85001",
        property_type="residential",
        service_type="inspection",
        issue_summary="inspection",
        authorized_contact=True,
    )
    fields.update(updates)
    result = eligibility(SimpleNamespace(**fields), business)
    assert (result["eligibility"], result["reason_code"]) == (expected, reason)
    if expected != "eligible":
        assert result["appointment_type"] is None


def test_missing_policy_needs_review(business):
    fields = dict(
        callback_phone="+16025550101",
        street_address="123 Test",
        city="Phoenix",
        state="AZ",
        postal_code="85001",
        property_type="residential",
        service_type="inspection",
        issue_summary="inspection",
        authorized_contact=True,
    )
    from dataclasses import replace

    result = eligibility(SimpleNamespace(**fields), replace(business, eligible_postal_codes=[]))
    assert result["reason_code"] == "POLICY_NOT_CONFIGURED"
