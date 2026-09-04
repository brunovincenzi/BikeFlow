import argparse
import logging
import math
import os
import random
import time
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from backend.app.logging import configure_logging

logger = logging.getLogger("bikeflow.simulator")

STATIONS = (
    ("station-centro", 41.9028, 12.4964),
    ("station-nord", 41.9305, 12.5003),
    ("station-sud", 41.8719, 12.4802),
    ("station-est", 41.8955, 12.5510),
    ("station-ovest", 41.9009, 12.4311),
)


@dataclass(frozen=True)
class SimulatorConfig:
    backend_url: str
    bikes: int
    events_per_second: float
    duration_seconds: float
    seed: int
    duplicate_probability: float
    anomaly_probability: float
    max_retries: int
    retry_base_seconds: float
    request_timeout_seconds: float


class TelemetryGenerator:
    def __init__(self, config: SimulatorConfig):
        self.config = config
        self.random = random.Random(config.seed)
        self.batteries = {
            f"bike-{number:04d}": self.random.randint(55, 100)
            for number in range(1, config.bikes + 1)
        }
        self.recent_events: deque[dict[str, Any]] = deque(maxlen=max(10, config.bikes * 2))

    def next_event(self) -> tuple[dict[str, Any], bool]:
        if self.recent_events and self.random.random() < self.config.duplicate_probability:
            # A shallow copy prevents accidental mutation while preserving event_id.
            return dict(self.random.choice(tuple(self.recent_events))), True

        bike_id = self.random.choice(tuple(self.batteries))
        station_id, base_latitude, base_longitude = self.random.choice(STATIONS)
        anomaly = self.random.random() < self.config.anomaly_probability
        if anomaly:
            status = self.random.choice(("maintenance", "offline"))
            battery_drop = self.random.randint(1, 4)
        else:
            status = self.random.choices(("available", "in_use"), weights=(0.65, 0.35), k=1)[0]
            battery_drop = self.random.choice((0, 0, 0, 1))

        self.batteries[bike_id] = max(0, self.batteries[bike_id] - battery_drop)
        event: dict[str, Any] = {
            "event_id": str(uuid4()),
            "bike_id": bike_id,
            "station_id": station_id,
            "latitude": round(base_latitude + self.random.uniform(-0.002, 0.002), 6),
            "longitude": round(base_longitude + self.random.uniform(-0.002, 0.002), 6),
            "battery_pct": self.batteries[bike_id],
            "status": status,
            "recorded_at": datetime.now(UTC).isoformat(),
        }
        self.recent_events.append(event)
        return event, False


def send_with_retry(
    client: httpx.Client,
    endpoint: str,
    event: dict[str, Any],
    config: SimulatorConfig,
    random_source: random.Random,
) -> tuple[bool, str]:
    """Send one immutable event; retries always reuse its event_id and payload."""

    for attempt in range(config.max_retries + 1):
        try:
            response = client.post(endpoint, json=event)
            if response.status_code in (200, 201):
                return True, response.json().get("outcome", "accepted")
            if response.status_code < 500:
                logger.error(
                    "Event rejected without retry",
                    extra={"event_id": event["event_id"], "status_code": response.status_code},
                )
                return False, f"http_{response.status_code}"
            reason = f"http_{response.status_code}"
        except httpx.RequestError as exc:
            reason = type(exc).__name__

        if attempt == config.max_retries:
            break
        exponential = config.retry_base_seconds * (2**attempt)
        jitter = random_source.uniform(0, config.retry_base_seconds)
        delay = min(10.0, exponential + jitter)
        logger.warning(
            "Temporary send failure; retry scheduled",
            extra={"event_id": event["event_id"], "attempt": attempt + 1, "delay": delay},
        )
        time.sleep(delay)

    logger.error(
        "Event delivery exhausted retries",
        extra={"event_id": event["event_id"], "reason": reason},
    )
    return False, reason


def run(config: SimulatorConfig) -> dict[str, int]:
    generator = TelemetryGenerator(config)
    endpoint = f"{config.backend_url.rstrip('/')}/api/v1/telemetry"
    deadline = time.monotonic() + config.duration_seconds
    interval = 1.0 / config.events_per_second
    next_tick = time.monotonic()
    counters = {"attempted": 0, "created": 0, "duplicate": 0, "failed": 0}

    with httpx.Client(timeout=config.request_timeout_seconds) as client:
        while time.monotonic() < deadline:
            event, intentional_duplicate = generator.next_event()
            counters["attempted"] += 1
            delivered, outcome = send_with_retry(client, endpoint, event, config, generator.random)
            if not delivered:
                counters["failed"] += 1
            elif outcome == "duplicate":
                counters["duplicate"] += 1
            else:
                counters["created"] += 1
            logger.info(
                "Simulation event completed",
                extra={
                    "event_id": event["event_id"],
                    "bike_id": event["bike_id"],
                    "intentional_duplicate": intentional_duplicate,
                    "outcome": outcome,
                },
            )
            next_tick += interval
            remaining = next_tick - time.monotonic()
            if remaining > 0:
                time.sleep(remaining)
            elif math.isfinite(remaining):
                next_tick = time.monotonic()

    logger.info("Simulation completed", extra={"statistics": counters})
    return counters


def _env(name: str, default: str) -> str:
    return os.getenv(name, default)


def _probability(value: str) -> float:
    parsed = float(value)
    if not 0 <= parsed <= 1:
        raise argparse.ArgumentTypeError("must be between 0 and 1")
    return parsed


def parse_args(argv: list[str] | None = None) -> SimulatorConfig:
    parser = argparse.ArgumentParser(description="Generate BikeFlow telemetry events")
    parser.add_argument("--backend-url", default=_env("BACKEND_URL", "http://localhost:8000"))
    parser.add_argument("--bikes", type=int, default=int(_env("SIM_BIKES", "20")))
    parser.add_argument(
        "--events-per-second",
        type=float,
        default=float(_env("SIM_EVENTS_PER_SECOND", "2")),
    )
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=float(_env("SIM_DURATION_SECONDS", "60")),
    )
    parser.add_argument("--seed", type=int, default=int(_env("SIM_SEED", "42")))
    parser.add_argument(
        "--duplicate-probability",
        type=_probability,
        default=_probability(_env("SIM_DUPLICATE_PROBABILITY", "0.05")),
    )
    parser.add_argument(
        "--anomaly-probability",
        type=_probability,
        default=_probability(_env("SIM_ANOMALY_PROBABILITY", "0.03")),
    )
    parser.add_argument("--max-retries", type=int, default=int(_env("SIM_MAX_RETRIES", "3")))
    parser.add_argument(
        "--retry-base-seconds",
        type=float,
        default=float(_env("SIM_RETRY_BASE_SECONDS", "0.25")),
    )
    parser.add_argument(
        "--request-timeout-seconds",
        type=float,
        default=float(_env("SIM_REQUEST_TIMEOUT_SECONDS", "3")),
    )
    args = parser.parse_args(argv)
    if args.bikes < 1:
        parser.error("--bikes must be at least 1")
    if args.events_per_second <= 0:
        parser.error("--events-per-second must be positive")
    if args.duration_seconds <= 0:
        parser.error("--duration-seconds must be positive")
    if args.max_retries < 0 or args.max_retries > 10:
        parser.error("--max-retries must be between 0 and 10")
    if args.retry_base_seconds < 0 or args.request_timeout_seconds <= 0:
        parser.error("retry delay cannot be negative and timeout must be positive")
    return SimulatorConfig(**vars(args))


def main(argv: list[str] | None = None) -> int:
    configure_logging(_env("LOG_LEVEL", "INFO"))
    counters = run(parse_args(argv))
    return 1 if counters["failed"] == counters["attempted"] else 0
