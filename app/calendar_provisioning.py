"""Explicitly approved, idempotent Cal.com calendar resource provisioning.

Management-only module: never exposed as an unauthenticated API endpoint.
No API requests occur for the default preview/draft workflow.
"""

import re
from datetime import time
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from pydantic import BaseModel, ConfigDict, Field, model_validator


class ProvisioningError(Exception):
    """Safe error category. Never include tokens or provider response bodies."""


Day = Literal["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"]


class AvailabilityWindow(BaseModel):
    model_config = ConfigDict(extra="forbid")
    days: list[Day] = Field(min_length=1)
    start_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end_time: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")

    @model_validator(mode="after")
    def ordered(self) -> "AvailabilityWindow":
        if time.fromisoformat(self.start_time) >= time.fromisoformat(self.end_time):
            raise ValueError("Availability start must precede end on the same day")
        return self

    def api_value(self) -> dict:
        return {"days": self.days, "startTime": self.start_time, "endTime": self.end_time}


class ClientCalendarDraft(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: Literal[1] = 1
    instance_key: str = Field(pattern=r"^[a-z][a-z0-9-]{2,47}$")
    calendar_account_ref: str = Field(pattern=r"^[a-z][a-z0-9-]{2,47}$")
    company_name: str = Field(min_length=3, max_length=120)
    event_title: str = Field(min_length=3, max_length=120)
    event_slug: str = Field(pattern=r"^[a-z][a-z0-9-]{2,80}$")
    event_description: str = Field(min_length=8, max_length=800)
    event_length_minutes: int = Field(ge=15, le=240)
    time_zone: str = Field(min_length=3, max_length=80)
    availability: list[AvailabilityWindow] = Field(min_length=1)
    destination_calendar_ref: str = Field(min_length=3, max_length=255)
    approved: bool = False
    demo_only: bool = True

    @model_validator(mode="after")
    def check_identifiers(self) -> "ClientCalendarDraft":
        if not self.event_slug.startswith(f"gl-{self.instance_key}-"):
            raise ValueError("Event slug must be namespaced to the instance")
        if not self.demo_only:
            raise ValueError("Real-client provisioning requires separate authenticated tenant onboarding")
        try:
            ZoneInfo(self.time_zone)
        except ZoneInfoNotFoundError as exc:
            raise ValueError("Unsupported time zone") from exc
        return self

    @property
    def schedule_name(self) -> str:
        return f"goodlookin-{self.instance_key}-schedule"

    def schedule_payload(self) -> dict:
        return {
            "name": self.schedule_name,
            "timeZone": self.time_zone,
            "isDefault": False,
            "availability": [row.api_value() for row in self.availability],
        }

    def event_payload(self, schedule_id: int) -> dict:
        return {
            "lengthInMinutes": self.event_length_minutes,
            "title": self.event_title,
            "slug": self.event_slug,
            "description": self.event_description,
            "scheduleId": schedule_id,
        }

    def preview(self) -> dict:
        return {
            "instance_key": self.instance_key,
            "calendar_account_ref": self.calendar_account_ref,
            "schedule": self.schedule_payload(),
            "event_type": self.event_payload(0),
            "destination_calendar_ref": self.destination_calendar_ref,
            "mutates_provider": False,
            "requires_review": True,
            "approval_recorded": self.approved,
        }


class CalComProvisioner:
    """Client supplied per-account API key; never store it in client configs."""

    def __init__(
        self,
        api_key: str,
        schedule_api_version: str,
        event_api_version: str,
        client: httpx.AsyncClient,
    ) -> None:
        if not api_key:
            raise ValueError("Provisioning API key is required")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", schedule_api_version):
            raise ValueError("An explicit Cal.com schedules API version is required")
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", event_api_version):
            raise ValueError("An explicit Cal.com event-types API version is required")
        self._key = api_key
        self._schedule_version = schedule_api_version
        self._event_version = event_api_version
        self._client = client

    async def _call(self, method: str, path: str, version: str, *, payload: dict | None = None) -> dict:
        try:
            response = await self._client.request(
                method,
                path,
                headers={
                    "Authorization": f"Bearer {self._key}",
                    "cal-api-version": version,
                    "Content-Type": "application/json",
                },
                json=payload,
            )
        except httpx.RequestError as exc:
            raise ProvisioningError("Cal.com request outcome uncertain; inspect remote resources before retry") from exc
        if not 200 <= response.status_code < 300:
            raise ProvisioningError(f"Cal.com {method} {path} returned HTTP {response.status_code}; no automatic retry")
        try:
            result = response.json()
            if result["status"] != "success":
                raise ValueError("Unsuccessful provider response")
            return result
        except (KeyError, TypeError, ValueError) as exc:
            raise ProvisioningError("Unrecognized Cal.com success response; verify resources manually") from exc

    async def _list(self, path: str, version: str) -> list[dict]:
        result = await self._call("GET", path, version)
        data = result.get("data")
        if not isinstance(data, list) or not all(isinstance(item, dict) for item in data):
            raise ProvisioningError("Unexpected provider resource list shape")
        pagination = result.get("pagination") or {}
        if pagination.get("hasMore"):
            raise ProvisioningError("Provider returned a partial resource list; refusing automatic creation")
        return data

    @staticmethod
    def _unique(items: list[dict], key: str, expected: str) -> dict | None:
        matches = [row for row in items if row.get(key) == expected]
        if len(matches) > 1:
            raise ProvisioningError("Ambiguous matching resources; manual inspection required")
        return matches[0] if matches else None

    async def apply(self, draft: ClientCalendarDraft, confirmed_instance: str) -> dict:
        if not draft.approved or not draft.demo_only or confirmed_instance != draft.instance_key:
            raise ProvisioningError("Demo-only provisioning requires approved draft and explicit instance confirmation")
        schedules = await self._list("/v2/schedules", self._schedule_version)
        existing_schedule = self._unique(schedules, "name", draft.schedule_name)
        schedule_state = "existing"
        if existing_schedule:
            required = draft.schedule_payload()
            if existing_schedule.get("timeZone") != required["timeZone"]:
                raise ProvisioningError("Existing schedule has a different time zone; no update performed")
            if existing_schedule.get("availability") != required["availability"]:
                raise ProvisioningError("Existing schedule has different availability; no update performed")
            schedule_id = existing_schedule.get("id")
        else:
            created = await self._call(
                "POST", "/v2/schedules", self._schedule_version, payload=draft.schedule_payload()
            )
            schedule_id = (created.get("data") or {}).get("id")
            schedule_state = "created"
        if not isinstance(schedule_id, int) or schedule_id <= 0:
            raise ProvisioningError("Provider did not supply a valid schedule ID")

        event_types = await self._list("/v2/event-types", self._event_version)
        existing_event = self._unique(event_types, "slug", draft.event_slug)
        event_state = "existing"
        if existing_event:
            if (
                existing_event.get("title") != draft.event_title
                or existing_event.get("lengthInMinutes") != draft.event_length_minutes
                or existing_event.get("scheduleId") != schedule_id
            ):
                raise ProvisioningError("Event slug collides with different settings; no changes performed")
            event_id = existing_event.get("id")
        else:
            created = await self._call(
                "POST", "/v2/event-types", self._event_version, payload=draft.event_payload(schedule_id)
            )
            event_id = (created.get("data") or {}).get("id")
            event_state = "created"
        if not isinstance(event_id, int) or event_id <= 0:
            raise ProvisioningError("Provider did not supply a valid event type ID")
        return {
            "instance_key": draft.instance_key,
            "calendar_account_ref": draft.calendar_account_ref,
            "schedule_id": schedule_id,
            "schedule_action": schedule_state,
            "event_type_id": event_id,
            "event_action": event_state,
            "destination_calendar_ref": draft.destination_calendar_ref,
            "destination_calendar_verified": False,
            "bookings_enabled": False,
        }
