import asyncio
import logging
from datetime import UTC, datetime, timedelta

from faststream.rabbit import RabbitBroker
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from payment_service.core.config import Settings
from payment_service.domain.models import OutboxEvent
from payment_service.messaging import PAYMENTS_EXCHANGE, PAYMENTS_ROUTING_KEY

logger = logging.getLogger(__name__)


class OutboxRelay:
    """Публикует подтверждённые события с гарантией доставки at-least-once"""

    def __init__(
        self,
        session_factory: async_sessionmaker[AsyncSession],
        broker: RabbitBroker,
        settings: Settings,
    ) -> None:
        self._session_factory = session_factory
        self._broker = broker
        self._settings = settings

    async def run(self) -> None:
        logger.info("outbox relay started")
        while True:
            try:
                published = await self.publish_batch()
                if published == 0:
                    await asyncio.sleep(self._settings.outbox_poll_interval_seconds)
            except asyncio.CancelledError:
                logger.info("outbox relay stopped")
                raise
            except Exception:
                logger.exception("outbox relay iteration failed")
                await asyncio.sleep(self._settings.outbox_poll_interval_seconds)

    async def publish_batch(self) -> int:
        published = 0
        async with self._session_factory() as session, session.begin():
            # SKIP LOCKED позволяет безопасно запустить несколько relay-процессов:
            # каждый экземпляр получит собственную непересекающуюся пачку событий
            events = list(
                (
                    await session.scalars(
                        select(OutboxEvent)
                        .where(
                            OutboxEvent.published_at.is_(None),
                            OutboxEvent.next_attempt_at <= datetime.now(UTC),
                        )
                        .order_by(OutboxEvent.created_at)
                        .limit(self._settings.outbox_batch_size)
                        .with_for_update(skip_locked=True)
                    )
                ).all()
            )
            for event in events:
                event.publish_attempts += 1
                request_id = str(event.payload.get("request_id", event.id))

                try:
                    # persist сохраняет сообщение при рестарте RabbitMQ, mandatory
                    # не позволяет молча потерять его при отсутствии маршрута
                    await self._broker.publish(
                        event.payload,
                        exchange=PAYMENTS_EXCHANGE,
                        routing_key=PAYMENTS_ROUTING_KEY,
                        mandatory=True,
                        persist=True,
                        timeout=self._settings.outbox_publish_timeout_seconds,
                        message_id=str(event.id),
                        correlation_id=str(event.aggregate_id),
                        message_type=event.event_type,
                        headers={"x-attempt": 1, "x-request-id": request_id},
                    )
                except Exception as exc:
                    event.last_error = str(exc)[:2000]
                    # Ошибка RabbitMQ не откатывает платёж. Событие остаётся в outbox
                    # и будет повторено с ограниченной экспоненциальной задержкой
                    backoff_seconds = min(2 ** (event.publish_attempts - 1), 60)
                    event.next_attempt_at = datetime.now(UTC) + timedelta(seconds=backoff_seconds)
                    logger.warning(
                        "outbox event publication failed",
                        extra={
                            "request_id": request_id,
                            "event_id": event.id,
                            "payment_id": event.aggregate_id,
                            "outbox_event_id": event.id,
                            "message_attempt": event.publish_attempts,
                        },
                    )
                else:
                    # published_at выставляется только после publisher confirm
                    # Сбой между confirm и commit может дать дубль, поэтому consumer
                    # обязан оставаться идемпотентным
                    event.published_at = datetime.now(UTC)
                    event.last_error = None

                    published += 1

                    logger.info(
                        "outbox event published",
                        extra={
                            "request_id": request_id,
                            "event_id": event.id,
                            "payment_id": event.aggregate_id,
                            "outbox_event_id": event.id,
                            "message_attempt": event.publish_attempts,
                        },
                    )

        return published
