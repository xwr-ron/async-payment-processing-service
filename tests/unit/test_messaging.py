from payment_service.core.config import Settings
from payment_service.messaging import declare_payment_topology, retry_routing_key


class DeclaredQueue:
    def __init__(self) -> None:
        self.bindings: list[tuple[object, str]] = []

    async def bind(self, exchange: object, *, routing_key: str) -> None:
        self.bindings.append((exchange, routing_key))


class FakeBroker:
    def __init__(self) -> None:
        self.exchanges: list[object] = []
        self.queues: list[tuple[object, DeclaredQueue]] = []

    async def declare_exchange(self, exchange: object) -> object:
        self.exchanges.append(exchange)
        return exchange

    async def declare_queue(self, queue: object) -> DeclaredQueue:
        declared = DeclaredQueue()
        self.queues.append((queue, declared))
        return declared


def test_retry_routing_key_uses_attempt_number() -> None:
    assert retry_routing_key(3) == "payments.retry.3"


async def test_topology_declares_main_retry_and_dlq() -> None:
    broker = FakeBroker()
    settings = Settings(consumer_max_attempts=3, consumer_retry_base_seconds=2)

    await declare_payment_topology(broker, settings)  # type: ignore[arg-type]

    queue_names = [str(queue.name) for queue, _ in broker.queues]
    assert queue_names == [
        "payments.new",
        "payments.retry.2",
        "payments.retry.3",
        "payments.new.dlq",
    ]
    retry_arguments = broker.queues[2][0].arguments
    assert retry_arguments["x-message-ttl"] == 4000
