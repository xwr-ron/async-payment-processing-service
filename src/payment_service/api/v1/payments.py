import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, status

from payment_service.api.dependencies import AuthDep, SessionDep
from payment_service.api.schemas import (
    ErrorResponse,
    PaymentAccepted,
    PaymentCreate,
    PaymentDetail,
)
from payment_service.domain.models import Payment
from payment_service.services.exceptions import IdempotencyConflictError, PaymentNotFoundError
from payment_service.services.payments import PaymentService

router = APIRouter(prefix="/payments", tags=["payments"])


def to_accepted(payment: Payment) -> PaymentAccepted:
    return PaymentAccepted(
        payment_id=payment.id,
        status=payment.status_value,
        created_at=payment.created_at,
    )


def to_detail(payment: Payment) -> PaymentDetail:
    return PaymentDetail(
        id=payment.id,
        amount=payment.amount,
        currency=payment.currency_value,
        description=payment.description,
        metadata=payment.metadata_,
        status=payment.status_value,
        webhook_url=payment.webhook_url,
        created_at=payment.created_at,
        processed_at=payment.processed_at,
        webhook_delivered_at=payment.webhook_delivered_at,
        webhook_attempts=payment.webhook_attempts,
    )


@router.post("",
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PaymentAccepted,
    responses={401: {"model": ErrorResponse}, 409: {"model": ErrorResponse}},
)
async def create_payment(
    body: PaymentCreate,
    session: SessionDep,
    _auth: AuthDep,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=1, max_length=255),
    ],
) -> PaymentAccepted:
    try:
        payment = await PaymentService(session).create(body, idempotency_key)
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was already used with a different request",
        ) from exc

    return to_accepted(payment)


@router.get("/{payment_id}",
    response_model=PaymentDetail,
    responses={401: {"model": ErrorResponse}, 404: {"model": ErrorResponse}},
)
async def get_payment(
    payment_id: uuid.UUID,
    session: SessionDep,
    _auth: AuthDep,
) -> PaymentDetail:
    try:
        payment = await PaymentService(session).get(payment_id)
    except PaymentNotFoundError as exc:
        raise HTTPException(status_code=404, detail="Payment not found") from exc

    return to_detail(payment)
