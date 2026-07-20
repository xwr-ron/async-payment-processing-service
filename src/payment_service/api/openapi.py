"""Построение локализованных OpenAPI-схем из актуальных маршрутов приложения"""

from typing import Any, Literal

from fastapi import FastAPI
from fastapi.openapi.utils import get_openapi

DocumentationLanguage = Literal["ru", "en"]
CREATE_PAYMENT_OPERATION_ID = "payments.create"
GET_PAYMENT_OPERATION_ID = "payments.get"

_HTTP_METHODS = frozenset({"get", "post", "put", "patch", "delete", "options", "head", "trace"})

_TEXT: dict[DocumentationLanguage, dict[str, str]] = {
    "ru": {
        "title": "Async Payment Processing Service",
        "summary": "Асинхронный API для создания и отслеживания платежей",
        "description": (
            "Платёж сохраняется вместе с outbox-событием и обрабатывается consumer в RabbitMQ. "
            "Для всех прикладных endpoint используйте `X-API-Key`; в Swagger UI ключ задаётся "
            "через кнопку **Authorize**"
        ),
        "tag_name": "Платежи",
        "tag_description": "Создание платежей и получение их асинхронного статуса",
        "security_description": "Статический API-ключ клиента",
        "create_summary": "Создать платёж",
        "create_description": (
            "Создаёт платёж в статусе `pending` и ставит его в асинхронную обработку. "
            "Заголовок `Idempotency-Key` обязателен: повтор с тем же телом вернёт исходный "
            "платёж, а с изменённым телом — `409 Conflict`"
        ),
        "create_response": "Платёж принят и ожидает асинхронной обработки",
        "get_summary": "Получить состояние платежа",
        "get_description": "Возвращает текущий статус платежа и состояние доставки webhook",
        "unauthorized": "API key is missing or invalid",
        "idempotency_conflict": ("Idempotency-Key was already used with a different request body"),
        "request_id": "Опциональный идентификатор для сквозной трассировки запроса",
        "not_found": "Payment not found",
        "amount": "Сумма платежа с точностью до копеек",
        "currency": "Валюта платежа: RUB, USD или EUR",
        "payment_description": "Описание платежа",
        "metadata": "Произвольные JSON-совместимые данные клиента",
        "webhook_url": "HTTPS/HTTP URL для асинхронного уведомления о результате",
        "accepted_schema": "Ответ о принятии платежа в асинхронную обработку",
        "detail_schema": "Текущее состояние платежа и доставки его webhook",
        "webhook_attempts": "Количество выполненных попыток webhook",
        "last_webhook_error": ("Текст последней ошибки webhook или null после успешной доставки"),
        "error_schema": "Standard application API error response",
        "example_description": "Оплата заказа #42",
    },
    "en": {
        "title": "Async Payment Processing Service",
        "summary": "Asynchronous API for creating and tracking payments",
        "description": (
            "A payment is persisted together with an outbox event and processed by a RabbitMQ "
            "consumer. Use `X-API-Key` for every application endpoint; set it with the "
            "**Authorize** button in Swagger UI"
        ),
        "tag_name": "Payments",
        "tag_description": "Create payments and retrieve their asynchronous status",
        "security_description": "Static client API key",
        "create_summary": "Create a payment",
        "create_description": (
            "Creates a payment in `pending` status and schedules asynchronous processing. "
            "`Idempotency-Key` is required: a repeated identical request returns the original "
            "payment, while a changed request returns `409 Conflict`"
        ),
        "create_response": "Payment accepted for asynchronous processing",
        "get_summary": "Get payment status",
        "get_description": "Returns the current payment status and webhook delivery state",
        "unauthorized": "API key is missing or invalid",
        "idempotency_conflict": ("Idempotency-Key was already used with a different request body"),
        "request_id": "Optional identifier for end-to-end request tracing",
        "not_found": "Payment not found",
        "amount": "Payment amount with two decimal places",
        "currency": "Payment currency: RUB, USD, or EUR",
        "payment_description": "Payment description",
        "metadata": "Arbitrary JSON-compatible client data",
        "webhook_url": "HTTPS/HTTP URL for the asynchronous result notification",
        "accepted_schema": "Response confirming asynchronous payment acceptance",
        "detail_schema": "Current payment and webhook delivery state",
        "webhook_attempts": "Number of webhook delivery attempts performed",
        "last_webhook_error": ("Most recent webhook error, or null after successful delivery"),
        "error_schema": "Standard application error response",
        "example_description": "Order #42 payment",
    },
}


def build_openapi_schema(app: FastAPI, language: DocumentationLanguage) -> dict[str, Any]:
    """Создаёт OpenAPI по живым маршрутам и накладывает перевод документации"""
    text = _TEXT[language]
    schema = get_openapi(
        title=text["title"],
        version=app.version,
        summary=text["summary"],
        description=text["description"],
        routes=app.routes,
        tags=[{"name": text["tag_name"], "description": text["tag_description"]}],
    )

    create_payment, get_payment = _localize_payment_operations(schema, text)
    _localize_security_scheme(schema, text, create_payment)
    _localize_payment_schemas(schema, text, create_payment, get_payment)
    return schema


def _operation_by_id(schema: dict[str, Any], operation_id: str) -> dict[str, Any] | None:
    for path_item in schema.get("paths", {}).values():
        if not isinstance(path_item, dict):
            continue

        for method, operation in path_item.items():
            if (
                method in _HTTP_METHODS
                and isinstance(operation, dict)
                and operation.get("operationId") == operation_id
            ):
                return operation

    return None


def _localize_security_scheme(
    schema: dict[str, Any],
    text: dict[str, str],
    operation: dict[str, Any] | None,
) -> None:
    if operation is None:
        return

    security = operation.get("security", [])
    if not security or not isinstance(security[0], dict):
        return

    scheme_name = next(iter(security[0]), None)
    security_schemes = schema.get("components", {}).get("securitySchemes", {})
    scheme = security_schemes.get(scheme_name)
    if isinstance(scheme, dict):
        scheme["description"] = text["security_description"]


def _localize_payment_operations(
    schema: dict[str, Any], text: dict[str, str]
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    create_payment = _operation_by_id(schema, CREATE_PAYMENT_OPERATION_ID)
    if create_payment is not None:
        create_payment.update(
            {
                "tags": [text["tag_name"]],
                "summary": text["create_summary"],
                "description": text["create_description"],
            }
        )
        _set_response_description(create_payment, "202", text["create_response"])
        _set_response_description(create_payment, "401", text["unauthorized"])
        _set_response_description(create_payment, "409", text["idempotency_conflict"])

        for parameter in create_payment.get("parameters", []):
            if isinstance(parameter, dict) and parameter.get("name") == "X-Request-ID":
                parameter["description"] = text["request_id"]

    get_payment = _operation_by_id(schema, GET_PAYMENT_OPERATION_ID)
    if get_payment is not None:
        get_payment.update(
            {
                "tags": [text["tag_name"]],
                "summary": text["get_summary"],
                "description": text["get_description"],
            }
        )
        _set_response_description(get_payment, "401", text["unauthorized"])
        _set_response_description(get_payment, "404", text["not_found"])

    return create_payment, get_payment


def _set_response_description(
    operation: dict[str, Any], status_code: str, description: str
) -> None:
    response = operation.get("responses", {}).get(status_code)
    if isinstance(response, dict):
        response["description"] = description


def _localize_payment_schemas(
    schema: dict[str, Any],
    text: dict[str, str],
    create_payment: dict[str, Any] | None,
    get_payment: dict[str, Any] | None,
) -> None:
    payment_create = _request_schema(schema, create_payment)
    if payment_create is not None:
        _localize_properties(
            payment_create,
            {
                "amount": text["amount"],
                "currency": text["currency"],
                "description": text["payment_description"],
                "metadata": text["metadata"],
                "webhook_url": text["webhook_url"],
            },
        )
        payment_create["examples"] = [
            {
                "amount": "125.50",
                "currency": "RUB",
                "description": text["example_description"],
                "metadata": {"order_id": 42},
                "webhook_url": "http://webhook-sink:8080/success",
            }
        ]

    payment_accepted = _response_schema(schema, create_payment, "202")
    if payment_accepted is not None:
        payment_accepted["description"] = text["accepted_schema"]

    payment_detail = _response_schema(schema, get_payment, "200")
    if payment_detail is not None:
        payment_detail["description"] = text["detail_schema"]
        _localize_properties(
            payment_detail,
            {
                "webhook_attempts": text["webhook_attempts"],
                "last_webhook_error": text["last_webhook_error"],
            },
        )

    error_response = _response_schema(schema, create_payment, "401")
    if error_response is not None:
        error_response["description"] = text["error_schema"]


def _localize_properties(component: dict[str, Any], descriptions: dict[str, str]) -> None:
    properties = component.get("properties", {})
    for field_name, description in descriptions.items():
        field = properties.get(field_name)
        if isinstance(field, dict):
            field["description"] = description


def _request_schema(
    schema: dict[str, Any], operation: dict[str, Any] | None
) -> dict[str, Any] | None:
    if operation is None:
        return None

    request_schema = (
        operation.get("requestBody", {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
    )
    return _resolve_schema_reference(schema, request_schema)


def _response_schema(
    schema: dict[str, Any], operation: dict[str, Any] | None, status_code: str
) -> dict[str, Any] | None:
    if operation is None:
        return None

    response_schema = (
        operation.get("responses", {})
        .get(status_code, {})
        .get("content", {})
        .get("application/json", {})
        .get("schema")
    )
    return _resolve_schema_reference(schema, response_schema)


def _resolve_schema_reference(schema: dict[str, Any], reference: Any) -> dict[str, Any] | None:
    if not isinstance(reference, dict):
        return None

    ref = reference.get("$ref")
    prefix = "#/components/schemas/"
    if not isinstance(ref, str) or not ref.startswith(prefix):
        return reference

    component_name = ref.removeprefix(prefix).replace("~1", "/").replace("~0", "~")
    component = schema.get("components", {}).get("schemas", {}).get(component_name)
    return component if isinstance(component, dict) else None
