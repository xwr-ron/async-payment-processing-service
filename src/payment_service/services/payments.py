import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from payment_service.api.schemas import PaymentCreate
from payment_service.domain.events import PaymentCreatedEvent
from payment_service.domain.models import OutboxEvent, Payment
from payment_service.services.exceptions import IdempotencyConflictError, PaymentNotFoundError


def request_fingerprint(data: PaymentCreate) -> str:
    """Строит стабильный отпечаток запроса для проверки идемпотентности"""
    # Одинаковые JSON-объекты могут иметь разный порядок ключей. Каноническое
    # представление гарантирует одинаковый SHA-256 для семантически равных запросов
    canonical: dict[str, Any] = {
        "amount": format(data.amount, ".2f"),
        "currency": data.currency.value,
        "description": data.description,
        "metadata": data.metadata,
        "webhook_url": str(data.webhook_url),
    }

    encoded = json.dumps(
        canonical,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        default=str,
    ).encode()

    return hashlib.sha256(encoded).hexdigest()


class PaymentService:
    """Реализует сценарии создания и чтения платежей"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def create(self, data: PaymentCreate, idempotency_key: str) -> Payment:
        fingerprint = request_fingerprint(data)
        payment_id = uuid.uuid4()
        event_id = uuid.uuid4()
        occurred_at = datetime.now(UTC)

        payment_values: dict[str, Any] = {
            "id": payment_id,
            "amount": data.amount,
            "currency": data.currency.value,
            "description": data.description,
            "metadata_": data.metadata,
            "status": "pending",
            "idempotency_key": idempotency_key,
            "request_fingerprint": fingerprint,
            "webhook_url": str(data.webhook_url),
        }

        statement = (
            insert(Payment)
            .values(**payment_values)
            # Уникальное ограничение в БД разрешает гонку параллельных запросов
            # Предварительный SELECT здесь был бы подвержен race condition
            .on_conflict_do_nothing(index_elements=[Payment.idempotency_key])
            .returning(Payment.id)
        )

        async with self._session.begin():
            inserted_id = (await self._session.execute(statement)).scalar_one_or_none()
            if inserted_id is not None:
                event = PaymentCreatedEvent(
                    event_id=event_id,
                    payment_id=payment_id,
                    occurred_at=occurred_at,
                )

                # Платёж и outbox-событие фиксируются одной транзакцией: невозможно
                # получить платёж без события или событие без платежа
                self._session.add(
                    OutboxEvent(
                        id=event_id,
                        aggregate_id=payment_id,
                        event_type=event.event_type,
                        payload=event.model_dump(mode="json"),
                    )
                )

        # После ON CONFLICT читаем фактическую строку: она могла быть создана
        # текущим запросом либо конкурентным запросом с тем же ключом
        payment = await self._get_by_idempotency_key(idempotency_key)

        # Один ключ нельзя переиспользовать для другого бизнес-запроса
        if payment.request_fingerprint != fingerprint:
            raise IdempotencyConflictError

        return payment

    async def get(self, payment_id: uuid.UUID) -> Payment:
        payment = await self._session.get(Payment, payment_id)

        if payment is None:
            raise PaymentNotFoundError

        return payment

    async def _get_by_idempotency_key(self, key: str) -> Payment:
        result = await self._session.execute(select(Payment).where(Payment.idempotency_key == key))
        payment = result.scalar_one_or_none()

        # Защитная проверка: INSERT и последующее чтение выполняются на основной БД,
        # поэтому исчезновение строки означало бы нарушение внутреннего инварианта
        if payment is None:
            raise RuntimeError("payment disappeared after idempotent insert")

        return payment
