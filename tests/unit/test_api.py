from copy import deepcopy

from fastapi.testclient import TestClient

from tests.fakes import InMemoryTelemetryRepository


def test_insert_and_idempotency(client: TestClient, event_payload: dict[str, object]) -> None:
    first = client.post("/api/v1/telemetry", json=event_payload)
    second = client.post("/api/v1/telemetry", json=event_payload)

    assert first.status_code == 201
    assert first.json()["outcome"] == "created"
    assert second.status_code == 200
    assert second.json()["outcome"] == "duplicate"


def test_same_id_with_different_payload_is_a_conflict(
    client: TestClient, event_payload: dict[str, object]
) -> None:
    client.post("/api/v1/telemetry", json=event_payload)
    changed = deepcopy(event_payload)
    changed["battery_pct"] = 1

    response = client.post("/api/v1/telemetry", json=changed)

    assert response.status_code == 409


def test_latest_and_history_are_ordered(
    client: TestClient, event_payload: dict[str, object]
) -> None:
    first = deepcopy(event_payload)
    second = deepcopy(event_payload)
    second["event_id"] = "c161633e-5318-4072-b3e6-fd1fd476e75f"
    second["recorded_at"] = "2026-09-04T09:00:00Z"
    client.post("/api/v1/telemetry", json=first)
    client.post("/api/v1/telemetry", json=second)

    latest = client.get("/api/v1/bikes/bike-0001/latest")
    history = client.get("/api/v1/bikes/bike-0001/history", params={"limit": 1})

    assert latest.status_code == 200
    assert latest.json()["event_id"] == second["event_id"]
    assert history.json()["items"][0]["event_id"] == second["event_id"]
    assert history.json()["has_more"] is True


def test_filters_and_bounded_pagination(
    client: TestClient, event_payload: dict[str, object]
) -> None:
    client.post("/api/v1/telemetry", json=event_payload)

    response = client.get(
        "/api/v1/telemetry",
        params={"status": "available", "station_id": "station-centro", "limit": 10},
    )
    invalid_limit = client.get("/api/v1/telemetry", params={"limit": 101})
    naive_timestamp = client.get(
        "/api/v1/telemetry", params={"recorded_from": "2026-09-04T08:00:00"}
    )

    assert response.status_code == 200
    assert len(response.json()["items"]) == 1
    assert invalid_limit.status_code == 422
    assert naive_timestamp.status_code == 422


def test_station_aggregate(client: TestClient, event_payload: dict[str, object]) -> None:
    second = deepcopy(event_payload)
    second["event_id"] = "69661fd5-c3d5-42bd-aea2-095474418cc9"
    second["bike_id"] = "bike-0002"
    second["battery_pct"] = 63
    second["status"] = "maintenance"
    client.post("/api/v1/telemetry", json=event_payload)
    client.post("/api/v1/telemetry", json=second)

    response = client.get("/api/v1/stations/summary")
    summary = response.json()[0]

    assert response.status_code == 200
    assert summary["total_events"] == 2
    assert summary["unique_bikes"] == 2
    assert summary["average_battery_pct"] == 75
    assert summary["status_counts"]["available"] == 1
    assert summary["status_counts"]["maintenance"] == 1


def test_liveness_and_readiness(
    client: TestClient, repository: InMemoryTelemetryRepository
) -> None:
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").json() == {
        "status": "ready",
        "mongo": "available",
    }

    repository.available = False
    assert client.get("/health/live").status_code == 200
    assert client.get("/health/ready").status_code == 503


def test_missing_bike_returns_404(client: TestClient) -> None:
    response = client.get("/api/v1/bikes/unknown/latest")

    assert response.status_code == 404
