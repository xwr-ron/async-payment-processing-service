"""Полный Compose integration smoke для API, outbox, RabbitMQ, consumer и webhook"""

import base64
import json
import os
import sys
import time
import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.error import HTTPError
from urllib.request import Request, urlopen

BASE_URL = os.getenv("BASE_URL", "http://localhost:8000")
WEBHOOK_RECEIVER_URL = os.getenv("WEBHOOK_RECEIVER_URL", "http://localhost:18080")
RABBITMQ_MANAGEMENT_URL = os.getenv("RABBITMQ_MANAGEMENT_URL", "http://localhost:15672")
API_KEY = os.getenv("API_KEY", "local-development-key")
RABBITMQ_USER = os.getenv("RABBITMQ_USER", "payments")
RABBITMQ_PASSWORD = os.getenv("RABBITMQ_PASSWORD", "payments")
RETRY_BASE_SECONDS = float(os.getenv("CONSUMER_RETRY_BASE_SECONDS", "2"))


def http_json(
    method: str,
    url: str,
    body: object | None = None,
    headers: dict[str, str] | None = None,
    *,
    timeout: float = 5,
) -> Any:
    payload = json.dumps(body).encode() if body is not None else None
    request = Request(
        url,
        data=payload,
        method=method,
        headers={"Content-Type": "application/json", **(headers or {})},
    )
    with urlopen(request, timeout=timeout) as response:
        content = response.read()
        return json.loads(content) if content else None


def api_request(
    method: str,
    path: str,
    body: object | None = None,
    **headers: str,
) -> dict[str, Any]:
    result = http_json(
        method,
        f"{BASE_URL}{path}",
        body,
        {"X-API-Key": API_KEY, **headers},
    )
    assert isinstance(result, dict)
    return result


def rabbitmq_request(method: str, path: str, body: object | None = None) -> Any:
    credentials = base64.b64encode(f"{RABBITMQ_USER}:{RABBITMQ_PASSWORD}".encode()).decode()
    return http_json(
        method,
        f"{RABBITMQ_MANAGEMENT_URL}{path}",
        body,
        {"Authorization": f"Basic {credentials}"},
    )


def queue_ack_count(queue_name: str) -> int:
    queue = rabbitmq_request("GET", f"/api/queues/%2F/{queue_name}")
    stats = queue.get("message_stats", {})
    return int(stats.get("ack", 0))


def wait_until[T](description: str, callback: Callable[[], T | None], timeout: float) -> T:
    deadline = time.monotonic() + timeout
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        try:
            result = callback()
            if result is not None:
                return result
        except (HTTPError, OSError, AssertionError) as exc:
            last_error = exc
        time.sleep(0.25)

    detail = f": {last_error}" if last_error else ""
    raise TimeoutError(f"timed out waiting for {description}{detail}")


def create_payment(webhook_path: str, request_id: str) -> tuple[str, dict[str, Any]]:
    idempotency_key = f"smoke-{uuid.uuid4()}"
    body = {
        "amount": "125.50",
        "currency": "RUB",
        "description": f"Integration smoke {webhook_path}",
        "metadata": {"source": "compose-integration-test"},
        "webhook_url": f"http://webhook-sink:8080{webhook_path}",
    }
    headers = {"Idempotency-Key": idempotency_key, "X-Request-ID": request_id}
    accepted = api_request("POST", "/api/v1/payments", body, **headers)
    payment_id = str(accepted["payment_id"])

    duplicate = api_request("POST", "/api/v1/payments", body, **headers)
    assert duplicate["payment_id"] == payment_id

    try:
        api_request("POST", "/api/v1/payments", {**body, "amount": "126.00"}, **headers)
    except HTTPError as exc:
        assert exc.code == 409
    else:
        raise AssertionError("changed idempotent request must return 409")

    return payment_id, body


def payment_when(payment_id: str, predicate: Callable[[dict[str, Any]], bool]) -> dict[str, Any]:
    def inspect() -> dict[str, Any] | None:
        payment = api_request("GET", f"/api/v1/payments/{payment_id}")
        return payment if predicate(payment) else None

    return wait_until(f"payment {payment_id} state", inspect, timeout=35)


def webhook_records(path: str, expected_count: int) -> list[dict[str, Any]] | None:
    state = http_json("GET", f"{WEBHOOK_RECEIVER_URL}/state")
    records = state.get(path, [])
    return records if len(records) >= expected_count else None


def publish_duplicate_event(
    payment: dict[str, Any],
    record: dict[str, Any],
    request_id: str,
) -> None:
    event_id = record["body"]["event_id"]
    event = {
        "event_id": event_id,
        "event_type": "payment.created",
        "payment_id": payment["id"],
        "occurred_at": datetime.now(UTC).isoformat(),
        "request_id": request_id,
    }
    result = rabbitmq_request(
        "POST",
        "/api/exchanges/%2F/payments/publish",
        {
            "properties": {
                "delivery_mode": 2,
                "message_id": event_id,
                "correlation_id": payment["id"],
                "type": "payment.created",
                "headers": {"x-attempt": 1, "x-request-id": request_id},
            },
            "routing_key": "payments.new",
            "payload": json.dumps(event),
            "payload_encoding": "string",
        },
    )
    assert result == {"routed": True}


def dlq_contains(payment_id: str) -> bool | None:
    messages = rabbitmq_request(
        "POST",
        "/api/queues/%2F/payments.new.dlq/get",
        {
            "count": 1000,
            "ackmode": "ack_requeue_true",
            "encoding": "auto",
            "truncate": 50000,
        },
    )
    for message in messages:
        payload = json.loads(message["payload"])
        if payload.get("payment_id") == payment_id:
            headers = message["properties"]["headers"]
            assert headers["x-error-type"]
            assert headers["x-error-reason"]
            return True
    return None


def verify_happy_path() -> None:
    request_id = f"request-happy-{uuid.uuid4()}"
    payment_id, _ = create_payment("/success", request_id)
    payment = payment_when(payment_id, lambda value: value["webhook_delivered_at"] is not None)

    assert payment["status"] in {"succeeded", "failed"}
    assert payment["webhook_attempts"] == 1
    assert payment["last_webhook_error"] is None
    records = wait_until(
        "successful webhook",
        lambda: webhook_records("/success", 1),
        timeout=10,
    )
    assert records[0]["request_id"] == request_id

    processed_at = payment["processed_at"]
    ack_before_duplicate = queue_ack_count("payments.new")
    publish_duplicate_event(payment, records[0], request_id)
    wait_until(
        "duplicate event acknowledgement",
        lambda: True if queue_ack_count("payments.new") > ack_before_duplicate else None,
        timeout=10,
    )
    duplicate_result = api_request("GET", f"/api/v1/payments/{payment_id}")
    assert duplicate_result["processed_at"] == processed_at
    assert len(webhook_records("/success", 1) or []) == 1


def verify_retry_and_dlq() -> None:
    request_id = f"request-retry-{uuid.uuid4()}"
    payment_id, _ = create_payment("/retry", request_id)
    first_attempt = payment_when(payment_id, lambda value: value["webhook_attempts"] >= 1)
    processed_at = first_attempt["processed_at"]
    payment = payment_when(payment_id, lambda value: value["webhook_attempts"] == 3)

    assert payment["status"] in {"succeeded", "failed"}
    assert payment["processed_at"] == processed_at
    assert payment["webhook_delivered_at"] is None
    assert "HTTP 503" in payment["last_webhook_error"]
    records = wait_until(
        "three retryable webhook attempts",
        lambda: webhook_records("/retry", 3),
        timeout=10,
    )
    assert all(record["request_id"] == request_id for record in records)

    intervals = [
        records[index]["received_at"] - records[index - 1]["received_at"] for index in range(1, 3)
    ]
    assert intervals[0] >= RETRY_BASE_SECONDS * 0.75
    assert intervals[1] >= RETRY_BASE_SECONDS * 2 * 0.75
    wait_until("retryable message in DLQ", lambda: dlq_contains(payment_id), timeout=10)


def verify_permanent_error_skips_retry() -> None:
    request_id = f"request-permanent-{uuid.uuid4()}"
    payment_id, _ = create_payment("/permanent", request_id)
    payment = payment_when(payment_id, lambda value: value["webhook_attempts"] == 1)

    assert payment["webhook_delivered_at"] is None
    assert "HTTP 400" in payment["last_webhook_error"]
    wait_until("permanently failed message in DLQ", lambda: dlq_contains(payment_id), timeout=10)
    time.sleep(RETRY_BASE_SECONDS + 0.5)
    records = webhook_records("/permanent", 1) or []
    assert len(records) == 1


def run_smoke() -> None:
    http_json("DELETE", f"{WEBHOOK_RECEIVER_URL}/state")
    verify_happy_path()
    verify_retry_and_dlq()
    verify_permanent_error_skips_retry()
    print("compose integration smoke passed")


if __name__ == "__main__":
    try:
        run_smoke()
    except Exception as exc:
        print(f"compose integration smoke failed: {exc}", file=sys.stderr)
        raise
