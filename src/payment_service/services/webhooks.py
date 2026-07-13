import asyncio
import ipaddress
import socket
import uuid
from datetime import datetime

import httpx
from pydantic import BaseModel, ConfigDict

from payment_service.domain.enums import PaymentStatus


class PaymentWebhook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID
    event_type: str = "payment.processed"
    payment_id: uuid.UUID
    status: PaymentStatus
    processed_at: datetime


class UnsafeWebhookTargetError(ValueError):
    pass


def is_unsafe_address(address: str) -> bool:
    """Запрещает адреса, не маршрутизируемые в публичном интернете"""
    ip = ipaddress.ip_address(address)

    return not ip.is_global


class WebhookClient:
    def __init__(self, client: httpx.AsyncClient, *, allow_private_hosts: bool = False) -> None:
        self._client = client
        self._allow_private_hosts = allow_private_hosts

    async def send(self, url: str, payload: PaymentWebhook) -> None:
        # Пользователь управляет webhook URL, поэтому перед запросом проверяем
        # назначение и не разрешаем доступ к внутренней сети по умолчанию
        if not self._allow_private_hosts:
            await self._validate_target(url)

        response = await self._client.post(
            url,
            json=payload.model_dump(mode="json"),
            headers={
                "Content-Type": "application/json",
                "X-Webhook-Event-ID": str(payload.event_id),
            },
        )
        response.raise_for_status()

    @staticmethod
    async def _validate_target(url: str) -> None:
        """Проверяет URL и все IP-адреса, полученные при DNS-разрешении"""
        parsed = httpx.URL(url)
        if parsed.userinfo:
            raise UnsafeWebhookTargetError("webhook URL must not contain credentials")

        host = parsed.host
        if not host:
            raise UnsafeWebhookTargetError("webhook URL has no host")

        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        loop = asyncio.get_running_loop()

        # Проверяются все A/AAAA результаты: достаточно одного внутреннего адреса,
        # чтобы хост считался небезопасным для webhook-вызова
        addresses = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
        if not addresses or any(is_unsafe_address(item[4][0]) for item in addresses):
            raise UnsafeWebhookTargetError("webhook target resolves to a non-public address")
