from enum import StrEnum


class Currency(StrEnum):
    """Поддерживаемая валюта платежа"""

    RUB = "RUB"
    USD = "USD"
    EUR = "EUR"


class PaymentStatus(StrEnum):
    """Состояние асинхронной обработки платежа"""

    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
