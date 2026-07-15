from decimal import Decimal

import pytest
from pydantic import ValidationError

from payment_service.api.schemas import PaymentCreate
from payment_service.domain.enums import Currency
from payment_service.services.payments import request_fingerprint


def payment_data(**overrides: object) -> dict[str, object]:
    data: dict[str, object] = {
        "amount": "125.50",
        "currency": "RUB",
        "description": "Order #42",
        "metadata": {"order_id": 42},
        "webhook_url": "https://merchant.example/webhooks/payments",
    }
    data.update(overrides)
    return data


def test_payment_create_normalizes_contract() -> None:
    payment = PaymentCreate.model_validate(payment_data())

    assert payment.amount == Decimal("125.50")
    assert payment.currency is Currency.RUB


@pytest.mark.parametrize("amount", ["0", "-1", "1.001"])
def test_payment_create_rejects_invalid_amount(amount: str) -> None:
    with pytest.raises(ValidationError):
        PaymentCreate.model_validate(payment_data(amount=amount))


def test_payment_create_forbids_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PaymentCreate.model_validate(payment_data(unexpected=True))


def test_payment_create_rejects_non_json_metadata() -> None:
    with pytest.raises(ValidationError):
        PaymentCreate.model_validate(payment_data(metadata={"value": float("nan")}))


def test_fingerprint_is_independent_of_metadata_key_order() -> None:
    first = PaymentCreate.model_validate(payment_data(metadata={"a": 1, "b": 2}))
    second = PaymentCreate.model_validate(payment_data(metadata={"b": 2, "a": 1}))

    assert request_fingerprint(first) == request_fingerprint(second)


def test_fingerprint_changes_with_request() -> None:
    first = PaymentCreate.model_validate(payment_data())
    second = PaymentCreate.model_validate(payment_data(amount="126.00"))

    assert request_fingerprint(first) != request_fingerprint(second)
