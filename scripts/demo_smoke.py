"""Exercise the seven Retell tools against a LOCAL demo backend.

No Retell, CRM or calendar credentials are required. This script intentionally
refuses non-loopback hosts; it MUST NOT be used for production traffic.
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta
from urllib.parse import urlparse
from uuid import uuid4
from zoneinfo import ZoneInfo

DEMO_PHONE = "6025550101"
DEMO_ADDRESS = "123 Example Street"
ZONE = ZoneInfo("America/Phoenix")


def require_local_base_url(value: str) -> str:
    parsed = urlparse(value)
    if (
        parsed.scheme != "http"
        or parsed.hostname not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username is not None
        or parsed.password is not None
        or parsed.path not in {"", "/"}
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Demo smoke tests only support http://127.0.0.1 or http://localhost")
    return value.rstrip("/")


def call_tool(base: str, name: str, call_id: str, **args: object) -> dict:
    payload = json.dumps({"name": name, "call": {"call_id": call_id}, "args": args}).encode()
    request = urllib.request.Request(
        f"{base}/v1/retell/tools/{name}",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            result = json.load(response)
    except urllib.error.HTTPError as exc:
        raise RuntimeError(f"{name}: HTTP {exc.code}; check APP_ENV and demo settings") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError("Cannot reach the local API. Start Uvicorn and PostgreSQL first.") from exc
    if not result.get("ok"):
        raise RuntimeError(f"{name}: business result code={result.get('code')}")
    return result["data"]


def run(base: str) -> None:
    # Each run uses its own call_id. Only synthetic contact details are sent.
    call_id = f"demo-{uuid4()}"
    steps = []

    context = call_tool(base, "get_business_context", call_id)
    if not context.get("company_name", "").startswith("FICTIONAL ROOFING DEMO"):
        raise RuntimeError("Refusing to run: the loaded business is not the fictional demo fixture")
    if context.get("transfer_available") or not context.get("booking_enabled"):
        raise RuntimeError("Demo fixture must have simulated booking on and transfers off")
    steps.append("Business context loaded")

    saved = call_tool(
        base,
        "save_lead",
        call_id,
        name="Demo Caller",
        callback_phone=DEMO_PHONE,
        street_address=DEMO_ADDRESS,
        city="Phoenix",
        state="AZ",
        postal_code="85001",
        property_type="residential",
        service_type="inspection",
        issue_summary="Simulated request for a roof inspection",
        authorized_contact=True,
    )
    lead_id = saved["lead_id"]
    if saved["missing_fields"]:
        raise RuntimeError("Synthetic lead is incomplete")
    steps.append("Synthetic lead saved")

    checked = call_tool(base, "check_service_eligibility", call_id, lead_id=lead_id)
    if checked["eligibility"] != "eligible":
        raise RuntimeError("Demo eligibility was not approved by deterministic rules")
    steps.append("Eligibility checked")

    # Look for a free day, so repeated smoke runs are less likely to collide.
    selected = None
    for days_ahead in range(2, 16):
        day = (datetime.now(ZONE) + timedelta(days=days_ahead)).date().isoformat()
        slots = call_tool(
            base, "get_available_appointments", call_id, lead_id=lead_id, preferred_date=day
        )["slots"]
        if slots:
            selected = slots[0]["slot_id"]
            break
    if selected is None:
        raise RuntimeError("No simulated slots available over the next 14 days")
    steps.append("Simulated appointment offered")

    booked = call_tool(
        base, "book_appointment", call_id,
        lead_id=lead_id, slot_id=selected, caller_confirmed=True
    )
    checked_booking = call_tool(
        base, "get_booking_status", call_id,
        lead_id=lead_id, operation_id=booked["operation_id"]
    )
    if checked_booking["status"] != "confirmed":
        raise RuntimeError("Simulated booking did not reach confirmed state")
    steps.append("Booking created and reconciled")

    callback = call_tool(
        base, "request_callback", call_id,
        lead_id=lead_id, reason="Synthetic follow-up", urgency="routine"
    )
    if callback["status"] != "queued":
        raise RuntimeError("Callback was not queued")
    steps.append("Callback request persisted (NOT delivered)")

    print("PASS: local Goodlookin AI demo smoke test")
    for step in steps:
        print(f"  - {step}")
    print("Reminder: appointment is FAKE and callback is NOT sent to a human.")
    # Never print caller phone, address, or full database records.


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default="http://127.0.0.1:8000")
    args = parser.parse_args()
    try:
        run(require_local_base_url(args.base_url))
    except (ValueError, RuntimeError, KeyError, TypeError) as exc:
        print(f"FAIL: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
