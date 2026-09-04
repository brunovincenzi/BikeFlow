from collections.abc import Iterator

import httpx

from simulator.cli import SimulatorConfig, TelemetryGenerator, send_with_retry


def config(**overrides: object) -> SimulatorConfig:
    values: dict[str, object] = {
        "backend_url": "http://backend:8000",
        "bikes": 2,
        "events_per_second": 10.0,
        "duration_seconds": 1.0,
        "seed": 7,
        "duplicate_probability": 0.0,
        "anomaly_probability": 0.0,
        "max_retries": 2,
        "retry_base_seconds": 0.0,
        "request_timeout_seconds": 1.0,
    }
    values.update(overrides)
    return SimulatorConfig(**values)  # type: ignore[arg-type]


def test_generator_is_reproducible_except_for_uuid_and_timestamp() -> None:
    first = TelemetryGenerator(config())
    second = TelemetryGenerator(config())

    first_event, _ = first.next_event()
    second_event, _ = second.next_event()

    ignored = {"event_id", "recorded_at"}
    assert {k: v for k, v in first_event.items() if k not in ignored} == {
        k: v for k, v in second_event.items() if k not in ignored
    }


def test_retry_reuses_event_id() -> None:
    seen_ids: list[str] = []
    statuses: Iterator[int] = iter((503, 201))

    def handler(request: httpx.Request) -> httpx.Response:
        payload = request.read().decode()
        seen_ids.append(payload)
        status = next(statuses)
        return httpx.Response(status, json={"outcome": "created"})

    generator = TelemetryGenerator(config())
    event, _ = generator.next_event()
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        delivered, outcome = send_with_retry(
            client,
            "http://backend/api/v1/telemetry",
            event,
            config(),
            generator.random,
        )

    assert delivered is True
    assert outcome == "created"
    assert len(seen_ids) == 2
    assert seen_ids[0] == seen_ids[1]
