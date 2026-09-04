import logging
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from typing import Annotated
from uuid import uuid4

from fastapi import FastAPI, HTTPException, Query, Request, Response, status
from fastapi.responses import JSONResponse
from pymongo.errors import PyMongoError

from backend.app.config import Settings, get_settings
from backend.app.logging import configure_logging
from backend.app.models import (
    HealthResponse,
    Identifier,
    IngestResponse,
    StationSummary,
    TelemetryEvent,
    TelemetryPage,
    TelemetryStatus,
)
from backend.app.repository import (
    DuplicateEventConflictError,
    MongoTelemetryRepository,
    TelemetryRepository,
)

logger = logging.getLogger("bikeflow.api")


def create_app(
    *,
    settings: Settings | None = None,
    repository: TelemetryRepository | None = None,
) -> FastAPI:
    runtime_settings = settings or get_settings()
    configure_logging(runtime_settings.log_level)
    owns_repository = repository is None
    telemetry_repository = repository or MongoTelemetryRepository(runtime_settings)

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        application.state.repository = telemetry_repository
        try:
            telemetry_repository.ensure_indexes()
            logger.info("MongoDB indexes are ready")
        except Exception:
            # The process remains live and readiness advertises the dependency failure.
            logger.warning("MongoDB unavailable during startup", exc_info=True)
        yield
        if owns_repository:
            telemetry_repository.close()

    app = FastAPI(
        title=runtime_settings.api_title,
        version=runtime_settings.api_version,
        description="API per acquisire e consultare la telemetria BikeFlow.",
        lifespan=lifespan,
    )
    app.state.repository = telemetry_repository

    @app.middleware("http")
    async def request_logging(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = request.headers.get("x-request-id", str(uuid4()))
        started = time.perf_counter()
        try:
            response = await call_next(request)
        except Exception:
            logger.exception(
                "Unhandled request error",
                extra={
                    "request_id": request_id,
                    "method": request.method,
                    "path": request.url.path,
                },
            )
            raise
        response.headers["x-request-id"] = request_id
        logger.info(
            "Request completed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "duration_ms": round((time.perf_counter() - started) * 1_000, 2),
            },
        )
        return response

    @app.exception_handler(PyMongoError)
    async def mongo_exception_handler(_request: Request, exception: PyMongoError) -> JSONResponse:
        logger.error("MongoDB operation failed", exc_info=exception)
        return JSONResponse(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"detail": "MongoDB is temporarily unavailable"},
        )

    def repo(request: Request) -> TelemetryRepository:
        return request.app.state.repository

    @app.post(
        "/api/v1/telemetry",
        response_model=IngestResponse,
        status_code=status.HTTP_201_CREATED,
        responses={
            200: {"description": "Evento già presente: nessun duplicato creato"},
            409: {"description": "event_id già associato a un payload differente"},
        },
    )
    def ingest_telemetry(
        event: TelemetryEvent,
        response: Response,
        request: Request,
    ) -> IngestResponse:
        try:
            created, persisted = repo(request).insert(event)
        except DuplicateEventConflictError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"event_id {exc} already exists with a different payload",
            ) from exc
        response.status_code = status.HTTP_201_CREATED if created else status.HTTP_200_OK
        logger.info(
            "Telemetry event processed",
            extra={
                "event_id": str(event.event_id),
                "bike_id": event.bike_id,
                "outcome": "created" if created else "duplicate",
            },
        )
        return IngestResponse(outcome="created" if created else "duplicate", event=persisted)

    @app.get("/api/v1/telemetry", response_model=TelemetryPage)
    def list_telemetry(
        request: Request,
        bike_id: Identifier | None = None,
        station_id: Identifier | None = None,
        telemetry_status: Annotated[TelemetryStatus | None, Query(alias="status")] = None,
        recorded_from: Annotated[datetime | None, Query()] = None,
        recorded_to: Annotated[datetime | None, Query()] = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    ) -> TelemetryPage:
        normalized_from = _normalize_query_timestamp(recorded_from)
        normalized_to = _normalize_query_timestamp(recorded_to)
        if normalized_from and normalized_to and normalized_from > normalized_to:
            raise HTTPException(status_code=422, detail="recorded_from must not exceed recorded_to")
        items, has_more = repo(request).list_events(
            bike_id=bike_id,
            station_id=station_id,
            status=telemetry_status,
            recorded_from=normalized_from,
            recorded_to=normalized_to,
            limit=limit,
            offset=offset,
        )
        return TelemetryPage(items=items, limit=limit, offset=offset, has_more=has_more)

    @app.get("/api/v1/bikes/{bike_id}/latest", response_model=TelemetryEvent)
    def latest_bike_event(bike_id: Identifier, request: Request) -> TelemetryEvent:
        event = repo(request).latest_for_bike(bike_id)
        if event is None:
            raise HTTPException(status_code=404, detail=f"No telemetry found for bike {bike_id}")
        return event

    @app.get("/api/v1/bikes/{bike_id}/history", response_model=TelemetryPage)
    def bike_history(
        bike_id: Identifier,
        request: Request,
        limit: Annotated[int, Query(ge=1, le=100)] = 50,
        offset: Annotated[int, Query(ge=0, le=100_000)] = 0,
    ) -> TelemetryPage:
        items, has_more = repo(request).history_for_bike(bike_id, limit=limit, offset=offset)
        return TelemetryPage(items=items, limit=limit, offset=offset, has_more=has_more)

    @app.get("/api/v1/stations/summary", response_model=list[StationSummary])
    def station_summary(
        request: Request, station_id: Identifier | None = None
    ) -> list[StationSummary]:
        return repo(request).station_summaries(station_id)

    @app.get("/health/live", response_model=HealthResponse)
    def live() -> HealthResponse:
        return HealthResponse(status="alive")

    @app.get(
        "/health/ready",
        response_model=HealthResponse,
        responses={503: {"description": "MongoDB non raggiungibile"}},
    )
    def ready(request: Request) -> HealthResponse:
        try:
            current_repository = repo(request)
            current_repository.ping()
            current_repository.ensure_indexes()
        except Exception:
            logger.warning("Readiness check failed", exc_info=True)
            raise HTTPException(status_code=503, detail="MongoDB is not ready") from None
        return HealthResponse(status="ready", mongo="available")

    return app


def _normalize_query_timestamp(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None or value.utcoffset() is None:
        raise HTTPException(status_code=422, detail="timestamps must include a timezone")
    return value.astimezone(UTC)


app = create_app()
