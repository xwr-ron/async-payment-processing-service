import asyncio
import logging
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager

from fastapi import Depends, FastAPI, Request
from faststream.rabbit import RabbitBroker
from sqlalchemy import text
from starlette.responses import Response

from payment_service.api.dependencies import require_api_key
from payment_service.api.v1.router import router as v1_router
from payment_service.core.config import get_settings
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
    lifespan=lifespan,
    docs_url=None,
    redoc_url=None,
    openapi_url=None,
)
app.include_router(v1_router)


@app.middleware("http")
async def request_id_middleware(
    request: Request, call_next: Callable[[Request], Awaitable[Response]]
) -> Response:
    request_id = request.headers.get("X-Request-ID", str(uuid.uuid4()))
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
