import json
import logging

from payment_service.core.logging import JsonFormatter, configure_logging


def test_json_formatter_includes_context_and_exception() -> None:
    try:
        raise ValueError("broken")
    except ValueError:
        record = logging.getLogger("test").makeRecord(
            "test",
            logging.ERROR,
            __file__,
            1,
            "failed %s",
            ("request",),
            __import__("sys").exc_info(),
            extra={"payment_id": "42"},
        )

    payload = json.loads(JsonFormatter().format(record))
    assert payload["message"] == "failed request"
    assert payload["payment_id"] == "42"
    assert "ValueError: broken" in payload["exception"]


def test_configure_logging_replaces_root_handler() -> None:
    configure_logging("WARNING")
    root = logging.getLogger()
    assert root.level == logging.WARNING
    assert len(root.handlers) == 1
