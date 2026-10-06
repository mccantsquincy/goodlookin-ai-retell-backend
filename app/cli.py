import argparse
import asyncio
import json
import logging
from pathlib import Path

from app.config import Settings
from app.db import session_factory
from app.webhooks import process_receipts


async def worker() -> None:
    sessions = session_factory(Settings().database_url)
    while True:
        try:
            async with sessions() as session:
                count = await process_receipts(session)
            if not count:
                await asyncio.sleep(1)
        except Exception:
            logging.getLogger("roofing.worker").error("webhook_worker_retry")
            await asyncio.sleep(5)


def bootstrap(path: Path) -> None:
    if path.exists():
        raise SystemExit(f"Already exists: {path}")
    path.write_text(
        json.dumps(
            {
                "company_name": "LOCAL DEVELOPMENT ROOFING DEMO",
                "business_hours": {},
                "services": [],
                "approved_faqs": [],
                "booking_enabled": False,
                "transfer_available": False,
                "transfer_phone_number": None,
                "callback_expectation": "The local demo team will review this request; no timing is promised.",
                "eligible_states": [],
                "eligible_postal_codes": [],
                "eligible_services": [],
                "appointment_type": None,
            },
            indent=2,
        )
        + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["bootstrap", "worker"])
    args = parser.parse_args()
    if args.command == "bootstrap":
        bootstrap(Settings().business_config_path)
    else:
        asyncio.run(worker())


if __name__ == "__main__":
    main()
