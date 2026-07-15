import uuid
from datetime import datetime
from decimal import Decimal

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, JsonValue, field_serializer

from payment_service.domain.enums import Currency, PaymentStatus


class PaymentCreate(BaseModel):
    """Входные данные для создания платежа"""

    model_config = ConfigDict(
        extra="forbid",
        allow_inf_nan=False,
        json_schema_extra={
            "examples": [
                {
                    "amount": "125.50",
                    "currency": "RUB",
                    "description": "Оплата заказа #42",
                    "metadata": {"order_id": 42},
                    "webhook_url": "http://webhook-sink:8080/success",
                }
            ]
        },
    )

    # Денежную сумму намеренно не храним в float: IEEE 754 представляет число
    # двоичной дробью, поэтому обычные десятичные значения вроде 0.1 зачастую
    # получаются приближёнными и накапливают ошибку при вычислениях. Decimal
    # сохраняет точное десятичное значение и позволяет явно ограничить масштаб
    # двумя знаками после запятой
    amount: Decimal = Field(
        gt=0,
        max_digits=18,
        decimal_places=2,
        description="Сумма платежа. Используется decimal-представление с точностью до копеек",
        examples=["125.50"],
    )
    currency: Currency = Field(description="Валюта платежа: RUB, USD или EUR.", examples=["RUB"])
    description: str = Field(
        min_length=1,
        max_length=500,
        description="Описание платежа, видимое получателю webhook",
        examples=["Оплата заказа #42"],
    )
    metadata: dict[str, JsonValue] = Field(
        default_factory=dict,
        description="Произвольные JSON-совместимые данные клиента",
        examples=[{"order_id": 42}],
    )
    webhook_url: HttpUrl = Field(
        description="HTTPS/HTTP URL для асинхронного уведомления о результате",
        examples=["http://webhook-sink:8080/success"],
    )


class PaymentAccepted(BaseModel):
    """Ответ о принятии платежа в асинхронную обработку"""

    payment_id: uuid.UUID
    status: PaymentStatus
    created_at: datetime


class PaymentDetail(BaseModel):
    """Текущее состояние платежа и доставки его webhook"""

    id: uuid.UUID
    amount: Decimal
    currency: Currency
    description: str
    metadata: dict[str, JsonValue]
    status: PaymentStatus
    webhook_url: str
    created_at: datetime
    processed_at: datetime | None
    webhook_delivered_at: datetime | None
    webhook_attempts: int = Field(description="Количество выполненных попыток webhook")
    last_webhook_error: str | None = Field(
        description="Текст последней ошибки webhook или null после успешной доставки"
    )

    @field_serializer("amount")
    def serialize_amount(self, value: Decimal) -> str:
        return format(value, ".2f")


class ErrorResponse(BaseModel):
    """Стандартный ответ с прикладной ошибкой API"""

    detail: str
