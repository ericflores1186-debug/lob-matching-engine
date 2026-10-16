from __future__ import annotations

import re
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from typing import Annotated, Any, Literal, TypeVar

from fastapi import FastAPI, Header, Path, Query, Request, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from backend.config import Settings, get_settings
from backend.database import init_db, make_engine, make_session_factory
from backend.fix_engine.parser import ValidationStatus
from backend.models import Direction
from backend.schemas import (
    IngestResponse,
    PayloadRequest,
    RebuildResponse,
    RemediationCreate,
    RemediationDecision,
)
from backend.seed import sample_batch, sample_frames
from backend.service import ServiceError, TradingService
from backend.ws import ConnectionManager

T = TypeVar("T")
SYMBOL_PATTERN = r"^[A-Z0-9][A-Z0-9./-]{0,15}$"
IDEMPOTENCY_KEY_RE = re.compile(r"^[A-Za-z0-9._:-]{8,128}$")
Symbol = Annotated[str, Path(pattern=SYMBOL_PATTERN)]


async def _read_payload(request: Request, limit: int) -> str:
    declared = request.headers.get("content-length", "")
    if declared.isdigit() and int(declared) > limit:
        raise ServiceError(413, "PAYLOAD_TOO_LARGE", f"Body exceeds {limit} bytes")
    content_type = request.headers.get("content-type", "")
    if content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or isinstance(upload, str):
            raise ServiceError(422, "FILE_REQUIRED", "multipart body must include a 'file' field")
        data = await upload.read(limit + 1)
        if len(data) > limit:
            raise ServiceError(413, "PAYLOAD_TOO_LARGE", f"File exceeds {limit} bytes")
        # FIX is byte-oriented; latin-1 round-trips every byte so checksums stay exact
        return data.decode("latin-1")
    body = await request.body()
    if len(body) > limit:
        raise ServiceError(413, "PAYLOAD_TOO_LARGE", f"Body exceeds {limit} bytes")
    if content_type.startswith("application/json"):
        try:
            return PayloadRequest.model_validate_json(body).payload
        except ValidationError as exc:
            raise ServiceError(422, "INVALID_BODY", exc.errors()[0]["msg"]) from exc
    return body.decode("latin-1")


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    db = make_engine(settings.database_url)
    service = TradingService(settings, make_session_factory(db))
    manager = ConnectionManager()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        # Schema bootstrap is idempotent; managed environments would apply migrations instead
        await run_in_threadpool(init_db, db)
        app.state.recovery = await run_in_threadpool(service.recover)
        yield
        db.dispose()

    app = FastAPI(
        title="LOBX Matching Engine & FIX Inspector",
        version="1.0.0",
        description="Price-time priority limit order book with FIX 4.2/4.4 ingest, deterministic replay and maker-checker remediation.",
        lifespan=lifespan,
    )
    app.state.service = service
    app.state.manager = manager
    app.state.recovery = {}
    app.add_middleware(CORSMiddleware, allow_origins=settings.cors_origins, allow_methods=["*"], allow_headers=["*"])

    @app.exception_handler(ServiceError)
    async def _service_error(_request: Request, exc: ServiceError) -> JSONResponse:
        return JSONResponse(status_code=exc.status_code, content={"error": exc.code, "detail": exc.detail})

    async def call(fn: Callable[..., T], *args: Any, **kwargs: Any) -> T:
        return await run_in_threadpool(fn, *args, **kwargs)

    @app.get("/api/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "comp_id": settings.comp_id,
            "database": db.dialect.name,
            "ws_clients": manager.client_count,
            "recovery": app.state.recovery,
            "inbound_by_status": await call(service.counts),
        }

    @app.post("/api/fix/ingest", response_model=IngestResponse)
    async def ingest(request: Request, idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key")] = None) -> dict[str, Any]:
        if not idempotency_key:
            raise ServiceError(400, "IDEMPOTENCY_KEY_REQUIRED", "Idempotency-Key header is required")
        if not IDEMPOTENCY_KEY_RE.match(idempotency_key):
            raise ServiceError(400, "IDEMPOTENCY_KEY_INVALID", "Idempotency-Key must be 8-128 chars of [A-Za-z0-9._:-]")
        payload = await _read_payload(request, settings.max_payload_bytes)
        summary, events = await call(service.ingest, payload, idempotency_key)
        await manager.broadcast(events)
        return summary

    @app.post("/api/fix/decode")
    async def decode(body: PayloadRequest) -> dict[str, Any]:
        return await call(service.decode, body.payload)

    @app.get("/api/fix/sample")
    async def sample() -> dict[str, Any]:
        return {
            "payload": sample_batch(),
            "messages": len(sample_frames()),
            "description": "Opening auction-to-continuous flow across four buy/sell-side sessions: sweeps, partial fills, IOC remainder, "
            "qty-down amend, fat-finger collar breach, MsgSeqNum gap with PossDup gap fill, duplicate ClOrdID, stale SendingTime, "
            "and a checksum-corrupted frame.",
        }

    @app.get("/api/symbols")
    async def symbols() -> list[dict[str, Any]]:
        return await call(service.symbols)

    @app.get("/api/book/{symbol}")
    async def book(symbol: Symbol, depth: Annotated[int, Query(ge=1, le=100)] = 10) -> dict[str, Any]:
        return await call(service.book_view, symbol, depth)

    @app.post("/api/book/{symbol}/rebuild", response_model=RebuildResponse)
    async def rebuild(symbol: Symbol) -> dict[str, Any]:
        return await call(service.rebuild, symbol)

    @app.get("/api/trades")
    async def trades(
        limit: Annotated[int, Query(ge=1, le=500)] = 100,
        symbol: Annotated[str | None, Query(pattern=SYMBOL_PATTERN)] = None,
    ) -> list[dict[str, Any]]:
        return await call(service.trades, limit, symbol)

    @app.get("/api/executions")
    async def executions(
        limit: Annotated[int, Query(ge=1, le=1000)] = 100,
        symbol: Annotated[str | None, Query(pattern=SYMBOL_PATTERN)] = None,
    ) -> list[dict[str, Any]]:
        return await call(service.executions, limit, symbol)

    @app.get("/api/messages")
    async def messages(
        limit: Annotated[int, Query(ge=1, le=1000)] = 200,
        direction: Direction | None = None,
        status: ValidationStatus | None = None,
        sender: Annotated[str | None, Query(max_length=64)] = None,
        symbol: Annotated[str | None, Query(pattern=SYMBOL_PATTERN)] = None,
        before_id: Annotated[int | None, Query(gt=0)] = None,
    ) -> list[dict[str, Any]]:
        return await call(service.messages, limit=limit, direction=direction, status=status, sender=sender, symbol=symbol, before_id=before_id)

    @app.get("/api/messages/{message_id}")
    async def message_detail(message_id: Annotated[int, Path(gt=0)]) -> dict[str, Any]:
        return await call(service.message_detail, message_id)

    @app.get("/api/exceptions")
    async def exceptions(scope: Literal["open", "all"] = "open") -> list[dict[str, Any]]:
        return await call(service.exceptions, scope)

    @app.get("/api/remediation")
    async def remediations(limit: Annotated[int, Query(ge=1, le=500)] = 100) -> list[dict[str, Any]]:
        return await call(service.remediations, limit)

    @app.post("/api/remediation/create", status_code=201)
    async def create_remediation(body: RemediationCreate) -> dict[str, Any]:
        view, events = await call(service.create_remediation, body)
        await manager.broadcast(events)
        return view

    @app.post("/api/remediation/{request_id}/approve")
    async def approve_remediation(request_id: Annotated[int, Path(gt=0)], body: RemediationDecision) -> dict[str, Any]:
        view, events = await call(service.approve_remediation, request_id, body)
        await manager.broadcast(events)
        return view

    @app.post("/api/remediation/{request_id}/decline")
    async def decline_remediation(request_id: Annotated[int, Path(gt=0)], body: RemediationDecision) -> dict[str, Any]:
        view, events = await call(service.decline_remediation, request_id, body)
        await manager.broadcast(events)
        return view

    @app.websocket("/ws/market-data")
    async def market_data(ws: WebSocket) -> None:
        await manager.connect(ws, lambda: call(service.snapshot_all))
        try:
            while True:
                if await ws.receive_text() == "ping":
                    await ws.send_text('{"type":"pong"}')
        except WebSocketDisconnect:
            pass
        finally:
            manager.disconnect(ws)

    return app


app = create_app()
