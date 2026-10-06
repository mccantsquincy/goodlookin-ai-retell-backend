import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from app.db import Lead

ZONE = ZoneInfo("America/Phoenix")
REQUIRED_INTAKE = (
    "callback_phone",
    "street_address",
    "city",
    "state",
    "postal_code",
    "property_type",
    "service_type",
    "issue_summary",
    "authorized_contact",
)


@dataclass(frozen=True)
class BusinessConfig:
    company_name: str
    business_hours: dict
    services: list[str]
    approved_faqs: list[dict]
    booking_enabled: bool
    transfer_available: bool
    transfer_phone_number: str | None
    callback_expectation: str
    eligible_states: list[str]
    eligible_postal_codes: list[str]
    eligible_services: list[str]
    appointment_type: str | None

    def __post_init__(self) -> None:
        if self.transfer_available and not self.transfer_phone_number:
            raise ValueError("transfer phone number is required when transfer is available")

    @classmethod
    def load(cls, path: Path) -> "BusinessConfig":
        return cls(**json.loads(path.read_text()))

    def context(self, at: datetime) -> dict:
        return {
            "company_name": self.company_name,
            "timezone": "America/Phoenix",
            "current_local_datetime": at.astimezone(ZONE).isoformat(),
            "business_hours": self.business_hours,
            "services": self.services,
            "approved_faqs": self.approved_faqs,
            "booking_enabled": self.booking_enabled,
            "transfer_available": self.transfer_available,
            "transfer_phone_number": self.transfer_phone_number,
            "callback_expectation": self.callback_expectation,
        }


def missing_fields(lead: Lead) -> list[str]:
    return [field for field in REQUIRED_INTAKE if getattr(lead, field) is None or getattr(lead, field) == ""]


def eligibility(lead: Lead, config: BusinessConfig) -> dict:
    if missing_fields(lead):
        return {"eligibility": "needs_review", "reason_code": "INTAKE_INCOMPLETE", "appointment_type": None}
    if not (
        config.eligible_states and config.eligible_postal_codes and config.eligible_services and config.appointment_type
    ):
        return {"eligibility": "needs_review", "reason_code": "POLICY_NOT_CONFIGURED", "appointment_type": None}
    if not lead.authorized_contact:
        return {"eligibility": "needs_review", "reason_code": "CONTACT_AUTHORIZATION_REVIEW", "appointment_type": None}
    if lead.state.upper() not in {x.upper() for x in config.eligible_states}:
        return {"eligibility": "ineligible", "reason_code": "STATE_OUTSIDE_CONFIGURED_AREA", "appointment_type": None}
    if lead.postal_code not in config.eligible_postal_codes:
        return {
            "eligibility": "ineligible",
            "reason_code": "POSTAL_CODE_OUTSIDE_CONFIGURED_AREA",
            "appointment_type": None,
        }
    if lead.service_type.casefold() not in {x.casefold() for x in config.eligible_services}:
        return {"eligibility": "ineligible", "reason_code": "SERVICE_NOT_CONFIGURED", "appointment_type": None}
    return {"eligibility": "eligible", "reason_code": "CONFIGURED_MATCH", "appointment_type": config.appointment_type}
