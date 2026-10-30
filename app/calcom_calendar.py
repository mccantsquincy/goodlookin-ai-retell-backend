"""Cal.com v2 adapter for a dedicated, consented demo calendar.

No network calls occur unless CALENDAR_PROVIDER=calcom is configured.
Uncertain booking outcomes remain pending; never blindly repeat a POST.
"""

from datetime import UTC, date, datetime
from zoneinfo import ZoneInfo

import httpx
from sqlalchemy.ext.asyncio import AsyncSession

from app.calendar import CalendarBooking, CalendarCommittedTimeout, SlotOffer, SlotUnavailable
from app.db import BookingOperation, Lead

ZONE = ZoneInfo("America/Phoenix")
SLOTS_API_VERSION = "2024-09-04"
CREATE_API_VERSION = "2026-02-25"
LIST_API_VERSION = "2026-05-01"
GET_BOOKING_API_VERSION = "2026-02-25"


class CalendarUnavailable(Exception):
    """Provider timeout or unknown response; do not claim a booking."""


class CalComCalendar:
    requires_contact_details = True

    def __init__(
        self, api_key: str, event_type_id: int, client: httpx.AsyncClient | None = None
    ) -> None:
        if not api_key or event_type_id < 1:
            raise ValueError("A Cal.com API key and event type ID are required")
        self.api_key = api_key
        self.event_type_id = event_type_id
        self.client = client

    async def _request(
        self, method: str, path: str, version: str, **kwargs: object
    ) -> dict:
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "cal-api-version": version,
            "Content-Type": "application/json",
        }
        try:
            if self.client is not None:
                response = await self.client.request(method, path, headers=headers, **kwargs)
            else:
                async with httpx.AsyncClient(base_url="https://api.cal.com", timeout=8) as client:
                    response = await client.request(method, path, headers=headers, **kwargs)
        except (httpx.RequestError, TimeoutError) as exc:
            raise CalendarUnavailable from exc
        if method == "POST" and response.status_code in {400, 409, 422}:
            raise SlotUnavailable
        if not 200 <= response.status_code < 300:
            raise CalendarUnavailable
        try:
            payload = response.json()
            if payload["status"] != "success":
                raise ValueError("Provider did not return success")
            return payload
        except (ValueError, KeyError, TypeError) as exc:
            raise CalendarUnavailable from exc

    async def available_slots(self, day: date, at: datetime) -> list[tuple[str, datetime, datetime]]:
        response = await self._request(
            "GET",
            "/v2/slots",
            SLOTS_API_VERSION,
            params={
                "eventTypeId": self.event_type_id,
                "start": day.isoformat(),
                "end": day.isoformat(),
                "timeZone": "America/Phoenix",
                "format": "range",
            },
        )
        try:
            entries = response["data"].get(day.isoformat(), [])
            result = []
            for value in entries:
                start = datetime.fromisoformat(value["start"].replace("Z", "+00:00"))
                end = datetime.fromisoformat(value["end"].replace("Z", "+00:00"))
                if start.tzinfo is None or end.tzinfo is None or end <= start:
                    raise ValueError("Invalid time range")
                if start.astimezone(ZONE).date() != day or start <= at:
                    continue
                key = f"cal:{self.event_type_id}:{start.astimezone(UTC):%Y%m%dT%H%M%SZ}"
                result.append((key, start, end))
            return result[:12]
        except (KeyError, TypeError, ValueError, AttributeError) as exc:
            raise CalendarUnavailable from exc

    async def _find_existing(self, session: AsyncSession, operation_id: str) -> dict | None:
        op = await session.get(BookingOperation, operation_id)
        if op is None:
            return None
        lead = await session.get(Lead, op.lead_id)
        if lead is None or not lead.email:
            return None
        # Paginate a bounded number of pages. If the provider's history exceeds this,
        # leave the booking pending rather than risk creating a duplicate.
        cursor = None
        for _ in range(10):
            params = {"attendeeEmail": lead.email, "eventTypeId": self.event_type_id, "limit": 100}
            if cursor:
                params["cursor"] = cursor
            payload = await self._request("GET", "/v2/bookings", LIST_API_VERSION, params=params)
            for record in payload.get("data", []):
                if record.get("metadata", {}).get("goodlookinOperationId") == operation_id:
                    return record
            pagination = payload.get("pagination") or {}
            if not pagination.get("hasMore"):
                return None
            cursor = pagination.get("nextCursor")
            if not cursor:
                break
        raise CalendarUnavailable

    @staticmethod
    def _confirmed_booking(record: dict) -> CalendarBooking | None:
        if record.get("status") != "accepted" or not record.get("uid"):
            return None
        try:
            starts = datetime.fromisoformat(record["start"].replace("Z", "+00:00"))
            ends = datetime.fromisoformat(record["end"].replace("Z", "+00:00"))
            if starts.tzinfo is None or ends.tzinfo is None or ends <= starts:
                raise ValueError("Invalid booking times")
            return CalendarBooking(str(record["uid"]), starts, ends)
        except (KeyError, TypeError, ValueError) as exc:
            raise CalendarUnavailable from exc

    async def lookup(self, session: AsyncSession, operation_id: str) -> CalendarBooking | None:
        op = await session.get(BookingOperation, operation_id)
        if op is None:
            return None
        if op.booking_id:
            record = (
                await self._request("GET", f"/v2/bookings/{op.booking_id}", GET_BOOKING_API_VERSION)
            ).get("data", {})
        else:
            record = await self._find_existing(session, operation_id)
        if record is None:
            return None
        if not op.booking_id and record.get("uid"):
            op.booking_id = str(record["uid"])
            await session.commit()
        return self._confirmed_booking(record)

    async def book(
        self, session: AsyncSession, operation_id: str, offer: SlotOffer, lead: Lead
    ) -> CalendarBooking | None:
        existing = await self.lookup(session, operation_id)
        if existing:
            return existing
        if not lead.name or not lead.email:
            raise ValueError("Contact name and email required")
        try:
            payload = await self._request(
                "POST",
                "/v2/bookings",
                CREATE_API_VERSION,
                json={
                    "start": (offer.starts_at.replace(tzinfo=ZONE) if offer.starts_at.tzinfo is None else offer.starts_at)
                    .astimezone(UTC)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "eventTypeId": self.event_type_id,
                    "attendee": {
                        "name": lead.name,
                        "email": lead.email,
                        "timeZone": "America/Phoenix",
                        "phoneNumber": lead.callback_phone,
                    },
                    "leadEnrichmentOptOut": True,
                    "metadata": {"goodlookinOperationId": operation_id},
                },
            )
        except CalendarUnavailable as exc:
            # Network or provider errors might occur AFTER the provider created
            # the appointment. Never retry the POST automatically.
            raise CalendarCommittedTimeout from exc
        record = payload.get("data")
        if not isinstance(record, dict) or not record.get("uid"):
            raise CalendarCommittedTimeout
        op = await session.get(BookingOperation, operation_id)
        op.booking_id = str(record["uid"])
        await session.commit()
        return self._confirmed_booking(record)
