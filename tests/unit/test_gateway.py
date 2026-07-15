from typing import cast

import pytest

from payment_service.core.config import Settings
from payment_service.domain.enums import PaymentStatus
from payment_service.services.gateway import EmulatedPaymentGateway


class FakeRandom:
    def __init__(self, outcome: float) -> None:
        self.outcome = outcome

    def uniform(self, minimum: float, maximum: float) -> float:
        assert minimum == maximum == 0
        return 0

    def random(self) -> float:
        return self.outcome


@pytest.mark.parametrize(
    ("sample", "expected"),
    [(0.0, PaymentStatus.SUCCEEDED), (0.899, PaymentStatus.SUCCEEDED), (0.9, PaymentStatus.FAILED)],
)
async def test_gateway_outcome(sample: float, expected: PaymentStatus) -> None:
    settings = Settings(payment_processing_min_seconds=0, payment_processing_max_seconds=0)
    gateway = EmulatedPaymentGateway(settings, cast("object", FakeRandom(sample)))

    assert await gateway.process(__import__("uuid").uuid4()) is expected
