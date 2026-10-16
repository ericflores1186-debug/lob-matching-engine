from __future__ import annotations

import re
from collections import Counter
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any

from backend.fix_engine import dictionary as fd
from backend.fix_engine.dictionary import FieldType

SOH = "\x01"
SUPPORTED_VERSIONS: frozenset[str] = frozenset({"FIX.4.2", "FIX.4.4"})
MAX_TRACKED_GAP = 10_000

_DELIMITER_LABELS = {SOH: "SOH", "|": "PIPE", "^A": "CARET_A"}
_DECIMAL_RE = re.compile(r"^(\d+)(?:\.(\d+))?$")
_FRAME_RE = re.compile(r"8=FIX[.\w]*[\x01|].*?[\x01|]10=\d{3}[\x01|]?")
_PREFIX_RE = re.compile(rb"^8=[^\x01]*\x019=[^\x01]*\x01")
SYMBOL_RE = re.compile(r"^[A-Z0-9][A-Z0-9./-]{0,15}$")


class ValidationStatus(StrEnum):
    VALID = "VALID"
    MALFORMED = "MALFORMED"
    BODY_LENGTH_ERR = "BODY_LENGTH_ERR"
    CHECKSUM_ERR = "CHECKSUM_ERR"
    SEQ_TOO_LOW = "SEQ_TOO_LOW"
    SEQ_GAP = "SEQ_GAP"
    POSS_DUP = "POSS_DUP"
    STALE_TIMESTAMP = "STALE_TIMESTAMP"
    DUPLICATE_CLORDID = "DUPLICATE_CLORDID"
    BUSINESS_REJECT = "BUSINESS_REJECT"
    COLLAR_REJECT = "COLLAR_REJECT"


class Severity(StrEnum):
    ERROR = "ERROR"
    WARN = "WARN"
    INFO = "INFO"


class IssueCode(StrEnum):
    EMPTY_MESSAGE = "EMPTY_MESSAGE"
    INVALID_TAG_FORMAT = "INVALID_TAG_FORMAT"
    TAG_WITHOUT_VALUE = "TAG_WITHOUT_VALUE"
    TAG_OUT_OF_ORDER = "TAG_OUT_OF_ORDER"
    TAG_REPEATED = "TAG_REPEATED"
    REQUIRED_TAG_MISSING = "REQUIRED_TAG_MISSING"
    UNSUPPORTED_VERSION = "UNSUPPORTED_VERSION"
    INVALID_MSG_TYPE = "INVALID_MSG_TYPE"
    INCORRECT_DATA_FORMAT = "INCORRECT_DATA_FORMAT"
    VALUE_OUT_OF_RANGE = "VALUE_OUT_OF_RANGE"
    BODY_LENGTH_MISMATCH = "BODY_LENGTH_MISMATCH"
    CHECKSUM_MISMATCH = "CHECKSUM_MISMATCH"
    SEQ_TOO_LOW = "SEQ_TOO_LOW"
    SEQ_GAP = "SEQ_GAP"
    GAP_FILLED = "GAP_FILLED"
    SEQUENCE_RESET = "SEQUENCE_RESET"
    POSS_DUP_IGNORED = "POSS_DUP_IGNORED"
    STALE_SENDING_TIME = "STALE_SENDING_TIME"
    DUPLICATE_CLORDID = "DUPLICATE_CLORDID"
    UNSUPPORTED_SIDE = "UNSUPPORTED_SIDE"
    UNSUPPORTED_ORD_TYPE = "UNSUPPORTED_ORD_TYPE"
    UNSUPPORTED_TIF = "UNSUPPORTED_TIF"
    INVALID_QUANTITY = "INVALID_QUANTITY"
    INVALID_PRICE = "INVALID_PRICE"
    TICK_SIZE_VIOLATION = "TICK_SIZE_VIOLATION"
    PRICE_COLLAR_BREACH = "PRICE_COLLAR_BREACH"
    NOTIONAL_LIMIT_BREACH = "NOTIONAL_LIMIT_BREACH"
    QTY_LIMIT_BREACH = "QTY_LIMIT_BREACH"
    INVALID_SYMBOL = "INVALID_SYMBOL"


S = ValidationStatus
IC = IssueCode

ISSUE_STATUS: dict[IssueCode, ValidationStatus] = {
    IC.EMPTY_MESSAGE: S.MALFORMED,
    IC.INVALID_TAG_FORMAT: S.MALFORMED,
    IC.TAG_WITHOUT_VALUE: S.MALFORMED,
    IC.TAG_OUT_OF_ORDER: S.MALFORMED,
    IC.TAG_REPEATED: S.MALFORMED,
    IC.REQUIRED_TAG_MISSING: S.MALFORMED,
    IC.UNSUPPORTED_VERSION: S.MALFORMED,
    IC.INVALID_MSG_TYPE: S.MALFORMED,
    IC.INCORRECT_DATA_FORMAT: S.MALFORMED,
    IC.VALUE_OUT_OF_RANGE: S.MALFORMED,
    IC.BODY_LENGTH_MISMATCH: S.BODY_LENGTH_ERR,
    IC.CHECKSUM_MISMATCH: S.CHECKSUM_ERR,
    IC.SEQ_TOO_LOW: S.SEQ_TOO_LOW,
    IC.SEQ_GAP: S.SEQ_GAP,
    IC.GAP_FILLED: S.VALID,
    IC.SEQUENCE_RESET: S.VALID,
    IC.POSS_DUP_IGNORED: S.POSS_DUP,
    IC.STALE_SENDING_TIME: S.STALE_TIMESTAMP,
    IC.DUPLICATE_CLORDID: S.DUPLICATE_CLORDID,
    IC.UNSUPPORTED_SIDE: S.BUSINESS_REJECT,
    IC.UNSUPPORTED_ORD_TYPE: S.BUSINESS_REJECT,
    IC.UNSUPPORTED_TIF: S.BUSINESS_REJECT,
    IC.INVALID_QUANTITY: S.BUSINESS_REJECT,
    IC.INVALID_PRICE: S.BUSINESS_REJECT,
    IC.TICK_SIZE_VIOLATION: S.BUSINESS_REJECT,
    IC.PRICE_COLLAR_BREACH: S.COLLAR_REJECT,
    IC.NOTIONAL_LIMIT_BREACH: S.COLLAR_REJECT,
    IC.QTY_LIMIT_BREACH: S.COLLAR_REJECT,
    IC.INVALID_SYMBOL: S.BUSINESS_REJECT,
}

# Garbled frames come first: their content cannot be trusted for anything else
STATUS_PRECEDENCE: tuple[ValidationStatus, ...] = (
    S.BODY_LENGTH_ERR,
    S.CHECKSUM_ERR,
    S.MALFORMED,
    S.SEQ_TOO_LOW,
    S.SEQ_GAP,
    S.POSS_DUP,
    S.STALE_TIMESTAMP,
    S.DUPLICATE_CLORDID,
    S.BUSINESS_REJECT,
    S.COLLAR_REJECT,
)

SESSION_REJECT_REASON: dict[IssueCode, str] = {
    IC.EMPTY_MESSAGE: "99",
    IC.INVALID_TAG_FORMAT: "0",
    IC.REQUIRED_TAG_MISSING: "1",
    IC.TAG_WITHOUT_VALUE: "4",
    IC.VALUE_OUT_OF_RANGE: "5",
    IC.INCORRECT_DATA_FORMAT: "6",
    IC.STALE_SENDING_TIME: "10",
    IC.INVALID_MSG_TYPE: "11",
    IC.TAG_REPEATED: "13",
    IC.TAG_OUT_OF_ORDER: "14",
    IC.UNSUPPORTED_VERSION: "99",
}

_ORD_REJ_REASON: dict[IssueCode, tuple[str, str]] = {
    # (FIX 4.2, FIX 4.4); 4.2 has no 'Other' so broker option is used
    IC.DUPLICATE_CLORDID: ("6", "6"),
    IC.PRICE_COLLAR_BREACH: ("3", "3"),
    IC.NOTIONAL_LIMIT_BREACH: ("3", "3"),
    IC.QTY_LIMIT_BREACH: ("3", "3"),
    IC.STALE_SENDING_TIME: ("8", "8"),
    IC.INVALID_QUANTITY: ("0", "13"),
    IC.UNSUPPORTED_SIDE: ("0", "11"),
    IC.UNSUPPORTED_ORD_TYPE: ("0", "11"),
    IC.UNSUPPORTED_TIF: ("0", "11"),
    IC.INVALID_PRICE: ("0", "99"),
    IC.INVALID_SYMBOL: ("1", "1"),
    IC.TICK_SIZE_VIOLATION: ("0", "99"),
}

_CXL_REJ_REASON: dict[IssueCode, tuple[str, str]] = {
    IC.DUPLICATE_CLORDID: ("2", "6"),
}

SUPPORTED_SIDES: frozenset[str] = frozenset({"1", "2", "5", "6"})
SUPPORTED_ORD_TYPES: frozenset[str] = frozenset({"1", "2"})
SUPPORTED_TIFS: frozenset[str] = frozenset({"0", "1", "3", "4"})


class TickSizeError(ValueError):
    pass


def parse_price_cents(raw: str) -> int:
    m = _DECIMAL_RE.match(raw.strip())
    if not m:
        raise ValueError(f"not a non-negative decimal price: {raw!r}")
    whole, frac = m.group(1), m.group(2) or ""
    if len(frac) > 2:
        if frac[2:].strip("0"):
            raise TickSizeError(f"sub-penny price {raw!r} violates $0.01 tick")
        frac = frac[:2]
    return int(whole) * 100 + int(frac.ljust(2, "0"))


def parse_qty(raw: str) -> int:
    m = _DECIMAL_RE.match(raw.strip())
    if not m or (m.group(2) and m.group(2).strip("0")):
        raise ValueError(f"not a whole-share quantity: {raw!r}")
    return int(m.group(1))


def format_cents(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}{whole}.{frac:02d}"


def format_usd(cents: int) -> str:
    sign = "-" if cents < 0 else ""
    whole, frac = divmod(abs(cents), 100)
    return f"{sign}${whole:,}.{frac:02d}"


def format_bps_as_pct(bps: int) -> str:
    whole, frac = divmod(bps, 100)
    return f"{whole}.{frac:02d}%"


def format_avg_px(cum_notional_cents: int, cum_qty: int) -> str:
    if cum_qty <= 0:
        return "0"
    # Integer round-half-up to 6dp: cents -> micros is x10_000
    micros = (cum_notional_cents * 20_000 + cum_qty) // (2 * cum_qty)
    whole, frac = divmod(micros, 1_000_000)
    text = f"{frac:06d}".rstrip("0")
    return f"{whole}.{text.ljust(2, '0')}"


def parse_utc_timestamp(raw: str) -> datetime:
    value = raw.strip()
    if "." in value:
        head, frac = value.split(".", 1)
        if not frac.isdigit():
            raise ValueError(f"bad fractional seconds: {raw!r}")
        # Sub-microsecond precision (4.4 allows up to picos) is truncated
        value = f"{head}.{frac[:6]}"
        fmt = "%Y%m%d-%H:%M:%S.%f"
    else:
        fmt = "%Y%m%d-%H:%M:%S"
    return datetime.strptime(value, fmt).replace(tzinfo=UTC)


def format_utc_timestamp(ts: datetime) -> str:
    ts = ts.astimezone(UTC) if ts.tzinfo else ts.replace(tzinfo=UTC)
    return ts.strftime("%Y%m%d-%H:%M:%S.") + f"{ts.microsecond // 1000:03d}"


def _to_bytes(text: str) -> bytes:
    try:
        return text.encode("latin-1")
    except UnicodeEncodeError:
        return text.encode("utf-8")


def compute_checksum(data: bytes) -> str:
    return f"{sum(data) % 256:03d}"


def detect_delimiter(raw: str) -> str:
    if SOH in raw:
        return SOH
    if "^A" in raw:
        return "^A"
    if "|" in raw:
        return "|"
    return SOH


def normalize(raw: str) -> tuple[str, str]:
    delimiter = detect_delimiter(raw)
    text = raw.strip(" \t\r\n")
    if delimiter != SOH:
        # Pipe-rendered logs replaced SOH for display; checksum is always over SOH bytes
        text = text.replace(delimiter, SOH)
    if text and not text.endswith(SOH):
        text += SOH
    return text, delimiter


def to_pipe(raw: str) -> str:
    return raw.replace(SOH, "|")


def split_fix_stream(blob: str) -> list[str]:
    frames: list[str] = []

    def take(fragment: str) -> None:
        start = fragment.find("8=FIX")
        if start >= 0:
            frames.append(fragment[start:].rstrip(" \t"))

    for line in blob.replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        line = line.replace("^A", SOH)
        start = line.find("8=FIX")
        if start < 0:
            continue
        segment = line[start:]
        pos = 0
        for m in _FRAME_RE.finditer(segment):
            take(segment[pos : m.start()])
            frames.append(m.group(0))
            pos = m.end()
        take(segment[pos:])
    return frames


@dataclass(frozen=True, slots=True)
class ValidationIssue:
    code: IssueCode
    severity: Severity
    detail: str
    tag: int | None = None

    @property
    def status(self) -> ValidationStatus:
        return ISSUE_STATUS[self.code]

    def to_dict(self) -> dict[str, Any]:
        return {
            "code": self.code.value,
            "severity": self.severity.value,
            "status": self.status.value,
            "tag": self.tag,
            "detail": self.detail,
        }


@dataclass(frozen=True, slots=True)
class FixField:
    index: int
    tag: int | None
    value: str
    name: str
    meaning: str | None


@dataclass(slots=True)
class ParsedMessage:
    raw: str
    normalized: str
    delimiter: str
    fields: list[FixField]
    issues: list[ValidationIssue]
    declared_body_length: int | None = None
    computed_body_length: int | None = None
    declared_checksum: str | None = None
    computed_checksum: str | None = None
    _tags: dict[int, str] = field(default_factory=dict)

    def get(self, tag: int, default: str | None = None) -> str | None:
        return self._tags.get(tag, default)

    def has(self, tag: int) -> bool:
        return tag in self._tags

    @property
    def begin_string(self) -> str | None:
        return self.get(8)

    @property
    def msg_type(self) -> str | None:
        return self.get(35)

    @property
    def msg_type_name(self) -> str | None:
        return fd.enum_code(35, self.msg_type) if self.msg_type else None

    @property
    def sender_comp_id(self) -> str | None:
        return self.get(49)

    @property
    def target_comp_id(self) -> str | None:
        return self.get(56)

    @property
    def msg_seq_num(self) -> int | None:
        return _maybe(int, self.get(34))

    @property
    def sending_time(self) -> datetime | None:
        return _maybe(parse_utc_timestamp, self.get(52))

    @property
    def transact_time(self) -> datetime | None:
        return _maybe(parse_utc_timestamp, self.get(60))

    @property
    def poss_dup(self) -> bool:
        return self.get(43) == "Y"

    @property
    def cl_ord_id(self) -> str | None:
        return self.get(11)

    @property
    def orig_cl_ord_id(self) -> str | None:
        return self.get(41)

    @property
    def symbol(self) -> str | None:
        return self.get(55)

    @property
    def side(self) -> str | None:
        return self.get(54)

    @property
    def ord_type(self) -> str | None:
        return self.get(40)

    @property
    def time_in_force(self) -> str:
        return self.get(59) or "0"

    @property
    def order_qty(self) -> int | None:
        return _maybe(parse_qty, self.get(38))

    @property
    def price_cents(self) -> int | None:
        return _maybe(parse_price_cents, self.get(44))

    @property
    def is_garbled(self) -> bool:
        return any(i.code in (IC.BODY_LENGTH_MISMATCH, IC.CHECKSUM_MISMATCH) for i in self.issues)

    @property
    def is_malformed(self) -> bool:
        return any(i.status is S.MALFORMED for i in self.issues)

    def tags(self) -> dict[str, str]:
        return {str(tag): value for tag, value in self._tags.items()}

    def decoded(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for tag, key in _BUSINESS_FIELDS:
            raw = self._tags.get(tag)
            if raw is None:
                continue
            ftype = fd.field_type(tag)
            if tag in fd.ENUMS:
                out[key] = fd.enum_code(tag, raw) or raw
            elif ftype is FieldType.QTY or ftype is FieldType.SEQNUM:
                parsed = _maybe(parse_qty, raw)
                out[key] = raw if parsed is None else parsed
            elif tag in (31, 44, 99):
                # Sub-penny prints (e.g. midpoint LastPx) stay as raw strings
                parsed = _maybe(parse_price_cents, raw)
                out[key] = raw if parsed is None else parsed
            elif ftype is FieldType.UTCTIMESTAMP:
                ts = _maybe(parse_utc_timestamp, raw)
                out[key] = ts.isoformat() if ts else raw
            else:
                out[key] = raw
        return out

    def to_dict(self) -> dict[str, Any]:
        issues_by_tag: dict[int, list[str]] = {}
        for issue in self.issues:
            if issue.tag is not None:
                issues_by_tag.setdefault(issue.tag, []).append(issue.code.value)
        sending_time = self.sending_time
        return {
            "begin_string": self.begin_string,
            "msg_type": self.msg_type,
            "msg_type_name": self.msg_type_name,
            "delimiter": _DELIMITER_LABELS.get(self.delimiter, "SOH"),
            "sender_comp_id": self.sender_comp_id,
            "target_comp_id": self.target_comp_id,
            "msg_seq_num": self.msg_seq_num,
            "sending_time": sending_time.isoformat() if sending_time else None,
            "body_length": {"declared": self.declared_body_length, "computed": self.computed_body_length},
            "checksum": {"declared": self.declared_checksum, "computed": self.computed_checksum},
            "fields": [
                {
                    "index": f.index,
                    "tag": f.tag,
                    "name": f.name,
                    "value": f.value,
                    "meaning": f.meaning,
                    "issues": issues_by_tag.get(f.tag, []) if f.tag is not None else [],
                }
                for f in self.fields
            ],
            "tags": self.tags(),
            "decoded": self.decoded(),
            "issues": [i.to_dict() for i in self.issues],
        }


_BUSINESS_FIELDS: tuple[tuple[int, str], ...] = (
    (35, "msg_type"),
    (49, "sender_comp_id"),
    (56, "target_comp_id"),
    (34, "msg_seq_num"),
    (52, "sending_time"),
    (11, "cl_ord_id"),
    (41, "orig_cl_ord_id"),
    (37, "order_id"),
    (17, "exec_id"),
    (1, "account"),
    (55, "symbol"),
    (54, "side"),
    (40, "ord_type"),
    (59, "time_in_force"),
    (38, "order_qty"),
    (44, "price_cents"),
    (99, "stop_px_cents"),
    (150, "exec_type"),
    (39, "ord_status"),
    (32, "last_qty"),
    (31, "last_px_cents"),
    (151, "leaves_qty"),
    (14, "cum_qty"),
    (6, "avg_px"),
    (103, "ord_rej_reason"),
    (102, "cxl_rej_reason"),
    (373, "session_reject_reason"),
    (45, "ref_seq_num"),
    (60, "transact_time"),
    (58, "text"),
)


def _maybe(fn: Callable[[str], Any], raw: str | None) -> Any:
    if raw is None:
        return None
    try:
        return fn(raw)
    except ValueError:
        return None


def describe_value(tag: int, value: str) -> str | None:
    desc = fd.enum_description(tag, value)
    if desc is not None:
        return desc
    if tag in fd.ENUMS:
        return "Unrecognized value"
    ftype = fd.field_type(tag)
    try:
        if ftype is FieldType.PRICE:
            m = _DECIMAL_RE.match(value)
            if not m:
                return None
            whole, frac = int(m.group(1)), (m.group(2) or "").ljust(2, "0")
            return f"${whole:,}.{frac}"
        if ftype is FieldType.QTY:
            return f"{parse_qty(value):,} shares"
        if ftype is FieldType.SEQNUM:
            return f"Sequence #{int(value):,}"
        if ftype is FieldType.LENGTH:
            return f"{int(value):,} bytes after tag 9 through the delimiter before tag 10"
        if ftype is FieldType.CHECKSUM:
            return "Sum of all preceding bytes mod 256"
        if ftype is FieldType.UTCTIMESTAMP:
            ts = parse_utc_timestamp(value)
            return ts.strftime("%Y-%m-%d %H:%M:%S.") + f"{ts.microsecond // 1000:03d} UTC"
        if ftype is FieldType.LOCALMKTDATE and len(value) == 8 and value.isdigit():
            return f"{value[:4]}-{value[4:6]}-{value[6:]}"
        if ftype is FieldType.BOOLEAN:
            return {"Y": "Yes", "N": "No"}.get(value)
        if tag == 108:
            return f"{int(value)}s heartbeat interval"
    except ValueError:
        return None
    return None


def _check_format(tag: int, value: str, begin_string: str | None) -> ValidationIssue | None:
    ftype = fd.field_type(tag)
    name = fd.field_name(tag, begin_string)
    bad = False
    if ftype in (FieldType.SEQNUM, FieldType.LENGTH, FieldType.INT):
        bad = not value.isdigit()
    elif ftype is FieldType.QTY:
        bad = _maybe(parse_qty, value) is None
    elif ftype is FieldType.PRICE:
        bad = _DECIMAL_RE.match(value) is None
    elif ftype is FieldType.UTCTIMESTAMP:
        bad = _maybe(parse_utc_timestamp, value) is None
    elif ftype is FieldType.BOOLEAN:
        bad = value not in ("Y", "N")
    elif ftype is FieldType.CHECKSUM:
        bad = not (len(value) == 3 and value.isdigit())
    if bad:
        return ValidationIssue(IC.INCORRECT_DATA_FORMAT, Severity.ERROR, f"{name}({tag})={value!r} is not a valid {ftype.value}", tag)
    if tag == 34 and value.isdigit() and int(value) == 0:
        return ValidationIssue(IC.VALUE_OUT_OF_RANGE, Severity.ERROR, "MsgSeqNum(34) must be >= 1", tag)
    if tag in fd.STRICT_ENUM_TAGS and value not in fd.ENUMS[tag]:
        return ValidationIssue(IC.VALUE_OUT_OF_RANGE, Severity.ERROR, f"{name}({tag})={value!r} is not a defined value", tag)
    return None


def parse_fix(raw: str) -> ParsedMessage:
    normalized, delimiter = normalize(raw)
    issues: list[ValidationIssue] = []
    msg = ParsedMessage(raw=raw, normalized=normalized, delimiter=delimiter, fields=[], issues=issues)
    if not normalized:
        issues.append(ValidationIssue(IC.EMPTY_MESSAGE, Severity.ERROR, "Empty message"))
        return msg

    tokens = normalized[:-1].split(SOH)
    begin_string = None
    if tokens and tokens[0].startswith("8="):
        begin_string = tokens[0][2:]

    for index, token in enumerate(tokens):
        tag_text, sep, value = token.partition("=")
        if not sep or not tag_text.isdigit() or tag_text.startswith("0"):
            issues.append(ValidationIssue(IC.INVALID_TAG_FORMAT, Severity.ERROR, f"Field {index + 1} {token!r} is not tag=value"))
            msg.fields.append(FixField(index, None, token, "Invalid", None))
            continue
        tag = int(tag_text)
        msg.fields.append(FixField(index, tag, value, fd.field_name(tag, begin_string), describe_value(tag, value) if value else None))
        if value == "":
            issues.append(ValidationIssue(IC.TAG_WITHOUT_VALUE, Severity.ERROR, f"{fd.field_name(tag, begin_string)}({tag}) has no value", tag))
            continue
        msg._tags.setdefault(tag, value)
        issue = _check_format(tag, value, begin_string)
        if issue:
            issues.append(issue)

    tags_in_order = [f.tag for f in msg.fields]
    present = set(tags_in_order)
    for position, expected in ((0, 8), (1, 9), (2, 35)):
        if expected not in present:
            issues.append(ValidationIssue(IC.REQUIRED_TAG_MISSING, Severity.ERROR, f"Required {fd.field_name(expected)}({expected}) is missing", expected))
        elif tags_in_order[position : position + 1] != [expected]:
            issues.append(ValidationIssue(IC.TAG_OUT_OF_ORDER, Severity.ERROR, f"Tag {expected} must be field {position + 1} of the header", expected))
    if 10 not in present:
        issues.append(ValidationIssue(IC.REQUIRED_TAG_MISSING, Severity.ERROR, "Required CheckSum(10) is missing", 10))
    elif tags_in_order[-1] != 10:
        issues.append(ValidationIssue(IC.TAG_OUT_OF_ORDER, Severity.ERROR, "CheckSum(10) must be the final field", 10))

    for tag, count in Counter(t for t in tags_in_order if t is not None).items():
        if count > 1:
            issues.append(ValidationIssue(IC.TAG_REPEATED, Severity.ERROR, f"{fd.field_name(tag, begin_string)}({tag}) appears {count} times", tag))

    if begin_string is not None and begin_string not in SUPPORTED_VERSIONS:
        issues.append(ValidationIssue(IC.UNSUPPORTED_VERSION, Severity.ERROR, f"BeginString {begin_string!r} is not FIX.4.2 or FIX.4.4", 8))

    _check_framing(msg)
    _check_required(msg, begin_string)
    return msg


def _check_framing(msg: ParsedMessage) -> None:
    data = _to_bytes(msg.normalized)
    msg.declared_body_length = _maybe(int, msg.get(9))
    msg.declared_checksum = msg.get(10)

    trailer_at = data.rfind(b"\x0110=")
    prefix = _PREFIX_RE.match(data)
    if trailer_at < 0 or prefix is None:
        return

    # BodyLength spans the byte after 9=nnn<SOH> through the SOH preceding 10=
    msg.computed_body_length = trailer_at + 1 - prefix.end()
    msg.computed_checksum = compute_checksum(data[: trailer_at + 1])

    if msg.declared_body_length is not None and msg.declared_body_length != msg.computed_body_length:
        msg.issues.append(
            ValidationIssue(
                IC.BODY_LENGTH_MISMATCH,
                Severity.ERROR,
                f"BodyLength(9) declares {msg.declared_body_length} bytes, frame has {msg.computed_body_length}",
                9,
            )
        )
    if msg.declared_checksum is not None and msg.declared_checksum != msg.computed_checksum:
        msg.issues.append(
            ValidationIssue(
                IC.CHECKSUM_MISMATCH,
                Severity.ERROR,
                f"CheckSum(10) declares {msg.declared_checksum}, computed {msg.computed_checksum}",
                10,
            )
        )


def _check_required(msg: ParsedMessage, begin_string: str | None) -> None:
    msg_type = msg.msg_type
    if msg_type is not None and msg_type not in fd.MSG_TYPES:
        msg.issues.append(ValidationIssue(IC.INVALID_MSG_TYPE, Severity.ERROR, f"MsgType(35)={msg_type!r} is not supported", 35))
        return
    required = list(fd.HEADER_REQUIRED)
    if msg_type:
        required += fd.REQUIRED_BY_MSG_TYPE.get(msg_type, ())
        required += fd.REQUIRED_BY_VERSION.get((begin_string or "", msg_type), ())
        if msg_type in ("D", "G") and msg.get(40) == "2":
            required.append(44)
    empty = {i.tag for i in msg.issues if i.code is IC.TAG_WITHOUT_VALUE}
    for tag in dict.fromkeys(required):
        if not msg.has(tag) and tag not in empty:
            msg.issues.append(
                ValidationIssue(IC.REQUIRED_TAG_MISSING, Severity.ERROR, f"Required {fd.field_name(tag, begin_string)}({tag}) is missing", tag)
            )


def resolve_status(issues: Iterable[ValidationIssue]) -> ValidationStatus:
    present = {i.status for i in issues if i.severity is not Severity.INFO}
    for status in STATUS_PRECEDENCE:
        if status in present:
            return status
    return S.VALID


def ord_rej_reason(code: IssueCode, begin_string: str | None) -> str:
    v42, v44 = _ORD_REJ_REASON.get(code, ("0", "99"))
    return v44 if begin_string == "FIX.4.4" else v42


def cxl_rej_reason(code: IssueCode, begin_string: str | None) -> str:
    v42, v44 = _CXL_REJ_REASON.get(code, ("2", "99"))
    return v44 if begin_string == "FIX.4.4" else v42


@dataclass(frozen=True, slots=True)
class RiskLimits:
    collar_bps: int = 1_000
    max_order_qty: int = 1_000_000
    max_notional_cents: int = 10_000_000_00


@dataclass(slots=True)
class _SessionState:
    expected_seq: int
    missing: set[int] = field(default_factory=set)
    cl_ord_ids: set[str] = field(default_factory=set)


@dataclass(frozen=True, slots=True)
class ValidationResult:
    status: ValidationStatus
    issues: tuple[ValidationIssue, ...]
    expected_seq: int | None = None
    gap: tuple[int, int] | None = None

    @property
    def is_valid(self) -> bool:
        return self.status is S.VALID

    @property
    def primary_issue(self) -> ValidationIssue | None:
        for issue in self.issues:
            if issue.severity is not Severity.INFO and issue.status is self.status:
                return issue
        return None


class SessionValidator:
    def __init__(
        self,
        *,
        limits: RiskLimits | None = None,
        max_clock_skew: timedelta = timedelta(seconds=120),
        reference_price: Callable[[str], int | None] | None = None,
    ) -> None:
        self.limits = limits or RiskLimits()
        self.max_clock_skew = max_clock_skew
        self._reference_price = reference_price or (lambda _symbol: None)
        self._sessions: dict[str, _SessionState] = {}
        self._clock_hwm: datetime | None = None

    def expected_seq(self, sender_comp_id: str) -> int | None:
        state = self._sessions.get(sender_comp_id)
        return state.expected_seq if state else None

    def open_gaps(self) -> dict[str, list[int]]:
        return {sender: sorted(s.missing) for sender, s in self._sessions.items() if s.missing}

    def register_cl_ord_id(self, sender_comp_id: str, cl_ord_id: str) -> None:
        self._sessions.setdefault(sender_comp_id, _SessionState(expected_seq=1)).cl_ord_ids.add(cl_ord_id)

    def validate(self, msg: ParsedMessage, *, now: datetime | None = None) -> ValidationResult:
        issues = list(msg.issues)
        # Garbled frames are dropped without consuming MsgSeqNum (FIX session layer rule)
        if msg.is_garbled:
            return ValidationResult(resolve_status(issues), tuple(issues))

        expected: int | None = None
        gap: tuple[int, int] | None = None
        sender, seq = msg.sender_comp_id, msg.msg_seq_num
        if sender and seq is not None:
            expected, gap, seq_issues, proceed = self._check_sequence(msg, sender, seq)
            issues += seq_issues
            if not proceed:
                return ValidationResult(resolve_status(issues), tuple(issues), expected, gap)

        issues += self._check_sending_time(msg, now)
        if msg.msg_type in fd.ORDER_ENTRY_MSG_TYPES and not msg.is_malformed and sender:
            issues += self._check_cl_ord_id(msg, sender)
            issues += self.check_order_terms(msg)
            issues += self.check_risk(msg)
        return ValidationResult(resolve_status(issues), tuple(issues), expected, gap)

    def _check_sequence(self, msg: ParsedMessage, sender: str, seq: int) -> tuple[int | None, tuple[int, int] | None, list[ValidationIssue], bool]:
        state = self._sessions.get(sender)
        if msg.msg_type == "A" and msg.get(141) == "Y":
            state = self._sessions[sender] = _SessionState(expected_seq=1, cl_ord_ids=state.cl_ord_ids if state else set())
        elif state is None:
            # First frame from an unseen session establishes the baseline
            state = self._sessions[sender] = _SessionState(expected_seq=seq)

        if msg.msg_type == "4" and msg.get(123) != "Y":
            # Reset mode ignores MsgSeqNum and abandons any outstanding gap
            new_seq = _maybe(int, msg.get(36)) or state.expected_seq
            state.missing.clear()
            prior, state.expected_seq = state.expected_seq, new_seq
            return prior, None, [ValidationIssue(IC.SEQUENCE_RESET, Severity.INFO, f"SequenceReset: expected {prior} -> {new_seq}", 36)], True

        expected = state.expected_seq
        issues: list[ValidationIssue] = []
        gap: tuple[int, int] | None = None
        if seq == expected:
            state.expected_seq = seq + 1
        elif seq > expected:
            gap = (expected, seq - 1)
            missing = seq - expected
            if missing <= MAX_TRACKED_GAP:
                state.missing.update(range(expected, seq))
            state.expected_seq = seq + 1
            issues.append(
                ValidationIssue(IC.SEQ_GAP, Severity.ERROR, f"MsgSeqNum gap: expected {expected}, received {seq} ({missing} missing: {expected}-{seq - 1})", 34)
            )
        elif seq in state.missing:
            state.missing.discard(seq)
            issues.append(ValidationIssue(IC.GAP_FILLED, Severity.INFO, f"Resent MsgSeqNum {seq} fills an open gap", 34))
        elif msg.poss_dup:
            issues.append(ValidationIssue(IC.POSS_DUP_IGNORED, Severity.WARN, f"PossDup resend of already-processed MsgSeqNum {seq}", 43))
            return expected, None, issues, False
        else:
            issues.append(ValidationIssue(IC.SEQ_TOO_LOW, Severity.ERROR, f"MsgSeqNum too low, expecting {expected} but received {seq}", 34))
            return expected, None, issues, False

        if msg.msg_type == "4" and msg.get(123) == "Y":
            new_seq = _maybe(int, msg.get(36))
            if new_seq is not None and new_seq > seq:
                state.missing.difference_update(range(seq, new_seq))
                state.expected_seq = max(state.expected_seq, new_seq)
                issues.append(ValidationIssue(IC.SEQUENCE_RESET, Severity.INFO, f"GapFill {seq}-{new_seq - 1}", 36))
        return expected, gap, issues, True

    def _check_sending_time(self, msg: ParsedMessage, now: datetime | None) -> list[ValidationIssue]:
        sent = msg.sending_time
        if sent is None or msg.poss_dup:
            return []
        reference = now or self._clock_hwm
        issues: list[ValidationIssue] = []
        if reference is not None:
            lag = reference - sent
            if lag > self.max_clock_skew or (now is not None and -lag > self.max_clock_skew):
                issues.append(
                    ValidationIssue(
                        IC.STALE_SENDING_TIME,
                        Severity.ERROR,
                        f"SendingTime {format_utc_timestamp(sent)} is {abs(int(lag.total_seconds()))}s from reference clock "
                        f"(tolerance {int(self.max_clock_skew.total_seconds())}s)",
                        52,
                    )
                )
        # Without a wall clock, staleness is measured against the stream high-water mark
        if self._clock_hwm is None or sent > self._clock_hwm:
            self._clock_hwm = sent
        return issues

    def _check_cl_ord_id(self, msg: ParsedMessage, sender: str) -> list[ValidationIssue]:
        cl_ord_id = msg.cl_ord_id
        if not cl_ord_id:
            return []
        seen = self._sessions.setdefault(sender, _SessionState(expected_seq=1)).cl_ord_ids
        if cl_ord_id in seen:
            return [ValidationIssue(IC.DUPLICATE_CLORDID, Severity.ERROR, f"ClOrdID {cl_ord_id!r} already used by {sender}", 11)]
        seen.add(cl_ord_id)
        return []

    def check_order_terms(self, msg: ParsedMessage) -> list[ValidationIssue]:
        if msg.msg_type not in fd.ORDER_ENTRY_MSG_TYPES:
            return []
        issues: list[ValidationIssue] = []
        if not SYMBOL_RE.match(msg.symbol or ""):
            issues.append(ValidationIssue(IC.INVALID_SYMBOL, Severity.ERROR, f"Symbol(55)={msg.symbol!r} is not a listed equity symbol format", 55))
        if msg.msg_type == "F":
            return issues
        if msg.side not in SUPPORTED_SIDES:
            issues.append(ValidationIssue(IC.UNSUPPORTED_SIDE, Severity.ERROR, f"Side(54)={msg.side} not accepted for continuous matching", 54))
        if msg.ord_type not in SUPPORTED_ORD_TYPES:
            issues.append(ValidationIssue(IC.UNSUPPORTED_ORD_TYPE, Severity.ERROR, f"OrdType(40)={msg.ord_type} not supported (MARKET, LIMIT only)", 40))
        if msg.time_in_force not in SUPPORTED_TIFS:
            issues.append(ValidationIssue(IC.UNSUPPORTED_TIF, Severity.ERROR, f"TimeInForce(59)={msg.time_in_force} not supported (DAY, GTC, IOC, FOK)", 59))
        qty = msg.order_qty
        if qty is None or qty <= 0:
            issues.append(ValidationIssue(IC.INVALID_QUANTITY, Severity.ERROR, f"OrderQty(38)={msg.get(38)} must be a positive whole number", 38))
        if msg.ord_type == "2" and msg.has(44):
            try:
                if parse_price_cents(msg.get(44) or "") <= 0:
                    issues.append(ValidationIssue(IC.INVALID_PRICE, Severity.ERROR, "Limit price must be positive", 44))
            except TickSizeError as exc:
                issues.append(ValidationIssue(IC.TICK_SIZE_VIOLATION, Severity.ERROR, str(exc), 44))
            except ValueError as exc:
                issues.append(ValidationIssue(IC.INVALID_PRICE, Severity.ERROR, str(exc), 44))
        return issues

    def check_risk(self, msg: ParsedMessage) -> list[ValidationIssue]:
        if msg.msg_type not in ("D", "G"):
            return []
        qty = msg.order_qty
        if qty is None or qty <= 0:
            return []
        limits = self.limits
        issues: list[ValidationIssue] = []
        if qty > limits.max_order_qty:
            issues.append(
                ValidationIssue(IC.QTY_LIMIT_BREACH, Severity.ERROR, f"OrderQty {qty:,} exceeds max {limits.max_order_qty:,} shares", 38)
            )

        reference = self._reference_price(msg.symbol or "")
        price = msg.price_cents if msg.ord_type == "2" else reference
        if price is not None and price * qty > limits.max_notional_cents:
            issues.append(
                ValidationIssue(
                    IC.NOTIONAL_LIMIT_BREACH,
                    Severity.ERROR,
                    f"Notional {format_usd(price * qty)} exceeds max {format_usd(limits.max_notional_cents)}",
                    38,
                )
            )

        limit_px = msg.price_cents if msg.ord_type == "2" else None
        if limit_px is not None and reference:
            deviation = abs(limit_px - reference)
            if deviation * 10_000 > limits.collar_bps * reference:
                issues.append(
                    ValidationIssue(
                        IC.PRICE_COLLAR_BREACH,
                        Severity.ERROR,
                        f"Limit {format_usd(limit_px)} is {format_bps_as_pct(deviation * 10_000 // reference)} from reference "
                        f"{format_usd(reference)} (collar +/-{format_bps_as_pct(limits.collar_bps)})",
                        44,
                    )
                )
        return issues


class OutboundSequencer:
    def __init__(self) -> None:
        self._next: dict[str, int] = {}

    def next(self, target_comp_id: str) -> int:
        seq = self._next.get(target_comp_id, 1)
        self._next[target_comp_id] = seq + 1
        return seq

    def peek(self, target_comp_id: str) -> int:
        return self._next.get(target_comp_id, 1)

    def resume(self, target_comp_id: str, last_sent: int) -> None:
        self._next[target_comp_id] = last_sent + 1


def encode_fix(
    begin_string: str,
    msg_type: str,
    *,
    sender: str,
    target: str,
    seq: int,
    sending_time: datetime,
    body: Iterable[tuple[int, str]],
    header_extra: Sequence[tuple[int, str]] = (),
) -> str:
    header = [(35, msg_type), (49, sender), (56, target), (34, str(seq)), *header_extra, (52, format_utc_timestamp(sending_time))]
    return _frame(begin_string, [*header, *body])


def _frame(begin_string: str, fields: Iterable[tuple[int, str]]) -> str:
    payload = "".join(f"{tag}={value}{SOH}" for tag, value in fields)
    head = f"8={begin_string}{SOH}9={len(_to_bytes(payload))}{SOH}"
    message = head + payload
    return f"{message}10={compute_checksum(_to_bytes(message))}{SOH}"


def amend_message(msg: ParsedMessage, overrides: Mapping[int, str], *, drop: Iterable[int] = ()) -> str:
    if any(f.tag is None for f in msg.fields):
        raise ValueError("cannot amend a frame containing non tag=value tokens")
    dropped = set(drop) | {8, 9, 10}
    pending = {tag: value for tag, value in overrides.items() if tag not in dropped}
    fields: list[tuple[int, str]] = []
    for f in msg.fields:
        if f.tag is None or f.tag in dropped:
            continue
        fields.append((f.tag, pending.pop(f.tag, f.value)))
    fields.extend(pending.items())
    return _frame(msg.begin_string or "FIX.4.2", fields)


def session_reject(
    msg: ParsedMessage,
    issue: ValidationIssue,
    *,
    sender: str,
    seq: int,
    sending_time: datetime,
) -> str:
    body: list[tuple[int, str]] = [(45, str(msg.msg_seq_num or 0))]
    if issue.tag is not None:
        body.append((371, str(issue.tag)))
    if msg.msg_type:
        body.append((372, msg.msg_type))
    body.append((373, SESSION_REJECT_REASON.get(issue.code, "99")))
    body.append((58, issue.detail[:120]))
    return encode_fix(msg.begin_string or "FIX.4.2", "3", sender=sender, target=msg.sender_comp_id or "UNKNOWN", seq=seq, sending_time=sending_time, body=body)


def resend_request(begin_string: str, *, sender: str, target: str, seq: int, sending_time: datetime, begin_seq: int, end_seq: int = 0) -> str:
    # EndSeqNo=0 requests everything from BeginSeqNo onward (4.2+)
    return encode_fix(begin_string, "2", sender=sender, target=target, seq=seq, sending_time=sending_time, body=[(7, str(begin_seq)), (16, str(end_seq))])


def logout(begin_string: str, *, sender: str, target: str, seq: int, sending_time: datetime, text: str) -> str:
    return encode_fix(begin_string, "5", sender=sender, target=target, seq=seq, sending_time=sending_time, body=[(58, text[:120])])
