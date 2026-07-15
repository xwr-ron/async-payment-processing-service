from faststream.rabbit import ExchangeType, RabbitBroker, RabbitExchange, RabbitQueue

from payment_service.core.config import Settings

PAYMENTS_ROUTING_KEY = "payments.new"
PAYMENTS_DLQ_ROUTING_KEY = "payments.new.dlq"

# Отдельные exchange разделяют обычную обработку, отложенные повторы и DLQ
PAYMENTS_EXCHANGE = RabbitExchange("payments", type=ExchangeType.DIRECT, durable=True)
PAYMENTS_RETRY_EXCHANGE = RabbitExchange("payments.retry", type=ExchangeType.DIRECT, durable=True)
PAYMENTS_DLX = RabbitExchange("payments.dlx", type=ExchangeType.DIRECT, durable=True)

PAYMENTS_QUEUE = RabbitQueue(
    "payments.new",
    durable=True,
    routing_key=PAYMENTS_ROUTING_KEY,
    arguments={
        # Reject с requeue=False направляет сообщение не в пустоту, а в DLX
        "x-dead-letter-exchange": PAYMENTS_DLX.name,
        "x-dead-letter-routing-key": PAYMENTS_DLQ_ROUTING_KEY,
    },
)
PAYMENTS_DLQ = RabbitQueue(
    "payments.new.dlq",
    durable=True,
    routing_key=PAYMENTS_DLQ_ROUTING_KEY,
)


def retry_routing_key(next_attempt: int) -> str:
    """Возвращает routing key очереди для конкретного номера попытки"""
    return f"payments.retry.{next_attempt}"


async def declare_payment_topology(broker: RabbitBroker, settings: Settings) -> None:
    """Объявляет устойчивую RabbitMQ-топологию обработки платежей"""
    payments_exchange = await broker.declare_exchange(PAYMENTS_EXCHANGE)
    payments_queue = await broker.declare_queue(PAYMENTS_QUEUE)
    await payments_queue.bind(payments_exchange, routing_key=PAYMENTS_ROUTING_KEY)

    retry_exchange = await broker.declare_exchange(PAYMENTS_RETRY_EXCHANGE)

    for next_attempt in range(2, settings.consumer_max_attempts + 1):
        # Для последующих попыток задержка равна base, затем base*2 и далее по экспоненте
        delay_seconds = settings.consumer_retry_base_seconds * (2 ** (next_attempt - 2))
        routing_key = retry_routing_key(next_attempt)

        retry_queue = await broker.declare_queue(
            RabbitQueue(
                f"payments.retry.{next_attempt}",
                durable=True,
                routing_key=routing_key,
                arguments={
                    # Retry-очереди не имеют consumer. Сообщение ждёт TTL, затем
                    # dead-letter-маршрутизация возвращает его в основную очередь
                    "x-message-ttl": int(delay_seconds * 1000),
                    "x-dead-letter-exchange": PAYMENTS_EXCHANGE.name,
                    "x-dead-letter-routing-key": PAYMENTS_ROUTING_KEY,
                },
            )
        )
        await retry_queue.bind(retry_exchange, routing_key=routing_key)

    dead_letter_exchange = await broker.declare_exchange(PAYMENTS_DLX)
    dead_letter_queue = await broker.declare_queue(PAYMENTS_DLQ)

    await dead_letter_queue.bind(dead_letter_exchange, routing_key=PAYMENTS_DLQ_ROUTING_KEY)
