from functools import lru_cache

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from payment_service.core.constants import DEFAULT_LOCAL_API_KEY


class Settings(BaseSettings):
    """Настройки приложения из переменных окружения и файла .env"""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
        case_sensitive=False,
    )

    log_level: str = "INFO"
    app_env: str = "local"
    app_name: str = "async-payment-processing-service"
    api_key: SecretStr = SecretStr(DEFAULT_LOCAL_API_KEY)

    database_url: str = "postgresql+asyncpg://payments:payments@localhost:5432/payments"
    rabbitmq_url: str = "amqp://payments:payments@localhost:5672/"

    outbox_poll_interval_seconds: float = Field(default=1.0, gt=0)
    outbox_batch_size: int = Field(default=100, ge=1, le=1000)
    outbox_publish_timeout_seconds: float = Field(default=5.0, gt=0)

    webhook_timeout_seconds: float = Field(default=10.0, gt=0)
    webhook_allow_private_hosts: bool = False

    payment_processing_min_seconds: float = Field(default=2.0, ge=0)
    payment_processing_max_seconds: float = Field(default=5.0, ge=0)

    consumer_max_attempts: int = Field(default=3, ge=1, le=10)
    consumer_retry_base_seconds: float = Field(default=2.0, gt=0)

    @field_validator("log_level")
    @classmethod
    def normalize_log_level(cls, value: str) -> str:
        return value.upper()

    @field_validator("app_env")
    @classmethod
    def normalize_app_env(cls, value: str) -> str:
        return value.lower()

    @field_validator("payment_processing_max_seconds")
    @classmethod
    def validate_processing_range(cls, value: float, info: ValidationInfo) -> float:
        minimum = info.data.get("payment_processing_min_seconds", 0)

        if value < minimum:
            raise ValueError("must be greater than or equal to payment_processing_min_seconds")

        return value

    @model_validator(mode="after")
    def validate_non_local_api_key(self) -> "Settings":
        if self.app_env != "local" and self.api_key.get_secret_value() == DEFAULT_LOCAL_API_KEY:
            raise ValueError("API_KEY must be changed outside local environment")

        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
