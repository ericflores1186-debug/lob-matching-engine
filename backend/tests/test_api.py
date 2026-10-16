from __future__ import annotations

from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import inspect, text
from sqlalchemy.exc import DBAPIError

from backend.config import Settings
from backend.database import make_engine
from backend.main import create_app
from backend.models import Base
from backend.seed import BUY, ClientSession, idempotency_key, liquidity_batch, sample_batch

SAMPLE = sample_batch()


def ingest(client: TestClient, payload: str, key: str | None = None) -> dict:
    response = client.post("/api/fix/ingest", json={"payload": payload}, headers={"Idempotency-Key": key or idempotency_key(payload)})
    assert response.status_code == 200, response.text
    return response.json()


def exceptions_by_status(client: TestClient) -> dict[str, dict]:
    return {e["status"]: e for e in client.get("/api/exceptions").json()}


def stage(client: TestClient, exception_id: int, action: str, maker: str = "ops.maker", **extra) -> dict:
    body = {"exception_message_id": exception_id, "action": action, "maker_id": maker, "reason": "Confirmed with client desk", **extra}
    response = client.post("/api/remediation/create", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def test_ingest_requires_idempotency_key(client: TestClient) -> None:
    response = client.post("/api/fix/ingest", json={"payload": SAMPLE})
    assert response.status_code == 400 and response.json()["error"] == "IDEMPOTENCY_KEY_REQUIRED"
    assert client.post("/api/fix/ingest", json={"payload": "no fix here"}, headers={"Idempotency-Key": "key-00000001"}).status_code == 422


def test_sample_batch_outcomes_and_book_state(client: TestClient) -> None:
    sample = client.get("/api/fix/sample").json()
    assert sample["messages"] == 25 and sample["payload"] == SAMPLE
    summary = ingest(client, SAMPLE)
    assert (summary["received"], summary["applied"], summary["exceptions"], summary["trades"]) == (25, 16, 5, 7)
    statuses = [m["status"] for m in summary["messages"] if m["status"] != "VALID"]
    assert statuses == ["COLLAR_REJECT", "SEQ_GAP", "DUPLICATE_CLORDID", "STALE_TIMESTAMP", "CHECKSUM_ERR"]
    responses = {m["status"]: m["responses"] for m in summary["messages"] if m["status"] != "VALID"}
    assert responses["SEQ_GAP"] == ["35=2 RESEND_REQUEST"]
    assert responses["STALE_TIMESTAMP"] == ["35=3 REJECT"]
    assert responses["COLLAR_REJECT"] == ["35=8 REJECTED/REJECTED"]
    assert responses["CHECKSUM_ERR"] == []

    aapl = client.get("/api/book/AAPL").json()
    assert aapl["asks"] == [{"price_cents": 22860, "qty": 100, "orders": 1}] and aapl["bids"] == []
    msft = client.get("/api/book/MSFT").json()
    assert msft["bids"][0] == {"price_cents": 43105, "qty": 1_000, "orders": 1} and msft["last_trade_px_cents"] == 43125
    nvda = client.get("/api/book/NVDA").json()
    assert nvda["l3"]["asks"][0]["cl_ord_id"] == "NB-240002A" and nvda["l3"]["asks"][0]["leaves_qty"] == 2_500

    again = ingest(client, SAMPLE)
    assert again["replayed"] and again["messages"] == summary["messages"]
    conflict = client.post("/api/fix/ingest", json={"payload": SAMPLE + "\n"}, headers={"Idempotency-Key": idempotency_key(SAMPLE)})
    assert conflict.status_code == 409


def test_file_upload_and_plain_text_ingest(client: TestClient) -> None:
    files = {"file": ("session.fix", SAMPLE.encode("latin-1"), "application/octet-stream")}
    uploaded = client.post("/api/fix/ingest", files=files, headers={"Idempotency-Key": "upload-0001"})
    assert uploaded.status_code == 200 and uploaded.json()["received"] == 25
    liquidity = liquidity_batch()
    plain = client.post("/api/fix/ingest", content=liquidity, headers={"Idempotency-Key": "plain-0001", "Content-Type": "text/plain"})
    assert plain.status_code == 200 and plain.json()["exceptions"] == 0


def test_decode_is_a_dry_run(client: TestClient) -> None:
    decoded = client.post("/api/fix/decode", json={"payload": SAMPLE}).json()
    assert len(decoded["messages"]) == 25
    assert decoded["counts"]["VALID"] == 20
    gap = next(m for m in decoded["messages"] if m["status"] == "SEQ_GAP")
    seq_field = next(f for f in gap["decoded"]["fields"] if f["tag"] == 34)
    assert seq_field["issues"] == ["SEQ_GAP"]
    corrupt = next(m for m in decoded["messages"] if m["status"] == "CHECKSUM_ERR")
    assert next(f for f in corrupt["decoded"]["fields"] if f["tag"] == 10)["issues"] == ["CHECKSUM_MISMATCH"]
    assert client.get("/api/messages").json() == []


def test_maker_checker_replay_of_sequence_gap(client: TestClient) -> None:
    ingest(client, SAMPLE)
    gap = exceptions_by_status(client)["SEQ_GAP"]
    assert gap["allowed_actions"] == ["REPLAY", "AMEND_AND_REPLAY"]
    request = stage(client, gap["id"], "REPLAY")
    assert request["status"] == "PENDING_APPROVAL" and request["preview"] == []
    assert client.post("/api/remediation/create", json={**{"exception_message_id": gap["id"], "action": "REPLAY", "maker_id": "x.y", "reason": "again!"}}).status_code == 409

    same = client.post(f"/api/remediation/{request['id']}/approve", json={"checker_id": "OPS.MAKER"})
    assert same.status_code == 403 and same.json()["error"] == "FOUR_EYES_VIOLATION"

    approved = client.post(f"/api/remediation/{request['id']}/approve", json={"checker_id": "ops.checker", "comment": "Verified against client OMS"})
    assert approved.status_code == 200 and approved.json()["status"] == "EXECUTED"
    assert approved.json()["result"]["responses"] == ["35=8 NEW/NEW"]
    assert client.get("/api/book/AAPL").json()["bids"] == [{"price_cents": 22850, "qty": 1_000, "orders": 1}]
    assert "SEQ_GAP" not in exceptions_by_status(client)
    assert client.post(f"/api/remediation/{request['id']}/approve", json={"checker_id": "ops.other"}).status_code == 409

    rebuilt = client.post("/api/book/AAPL/rebuild").json()
    assert rebuilt["matches_live"] and rebuilt["messages_replayed"] == 9


def test_amend_fat_finger_and_failed_duplicate_replay(client: TestClient) -> None:
    ingest(client, SAMPLE)
    by_status = exceptions_by_status(client)
    fat = by_status["COLLAR_REJECT"]
    assert "900.00%" in fat["root_cause"]
    amended = stage(client, fat["id"], "AMEND_AND_REPLAY", overrides={"44": "431.25"})
    assert "44=431.25" in amended["staged_pipe"] and amended["preview"] == []
    result = client.post(f"/api/remediation/{amended['id']}/approve", json={"checker_id": "ops.checker"}).json()
    assert result["status"] == "EXECUTED"
    assert client.get("/api/book/MSFT").json()["bids"][0] == {"price_cents": 43125, "qty": 500, "orders": 1}

    dup = by_status["DUPLICATE_CLORDID"]
    blind = stage(client, dup["id"], "REPLAY")
    assert [i["code"] for i in blind["preview"]] == ["DUPLICATE_CLORDID"]
    failed = client.post(f"/api/remediation/{blind['id']}/approve", json={"checker_id": "ops.checker"}).json()
    assert failed["status"] == "FAILED" and failed["result"]["issues"][0]["code"] == "DUPLICATE_CLORDID"
    fixed = stage(client, dup["id"], "AMEND_AND_REPLAY", overrides={"11": "HAL-77004"})
    assert client.post(f"/api/remediation/{fixed['id']}/approve", json={"checker_id": "ops.checker"}).json()["status"] == "EXECUTED"

    corrupt = by_status["CHECKSUM_ERR"]
    assert corrupt["allowed_actions"] == ["AMEND_AND_REPLAY"]
    reframed = stage(client, corrupt["id"], "AMEND_AND_REPLAY", overrides={"44": "228.58"})
    decline = client.post(f"/api/remediation/{reframed['id']}/decline", json={"checker_id": "ops.checker", "comment": "Client will resend"})
    assert decline.json()["status"] == "DECLINED"
    for symbol in ("AAPL", "MSFT", "NVDA"):
        assert client.post(f"/api/book/{symbol}/rebuild").json()["matches_live"]


def test_cancel_replace_adjustment_on_live_order(client: TestClient) -> None:
    ingest(client, SAMPLE)
    s = ClientSession("NBAM_OMS", "FIX.4.2", account="NB-LARGECAP-01", seq=7)
    when = datetime(2026, 10, 16, 13, 30, 15, tzinfo=UTC)
    oversized = s.replace(when, "NB-240002B", "NB-240002A", "NVDA", "2", 4_000, 1_385_000)
    summary = ingest(client, oversized, key="nb-replace-0001")
    assert summary["messages"][0]["status"] == "COLLAR_REJECT"
    assert summary["messages"][0]["responses"] == ["35=9 CxlRejReason=2"]
    exc = exceptions_by_status(client)["COLLAR_REJECT"]
    exc = next(e for e in client.get("/api/exceptions").json() if e["cl_ord_id"] == "NB-240002B")
    assert "CANCEL_REPLACE" in exc["allowed_actions"]
    cr = stage(client, exc["id"], "CANCEL_REPLACE", overrides={"44": "138.55", "38": "3500"})
    assert "41=NB-240002A" in cr["staged_pipe"] and "43=Y" in cr["staged_pipe"]
    done = client.post(f"/api/remediation/{cr['id']}/approve", json={"checker_id": "ops.checker"}).json()
    assert done["status"] == "EXECUTED"
    asks = client.get("/api/book/NVDA").json()["l3"]["asks"]
    assert asks[0]["price_cents"] == 13855 and asks[0]["leaves_qty"] == 2_000


def test_staged_payload_tamper_is_detected(client: TestClient, settings: Settings) -> None:
    ingest(client, SAMPLE)
    gap = exceptions_by_status(client)["SEQ_GAP"]
    request = stage(client, gap["id"], "REPLAY")
    engine = make_engine(settings.database_url)
    with engine.begin() as conn:
        conn.execute(text("UPDATE remediation_requests SET staged_payload = replace(staged_payload, '38=1000', '38=9000') WHERE id = :id"), {"id": request["id"]})
    engine.dispose()
    tampered = client.post(f"/api/remediation/{request['id']}/approve", json={"checker_id": "ops.checker"})
    assert tampered.status_code == 409 and tampered.json()["error"] == "STAGED_PAYLOAD_TAMPERED"


def test_append_only_tables_and_integer_money_columns(client: TestClient, settings: Settings) -> None:
    ingest(client, SAMPLE)
    engine = make_engine(settings.database_url)
    for statement in ("UPDATE fix_messages SET engine_applied = NOT engine_applied", "DELETE FROM executions", "UPDATE orders SET quantity = quantity + 1"):
        with pytest.raises(DBAPIError, match="append-only"):
            with engine.begin() as conn:
                conn.execute(text(statement))
    columns = {(t, c["name"]): c["type"] for t in ("orders", "executions") for c in inspect(engine).get_columns(t)}
    for (table, name), column_type in columns.items():
        if name.endswith("_cents") or name in ("quantity", "order_qty", "last_qty", "leaves_qty", "cum_qty"):
            assert column_type.python_type is int, f"{table}.{name} must be integral"
    engine.dispose()


def test_restart_recovers_book_sequence_and_clordid_state(settings: Settings) -> None:
    with TestClient(create_app(settings)) as first:
        ingest(first, SAMPLE)
        ingest(first, liquidity_batch())
        gap = exceptions_by_status(first)["SEQ_GAP"]
        request = stage(first, gap["id"], "REPLAY")
        first.post(f"/api/remediation/{request['id']}/approve", json={"checker_id": "ops.checker"})
        books = {s: first.get(f"/api/book/{s}").json()["state_checksum"] for s in ("AAPL", "MSFT", "NVDA")}
        tape = first.get("/api/trades").json()

    with TestClient(create_app(settings)) as second:
        assert {s: second.get(f"/api/book/{s}").json()["state_checksum"] for s in ("AAPL", "MSFT", "NVDA")} == books
        assert [t["match_id"] for t in second.get("/api/trades").json()] == [t["match_id"] for t in tape]
        nb = ClientSession("NBAM_OMS", "FIX.4.2", seq=7)
        when = datetime(2026, 10, 16, 13, 31, 0, tzinfo=UTC)
        nxt = ingest(second, nb.new_order(when, "NB-240006", "AAPL", BUY, 100, 22849), key="after-restart-1")
        assert nxt["messages"][0]["status"] == "VALID", "expected MsgSeqNum must survive restart"
        reused = ingest(second, nb.new_order(when, "NB-240005", "AAPL", BUY, 100, 22849), key="after-restart-2")
        assert reused["messages"][0]["status"] == "DUPLICATE_CLORDID", "remediated ClOrdIDs must stay reserved"


def test_websocket_streams_snapshot_then_batches(client: TestClient) -> None:
    with client.websocket_connect("/ws/market-data") as ws:
        snapshot = ws.receive_json()
        assert snapshot["type"] == "snapshot" and {b["symbol"] for b in snapshot["books"]} == {"AAPL", "MSFT", "NVDA"}
        ingest(client, SAMPLE)
        batch = ws.receive_json()
        kinds = {e["type"] for e in batch["events"]}
        assert {"execution", "trade", "book", "exception", "cancel_reject"} - kinds == {"cancel_reject"}
        books = [e["book"]["symbol"] for e in batch["events"] if e["type"] == "book"]
        assert sorted(books) == ["AAPL", "MSFT", "NVDA"]
        ws.send_text("ping")
        assert ws.receive_json() == {"type": "pong"}


def test_schema_metadata_matches_models(settings: Settings) -> None:
    engine = make_engine(settings.database_url)
    assert set(Base.metadata.tables) <= set(inspect(engine).get_table_names())
    engine.dispose()
