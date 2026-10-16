# LOBX — Limit Order Book Matching Engine & FIX 4.2/4.4 Inspector

A deterministic price-time priority matching engine with a FIX 4.2/4.4 session validator, an append-only PostgreSQL event log that can rebuild any book bit-for-bit, and an operator workbench for production-support triage with four-eyes (maker-checker) replay.

![Operator workbench](docs/workbench.png)

## What it does

| Area | Behaviour |
| --- | --- |
| **FIX parsing** | SOH, `\|` and `^A` delimiters; log-prefixed and concatenated streams; BodyLength(9) and mod-256 CheckSum(10) verification; header ordering, required tags per MsgType and version, data-format and enum-range checks; tag-by-tag decode with version-specific field names (`32` LastShares in 4.2 vs LastQty in 4.4). |
| **Session layer** | Per-`SenderCompID` MsgSeqNum tracking. Gaps are held and answered with a `35=2` ResendRequest for the exact missing range; PossDup resends fill the gap; SequenceReset (GapFill and Reset) is honoured; garbled frames are dropped *without* consuming a sequence number; stale SendingTime gets a `35=3` with `373=10`. |
| **Pre-trade risk** | Duplicate ClOrdID (idempotency), $0.01 tick enforcement, price collar against last print / mid / prior close, max order quantity and max notional. Breaches return `35=8 39=8` with the correct `103` OrdRejReason for the session's FIX version. |
| **Matching** | Strict price-time FIFO; partial fills rest the remainder; MARKET, LIMIT, DAY/GTC, IOC and FOK; cancel and cancel/replace with exchange-style queue semantics (quantity-down keeps priority, price change or quantity-up re-queues, a re-priced order that crosses executes immediately); `35=9` cancel rejects for unknown, filled or mismatched orders. |
| **Execution reports** | One `35=8` per state transition, rendered per counterparty version: 4.2 sends `20=0`, `150=1/2` and `39=5` on replace; 4.4 sends `150=F` and reports live OrdStatus. |
| **Persistence** | Every inbound and outbound frame, every order version and every execution is written to tables guarded by PL/pgSQL triggers that reject `UPDATE`, `DELETE` and `TRUNCATE`. |
| **Deterministic replay** | `POST /api/book/{symbol}/rebuild` replays the log into a fresh engine and compares a SHA-256 state checksum with the live book. The same path recovers the engine, the session validator and outbound sequence numbers on restart. |
| **Maker-checker** | An operator stages REPLAY, AMEND_AND_REPLAY (tag overrides, re-framed with recomputed 9/10) or CANCEL_REPLACE (synthetic PossDup `35=G` against the live order). A different operator must approve. The API, a case-insensitive database `CHECK`, and a SHA-256 of the staged bytes all enforce it. |

## Architecture

```mermaid
flowchart LR
    subgraph Client
        UI["React workbench"]
    end
    subgraph API["FastAPI"]
        ING["POST /api/fix/ingest<br/>Idempotency-Key"]
        REM["remediation create / approve"]
        WS["WS /ws/market-data"]
    end
    subgraph Core["Single-writer critical section"]
        P["FIX parser<br/>framing + decode"] --> V["Session validator<br/>seq / dup / stale / collar"]
        V -->|"VALID 35=D/F/G"| E["Matching engine<br/>price-time FIFO"]
        V -->|reject| R["35=8 39=8 · 35=9 · 35=3 · 35=2"]
        E --> X["35=8 ExecutionReports"]
    end
    DB[("PostgreSQL<br/>append-only log")]
    UI --> ING
    UI --> REM
    ING --> P
    REM --> E
    V --> DB
    X --> DB
    R --> DB
    DB -. "rebuild_book_from_log" .-> E
    X --> WS
    E --> WS
    WS --> UI
```

### Design decisions

- **No floating-point money.** Prices are parsed straight from the FIX string into integer cents (`"228.55"` becomes `22855`), and sub-penny limits are rejected at the tick check. Notional, VWAP and AvgPx use integer arithmetic (AvgPx is rounded half-up to 6 dp). Every price and quantity column is `BIGINT`. The test suite walks every execution payload and fails if a `float` appears.
- **Determinism by construction.** The engine clock is the inbound SendingTime, not wall time. Queue priority is an arrival sequence number. Order, exec and match IDs come from per-symbol counters. Validation rejects use IDs derived from the log row and never touch book counters. Together these make a per-symbol replay reproduce the live state exactly. A randomized 3,000-message flow is run through two engines and must produce byte-identical outbound FIX.
- **One writer.** Validate → log → match → persist runs under one lock, so the order of `fix_messages.id` is the order the engine applied messages. If a commit fails, in-memory state is rebuilt from the committed log rather than trusted.
- **The log is the source of truth.** `fix_messages` stores the SOH-normalized raw frame, with the decoded tags as JSONB for querying. Replay re-parses the raw bytes and raises `LogIntegrityError` if a stored frame no longer passes framing validation.
- **Ordered streaming.** Each broadcast batch carries a monotonic `seq`. The WebSocket snapshot is taken under the same lock that serializes broadcasts. Clients drop batches already folded into their snapshot, keep the newest book per symbol, and de-duplicate prints and executions by ID.

### Validation status → venue response

| Status | Trigger | Outbound | Book effect |
| --- | --- | --- | --- |
| `VALID` | — | `35=8` NEW / TRADE / CANCELED / REPLACED, or `35=9` | applied |
| `SEQ_GAP` | MsgSeqNum above expected | `35=2` ResendRequest `7`–`16` | held for operator replay |
| `SEQ_TOO_LOW` | below expected, no PossDup | `35=5` Logout | none |
| `POSS_DUP` | PossDup of an already-processed seq | none | none |
| `CHECKSUM_ERR` / `BODY_LENGTH_ERR` | framing mismatch | none (garbled, seq not consumed) | none |
| `MALFORMED` | structure / required tag / format / enum range | `35=3` with `373` reason | none |
| `STALE_TIMESTAMP` | SendingTime outside tolerance | `35=3` `373=10` | none |
| `DUPLICATE_CLORDID` | ClOrdID reused by the session | `35=8 39=8 103=6` or `35=9` | none |
| `BUSINESS_REJECT` | unsupported OrdType/TIF/side, tick size, symbol | `35=8 39=8` | none |
| `COLLAR_REJECT` | price collar, quantity or notional limit | `35=8 39=8 103=3` or `35=9` | none |

## Running locally

Requirements: Python 3.11+, Node 20+, and Docker (or any PostgreSQL 14+).

```bash
docker compose up -d postgres
```

```bash
python -m venv .venv && source .venv/bin/activate && pip install -r backend/requirements.txt
```

```bash
uvicorn backend.main:app --reload --port 8000
```

```bash
cd frontend && npm install && npm run dev
```

Open http://localhost:5173. In the FIX inspector, click **Load 25-msg sample batch**, then **Ingest to engine**. To fill out the book with layered market-maker liquidity:

```bash
python -m backend.seed
```

The seed derives its Idempotency-Key from a content hash, the same way the UI does, so re-sending the sample is answered from the ingest ledger instead of being re-applied. Add `--direct` to write through the service layer without a running API.

Configuration uses `LOB_`-prefixed environment variables (see [backend/config.py](backend/config.py)): `LOB_DATABASE_URL`, `LOB_COLLAR_BPS`, `LOB_MAX_ORDER_QTY`, `LOB_MAX_NOTIONAL_CENTS`, `LOB_MAX_CLOCK_SKEW_SECONDS`, and `LOB_REFERENCE_PRICES_CENTS` (JSON).

### The sample batch

25 messages across four sessions (two FIX 4.2, two FIX 4.4):

- Logons, then market-maker quotes in AAPL and MSFT.
- An aggressive buy that sweeps one level and partially fills the next.
- An IOC sell whose 200-share remainder is canceled.
- A 2,000-share buy that fills 1,200 and rests the rest.
- A quantity-down cancel/replace that keeps queue priority.
- A fat-finger limit at `4312.50` instead of `431.25` (collar breach).
- A MsgSeqNum jump from 4 to 7, followed by PossDup resends of 5 and 6.
- A reused ClOrdID.
- A SendingTime six minutes behind the stream.
- A frame whose price was altered in transit to the same length, so only CheckSum(10) catches it.

## API

| Method | Path | Purpose |
| --- | --- | --- |
| `POST` | `/api/fix/ingest` | Raw FIX as JSON `{payload}`, `text/plain`, or a multipart `.fix` upload. `Idempotency-Key` header required; a reused key with a different body returns `409`. |
| `POST` | `/api/fix/decode` | Dry-run decode and validation against an isolated session state; nothing is persisted. |
| `GET` | `/api/fix/sample` | The 25-message sample batch. |
| `GET` | `/api/symbols` | Top of book per symbol. |
| `GET` | `/api/book/{symbol}` | L2 depth by price level, L3 resting orders with queue position, spread in hundredths of a bp, state checksum. |
| `POST` | `/api/book/{symbol}/rebuild` | Replay the append-only log and compare against the live checksum. |
| `GET` | `/api/trades`, `/api/executions` | Print tape and execution reports. |
| `GET` | `/api/messages`, `/api/messages/{id}` | Message log with filters; full tag decode with session findings merged per field. |
| `GET` | `/api/exceptions?scope=open\|all` | Rejected, gapped, stale, duplicate and collar-held messages, with root cause and allowed actions. |
| `POST` | `/api/remediation/create` | Maker stages a remediation (`PENDING_APPROVAL`). |
| `POST` | `/api/remediation/{id}/approve` | Checker approves; the staged hash is re-verified, pre-trade checks are re-run, and the message is injected. |
| `POST` | `/api/remediation/{id}/decline` | Checker declines. |
| `WS` | `/ws/market-data` | Snapshot on connect, then sequenced batches of book, trade, execution, exception and remediation events. |

Interactive OpenAPI docs are served at http://localhost:8000/docs.

## Tests

```bash
pytest
```

41 tests cover:

- **Framing:** parser framing against the canonical FIX reference frame (`9=65`, `10=062`).
- **Session state:** gap → PossDup fill → too-low; garbled frames not consuming sequence; SequenceReset.
- **Matching rules:** FIFO sweeps; IOC, FOK and market orders; replace priority rules; cancel-reject reasons; 4.2 vs 4.4 rendering.
- **Determinism:** a randomized 3,000-message flow checked against book invariants after every message, with byte-identical output across two engines and a matching rebuild.
- **Concurrency:** parallel ingest from eight sessions; one winner among six requests sharing an idempotency key; one winner among five concurrent approvals.
- **Recovery:** a failed commit resyncs from the log; restart recovery keeps the book, sequence and ClOrdID state.
- **Remediation:** the full maker-checker flow, including payload tampering, which is detected.
- **Persistence:** append-only enforcement.
- **WebSocket:** ordered snapshot then batches.

The suite runs on SQLite by default. Point it at PostgreSQL to exercise the PL/pgSQL triggers and row locks; CI runs both:

```bash
LOB_TEST_DATABASE_URL=postgresql+psycopg://lob:lob@localhost:5432/lob pytest
```

## Layout

```
backend/
  fix_engine/
    dictionary.py   tag names, types and enums for 4.2/4.4
    parser.py       framing, decode, session validator, risk checks, FIX encoder
    order_book.py   price levels, OrderBook, MatchingEngine, ExecutionReport rendering, replay
  models.py         SQLAlchemy 2.0 schema, append-only triggers, four-eyes constraints
  event_log.py      PostgreSQL replay source for the engine
  service.py        single-writer ingest pipeline, idempotency, remediation workflow
  main.py           FastAPI routes and WebSocket endpoint
  ws.py             sequenced broadcast manager
  seed.py           sample batch and liquidity generator
  tests/
frontend/src/
  components/       order book ladder, tape/blotter, FIX inspector, tag decoder, exception queue
  hooks/            WebSocket market-data store, operator role
  api/              typed REST client and payload types
```

## Scope

- **No FIX transport.** Messages arrive over HTTP rather than a FIX acceptor socket, so there is no Logon handshake, Heartbeat, or TestRequest timer. Session-level replies are generated and logged, but not transmitted.
- **Single process.** One matching engine per process. Horizontal scaling would partition by symbol.
- **Supported order types:** MARKET and LIMIT only; stop, pegged and GTD orders are business-rejected. Repeating groups are not parsed, and a repeated tag is rejected with `373=13`.
