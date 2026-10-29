"""Guardrails for the local-only demo and its synthetic business fixture."""
from pathlib import Path

import pytest

from app.business import BusinessConfig
from scripts.demo_smoke import require_local_base_url


@pytest.mark.parametrize("url", [
    "http://127.0.0.1:8000",
    "http://localhost:8000/",
    "http://[::1]:8000",
])
def test_demo_targets_are_loopback(url):
    assert require_local_base_url(url)


@pytest.mark.parametrize("url", [
    "https://example.com",
    "http://roofingcompany.com",
    "http://127.0.0.1.evil.com",
    "http://127.0.0.1:8000/other",
    "http://localhost:8000?secret=1",
    "https://localhost:8000",
])
def test_demo_rejects_external_or_unexpected_targets(url):
    with pytest.raises(ValueError):
        require_local_base_url(url)


def test_fictional_demo_fixture_is_explicitly_nonproduction():
    path = Path(__file__).resolve().parents[1] / "app" / "business.demo.json"
    config = BusinessConfig.load(path)
    assert config.company_name.startswith("FICTIONAL ROOFING DEMO")
    assert config.booking_enabled
    assert not config.transfer_available
    assert config.transfer_phone_number is None
    assert config.eligible_postal_codes == ["85001"]
    assert "no callback will be made" in config.callback_expectation
