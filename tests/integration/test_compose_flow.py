import os

import pytest

from scripts.smoke_test import run_smoke


@pytest.mark.integration
def test_compose_payment_flow() -> None:
    if os.getenv("RUN_COMPOSE_INTEGRATION") != "1":
        pytest.skip("set RUN_COMPOSE_INTEGRATION=1 for the Docker Compose integration test")

    run_smoke()
