from datetime import UTC, datetime
from enum import StrEnum
from typing import Annotated
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

Identifier = Annotated[
    str,
    Field(
        min_length=1,
        max_length=64,
        pattern=r"^[A-Za-z0-9][A-Za-z0-9._:-]{0,63}$",
    ),
]


class TelemetryStatus(StrEnum):
    available = "available"
    in_use = "in_use"
    maintenance = "maintenance"
    offline = "offline"


class TelemetryEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: UUID
    bike_id: Identifier
    station_id: Identifier
    latitude: Annotated[float, Field(strict=True, ge=-90, le=90, allow_inf_nan=False)]
    longitude: Annotated[float, Field(strict=True, ge=-180, le=180, allow_inf_nan=False)]
    battery_pct: Annotated[int, Field(strict=True, ge=0, le=100)]
    status: TelemetryStatus
    recorded_at: AwareDatetime

    @field_validator("bike_id", "station_id")
    @classmethod
    def identifiers_must_not_have_surrounding_whitespace(cls, value: str) -> str:
        if value != value.strip():
            raise ValueError("must not contain surrounding whitespace")
        return value

    @field_validator("recorded_at")
    @classmethod
    def normalize_timestamp_to_utc(cls, value: datetime) -> datetime:
        utc_value = value.astimezone(UTC)
        # BSON datetimes have millisecond precision. Canonicalizing before the
        # idempotency comparison avoids treating a retry as a conflicting payload.
        milliseconds = (utc_value.microsecond // 1_000) * 1_000
        return utc_value.replace(microsecond=milliseconds)

    @field_validator("recorded_at", mode="before")
    @classmethod
    def timestamp_must_be_iso_string_or_database_datetime(cls, value: object) -> object:
        if not isinstance(value, str | datetime):
            raise ValueError("must be an ISO 8601 timestamp")
        return value


class IngestResponse(BaseModel):
    outcome: str = Field(pattern=r"^(created|duplicate)$")
    event: TelemetryEvent


class TelemetryPage(BaseModel):
    items: list[TelemetryEvent]
    limit: int
    offset: int
    has_more: bool


class StationSummary(BaseModel):
    station_id: str
    total_events: int
    unique_bikes: int
    average_battery_pct: float
    latest_recorded_at: datetime
    status_counts: dict[str, int]


class HealthResponse(BaseModel):
    status: str
    mongo: str | None = None
