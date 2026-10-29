"""Staging-only operations preview; shows aggregate facts, never caller PII."""

import base64
import binascii
import hashlib
import hmac
from html import escape

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.config import Settings
from app.db import Appointment, BookingOperation, CallbackRequest, CallSession, Lead

HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "Content-Security-Policy": "default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'; frame-ancestors 'none'",
}


def valid_basic_auth(request: Request, username: str, password: str) -> bool:
    """Compare credentials without logging or reflecting supplied values."""
    scheme, _, value = request.headers.get("authorization", "").partition(" ")
    if scheme.casefold() != "basic" or not value:
        return False
    try:
        decoded = base64.b64decode(value, validate=True).decode("utf-8")
        supplied_user, supplied_password = decoded.split(":", 1)
    except (binascii.Error, UnicodeError, ValueError):
        return False
    user_ok = hmac.compare_digest(supplied_user.encode(), username.encode())
    password_ok = hmac.compare_digest(supplied_password.encode(), password.encode())
    return user_ok and password_ok


async def dashboard_data(session: AsyncSession) -> tuple[dict[str, int], list[dict[str, str | bool]]]:
    """All counts are database facts; no lead fields, transcripts or raw call IDs leave this function."""
    counts = {}
    for label, model in (
        ("calls", CallSession),
        ("leads", Lead),
        ("callbacks", CallbackRequest),
        ("appointments", Appointment),
    ):
        counts[label] = (await session.scalar(select(func.count()).select_from(model))) or 0

    calls = (await session.scalars(select(CallSession).order_by(CallSession.created_at.desc()).limit(20))).all()
    ids = [call.call_id for call in calls]
    if not ids:
        return counts, []

    leads = set((await session.scalars(select(Lead.call_id).where(Lead.call_id.in_(ids)))).all())
    callbacks = set(
        (await session.scalars(select(CallbackRequest.call_id).where(CallbackRequest.call_id.in_(ids)))).all()
    )
    bookings = set(
        (
            await session.scalars(
                select(BookingOperation.call_id).where(
                    BookingOperation.call_id.in_(ids), BookingOperation.status == "confirmed"
                )
            )
        ).all()
    )
    rows = []
    for call in calls:
        rows.append(
            {
                "reference": hashlib.sha256(call.call_id.encode()).hexdigest()[:10],
                "created_at": call.created_at.strftime("%Y-%m-%d %H:%M UTC"),
                "status": call.status if call.status in {"active", "ended", "analyzed"} else "other",
                "lead": call.call_id in leads,
                "callback": call.call_id in callbacks,
                "simulated_booking": call.call_id in bookings,
            }
        )
    return counts, rows


def dashboard_html(counts: dict[str, int], rows: list[dict[str, str | bool]]) -> str:
    """Render server-side HTML; escape all variable content."""
    cards = "".join(
        f'<article class="stat"><div class="number">{counts[name]}</div><div class="label">{label}</div></article>'
        for name, label in (
            ("calls", "Recorded calls"),
            ("leads", "Saved leads"),
            ("callbacks", "Callback requests queued"),
            ("appointments", "Simulated appointments"),
        )
    )
    table_rows = (
        "".join(
            "<tr>"
            f"<td><code>{escape(str(item['reference']))}</code></td>"
            f"<td>{escape(str(item['created_at']))}</td>"
            f"<td>{escape(str(item['status']))}</td>"
            f"<td>{'Saved' if item['lead'] else '—'}</td>"
            f"<td>{'Queued' if item['callback'] else '—'}</td>"
            f"<td>{'Simulated' if item['simulated_booking'] else '—'}</td>"
            "</tr>"
            for item in rows
        )
        or '<tr><td colspan="6">No call activity recorded yet. Run a fictional Retell test call.</td></tr>'
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Goodlookin AI · Staging Operations</title><style>
*{{box-sizing:border-box}} body{{margin:0;background:#0e1015;color:#fafafa;font:16px system-ui,Arial,sans-serif}}
main{{max-width:1100px;margin:0 auto;padding:38px 20px}} .eyebrow{{color:#ff8d3f;text-transform:uppercase;
letter-spacing:.16em;font-size:12px;font-weight:700}} h1{{margin:10px 0;font-size:clamp(26px,4vw,42px)}}
p{{color:#c5c7cc;line-height:1.6}}
.grid{{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px;
margin:28px 0}}
.stat{{background:#1a1d24;border:1px solid #323742;border-radius:15px;padding:22px}}
.number{{font-size:35px;font-weight:750;color:#ff8838}} .label{{color:#b7bbc4;font-size:13px;margin-top:6px}}
section{{background:#1a1d24;border:1px solid #323742;border-radius:15px;overflow-x:auto}}
table{{border-collapse:collapse;width:100%;min-width:760px;font-size:14px}}
th,td{{padding:16px;text-align:left;border-bottom:1px solid #30343d}}
th{{color:#bbc0ca;font-size:12px;text-transform:uppercase;letter-spacing:.06em}}code{{color:#ffb47f}}
.note{{padding:17px 20px;margin:22px 0;background:#241f1a;border-left:4px solid #ff8838;border-radius:8px}}
footer{{margin-top:26px;color:#aeb2bc;font-size:13px}}
</style></head><body><main>
<div class="eyebrow">Goodlookin AI / Staging-only</div><h1>Receptionist operations</h1>
<p>Read-only snapshot of the current database. No caller names, phone numbers, addresses or transcripts.</p>
<div class="grid">{cards}</div>
<div class="note"><strong>Important:</strong> Queued callbacks are stored, <em>not delivered</em> to the office.
All appointments shown here are <em>simulated</em>, not external calendar bookings.</div>
<h2>Recent activity</h2><section><table><thead><tr><th>Anonymous reference</th><th>First recorded (UTC)</th>
<th>Call state</th><th>Lead</th><th>Callback</th><th>Booking</th></tr></thead>
<tbody>{table_rows}</tbody></table></section>
<footer>Last 20 call records · Totals are all-time · Refresh your browser to update · Private staging preview</footer>
</main></body></html>"""


def build_dashboard_router(settings: Settings, sessions: async_sessionmaker[AsyncSession]) -> APIRouter:
    router = APIRouter()

    @router.get("/demo", include_in_schema=False)
    async def demo_dashboard(request: Request) -> Response:
        if settings.app_env not in {"development", "test", "staging"} or not settings.demo_dashboard_username:
            raise HTTPException(status_code=404)
        password = settings.demo_dashboard_password.get_secret_value()
        if not valid_basic_auth(request, settings.demo_dashboard_username, password):
            return Response(
                content="Authentication required.",
                status_code=401,
                headers={**HEADERS, "WWW-Authenticate": 'Basic realm="Goodlookin staging", charset="UTF-8"'},
            )
        async with sessions() as session:
            counts, rows = await dashboard_data(session)
        return HTMLResponse(content=dashboard_html(counts, rows), headers=HEADERS)

    return router
