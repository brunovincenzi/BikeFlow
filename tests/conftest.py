from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from backend.app.config import Settings
from backend.app.main import create_app
from tests.fakes import InMemoryTelemetryRepository


@pytest.fixture
def repository() -> InMemoryTelemetryRepository:
    return InMemoryTelemetryRepository()


@pytest.fixture
def client(repository: InMemoryTelemetryRepository) -> Iterator[TestClient]:
    application = create_app(
        settings=Settings(log_level="CRITICAL"),
        repository=repository,
    )
    with TestClient(application) as test_client:
        yield test_client


@pytest.fixture
def event_payload() -> dict[str, object]:
    return {
        "event_id": "d74bc05b-cf9e-4bb2-a8d0-36e9c9e508c8",
        "bike_id": "bike-0001",
        "station_id": "station-centro",
        "latitude": 41.9028,
        "longitude": 12.4964,
        "battery_pct": 87,
        "status": "available",
        "recorded_at": "2026-09-04T10:00:00+02:00",
    }
