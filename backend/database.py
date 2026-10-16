from __future__ import annotations

import os
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models import Base

DEFAULT_DATABASE_URL = "postgresql+psycopg://lob:lob@localhost:5432/lob"


def database_url() -> str:
    return os.getenv("LOB_DATABASE_URL", DEFAULT_DATABASE_URL)


def make_engine(url: str | None = None, **kwargs: Any) -> Engine:
    url = url or database_url()
    if url.startswith("sqlite"):
        options: dict[str, Any] = {"connect_args": {"check_same_thread": False}}
        if ":memory:" in url or url in ("sqlite://", "sqlite+pysqlite://"):
            options["poolclass"] = StaticPool
        engine = create_engine(url, **options, **kwargs)

        @event.listens_for(engine, "connect")
        def _enable_foreign_keys(dbapi_connection: Any, _record: Any) -> None:
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        return engine
    return create_engine(url, pool_pre_ping=True, pool_size=10, max_overflow=20, **kwargs)


def make_session_factory(engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(bind=engine, expire_on_commit=False, autoflush=False)


def init_db(engine: Engine) -> None:
    Base.metadata.create_all(engine)


@contextmanager
def transaction(factory: sessionmaker[Session]) -> Iterator[Session]:
    with factory() as session, session.begin():
        yield session
