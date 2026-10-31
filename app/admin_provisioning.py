"""Staging-only browser console for explicitly reviewed Cal.com provisioning.

Hidden unless a separate, strong admin password is configured. No caller
payloads, website content, API keys or arbitrary URLs are accepted.
"""

import asyncio
import hashlib
import hmac
import json
import secrets
import time
from html import escape
from pathlib import Path
from urllib.parse import parse_qs

import httpx
from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import HTMLResponse

from app.calendar_provisioning import CalComProvisioner, ClientCalendarDraft, ProvisioningError
from app.config import Settings
from app.demo_dashboard import valid_basic_auth

INSTANCE_PATH = Path(__file__).parent / "instances" / "summit-roofing-demo.json"
ADMIN_PATH = "/admin/calendar"
FORM_ACTION = "/admin/calendar/provision"
CSRF_COOKIE = "gl_calendar_approval"
CSRF_MAX_AGE = 900
ADMIN_HEADERS = {
    "Cache-Control": "no-store",
    "Pragma": "no-cache",
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
    "Content-Security-Policy": (
        "default-src 'none'; style-src 'unsafe-inline'; form-action 'self'; base-uri 'none'; frame-ancestors 'none'"
    ),
}


def _page(title: str, content: str) -> HTMLResponse:
    title = escape(title)
    return HTMLResponse(
        content=f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>Goodlookin AI / {title}</title>
<style>body{{background:#101218;color:#f6f7fa;max-width:800px;margin:48px auto;
font:16px system-ui,Arial;padding:0 22px;line-height:1.55}}
h1{{color:#ff8b42}}pre{{background:#20232b;white-space:pre-wrap;overflow-wrap:anywhere;
padding:18px;border-radius:9px}}button{{background:#ff8b42;color:#101218;border:0;
padding:12px 16px;font-size:16px;font-weight:700;cursor:pointer;border-radius:8px}}
input[type=text]{{width:100%;padding:12px;box-sizing:border-box;margin:10px 0;
background:#242832;border:1px solid #666;color:white}}
.note{{border-left:3px solid #ff8b42;padding:12px;background:#22252d}}
a{{color:#ffaf78}}</style></head><body>
<h1>{title}</h1>{content}</body></html>""",
        headers=ADMIN_HEADERS,
    )


def _load() -> tuple[ClientCalendarDraft, str]:
    raw = INSTANCE_PATH.read_bytes()
    return ClientCalendarDraft.model_validate_json(raw), hashlib.sha256(raw).hexdigest()


def _csrf(admin_password: str, digest: str, key: str, session_nonce: str) -> str:
    message = f"provision-demo:v2:{digest}:{key}:{session_nonce}".encode()
    return hmac.new(admin_password.encode(), message, hashlib.sha256).hexdigest()


def _new_session_nonce() -> str:
    return f"{int(time.time())}.{secrets.token_hex(32)}"


def _valid_session_nonce(raw: str | None) -> bool:
    if not raw or len(raw) > 100:
        return False
    issued, sep, token = raw.partition(".")
    if not sep or len(issued) != 10 or not issued.isdecimal() or len(token) != 64:
        return False
    if any(ch not in "0123456789abcdef" for ch in token):
        return False
    age = time.time() - int(issued)
    return -60 <= age <= CSRF_MAX_AGE


def build_admin_provisioning_router(
    settings: Settings,
    test_client: httpx.AsyncClient | None = None,
) -> APIRouter:
    router = APIRouter()
    lock = asyncio.Lock()

    def authenticate(request: Request) -> str | Response:
        password = settings.provisioning_admin_password.get_secret_value()
        if settings.app_env not in {"staging", "test"} or not password:
            raise HTTPException(status_code=404)
        if not valid_basic_auth(request, "provisioner", password):
            return Response(
                content="Authentication required.",
                status_code=401,
                headers={
                    **ADMIN_HEADERS,
                    "WWW-Authenticate": 'Basic realm="Goodlookin calendar admin", charset="UTF-8"',
                },
            )
        return password

    @router.get(ADMIN_PATH, include_in_schema=False)
    async def view(request: Request) -> Response:
        auth = authenticate(request)
        if isinstance(auth, Response):
            return auth
        draft, digest = _load()
        preview = json.dumps(draft.preview(), indent=2, ensure_ascii=True)
        safe_preview = escape(preview)
        phrase = f"PROVISION {draft.instance_key}"
        nonce = _new_session_nonce()
        csrf = _csrf(auth, digest, draft.instance_key, nonce)
        page = _page(
            "Review calendar provisioning",
            f"""<p><strong>Staging-only. Fictional company.</strong>
This action creates an availability schedule and a Cal.com event type.</p>
<pre>{safe_preview}</pre>
<div class="note"><strong>Not included:</strong> connecting or verifying the Google
destination calendar; creating an appointment; enabling Retell bookings.
Review the linked Cal.com account before any creation.</div>
<form method="post" action="{FORM_ACTION}">
<input type="hidden" name="draft_sha256" value="{digest}">
<input type="hidden" name="csrf_token" value="{csrf}">
<label><input type="checkbox" name="approve" value="yes" required>
I reviewed the fictional company settings, schedule, account, and destination.</label>
<p>Type <strong>{escape(phrase)}</strong> to approve:</p>
<input type="text" name="confirmation" required autocomplete="off" maxlength="75">
<button type="submit">Create or verify Cal.com resources</button>
</form><p><a href="/demo">Back to operations</a></p>""",
        )
        page.set_cookie(
            CSRF_COOKIE,
            nonce,
            path="/admin/calendar",
            secure=settings.app_env == "staging",
            httponly=True,
            samesite="strict",
            max_age=CSRF_MAX_AGE,
        )
        return page

    @router.post(FORM_ACTION, include_in_schema=False)
    async def apply(request: Request) -> Response:
        auth = authenticate(request)
        if isinstance(auth, Response):
            return auth
        # A browser session bound to the approved GET is required. The
        # short-lived, HttpOnly, SameSite=Strict cookie is not sent by browsers
        # on cross-site POSTs. Require its signed form token below, too.
        # Do not depend on browser Origin/Fetch-Metadata headers, which can be
        # modified or omitted by privacy software and intermediate proxies.
        nonce = request.cookies.get(CSRF_COOKIE)
        if not _valid_session_nonce(nonce):
            raise HTTPException(status_code=403, detail="Approval session missing or expired; reopen admin page")
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/x-www-form-urlencoded":
            raise HTTPException(status_code=415)
        raw = await request.body()
        if len(raw) > 4096:
            raise HTTPException(status_code=413)
        try:
            fields = parse_qs(raw.decode("utf-8"), strict_parsing=True, max_num_fields=8)
        except (UnicodeError, ValueError) as exc:
            raise HTTPException(status_code=400, detail="Invalid form") from exc

        def one(key: str) -> str:
            values = fields.get(key, [])
            return values[0] if len(values) == 1 else ""

        draft, digest = _load()
        valid = (
            one("draft_sha256") == digest
            and hmac.compare_digest(one("csrf_token"), _csrf(auth, digest, draft.instance_key, nonce))
            and one("approve") == "yes"
            and one("confirmation") == f"PROVISION {draft.instance_key}"
        )
        if not valid:
            raise HTTPException(status_code=400, detail="Draft approval missing or stale")
        key = settings.calcom_provisioning_api_key.get_secret_value()
        versions = (settings.calcom_schedules_api_version, settings.calcom_event_types_api_version)
        if not key or not all(versions):
            return _page("Provisioning unavailable", "<p>Cal.com provisioning variables are not configured.</p>")
        async with lock:
            # The validated POST is the one-time review authorization; the
            # version-controlled fixture remains approved=false by default.
            approved = draft.model_copy(update={"approved": True})
            try:
                if test_client is not None:
                    provider = CalComProvisioner(key, *versions, test_client)
                    result = await provider.apply(approved, draft.instance_key)
                else:
                    async with httpx.AsyncClient(base_url="https://api.cal.com", timeout=10) as client:
                        provider = CalComProvisioner(key, *versions, client)
                        result = await provider.apply(approved, draft.instance_key)
            except ProvisioningError:
                # Do not leak upstream error bodies or tokens. A POST may have
                # committed remotely; the operator must inspect Cal.com.
                return _page(
                    "Provisioning could not be verified",
                    "<p>Check the Cal.com account for an existing schedule/event before trying again. "
                    "No automatic retry was performed.</p>",
                )
        return _page(
            "Calendar resources checked",
            f"<p>The result below is from Cal.com. <strong>Bookings remain disabled.</strong></p>"
            f"<pre>{escape(json.dumps(result, indent=2))}</pre>"
            "<p>Verify the Google destination calendar in Cal.com before activating any bookings.</p>",
        )

    return router
