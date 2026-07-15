import secrets
from typing import Annotated

from fastapi import Depends, HTTPException, Security, status
from fastapi.security import APIKeyHeader
from sqlalchemy.ext.asyncio import AsyncSession

from payment_service.core.config import Settings, get_settings
from payment_service.db.session import get_session

SessionDep = Annotated[AsyncSession, Depends(get_session)]
SettingsDep = Annotated[Settings, Depends(get_settings)]

# Security-схема одновременно проверяет API-ключ и добавляет кнопку Authorize
# в Swagger UI. FastAPI включает её в OpenAPI автоматически
api_key_scheme = APIKeyHeader(
    name="X-API-Key",
    scheme_name="ApiKeyAuth",
    description="Статический API-ключ клиента",
    auto_error=False,
)


async def require_api_key(
    settings: SettingsDep,
    x_api_key: Annotated[str | None, Security(api_key_scheme)],
) -> None:
    expected = settings.api_key.get_secret_value()
    if x_api_key is None or not secrets.compare_digest(x_api_key, expected):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="API key is missing or invalid",
            headers={"WWW-Authenticate": "ApiKey"},
        )


AuthDep = Annotated[None, Depends(require_api_key)]
