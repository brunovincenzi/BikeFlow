from datetime import datetime
from threading import RLock

from backend.app.models import StationSummary, TelemetryEvent, TelemetryStatus
from backend.app.repository import DuplicateEventConflictError


class InMemoryTelemetryRepository:
    def __init__(self) -> None:
        self.events: dict[str, TelemetryEvent] = {}
        self.lock = RLock()
        self.available = True
        self.indexes_ensured = False

    def ensure_indexes(self) -> None:
        self.indexes_ensured = True

    def insert(self, event: TelemetryEvent) -> tuple[bool, TelemetryEvent]:
        key = str(event.event_id)
        with self.lock:
            existing = self.events.get(key)
            if existing is None:
                self.events[key] = event
                return True, event
            if existing != event:
                raise DuplicateEventConflictError(key)
            return False, existing

    def list_events(
        self,
        *,
        bike_id: str | None = None,
        station_id: str | None = None,
        status: TelemetryStatus | None = None,
        recorded_from: datetime | None = None,
        recorded_to: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
    ) -> tuple[list[TelemetryEvent], bool]:
        items = list(self.events.values())
        if bike_id is not None:
            items = [event for event in items if event.bike_id == bike_id]
        if station_id is not None:
            items = [event for event in items if event.station_id == station_id]
        if status is not None:
            items = [event for event in items if event.status == status]
        if recorded_from is not None:
            items = [event for event in items if event.recorded_at >= recorded_from]
        if recorded_to is not None:
            items = [event for event in items if event.recorded_at <= recorded_to]
        items.sort(key=lambda item: (item.recorded_at, str(item.event_id)), reverse=True)
        page = items[offset : offset + limit + 1]
        return page[:limit], len(page) > limit

    def latest_for_bike(self, bike_id: str) -> TelemetryEvent | None:
        items, _ = self.list_events(bike_id=bike_id, limit=1)
        return items[0] if items else None

    def history_for_bike(
        self, bike_id: str, *, limit: int, offset: int
    ) -> tuple[list[TelemetryEvent], bool]:
        return self.list_events(bike_id=bike_id, limit=limit, offset=offset)

    def station_summaries(self, station_id: str | None = None) -> list[StationSummary]:
        grouped: dict[str, list[TelemetryEvent]] = {}
        for event in self.events.values():
            if station_id is None or event.station_id == station_id:
                grouped.setdefault(event.station_id, []).append(event)
        summaries = []
        for current_station, events in sorted(grouped.items()):
            counts = {item.value: 0 for item in TelemetryStatus}
            for event in events:
                counts[event.status.value] += 1
            summaries.append(
                StationSummary(
                    station_id=current_station,
                    total_events=len(events),
                    unique_bikes=len({event.bike_id for event in events}),
                    average_battery_pct=round(
                        sum(event.battery_pct for event in events) / len(events), 2
                    ),
                    latest_recorded_at=max(event.recorded_at for event in events),
                    status_counts=counts,
                )
            )
        return summaries

    def ping(self) -> None:
        if not self.available:
            raise ConnectionError("fake MongoDB unavailable")

    def close(self) -> None:
        pass
