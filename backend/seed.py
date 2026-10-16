from __future__ import annotations

import argparse
import hashlib
import sys
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from backend.fix_engine.parser import encode_fix, format_cents, format_utc_timestamp, parse_fix, to_pipe

VENUE = "LOBX"
SESSION_OPEN = datetime(2026, 10, 16, 13, 30, 0, tzinfo=UTC)
BUY, SELL = "1", "2"
DAY, IOC = "0", "3"


def idempotency_key(payload: str) -> str:
    return "sha256:" + hashlib.sha256(payload.encode("utf-8")).hexdigest()


@dataclass
class ClientSession:
    comp_id: str
    begin_string: str
    account: str | None = None
    capacity: str = "A"
    seq: int = 0

    def frame(self, msg_type: str, body: list[tuple[int, str]], at: datetime, *, seq: int | None = None, poss_dup_of: datetime | None = None) -> str:
        if seq is None:
            self.seq += 1
            seq = self.seq
        else:
            self.seq = max(self.seq, seq)
        extra = [(43, "Y"), (122, format_utc_timestamp(poss_dup_of))] if poss_dup_of else []
        return encode_fix(self.begin_string, msg_type, sender=self.comp_id, target=VENUE, seq=seq, sending_time=at, header_extra=extra, body=body)

    def logon(self, at: datetime, *, reset: bool = False) -> str:
        body = [(98, "0"), (108, "30")]
        if reset:
            body.append((141, "Y"))
        return self.frame("A", body, at)

    def _capacity(self) -> tuple[int, str]:
        # 4.2 Rule80A was superseded by OrderCapacity in 4.4
        return (528, self.capacity) if self.begin_string == "FIX.4.4" else (47, self.capacity)

    def new_order(
        self,
        at: datetime,
        cl_ord_id: str,
        symbol: str,
        side: str,
        qty: int,
        price_cents: int | None = None,
        *,
        tif: str = DAY,
        seq: int | None = None,
        poss_dup_of: datetime | None = None,
    ) -> str:
        body: list[tuple[int, str]] = [(11, cl_ord_id)]
        if self.account:
            body.append((1, self.account))
        body += [(21, "1"), (55, symbol), (54, side), (38, str(qty)), (40, "1" if price_cents is None else "2")]
        if price_cents is not None:
            body.append((44, format_cents(price_cents)))
        body += [(59, tif), self._capacity(), (60, format_utc_timestamp(poss_dup_of or at))]
        return self.frame("D", body, at, seq=seq, poss_dup_of=poss_dup_of)

    def cancel(
        self, at: datetime, cl_ord_id: str, orig: str, symbol: str, side: str, qty: int, *, seq: int | None = None, poss_dup_of: datetime | None = None
    ) -> str:
        body = [(41, orig), (11, cl_ord_id), (55, symbol), (54, side), (38, str(qty)), (60, format_utc_timestamp(poss_dup_of or at))]
        return self.frame("F", body, at, seq=seq, poss_dup_of=poss_dup_of)

    def replace(self, at: datetime, cl_ord_id: str, orig: str, symbol: str, side: str, qty: int, price_cents: int, *, tif: str = DAY) -> str:
        body = [
            (41, orig),
            (11, cl_ord_id),
            (21, "1"),
            (55, symbol),
            (54, side),
            (38, str(qty)),
            (40, "2"),
            (44, format_cents(price_cents)),
            (59, tif),
            (60, format_utc_timestamp(at)),
        ]
        return self.frame("G", body, at)


def _t(ms: int) -> datetime:
    return SESSION_OPEN + timedelta(milliseconds=ms)


def sample_frames() -> list[str]:
    mm = ClientSession("CEDAR_MM", "FIX.4.4", capacity="P")
    nb = ClientSession("NBAM_OMS", "FIX.4.2", account="NB-LARGECAP-01")
    hal = ClientSession("HALCYON_EMS", "FIX.4.2", account="HAL-QUANT-07")
    bwq = ClientSession("BRIGHTWATER_QT", "FIX.4.4", account="BWQ-STAT-ARB")

    frames = [
        mm.logon(_t(0), reset=True),
        nb.logon(_t(120)),
        hal.logon(_t(240)),
        bwq.logon(_t(360), reset=True),
        mm.new_order(_t(1_000), "CDR-0001", "AAPL", SELL, 500, 22855),
        mm.new_order(_t(1_050), "CDR-0002", "AAPL", SELL, 300, 22860),
        mm.new_order(_t(1_100), "CDR-0003", "AAPL", BUY, 400, 22845),
        mm.new_order(_t(1_150), "CDR-0004", "AAPL", BUY, 600, 22840),
        # Sweeps 228.55 and partially fills the 228.60 offer
        nb.new_order(_t(2_400), "NB-240001", "AAPL", BUY, 700, 22860),
        # IOC: 1,000 executes across two bid levels, 200 canceled
        hal.new_order(_t(3_100), "HAL-77001", "AAPL", SELL, 1_200, 22840, tif=IOC),
        mm.new_order(_t(4_000), "CDR-0005", "MSFT", SELL, 1_200, 43125),
        mm.new_order(_t(4_050), "CDR-0006", "MSFT", BUY, 1_000, 43105),
        # 1,200 fills, 800 remainder rests as best bid
        bwq.new_order(_t(5_200), "BWQ-9001", "MSFT", BUY, 2_000, 43125),
        nb.new_order(_t(6_000), "NB-240002", "NVDA", SELL, 5_000, 13850),
        bwq.new_order(_t(6_900), "BWQ-9002", "NVDA", BUY, 1_500, 13850),
        # Qty-down amend keeps time priority
        nb.replace(_t(7_500), "NB-240002A", "NB-240002", "NVDA", SELL, 4_000, 13850),
        # Fat-finger: decimal slipped one place
        hal.new_order(_t(8_200), "HAL-77002", "MSFT", BUY, 500, 431_250),
        hal.new_order(_t(8_900), "HAL-77003", "MSFT", SELL, 500, 43125),
        bwq.cancel(_t(9_600), "BWQ-9001C", "BWQ-9001", "MSFT", BUY, 2_000),
        # 34=5 and 34=6 lost in transit; 34=7 arrives first and is held
        nb.new_order(_t(10_400), "NB-240005", "AAPL", BUY, 1_000, 22850, seq=7),
        nb.new_order(_t(11_000), "NB-240003", "AAPL", BUY, 200, 22855, seq=5, poss_dup_of=_t(9_900)),
        nb.cancel(_t(11_050), "NB-240004", "NB-240003", "AAPL", BUY, 200, seq=6, poss_dup_of=_t(10_100)),
        hal.new_order(_t(11_800), "HAL-77003", "NVDA", BUY, 800, 13850),
        # Host clock drifted six minutes behind
        bwq.new_order(_t(12_500) - timedelta(minutes=6), "BWQ-9003", "NVDA", BUY, 1_000, 13845),
    ]
    clean = mm.new_order(_t(13_200), "CDR-0007", "AAPL", SELL, 400, 22858)
    # Bit-flip in transit: same length, so only CheckSum(10) catches it
    frames.append(clean.replace("\x0144=228.58\x01", "\x0144=228.88\x01"))
    return frames


def liquidity_frames() -> list[str]:
    start = SESSION_OPEN + timedelta(seconds=30)
    kestrel = ClientSession("KESTREL_MM", "FIX.4.4", capacity="P")
    orion = ClientSession("ORION_LP", "FIX.4.4", capacity="P")
    summit = ClientSession("SUMMIT_PB", "FIX.4.2", account="SPB-GLOBAL-MACRO")

    frames = [kestrel.logon(start, reset=True), orion.logon(start + timedelta(milliseconds=50), reset=True)]
    ladders: dict[str, tuple[list[int], list[int], list[int]]] = {
        "AAPL": ([22848, 22846, 22844, 22842, 22840], [22862, 22864, 22866, 22868, 22870], [300, 500, 700, 1_000, 1_500]),
        "MSFT": ([43100, 43095, 43090, 43085, 43080], [43130, 43135, 43140, 43145, 43150], [200, 300, 400, 600, 900]),
        "NVDA": ([13846, 13845, 13844, 13843, 13842], [13852, 13853, 13854, 13855, 13856], [1_000, 1_500, 2_500, 4_000, 6_000]),
    }
    joins: dict[str, tuple[list[int], list[int]]] = {
        "AAPL": ([22848, 22846, 22844], [22860, 22862, 22864]),
        "MSFT": ([43105, 43100, 43095], [43130, 43135]),
        "NVDA": ([13846, 13845], [13850, 13852]),
    }
    clock = 1_000
    n = 0
    for symbol, (bids, asks, sizes) in ladders.items():
        for bid, ask, size in zip(bids, asks, sizes, strict=True):
            n += 1
            frames.append(kestrel.new_order(start + timedelta(milliseconds=clock), f"KST-{n:05d}B", symbol, BUY, size, bid))
            frames.append(kestrel.new_order(start + timedelta(milliseconds=clock + 5), f"KST-{n:05d}S", symbol, SELL, size, ask))
            clock += 40
    for symbol, (bids, asks) in joins.items():
        for i, px in enumerate(bids):
            n += 1
            frames.append(orion.new_order(start + timedelta(milliseconds=clock), f"ORN-{n:05d}", symbol, BUY, 200 * (i + 1), px))
            clock += 25
        for i, px in enumerate(asks):
            n += 1
            frames.append(orion.new_order(start + timedelta(milliseconds=clock), f"ORN-{n:05d}", symbol, SELL, 200 * (i + 1), px))
            clock += 25

    prints = start + timedelta(seconds=20)
    frames += [
        summit.logon(prints),
        # Lifts 228.60 (CEDAR then ORION by time priority), then 228.62
        summit.new_order(prints + timedelta(milliseconds=600), "SPB-0001", "AAPL", BUY, 400, 22862, tif=IOC),
        summit.new_order(prints + timedelta(milliseconds=1_400), "SPB-0002", "MSFT", SELL, 300),
        summit.new_order(prints + timedelta(milliseconds=2_100), "SPB-0003", "NVDA", BUY, 1_000, 13850),
        summit.new_order(prints + timedelta(milliseconds=2_900), "SPB-0004", "AAPL", SELL, 250, 22846),
    ]
    return frames


def as_log(frames: list[str]) -> str:
    lines = []
    received: datetime | None = None
    for frame in frames:
        sent = parse_fix(frame).sending_time or SESSION_OPEN
        received = sent if received is None or sent > received else received
        lines.append(f"{format_utc_timestamp(received)} : {to_pipe(frame)}")
    return "\n".join(lines) + "\n"


def sample_batch() -> str:
    return as_log(sample_frames())


def liquidity_batch() -> str:
    return as_log(liquidity_frames())


def _report(name: str, summary: dict[str, Any]) -> None:
    tag = " (idempotent replay)" if summary.get("replayed") else ""
    print(
        f"{name:<10} received={summary['received']:>3} applied={summary['applied']:>3} "
        f"exceptions={summary['exceptions']:>2} executions={summary['executions']:>3} trades={summary['trades']:>2}{tag}"
    )
    for item in summary["messages"]:
        if item["status"] != "VALID":
            detail = next((i["detail"] for i in item["issues"] if i["severity"] != "INFO"), "")
            print(f"    id={item['id']:<5} {item['sender_comp_id'] or '?':<15} 34={item['msg_seq_num']!s:<4} {item['status']:<17} {detail}")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Load sample institutional FIX traffic into the matching engine")
    parser.add_argument("--api", default="http://127.0.0.1:8000", help="base URL of a running API")
    parser.add_argument("--direct", action="store_true", help="write through the service layer without a running API")
    parser.add_argument("--print-sample", action="store_true", help="print the 25-message sample batch and exit")
    args = parser.parse_args(argv)

    if args.print_sample:
        sys.stdout.write(sample_batch())
        return 0

    batches = [("sample", sample_batch()), ("liquidity", liquidity_batch())]
    if args.direct:
        from backend.config import get_settings
        from backend.database import init_db, make_engine, make_session_factory
        from backend.service import TradingService

        settings = get_settings()
        db = make_engine(settings.database_url)
        init_db(db)
        service = TradingService(settings, make_session_factory(db))
        service.recover()
        for name, payload in batches:
            summary, _ = service.ingest(payload, idempotency_key(payload))
            _report(name, summary)
        return 0

    import httpx

    with httpx.Client(base_url=args.api, timeout=30.0) as client:
        for name, payload in batches:
            response = client.post("/api/fix/ingest", json={"payload": payload}, headers={"Idempotency-Key": idempotency_key(payload)})
            response.raise_for_status()
            _report(name, response.json())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
