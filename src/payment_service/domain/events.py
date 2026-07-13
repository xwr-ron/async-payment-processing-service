import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict


class PaymentCreatedEvent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: uuid.UUID
    event_type: Literal["payment.created"] = "payment.created"
    payment_id: uuid.UUID
    occurred_at: datetime
