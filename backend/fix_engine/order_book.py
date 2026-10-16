from __future__ import annotations

import hashlib
from bisect import bisect_left, insort
from collections import OrderedDict
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from backend.fix_engine.dictionary import enum_code
from backend.fix_engine.parser import (
    OutboundSequencer,
    ParsedMessage,
    ValidationIssue,
    cxl_rej_reason,
    encode_fix,
    format_avg_px,
    format_cents,
    format_utc_timestamp,
    ord_rej_reason,
    parse_fix,
)

_EPOCH = datetime(1970, 1, 1, tzinfo=UTC)
RESTING_TIFS: frozenset[str] = frozenset({"0", "1"})
TIF_IOC = "3"
TIF_FOK = "4"
ORD_TYPE_MARKET = "1"
ORD_TYPE_LIMIT = "2"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"

    @classmethod
    def from_fix(cls, code: str | None) -> Side:
        if code == "1":
            return cls.BUY
        # 54=5/6 short sales rest on the offer side
        if code in ("2", "5", "6"):
            return cls.SELL
        raise ValueError(f"unsupported Side(54)={code!r}")


class OrdStatus(StrEnum):
    NEW = "NEW"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCELED = "CANCELED"
    REJECTED = "REJECTED"


class ExecType(StrEnum):
    NEW = "NEW"
    TRADE = "TRADE"
    CANCELED = "CANCELED"
    REPLACED = "REPLACED"
    REJECTED = "REJECTED"


_ORD_STATUS_FIX: dict[OrdStatus, str] = {
    OrdStatus.NEW: "0",
    OrdStatus.PARTIALLY_FILLED: "1",
    OrdStatus.FILLED: "2",
    OrdStatus.CANCELED: "4",
    OrdStatus.REJECTED: "8",
}
_EXEC_TYPE_FIX: dict[ExecType, str] = {
    ExecType.NEW: "0",
    ExecType.CANCELED: "4",
    ExecType.REPLACED: "5",
    ExecType.REJECTED: "8",
}


class LogIntegrityError(RuntimeError):
    pass


@dataclass(slots=True)
class Order:
    order_id: str
    symbol: str
    side: Side
    fix_side: str
    ord_type: str
    time_in_force: str
    price_cents: int | None
    order_qty: int
    leaves_qty: int
    cl_ord_id: str
    orig_cl_ord_id: str | None
    client_comp_id: str
    venue_comp_id: str
    begin_string: str
    account: str | None
    transact_time: datetime
    cum_qty: int = 0
    cum_notional_cents: int = 0
    status: OrdStatus = OrdStatus.NEW
    priority: int = 0
    version: int = 1

    @property
    def is_open(self) -> bool:
        return self.status in (OrdStatus.NEW, OrdStatus.PARTIALLY_FILLED) and self.leaves_qty > 0

    @property
    def is_limit(self) -> bool:
        return self.ord_type == ORD_TYPE_LIMIT

    def apply_fill(self, qty: int, price_cents: int) -> None:
        self.leaves_qty -= qty
        self.cum_qty += qty
        self.cum_notional_cents += qty * price_cents
        self.status = OrdStatus.FILLED if self.leaves_qty == 0 else OrdStatus.PARTIALLY_FILLED

    def l3(self, queue_position: int) -> dict[str, Any]:
        return {
            "order_id": self.order_id,
            "cl_ord_id": self.cl_ord_id,
            "client_comp_id": self.client_comp_id,
            "side": self.side.value,
            "price_cents": self.price_cents,
            "order_qty": self.order_qty,
            "leaves_qty": self.leaves_qty,
            "cum_qty": self.cum_qty,
            "time_in_force": self.time_in_force,
            "priority": self.priority,
            "queue_position": queue_position,
            "transact_time": self.transact_time.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class ExecutionReport:
    exec_id: str
    exec_type: ExecType
    ord_status: OrdStatus
    order_id: str
    cl_ord_id: str
    orig_cl_ord_id: str | None
    client_comp_id: str
    venue_comp_id: str
    begin_string: str
    symbol: str
    fix_side: str
    ord_type: str
    time_in_force: str
    price_cents: int | None
    order_qty: int
    leaves_qty: int
    cum_qty: int
    cum_notional_cents: int
    last_qty: int
    last_px_cents: int | None
    transact_time: datetime
    account: str | None = None
    match_id: str | None = None
    contra_order_id: str | None = None
    ord_rej_reason: str | None = None
    text: str | None = None

    msg_type = "8"

    @property
    def avg_px(self) -> str:
        return format_avg_px(self.cum_notional_cents, self.cum_qty)

    def _fix_exec_type(self, v44: bool) -> str:
        if self.exec_type is ExecType.TRADE:
            if v44:
                return "F"
            return "2" if self.ord_status is OrdStatus.FILLED else "1"
        return _EXEC_TYPE_FIX[self.exec_type]

    def _fix_ord_status(self, v44: bool) -> str:
        # 4.2 acknowledges a replace with OrdStatus=5; 4.4 reports the live state
        if self.exec_type is ExecType.REPLACED and not v44:
            return "5"
        return _ORD_STATUS_FIX[self.ord_status]

    def fix_body(self) -> list[tuple[int, str]]:
        v44 = self.begin_string == "FIX.4.4"
        body: list[tuple[int, str]] = [(37, self.order_id), (11, self.cl_ord_id)]
        if self.orig_cl_ord_id:
            body.append((41, self.orig_cl_ord_id))
        body.append((17, self.exec_id))
        if not v44:
            body.append((20, "0"))
        body.append((150, self._fix_exec_type(v44)))
        body.append((39, self._fix_ord_status(v44)))
        if self.ord_rej_reason is not None:
            body.append((103, self.ord_rej_reason))
        if self.account:
            body.append((1, self.account))
        body += [(55, self.symbol), (54, self.fix_side), (38, str(self.order_qty)), (40, self.ord_type)]
        if self.price_cents is not None:
            body.append((44, format_cents(self.price_cents)))
        body.append((59, self.time_in_force))
        if self.last_qty and self.last_px_cents is not None:
            body += [(32, str(self.last_qty)), (31, format_cents(self.last_px_cents))]
        elif not v44:
            # 4.2 counterparties commonly require LastShares/LastPx on every report
            body += [(32, "0"), (31, "0")]
        body += [
            (151, str(self.leaves_qty)),
            (14, str(self.cum_qty)),
            (6, self.avg_px),
            (60, format_utc_timestamp(self.transact_time)),
        ]
        if self.text:
            body.append((58, self.text))
        return body

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "EXECUTION_REPORT",
            "exec_id": self.exec_id,
            "exec_type": self.exec_type.value,
            "ord_status": self.ord_status.value,
            "order_id": self.order_id,
            "cl_ord_id": self.cl_ord_id,
            "orig_cl_ord_id": self.orig_cl_ord_id,
            "client_comp_id": self.client_comp_id,
            "symbol": self.symbol,
            "side": enum_code(54, self.fix_side) or self.fix_side,
            "ord_type": enum_code(40, self.ord_type) or self.ord_type,
            "time_in_force": enum_code(59, self.time_in_force) or self.time_in_force,
            "price_cents": self.price_cents,
            "order_qty": self.order_qty,
            "leaves_qty": self.leaves_qty,
            "cum_qty": self.cum_qty,
            "cum_notional_cents": self.cum_notional_cents,
            "avg_px": self.avg_px,
            "last_qty": self.last_qty,
            "last_px_cents": self.last_px_cents,
            "match_id": self.match_id,
            "contra_order_id": self.contra_order_id,
            "ord_rej_reason": self.ord_rej_reason,
            "text": self.text,
            "transact_time": self.transact_time.isoformat(),
        }


@dataclass(frozen=True, slots=True)
class CancelReject:
    order_id: str
    cl_ord_id: str
    orig_cl_ord_id: str
    client_comp_id: str
    venue_comp_id: str
    begin_string: str
    symbol: str
    ord_status: OrdStatus
    response_to: str
    reason: str
    text: str
    transact_time: datetime

    msg_type = "9"

    def fix_body(self) -> list[tuple[int, str]]:
        body = [
            (37, self.order_id),
            (11, self.cl_ord_id),
            (41, self.orig_cl_ord_id),
            (39, _ORD_STATUS_FIX[self.ord_status]),
            (434, self.response_to),
            (102, self.reason),
            (58, self.text),
        ]
        if self.begin_string == "FIX.4.4":
            body.append((60, format_utc_timestamp(self.transact_time)))
        return body

    def to_dict(self) -> dict[str, Any]:
        return {
            "type": "ORDER_CANCEL_REJECT",
            "order_id": self.order_id,
            "cl_ord_id": self.cl_ord_id,
            "orig_cl_ord_id": self.orig_cl_ord_id,
            "client_comp_id": self.client_comp_id,
            "symbol": self.symbol,
            "ord_status": self.ord_status.value,
            "response_to": self.response_to,
            "reason": self.reason,
            "text": self.text,
            "transact_time": self.transact_time.isoformat(),
        }


Report = ExecutionReport | CancelReject


@dataclass(frozen=True, slots=True)
class Trade:
    match_id: str
    symbol: str
    price_cents: int
    qty: int
    aggressor_side: Side
    buy_order_id: str
    sell_order_id: str
    buy_comp_id: str
    sell_comp_id: str
    transact_time: datetime

    @property
    def notional_cents(self) -> int:
        return self.price_cents * self.qty

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_id": self.match_id,
            "symbol": self.symbol,
            "price_cents": self.price_cents,
            "qty": self.qty,
            "notional_cents": self.notional_cents,
            "aggressor_side": self.aggressor_side.value,
            "buy_order_id": self.buy_order_id,
            "sell_order_id": self.sell_order_id,
            "buy_comp_id": self.buy_comp_id,
            "sell_comp_id": self.sell_comp_id,
            "transact_time": self.transact_time.isoformat(),
        }


@dataclass(slots=True)
class EngineResult:
    symbol: str | None
    reports: list[Report] = field(default_factory=list)
    trades: list[Trade] = field(default_factory=list)
    book_changed: bool = False

    @property
    def executions(self) -> list[ExecutionReport]:
        return [r for r in self.reports if isinstance(r, ExecutionReport)]


@dataclass(frozen=True, slots=True)
class Fill:
    maker: Order
    qty: int
    price_cents: int
    match_id: str


class PriceLevel:
    __slots__ = ("price_cents", "orders", "total_qty")

    def __init__(self, price_cents: int) -> None:
        self.price_cents = price_cents
        self.orders: OrderedDict[str, Order] = OrderedDict()
        self.total_qty = 0

    def head(self) -> Order:
        return next(iter(self.orders.values()))


class BookSide:
    def __init__(self, side: Side) -> None:
        self.side = side
        self._prices: list[int] = []
        self._levels: dict[int, PriceLevel] = {}

    def __len__(self) -> int:
        return len(self._prices)

    def best_price(self) -> int | None:
        if not self._prices:
            return None
        return self._prices[-1] if self.side is Side.BUY else self._prices[0]

    def best_level(self) -> PriceLevel | None:
        price = self.best_price()
        return None if price is None else self._levels[price]

    def levels(self) -> Iterator[PriceLevel]:
        prices = reversed(self._prices) if self.side is Side.BUY else iter(self._prices)
        for price in prices:
            yield self._levels[price]

    def level(self, price_cents: int) -> PriceLevel | None:
        return self._levels.get(price_cents)

    def add(self, order: Order) -> None:
        assert order.price_cents is not None
        level = self._levels.get(order.price_cents)
        if level is None:
            level = self._levels[order.price_cents] = PriceLevel(order.price_cents)
            insort(self._prices, order.price_cents)
        level.orders[order.order_id] = order
        level.total_qty += order.leaves_qty

    def remove(self, order: Order) -> None:
        assert order.price_cents is not None
        level = self._levels[order.price_cents]
        del level.orders[order.order_id]
        level.total_qty -= order.leaves_qty
        if not level.orders:
            self.drop_level(order.price_cents)

    def drop_level(self, price_cents: int) -> None:
        del self._levels[price_cents]
        del self._prices[bisect_left(self._prices, price_cents)]


def _crosses(taker: Order, maker_price: int) -> bool:
    if taker.ord_type == ORD_TYPE_MARKET:
        return True
    assert taker.price_cents is not None
    return maker_price <= taker.price_cents if taker.side is Side.BUY else maker_price >= taker.price_cents


class OrderBook:
    def __init__(self, symbol: str) -> None:
        self.symbol = symbol
        self.bids = BookSide(Side.BUY)
        self.asks = BookSide(Side.SELL)
        self.resting: dict[str, Order] = {}
        self.last_trade_px_cents: int | None = None
        self.last_trade_qty: int = 0
        self.volume: int = 0
        self.notional_cents: int = 0
        self.trade_count: int = 0
        self._order_seq = 0
        self._exec_seq = 0
        self._match_seq = 0
        self._priority_seq = 0

    def next_order_id(self) -> str:
        self._order_seq += 1
        return f"{self.symbol}-{self._order_seq:07d}"

    def next_exec_id(self) -> str:
        self._exec_seq += 1
        return f"{self.symbol}-X{self._exec_seq:08d}"

    def _next_match_id(self) -> str:
        self._match_seq += 1
        return f"{self.symbol}-M{self._match_seq:08d}"

    def side_of(self, side: Side) -> BookSide:
        return self.bids if side is Side.BUY else self.asks

    def best_bid(self) -> int | None:
        return self.bids.best_price()

    def best_ask(self) -> int | None:
        return self.asks.best_price()

    def reference_price(self) -> int | None:
        if self.last_trade_px_cents is not None:
            return self.last_trade_px_cents
        bid, ask = self.best_bid(), self.best_ask()
        if bid is not None and ask is not None:
            return (bid + ask) // 2
        return None

    def rest(self, order: Order) -> None:
        # Arrival sequence, not wall-clock time, decides FIFO so replay is exact
        self._priority_seq += 1
        order.priority = self._priority_seq
        self.side_of(order.side).add(order)
        self.resting[order.order_id] = order

    def unrest(self, order: Order) -> None:
        self.side_of(order.side).remove(order)
        del self.resting[order.order_id]

    def reduce_in_place(self, order: Order, new_leaves: int) -> None:
        assert order.price_cents is not None and new_leaves <= order.leaves_qty
        level = self.side_of(order.side).level(order.price_cents)
        assert level is not None
        level.total_qty -= order.leaves_qty - new_leaves
        order.leaves_qty = new_leaves

    def executable_qty(self, taker: Order, needed: int) -> int:
        total = 0
        for level in self.side_of(Side.SELL if taker.side is Side.BUY else Side.BUY).levels():
            if not _crosses(taker, level.price_cents):
                break
            total += level.total_qty
            if total >= needed:
                break
        return total

    def match(self, taker: Order) -> Iterator[Fill]:
        opposite = self.asks if taker.side is Side.BUY else self.bids
        while taker.leaves_qty > 0:
            level = opposite.best_level()
            if level is None or not _crosses(taker, level.price_cents):
                return
            maker = level.head()
            qty = min(taker.leaves_qty, maker.leaves_qty)
            # Passive order sets the print price
            price = level.price_cents
            taker.apply_fill(qty, price)
            maker.apply_fill(qty, price)
            level.total_qty -= qty
            if maker.leaves_qty == 0:
                del level.orders[maker.order_id]
                del self.resting[maker.order_id]
                if not level.orders:
                    opposite.drop_level(price)
            self.last_trade_px_cents = price
            self.last_trade_qty = qty
            self.volume += qty
            self.notional_cents += qty * price
            self.trade_count += 1
            yield Fill(maker=maker, qty=qty, price_cents=price, match_id=self._next_match_id())

    def depth(self, levels: int = 10) -> dict[str, list[dict[str, int]]]:
        def side_depth(book_side: BookSide) -> list[dict[str, int]]:
            out = []
            for level in book_side.levels():
                if len(out) >= levels:
                    break
                out.append({"price_cents": level.price_cents, "qty": level.total_qty, "orders": len(level.orders)})
            return out

        return {"bids": side_depth(self.bids), "asks": side_depth(self.asks)}

    def l3(self) -> dict[str, list[dict[str, Any]]]:
        def side_orders(book_side: BookSide) -> list[dict[str, Any]]:
            return [o.l3(pos) for level in book_side.levels() for pos, o in enumerate(level.orders.values(), start=1)]

        return {"bids": side_orders(self.bids), "asks": side_orders(self.asks)}

    def spread(self) -> tuple[int | None, int | None]:
        bid, ask = self.best_bid(), self.best_ask()
        if bid is None or ask is None:
            return None, None
        spread = ask - bid
        # bps = spread / mid * 1e4; returned in hundredths of a bp to stay integral
        return spread, spread * 2_000_000 // (bid + ask)

    def snapshot(self, levels: int = 10) -> dict[str, Any]:
        spread_cents, spread_bps_e2 = self.spread()
        return {
            "symbol": self.symbol,
            **self.depth(levels),
            "best_bid_cents": self.best_bid(),
            "best_ask_cents": self.best_ask(),
            "spread_cents": spread_cents,
            "spread_bps_e2": spread_bps_e2,
            "last_trade_px_cents": self.last_trade_px_cents,
            "last_trade_qty": self.last_trade_qty,
            "volume": self.volume,
            "notional_cents": self.notional_cents,
            "trade_count": self.trade_count,
            "resting_orders": len(self.resting),
            "state_checksum": self.state_checksum(),
        }

    def state_checksum(self) -> str:
        digest = hashlib.sha256()
        for book_side in (self.bids, self.asks):
            for level in book_side.levels():
                for o in level.orders.values():
                    digest.update(
                        f"{o.side}|{o.price_cents}|{o.order_id}|{o.cl_ord_id}|{o.client_comp_id}|"
                        f"{o.order_qty}|{o.leaves_qty}|{o.cum_qty}|{o.cum_notional_cents}|{o.priority}|{o.version}\n".encode()
                    )
        digest.update(
            f"LAST|{self.last_trade_px_cents}|{self.volume}|{self.notional_cents}|{self.trade_count}|"
            f"SEQ|{self._order_seq}|{self._exec_seq}|{self._match_seq}|{self._priority_seq}".encode()
        )
        return digest.hexdigest()


class EventLogReader(Protocol):
    def iter_applied(self, symbol: str) -> Iterable[tuple[int, str]]: ...

    def applied_symbols(self) -> list[str]: ...


@dataclass(frozen=True, slots=True)
class RebuildResult:
    symbol: str
    messages_replayed: int
    checksum: str
    live_checksum: str
    book: OrderBook

    @property
    def matches_live(self) -> bool:
        return self.checksum == self.live_checksum


class MatchingEngine:
    # Single writer: callers serialize process()/reject_message() per engine instance
    def __init__(
        self,
        comp_id: str = "LOBX",
        *,
        event_log: EventLogReader | None = None,
        reference_prices: Mapping[str, int] | None = None,
    ) -> None:
        self.comp_id = comp_id
        self.books: dict[str, OrderBook] = {}
        self.sequencer = OutboundSequencer()
        self._event_log = event_log
        self._static_refs: dict[str, int] = dict(reference_prices or {})
        self._orders: dict[str, Order] = {}
        self._by_cl_ord_id: dict[tuple[str, str], str] = {}

    def book(self, symbol: str) -> OrderBook:
        book = self.books.get(symbol)
        if book is None:
            book = self.books[symbol] = OrderBook(symbol)
        return book

    def book_checksum(self, symbol: str) -> str:
        return (self.books.get(symbol) or OrderBook(symbol)).state_checksum()

    def reference_price(self, symbol: str) -> int | None:
        book = self.books.get(symbol)
        live = book.reference_price() if book else None
        return live if live is not None else self._static_refs.get(symbol)

    def set_reference_price(self, symbol: str, price_cents: int) -> None:
        self._static_refs[symbol] = price_cents

    def order(self, order_id: str) -> Order | None:
        return self._orders.get(order_id)

    def find_order(self, client_comp_id: str, cl_ord_id: str) -> Order | None:
        order_id = self._by_cl_ord_id.get((client_comp_id, cl_ord_id))
        return self._orders.get(order_id) if order_id else None

    def process(self, msg: ParsedMessage) -> EngineResult:
        if msg.msg_type == "D":
            return self._on_new_order(msg)
        if msg.msg_type == "F":
            return self._on_cancel(msg)
        if msg.msg_type == "G":
            return self._on_replace(msg)
        return EngineResult(msg.symbol)

    def reject_message(self, msg: ParsedMessage, issue: ValidationIssue, *, ref: str) -> EngineResult:
        # Validation rejects never touch book counters, so live and replayed state stay identical
        now = _clock(msg)
        result = EngineResult(msg.symbol)
        begin = msg.begin_string or "FIX.4.2"
        if msg.msg_type == "D":
            result.reports.append(
                ExecutionReport(
                    exec_id=f"REJ-{ref}",
                    exec_type=ExecType.REJECTED,
                    ord_status=OrdStatus.REJECTED,
                    order_id="NONE",
                    cl_ord_id=msg.cl_ord_id or "NONE",
                    orig_cl_ord_id=None,
                    client_comp_id=msg.sender_comp_id or "UNKNOWN",
                    venue_comp_id=msg.target_comp_id or self.comp_id,
                    begin_string=begin,
                    symbol=msg.symbol or "",
                    fix_side=msg.side or "",
                    ord_type=msg.ord_type or "",
                    time_in_force=msg.time_in_force,
                    price_cents=msg.price_cents,
                    order_qty=msg.order_qty or 0,
                    leaves_qty=0,
                    cum_qty=0,
                    cum_notional_cents=0,
                    last_qty=0,
                    last_px_cents=None,
                    transact_time=now,
                    account=msg.get(1),
                    ord_rej_reason=ord_rej_reason(issue.code, begin),
                    text=issue.detail[:120],
                )
            )
        elif msg.msg_type in ("F", "G"):
            existing = self.find_order(msg.sender_comp_id or "", msg.orig_cl_ord_id or "")
            result.reports.append(
                CancelReject(
                    order_id=existing.order_id if existing else "NONE",
                    cl_ord_id=msg.cl_ord_id or "NONE",
                    orig_cl_ord_id=msg.orig_cl_ord_id or "NONE",
                    client_comp_id=msg.sender_comp_id or "UNKNOWN",
                    venue_comp_id=msg.target_comp_id or self.comp_id,
                    begin_string=begin,
                    symbol=msg.symbol or "",
                    ord_status=existing.status if existing else OrdStatus.REJECTED,
                    response_to="1" if msg.msg_type == "F" else "2",
                    reason=cxl_rej_reason(issue.code, begin),
                    text=issue.detail[:120],
                    transact_time=now,
                )
            )
        return result

    def render(self, report: Report) -> str:
        return encode_fix(
            report.begin_string,
            report.msg_type,
            sender=report.venue_comp_id,
            target=report.client_comp_id,
            seq=self.sequencer.next(report.client_comp_id),
            sending_time=report.transact_time,
            body=report.fix_body(),
        )

    def rebuild_book_from_log(self, symbol: str) -> RebuildResult:
        if self._event_log is None:
            raise RuntimeError("engine has no event log configured")
        replica = MatchingEngine(self.comp_id, reference_prices=self._static_refs)
        replayed = replica._replay(symbol, self._event_log.iter_applied(symbol))
        book = replica.book(symbol)
        return RebuildResult(
            symbol=symbol,
            messages_replayed=replayed,
            checksum=book.state_checksum(),
            live_checksum=self.book_checksum(symbol),
            book=book,
        )

    def recover_from_log(
        self,
        symbols: Iterable[str] | None = None,
        on_result: Callable[[EngineResult], None] | None = None,
    ) -> dict[str, int]:
        if self._event_log is None:
            raise RuntimeError("engine has no event log configured")
        targets = list(symbols) if symbols is not None else self._event_log.applied_symbols()
        return {symbol: self._replay(symbol, self._event_log.iter_applied(symbol), on_result) for symbol in targets}

    def _replay(
        self,
        symbol: str,
        rows: Iterable[tuple[int, str]],
        on_result: Callable[[EngineResult], None] | None = None,
    ) -> int:
        count = 0
        for log_id, raw in rows:
            msg = parse_fix(raw)
            if msg.is_garbled or msg.is_malformed:
                raise LogIntegrityError(f"fix_messages.id={log_id} no longer passes framing validation")
            if msg.symbol != symbol:
                raise LogIntegrityError(f"fix_messages.id={log_id} symbol {msg.symbol!r} != {symbol!r}")
            result = self.process(msg)
            if on_result is not None:
                on_result(result)
            count += 1
        return count

    def _register(self, order: Order) -> None:
        self._orders[order.order_id] = order
        self._by_cl_ord_id[(order.client_comp_id, order.cl_ord_id)] = order.order_id

    def _report(
        self,
        book: OrderBook,
        order: Order,
        exec_type: ExecType,
        now: datetime,
        *,
        last_qty: int = 0,
        last_px_cents: int | None = None,
        match_id: str | None = None,
        contra_order_id: str | None = None,
        text: str | None = None,
    ) -> ExecutionReport:
        return ExecutionReport(
            exec_id=book.next_exec_id(),
            exec_type=exec_type,
            ord_status=order.status,
            order_id=order.order_id,
            cl_ord_id=order.cl_ord_id,
            orig_cl_ord_id=order.orig_cl_ord_id,
            client_comp_id=order.client_comp_id,
            venue_comp_id=order.venue_comp_id,
            begin_string=order.begin_string,
            symbol=order.symbol,
            fix_side=order.fix_side,
            ord_type=order.ord_type,
            time_in_force=order.time_in_force,
            price_cents=order.price_cents,
            order_qty=order.order_qty,
            leaves_qty=order.leaves_qty,
            cum_qty=order.cum_qty,
            cum_notional_cents=order.cum_notional_cents,
            last_qty=last_qty,
            last_px_cents=last_px_cents,
            transact_time=now,
            account=order.account,
            match_id=match_id,
            contra_order_id=contra_order_id,
            text=text,
        )

    def _cancel_reject(self, msg: ParsedMessage, order: Order | None, reason: str, text: str) -> EngineResult:
        result = EngineResult(msg.symbol)
        result.reports.append(
            CancelReject(
                order_id=order.order_id if order else "NONE",
                cl_ord_id=msg.cl_ord_id or "NONE",
                orig_cl_ord_id=msg.orig_cl_ord_id or "NONE",
                client_comp_id=msg.sender_comp_id or "UNKNOWN",
                venue_comp_id=msg.target_comp_id or self.comp_id,
                begin_string=msg.begin_string or "FIX.4.2",
                symbol=msg.symbol or "",
                ord_status=order.status if order else OrdStatus.REJECTED,
                response_to="1" if msg.msg_type == "F" else "2",
                reason=reason,
                text=text,
                transact_time=_clock(msg),
            )
        )
        return result

    def _execute(self, book: OrderBook, taker: Order, now: datetime, result: EngineResult) -> None:
        for fill in book.match(taker):
            maker = fill.maker
            buy, sell = (taker, maker) if taker.side is Side.BUY else (maker, taker)
            result.trades.append(
                Trade(
                    match_id=fill.match_id,
                    symbol=book.symbol,
                    price_cents=fill.price_cents,
                    qty=fill.qty,
                    aggressor_side=taker.side,
                    buy_order_id=buy.order_id,
                    sell_order_id=sell.order_id,
                    buy_comp_id=buy.client_comp_id,
                    sell_comp_id=sell.client_comp_id,
                    transact_time=now,
                )
            )
            for party, contra in ((taker, maker), (maker, taker)):
                result.reports.append(
                    self._report(
                        book,
                        party,
                        ExecType.TRADE,
                        now,
                        last_qty=fill.qty,
                        last_px_cents=fill.price_cents,
                        match_id=fill.match_id,
                        contra_order_id=contra.order_id,
                    )
                )
            result.book_changed = True

    def _cancel_remainder(self, book: OrderBook, order: Order, now: datetime, result: EngineResult, text: str) -> None:
        order.leaves_qty = 0
        order.status = OrdStatus.CANCELED
        result.reports.append(self._report(book, order, ExecType.CANCELED, now, text=text))

    def _on_new_order(self, msg: ParsedMessage) -> EngineResult:
        symbol = msg.symbol or ""
        book = self.book(symbol)
        now = _clock(msg)
        qty = msg.order_qty or 0
        order = Order(
            order_id=book.next_order_id(),
            symbol=symbol,
            side=Side.from_fix(msg.side),
            fix_side=msg.side or "",
            ord_type=msg.ord_type or "",
            time_in_force=msg.time_in_force,
            price_cents=msg.price_cents if msg.ord_type == ORD_TYPE_LIMIT else None,
            order_qty=qty,
            leaves_qty=qty,
            cl_ord_id=msg.cl_ord_id or "",
            orig_cl_ord_id=None,
            client_comp_id=msg.sender_comp_id or "",
            venue_comp_id=msg.target_comp_id or self.comp_id,
            begin_string=msg.begin_string or "FIX.4.2",
            account=msg.get(1),
            transact_time=now,
        )
        self._register(order)
        result = EngineResult(symbol)
        result.reports.append(self._report(book, order, ExecType.NEW, now))

        if order.time_in_force == TIF_FOK and book.executable_qty(order, qty) < qty:
            self._cancel_remainder(book, order, now, result, "FOK: insufficient executable quantity")
            return result

        self._execute(book, order, now, result)
        if order.leaves_qty > 0:
            if order.is_limit and order.time_in_force in RESTING_TIFS:
                # Tag 39=0/1: unfilled remainder rests on the book
                book.rest(order)
                result.book_changed = True
            elif order.ord_type == ORD_TYPE_MARKET:
                self._cancel_remainder(book, order, now, result, "Market order remainder canceled: no contra liquidity")
            else:
                self._cancel_remainder(book, order, now, result, "IOC remainder canceled")
        return result

    def _resolve_target(self, msg: ParsedMessage) -> tuple[Order | None, EngineResult | None]:
        order = self.find_order(msg.sender_comp_id or "", msg.orig_cl_ord_id or "")
        begin = msg.begin_string
        if order is None:
            return None, self._cancel_reject(msg, None, "1", f"Unknown OrigClOrdID {msg.orig_cl_ord_id}")
        if order.symbol != msg.symbol or order.fix_side != msg.side:
            return None, self._cancel_reject(msg, order, "2" if begin != "FIX.4.4" else "99", "Symbol/Side does not match original order")
        if not order.is_open:
            return None, self._cancel_reject(msg, order, "0", f"Order is {order.status.value}")
        return order, None

    def _chain_cl_ord_id(self, order: Order, new_cl_ord_id: str) -> None:
        order.orig_cl_ord_id = order.cl_ord_id
        order.cl_ord_id = new_cl_ord_id
        self._by_cl_ord_id[(order.client_comp_id, new_cl_ord_id)] = order.order_id

    def _on_cancel(self, msg: ParsedMessage) -> EngineResult:
        order, rejection = self._resolve_target(msg)
        if rejection is not None:
            return rejection
        assert order is not None
        book = self.book(order.symbol)
        now = _clock(msg)
        book.unrest(order)
        order.leaves_qty = 0
        order.status = OrdStatus.CANCELED
        self._chain_cl_ord_id(order, msg.cl_ord_id or "")
        result = EngineResult(order.symbol, book_changed=True)
        result.reports.append(self._report(book, order, ExecType.CANCELED, now))
        return result

    def _on_replace(self, msg: ParsedMessage) -> EngineResult:
        order, rejection = self._resolve_target(msg)
        if rejection is not None:
            return rejection
        assert order is not None
        other = "2" if msg.begin_string != "FIX.4.4" else "99"
        new_qty = msg.order_qty
        new_px = msg.price_cents
        if msg.ord_type != ORD_TYPE_LIMIT or not order.is_limit or new_px is None:
            return self._cancel_reject(msg, order, other, "Only resting LIMIT orders can be replaced to a LIMIT price")
        if new_qty is None or new_qty <= order.cum_qty:
            return self._cancel_reject(msg, order, other, f"OrderQty must exceed CumQty {order.cum_qty}")
        if msg.time_in_force != order.time_in_force:
            return self._cancel_reject(msg, order, other, "TimeInForce cannot change on replace")

        book = self.book(order.symbol)
        now = _clock(msg)
        new_leaves = new_qty - order.cum_qty
        result = EngineResult(order.symbol, book_changed=True)

        # Qty-down amend retains queue position; price change or qty-up re-queues
        if new_px == order.price_cents and new_leaves <= order.leaves_qty:
            book.reduce_in_place(order, new_leaves)
            order.order_qty = new_qty
            order.version += 1
            self._chain_cl_ord_id(order, msg.cl_ord_id or "")
            result.reports.append(self._report(book, order, ExecType.REPLACED, now))
            return result

        book.unrest(order)
        order.price_cents = new_px
        order.order_qty = new_qty
        order.leaves_qty = new_leaves
        order.version += 1
        self._chain_cl_ord_id(order, msg.cl_ord_id or "")
        result.reports.append(self._report(book, order, ExecType.REPLACED, now))
        # A re-priced order can become marketable
        self._execute(book, order, now, result)
        if order.leaves_qty > 0:
            book.rest(order)
        return result


def _clock(msg: ParsedMessage) -> datetime:
    # Engine time is the inbound SendingTime so replay reproduces every timestamp
    return msg.sending_time or msg.transact_time or _EPOCH
