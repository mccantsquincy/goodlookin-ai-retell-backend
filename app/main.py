import json
import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from uuid import uuid4

import httpx
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.admin_provisioning import build_admin_provisioning_router
from app.business import BusinessConfig
from app.calcom_calendar import CalComCalendar
from app.calendar import CalendarAdapter, FakeCalendar
from app.config import Settings
from app.contracts import ARG_MODELS, ToolRequest, ToolResponse
from app.crm import CRMAdapter, LocalCRM
from app.db import get_session, session_factory
from app.demo_dashboard import build_dashboard_router
from app.knowledge import search_knowledge, service_area_check
from app.security import verified_body
from app.service import (
    BusinessFailure,
    availability,
    book,
    booking_status,
    callback,
    check_eligibility,
    save_lead,
)
from app.webhooks import receive

log = logging.getLogger("roofing.api")


def create_app(
    settings: Settings | None = None,
    business: BusinessConfig | None = None,
    calendar: CalendarAdapter | None = None,
    crm: CRMAdapter | None = None,
    provisioning_client: httpx.AsyncClient | None = None,
) -> FastAPI:
    settings = settings or Settings()
    if business is None:
        config_path = settings.business_config_path
        if not config_path.exists():
            if settings.app_env == "production":
                raise ValueError("Business configuration is required in production")
            config_path = Path(__file__).with_name("business.local.example.json")
        business = BusinessConfig.load(config_path)
    if calendar is None:
        calendar = (
            CalComCalendar(settings.calcom_api_key.get_secret_value(), settings.calcom_event_type_id)
            if settings.calendar_provider == "calcom"
            else FakeCalendar()
        )
    crm = crm or LocalCRM()
    if settings.app_env == "production" and (isinstance(calendar, FakeCalendar) or isinstance(crm, LocalCRM)):
        raise ValueError("Production calendar and CRM adapters are required")
    sessions = session_factory(settings.database_url)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        yield
        await sessions.kw["bind"].dispose()

    app = FastAPI(title="Goodlookin AI Roofing Receptionist", version="0.1.0", lifespan=lifespan)
    app.state.settings = settings
    app.state.business = business
    app.state.calendar = calendar
    app.state.crm = crm
    app.state.sessions = sessions
    app.include_router(build_dashboard_router(settings, sessions))
    app.include_router(build_admin_provisioning_router(settings, provisioning_client))

    async def database_session() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    app.dependency_overrides[get_session] = database_session

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.get("/ready")
    async def ready() -> dict[str, str]:
        try:
            async with sessions() as session:
                await session.execute(text("SELECT 1"))
            return {"status": "ready"}
        except Exception as exc:
            raise HTTPException(status_code=503, detail="Database unavailable") from exc

    async def handle_tool(name: str, request: Request) -> ToolResponse | JSONResponse:
        started = time.monotonic()
        request_id = str(uuid4())
        call_id = None
        code = "INTERNAL_ERROR"
        try:
            raw = await verified_body(request, settings)
            try:
                envelope = ToolRequest.model_validate_json(raw)
                if envelope.name != name:
                    raise ValueError("Tool name does not match route")
                call_id = envelope.call.call_id
                args = ARG_MODELS[name].model_validate(envelope.args)
            except (ValidationError, ValueError) as exc:
                raise HTTPException(status_code=422, detail="Malformed Retell tool request") from exc
            async with sessions() as session:
                if name == "get_business_context":
                    from app.db import now

                    data = business.context(now())
                elif name == "search_knowledge":
                    data = search_knowledge(business, args.question)
                elif name == "check_service_area":
                    data = service_area_check(business, args.state, args.postal_code, args.service_type)
                elif name == "save_lead":
                    data = await save_lead(session, call_id, args, crm)
                elif name == "check_service_eligibility":
                    data = await check_eligibility(session, call_id, args, business)
                elif name == "get_available_appointments":
                    from app.db import now

                    data = await availability(session, call_id, args, business, calendar, now())
                elif name == "book_appointment":
                    from app.db import now

                    data = await book(session, call_id, args, business, calendar, now())
                elif name == "get_booking_status":
                    data = await booking_status(session, call_id, args, calendar)
                else:
                    data = await callback(session, call_id, args, business)
            code = "OK"
            return ToolResponse(ok=True, code=code, data=data, request_id=request_id)
        except BusinessFailure as exc:
            code = exc.code
            return ToolResponse(ok=False, code=code, data=exc.data, request_id=request_id)
        except HTTPException as exc:
            code = "INVALID_SIGNATURE" if exc.status_code == 401 else "MALFORMED_REQUEST"
            return JSONResponse(
                status_code=exc.status_code,
                content=ToolResponse(ok=False, code=code, data={}, request_id=request_id).model_dump(),
            )
        except Exception:
            return JSONResponse(
                status_code=500,
                content=ToolResponse(ok=False, code=code, data={}, request_id=request_id).model_dump(),
            )
        finally:
            # Retell gets HTTP 200 even for business validation errors.
            # Warn with safe error codes, never with caller-provided fields.
            log.log(
                logging.WARNING if code != "OK" else logging.INFO,
                json.dumps(
                    {
                        "request_id": request_id,
                        "call_id": call_id,
                        "endpoint": name,
                        "code": code,
                        "elapsed_ms": round((time.monotonic() - started) * 1000, 2),
                    }
                ),
            )

    def register(name: str) -> None:
        async def endpoint(request: Request) -> ToolResponse | JSONResponse:
            return await handle_tool(name, request)

        endpoint.__name__ = f"tool_{name}"
        app.add_api_route(
            f"/v1/retell/tools/{name}",
            endpoint,
            methods=["POST"],
            response_model=ToolResponse,
            name=name,
            openapi_extra={
                "requestBody": {
                    "required": True,
                    "content": {
                        "application/json": {
                            "schema": {
                                "type": "object",
                                "required": ["name", "call", "args"],
                                "properties": {
                                    "name": {"const": name},
                                    "call": {
                                        "type": "object",
                                        "required": ["call_id"],
                                        "properties": {"call_id": {"type": "string"}},
                                    },
                                    "args": ARG_MODELS[name].model_json_schema(),
                                },
                            }
                        }
                    },
                }
            },
        )

    for tool_name in ARG_MODELS:
        register(tool_name)

    @app.post(
        "/v1/retell/webhooks",
        status_code=204,
        openapi_extra={
            "requestBody": {
                "required": True,
                "content": {
                    "application/json": {
                        "schema": {
                            "type": "object",
                            "required": ["event"],
                            "properties": {
                                "event": {"type": "string"},
                                "call": {
                                    "type": "object",
                                    "properties": {"call_id": {"type": "string"}},
                                    "additionalProperties": True,
                                },
                            },
                            "additionalProperties": True,
                        }
                    }
                },
            }
        },
    )
    async def webhook(request: Request) -> Response:
        raw = await verified_body(request, settings)
        try:
            async with sessions() as session:
                await receive(session, raw)
        except (ValueError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=422, detail="Malformed webhook") from exc
        return Response(status_code=204)

    return app


app = create_app()
