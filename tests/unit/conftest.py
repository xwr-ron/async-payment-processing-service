import uuid
from datetime import UTC, datetime
from decimal import Decimal

from payment_service.domain.models import OutboxEvent, Payment


def make_payment(**overrides: object) -> Payment:
    values: dict[str, object] = {
        "id": uuid.uuid4(),
        "amount": Decimal("125.50"),
        "currency": "RUB",
        "description": "Order #42",
        "metadata_": {"order_id": 42},
        "status": "pending",
        "idempotency_key": "idem-42",
        "request_fingerprint": "f" * 64,
        "webhook_url": "https://merchant.example/webhook",
        "created_at": datetime.now(UTC),
        "processed_at": None,
        "webhook_delivered_at": None,
        "webhook_attempts": 0,
        "last_webhook_error": None,
    }
    values.update(overrides)
    return Payment(**values)


def make_outbox_event(**overrides: object) -> OutboxEvent:
    event_id = uuid.uuid4()
    payment_id = uuid.uuid4()
    values: dict[str, object] = {
        "id": event_id,
        "aggregate_id": payment_id,
        "event_type": "payment.created",
        "payload": {
            "event_id": str(event_id),
            "event_type": "payment.created",
            "payment_id": str(payment_id),
            "occurred_at": datetime.now(UTC).isoformat(),
            "request_id": "request-42",
        },
        "created_at": datetime.now(UTC),
        "next_attempt_at": datetime.now(UTC),
        "published_at": None,
        "publish_attempts": 0,
        "last_error": None,
    }
    values.update(overrides)

    return OutboxEvent(**values)
