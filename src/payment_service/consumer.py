import logging

import httpx
from faststream import AckPolicy, FastStream
from faststream.rabbit import RabbitBroker
from faststream.rabbit.annotations import RabbitMessage

from payment_service.core.config import get_settings
from payment_service.core.logging import configure_logging
from payment_service.db.session import engine, session_factory
from payment_service.domain.events import PaymentCreatedEvent
from payment_service.messaging import (
    PAYMENTS_EXCHANGE,
    PAYMENTS_QUEUE,
    PAYMENTS_RETRY_EXCHANGE,
    declare_payment_topology,
    retry_routing_key,
)
from payment_service.services.gateway import EmulatedPaymentGateway
from payment_service.services.processor import PaymentProcessor
from payment_service.services.webhooks import WebhookClient

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)

broker = RabbitBroker(settings.rabbitmq_url)
app = FastStream(broker)
http_client = httpx.AsyncClient(
    timeout=settings.webhook_timeout_seconds,
    follow_redirects=False,
    trust_env=False,
)
processor = PaymentProcessor(
    session_factory,
    EmulatedPaymentGateway(settings),
    WebhookClient(http_client, allow_private_hosts=settings.webhook_allow_private_hosts),
)


# Ручное подтверждение необходимо, чтобы ACK выполнялся только после сохранения
# результата либо после надёжной публикации копии сообщения в retry-очередь
@broker.subscriber(PAYMENTS_QUEUE, PAYMENTS_EXCHANGE, ack_policy=AckPolicy.MANUAL)
async def handle_payment_created(event: PaymentCreatedEvent, message: RabbitMessage) -> None:
    """Обрабатывает payment.created и управляет retry/DLQ на уровне RabbitMQ"""
    raw_attempt = message.headers.get("x-attempt", 1)

    try:
        attempt = max(1, int(raw_attempt))
    except (TypeError, ValueError):
        attempt = 1

    try:
        await processor.process(event.payment_id, event.event_id)
    except Exception:
        logger.exception(
            "payment message processing failed",
            extra={
                "event_id": event.event_id,
                "payment_id": event.payment_id,
                "attempt": attempt,
            },
        )

        if attempt >= settings.consumer_max_attempts:
            # requeue=False передаёт сообщение в DLX основной очереди, откуда
            # RabbitMQ маршрутизирует его в payments.new.dlq
            await message.reject(requeue=False)

            logger.error(
                "payment message moved to dead letter queue",
                extra={"event_id": event.event_id, "attempt": attempt},
            )
            return

        try:
            # Сообщение публикуется в TTL retry-очередь. После истечения TTL
            # RabbitMQ автоматически возвращает его в payments.new
            await broker.publish(
                event.model_dump(mode="json"),
                exchange=PAYMENTS_RETRY_EXCHANGE,
                routing_key=retry_routing_key(attempt + 1),
                mandatory=True,
                persist=True,
                timeout=settings.outbox_publish_timeout_seconds,
                message_id=str(event.event_id),
                correlation_id=str(event.payment_id),
                message_type=event.event_type,
                headers={"x-attempt": attempt + 1},
            )
        except Exception:
            logger.exception("failed to republish payment message for retry")

            # Если retry-сообщение создать не удалось, исходное не подтверждаем:
            # RabbitMQ немедленно вернёт его в основную очередь без потери данных
            await message.nack(requeue=True)
            return

        # ACK исходного сообщения безопасен только после подтверждения публикации retry
        await message.ack()
        return

    await message.ack()
    logger.info(
        "payment message processed",
        extra={"event_id": event.event_id, "payment_id": event.payment_id},
    )


@app.after_startup
async def setup_topology() -> None:
    """Идемпотентно объявляет exchange, рабочую, retry и dead-letter очереди"""
    await declare_payment_topology(broker, settings)


@app.after_shutdown
async def close_resources() -> None:
    await http_client.aclose()
    await engine.dispose()


def run() -> None:
    import asyncio

    asyncio.run(app.run())


if __name__ == "__main__":
    run()
