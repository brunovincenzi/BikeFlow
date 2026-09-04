from concurrent.futures import ThreadPoolExecutor

from backend.app.models import TelemetryEvent
from tests.fakes import InMemoryTelemetryRepository


def test_concurrent_inserts_create_exactly_one_event(event_payload: dict[str, object]) -> None:
    repository = InMemoryTelemetryRepository()
    event = TelemetryEvent.model_validate(event_payload)

    with ThreadPoolExecutor(max_workers=12) as pool:
        results = list(pool.map(lambda _: repository.insert(event), range(30)))

    assert sum(created for created, _ in results) == 1
    assert len(repository.events) == 1
