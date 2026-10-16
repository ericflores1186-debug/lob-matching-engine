from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

from backend.models import RemediationAction, RemediationStatus

OPERATOR_ID_PATTERN = r"^[A-Za-z0-9._@-]{2,64}$"
# Session identity and framing are never operator-editable
PROTECTED_TAGS = frozenset({8, 9, 10, 34, 35, 49, 56})


class PayloadRequest(BaseModel):
    payload: str = Field(min_length=1)


class RemediationCreate(BaseModel):
    exception_message_id: int = Field(gt=0)
    action: RemediationAction
    maker_id: str = Field(pattern=OPERATOR_ID_PATTERN)
    reason: str = Field(min_length=5, max_length=2_000)
    overrides: dict[int, str] = Field(default_factory=dict)
    bypass_risk_checks: bool = False

    @field_validator("overrides")
    @classmethod
    def _check_overrides(cls, value: dict[int, str]) -> dict[int, str]:
        for tag, tag_value in value.items():
            if tag <= 0:
                raise ValueError(f"tag {tag} is not a valid FIX tag number")
            if tag in PROTECTED_TAGS:
                raise ValueError(f"tag {tag} is session-controlled and cannot be overridden")
            if not tag_value or any(ch in tag_value for ch in ("\x01", "|", "=")):
                raise ValueError(f"tag {tag} value must be non-empty and contain no delimiters")
        return value


class RemediationDecision(BaseModel):
    checker_id: str = Field(pattern=OPERATOR_ID_PATTERN)
    comment: str | None = Field(default=None, max_length=2_000)


class IssueOut(BaseModel):
    code: str
    severity: str
    status: str
    tag: int | None = None
    detail: str


class IngestedMessage(BaseModel):
    id: int
    msg_seq_num: int | None
    sender_comp_id: str | None
    msg_type: str | None
    msg_type_name: str | None
    cl_ord_id: str | None
    symbol: str | None
    status: str
    applied: bool
    issues: list[IssueOut]
    responses: list[str]


class IngestResponse(BaseModel):
    idempotency_key: str
    replayed: bool
    received: int
    applied: int
    exceptions: int
    executions: int
    trades: int
    messages: list[IngestedMessage]


class RebuildResponse(BaseModel):
    symbol: str
    messages_replayed: int
    checksum: str
    live_checksum: str
    matches_live: bool
    elapsed_ms: int
    book: dict[str, Any]


class RemediationOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    exception_message_id: int
    action: RemediationAction
    status: RemediationStatus
    staged_payload: str
    staged_payload_sha256: str
    overrides: dict[str, str]
    bypass_risk_checks: bool
    reason: str
    maker_id: str
    checker_id: str | None
    checker_comment: str | None
    created_at: datetime | None
    decided_at: datetime | None
    result_message_id: int | None
    result: dict[str, Any] | None
    preview: list[IssueOut] = []
