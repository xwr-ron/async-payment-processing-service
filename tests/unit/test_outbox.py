import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime

from payment_service.core.config import Settings
from payment_service.services.outbox import OutboxRelay
from tests.unit.conftest import make_outbox_event


class ScalarResult:
    def __init__(self, values: list[object]) -> None:
        self.values = values

    def all(self) -> list[object]:
        return self.values


class FakeSession:
    def __init__(self, events: list[object]) -> None:
        self.events = events

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        yield

    async def scalars(self, _: object) -> ScalarResult:
        return ScalarResult(self.events)


class FakeFactory:
    def __init__(self, events: list[object]) -> None:
        self.session = FakeSession(events)

    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[FakeSession]:
        yield self.session


class FakeBroker:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.calls: list[dict] = []

    async def publish(self, _: object, **kwargs: object) -> None:
        self.calls.append(kwargs)
        if self.error:
            raise self.error


def relay(events: list[object], broker: FakeBroker) -> OutboxRelay:
    return OutboxRelay(FakeFactory(events), broker, Settings())  # type: ignore[arg-type]


async def test_publish_batch_marks_confirmed_event() -> None:
    event = make_outbox_event()
    broker = FakeBroker()

    assert await relay([event], broker).publish_batch() == 1

    assert event.published_at is not None
    assert event.publish_attempts == 1
    assert event.last_error is None
    assert broker.calls[0]["persist"] is True
    assert broker.calls[0]["mandatory"] is True
    assert broker.calls[0]["headers"] == {
        "x-attempt": 1,
        "x-request-id": "request-42",
    }


async def test_publish_failure_is_recorded_with_backoff(caplog) -> None:
    event = make_outbox_event()
    before = datetime.now(UTC)

    with caplog.at_level(logging.WARNING, logger="payment_service.services.outbox"):
        assert await relay([event], FakeBroker(ConnectionError("rabbit down"))).publish_batch() == 0

    assert event.published_at is None
    assert event.publish_attempts == 1
    assert event.last_error == "rabbit down"
    assert event.next_attempt_at > before
    assert caplog.records[0].error_type == "ConnectionError"
    assert caplog.records[0].error == "rabbit down"


async def test_empty_batch_does_nothing() -> None:
    broker = FakeBroker()

    assert await relay([], broker).publish_batch() == 0
    assert broker.calls == []
