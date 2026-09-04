from copy import deepcopy
from datetime import UTC

import pytest
from pydantic import ValidationError

from backend.app.models import TelemetryEvent


def test_valid_event_is_normalized_to_utc(event_payload: dict[str, object]) -> None:
    event = TelemetryEvent.model_validate(event_payload)

    assert event.recorded_at.tzinfo == UTC
    assert event.recorded_at.isoformat() == "2026-09-04T08:00:00+00:00"


def test_timestamp_is_canonicalized_to_bson_milliseconds(
    event_payload: dict[str, object],
) -> None:
    payload = deepcopy(event_payload)
    payload["recorded_at"] = "2026-09-04T10:00:00.123456Z"

    event = TelemetryEvent.model_validate(payload)

    assert event.recorded_at.isoformat() == "2026-09-04T10:00:00.123000+00:00"


@pytest.mark.parametrize(
    ("field", "invalid_value"),
    [
        ("latitude", -90.1),
        ("latitude", float("nan")),
        ("latitude", "41.9"),
        ("longitude", 180.1),
        ("battery_pct", -1),
        ("battery_pct", 101),
        ("battery_pct", "50"),
        ("status", "moving"),
        ("bike_id", " bad-bike"),
        ("station_id", ""),
        ("recorded_at", "2026-09-04T10:00:00"),
        ("recorded_at", 1788516000),
    ],
)
def test_invalid_event_is_rejected(
    event_payload: dict[str, object], field: str, invalid_value: object
) -> None:
    payload = deepcopy(event_payload)
    payload[field] = invalid_value

    with pytest.raises(ValidationError):
        TelemetryEvent.model_validate(payload)


def test_unknown_fields_are_rejected(event_payload: dict[str, object]) -> None:
    payload = deepcopy(event_payload)
    payload["unexpected"] = True

    with pytest.raises(ValidationError):
        TelemetryEvent.model_validate(payload)
