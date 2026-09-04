from collections.abc import Mapping
from datetime import datetime
from threading import Lock
from typing import Any, Protocol

from pymongo import ASCENDING, DESCENDING, MongoClient
from pymongo.collection import Collection
from pymongo.errors import DuplicateKeyError
from pymongo.write_concern import WriteConcern

from backend.app.config import Settings
from backend.app.models import StationSummary, TelemetryEvent, TelemetryStatus


class DuplicateEventConflictError(Exception):
    """The event ID exists but its payload differs."""


class TelemetryRepository(Protocol):
    def ensure_indexes(self) -> None: ...

    def insert(self, event: TelemetryEvent) -> tuple[bool, TelemetryEvent]: ...

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
    ) -> tuple[list[TelemetryEvent], bool]: ...

    def latest_for_bike(self, bike_id: str) -> TelemetryEvent | None: ...

    def history_for_bike(
        self, bike_id: str, *, limit: int, offset: int
    ) -> tuple[list[TelemetryEvent], bool]: ...

    def station_summaries(self, station_id: str | None = None) -> list[StationSummary]: ...

    def ping(self) -> None: ...

    def close(self) -> None: ...


def _to_document(event: TelemetryEvent) -> dict[str, Any]:
    document = event.model_dump(mode="python")
    document["event_id"] = str(event.event_id)
    document["status"] = event.status.value
    return document


def _to_event(document: Mapping[str, Any]) -> TelemetryEvent:
    payload = dict(document)
    payload.pop("_id", None)
    return TelemetryEvent.model_validate(payload)


class MongoTelemetryRepository:
    def __init__(self, settings: Settings):
        self._client: MongoClient[dict[str, Any]] = MongoClient(
            settings.mongo_uri,
            appname="bikeflow-backend",
            tz_aware=True,
            uuidRepresentation="standard",
            serverSelectionTimeoutMS=settings.mongo_server_selection_timeout_ms,
            connectTimeoutMS=settings.mongo_connect_timeout_ms,
        )
        database = self._client[settings.mongo_database]
        write_concern = WriteConcern(w="majority", wtimeout=settings.mongo_write_timeout_ms)
        self._collection: Collection[dict[str, Any]] = database.get_collection(
            settings.mongo_collection,
            write_concern=write_concern,
        )
        self._indexes_ready = False
        self._index_lock = Lock()

    def ensure_indexes(self) -> None:
        if self._indexes_ready:
            return
        with self._index_lock:
            if self._indexes_ready:
                return
            self._collection.create_index(
                [("event_id", ASCENDING)], unique=True, name="uq_event_id"
            )
            self._collection.create_index(
                [("bike_id", ASCENDING), ("recorded_at", DESCENDING)],
                name="bike_recorded_at",
            )
            self._collection.create_index(
                [("station_id", ASCENDING), ("recorded_at", DESCENDING)],
                name="station_recorded_at",
            )
            self._indexes_ready = True

    def insert(self, event: TelemetryEvent) -> tuple[bool, TelemetryEvent]:
        try:
            self._collection.insert_one(_to_document(event))
            return True, event
        except DuplicateKeyError:
            # This read also handles the race where two requests passed validation
            # before either insert completed.
            existing_document = self._collection.find_one({"event_id": str(event.event_id)})
            if existing_document is None:
                raise
            existing = _to_event(existing_document)
            if existing != event:
                raise DuplicateEventConflictError(str(event.event_id)) from None
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
        query: dict[str, Any] = {}
        if bike_id is not None:
            query["bike_id"] = bike_id
        if station_id is not None:
            query["station_id"] = station_id
        if status is not None:
            query["status"] = status.value
        if recorded_from is not None or recorded_to is not None:
            timestamp_filter: dict[str, datetime] = {}
            if recorded_from is not None:
                timestamp_filter["$gte"] = recorded_from
            if recorded_to is not None:
                timestamp_filter["$lte"] = recorded_to
            query["recorded_at"] = timestamp_filter

        cursor = (
            self._collection.find(query)
            .sort([("recorded_at", DESCENDING), ("event_id", ASCENDING)])
            .skip(offset)
            .limit(limit + 1)
        )
        documents = list(cursor)
        return [_to_event(document) for document in documents[:limit]], len(documents) > limit

    def latest_for_bike(self, bike_id: str) -> TelemetryEvent | None:
        document = self._collection.find_one(
            {"bike_id": bike_id},
            sort=[("recorded_at", DESCENDING), ("event_id", ASCENDING)],
        )
        return _to_event(document) if document is not None else None

    def history_for_bike(
        self, bike_id: str, *, limit: int, offset: int
    ) -> tuple[list[TelemetryEvent], bool]:
        return self.list_events(bike_id=bike_id, limit=limit, offset=offset)

    def station_summaries(self, station_id: str | None = None) -> list[StationSummary]:
        pipeline: list[dict[str, Any]] = []
        if station_id is not None:
            pipeline.append({"$match": {"station_id": station_id}})
        pipeline.extend(
            [
                {
                    "$group": {
                        "_id": "$station_id",
                        "total_events": {"$sum": 1},
                        "bikes": {"$addToSet": "$bike_id"},
                        "average_battery_pct": {"$avg": "$battery_pct"},
                        "latest_recorded_at": {"$max": "$recorded_at"},
                        "statuses": {"$push": "$status"},
                    }
                },
                {
                    "$project": {
                        "_id": 0,
                        "station_id": "$_id",
                        "total_events": 1,
                        "unique_bikes": {"$size": "$bikes"},
                        "average_battery_pct": {"$round": ["$average_battery_pct", 2]},
                        "latest_recorded_at": 1,
                        "status_counts": {
                            "available": {
                                "$size": {
                                    "$filter": {
                                        "input": "$statuses",
                                        "as": "status",
                                        "cond": {"$eq": ["$$status", "available"]},
                                    }
                                }
                            },
                            "in_use": {
                                "$size": {
                                    "$filter": {
                                        "input": "$statuses",
                                        "as": "status",
                                        "cond": {"$eq": ["$$status", "in_use"]},
                                    }
                                }
                            },
                            "maintenance": {
                                "$size": {
                                    "$filter": {
                                        "input": "$statuses",
                                        "as": "status",
                                        "cond": {"$eq": ["$$status", "maintenance"]},
                                    }
                                }
                            },
                            "offline": {
                                "$size": {
                                    "$filter": {
                                        "input": "$statuses",
                                        "as": "status",
                                        "cond": {"$eq": ["$$status", "offline"]},
                                    }
                                }
                            },
                        },
                    }
                },
                {"$sort": {"station_id": 1}},
            ]
        )
        return [
            StationSummary.model_validate(item) for item in self._collection.aggregate(pipeline)
        ]

    def ping(self) -> None:
        self._client.admin.command("ping")

    def close(self) -> None:
        self._client.close()
