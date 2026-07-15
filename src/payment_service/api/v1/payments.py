import uuid
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status

from payment_service.api.dependencies import AuthDep, SessionDep
from payment_service.api.openapi import CREATE_PAYMENT_OPERATION_ID, GET_PAYMENT_OPERATION_ID
from payment_service.api.schemas import (
    ErrorResponse,
    PaymentAccepted,
    PaymentCreate,
    PaymentDetail,
)
from payment_service.core.constants import REQUEST_ID_MAX_LENGTH
from payment_service.domain.models import Payment
from payment_service.services.exceptions import IdempotencyConflictError, PaymentNotFoundError
from payment_service.services.payments import PaymentService

router = APIRouter(prefix="/payments", tags=["Платежи"])


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
        last_webhook_error=payment.last_webhook_error,
    )


@router.post(
    "",
    operation_id=CREATE_PAYMENT_OPERATION_ID,
    status_code=status.HTTP_202_ACCEPTED,
    response_model=PaymentAccepted,
    summary="Создать платёж",
    description=(
        "Создаёт платёж в статусе `pending` и ставит его в асинхронную обработку. "
        "Заголовок `Idempotency-Key` обязателен: повтор с тем же телом вернёт исходный "
        "платёж, а с изменённым телом — `409 Conflict`"
    ),
    response_description="Платёж принят и ожидает асинхронной обработки",
    responses={
        401: {"model": ErrorResponse, "description": "API key is missing or invalid"},
        409: {
            "model": ErrorResponse,
            "description": "Idempotency-Key was already used with a different request body",
        },
    },
)
async def create_payment(
    body: PaymentCreate,
    request: Request,
    session: SessionDep,
    _auth: AuthDep,
    idempotency_key: Annotated[str, Header(alias="Idempotency-Key", min_length=1, max_length=255)],
    _request_id_header: Annotated[
        str | None,
        Header(
            alias="X-Request-ID",
            max_length=REQUEST_ID_MAX_LENGTH,
            description="Опциональный идентификатор для сквозной трассировки запроса",
        ),
    ] = None,
) -> PaymentAccepted:
    try:
        payment = await PaymentService(session).create(
            body,
            idempotency_key,
            request.state.request_id,
        )
    except IdempotencyConflictError as exc:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail="Idempotency-Key was already used with a different request body",
        ) from exc

    return to_accepted(payment)


@router.get(
    "/{payment_id}",
    operation_id=GET_PAYMENT_OPERATION_ID,
    response_model=PaymentDetail,
    summary="Получить состояние платежа",
    description=(
        "Возвращает текущий статус платежа, результат обработки и сведения о доставке webhook"
    ),
    responses={
        401: {"model": ErrorResponse, "description": "API key is missing or invalid"},
        404: {"model": ErrorResponse, "description": "Payment was not found"},
    },
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
