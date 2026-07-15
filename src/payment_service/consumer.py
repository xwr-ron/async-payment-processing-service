import asyncio
import logging
from time import perf_counter
from typing import Any

import httpx
from faststream import AckPolicy, FastStream
from faststream.rabbit import RabbitBroker
from faststream.rabbit.annotations import RabbitMessage
from pydantic import ValidationError

from payment_service.core.config import get_settings
from payment_service.core.logging import configure_logging
from payment_service.db.session import engine, session_factory
from payment_service.domain.events import PaymentCreatedEvent
from payment_service.messaging import (
    PAYMENTS_DLQ_ROUTING_KEY,
    PAYMENTS_DLX,
    PAYMENTS_EXCHANGE,
    PAYMENTS_QUEUE,
    PAYMENTS_RETRY_EXCHANGE,
    declare_payment_topology,
    retry_routing_key,
)
from payment_service.services.exceptions import PermanentProcessingError
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
async def handle_payment_created(event_payload: dict[str, Any], message: RabbitMessage) -> None:
    """Обрабатывает payment.created и управляет retry/DLQ на уровне RabbitMQ"""
    raw_attempt = message.headers.get("x-attempt", 1)
    started_at = perf_counter()

    try:
        attempt = max(1, int(raw_attempt))
    except (TypeError, ValueError):
        attempt = 1

    try:
        event = PaymentCreatedEvent.model_validate(event_payload)
    except ValidationError as exc:
        await _move_to_dlq(
            event_payload,
            message,
            attempt=attempt,
            request_id=str(message.headers.get("x-request-id", "unknown")),
            error=PermanentProcessingError(f"invalid payment event: {exc}"),
        )
        return

    log_context = {
        "request_id": event.request_id,
        "event_id": event.event_id,
        "payment_id": event.payment_id,
        "message_attempt": attempt,
        "outbox_event_id": event.event_id,
    }

    try:
        await processor.process(event.payment_id, event.event_id, event.request_id)
    except PermanentProcessingError as exc:
        logger.error(
            "payment message has a permanent processing error",
            extra={**log_context, "error_type": type(exc).__name__, "error": str(exc)},
        )

        await _move_to_dlq(
            event.model_dump(mode="json"),
            message,
            attempt=attempt,
            request_id=event.request_id,
            error=exc,
        )

        return
    except Exception as exc:
        logger.exception(
            "payment message processing failed",
            extra={**log_context, "error_type": type(exc).__name__},
        )

        if attempt >= settings.consumer_max_attempts:
            await _move_to_dlq(
                event.model_dump(mode="json"),
                message,
                attempt=attempt,
                request_id=event.request_id,
                error=exc,
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
                headers={
                    "x-attempt": attempt + 1,
                    "x-request-id": event.request_id,
                },
            )
        except Exception:
            logger.exception("failed to republish payment message for retry", extra=log_context)

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
        extra={**log_context, "duration_ms": round((perf_counter() - started_at) * 1000, 2)},
    )


async def _move_to_dlq(
    event_payload: dict[str, Any],
    message: RabbitMessage,
    *,
    attempt: int,
    request_id: str,
    error: Exception,
) -> None:
    """Публикует сообщение в DLQ с причиной и только после confirm подтверждает исходное"""
    event_id = str(event_payload.get("event_id", "unknown"))
    payment_id = str(event_payload.get("payment_id", "unknown"))
    error_message = str(error)[:2000]

    try:
        await broker.publish(
            event_payload,
            exchange=PAYMENTS_DLX,
            routing_key=PAYMENTS_DLQ_ROUTING_KEY,
            mandatory=True,
            persist=True,
            timeout=settings.outbox_publish_timeout_seconds,
            message_id=event_id,
            correlation_id=payment_id,
            message_type=str(event_payload.get("event_type", "invalid")),
            headers={
                "x-attempt": attempt,
                "x-request-id": request_id,
                "x-error-type": type(error).__name__,
                "x-error-reason": error_message,
            },
        )
    except Exception:
        logger.exception(
            "failed to publish payment message to dead letter queue",
            extra={
                "request_id": request_id,
                "event_id": event_id,
                "payment_id": payment_id,
                "message_attempt": attempt,
            },
        )

        await message.nack(requeue=True)
        return

    await message.ack()

    logger.error(
        "payment message moved to dead letter queue",
        extra={
            "request_id": request_id,
            "event_id": event_id,
            "payment_id": payment_id,
            "message_attempt": attempt,
            "outbox_event_id": event_id,
            "error_type": type(error).__name__,
            "error": error_message,
        },
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
    asyncio.run(app.run())


if __name__ == "__main__":
    run()
