from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta
from typing import Protocol
from uuid import NAMESPACE_URL, uuid5
from zoneinfo import ZoneInfo

from sqlalchemy import DateTime, ForeignKey, String, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base, Lead, Timestamps

ZONE = ZoneInfo("America/Phoenix")


class SlotUnavailable(Exception):
    pass


class CalendarCommittedTimeout(Exception):
    pass


@dataclass(frozen=True)
class CalendarBooking:
    booking_id: str
    starts_at: datetime
    ends_at: datetime


class SlotOffer(Base, Timestamps):
    __tablename__ = "slot_offers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    call_id: Mapped[str] = mapped_column(String(128))
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"))
    slot_key: Mapped[str] = mapped_column(String(100))
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class FakeCalendarReservation(Base, Timestamps):
    __tablename__ = "fake_calendar_reservations"
    booking_id: Mapped[str] = mapped_column(String(100), primary_key=True)
    operation_id: Mapped[str] = mapped_column(String(36), unique=True)
    slot_key: Mapped[str] = mapped_column(String(100), unique=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))


class CalendarAdapter(Protocol):
    async def available_slots(self, day: date, at: datetime) -> list[tuple[str, datetime, datetime]]: ...
    async def book(
        self, session: AsyncSession, operation_id: str, offer: SlotOffer, lead: Lead
    ) -> CalendarBooking | None: ...
    async def lookup(self, session: AsyncSession, operation_id: str) -> CalendarBooking | None: ...


class FakeCalendar:
    def __init__(self) -> None:
        self.timeout_after_commit = False

    async def available_slots(self, day: date, at: datetime) -> list[tuple[str, datetime, datetime]]:
        return proposed_slots(day, at)

    async def book(self, session: AsyncSession, operation_id: str, offer: SlotOffer, lead: Lead) -> CalendarBooking:
        prior = await self.lookup(session, operation_id)
        if prior:
            return prior
        occupied = await session.scalar(
            select(FakeCalendarReservation).where(FakeCalendarReservation.slot_key == offer.slot_key)
        )
        if occupied:
            raise SlotUnavailable
        record = FakeCalendarReservation(
            booking_id=str(uuid5(NAMESPACE_URL, f"booking:{operation_id}")),
            operation_id=operation_id,
            slot_key=offer.slot_key,
            starts_at=offer.starts_at,
            ends_at=offer.ends_at,
        )
        session.add(record)
        await session.commit()
        if self.timeout_after_commit:
            raise CalendarCommittedTimeout
        return CalendarBooking(record.booking_id, record.starts_at, record.ends_at)

    async def lookup(self, session: AsyncSession, operation_id: str) -> CalendarBooking | None:
        record = await session.scalar(
            select(FakeCalendarReservation).where(FakeCalendarReservation.operation_id == operation_id)
        )
        return CalendarBooking(record.booking_id, record.starts_at, record.ends_at) if record else None


def proposed_slots(day: date, at: datetime) -> list[tuple[str, datetime, datetime]]:
    result = []
    for hour in (9, 11, 14):
        start = datetime.combine(day, time(hour), ZONE)
        end = start + timedelta(hours=1)
        if start > at.astimezone(ZONE):
            result.append((f"{day.isoformat()}-{hour:02d}", start, end))
    return result


def offer_expiration(at: datetime) -> datetime:
    return at.astimezone(UTC) + timedelta(minutes=10)
