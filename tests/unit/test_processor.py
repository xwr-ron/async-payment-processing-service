import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from unittest.mock import AsyncMock, patch

import httpx
import pytest

from payment_service.domain.enums import PaymentStatus
from payment_service.services.processor import PaymentProcessor
from payment_service.services.webhooks import PaymentWebhook
from tests.unit.conftest import make_payment


class FakeSession:
    @asynccontextmanager
    async def begin(self) -> AsyncIterator[None]:
        yield


class FakeSessionFactory:
    @asynccontextmanager
    async def __call__(self) -> AsyncIterator[FakeSession]:
        yield FakeSession()


class FakeGateway:
    def __init__(self, outcome: PaymentStatus) -> None:
        self.outcome = outcome
        self.calls = 0

    async def process(self, _: uuid.UUID) -> PaymentStatus:
        self.calls += 1
        return self.outcome


class FakeWebhookClient:
    def __init__(self, error: Exception | None = None) -> None:
        self.error = error
        self.payloads: list[PaymentWebhook] = []
        self.request_ids: list[str] = []

    async def send(self, _: str, payload: PaymentWebhook, *, request_id: str) -> None:
        self.payloads.append(payload)
        self.request_ids.append(request_id)
        if self.error:
            raise self.error


def processor(gateway: FakeGateway, webhook: FakeWebhookClient) -> PaymentProcessor:
    return PaymentProcessor(FakeSessionFactory(), gateway, webhook)  # type: ignore[arg-type]


async def test_processor_updates_status_then_delivers_webhook() -> None:
    payment = make_payment()
    gateway = FakeGateway(PaymentStatus.SUCCEEDED)
    webhook = FakeWebhookClient()
    service = processor(gateway, webhook)

    with patch.object(service, "_locked_payment", AsyncMock(return_value=payment)):
        await service.process(payment.id, uuid.uuid4(), "request-42")

    assert payment.status == "succeeded"
    assert payment.processed_at is not None
    assert payment.webhook_delivered_at is not None
    assert payment.webhook_attempts == 1
    assert gateway.calls == 1
    assert len(webhook.payloads) == 1
    assert webhook.request_ids == ["request-42"]


async def test_retry_skips_gateway_for_processed_payment() -> None:
    payment = make_payment(status="failed", processed_at=datetime.now(UTC))
    gateway = FakeGateway(PaymentStatus.SUCCEEDED)
    webhook = FakeWebhookClient()
    service = processor(gateway, webhook)

    with patch.object(service, "_locked_payment", AsyncMock(return_value=payment)):
        await service.process(payment.id, uuid.uuid4(), "request-42")

    assert gateway.calls == 0
    assert len(webhook.payloads) == 1


async def test_delivered_webhook_is_not_sent_twice() -> None:
    payment = make_payment(
        status="succeeded",
        processed_at=datetime.now(UTC),
        webhook_delivered_at=datetime.now(UTC),
    )
    gateway = FakeGateway(PaymentStatus.SUCCEEDED)
    webhook = FakeWebhookClient()
    service = processor(gateway, webhook)

    with patch.object(service, "_locked_payment", AsyncMock(return_value=payment)):
        await service.process(payment.id, uuid.uuid4(), "request-42")

    assert gateway.calls == 0
    assert webhook.payloads == []


async def test_webhook_failure_is_persisted_and_raised() -> None:
    payment = make_payment(status="succeeded", processed_at=datetime.now(UTC))
    error = httpx.ConnectError("merchant unavailable")
    service = processor(FakeGateway(PaymentStatus.SUCCEEDED), FakeWebhookClient(error))

    with (
        patch.object(service, "_locked_payment", AsyncMock(return_value=payment)),
        pytest.raises(httpx.ConnectError),
    ):
        await service.process(payment.id, uuid.uuid4(), "request-42")

    assert payment.webhook_attempts == 1
    assert payment.last_webhook_error == "merchant unavailable"
    assert payment.webhook_delivered_at is None


@pytest.mark.parametrize(
    ("status", "processed_at", "message"),
    [("pending", None, "unprocessed"), ("succeeded", None, "processed_at")],
)
async def test_inconsistent_payment_state_is_rejected(
    status: str, processed_at: datetime | None, message: str
) -> None:
    payment = make_payment(status=status, processed_at=processed_at)
    service = processor(FakeGateway(PaymentStatus.SUCCEEDED), FakeWebhookClient())

    with (
        patch.object(service, "_locked_payment", AsyncMock(return_value=payment)),
        pytest.raises(Exception, match=message),
    ):
        await service._deliver_webhook_once(payment.id, uuid.uuid4(), "request-42")
