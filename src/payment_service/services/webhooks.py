import asyncio
import ipaddress
import socket
import uuid
from datetime import datetime
from http import HTTPStatus

import httpx
from pydantic import BaseModel, ConfigDict

from payment_service.domain.enums import PaymentStatus
from payment_service.services.exceptions import (
    PermanentProcessingError,
    RetryableProcessingError,
)


class PaymentWebhook(BaseModel):
    """Событие с результатом обработки для получателя webhook"""

    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID
    event_type: str = "payment.processed"
    payment_id: uuid.UUID
    status: PaymentStatus
    processed_at: datetime


class UnsafeWebhookTargetError(PermanentProcessingError, ValueError):
    """Webhook указывает на запрещённый сетевой адрес"""

    pass


def is_unsafe_address(address: str) -> bool:
    """Запрещает адреса, не маршрутизируемые в публичном интернете"""
    ip = ipaddress.ip_address(address)

    return not ip.is_global


class WebhookClient:
    """Проверяет адрес и доставляет результат платежа по webhook"""

    _ALLOWED_SCHEMES = frozenset({"http", "https"})

    def __init__(self, client: httpx.AsyncClient, *, allow_private_hosts: bool = False) -> None:
        self._client = client
        self._allow_private_hosts = allow_private_hosts

    async def send(self, url: str, payload: PaymentWebhook, *, request_id: str) -> None:
        parsed = self._parse_target_url(url)

        # Пользователь управляет webhook URL, поэтому перед запросом проверяем
        # назначение и не разрешаем доступ к внутренней сети по умолчанию
        if not self._allow_private_hosts:
            await self._validate_target(parsed)

        try:
            response = await self._client.post(
                url,
                json=payload.model_dump(mode="json"),
                headers={
                    "Content-Type": "application/json",
                    "X-Request-ID": request_id,
                    "X-Webhook-Event-ID": str(payload.event_id),
                },
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise RetryableProcessingError(f"webhook transport error: {exc}") from exc

        status_code = response.status_code

        if (
            status_code == HTTPStatus.TOO_MANY_REQUESTS
            or status_code >= HTTPStatus.INTERNAL_SERVER_ERROR
        ):
            raise RetryableProcessingError(f"webhook returned HTTP {status_code}")
        if not HTTPStatus.OK <= status_code < HTTPStatus.MULTIPLE_CHOICES:
            raise PermanentProcessingError(f"webhook returned HTTP {status_code}")

    @staticmethod
    def _parse_target_url(url: str) -> httpx.URL:
        """Проверяет базовую форму URL до сетевого вызова"""
        parsed = httpx.URL(url)
        if parsed.scheme not in WebhookClient._ALLOWED_SCHEMES:
            raise UnsafeWebhookTargetError("webhook URL scheme must be http or https")
        if parsed.userinfo:
            raise UnsafeWebhookTargetError("webhook URL must not contain credentials")

        host = parsed.host
        if not host:
            raise UnsafeWebhookTargetError("webhook URL has no host")

        return parsed

    @staticmethod
    async def _validate_target(parsed: httpx.URL) -> None:
        """Проверяет URL и все IP-адреса, полученные при DNS-разрешении"""
        host = parsed.host
        if host is None:
            raise UnsafeWebhookTargetError("webhook URL has no host")

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        loop = asyncio.get_running_loop()

        # Проверяются все A/AAAA результаты: достаточно одного внутреннего адреса,
        # чтобы хост считался небезопасным для webhook-вызова
        try:
            addresses = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        except OSError as exc:
            raise RetryableProcessingError(f"webhook DNS resolution failed: {exc}") from exc

        if not addresses or any(is_unsafe_address(item[4][0]) for item in addresses):
            raise UnsafeWebhookTargetError("webhook target resolves to a non-public address")
