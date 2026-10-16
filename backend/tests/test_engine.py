from __future__ import annotations

import random
import threading
from collections.abc import Iterable
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sqlalchemy import select

from backend.fix_engine.order_book import CancelReject, ExecType, ExecutionReport, MatchingEngine, OrderBook, OrdStatus
from backend.fix_engine.parser import SessionValidator, parse_fix
from backend.models import Direction, FixMessage, RemediationAction
from backend.schemas import RemediationCreate, RemediationDecision
from backend.seed import BUY, IOC, SELL, ClientSession, sample_batch
from backend.service import ServiceError, TradingService

T0 = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)
FOK = "4"


class MemoryLog:
    def __init__(self) -> None:
        self.rows: list[tuple[int, str, str]] = []

    def append(self, raw: str) -> None:
        self.rows.append((len(self.rows) + 1, parse_fix(raw).symbol or "", raw))

    def iter_applied(self, symbol: str) -> Iterable[tuple[int, str]]:
        return [(i, raw) for i, sym, raw in self.rows if sym == symbol]

    def applied_symbols(self) -> list[str]:
        return sorted({sym for _, sym, _ in self.rows})


class Harness:
    def __init__(self) -> None:
        self.log = MemoryLog()
        self.engine = MatchingEngine("LOBX", event_log=self.log)
        self.sessions: dict[str, ClientSession] = {}
        self.clock = T0

    def session(self, comp_id: str, version: str = "FIX.4.2") -> ClientSession:
        return self.sessions.setdefault(comp_id, ClientSession(comp_id, version))

    def tick(self) -> datetime:
        self.clock += timedelta(milliseconds=10)
        return self.clock

    def send(self, raw: str):
        self.log.append(raw)
        return self.engine.process(parse_fix(raw))

    def new(self, comp_id: str, cl: str, side: str, qty: int, px: int | None, tif: str = "0", symbol: str = "AAPL", version: str = "FIX.4.2"):
        return self.send(self.session(comp_id, version).new_order(self.tick(), cl, symbol, side, qty, px, tif=tif))

    def cancel(self, comp_id: str, cl: str, orig: str, side: str, symbol: str = "AAPL"):
        return self.send(self.session(comp_id).cancel(self.tick(), cl, orig, symbol, side, 0))

    def replace(self, comp_id: str, cl: str, orig: str, side: str, qty: int, px: int, symbol: str = "AAPL"):
        return self.send(self.session(comp_id).replace(self.tick(), cl, orig, symbol, side, qty, px))


def assert_book_invariants(book: OrderBook) -> None:
    bid, ask = book.best_bid(), book.best_ask()
    assert bid is None or ask is None or bid < ask, f"crossed book {bid} >= {ask}"
    resting = 0
    for side in (book.bids, book.asks):
        previous = None
        for level in side.levels():
            assert level.orders, "empty price level left on book"
            assert level.total_qty == sum(o.leaves_qty for o in level.orders.values())
            if previous is not None:
                assert (level.price_cents < previous) if side is book.bids else (level.price_cents > previous)
            previous = level.price_cents
            priorities = [o.priority for o in level.orders.values()]
            assert priorities == sorted(priorities), "FIFO order violated within level"
            for o in level.orders.values():
                assert o.is_open and o.price_cents == level.price_cents
                assert o.cum_qty + o.leaves_qty == o.order_qty
                resting += 1
    assert resting == len(book.resting)


def assert_no_floats(value: Any) -> None:
    if isinstance(value, dict):
        for v in value.values():
            assert_no_floats(v)
    elif isinstance(value, list | tuple):
        for v in value:
            assert_no_floats(v)
    else:
        assert not isinstance(value, float), f"float leaked into money path: {value!r}"


def test_price_time_priority_fifo_sweep() -> None:
    h = Harness()
    h.new("MM1", "S1", SELL, 200, 18710)
    h.new("MM2", "S2", SELL, 100, 18710)
    h.new("MM1", "S3", SELL, 500, 18720)
    result = h.new("BUY", "B1", BUY, 450, 18720)
    assert [(t.price_cents, t.qty, t.sell_comp_id) for t in result.trades] == [(18710, 200, "MM1"), (18710, 100, "MM2"), (18720, 150, "MM1")]
    final = result.executions[-2]
    assert final.cl_ord_id == "B1" and final.ord_status is OrdStatus.FILLED
    assert final.cum_notional_cents == 300 * 18710 + 150 * 18720
    assert final.avg_px == "187.133333"
    book = h.engine.book("AAPL")
    assert book.depth()["asks"] == [{"price_cents": 18720, "qty": 350, "orders": 1}]
    assert_book_invariants(book)


def test_partial_fill_leaves_remainder_resting() -> None:
    h = Harness()
    h.new("MM", "S1", SELL, 300, 43125, symbol="MSFT")
    result = h.new("BWQ", "B1", BUY, 2_000, 43125, symbol="MSFT")
    taker = [r for r in result.executions if r.cl_ord_id == "B1"]
    assert [r.exec_type for r in taker] == [ExecType.NEW, ExecType.TRADE]
    assert taker[-1].ord_status is OrdStatus.PARTIALLY_FILLED and taker[-1].leaves_qty == 1_700
    assert h.engine.book("MSFT").best_bid() == 43125


def test_ioc_fok_and_market_orders() -> None:
    h = Harness()
    h.new("MM", "S1", SELL, 300, 22860)
    ioc = h.new("T", "I1", BUY, 500, 22860, tif=IOC)
    assert ioc.executions[-1].exec_type is ExecType.CANCELED and ioc.executions[-1].cum_qty == 300
    assert h.engine.book("AAPL").best_bid() is None

    h.new("MM", "S2", SELL, 100, 22870)
    fok = h.new("T", "F1", BUY, 500, 22870, tif=FOK)
    assert not fok.trades and fok.executions[-1].exec_type is ExecType.CANCELED
    assert h.engine.book("AAPL").best_ask() == 22870

    market = h.new("T", "M1", BUY, 250, None)
    assert market.trades[0].qty == 100 and market.executions[-1].text.startswith("Market order remainder canceled")


def test_replace_priority_semantics() -> None:
    h = Harness()
    h.new("A", "A1", BUY, 300, 18690)
    h.new("B", "B1", BUY, 300, 18690)
    h.replace("A", "A2", "A1", BUY, 200, 18690)
    level = h.engine.book("AAPL").bids.level(18690)
    assert [o.cl_ord_id for o in level.orders.values()] == ["A2", "B1"], "qty-down must keep queue position"
    h.replace("A", "A3", "A2", BUY, 400, 18690)
    assert [o.cl_ord_id for o in level.orders.values()] == ["B1", "A3"], "qty-up must re-queue"
    h.new("S", "S1", SELL, 100, 18700)
    crossing = h.replace("A", "A4", "A3", BUY, 400, 18700)
    assert crossing.executions[0].exec_type is ExecType.REPLACED and crossing.trades[0].price_cents == 18700
    assert_book_invariants(h.engine.book("AAPL"))


def test_cancel_and_cancel_reject_reasons() -> None:
    h = Harness()
    h.new("A", "A1", BUY, 100, 18690)
    live = h.cancel("A", "A1x", "A1", BUY)
    assert live.executions[0].ord_status is OrdStatus.CANCELED and live.book_changed
    late = h.cancel("A", "A1y", "A1", BUY)
    unknown = h.cancel("A", "A1z", "NOPE", BUY)
    wrong_side = h.new("A", "A2", BUY, 100, 18690)
    mismatch = h.cancel("A", "A2x", "A2", SELL)
    assert isinstance(late.reports[0], CancelReject) and late.reports[0].reason == "0"
    assert isinstance(unknown.reports[0], CancelReject) and unknown.reports[0].reason == "1"
    assert wrong_side.executions and isinstance(mismatch.reports[0], CancelReject)
    replace_below_cum = h.replace("A", "A2y", "A2", BUY, 0, 18690)
    assert isinstance(replace_below_cum.reports[0], CancelReject)


def test_fix42_and_fix44_execution_report_rendering() -> None:
    h = Harness()
    h.new("MM", "S1", SELL, 100, 22855, version="FIX.4.4")
    result = h.new("NB", "B1", BUY, 300, 22855, version="FIX.4.2")
    taker_trade, maker_trade = result.executions[1], result.executions[2]
    v42 = parse_fix(h.engine.render(taker_trade))
    v44 = parse_fix(h.engine.render(maker_trade))
    assert v42.issues == [] and v44.issues == []
    assert (v42.get(20), v42.get(150), v42.get(39), v42.get(32), v42.get(31)) == ("0", "1", "1", "100", "228.55")
    assert (v44.has(20), v44.get(150), v44.get(39)) == (False, "F", "2")
    replaced = h.replace("NB", "B2", "B1", BUY, 250, 22850)
    assert parse_fix(h.engine.render(replaced.executions[0])).get(39) == "5"


def _random_flow(seed: int, count: int) -> list[str]:
    rng = random.Random(seed)
    sessions = [ClientSession(f"FIRM{i}", "FIX.4.4" if i % 2 else "FIX.4.2") for i in range(6)]
    mids = {"AAPL": 22850, "MSFT": 43120, "NVDA": 13840}
    live: list[tuple[ClientSession, str, str, str]] = []
    clock = T0
    frames: list[str] = []
    for n in range(count):
        clock += timedelta(milliseconds=rng.randint(1, 40))
        s = rng.choice(sessions)
        roll = rng.random()
        if live and roll < 0.15:
            owner, cl, symbol, side = live.pop(rng.randrange(len(live)))
            frames.append(owner.cancel(clock, f"X{n}", cl, symbol, side, 0))
        elif live and roll < 0.30:
            idx = rng.randrange(len(live))
            owner, cl, symbol, side = live[idx]
            new_cl = f"R{n}"
            px = mids[symbol] + rng.randint(-15, 15)
            frames.append(owner.replace(clock, new_cl, cl, symbol, side, rng.choice([100, 200, 300, 500, 800]), px))
            live[idx] = (owner, new_cl, symbol, side)
        else:
            symbol = rng.choice(list(mids))
            side = rng.choice([BUY, SELL])
            tif = rng.choices(["0", IOC, FOK], weights=[8, 2, 1])[0]
            px = None if rng.random() < 0.05 else mids[symbol] + rng.randint(-12, 12)
            cl = f"N{n}"
            frames.append(s.new_order(clock, cl, symbol, side, rng.choice([100, 200, 300, 500, 1_000]), px, tif=tif if px else IOC))
            if px is not None and tif == "0":
                live.append((s, cl, symbol, side))
    return frames


def test_randomized_flow_is_deterministic_and_preserves_invariants() -> None:
    frames = _random_flow(seed=20261016, count=3_000)
    first, second = Harness(), Harness()
    first_out: list[str] = []
    second_out: list[str] = []
    buys = sells = 0
    for raw in frames:
        for harness, sink in ((first, first_out), (second, second_out)):
            result = harness.send(raw)
            sink.extend(harness.engine.render(r) for r in result.reports)
            for report in result.reports:
                assert_no_floats(report.to_dict())
            if harness is first:
                for trade in result.trades:
                    assert trade.price_cents > 0 and trade.qty > 0
                buys += sum(r.last_qty for r in result.executions if r.exec_type is ExecType.TRADE and r.fix_side == BUY)
                sells += sum(r.last_qty for r in result.executions if r.exec_type is ExecType.TRADE and r.fix_side == SELL)
        assert_book_invariants(first.engine.book(parse_fix(raw).symbol or "AAPL"))

    assert buys == sells > 0, "every print must have equal buy and sell quantity"
    assert first_out == second_out, "identical input must produce byte-identical outbound FIX"
    for symbol in ("AAPL", "MSFT", "NVDA"):
        assert first.engine.book_checksum(symbol) == second.engine.book_checksum(symbol)
        rebuilt = first.engine.rebuild_book_from_log(symbol)
        assert rebuilt.matches_live and rebuilt.book.l3() == first.engine.book(symbol).l3()

    recovered = MatchingEngine("LOBX", event_log=first.log)
    recovered.recover_from_log()
    assert all(recovered.book_checksum(s) == first.engine.book_checksum(s) for s in ("AAPL", "MSFT", "NVDA"))


def test_rebuild_detects_tampered_log() -> None:
    h = Harness()
    h.new("MM", "S1", SELL, 100, 22855)
    idx, sym, raw = h.log.rows[0]
    h.log.rows[0] = (idx, sym, raw.replace("38=100", "38=900"))
    with pytest.raises(Exception, match="framing"):
        h.engine.rebuild_book_from_log("AAPL")


def _session_batch(comp_id: str, symbol: str, side: str, base_px: int, n: int, start: datetime) -> str:
    s = ClientSession(comp_id, "FIX.4.4")
    lines = [s.logon(start, reset=True)]
    for i in range(n):
        lines.append(s.new_order(start + timedelta(milliseconds=i + 1), f"{comp_id}-{i}", symbol, side, 100, base_px + (i % 5)))
    return "\n".join(lines)


def test_concurrent_ingest_is_serialized_and_replayable(service: TradingService) -> None:
    start = T0
    payloads = []
    for i in range(8):
        side = BUY if i % 2 == 0 else SELL
        # Overlapping bands so concurrent batches cross each other
        px = 22840 if side == BUY else 22838
        payloads.append(_session_batch(f"DESK{i}", "AAPL", side, px, 40, start))
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda item: service.ingest(item[1], f"concurrent-{item[0]:04d}"), enumerate(payloads)))

    assert all(summary["exceptions"] == 0 for summary, _ in results)
    assert sum(summary["applied"] for summary, _ in results) == 8 * 40
    rebuilt = service.rebuild("AAPL")
    assert rebuilt["matches_live"] and rebuilt["messages_replayed"] == 320
    assert_book_invariants(service.engine.book("AAPL"))
    assert service.engine.book("AAPL").trade_count > 0


def test_concurrent_duplicate_idempotency_key_applies_once(service: TradingService) -> None:
    payload = sample_batch()
    barrier = threading.Barrier(6)

    def submit(_: int) -> dict[str, Any]:
        barrier.wait()
        return service.ingest(payload, "same-key-0001")[0]

    with ThreadPoolExecutor(max_workers=6) as pool:
        summaries = list(pool.map(submit, range(6)))
    assert sum(1 for s in summaries if not s["replayed"]) == 1
    assert len({tuple(m["id"] for m in s["messages"]) for s in summaries}) == 1
    with service._session_factory() as session:
        inbound = session.scalars(select(FixMessage.id).where(FixMessage.direction == Direction.INBOUND)).all()
    assert len(inbound) == 25
    with pytest.raises(ServiceError) as exc:
        service.ingest(payload + "\n", "same-key-0001")
    assert exc.value.code == "IDEMPOTENCY_KEY_REUSED"


def test_concurrent_approvals_have_single_winner(service: TradingService) -> None:
    summary, _ = service.ingest(sample_batch(), "seed-sample-01")
    gap = next(m for m in summary["messages"] if m["status"] == "SEQ_GAP")
    view, _ = service.create_remediation(
        RemediationCreate(exception_message_id=gap["id"], action=RemediationAction.REPLAY, maker_id="ops.maker", reason="Counterparty confirmed 34=7 is live")
    )
    barrier = threading.Barrier(5)

    def approve(i: int) -> str:
        barrier.wait()
        try:
            return service.approve_remediation(view["id"], RemediationDecision(checker_id=f"ops.checker{i}"))[0]["status"]
        except ServiceError as exc:
            return exc.code

    with ThreadPoolExecutor(max_workers=5) as pool:
        outcomes = list(pool.map(approve, range(5)))
    assert outcomes.count("EXECUTED") == 1 and outcomes.count("ALREADY_DECIDED") == 4
    assert service.rebuild("AAPL")["matches_live"]
    assert service.engine.book("AAPL").best_bid() == 22850


def test_failed_commit_resyncs_engine_from_log(service: TradingService, monkeypatch: pytest.MonkeyPatch) -> None:
    service.ingest(_session_batch("DESKA", "MSFT", SELL, 43120, 5, T0), "resync-base-1")
    before = service.engine.book_checksum("MSFT")
    original = service._persist_execution
    calls = {"n": 0}

    def flaky(*args: Any, **kwargs: Any):
        calls["n"] += 1
        if calls["n"] == 3:
            raise RuntimeError("simulated database failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(service, "_persist_execution", flaky)
    with pytest.raises(RuntimeError):
        service.ingest(_session_batch("DESKB", "MSFT", BUY, 43120, 5, T0 + timedelta(seconds=1)), "resync-fail-1")
    assert service.engine.book_checksum("MSFT") == before, "engine must roll back to the committed log"
    assert service.rebuild("MSFT")["matches_live"]


def test_engine_rejects_never_touch_book_counters() -> None:
    h = Harness()
    h.new("MM", "S1", SELL, 100, 22855)
    before = h.engine.book_checksum("AAPL")
    v = SessionValidator(reference_price=lambda _s: 22850)
    raw = ClientSession("FAT", "FIX.4.2").new_order(T0, "F1", "AAPL", BUY, 100, 228_550)
    verdict = v.validate(parse_fix(raw))
    rejected = h.engine.reject_message(parse_fix(raw), verdict.primary_issue, ref="77")
    report = rejected.reports[0]
    assert isinstance(report, ExecutionReport) and report.exec_id == "REJ-77" and report.ord_rej_reason == "3"
    assert h.engine.book_checksum("AAPL") == before
