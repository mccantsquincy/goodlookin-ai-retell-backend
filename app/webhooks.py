import hashlib
import json

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession

from app.db import CallSession, WebhookReceipt


async def receive(session: AsyncSession, raw_body: bytes) -> bool:
    payload = json.loads(raw_body)
    if not isinstance(payload, dict) or not isinstance(payload.get("event"), str):
        raise ValueError("Invalid webhook payload")
    digest = hashlib.sha256(raw_body).hexdigest()
    if await session.scalar(select(WebhookReceipt.id).where(WebhookReceipt.payload_hash == digest)):
        return False
    call = payload.get("call")
    call_id = call.get("call_id") if isinstance(call, dict) else None
    if not isinstance(call_id, str) or not call_id or len(call_id) > 128:
        call_id = None
    session.add(WebhookReceipt(payload_hash=digest, event_type=payload["event"][:80], call_id=call_id))
    try:
        await session.commit()
    except IntegrityError:
        await session.rollback()
        return False
    return True


async def process_receipts(session: AsyncSession, limit: int = 100) -> int:
    # The worker stores only event metadata; transcripts and contact details stay out of this database.
    receipts = (
        await session.scalars(
            select(WebhookReceipt)
            .where(WebhookReceipt.status == "pending")
            .order_by(WebhookReceipt.created_at)
            .limit(limit)
            .with_for_update(skip_locked=True)
        )
    ).all()
    for receipt in receipts:
        receipt.attempts += 1
        if receipt.event_type in {"call_started", "call_ended", "call_analyzed"} and receipt.call_id:
            call = await session.get(CallSession, receipt.call_id)
            if not call:
                call = CallSession(call_id=receipt.call_id)
                session.add(call)
            call.last_event = receipt.event_type
            if receipt.event_type == "call_ended":
                call.status = "ended"
            elif receipt.event_type == "call_analyzed":
                call.status = "analyzed"
        receipt.status = (
            "processed" if receipt.event_type in {"call_started", "call_ended", "call_analyzed"} else "ignored"
        )
    await session.commit()
    return len(receipts)
