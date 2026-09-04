import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from uuid import uuid4

import pytest
from pymongo import MongoClient

from backend.app.config import Settings
from backend.app.models import TelemetryEvent
from backend.app.repository import MongoTelemetryRepository

pytestmark = pytest.mark.integration


@pytest.fixture
def mongo_repository() -> MongoTelemetryRepository:
    uri = os.getenv("MONGO_TEST_URI")
    if not uri:
        pytest.skip("MONGO_TEST_URI is not configured")
    database_name = f"bikeflow_test_{uuid4().hex}"
    settings = Settings(mongo_uri=uri, mongo_database=database_name)
    repository = MongoTelemetryRepository(settings)
    repository.ping()
    repository.ensure_indexes()
    try:
        yield repository
    finally:
        repository.close()
        client = MongoClient(uri, serverSelectionTimeoutMS=2_000)
        client.drop_database(database_name)
        client.close()


def event_payload(**overrides: object) -> dict[str, object]:
    payload: dict[str, object] = {
        "event_id": str(uuid4()),
        "bike_id": "bike-integration",
        "station_id": "station-integration",
        "latitude": 41.9,
        "longitude": 12.5,
        "battery_pct": 80,
        "status": "available",
        "recorded_at": "2026-09-04T10:00:00Z",
    }
    payload.update(overrides)
    return payload


def test_real_mongo_insert_idempotency_and_concurrent_unique_index(
    mongo_repository: MongoTelemetryRepository,
) -> None:
    event = TelemetryEvent.model_validate(event_payload(recorded_at="2026-09-04T10:00:00.123456Z"))

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: mongo_repository.insert(event), range(24)))

    assert sum(created for created, _ in results) == 1
    events, has_more = mongo_repository.list_events(limit=100)
    assert len(events) == 1
    assert has_more is False


def test_real_mongo_latest_and_aggregate(mongo_repository: MongoTelemetryRepository) -> None:
    first_payload = event_payload()
    second_payload = deepcopy(first_payload)
    second_payload.update(
        event_id=str(uuid4()),
        bike_id="bike-integration-2",
        battery_pct=60,
        status="maintenance",
        recorded_at="2026-09-04T11:00:00Z",
    )
    first = TelemetryEvent.model_validate(first_payload)
    second = TelemetryEvent.model_validate(second_payload)
    mongo_repository.insert(first)
    mongo_repository.insert(second)

    assert mongo_repository.latest_for_bike("bike-integration-2") == second
    summary = mongo_repository.station_summaries("station-integration")[0]
    assert summary.total_events == 2
    assert summary.unique_bikes == 2
    assert summary.average_battery_pct == 70
    assert summary.status_counts["maintenance"] == 1


def test_replica_set_has_three_members() -> None:
    uri = os.getenv("MONGO_TEST_URI")
    if not uri:
        pytest.skip("MONGO_TEST_URI is not configured")
    client = MongoClient(uri, serverSelectionTimeoutMS=2_000)
    try:
        status = client.admin.command("replSetGetStatus")
    finally:
        client.close()

    assert len(status["members"]) == 3
    assert sum(member["stateStr"] == "PRIMARY" for member in status["members"]) == 1
