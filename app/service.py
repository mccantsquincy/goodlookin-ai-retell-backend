import hashlib
import json
from datetime import UTC, datetime
from uuid import uuid4
from zoneinfo import ZoneInfo

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.business import BusinessConfig, eligibility, missing_fields
from app.calendar import (
    CalendarAdapter,
    CalendarCommittedTimeout,
    SlotOffer,
    SlotUnavailable,
    offer_expiration,
)
from app.calcom_calendar import CalendarUnavailable
from app.contracts import (
    AvailabilityArgs,
    BookArgs,
    BookingStatusArgs,
    CallbackArgs,
    LeadIdArgs,
    SaveLeadArgs,
)
from app.crm import CRMAdapter
from app.db import Appointment, BookingOperation, CallbackRequest, Lead, now


class BusinessFailure(Exception):
    def __init__(self, code: str, data: dict | None = None) -> None:
        self.code = code
        self.data = data or {}


def status_event(status: str) -> dict[str, str]:
    return {"status": status, "at": now().isoformat()}


def add_status_event(item: BookingOperation, status: str) -> None:
    history = json.loads(item.status_history)
    history.append(status_event(status))
    item.status_history = json.dumps(history)


async def owned_lead(session: AsyncSession, call_id: str, lead_id: str) -> Lead:
    lead = await session.get(Lead, lead_id)
    if not lead or lead.call_id != call_id:
        raise BusinessFailure("LEAD_NOT_FOUND")
    return lead


async def save_lead(session: AsyncSession, call_id: str, args: SaveLeadArgs, crm: CRMAdapter) -> dict:
    try:
        lead = await crm.upsert_lead(session, call_id, args)
    except LookupError:
        raise BusinessFailure("LEAD_NOT_FOUND") from None
    except ValueError as exc:
        code, _, field = str(exc).partition(":")
        raise BusinessFailure(code, {"field": field} if field else {}) from None
    return {"saved": True, "lead_id": lead.id, "missing_fields": missing_fields(lead)}


async def check_eligibility(session: AsyncSession, call_id: str, args: LeadIdArgs, config: BusinessConfig) -> dict:
    return eligibility(await owned_lead(session, call_id, args.lead_id), config)


async def availability(
    session: AsyncSession, call_id: str, args: AvailabilityArgs, config: BusinessConfig, calendar: CalendarAdapter, at: datetime
) -> dict:
    lead = await owned_lead(session, call_id, args.lead_id)
    result = eligibility(lead, config)
    if result["eligibility"] != "eligible":
        raise BusinessFailure("NOT_ELIGIBLE", result)
    if not config.booking_enabled:
        raise BusinessFailure("BOOKING_DISABLED")
    slots = []
    try:
        available_slots = await calendar.available_slots(args.preferred_date, at)
    except CalendarUnavailable:
        raise BusinessFailure("CALENDAR_UNAVAILABLE") from None
    for slot_key, start, end in available_slots:
        booked = await session.scalar(
            select(BookingOperation).where(
                BookingOperation.slot_key == slot_key,
            )
        )
        if booked:
            continue
        offer = SlotOffer(
            id=str(uuid4()),
            call_id=call_id,
            lead_id=lead.id,
            slot_key=slot_key,
            starts_at=start,
            ends_at=end,
            expires_at=offer_expiration(at),
        )
        session.add(offer)
        slots.append(
            {
                "slot_id": offer.id,
                "starts_at": start.isoformat(),
                "ends_at": end.isoformat(),
                "spoken_label": start.strftime("%A, %B %-d at %-I %p"),
                "expires_at": offer.expires_at.isoformat(),
            }
        )
    await session.commit()
    return {"timezone": "America/Phoenix", "slots": slots}


def booking_data(op: BookingOperation) -> dict:
    zone = ZoneInfo("America/Phoenix")

    def local_iso(value: datetime | None) -> str | None:
        if value is None:
            return None
        return value.replace(tzinfo=zone).isoformat() if value.tzinfo is None else value.astimezone(zone).isoformat()

    return {
        "operation_id": op.id,
        "booking_id": op.booking_id,
        "status": op.status,
        "starts_at": local_iso(op.starts_at),
        "ends_at": local_iso(op.ends_at),
        "timezone": "America/Phoenix",
    }


async def reconcile(session: AsyncSession, op: BookingOperation, calendar: CalendarAdapter) -> None:
    try:
        reservation = await calendar.lookup(session, op.id)
    except CalendarUnavailable:
        return  # retain pending status; do not claim confirmation
    if reservation and op.status != "confirmed":
        op.status = "confirmed"
        add_status_event(op, "confirmed")
        op.booking_id = reservation.booking_id
        op.starts_at = reservation.starts_at
        op.ends_at = reservation.ends_at
        if not await session.get(Appointment, reservation.booking_id):
            session.add(
                Appointment(
                    id=reservation.booking_id,
                    operation_id=op.id,
                    lead_id=op.lead_id,
                    slot_id=op.slot_id,
                    starts_at=reservation.starts_at,
                    ends_at=reservation.ends_at,
                )
            )
        await session.commit()


async def book(
    session: AsyncSession,
    call_id: str,
    args: BookArgs,
    config: BusinessConfig,
    calendar: CalendarAdapter,
    at: datetime,
) -> dict:
    if not args.caller_confirmed:
        raise BusinessFailure("CONFIRMATION_REQUIRED")
    lead = await owned_lead(session, call_id, args.lead_id)
    existing = await session.scalar(
        select(BookingOperation).where(
            BookingOperation.call_id == call_id,
            BookingOperation.lead_id == lead.id,
            BookingOperation.slot_id == args.slot_id,
        )
    )
    if existing:
        await reconcile(session, existing, calendar)
        if existing.status == "failed":
            raise BusinessFailure(existing.error_code or "BOOKING_FAILED", booking_data(existing))
        return booking_data(existing)
    if getattr(calendar, "requires_contact_details", False) and (not lead.name or not lead.email):
        raise BusinessFailure("BOOKING_CONTACT_DETAILS_REQUIRED")
    if eligibility(lead, config)["eligibility"] != "eligible":
        raise BusinessFailure("NOT_ELIGIBLE")
    if not config.booking_enabled:
        raise BusinessFailure("BOOKING_DISABLED")
    offer = await session.get(SlotOffer, args.slot_id)
    if not offer or offer.call_id != call_id or offer.lead_id != lead.id:
        raise BusinessFailure("SLOT_UNAVAILABLE")
    expires = offer.expires_at.replace(tzinfo=UTC) if offer.expires_at.tzinfo is None else offer.expires_at
    if expires <= at.astimezone(UTC):
        raise BusinessFailure("SLOT_UNAVAILABLE")
    occupied = await session.scalar(
        select(BookingOperation).where(
            BookingOperation.slot_key == offer.slot_key,
        )
    )
    if occupied:
        raise BusinessFailure("SLOT_UNAVAILABLE")
    op = BookingOperation(
        call_id=call_id,
        lead_id=lead.id,
        slot_id=offer.id,
        slot_key=offer.slot_key,
        starts_at=offer.starts_at,
        ends_at=offer.ends_at,
        status_history=json.dumps([status_event("pending")]),
    )
    session.add(op)
    try:
        await session.commit()  # durable intent before any external calendar call
    except IntegrityError:
        await session.rollback()
        raise BusinessFailure("SLOT_UNAVAILABLE") from None
    try:
        await calendar.book(session, op.id, offer, lead)
    except CalendarCommittedTimeout:
        return booking_data(op)
    except SlotUnavailable:
        op.status = "failed"
        op.error_code = "SLOT_UNAVAILABLE"
        add_status_event(op, "failed")
        await session.commit()
        raise BusinessFailure("SLOT_UNAVAILABLE", booking_data(op)) from None
    await reconcile(session, op, calendar)
    return booking_data(op)


async def booking_status(
    session: AsyncSession, call_id: str, args: BookingStatusArgs, calendar: CalendarAdapter
) -> dict:
    await owned_lead(session, call_id, args.lead_id)
    stmt = select(BookingOperation).where(BookingOperation.call_id == call_id, BookingOperation.lead_id == args.lead_id)
    if args.operation_id:
        stmt = stmt.where(BookingOperation.id == args.operation_id)
    else:
        stmt = stmt.order_by(BookingOperation.created_at.desc())
    op = await session.scalar(stmt)
    if not op:
        raise BusinessFailure("BOOKING_NOT_FOUND")
    await reconcile(session, op, calendar)
    return booking_data(op)


async def callback(session: AsyncSession, call_id: str, args: CallbackArgs, config: BusinessConfig) -> dict:
    await owned_lead(session, call_id, args.lead_id)
    key = hashlib.sha256(args.reason.strip().casefold().encode()).hexdigest()
    item = await session.scalar(
        select(CallbackRequest).where(CallbackRequest.call_id == call_id, CallbackRequest.reason_key == key)
    )
    if not item:
        item = CallbackRequest(
            call_id=call_id,
            lead_id=args.lead_id,
            reason=args.reason,
            reason_key=key,
            urgency=args.urgency,
            preferred_callback_window=args.preferred_callback_window,
            status_history=json.dumps([status_event("queued")]),
        )
        session.add(item)
        try:
            await session.commit()
        except IntegrityError:
            await session.rollback()
            item = await session.scalar(
                select(CallbackRequest).where(CallbackRequest.call_id == call_id, CallbackRequest.reason_key == key)
            )
            if item is None:
                raise
    return {
        "callback_request_id": item.id,
        "status": item.status,
        "callback_expectation": config.callback_expectation,
    }
