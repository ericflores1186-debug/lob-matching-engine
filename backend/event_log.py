from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from backend.models import Direction, FixMessage

REPLAY_BATCH = 1_000


class SqlEventLog:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._session_factory = session_factory

    def iter_applied(self, symbol: str) -> Iterator[tuple[int, str]]:
        # Insert order (id) is engine application order; ingest holds the engine lock across the insert
        stmt = (
            select(FixMessage.id, FixMessage.raw_payload)
            .where(
                FixMessage.symbol == symbol,
                FixMessage.engine_applied.is_(True),
                FixMessage.direction == Direction.INBOUND,
            )
            .order_by(FixMessage.id)
            .execution_options(yield_per=REPLAY_BATCH)
        )
        with self._session_factory() as session:
            for row in session.execute(stmt):
                yield row.id, row.raw_payload

    def applied_symbols(self) -> list[str]:
        stmt = (
            select(FixMessage.symbol)
            .where(FixMessage.engine_applied.is_(True), FixMessage.symbol.is_not(None))
            .distinct()
            .order_by(FixMessage.symbol)
        )
        with self._session_factory() as session:
            return [s for s in session.scalars(stmt) if s]
