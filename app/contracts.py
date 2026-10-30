from datetime import date
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class RetellCall(StrictModel):
    # Retell sends the full call object; only its ID is needed by the backend.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    call_id: str = Field(min_length=1, max_length=128)


class ToolRequest(StrictModel):
    name: str
    call: RetellCall
    args: dict[str, Any]


class ToolResponse(BaseModel):
    ok: bool
    code: str
    data: dict[str, Any]
    request_id: str


class SearchKnowledgeArgs(StrictModel):
    question: str = Field(min_length=2, max_length=500)


class ServiceAreaArgs(StrictModel):
    state: str = Field(min_length=2, max_length=40)
    postal_code: str = Field(min_length=1, max_length=10)
    service_type: str | None = Field(default=None, max_length=120)


class EmptyArgs(StrictModel):
    pass


class SaveLeadArgs(StrictModel):
    lead_id: str | None = None
    name: str | None = Field(default=None, max_length=200)
    callback_phone: str = Field(min_length=3, max_length=40)
    email: str | None = Field(default=None, max_length=320)
    street_address: str | None = Field(default=None, max_length=300)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = None
    postal_code: str | None = None
    property_type: Literal["residential", "commercial"] | None = None
    service_type: str | None = Field(default=None, max_length=120)
    issue_summary: str | None = Field(default=None, max_length=4000)
    authorized_contact: bool | None = None
    active_leak: bool | None = None


class LeadIdArgs(StrictModel):
    lead_id: str = Field(min_length=1)


class AvailabilityArgs(LeadIdArgs):
    preferred_date: date


class BookArgs(LeadIdArgs):
    slot_id: str = Field(min_length=1)
    caller_confirmed: bool


class BookingStatusArgs(LeadIdArgs):
    operation_id: str | None = None


class CallbackArgs(LeadIdArgs):
    reason: str = Field(min_length=1, max_length=1000)
    urgency: Literal["routine", "urgent"]
    preferred_callback_window: str | None = Field(default=None, max_length=200)


ARG_MODELS = {
    "get_business_context": EmptyArgs,
    "search_knowledge": SearchKnowledgeArgs,
    "check_service_area": ServiceAreaArgs,
    "save_lead": SaveLeadArgs,
    "check_service_eligibility": LeadIdArgs,
    "get_available_appointments": AvailabilityArgs,
    "book_appointment": BookArgs,
    "get_booking_status": BookingStatusArgs,
    "request_callback": CallbackArgs,
}
