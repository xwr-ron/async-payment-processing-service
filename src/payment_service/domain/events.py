import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from payment_service.core.constants import REQUEST_ID_MAX_LENGTH


class PaymentCreatedEvent(BaseModel):
    """Событие создания платежа для асинхронной обработки"""

    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID
    event_type: Literal["payment.created"] = "payment.created"
    payment_id: uuid.UUID
    occurred_at: datetime
    request_id: str = Field(min_length=1, max_length=REQUEST_ID_MAX_LENGTH)
