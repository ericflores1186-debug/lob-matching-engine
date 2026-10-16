from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from backend.fix_engine.parser import (
    SOH,
    IssueCode,
    RiskLimits,
    SessionValidator,
    TickSizeError,
    ValidationStatus,
    amend_message,
    format_avg_px,
    parse_fix,
    parse_price_cents,
    split_fix_stream,
    to_pipe,
)
from backend.seed import BUY, IOC, SELL, ClientSession

REFERENCE_LOGON = "8=FIX.4.2|9=65|35=A|49=SERVER|56=CLIENT|34=177|52=20090107-18:15:16|98=0|108=30|10=062|"
T0 = datetime(2026, 10, 16, 14, 0, tzinfo=UTC)


def codes(msg_or_result) -> list[IssueCode]:
    return [i.code for i in msg_or_result.issues]


def test_reference_frame_body_length_and_checksum() -> None:
    msg = parse_fix(REFERENCE_LOGON)
    assert msg.issues == []
    assert (msg.declared_body_length, msg.computed_body_length) == (65, 65)
    assert (msg.declared_checksum, msg.computed_checksum) == ("062", "062")


@pytest.mark.parametrize("delimiter", [SOH, "|", "^A"])
def test_delimiters_are_equivalent(delimiter: str) -> None:
    msg = parse_fix(REFERENCE_LOGON.replace("|", delimiter))
    assert msg.issues == []
    assert msg.tags()["108"] == "30"
    assert msg.normalized == REFERENCE_LOGON.replace("|", SOH)


def test_stream_splitter_handles_log_prefixes_and_concatenation() -> None:
    soh = REFERENCE_LOGON.replace("|", SOH)
    blob = f"20261016-14:00:00.000 : {REFERENCE_LOGON}\n\nnoise line\n{soh}{soh}\r\n8=FIX.4.2|9=5|35=0|"
    frames = split_fix_stream(blob)
    assert len(frames) == 4
    assert frames[0] == REFERENCE_LOGON
    assert frames[3].startswith("8=FIX.4.2|9=5")
    assert parse_fix(frames[3]).issues


def test_enum_and_typed_decoding() -> None:
    nb = ClientSession("BUYSIDE", "FIX.4.2")
    msg = parse_fix(nb.new_order(T0, "C1", "AAPL", BUY, 1_500, 22855, tif=IOC))
    decoded = msg.decoded()
    assert decoded["side"] == "BUY"
    assert decoded["ord_type"] == "LIMIT"
    assert decoded["time_in_force"] == "IOC"
    assert decoded["order_qty"] == 1_500
    assert decoded["price_cents"] == 22855 and isinstance(decoded["price_cents"], int)
    fields = {f.tag: f for f in msg.fields}
    assert fields[54].meaning == "Buy"
    assert fields[59].meaning == "Immediate or cancel"
    assert fields[44].meaning == "$228.55"
    assert fields[38].meaning == "1,500 shares"

    er = parse_fix("8=FIX.4.4|9=5|35=8|39=1|150=F|10=000|")
    meanings = {f.tag: f.meaning for f in er.fields}
    assert meanings[39] == "Partially filled"
    assert meanings[150].startswith("Trade")


def test_version_specific_field_names() -> None:
    assert {f.tag: f.name for f in parse_fix("8=FIX.4.2|9=5|35=8|32=100|10=000|").fields}[32] == "LastShares"
    assert {f.tag: f.name for f in parse_fix("8=FIX.4.4|9=5|35=8|32=100|10=000|").fields}[32] == "LastQty"


def test_checksum_and_body_length_corruption() -> None:
    frame = to_pipe(ClientSession("S", "FIX.4.2").new_order(T0, "C1", "AAPL", SELL, 100, 22855))
    assert codes(parse_fix(frame.replace("38=100", "38=900"))) == [IssueCode.CHECKSUM_MISMATCH]
    assert IssueCode.BODY_LENGTH_MISMATCH in codes(parse_fix(frame.replace("38=100", "38=1000")))


def test_structural_errors() -> None:
    msg = parse_fix("35=D|8=FIX.4.2|9=10|49=X|34=0|52=bad|11=|10=000|")
    found = set(codes(msg))
    assert {IssueCode.TAG_OUT_OF_ORDER, IssueCode.VALUE_OUT_OF_RANGE, IssueCode.INCORRECT_DATA_FORMAT, IssueCode.TAG_WITHOUT_VALUE} <= found
    assert IssueCode.REQUIRED_TAG_MISSING in found
    assert msg.is_malformed
    assert parse_fix("8=FIX.4.2|9=5|35=Z|49=A|56=B|34=1|52=20261016-14:00:00|10=000|").is_malformed
    assert IssueCode.UNSUPPORTED_VERSION in codes(parse_fix("8=FIXT.1.1|9=5|35=0|10=000|"))


def test_prices_are_integral_minor_units() -> None:
    assert parse_price_cents("228.55") == 22855
    assert parse_price_cents("228.5") == 22850
    assert parse_price_cents("228.5500") == 22855
    assert parse_price_cents("1") == 100
    with pytest.raises(TickSizeError):
        parse_price_cents("228.555")
    with pytest.raises(ValueError):
        parse_price_cents("1e2")
    assert format_avg_px(200 * 18710 + 100 * 18710 + 150 * 18720, 450) == "187.133333"
    assert format_avg_px(3 * 1, 2) == "0.015"
    assert format_avg_px(0, 0) == "0"


def test_amend_recomputes_framing() -> None:
    raw = ClientSession("S", "FIX.4.2").new_order(T0, "C1", "MSFT", BUY, 500, 431_250)
    amended = parse_fix(amend_message(parse_fix(raw), {44: "431.25", 58: "Price corrected per desk"}))
    assert amended.issues == []
    assert amended.price_cents == 43125
    assert amended.get(58) == "Price corrected per desk"
    assert [f.tag for f in amended.fields][:3] == [8, 9, 35]

    garbled = parse_fix(to_pipe(raw).replace("38=500", "38=900"))
    assert garbled.is_garbled
    assert parse_fix(amend_message(garbled, {38: "500"})).issues == []


def _validator(**limits: int) -> SessionValidator:
    refs = {"AAPL": 22850, "MSFT": 43120}
    return SessionValidator(limits=RiskLimits(**limits), reference_price=refs.get)


def test_sequence_gap_then_possdup_gap_fill() -> None:
    v = _validator()
    s = ClientSession("NB", "FIX.4.2")
    assert v.validate(parse_fix(s.logon(T0))).is_valid
    gap = v.validate(parse_fix(s.new_order(T0, "C3", "AAPL", BUY, 100, 22850, seq=4)))
    assert gap.status is ValidationStatus.SEQ_GAP and gap.gap == (2, 3)
    assert v.open_gaps() == {"NB": [2, 3]}
    fill = v.validate(parse_fix(s.new_order(T0, "C1", "AAPL", BUY, 100, 22850, seq=2, poss_dup_of=T0)))
    assert fill.is_valid and IssueCode.GAP_FILLED in codes(fill)
    dup = v.validate(parse_fix(s.new_order(T0, "C1", "AAPL", BUY, 100, 22850, seq=2, poss_dup_of=T0)))
    assert dup.status is ValidationStatus.POSS_DUP
    low = v.validate(parse_fix(s.new_order(T0, "C9", "AAPL", BUY, 100, 22850, seq=1)))
    assert low.status is ValidationStatus.SEQ_TOO_LOW
    assert v.expected_seq("NB") == 5


def test_garbled_frames_do_not_consume_sequence() -> None:
    v = _validator()
    s = ClientSession("MM", "FIX.4.4")
    v.validate(parse_fix(s.logon(T0)))
    corrupt = to_pipe(s.new_order(T0, "C1", "AAPL", SELL, 100, 22855)).replace("38=100", "38=900")
    assert v.validate(parse_fix(corrupt)).status is ValidationStatus.CHECKSUM_ERR
    assert v.expected_seq("MM") == 2


def test_sequence_reset_gap_fill() -> None:
    v = _validator()
    s = ClientSession("MM", "FIX.4.4")
    v.validate(parse_fix(s.logon(T0)))
    v.validate(parse_fix(s.new_order(T0, "C1", "AAPL", SELL, 100, 22855, seq=5)))
    assert v.open_gaps() == {"MM": [2, 3, 4]}
    gap_fill = s.frame("4", [(123, "Y"), (36, "5")], T0, seq=2, poss_dup_of=T0)
    assert v.validate(parse_fix(gap_fill)).is_valid
    assert v.open_gaps() == {}


def test_stale_duplicate_and_risk_checks() -> None:
    v = _validator(collar_bps=1_000, max_order_qty=10_000, max_notional_cents=1_000_000_00)
    s = ClientSession("HAL", "FIX.4.2")
    assert v.validate(parse_fix(s.new_order(T0, "C1", "AAPL", BUY, 100, 22850))).is_valid
    assert v.validate(parse_fix(s.new_order(T0, "C1", "AAPL", BUY, 100, 22850))).status is ValidationStatus.DUPLICATE_CLORDID
    stale = v.validate(parse_fix(s.new_order(T0 - timedelta(minutes=5), "C2", "AAPL", BUY, 100, 22850)))
    assert stale.status is ValidationStatus.STALE_TIMESTAMP
    collar = v.validate(parse_fix(s.new_order(T0, "C3", "MSFT", BUY, 10, 431_250)))
    assert collar.status is ValidationStatus.COLLAR_REJECT
    assert "900.11%" in collar.primary_issue.detail
    assert v.validate(parse_fix(s.new_order(T0, "C4", "AAPL", BUY, 20_000, 22850))).status is ValidationStatus.COLLAR_REJECT
    notional = v.validate(parse_fix(s.new_order(T0, "C5", "AAPL", BUY, 5_000, 22850)))
    assert [i.code for i in notional.issues] == [IssueCode.NOTIONAL_LIMIT_BREACH]


def test_business_rejects_for_unsupported_terms() -> None:
    v = _validator()
    s = ClientSession("Q", "FIX.4.4")
    stop = s.frame("D", [(11, "S1"), (55, "AAPL"), (54, "1"), (38, "100"), (40, "3"), (99, "229.00"), (59, "0"), (60, "20261016-14:00:00")], T0)
    assert v.validate(parse_fix(stop)).status is ValidationStatus.BUSINESS_REJECT
    sub_penny = s.frame("D", [(11, "S2"), (55, "AAPL"), (54, "1"), (38, "100"), (40, "2"), (44, "228.555"), (59, "0"), (60, "20261016-14:00:00")], T0)
    result = v.validate(parse_fix(sub_penny))
    assert result.status is ValidationStatus.BUSINESS_REJECT
    assert IssueCode.TICK_SIZE_VIOLATION in codes(result)
    bad_symbol = s.frame("D", [(11, "S3"), (55, "aapl us"), (54, "1"), (38, "100"), (40, "1"), (59, "3"), (60, "20261016-14:00:00")], T0)
    assert IssueCode.INVALID_SYMBOL in codes(v.validate(parse_fix(bad_symbol)))
