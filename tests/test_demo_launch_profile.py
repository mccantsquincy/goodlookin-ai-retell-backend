"""Activation of external booking is explicit and confined to a demo-only business config."""

from pathlib import Path

from app.business import BusinessConfig
from app.demo_dashboard import dashboard_html

BASE = Path(__file__).resolve().parents[1] / "app"


def test_demo_profiles_fail_closed_until_explicit_runtime_selection():
    safe = BusinessConfig.load(BASE / "business.sales-demo.json")
    live = BusinessConfig.load(BASE / "business.sales-demo.bookable.json")
    assert safe.booking_enabled is False
    assert live.booking_enabled is True
    assert live.company_name == "Summit Roofing (Fictional Demo)"
    assert live.eligible_states == safe.eligible_states
    assert live.eligible_postal_codes == safe.eligible_postal_codes
    assert live.eligible_services == safe.eligible_services
    assert live.approved_faqs == safe.approved_faqs
    assert live.callback_expectation == safe.callback_expectation


def test_dashboard_never_labels_external_booking_data_as_only_simulated():
    counts = {"calls": 0, "leads": 0, "callbacks": 0, "appointments": 1}
    offline = dashboard_html(counts, [], "fake")
    external = dashboard_html(counts, [], "calcom")
    assert "Simulated appointments" in offline
    assert "Appointment records (mixed history)" in external
    assert "historical simulated tests" in external
    assert "Goodlookin Demo Google Calendar" in external
