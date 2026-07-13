import asyncio
import random
import uuid
from typing import Protocol

from payment_service.core.config import Settings
from payment_service.domain.enums import PaymentStatus


class PaymentGateway(Protocol):
    """Контракт внешнего платёжного провайдера"""

    async def process(self, payment_id: uuid.UUID) -> PaymentStatus: ...


class EmulatedPaymentGateway:
    """Эмуляция задержки и бизнес-результата платёжного провайдера"""

    def __init__(self, settings: Settings, random_source: random.Random | None = None) -> None:
        self._settings = settings
        self._random = random_source or random.Random()

    async def process(self, payment_id: uuid.UUID) -> PaymentStatus:
        # Реальный провайдер должен получать payment_id как собственный
        # idempotency key, чтобы повтор после сетевого сбоя не создавал списание
        del payment_id
        delay = self._random.uniform(
            self._settings.payment_processing_min_seconds,
            self._settings.payment_processing_max_seconds,
        )

        await asyncio.sleep(delay)
        return PaymentStatus.SUCCEEDED if self._random.random() < 0.9 else PaymentStatus.FAILED
