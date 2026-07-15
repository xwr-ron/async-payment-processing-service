import uuid
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from payment_service.domain.enums import PaymentStatus
from payment_service.domain.models import Payment
from payment_service.services.exceptions import PaymentNotFoundError
from payment_service.services.gateway import PaymentGateway
from payment_service.services.webhooks import PaymentWebhook, WebhookClient


class PaymentProcessor:
    """Обрабатывает платёж и доставляет webhook как две сохраняемые фазы"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        gateway: PaymentGateway,
        webhook_client: WebhookClient,
    ) -> None:
        self._session_factory = session_factory
        self._gateway = gateway
        self._webhook_client = webhook_client

    async def process(self, payment_id: uuid.UUID, event_id: uuid.UUID) -> None:
        # Раздельные транзакции важны для retry: после успешного шлюза ошибка
        # webhook не должна приводить к повторной обработке самого платежа
        await self._process_gateway_once(payment_id)
        await self._deliver_webhook_once(payment_id, event_id)

    async def _process_gateway_once(self, payment_id: uuid.UUID) -> None:
        async with self._session_factory() as session, session.begin():
            payment = await self._locked_payment(session, payment_id)
            # Дубликат сообщения увидит финальный статус и не вызовет шлюз повторно
            if payment.status != PaymentStatus.PENDING.value:
                return

            outcome = await self._gateway.process(payment.id)
            payment.status = outcome.value
            payment.processed_at = datetime.now(UTC)

    async def _deliver_webhook_once(self, payment_id: uuid.UUID, event_id: uuid.UUID) -> None:
        delivery_error: Exception | None = None

        async with self._session_factory() as session, session.begin():
            payment = await self._locked_payment(session, payment_id)

            if payment.status == PaymentStatus.PENDING.value:
                raise RuntimeError("cannot deliver a webhook for an unprocessed payment")
            if payment.webhook_delivered_at is not None:
                # Успешно доставленный webhook не отправляется повторно при дубле
                return
            if payment.processed_at is None:
                raise RuntimeError("processed payment has no processed_at timestamp")

            payment.webhook_attempts += 1

            payload = PaymentWebhook(
                event_id=event_id,
                payment_id=payment.id,
                status=payment.status_value,
                processed_at=payment.processed_at,
            )

            try:
                await self._webhook_client.send(payment.webhook_url, payload)
            except Exception as exc:
                payment.last_webhook_error = str(exc)[:2000]
                delivery_error = exc
            else:
                payment.webhook_delivered_at = datetime.now(UTC)
                payment.last_webhook_error = None

        # Исключение поднимается после выхода из транзакции, чтобы счётчик попыток
        # и текст ошибки успели сохраниться перед отправкой сообщения на retry
        if delivery_error is not None:
            raise delivery_error

    @staticmethod
    async def _locked_payment(session: AsyncSession, payment_id: uuid.UUID) -> Payment:
        # FOR UPDATE сериализует обработку дублей одного платежа даже при
        # одновременной работе нескольких экземпляров consumer
        result = await session.execute(
            select(Payment).where(Payment.id == payment_id).with_for_update()
        )

        payment = result.scalar_one_or_none()
        if payment is None:
            raise PaymentNotFoundError

        return payment
