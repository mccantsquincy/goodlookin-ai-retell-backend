"""Preview or explicitly apply a fictional client's Cal.com resource draft.

This is a management command, not a public API endpoint. API secrets remain
in a private environment, never in the checked-in draft file.
"""
import argparse
import asyncio
import json
import os
from pathlib import Path

import httpx

from app.calendar_provisioning import CalComProvisioner, ClientCalendarDraft, ProvisioningError


async def run(args: argparse.Namespace) -> dict:
    draft = ClientCalendarDraft.model_validate_json(Path(args.draft).read_text())
    if not args.apply:
        return draft.preview()
    if args.confirm_instance != draft.instance_key:
        raise ProvisioningError("Pass --confirm-instance matching the reviewed draft instance_key")
    if not draft.approved:
        raise ProvisioningError("Draft must be explicitly approved before --apply")
    token = os.environ.get("CALCOM_PROVISIONING_API_KEY", "")
    if not token:
        raise ProvisioningError("CALCOM_PROVISIONING_API_KEY is missing")
    schedule_version = os.environ.get("CALCOM_SCHEDULES_API_VERSION", "")
    event_version = os.environ.get("CALCOM_EVENT_TYPES_API_VERSION", "")
    async with httpx.AsyncClient(base_url="https://api.cal.com", timeout=10) as client:
        provisioner = CalComProvisioner(token, schedule_version, event_version, client)
        return await provisioner.apply(draft, args.confirm_instance)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--draft", default="app/instances/summit-roofing-demo.json")
    parser.add_argument("--apply", action="store_true", help="Opt in to remote resource writes")
    parser.add_argument("--confirm-instance", default="", help="Must match reviewed instance key")
    args = parser.parse_args()
    try:
        result = asyncio.run(run(args))
    except (ValueError, OSError, ProvisioningError) as exc:
        print(f"FAIL: {exc}")  # Sanitized errors only; never print API keys or raw Cal.com responses.
        return 1
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
