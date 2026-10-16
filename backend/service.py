from __future__ import annotations

import hashlib
import threading
import time
from collections import Counter, deque
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, sessionmaker

from backend.config import Settings
from backend.event_log import SqlEventLog
from backend.fix_engine import dictionary as fd
from backend.fix_engine.order_book import (
    EngineResult,
    ExecType,
    ExecutionReport,
    MatchingEngine,
    Order,
    Report,
)
from backend.fix_engine.parser import (
    SUPPORTED_VERSIONS,
    IssueCode,
    ParsedMessage,
    RiskLimits,
    SessionValidator,
    Severity,
    ValidationIssue,
    ValidationResult,
    ValidationStatus,
    amend_message,
    encode_fix,
    format_avg_px,
    format_cents,
    format_utc_timestamp,
    logout,
    parse_fix,
    resend_request,
    session_reject,
    split_fix_stream,
    to_pipe,
)
from backend.models import (
    Direction,
    ExecutionRecord,
    FixMessage,
    IngestRequest,
    MessageSource,
    OrderRecord,
    RemediationAction,
    RemediationRequest,
    RemediationStatus,
)
from backend.schemas import RemediationCreate, RemediationDecision, RemediationOut

VS = ValidationStatus
ENGINE_REJECT_STATUSES = frozenset({VS.DUPLICATE_CLORDID, VS.BUSINESS_REJECT, VS.COLLAR_REJECT})
SESSION_REJECT_STATUSES = frozenset({VS.MALFORMED, VS.STALE_TIMESTAMP})
SILENT_STATUSES = frozenset({VS.VALID, VS.POSS_DUP, VS.BODY_LENGTH_ERR, VS.CHECKSUM_ERR})
NOT_REMEDIABLE = frozenset({VS.VALID, VS.POSS_DUP})
CANCEL_REPLACE_TAGS = frozenset({11, 38, 44, 58})
MAX_BIGINT = 2**63 - 1


class ServiceError(Exception):
    def __init__(self, status_code: int, code: str, detail: str) -> None:
        super().__init__(detail)
        self.status_code = status_code
        self.code = code
        self.detail = detail


def payload_sha256(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def _clip(value: str | None, size: int) -> str | None:
    return value[:size] if value is not None else None


def _db_seq(seq: int | None) -> int | None:
    return seq if seq is not None and 0 < seq <= MAX_BIGINT else None


def _iso(ts: datetime | None) -> str | None:
    return ts.isoformat() if ts else None


def _primary_detail(issues: list[dict[str, Any]], status: str) -> str | None:
    for issue in issues:
        if issue["status"] == status and issue["severity"] != Severity.INFO.value:
            return issue["detail"]
    return None


def _merge_issues(decoded: dict[str, Any], issues: list[dict[str, Any]]) -> dict[str, Any]:
    # Session-level findings (gaps, staleness, collars) are tagged onto the fields they concern
    known = {(i["code"], i["tag"]) for i in decoded["issues"]}
    extra = [i for i in issues if (i["code"], i.get("tag")) not in known]
    for f in decoded["fields"]:
        f["issues"] = f["issues"] + [i["code"] for i in extra if i.get("tag") is not None and i.get("tag") == f["tag"]]
    decoded["issues"] = decoded["issues"] + extra
    return decoded


def _describe(report: Report) -> str:
    if isinstance(report, ExecutionReport):
        return f"35=8 {report.exec_type.value}/{report.ord_status.value}"
    return f"35=9 CxlRejReason={report.reason}"


@dataclass(slots=True)
class _Batch:
    events: list[dict[str, Any]] = field(default_factory=list)
    books: dict[str, None] = field(default_factory=dict)
    executions: int = 0
    trades: int = 0


class TradingService:
    def __init__(self, settings: Settings, session_factory: sessionmaker[Session]) -> None:
        self.settings = settings
        self._session_factory = session_factory
        self._event_log = SqlEventLog(session_factory)
        # Single writer: validate -> log -> match -> persist runs under one lock so log order == match order
        self._lock = threading.RLock()
        self._reset_state()

    def _reset_state(self) -> None:
        s = self.settings
        self.engine = MatchingEngine(s.comp_id, event_log=self._event_log, reference_prices=s.reference_prices_cents)
        self.validator = SessionValidator(
            limits=RiskLimits(collar_bps=s.collar_bps, max_order_qty=s.max_order_qty, max_notional_cents=s.max_notional_cents),
            max_clock_skew=timedelta(seconds=s.max_clock_skew_seconds),
            reference_price=self.engine.reference_price,
        )
        self.tape: deque[dict[str, Any]] = deque(maxlen=s.tape_size)
        self.blotter: deque[dict[str, Any]] = deque(maxlen=s.tape_size)
        # Monotonic per-process sequence lets subscribers discard batches already folded into a snapshot
        self.event_seq = getattr(self, "event_seq", 0)
        for symbol in s.reference_prices_cents:
            self.engine.book(symbol)

    def recover(self) -> dict[str, Any]:
        with self._lock:
            self._reset_state()
            trades: list[dict[str, Any]] = []
            replayed = self.engine.recover_from_log(on_result=lambda r: trades.extend(t.to_dict() for t in r.trades))
            trades.sort(key=lambda t: (t["transact_time"], t["match_id"]))
            self.tape.extend(trades)

            inbound = 0
            with self._session_factory() as session:
                stmt = (
                    select(FixMessage.source, FixMessage.raw_payload)
                    .where(FixMessage.direction == Direction.INBOUND)
                    .order_by(FixMessage.id)
                    .execution_options(yield_per=1_000)
                )
                for source, raw in session.execute(stmt):
                    msg = parse_fix(raw)
                    if source is MessageSource.REMEDIATION:
                        if msg.sender_comp_id and msg.cl_ord_id:
                            self.validator.register_cl_ord_id(msg.sender_comp_id, msg.cl_ord_id)
                    else:
                        self.validator.validate(msg)
                    inbound += 1

                last_sent = (
                    select(FixMessage.target_comp_id, func.max(FixMessage.msg_seq_num))
                    .where(FixMessage.direction == Direction.OUTBOUND, FixMessage.target_comp_id.is_not(None))
                    .group_by(FixMessage.target_comp_id)
                )
                for target, seq in session.execute(last_sent):
                    if seq:
                        self.engine.sequencer.resume(target, seq)

                recent = session.scalars(select(ExecutionRecord).order_by(ExecutionRecord.id.desc()).limit(self.settings.tape_size)).all()
                self.blotter.extend(self._execution_view(r) for r in reversed(recent))
            return {"symbols": replayed, "inbound_messages": inbound}

    def _resync(self) -> None:
        # Memory can be ahead of a rolled-back transaction; rebuild from the committed log
        self.recover()

    def ingest(self, payload: str, idempotency_key: str) -> tuple[dict[str, Any], dict[str, Any]]:
        frames = split_fix_stream(payload)
        if not frames:
            raise ServiceError(422, "NO_FIX_MESSAGES", "Payload contains no frames beginning with 8=FIX")
        if len(frames) > self.settings.max_batch_messages:
            raise ServiceError(413, "BATCH_TOO_LARGE", f"{len(frames)} messages exceeds limit of {self.settings.max_batch_messages}")
        request_hash = payload_sha256(payload)

        with self._lock:
            with self._session_factory() as session:
                prior = session.get(IngestRequest, idempotency_key)
                if prior is not None:
                    if prior.request_sha256 != request_hash:
                        raise ServiceError(409, "IDEMPOTENCY_KEY_REUSED", "Idempotency-Key was already used with a different payload")
                    return {**prior.response, "replayed": True}, self._publish([])

            batch = _Batch()
            try:
                with self._session_factory() as session, session.begin():
                    items = [self._ingest_frame(session, raw, idempotency_key, batch) for raw in frames]
                    summary = {
                        "idempotency_key": idempotency_key,
                        "replayed": False,
                        "received": len(items),
                        "applied": sum(1 for i in items if i["applied"]),
                        "exceptions": sum(1 for i in items if i["status"] != VS.VALID.value),
                        "executions": batch.executions,
                        "trades": batch.trades,
                        "messages": items,
                    }
                    session.add(IngestRequest(idempotency_key=idempotency_key, request_sha256=request_hash, message_count=len(items), response=summary))
            except Exception:
                self._resync()
                raise
            return summary, self._finish(batch)

    def _ingest_frame(self, session: Session, raw: str, key: str, batch: _Batch) -> dict[str, Any]:
        msg = parse_fix(raw)
        verdict = self.validator.validate(msg)
        is_order_entry = msg.msg_type in fd.ORDER_ENTRY_MSG_TYPES
        applies = verdict.is_valid and is_order_entry
        row = self._log_inbound(session, msg, verdict.status, list(verdict.issues), applied=applies, source=MessageSource.GATEWAY, ingest_key=key)

        responses: list[str] = []
        if applies:
            responses = self._apply(session, row, self.engine.process(msg), batch)
        elif verdict.status in ENGINE_REJECT_STATUSES and is_order_entry and verdict.primary_issue:
            responses = self._apply(session, row, self.engine.reject_message(msg, verdict.primary_issue, ref=str(row.id)), batch)
        else:
            reply = self._session_reply(msg, verdict)
            if reply:
                out = self._log_outbound(session, reply)
                responses = [f"35={out.msg_type} {fd.enum_code(35, out.msg_type or '') or ''}".strip()]

        if not verdict.is_valid:
            batch.events.append({"type": "exception", "message": self._exception_view(row, None)})
        return {
            "id": row.id,
            "msg_seq_num": msg.msg_seq_num,
            "sender_comp_id": msg.sender_comp_id,
            "msg_type": msg.msg_type,
            "msg_type_name": msg.msg_type_name,
            "cl_ord_id": msg.cl_ord_id,
            "symbol": msg.symbol,
            "status": verdict.status.value,
            "applied": applies,
            "issues": [i.to_dict() for i in verdict.issues],
            "responses": responses,
        }

    def _session_reply(self, msg: ParsedMessage, verdict: ValidationResult) -> str | None:
        sender = msg.sender_comp_id
        if not sender or verdict.status in SILENT_STATUSES:
            return None
        venue = msg.target_comp_id or self.settings.comp_id
        begin = msg.begin_string if msg.begin_string in SUPPORTED_VERSIONS else "FIX.4.2"
        now = msg.sending_time or datetime.now(UTC)
        issue = verdict.primary_issue
        if verdict.status is VS.SEQ_GAP and verdict.gap:
            # Tag 7/16: request exactly the missing range
            return resend_request(begin, sender=venue, target=sender, seq=self.engine.sequencer.next(sender), sending_time=now, begin_seq=verdict.gap[0], end_seq=verdict.gap[1])
        if verdict.status is VS.SEQ_TOO_LOW and issue:
            return logout(begin, sender=venue, target=sender, seq=self.engine.sequencer.next(sender), sending_time=now, text=issue.detail)
        if issue and msg.msg_seq_num:
            return session_reject(msg, issue, sender=venue, seq=self.engine.sequencer.next(sender), sending_time=now)
        return None

    def _log_inbound(
        self,
        session: Session,
        msg: ParsedMessage,
        status: ValidationStatus,
        issues: list[ValidationIssue],
        *,
        applied: bool,
        source: MessageSource,
        ingest_key: str | None = None,
        replay_of_id: int | None = None,
    ) -> FixMessage:
        row = FixMessage(
            direction=Direction.INBOUND,
            source=source,
            **self._header_columns(msg),
            raw_payload=msg.normalized,
            parsed_tags={"tags": msg.tags(), "decoded": msg.decoded()},
            validation_status=status,
            validation_issues=[i.to_dict() for i in issues],
            engine_applied=applied,
            ingest_key=ingest_key,
            replay_of_id=replay_of_id,
        )
        session.add(row)
        session.flush()
        return row

    def _log_outbound(self, session: Session, raw: str) -> FixMessage:
        msg = parse_fix(raw)
        row = FixMessage(
            direction=Direction.OUTBOUND,
            source=MessageSource.ENGINE,
            **self._header_columns(msg),
            raw_payload=raw,
            parsed_tags={"tags": msg.tags(), "decoded": msg.decoded()},
            validation_status=VS.VALID,
            validation_issues=[],
            engine_applied=False,
        )
        session.add(row)
        session.flush()
        return row

    @staticmethod
    def _header_columns(msg: ParsedMessage) -> dict[str, Any]:
        return {
            "begin_string": _clip(msg.begin_string, 16),
            "msg_type": _clip(msg.msg_type, 4),
            "sender_comp_id": _clip(msg.sender_comp_id, 64),
            "target_comp_id": _clip(msg.target_comp_id, 64),
            "msg_seq_num": _db_seq(msg.msg_seq_num),
            "sending_time": msg.sending_time,
            "cl_ord_id": _clip(msg.cl_ord_id, 64),
            "orig_cl_ord_id": _clip(msg.orig_cl_ord_id, 64),
            "symbol": _clip(msg.symbol, 32),
        }

    def _apply(self, session: Session, inbound: FixMessage, result: EngineResult, batch: _Batch) -> list[str]:
        responses: list[str] = []
        for report in result.reports:
            out = self._log_outbound(session, self.engine.render(report))
            if isinstance(report, ExecutionReport):
                record = self._persist_execution(session, inbound, out, report)
                view = report.to_dict() | {"id": record.id, "log_id": out.id}
                self.blotter.append(view)
                batch.events.append({"type": "execution", "execution": view})
                batch.executions += 1
            else:
                batch.events.append({"type": "cancel_reject", "cancel_reject": report.to_dict() | {"log_id": out.id}})
            responses.append(_describe(report))
        for trade in result.trades:
            view = trade.to_dict()
            self.tape.append(view)
            batch.events.append({"type": "trade", "trade": view})
            batch.trades += 1
        if result.symbol and result.symbol in self.engine.books:
            batch.books[result.symbol] = None
        return responses

    def _persist_execution(self, session: Session, inbound: FixMessage, out: FixMessage, r: ExecutionReport) -> ExecutionRecord:
        if r.exec_type in (ExecType.NEW, ExecType.REPLACED) and r.order_id != "NONE":
            order = self.engine.order(r.order_id)
            session.add(
                OrderRecord(
                    order_id=r.order_id,
                    version=order.version if order is not None and r.exec_type is ExecType.REPLACED else 1,
                    cl_ord_id=_clip(r.cl_ord_id, 64),
                    orig_cl_ord_id=_clip(r.orig_cl_ord_id, 64),
                    sender_comp_id=_clip(r.client_comp_id, 64),
                    symbol=r.symbol,
                    side=r.fix_side,
                    ord_type=r.ord_type,
                    time_in_force=r.time_in_force,
                    price_cents=r.price_cents,
                    quantity=r.order_qty,
                    account=_clip(r.account, 64),
                    transact_time=r.transact_time,
                    source_message_id=inbound.id,
                )
            )
        record = ExecutionRecord(
            exec_id=r.exec_id,
            order_id=r.order_id,
            cl_ord_id=_clip(r.cl_ord_id, 64) or "",
            orig_cl_ord_id=_clip(r.orig_cl_ord_id, 64),
            sender_comp_id=_clip(r.client_comp_id, 64) or "",
            symbol=_clip(r.symbol, 32) or "",
            side=r.fix_side[:1],
            exec_type=r.exec_type,
            ord_status=r.ord_status,
            price_cents=r.price_cents,
            order_qty=r.order_qty,
            last_qty=r.last_qty,
            last_px_cents=r.last_px_cents,
            leaves_qty=r.leaves_qty,
            cum_qty=r.cum_qty,
            cum_notional_cents=r.cum_notional_cents,
            match_id=r.match_id,
            contra_order_id=r.contra_order_id,
            ord_rej_reason=r.ord_rej_reason,
            text=_clip(r.text, 255),
            transact_time=r.transact_time,
            source_message_id=inbound.id,
            outbound_message_id=out.id,
        )
        session.add(record)
        session.flush()
        return record

    def _finish(self, batch: _Batch) -> dict[str, Any]:
        events = batch.events
        cap = self.settings.broadcast_event_cap
        if len(events) > cap:
            events = [{"type": "notice", "text": f"{len(events) - cap} events coalesced into book snapshots"}, *events[-cap:]]
        for symbol in batch.books:
            book = self.engine.books.get(symbol)
            if book is not None:
                events.append({"type": "book", "book": book.snapshot(self.settings.book_depth)})
        return self._publish(events)

    def _publish(self, events: list[dict[str, Any]]) -> dict[str, Any]:
        with self._lock:
            if events:
                self.event_seq += 1
            return {"seq": self.event_seq, "events": events}

    @staticmethod
    def _execution_view(r: ExecutionRecord) -> dict[str, Any]:
        return {
            "type": "EXECUTION_REPORT",
            "id": r.id,
            "log_id": r.outbound_message_id,
            "exec_id": r.exec_id,
            "exec_type": r.exec_type.value,
            "ord_status": r.ord_status.value,
            "order_id": r.order_id,
            "cl_ord_id": r.cl_ord_id,
            "orig_cl_ord_id": r.orig_cl_ord_id,
            "client_comp_id": r.sender_comp_id,
            "symbol": r.symbol,
            "side": fd.enum_code(54, r.side) or r.side,
            "price_cents": r.price_cents,
            "order_qty": r.order_qty,
            "leaves_qty": r.leaves_qty,
            "cum_qty": r.cum_qty,
            "cum_notional_cents": r.cum_notional_cents,
            "avg_px": format_avg_px(r.cum_notional_cents, r.cum_qty),
            "last_qty": r.last_qty,
            "last_px_cents": r.last_px_cents,
            "match_id": r.match_id,
            "contra_order_id": r.contra_order_id,
            "ord_rej_reason": r.ord_rej_reason,
            "text": r.text,
            "transact_time": _iso(r.transact_time),
        }

    def symbols(self) -> list[dict[str, Any]]:
        with self._lock:
            out = []
            for symbol in sorted(self.engine.books):
                book = self.engine.books[symbol]
                spread_cents, spread_bps_e2 = book.spread()
                out.append(
                    {
                        "symbol": symbol,
                        "best_bid_cents": book.best_bid(),
                        "best_ask_cents": book.best_ask(),
                        "spread_cents": spread_cents,
                        "spread_bps_e2": spread_bps_e2,
                        "last_trade_px_cents": book.last_trade_px_cents,
                        "reference_price_cents": self.engine.reference_price(symbol),
                        "volume": book.volume,
                        "trade_count": book.trade_count,
                        "resting_orders": len(book.resting),
                    }
                )
            return out

    def book_view(self, symbol: str, depth: int) -> dict[str, Any]:
        with self._lock:
            book = self.engine.books.get(symbol)
            if book is None:
                raise ServiceError(404, "UNKNOWN_SYMBOL", f"No book for {symbol}")
            return book.snapshot(depth) | {"l3": book.l3(), "reference_price_cents": self.engine.reference_price(symbol)}

    def snapshot_all(self) -> dict[str, Any]:
        with self._lock:
            return {
                "seq": self.event_seq,
                "books": [self.engine.books[s].snapshot(self.settings.book_depth) for s in sorted(self.engine.books)],
                "trades": list(self.tape)[-200:],
                "executions": list(self.blotter)[-200:],
            }

    def trades(self, limit: int, symbol: str | None) -> list[dict[str, Any]]:
        with self._lock:
            rows = [t for t in self.tape if symbol is None or t["symbol"] == symbol]
        return rows[-limit:][::-1]

    def executions(self, limit: int, symbol: str | None) -> list[dict[str, Any]]:
        stmt = select(ExecutionRecord).order_by(ExecutionRecord.id.desc()).limit(limit)
        if symbol:
            stmt = stmt.where(ExecutionRecord.symbol == symbol)
        with self._session_factory() as session:
            return [self._execution_view(r) for r in session.scalars(stmt)]

    def rebuild(self, symbol: str) -> dict[str, Any]:
        with self._lock:
            if symbol not in self.engine.books:
                raise ServiceError(404, "UNKNOWN_SYMBOL", f"No book for {symbol}")
            started = time.perf_counter()
            result = self.engine.rebuild_book_from_log(symbol)
            elapsed_ms = int((time.perf_counter() - started) * 1000)
        return {
            "symbol": symbol,
            "messages_replayed": result.messages_replayed,
            "checksum": result.checksum,
            "live_checksum": result.live_checksum,
            "matches_live": result.matches_live,
            "elapsed_ms": elapsed_ms,
            "book": result.book.snapshot(self.settings.book_depth) | {"l3": result.book.l3()},
        }

    def decode(self, payload: str) -> dict[str, Any]:
        frames = split_fix_stream(payload)
        if not frames:
            raise ServiceError(422, "NO_FIX_MESSAGES", "Payload contains no frames beginning with 8=FIX")
        if len(frames) > self.settings.max_batch_messages:
            raise ServiceError(413, "BATCH_TOO_LARGE", f"{len(frames)} messages exceeds limit of {self.settings.max_batch_messages}")
        out: list[dict[str, Any]] = []
        with self._lock:
            # Dry run: isolated session state, live reference prices
            sandbox = SessionValidator(
                limits=self.validator.limits,
                max_clock_skew=self.validator.max_clock_skew,
                reference_price=self.engine.reference_price,
            )
            for index, raw in enumerate(frames):
                msg = parse_fix(raw)
                verdict = sandbox.validate(msg)
                decoded = _merge_issues(msg.to_dict(), [i.to_dict() for i in verdict.issues])
                primary = verdict.primary_issue
                out.append(
                    {
                        "index": index,
                        "status": verdict.status.value,
                        "root_cause": primary.detail if primary else None,
                        "raw_pipe": to_pipe(msg.normalized),
                        "decoded": decoded,
                    }
                )
        return {"messages": out, "counts": dict(Counter(m["status"] for m in out))}

    def messages(
        self,
        *,
        limit: int,
        direction: Direction | None,
        status: ValidationStatus | None,
        sender: str | None,
        symbol: str | None,
        before_id: int | None,
    ) -> list[dict[str, Any]]:
        stmt = select(FixMessage).order_by(FixMessage.id.desc()).limit(limit)
        if direction:
            stmt = stmt.where(FixMessage.direction == direction)
        if status:
            stmt = stmt.where(FixMessage.validation_status == status)
        if sender:
            stmt = stmt.where(FixMessage.sender_comp_id == sender)
        if symbol:
            stmt = stmt.where(FixMessage.symbol == symbol)
        if before_id:
            stmt = stmt.where(FixMessage.id < before_id)
        with self._session_factory() as session:
            return [self._message_summary(r) for r in session.scalars(stmt)]

    @staticmethod
    def _message_summary(row: FixMessage) -> dict[str, Any]:
        return {
            "id": row.id,
            "received_at": _iso(row.received_at),
            "direction": row.direction.value,
            "source": row.source.value,
            "msg_type": row.msg_type,
            "msg_type_name": fd.enum_code(35, row.msg_type or ""),
            "sender_comp_id": row.sender_comp_id,
            "target_comp_id": row.target_comp_id,
            "msg_seq_num": row.msg_seq_num,
            "cl_ord_id": row.cl_ord_id,
            "symbol": row.symbol,
            "status": row.validation_status.value,
            "engine_applied": row.engine_applied,
            "replay_of_id": row.replay_of_id,
            "raw_pipe": to_pipe(row.raw_payload),
        }

    def message_detail(self, message_id: int) -> dict[str, Any]:
        with self._session_factory() as session:
            row = session.get(FixMessage, message_id)
            if row is None:
                raise ServiceError(404, "MESSAGE_NOT_FOUND", f"fix_messages.id={message_id} not found")
            decoded = _merge_issues(parse_fix(row.raw_payload).to_dict(), row.validation_issues)
            return self._message_summary(row) | {"decoded": decoded, "issues": row.validation_issues}

    def exceptions(self, scope: str, limit: int = 500) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.scalars(
                select(FixMessage)
                .where(FixMessage.direction == Direction.INBOUND, FixMessage.validation_status != VS.VALID)
                .order_by(FixMessage.id.desc())
                .limit(limit)
            ).all()
            ids = [r.id for r in rows]
            requests = (
                session.scalars(select(RemediationRequest).where(RemediationRequest.exception_message_id.in_(ids)).order_by(RemediationRequest.id)).all()
                if ids
                else []
            )
        latest: dict[int, RemediationRequest] = {}
        executed: set[int] = set()
        for rq in requests:
            latest[rq.exception_message_id] = rq
            if rq.status is RemediationStatus.EXECUTED:
                executed.add(rq.exception_message_id)
        views = []
        with self._lock:
            for row in rows:
                resolved = row.id in executed
                if scope == "open" and (resolved or row.validation_status is VS.POSS_DUP):
                    continue
                views.append(self._exception_view(row, latest.get(row.id), resolved))
        return views

    def _exception_view(self, row: FixMessage, rq: RemediationRequest | None, resolved: bool = False) -> dict[str, Any]:
        status = row.validation_status.value
        return self._message_summary(row) | {
            "sending_time": _iso(row.sending_time),
            "root_cause": _primary_detail(row.validation_issues, status),
            "issues": row.validation_issues,
            "allowed_actions": [a.value for a in self._allowed_actions(row)],
            "resolved": resolved,
            "remediation": None
            if rq is None
            else {"id": rq.id, "status": rq.status.value, "action": rq.action.value, "maker_id": rq.maker_id, "checker_id": rq.checker_id},
        }

    def _allowed_actions(self, row: FixMessage) -> list[RemediationAction]:
        if row.validation_status in NOT_REMEDIABLE or row.msg_type not in fd.ORDER_ENTRY_MSG_TYPES:
            return []
        parsed = parse_fix(row.raw_payload)
        actions: list[RemediationAction] = []
        if not parsed.is_garbled and not parsed.is_malformed:
            actions.append(RemediationAction.REPLAY)
        if all(f.tag is not None for f in parsed.fields):
            actions.append(RemediationAction.AMEND_AND_REPLAY)
        if self._cancel_replace_target(parsed) is not None:
            actions.append(RemediationAction.CANCEL_REPLACE)
        return actions

    def _cancel_replace_target(self, parsed: ParsedMessage) -> Order | None:
        sender = parsed.sender_comp_id or ""
        for key in (parsed.orig_cl_ord_id, parsed.cl_ord_id):
            if key:
                order = self.engine.find_order(sender, key)
                if order is not None and order.is_open and order.is_limit:
                    return order
        return None

    def _pre_trade_checks(self, msg: ParsedMessage, bypass_risk: bool) -> list[ValidationIssue]:
        issues = list(self.validator.check_order_terms(msg))
        if not bypass_risk:
            issues += self.validator.check_risk(msg)
        if msg.cl_ord_id and self.engine.find_order(msg.sender_comp_id or "", msg.cl_ord_id) is not None:
            issues.append(ValidationIssue(IssueCode.DUPLICATE_CLORDID, Severity.ERROR, f"ClOrdID {msg.cl_ord_id!r} is already known to the engine", 11))
        return issues

    def _stage(self, parsed: ParsedMessage, req: RemediationCreate, row: FixMessage) -> str:
        if req.action is RemediationAction.REPLAY:
            if req.overrides:
                raise ServiceError(422, "OVERRIDES_NOT_ALLOWED", "REPLAY injects the original bytes; use AMEND_AND_REPLAY to change tags")
            if parsed.is_garbled or parsed.is_malformed:
                raise ServiceError(422, "FRAME_INVALID", "Original frame fails structural validation; use AMEND_AND_REPLAY to re-frame it")
            return parsed.normalized

        if req.action is RemediationAction.AMEND_AND_REPLAY:
            if not req.overrides and not parsed.is_garbled:
                raise ServiceError(422, "OVERRIDES_REQUIRED", "Provide at least one tag override")
            try:
                return amend_message(parsed, req.overrides)
            except ValueError as exc:
                raise ServiceError(422, "FRAME_INVALID", str(exc)) from exc

        order = self._cancel_replace_target(parsed)
        if order is None or order.price_cents is None:
            raise ServiceError(409, "NO_LIVE_ORDER", "No open LIMIT order is linked to this exception")
        unsupported = set(req.overrides) - CANCEL_REPLACE_TAGS
        if unsupported:
            raise ServiceError(422, "OVERRIDES_NOT_ALLOWED", f"CANCEL_REPLACE accepts tags 11, 38, 44, 58 only (got {sorted(unsupported)})")
        now = datetime.now(UTC)
        body = [
            (41, order.cl_ord_id),
            (11, req.overrides.get(11, f"{order.cl_ord_id}-R{row.id}")[:64]),
            (21, "1"),
            (55, order.symbol),
            (54, order.fix_side),
            (38, req.overrides.get(38, str(order.order_qty))),
            (40, "2"),
            (44, req.overrides.get(44, format_cents(order.price_cents))),
            (59, order.time_in_force),
            (60, format_utc_timestamp(now)),
        ]
        if 58 in req.overrides:
            body.append((58, req.overrides[58]))
        # Synthetic resend on the client's behalf: PossDup with the exception's original SendingTime
        return encode_fix(
            order.begin_string,
            "G",
            sender=order.client_comp_id,
            target=order.venue_comp_id,
            seq=parsed.msg_seq_num or 1,
            sending_time=now,
            header_extra=[(43, "Y"), (122, format_utc_timestamp(parsed.sending_time or now))],
            body=body,
        )

    def create_remediation(self, req: RemediationCreate) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._lock, self._session_factory() as session, session.begin():
            row = session.get(FixMessage, req.exception_message_id)
            if row is None or row.direction is not Direction.INBOUND:
                raise ServiceError(404, "EXCEPTION_NOT_FOUND", f"Inbound message {req.exception_message_id} not found")
            if row.validation_status in NOT_REMEDIABLE or row.msg_type not in fd.ORDER_ENTRY_MSG_TYPES:
                raise ServiceError(409, "NOT_REMEDIABLE", f"{row.validation_status.value} 35={row.msg_type} cannot be replayed into the engine")
            existing = session.scalars(
                select(RemediationRequest).where(
                    RemediationRequest.exception_message_id == row.id,
                    RemediationRequest.status.in_([RemediationStatus.PENDING_APPROVAL, RemediationStatus.EXECUTED]),
                )
            ).first()
            if existing is not None:
                raise ServiceError(409, "REMEDIATION_EXISTS", f"Request #{existing.id} is already {existing.status.value}")

            staged = self._stage(parse_fix(row.raw_payload), req, row)
            staged_msg = parse_fix(staged)
            if staged_msg.issues:
                raise ServiceError(422, "STAGED_MESSAGE_INVALID", "; ".join(i.detail for i in staged_msg.issues))
            preview = self._pre_trade_checks(staged_msg, req.bypass_risk_checks)

            rq = RemediationRequest(
                exception_message_id=row.id,
                action=req.action,
                status=RemediationStatus.PENDING_APPROVAL,
                staged_payload=staged,
                staged_payload_sha256=payload_sha256(staged),
                overrides={str(tag): value for tag, value in req.overrides.items()},
                bypass_risk_checks=req.bypass_risk_checks,
                reason=req.reason.strip(),
                maker_id=req.maker_id,
            )
            session.add(rq)
            session.flush()
            session.refresh(rq)
            view = self._remediation_view(rq, preview)
            return view, self._publish([{"type": "remediation", "remediation": view}])

    @staticmethod
    def _assert_decidable(rq: RemediationRequest | None, checker_id: str) -> RemediationRequest:
        if rq is None:
            raise ServiceError(404, "REMEDIATION_NOT_FOUND", "Remediation request not found")
        if rq.status is not RemediationStatus.PENDING_APPROVAL:
            raise ServiceError(409, "ALREADY_DECIDED", f"Request #{rq.id} is {rq.status.value}")
        if checker_id.lower() == rq.maker_id.lower():
            raise ServiceError(403, "FOUR_EYES_VIOLATION", f"Checker must be a different operator than maker {rq.maker_id}")
        return rq

    def approve_remediation(self, request_id: int, decision: RemediationDecision) -> tuple[dict[str, Any], dict[str, Any]]:
        batch = _Batch()
        with self._lock:
            try:
                with self._session_factory() as session, session.begin():
                    rq = self._assert_decidable(session.get(RemediationRequest, request_id, with_for_update=True), decision.checker_id)
                    if payload_sha256(rq.staged_payload) != rq.staged_payload_sha256:
                        raise ServiceError(409, "STAGED_PAYLOAD_TAMPERED", "Staged payload no longer matches the hash the maker submitted")
                    msg = parse_fix(rq.staged_payload)
                    blocking = [i for i in [*msg.issues, *self._pre_trade_checks(msg, rq.bypass_risk_checks)] if i.severity is Severity.ERROR]

                    result_message_id: int | None = None
                    if blocking:
                        status = RemediationStatus.FAILED
                        outcome: dict[str, Any] = {"issues": [i.to_dict() for i in blocking]}
                    else:
                        row = self._log_inbound(
                            session, msg, VS.VALID, [], applied=True, source=MessageSource.REMEDIATION, replay_of_id=rq.exception_message_id
                        )
                        responses = self._apply(session, row, self.engine.process(msg), batch)
                        if msg.sender_comp_id and msg.cl_ord_id:
                            self.validator.register_cl_ord_id(msg.sender_comp_id, msg.cl_ord_id)
                        status = RemediationStatus.EXECUTED
                        result_message_id = row.id
                        outcome = {"responses": responses, "executions": batch.executions, "trades": batch.trades}
                    # Decision columns change together: the four-eyes CHECKs reject any partial state
                    rq.status = status
                    rq.checker_id = decision.checker_id
                    rq.checker_comment = decision.comment
                    rq.decided_at = datetime.now(UTC)
                    rq.result_message_id = result_message_id
                    rq.result = outcome
                    session.flush()
                    view = self._remediation_view(rq)
            except ServiceError:
                raise
            except Exception:
                self._resync()
                raise
            batch.events.append({"type": "remediation", "remediation": view})
            return view, self._finish(batch)

    def decline_remediation(self, request_id: int, decision: RemediationDecision) -> tuple[dict[str, Any], dict[str, Any]]:
        with self._lock, self._session_factory() as session, session.begin():
            rq = self._assert_decidable(session.get(RemediationRequest, request_id, with_for_update=True), decision.checker_id)
            rq.status = RemediationStatus.DECLINED
            rq.checker_id = decision.checker_id
            rq.checker_comment = decision.comment
            rq.decided_at = datetime.now(UTC)
            session.flush()
            view = self._remediation_view(rq)
            return view, self._publish([{"type": "remediation", "remediation": view}])

    def remediations(self, limit: int) -> list[dict[str, Any]]:
        with self._session_factory() as session:
            rows = session.scalars(select(RemediationRequest).order_by(RemediationRequest.id.desc()).limit(limit)).all()
            return [self._remediation_view(r) for r in rows]

    @staticmethod
    def _remediation_view(rq: RemediationRequest, preview: list[ValidationIssue] | None = None) -> dict[str, Any]:
        out = RemediationOut.model_validate(rq).model_dump(mode="json")
        out["preview"] = [i.to_dict() for i in preview or []]
        out["staged_pipe"] = to_pipe(rq.staged_payload)
        return out

    def counts(self) -> dict[str, Any]:
        with self._session_factory() as session:
            by_status = dict(
                session.execute(
                    select(FixMessage.validation_status, func.count()).where(FixMessage.direction == Direction.INBOUND).group_by(FixMessage.validation_status)
                ).all()
            )
        return {status.value: n for status, n in by_status.items()}

