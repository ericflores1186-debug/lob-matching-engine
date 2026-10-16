from __future__ import annotations

import enum
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DDL,
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    Table,
    Text,
    UniqueConstraint,
    event,
    false,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

from backend.fix_engine.order_book import ExecType, OrdStatus
from backend.fix_engine.parser import ValidationStatus

JsonDoc = JSON().with_variant(JSONB(), "postgresql")
# SQLite only autoincrements INTEGER PRIMARY KEY; Postgres gets BIGSERIAL
BigPK = BigInteger().with_variant(Integer(), "sqlite")

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Direction(enum.StrEnum):
    INBOUND = "INBOUND"
    OUTBOUND = "OUTBOUND"


class MessageSource(enum.StrEnum):
    GATEWAY = "GATEWAY"
    REMEDIATION = "REMEDIATION"
    ENGINE = "ENGINE"


class RemediationAction(enum.StrEnum):
    REPLAY = "REPLAY"
    AMEND_AND_REPLAY = "AMEND_AND_REPLAY"
    CANCEL_REPLACE = "CANCEL_REPLACE"


class RemediationStatus(enum.StrEnum):
    PENDING_APPROVAL = "PENDING_APPROVAL"
    EXECUTED = "EXECUTED"
    DECLINED = "DECLINED"
    FAILED = "FAILED"


def _enum(cls: type[enum.Enum], name: str) -> Enum:
    return Enum(
        cls,
        name=name,
        native_enum=False,
        create_constraint=True,
        length=max(len(m.value) for m in cls),
        values_callable=lambda members: [m.value for m in members],
        validate_strings=True,
    )


class FixMessage(Base):
    __tablename__ = "fix_messages"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    direction: Mapped[Direction] = mapped_column(_enum(Direction, "direction"))
    source: Mapped[MessageSource] = mapped_column(_enum(MessageSource, "source"))
    begin_string: Mapped[str | None] = mapped_column(String(16))
    msg_type: Mapped[str | None] = mapped_column(String(4))
    sender_comp_id: Mapped[str | None] = mapped_column(String(64))
    target_comp_id: Mapped[str | None] = mapped_column(String(64))
    msg_seq_num: Mapped[int | None] = mapped_column(BigInteger)
    sending_time: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    cl_ord_id: Mapped[str | None] = mapped_column(String(64))
    orig_cl_ord_id: Mapped[str | None] = mapped_column(String(64))
    symbol: Mapped[str | None] = mapped_column(String(32))
    raw_payload: Mapped[str] = mapped_column(Text)
    parsed_tags: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    validation_status: Mapped[ValidationStatus] = mapped_column(_enum(ValidationStatus, "validation_status"))
    validation_issues: Mapped[list[dict[str, Any]]] = mapped_column(JsonDoc, default=list)
    engine_applied: Mapped[bool] = mapped_column(Boolean(create_constraint=False), default=False, server_default=false())
    ingest_key: Mapped[str | None] = mapped_column(String(128))
    replay_of_id: Mapped[int | None] = mapped_column(ForeignKey("fix_messages.id"))

    replay_of: Mapped[FixMessage | None] = relationship(remote_side="FixMessage.id")

    __table_args__ = (
        Index("ix_fix_messages_session_seq", "sender_comp_id", "msg_seq_num"),
        Index("ix_fix_messages_session_clordid", "sender_comp_id", "cl_ord_id"),
        Index("ix_fix_messages_status", "validation_status", "id"),
        Index("ix_fix_messages_ingest_key", "ingest_key"),
        # Replay path: WHERE symbol = ? AND engine_applied ORDER BY id
        Index(
            "ix_fix_messages_replay",
            "symbol",
            "id",
            postgresql_where=text("engine_applied"),
            sqlite_where=text("engine_applied"),
        ),
        CheckConstraint("NOT engine_applied OR direction = 'INBOUND'", name="applied_inbound_only"),
        CheckConstraint("msg_seq_num IS NULL OR msg_seq_num > 0", name="seq_positive"),
    )


class OrderRecord(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    order_id: Mapped[str] = mapped_column(String(48))
    version: Mapped[int] = mapped_column(Integer)
    cl_ord_id: Mapped[str] = mapped_column(String(64))
    orig_cl_ord_id: Mapped[str | None] = mapped_column(String(64))
    sender_comp_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(1))
    ord_type: Mapped[str] = mapped_column(String(1))
    time_in_force: Mapped[str] = mapped_column(String(1))
    price_cents: Mapped[int | None] = mapped_column(BigInteger)
    quantity: Mapped[int] = mapped_column(BigInteger)
    account: Mapped[str | None] = mapped_column(String(64))
    transact_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_message_id: Mapped[int] = mapped_column(ForeignKey("fix_messages.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("order_id", "version"),
        Index("ix_orders_session_clordid", "sender_comp_id", "cl_ord_id"),
        Index("ix_orders_symbol", "symbol", "id"),
        CheckConstraint("quantity > 0", name="quantity_positive"),
        CheckConstraint("price_cents IS NULL OR price_cents > 0", name="price_positive"),
        CheckConstraint("version >= 1", name="version_positive"),
        CheckConstraint("ord_type <> '2' OR price_cents IS NOT NULL", name="limit_has_price"),
    )


class ExecutionRecord(Base):
    __tablename__ = "executions"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    exec_id: Mapped[str] = mapped_column(String(48), unique=True)
    order_id: Mapped[str] = mapped_column(String(48))
    cl_ord_id: Mapped[str] = mapped_column(String(64))
    orig_cl_ord_id: Mapped[str | None] = mapped_column(String(64))
    sender_comp_id: Mapped[str] = mapped_column(String(64))
    symbol: Mapped[str] = mapped_column(String(32))
    side: Mapped[str] = mapped_column(String(1))
    exec_type: Mapped[ExecType] = mapped_column(_enum(ExecType, "exec_type"))
    ord_status: Mapped[OrdStatus] = mapped_column(_enum(OrdStatus, "ord_status"))
    price_cents: Mapped[int | None] = mapped_column(BigInteger)
    order_qty: Mapped[int] = mapped_column(BigInteger)
    last_qty: Mapped[int] = mapped_column(BigInteger, default=0)
    last_px_cents: Mapped[int | None] = mapped_column(BigInteger)
    leaves_qty: Mapped[int] = mapped_column(BigInteger)
    cum_qty: Mapped[int] = mapped_column(BigInteger)
    cum_notional_cents: Mapped[int] = mapped_column(BigInteger)
    match_id: Mapped[str | None] = mapped_column(String(48))
    contra_order_id: Mapped[str | None] = mapped_column(String(48))
    ord_rej_reason: Mapped[str | None] = mapped_column(String(4))
    text: Mapped[str | None] = mapped_column(String(255))
    transact_time: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    source_message_id: Mapped[int] = mapped_column(ForeignKey("fix_messages.id"))
    outbound_message_id: Mapped[int | None] = mapped_column(ForeignKey("fix_messages.id"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        Index("ix_executions_order", "order_id", "id"),
        Index("ix_executions_symbol", "symbol", "id"),
        Index("ix_executions_match", "match_id"),
        CheckConstraint("last_qty >= 0 AND leaves_qty >= 0 AND cum_qty >= 0 AND cum_notional_cents >= 0", name="non_negative"),
        CheckConstraint("exec_type <> 'TRADE' OR (last_qty > 0 AND last_px_cents > 0)", name="trade_has_fill"),
        CheckConstraint("cum_qty + leaves_qty <= order_qty OR exec_type = 'REJECTED'", name="qty_conservation"),
    )


class RemediationRequest(Base):
    __tablename__ = "remediation_requests"

    id: Mapped[int] = mapped_column(BigPK, primary_key=True, autoincrement=True)
    exception_message_id: Mapped[int] = mapped_column(ForeignKey("fix_messages.id"))
    action: Mapped[RemediationAction] = mapped_column(_enum(RemediationAction, "action"))
    status: Mapped[RemediationStatus] = mapped_column(
        _enum(RemediationStatus, "status"),
        default=RemediationStatus.PENDING_APPROVAL,
        server_default=RemediationStatus.PENDING_APPROVAL.value,
    )
    # Checker signs off on these exact bytes; hash is re-verified at approval
    staged_payload: Mapped[str] = mapped_column(Text)
    staged_payload_sha256: Mapped[str] = mapped_column(String(64))
    overrides: Mapped[dict[str, str]] = mapped_column(JsonDoc, default=dict)
    bypass_risk_checks: Mapped[bool] = mapped_column(Boolean(create_constraint=False), default=False, server_default=false())
    reason: Mapped[str] = mapped_column(Text)
    maker_id: Mapped[str] = mapped_column(String(64))
    checker_id: Mapped[str | None] = mapped_column(String(64))
    checker_comment: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    decided_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    result_message_id: Mapped[int | None] = mapped_column(ForeignKey("fix_messages.id"))
    result: Mapped[dict[str, Any] | None] = mapped_column(JsonDoc)

    exception_message: Mapped[FixMessage] = relationship(foreign_keys=[exception_message_id])
    result_message: Mapped[FixMessage | None] = relationship(foreign_keys=[result_message_id])

    __table_args__ = (
        CheckConstraint("checker_id IS NULL OR lower(checker_id) <> lower(maker_id)", name="four_eyes"),
        CheckConstraint("(status = 'PENDING_APPROVAL') = (checker_id IS NULL)", name="checker_iff_decided"),
        CheckConstraint("(status = 'PENDING_APPROVAL') = (decided_at IS NULL)", name="decided_at_iff_decided"),
        CheckConstraint("length(trim(reason)) > 0", name="reason_required"),
        Index(
            "uq_remediation_one_pending",
            "exception_message_id",
            unique=True,
            postgresql_where=text("status = 'PENDING_APPROVAL'"),
            sqlite_where=text("status = 'PENDING_APPROVAL'"),
        ),
        Index("ix_remediation_status", "status", "id"),
    )


class IngestRequest(Base):
    __tablename__ = "ingest_requests"

    idempotency_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    request_sha256: Mapped[str] = mapped_column(String(64))
    message_count: Mapped[int] = mapped_column(Integer)
    response: Mapped[dict[str, Any]] = mapped_column(JsonDoc)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())


APPEND_ONLY_TABLES: tuple[Table, ...] = tuple(Base.metadata.tables[name] for name in ("fix_messages", "orders", "executions"))

_PG_FORBID_MUTATION = DDL(
    """
    CREATE OR REPLACE FUNCTION lob_forbid_mutation() RETURNS trigger
    LANGUAGE plpgsql AS $$
    BEGIN
        RAISE EXCEPTION USING
            ERRCODE = 'restrict_violation',
            MESSAGE = TG_TABLE_NAME || ' is append-only: ' || TG_OP || ' rejected';
    END;
    $$
    """
)


def _install_append_only_guards(table: Table) -> None:
    name = table.name
    event.listen(table, "after_create", _PG_FORBID_MUTATION.execute_if(dialect="postgresql"))
    event.listen(
        table,
        "after_create",
        DDL(
            f"CREATE TRIGGER {name}_append_only BEFORE UPDATE OR DELETE ON {name} "
            "FOR EACH ROW EXECUTE FUNCTION lob_forbid_mutation()"
        ).execute_if(dialect="postgresql"),
    )
    event.listen(
        table,
        "after_create",
        DDL(
            f"CREATE TRIGGER {name}_no_truncate BEFORE TRUNCATE ON {name} "
            "FOR EACH STATEMENT EXECUTE FUNCTION lob_forbid_mutation()"
        ).execute_if(dialect="postgresql"),
    )
    for op in ("UPDATE", "DELETE"):
        event.listen(
            table,
            "after_create",
            DDL(
                f"CREATE TRIGGER {name}_no_{op.lower()} BEFORE {op} ON {name} "
                f"BEGIN SELECT RAISE(ABORT, '{name} is append-only: {op} rejected'); END"
            ).execute_if(dialect="sqlite"),
        )


for _table in APPEND_ONLY_TABLES:
    _install_append_only_guards(_table)
