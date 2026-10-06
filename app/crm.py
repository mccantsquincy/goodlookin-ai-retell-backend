from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.contracts import SaveLeadArgs
from app.db import CallSession, Lead
from app.normalize import email, phone, postal_code, state


class CRMAdapter(Protocol):
    async def upsert_lead(self, session: AsyncSession, call_id: str, args: SaveLeadArgs) -> Lead: ...


class LocalCRM:
    """Database-backed CRM substitute for local flow validation."""

    async def upsert_lead(self, session: AsyncSession, call_id: str, args: SaveLeadArgs) -> Lead:
        if not await session.get(CallSession, call_id):
            session.add(CallSession(call_id=call_id))
            await session.flush()
        lead = await session.scalar(select(Lead).where(Lead.call_id == call_id))
        if args.lead_id and (not lead or lead.id != args.lead_id):
            raise LookupError("LEAD_NOT_FOUND")
        normalized_phone = phone(args.callback_phone)
        updates = args.model_dump(exclude_unset=True, exclude={"lead_id", "callback_phone"})
        for field, normalizer in (("email", email), ("state", state), ("postal_code", postal_code)):
            if field in updates and updates[field] is not None:
                updates[field] = normalizer(updates[field])
        for field, value in updates.items():
            if value is None or (isinstance(value, str) and not value):
                raise ValueError(f"INVALID_FIELD_VALUE:{field}")
        if lead is None:
            lead = Lead(call_id=call_id, callback_phone=normalized_phone)
            session.add(lead)
        lead.callback_phone = normalized_phone
        for field, value in updates.items():
            setattr(lead, field, value)
        await session.commit()
        return lead
