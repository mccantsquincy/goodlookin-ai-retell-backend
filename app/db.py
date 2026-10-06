from collections.abc import AsyncIterator
from datetime import UTC, datetime
from uuid import uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def now() -> datetime:
    return datetime.now(UTC)


def new_id() -> str:
    return str(uuid4())


class Base(DeclarativeBase):
    pass


class Timestamps:
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=now, onupdate=now)


class CallSession(Base, Timestamps):
    __tablename__ = "call_sessions"
    call_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    status: Mapped[str] = mapped_column(String(32), default="active")
    last_event: Mapped[str | None] = mapped_column(String(80))


class Lead(Base, Timestamps):
    __tablename__ = "leads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    call_id: Mapped[str] = mapped_column(ForeignKey("call_sessions.call_id"), unique=True)
    name: Mapped[str | None] = mapped_column(String(200))
    callback_phone: Mapped[str] = mapped_column(String(40))
    email: Mapped[str | None] = mapped_column(String(320))
    street_address: Mapped[str | None] = mapped_column(String(300))
    city: Mapped[str | None] = mapped_column(String(120))
    state: Mapped[str | None] = mapped_column(String(2))
    postal_code: Mapped[str | None] = mapped_column(String(10))
    property_type: Mapped[str | None] = mapped_column(String(20))
    service_type: Mapped[str | None] = mapped_column(String(120))
    issue_summary: Mapped[str | None] = mapped_column(Text)
    authorized_contact: Mapped[bool | None]
    active_leak: Mapped[bool | None]


class BookingOperation(Base, Timestamps):
    __tablename__ = "booking_operations"
    __table_args__ = (UniqueConstraint("call_id", "lead_id", "slot_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    call_id: Mapped[str] = mapped_column(ForeignKey("call_sessions.call_id"))
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"))
    slot_id: Mapped[str] = mapped_column(String(200), unique=True)
    slot_key: Mapped[str] = mapped_column(String(100), unique=True)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    status_history: Mapped[str] = mapped_column(Text, default="[]")
    booking_id: Mapped[str | None] = mapped_column(String(100), unique=True)
    starts_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    error_code: Mapped[str | None] = mapped_column(String(80))


class Appointment(Base, Timestamps):
    __tablename__ = "appointments"
    id: Mapped[str] = mapped_column(String(100), primary_key=True)
    operation_id: Mapped[str] = mapped_column(ForeignKey("booking_operations.id"), unique=True)
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"))
    slot_id: Mapped[str] = mapped_column(String(200), unique=True)
    starts_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    ends_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(20), default="confirmed")


class CallbackRequest(Base, Timestamps):
    __tablename__ = "callback_requests"
    __table_args__ = (UniqueConstraint("call_id", "reason_key"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    call_id: Mapped[str] = mapped_column(ForeignKey("call_sessions.call_id"))
    lead_id: Mapped[str] = mapped_column(ForeignKey("leads.id"))
    reason: Mapped[str] = mapped_column(Text)
    reason_key: Mapped[str] = mapped_column(String(64))
    urgency: Mapped[str] = mapped_column(String(10))
    preferred_callback_window: Mapped[str | None] = mapped_column(String(200))
    status: Mapped[str] = mapped_column(String(20), default="queued")
    status_history: Mapped[str] = mapped_column(Text, default="[]")


class WebhookReceipt(Base, Timestamps):
    __tablename__ = "webhook_receipts"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    payload_hash: Mapped[str] = mapped_column(String(64), unique=True)
    event_type: Mapped[str] = mapped_column(String(80))
    call_id: Mapped[str | None] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(default=0)
    error_code: Mapped[str | None] = mapped_column(String(80))


def session_factory(url: str) -> async_sessionmaker[AsyncSession]:
    engine = create_async_engine(url)
    return async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    raise RuntimeError("get_session must be overridden by app factory")
    yield  # pragma: no cover
