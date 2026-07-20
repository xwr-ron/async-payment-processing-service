import pytest
from pydantic import ValidationError

from payment_service.core.config import Settings
from payment_service.core.constants import DEFAULT_LOCAL_API_KEY


def test_processing_range_is_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(payment_processing_min_seconds=5, payment_processing_max_seconds=2)


def test_log_level_is_normalized() -> None:
    assert Settings(log_level="warning").log_level == "WARNING"


def test_app_env_is_normalized() -> None:
    assert Settings(app_env="PRODUCTION", api_key="secret").app_env == "production"


def test_local_api_key_default_matches_compose_and_smoke() -> None:
    assert Settings(_env_file=None).api_key.get_secret_value() == DEFAULT_LOCAL_API_KEY


def test_default_api_key_is_rejected_outside_local_environment() -> None:
    with pytest.raises(ValidationError, match="API_KEY must be changed"):
        Settings(_env_file=None, app_env="production")
