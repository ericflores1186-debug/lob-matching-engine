from __future__ import annotations

from enum import StrEnum


class FieldType(StrEnum):
    STRING = "STRING"
    CHAR = "CHAR"
    INT = "INT"
    SEQNUM = "SEQNUM"
    LENGTH = "LENGTH"
    QTY = "QTY"
    PRICE = "PRICE"
    UTCTIMESTAMP = "UTCTIMESTAMP"
    LOCALMKTDATE = "LOCALMKTDATE"
    BOOLEAN = "BOOLEAN"
    CHECKSUM = "CHECKSUM"


T = FieldType

FIELDS: dict[int, tuple[str, FieldType]] = {
    1: ("Account", T.STRING),
    6: ("AvgPx", T.PRICE),
    7: ("BeginSeqNo", T.SEQNUM),
    8: ("BeginString", T.STRING),
    9: ("BodyLength", T.LENGTH),
    10: ("CheckSum", T.CHECKSUM),
    11: ("ClOrdID", T.STRING),
    14: ("CumQty", T.QTY),
    15: ("Currency", T.STRING),
    16: ("EndSeqNo", T.SEQNUM),
    17: ("ExecID", T.STRING),
    18: ("ExecInst", T.STRING),
    20: ("ExecTransType", T.CHAR),
    21: ("HandlInst", T.CHAR),
    22: ("IDSource", T.STRING),
    30: ("LastMkt", T.STRING),
    31: ("LastPx", T.PRICE),
    32: ("LastShares", T.QTY),
    34: ("MsgSeqNum", T.SEQNUM),
    35: ("MsgType", T.STRING),
    36: ("NewSeqNo", T.SEQNUM),
    37: ("OrderID", T.STRING),
    38: ("OrderQty", T.QTY),
    39: ("OrdStatus", T.CHAR),
    40: ("OrdType", T.CHAR),
    41: ("OrigClOrdID", T.STRING),
    43: ("PossDupFlag", T.BOOLEAN),
    44: ("Price", T.PRICE),
    45: ("RefSeqNum", T.SEQNUM),
    47: ("Rule80A", T.CHAR),
    48: ("SecurityID", T.STRING),
    49: ("SenderCompID", T.STRING),
    50: ("SenderSubID", T.STRING),
    52: ("SendingTime", T.UTCTIMESTAMP),
    54: ("Side", T.CHAR),
    55: ("Symbol", T.STRING),
    56: ("TargetCompID", T.STRING),
    57: ("TargetSubID", T.STRING),
    58: ("Text", T.STRING),
    59: ("TimeInForce", T.CHAR),
    60: ("TransactTime", T.UTCTIMESTAMP),
    63: ("SettlmntTyp", T.CHAR),
    64: ("FutSettDate", T.LOCALMKTDATE),
    75: ("TradeDate", T.LOCALMKTDATE),
    76: ("ExecBroker", T.STRING),
    97: ("PossResend", T.BOOLEAN),
    98: ("EncryptMethod", T.INT),
    99: ("StopPx", T.PRICE),
    100: ("ExDestination", T.STRING),
    102: ("CxlRejReason", T.INT),
    103: ("OrdRejReason", T.INT),
    108: ("HeartBtInt", T.INT),
    109: ("ClientID", T.STRING),
    110: ("MinQty", T.QTY),
    111: ("MaxFloor", T.QTY),
    112: ("TestReqID", T.STRING),
    115: ("OnBehalfOfCompID", T.STRING),
    122: ("OrigSendingTime", T.UTCTIMESTAMP),
    123: ("GapFillFlag", T.BOOLEAN),
    126: ("ExpireTime", T.UTCTIMESTAMP),
    128: ("DeliverToCompID", T.STRING),
    141: ("ResetSeqNumFlag", T.BOOLEAN),
    150: ("ExecType", T.CHAR),
    151: ("LeavesQty", T.QTY),
    167: ("SecurityType", T.STRING),
    207: ("SecurityExchange", T.STRING),
    371: ("RefTagID", T.INT),
    372: ("RefMsgType", T.STRING),
    373: ("SessionRejectReason", T.INT),
    375: ("ContraBroker", T.STRING),
    376: ("ComplianceID", T.STRING),
    432: ("ExpireDate", T.LOCALMKTDATE),
    434: ("CxlRejResponseTo", T.CHAR),
    440: ("ClearingAccount", T.STRING),
    528: ("OrderCapacity", T.CHAR),
    529: ("OrderRestrictions", T.STRING),
}

# Fields renamed between 4.2 and 4.4
FIELD_NAME_OVERRIDES: dict[str, dict[int, str]] = {
    "FIX.4.4": {22: "SecurityIDSource", 32: "LastQty", 63: "SettlType", 64: "SettlDate"},
}

MSG_TYPES: dict[str, tuple[str, str]] = {
    "0": ("HEARTBEAT", "Heartbeat"),
    "1": ("TEST_REQUEST", "Test Request"),
    "2": ("RESEND_REQUEST", "Resend Request"),
    "3": ("REJECT", "Session-level Reject"),
    "4": ("SEQUENCE_RESET", "Sequence Reset"),
    "5": ("LOGOUT", "Logout"),
    "8": ("EXECUTION_REPORT", "Execution Report"),
    "9": ("ORDER_CANCEL_REJECT", "Order Cancel Reject"),
    "A": ("LOGON", "Logon"),
    "D": ("NEW_ORDER_SINGLE", "New Order - Single"),
    "F": ("ORDER_CANCEL_REQUEST", "Order Cancel Request"),
    "G": ("ORDER_CANCEL_REPLACE_REQUEST", "Order Cancel/Replace Request"),
    "H": ("ORDER_STATUS_REQUEST", "Order Status Request"),
    "j": ("BUSINESS_MESSAGE_REJECT", "Business Message Reject"),
}

ENUMS: dict[int, dict[str, tuple[str, str]]] = {
    20: {
        "0": ("NEW", "New"),
        "1": ("CANCEL", "Cancel"),
        "2": ("CORRECT", "Correct"),
        "3": ("STATUS", "Status"),
    },
    21: {
        "1": ("AUTOMATED_PRIVATE", "Automated execution, no broker intervention"),
        "2": ("AUTOMATED_PUBLIC", "Automated execution, broker intervention OK"),
        "3": ("MANUAL", "Manual order, best execution"),
    },
    22: {
        "1": ("CUSIP", "CUSIP"),
        "2": ("SEDOL", "SEDOL"),
        "4": ("ISIN", "ISIN"),
        "5": ("RIC", "RIC code"),
        "8": ("EXCHANGE_SYMBOL", "Exchange symbol"),
    },
    39: {
        "0": ("NEW", "New"),
        "1": ("PARTIALLY_FILLED", "Partially filled"),
        "2": ("FILLED", "Filled"),
        "3": ("DONE_FOR_DAY", "Done for day"),
        "4": ("CANCELED", "Canceled"),
        "5": ("REPLACED", "Replaced (FIX 4.2)"),
        "6": ("PENDING_CANCEL", "Pending cancel"),
        "7": ("STOPPED", "Stopped"),
        "8": ("REJECTED", "Rejected"),
        "9": ("SUSPENDED", "Suspended"),
        "A": ("PENDING_NEW", "Pending new"),
        "B": ("CALCULATED", "Calculated"),
        "C": ("EXPIRED", "Expired"),
        "D": ("ACCEPTED_FOR_BIDDING", "Accepted for bidding"),
        "E": ("PENDING_REPLACE", "Pending replace"),
    },
    40: {
        "1": ("MARKET", "Market"),
        "2": ("LIMIT", "Limit"),
        "3": ("STOP", "Stop"),
        "4": ("STOP_LIMIT", "Stop limit"),
        "5": ("MARKET_ON_CLOSE", "Market on close"),
        "6": ("WITH_OR_WITHOUT", "With or without"),
        "7": ("LIMIT_OR_BETTER", "Limit or better"),
        "8": ("LIMIT_WITH_OR_WITHOUT", "Limit with or without"),
        "9": ("ON_BASIS", "On basis"),
        "A": ("ON_CLOSE", "On close"),
        "B": ("LIMIT_ON_CLOSE", "Limit on close"),
        "C": ("FOREX_MARKET", "Forex - market"),
        "D": ("PREVIOUSLY_QUOTED", "Previously quoted"),
        "E": ("PREVIOUSLY_INDICATED", "Previously indicated"),
        "F": ("FOREX_LIMIT", "Forex - limit"),
        "G": ("FOREX_SWAP", "Forex - swap"),
        "H": ("FOREX_PREVIOUSLY_QUOTED", "Forex - previously quoted"),
        "I": ("FUNARI", "Funari (limit day order, unexecuted portion MOC)"),
        "J": ("MARKET_IF_TOUCHED", "Market if touched"),
        "K": ("MARKET_WITH_LEFTOVER_AS_LIMIT", "Market with leftover as limit"),
        "L": ("PREVIOUS_FUND_VALUATION_POINT", "Previous fund valuation point"),
        "M": ("NEXT_FUND_VALUATION_POINT", "Next fund valuation point"),
        "P": ("PEGGED", "Pegged"),
    },
    47: {
        "A": ("AGENCY_SINGLE", "Agency single order"),
        "I": ("INDIVIDUAL", "Individual investor, single order"),
        "P": ("PRINCIPAL", "Principal"),
        "W": ("AGENT_FOR_OTHER_MEMBER", "Agent for other member"),
    },
    54: {
        "1": ("BUY", "Buy"),
        "2": ("SELL", "Sell"),
        "3": ("BUY_MINUS", "Buy minus"),
        "4": ("SELL_PLUS", "Sell plus"),
        "5": ("SELL_SHORT", "Sell short"),
        "6": ("SELL_SHORT_EXEMPT", "Sell short exempt"),
        "7": ("UNDISCLOSED", "Undisclosed"),
        "8": ("CROSS", "Cross"),
        "9": ("CROSS_SHORT", "Cross short"),
    },
    59: {
        "0": ("DAY", "Day"),
        "1": ("GTC", "Good till cancel"),
        "2": ("OPG", "At the opening"),
        "3": ("IOC", "Immediate or cancel"),
        "4": ("FOK", "Fill or kill"),
        "5": ("GTX", "Good till crossing"),
        "6": ("GTD", "Good till date"),
        "7": ("AT_THE_CLOSE", "At the close"),
    },
    98: {"0": ("NONE", "None / other")},
    102: {
        "0": ("TOO_LATE_TO_CANCEL", "Too late to cancel"),
        "1": ("UNKNOWN_ORDER", "Unknown order"),
        "2": ("BROKER_OPTION", "Broker / exchange option"),
        "3": ("ALREADY_PENDING", "Order already pending cancel or replace"),
        "6": ("DUPLICATE_CLORDID", "Duplicate ClOrdID received"),
        "99": ("OTHER", "Other"),
    },
    103: {
        "0": ("BROKER_OPTION", "Broker / exchange option"),
        "1": ("UNKNOWN_SYMBOL", "Unknown symbol"),
        "2": ("EXCHANGE_CLOSED", "Exchange closed"),
        "3": ("ORDER_EXCEEDS_LIMIT", "Order exceeds limit"),
        "4": ("TOO_LATE_TO_ENTER", "Too late to enter"),
        "5": ("UNKNOWN_ORDER", "Unknown order"),
        "6": ("DUPLICATE_ORDER", "Duplicate order (duplicate ClOrdID)"),
        "7": ("DUPLICATE_VERBAL", "Duplicate of a verbally communicated order"),
        "8": ("STALE_ORDER", "Stale order"),
        "9": ("TRADE_ALONG_REQUIRED", "Trade along required"),
        "10": ("INVALID_INVESTOR_ID", "Invalid investor ID"),
        "11": ("UNSUPPORTED_ORDER_CHARACTERISTIC", "Unsupported order characteristic"),
        "12": ("SURVEILLANCE_OPTION", "Surveillance option"),
        "13": ("INCORRECT_QUANTITY", "Incorrect quantity"),
        "14": ("INCORRECT_ALLOCATED_QUANTITY", "Incorrect allocated quantity"),
        "15": ("UNKNOWN_ACCOUNT", "Unknown account(s)"),
        "99": ("OTHER", "Other"),
    },
    150: {
        "0": ("NEW", "New"),
        "1": ("PARTIAL_FILL", "Partial fill (FIX 4.2)"),
        "2": ("FILL", "Fill (FIX 4.2)"),
        "3": ("DONE_FOR_DAY", "Done for day"),
        "4": ("CANCELED", "Canceled"),
        "5": ("REPLACED", "Replaced"),
        "6": ("PENDING_CANCEL", "Pending cancel"),
        "7": ("STOPPED", "Stopped"),
        "8": ("REJECTED", "Rejected"),
        "9": ("SUSPENDED", "Suspended"),
        "A": ("PENDING_NEW", "Pending new"),
        "B": ("CALCULATED", "Calculated"),
        "C": ("EXPIRED", "Expired"),
        "D": ("RESTATED", "Restated"),
        "E": ("PENDING_REPLACE", "Pending replace"),
        "F": ("TRADE", "Trade - partial or full fill (FIX 4.4)"),
        "G": ("TRADE_CORRECT", "Trade correct"),
        "H": ("TRADE_CANCEL", "Trade cancel"),
        "I": ("ORDER_STATUS", "Order status"),
    },
    167: {
        "CS": ("COMMON_STOCK", "Common stock"),
        "PS": ("PREFERRED_STOCK", "Preferred stock"),
        "WAR": ("WARRANT", "Warrant"),
    },
    373: {
        "0": ("INVALID_TAG_NUMBER", "Invalid tag number"),
        "1": ("REQUIRED_TAG_MISSING", "Required tag missing"),
        "2": ("TAG_NOT_DEFINED_FOR_MSG_TYPE", "Tag not defined for this message type"),
        "3": ("UNDEFINED_TAG", "Undefined tag"),
        "4": ("TAG_WITHOUT_VALUE", "Tag specified without a value"),
        "5": ("VALUE_OUT_OF_RANGE", "Value is incorrect (out of range) for this tag"),
        "6": ("INCORRECT_DATA_FORMAT", "Incorrect data format for value"),
        "7": ("DECRYPTION_PROBLEM", "Decryption problem"),
        "8": ("SIGNATURE_PROBLEM", "Signature problem"),
        "9": ("COMPID_PROBLEM", "CompID problem"),
        "10": ("SENDING_TIME_ACCURACY", "SendingTime accuracy problem"),
        "11": ("INVALID_MSG_TYPE", "Invalid MsgType"),
        "12": ("XML_VALIDATION_ERROR", "XML validation error"),
        "13": ("TAG_REPEATED", "Tag appears more than once"),
        "14": ("TAG_OUT_OF_ORDER", "Tag specified out of required order"),
        "15": ("GROUP_OUT_OF_ORDER", "Repeating group fields out of order"),
        "16": ("INCORRECT_NUM_IN_GROUP", "Incorrect NumInGroup count for repeating group"),
        "17": ("NON_DATA_VALUE_HAS_DELIMITER", "Non-data value includes field delimiter"),
        "99": ("OTHER", "Other"),
    },
    434: {
        "1": ("ORDER_CANCEL_REQUEST", "Response to Order Cancel Request (35=F)"),
        "2": ("ORDER_CANCEL_REPLACE_REQUEST", "Response to Cancel/Replace Request (35=G)"),
    },
    528: {
        "A": ("AGENCY", "Agency"),
        "G": ("PROPRIETARY", "Proprietary"),
        "I": ("INDIVIDUAL", "Individual"),
        "P": ("PRINCIPAL", "Principal"),
        "R": ("RISKLESS_PRINCIPAL", "Riskless principal"),
        "W": ("AGENT_FOR_OTHER_MEMBER", "Agent for other member"),
    },
}
ENUMS[35] = MSG_TYPES

# Enumerations complete enough to reject unknown values with 373=5
STRICT_ENUM_TAGS: frozenset[int] = frozenset({20, 21, 39, 40, 54, 59, 150})

HEADER_REQUIRED: tuple[int, ...] = (49, 56, 34, 52)

REQUIRED_BY_MSG_TYPE: dict[str, tuple[int, ...]] = {
    "D": (11, 55, 54, 38, 40, 60),
    "F": (41, 11, 55, 54, 60),
    "G": (41, 11, 55, 54, 38, 40, 60),
    "8": (37, 17, 150, 39, 55, 54, 151, 14, 6),
    "9": (37, 11, 41, 39, 434),
    "3": (45,),
    "2": (7, 16),
    "4": (36,),
    "A": (98, 108),
    "1": (112,),
}

# 4.2 ExecutionReport also carries ExecTransType
REQUIRED_BY_VERSION: dict[tuple[str, str], tuple[int, ...]] = {
    ("FIX.4.2", "8"): (20,),
}

ORDER_ENTRY_MSG_TYPES: frozenset[str] = frozenset({"D", "F", "G"})
SESSION_MSG_TYPES: frozenset[str] = frozenset({"0", "1", "2", "3", "4", "5", "A"})


def field_name(tag: int, begin_string: str | None = None) -> str:
    if begin_string:
        override = FIELD_NAME_OVERRIDES.get(begin_string, {}).get(tag)
        if override:
            return override
    entry = FIELDS.get(tag)
    return entry[0] if entry else f"Tag{tag}"


def field_type(tag: int) -> FieldType:
    entry = FIELDS.get(tag)
    return entry[1] if entry else FieldType.STRING


def enum_code(tag: int, value: str) -> str | None:
    entry = ENUMS.get(tag, {}).get(value)
    return entry[0] if entry else None


def enum_description(tag: int, value: str) -> str | None:
    entry = ENUMS.get(tag, {}).get(value)
    return entry[1] if entry else None
