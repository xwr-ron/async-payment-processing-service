from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from payment_service.api.dependencies import require_api_key
from payment_service.api.schemas import PaymentDetail
from payment_service.api.v1.payments import get_payment, to_accepted, to_detail
from payment_service.core.config import Settings
from payment_service.services.exceptions import PaymentNotFoundError
from tests.unit.conftest import make_payment


async def test_api_key_accepts_expected_secret() -> None:
    assert await require_api_key(Settings(api_key="secret"), "secret") is None


@pytest.mark.parametrize("provided", [None, "wrong"])
async def test_api_key_rejects_missing_or_wrong_secret(provided: str | None) -> None:
    with pytest.raises(HTTPException) as caught:
        await require_api_key(Settings(api_key="secret"), provided)
    assert caught.value.status_code == 401
    assert caught.value.detail == "API key is missing or invalid"


def test_payment_response_mappers() -> None:
    payment = make_payment()

    accepted = to_accepted(payment)
    detail = to_detail(payment)

    assert accepted.payment_id == payment.id
    assert isinstance(detail, PaymentDetail)
    assert detail.metadata == {"order_id": 42}
    assert detail.model_dump(mode="json")["amount"] == "125.50"
    assert detail.last_webhook_error is None


async def test_get_endpoint_maps_missing_payment_to_404() -> None:
    with (
        patch(
            "payment_service.api.v1.payments.PaymentService.get",
            AsyncMock(side_effect=PaymentNotFoundError),
        ),
        pytest.raises(HTTPException) as caught,
    ):
        await get_payment(make_payment().id, object(), None)  # type: ignore[arg-type]
    assert caught.value.status_code == 404
    assert caught.value.detail == "Payment not found"
