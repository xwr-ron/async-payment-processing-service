import socket
import uuid
from datetime import UTC, datetime

import httpx
import pytest

from payment_service.domain.enums import PaymentStatus
from payment_service.services.exceptions import (
    PermanentProcessingError,
    RetryableProcessingError,
)
from payment_service.services.webhooks import (
    PaymentWebhook,
    UnsafeWebhookTargetError,
    WebhookClient,
    is_unsafe_address,
)


def payload() -> PaymentWebhook:
    return PaymentWebhook(
        event_id=uuid.uuid4(),
        payment_id=uuid.uuid4(),
        status=PaymentStatus.SUCCEEDED,
        processed_at=datetime.now(UTC),
    )


@pytest.mark.parametrize("address", ["127.0.0.1", "10.0.0.1", "169.254.169.254", "::1"])
def test_private_addresses_are_unsafe(address: str) -> None:
    assert is_unsafe_address(address)


def test_public_address_is_safe() -> None:
    assert not is_unsafe_address("93.184.216.34")


async def test_webhook_sends_stable_event_header() -> None:
    requests: list[httpx.Request] = []

    async def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(204)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        client = WebhookClient(http_client, allow_private_hosts=True)
        event = payload()
        await client.send(
            "https://merchant.example/webhook",
            event,
            request_id="request-42",
        )

    assert requests[0].headers["X-Webhook-Event-ID"] == str(event.event_id)
    assert requests[0].headers["X-Request-ID"] == "request-42"
    assert b'"status":"succeeded"' in requests[0].content


async def test_webhook_raises_for_error_response() -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(RetryableProcessingError, match="HTTP 503"):
            await WebhookClient(http_client, allow_private_hosts=True).send(
                "https://merchant.example/webhook",
                payload(),
                request_id="request-42",
            )


@pytest.mark.parametrize("status_code", [400, 401, 403])
async def test_webhook_does_not_retry_permanent_http_error(status_code: int) -> None:
    async def handler(_: httpx.Request) -> httpx.Response:
        return httpx.Response(status_code)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http_client:
        with pytest.raises(PermanentProcessingError, match=f"HTTP {status_code}"):
            await WebhookClient(http_client, allow_private_hosts=True).send(
                "https://merchant.example/webhook",
                payload(),
                request_id="request-42",
            )


async def test_webhook_rejects_credentials() -> None:
    async with httpx.AsyncClient() as http_client:
        with pytest.raises(UnsafeWebhookTargetError, match="credentials"):
            await WebhookClient(http_client).send(
                "https://user:password@merchant.example/webhook",
                payload(),
                request_id="request-42",
            )


@pytest.mark.parametrize("url", ["ftp://merchant.example/webhook", "merchant.example/webhook"])
async def test_webhook_rejects_unsupported_scheme_even_for_private_hosts(url: str) -> None:
    async with httpx.AsyncClient() as http_client:
        with pytest.raises(UnsafeWebhookTargetError, match="scheme"):
            await WebhookClient(http_client, allow_private_hosts=True).send(
                url,
                payload(),
                request_id="request-42",
            )


async def test_webhook_rejects_private_dns_result(monkeypatch: pytest.MonkeyPatch) -> None:
    def fake_getaddrinfo(*_: object, **__: object) -> list[tuple]:
        return [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 443))]

    monkeypatch.setattr(socket, "getaddrinfo", fake_getaddrinfo)
    async with httpx.AsyncClient() as http_client:
        with pytest.raises(UnsafeWebhookTargetError, match="non-public"):
            await WebhookClient(http_client).send(
                "https://localhost/webhook",
                payload(),
                request_id="request-42",
            )
