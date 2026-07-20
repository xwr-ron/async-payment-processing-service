from fastapi import FastAPI, status

from payment_service.api.openapi import CREATE_PAYMENT_OPERATION_ID, build_openapi_schema
from payment_service.api.schemas import PaymentAccepted, PaymentCreate
from payment_service.main import app


def test_openapi_schema_is_generated_from_payment_routes() -> None:
    schema = build_openapi_schema(app, "ru")

    assert schema["info"]["title"] == "Async Payment Processing Service"
    assert schema["paths"]["/api/v1/payments"]["post"]["summary"] == "Создать платёж"
    assert schema["paths"]["/api/v1/payments/{payment_id}"]["get"]["summary"] == (
        "Получить состояние платежа"
    )


def test_openapi_schema_describes_api_key_authorization() -> None:
    schema = build_openapi_schema(app, "ru")
    security_schemes = schema["components"]["securitySchemes"]
    create_payment = schema["paths"]["/api/v1/payments"]["post"]

    assert security_schemes["ApiKeyAuth"] == {
        "type": "apiKey",
        "description": "Статический API-ключ клиента",
        "in": "header",
        "name": "X-API-Key",
    }
    assert create_payment["security"] == [{"ApiKeyAuth": []}]
    assert create_payment["responses"]["401"]["description"] == ("API key is missing or invalid")
    assert create_payment["responses"]["409"]["description"] == (
        "Idempotency-Key was already used with a different request body"
    )
    get_payment = schema["paths"]["/api/v1/payments/{payment_id}"]["get"]
    assert get_payment["responses"]["404"]["description"] == "Payment not found"


def test_english_openapi_schema_localizes_routes_and_models() -> None:
    schema = build_openapi_schema(app, "en")
    create_payment = schema["paths"]["/api/v1/payments"]["post"]
    properties = schema["components"]["schemas"]["PaymentCreate"]["properties"]

    assert schema["tags"] == [
        {
            "name": "Payments",
            "description": "Create payments and retrieve their asynchronous status",
        }
    ]
    assert create_payment["summary"] == "Create a payment"
    assert create_payment["tags"] == ["Payments"]
    request_id = next(
        parameter
        for parameter in create_payment["parameters"]
        if parameter["name"] == "X-Request-ID"
    )
    assert request_id["description"] == "Optional identifier for end-to-end request tracing"
    assert properties["amount"]["description"] == "Payment amount with two decimal places"
    assert properties["description"]["description"] == "Payment description"
    detail = schema["components"]["schemas"]["PaymentDetail"]["properties"]
    assert detail["last_webhook_error"]["description"].startswith("Most recent webhook error")
    assert schema["components"]["securitySchemes"]["ApiKeyAuth"]["description"] == (
        "Static client API key"
    )


def test_openapi_localization_follows_operation_id_after_path_change() -> None:
    renamed_app = FastAPI(version="test")

    @renamed_app.post(
        "/renamed-payments",
        operation_id=CREATE_PAYMENT_OPERATION_ID,
        response_model=PaymentAccepted,
        status_code=status.HTTP_202_ACCEPTED,
    )
    async def renamed_create_payment(_: PaymentCreate) -> PaymentAccepted:
        raise NotImplementedError

    schema = build_openapi_schema(renamed_app, "en")
    operation = schema["paths"]["/renamed-payments"]["post"]
    request_ref = operation["requestBody"]["content"]["application/json"]["schema"]["$ref"]
    request_schema = schema["components"]["schemas"][request_ref.rsplit("/", 1)[-1]]

    assert operation["summary"] == "Create a payment"
    assert request_schema["examples"][0]["webhook_url"] == ("http://webhook-sink:8080/success")
