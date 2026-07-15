import pytest
from pydantic import ValidationError

from payment_service.core.config import Settings


def test_processing_range_is_validated() -> None:
    with pytest.raises(ValidationError):
        Settings(payment_processing_min_seconds=5, payment_processing_max_seconds=2)


def test_log_level_is_normalized() -> None:
    assert Settings(log_level="warning").log_level == "WARNING"


def test_local_api_key_default_matches_compose_and_smoke() -> None:
    assert Settings(_env_file=None).api_key.get_secret_value() == "local-development-key"
