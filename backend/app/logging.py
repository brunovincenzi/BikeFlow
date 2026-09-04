import json
import logging
from datetime import UTC, datetime


class JsonFormatter(logging.Formatter):
    """Minimal JSON formatter suitable for container logs."""

    EXTRA_FIELDS = (
        "request_id",
        "method",
        "path",
        "status_code",
        "duration_ms",
        "event_id",
        "bike_id",
        "outcome",
        "attempt",
        "delay",
        "reason",
        "intentional_duplicate",
        "statistics",
    )

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "timestamp": datetime.now(UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        for field in self.EXTRA_FIELDS:
            if hasattr(record, field):
                payload[field] = getattr(record, field)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False, default=str)


def configure_logging(level: str) -> None:
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level.upper())
