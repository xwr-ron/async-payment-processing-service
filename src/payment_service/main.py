import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from fastapi.openapi.docs import get_swagger_ui_html
from fastapi.responses import JSONResponse
from faststream.rabbit import RabbitBroker
from sqlalchemy import text
from starlette.responses import Response

from payment_service.api.dependencies import require_api_key
from payment_service.api.openapi import DocumentationLanguage, build_openapi_schema
from payment_service.api.v1.router import router as v1_router
from payment_service.core.config import get_settings
from payment_service.core.constants import REQUEST_ID_MAX_LENGTH
from payment_service.core.logging import configure_logging
from payment_service.db.session import engine, session_factory
from payment_service.messaging import declare_payment_topology
from payment_service.services.outbox import OutboxRelay

settings = get_settings()
configure_logging(settings.log_level)
logger = logging.getLogger(__name__)
outbox_broker = RabbitBroker(settings.rabbitmq_url, fail_fast=False)


async def run_outbox_worker() -> None:
    while True:
        try:
            await outbox_broker.start()
            await declare_payment_topology(outbox_broker, settings)
            await OutboxRelay(session_factory, outbox_broker, settings).run()
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception("outbox broker initialization failed")
            await asyncio.sleep(settings.outbox_poll_interval_seconds)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    relay_task = asyncio.create_task(run_outbox_worker(), name="outbox-relay")

    try:
        yield
    finally:
        relay_task.cancel()
        await asyncio.gather(relay_task, return_exceptions=True)
        await outbox_broker.stop()
        await engine.dispose()


app = FastAPI(
    title="Async Payment Processing Service",
    version="0.1.0",
    summary="Асинхронный API для создания и отслеживания платежей.",
    description=(
        "Платёж сохраняется вместе с outbox-событием и обрабатывается consumer в RabbitMQ. "
        "Для всех прикладных endpoint используйте `X-API-Key`; в Swagger UI ключ задаётся "
        "через кнопку **Authorize**."
    ),
    openapi_tags=[
        {
            "name": "Платежи",
            "description": "Создание платежей и получение их асинхронного статуса.",
        }
    ],
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(v1_router)


@app.get("/openapi.json", include_in_schema=False)
async def openapi_russian() -> JSONResponse:
    return _openapi_response("ru")


@app.get("/openapi.en.json", include_in_schema=False)
async def openapi_english() -> JSONResponse:
    return _openapi_response("en")


@app.get("/docs", include_in_schema=False)
async def swagger_russian() -> Response:
    return _swagger_ui("/openapi.json", "Swagger UI — Русский")


@app.get("/docs/en", include_in_schema=False)
async def swagger_english() -> Response:
    return _swagger_ui("/openapi.en.json", "Swagger UI — English")


def _openapi_response(language: DocumentationLanguage) -> JSONResponse:
    # Схема каждый раз строится по текущему набору FastAPI-маршрутов
    # Перевод не дублирует контракт, а изменяет только отображаемые в документации тексты
    return JSONResponse(build_openapi_schema(app, language))


def _swagger_ui(openapi_url: str, title: str) -> Response:
    return get_swagger_ui_html(
        openapi_url=openapi_url,
        title=title,
        swagger_ui_parameters={"persistAuthorization": True},
    )


@app.middleware("http")
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    supplied_request_id = request.headers.get("X-Request-ID")

    request_id = (
        supplied_request_id
        if supplied_request_id and len(supplied_request_id) <= REQUEST_ID_MAX_LENGTH
        else str(uuid.uuid4())
    )

    request.state.request_id = request_id

    response = await call_next(request)
    response.headers["X-Request-ID"] = request_id

    return response


@app.get("/health/live", dependencies=[Depends(require_api_key)], include_in_schema=False)
async def liveness() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/health/ready", dependencies=[Depends(require_api_key)], include_in_schema=False)
async def readiness() -> dict[str, str]:
    async with engine.connect() as connection:
        await connection.execute(text("SELECT 1"))
    return {"status": "ok"}
