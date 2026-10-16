from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from backend.config import Settings
from backend.database import make_engine, make_session_factory
from backend.main import create_app
from backend.models import Base
from backend.service import TradingService

REFERENCE_PRICES = {"AAPL": 22850, "MSFT": 43120, "NVDA": 13840}


@pytest.fixture
def db_url(tmp_path: Path) -> str:
    # Set LOB_TEST_DATABASE_URL to run the suite against PostgreSQL
    url = os.getenv("LOB_TEST_DATABASE_URL") or f"sqlite:///{tmp_path / 'lob.db'}"
    engine = make_engine(url)
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    engine.dispose()
    return url


@pytest.fixture
def settings(db_url: str) -> Settings:
    return Settings(database_url=db_url, reference_prices_cents=dict(REFERENCE_PRICES), cors_origins=[])


@pytest.fixture
def service(settings: Settings) -> Iterator[TradingService]:
    engine = make_engine(settings.database_url)
    svc = TradingService(settings, make_session_factory(engine))
    svc.recover()
    yield svc
    engine.dispose()


@pytest.fixture
def client(settings: Settings) -> Iterator[TestClient]:
    with TestClient(create_app(settings)) as test_client:
        yield test_client
