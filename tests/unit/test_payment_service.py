import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from unittest.mock import AsyncMock, patch

import pytest

from payment_service.api.schemas import PaymentCreate
from payment_service.services.exceptions import IdempotencyConflictError, PaymentNotFoundError
from payment_service.services.payments import PaymentService, request_fingerprint
from tests.unit.conftest import make_payment


class Result:
    def __init__(self, value: object) -> None:
        self.value = value

    def scalar_one_or_none(self) -> object:
        return self.value


class FakeSession:
    def __init__(self, execute_result: object = None, get_result: object = None) -> None:
        self.execute_result = execute_result
        self.get_result = get_result
        self.added: list[object] = []

    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        yield

    async def execute(self, _: object) -> Result:
        return Result(self.execute_result)

    async def get(self, *_: object) -> object:
        return self.get_result

    def add(self, value: object) -> None:
        self.added.append(value)


def create_body(amount: str = "125.50") -> PaymentCreate:
    return PaymentCreate.model_validate(
        {
            "amount": amount,
            "currency": "RUB",
            "description": "Order #42",
            "metadata": {"order_id": 42},
            "webhook_url": "https://merchant.example/webhook",
        }
    )


async def test_create_adds_outbox_event_in_same_transaction() -> None:
    body = create_body()
    session = FakeSession(execute_result=uuid.uuid4())
    service = PaymentService(session)  # type: ignore[arg-type]
    payment = make_payment(request_fingerprint=request_fingerprint(body))

    with patch.object(service, "_get_by_idempotency_key", AsyncMock(return_value=payment)):
        result = await service.create(body, "idem-42", "request-42")

    assert result is payment
    assert len(session.added) == 1
    event = session.added[0]
    assert str(event.aggregate_id) == event.payload["payment_id"]
    assert event.payload["request_id"] == "request-42"


async def test_existing_identical_request_does_not_add_event() -> None:
    body = create_body()
    session = FakeSession(execute_result=None)
    service = PaymentService(session)  # type: ignore[arg-type]
    payment = make_payment(request_fingerprint=request_fingerprint(body))

    with patch.object(service, "_get_by_idempotency_key", AsyncMock(return_value=payment)):
        assert await service.create(body, "idem-42") is payment

    assert session.added == []


async def test_existing_changed_request_conflicts() -> None:
    session = FakeSession(execute_result=None)
    service = PaymentService(session)  # type: ignore[arg-type]
    payment = make_payment(request_fingerprint=request_fingerprint(create_body()))

    with (
        patch.object(service, "_get_by_idempotency_key", AsyncMock(return_value=payment)),
        pytest.raises(IdempotencyConflictError),
    ):
        await service.create(create_body("126.00"), "idem-42")


async def test_get_returns_payment() -> None:
    payment = make_payment()
    service = PaymentService(FakeSession(get_result=payment))  # type: ignore[arg-type]

    assert await service.get(payment.id) is payment


async def test_get_missing_payment_raises() -> None:
    service = PaymentService(FakeSession())  # type: ignore[arg-type]

    with pytest.raises(PaymentNotFoundError):
        await service.get(uuid.uuid4())


async def test_defensive_missing_idempotency_read_raises() -> None:
    service = PaymentService(FakeSession(execute_result=None))  # type: ignore[arg-type]

    with pytest.raises(RuntimeError, match="disappeared"):
        await service._get_by_idempotency_key("missing")
