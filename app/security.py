from fastapi import HTTPException, Request
from retell.lib.webhook_auth import verify

from app.config import Settings


def verify_retell_request(raw_body: bytes, signature: str | None, settings: Settings) -> None:
    if settings.retell_signature_bypass and settings.app_env in {"development", "test"}:
        return
    if not signature or not settings.retell_api_key:
        raise HTTPException(status_code=401, detail="Invalid Retell signature")
    try:
        # Retell's official SDK verifies the original body and timestamped signature.
        valid = verify(raw_body.decode("utf-8"), settings.retell_api_key, signature)
    except (ValueError, UnicodeError):
        valid = False
    if not valid:
        raise HTTPException(status_code=401, detail="Invalid Retell signature")


async def verified_body(request: Request, settings: Settings) -> bytes:
    body = await request.body()
    verify_retell_request(body, request.headers.get("X-Retell-Signature"), settings)
    return body
