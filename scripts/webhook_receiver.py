"""Управляемый HTTP-приёмник для локальных Compose-интеграционных тестов"""

import json
import threading
import time
from collections import defaultdict
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, ClassVar


class WebhookHandler(BaseHTTPRequestHandler):
    """Принимает webhook и возвращает управляемые ответы для integration-теста"""

    records: ClassVar[dict[str, list[dict[str, Any]]]] = defaultdict(list)
    lock: ClassVar[threading.Lock] = threading.Lock()

    def do_GET(self) -> None:
        if self.path == "/health":
            self._send_json(HTTPStatus.OK, {"status": "ok"})
            return

        if self.path == "/state":
            with self.lock:
                snapshot = {path: list(records) for path, records in self.records.items()}
            self._send_json(HTTPStatus.OK, snapshot)
            return

        self._send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})

    def do_DELETE(self) -> None:
        if self.path != "/state":
            self._send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return

        with self.lock:
            self.records.clear()

        self._send_json(HTTPStatus.OK, {"status": "cleared"})

    def do_POST(self) -> None:
        response_status = {
            "/success": HTTPStatus.NO_CONTENT,
            "/retry": HTTPStatus.SERVICE_UNAVAILABLE,
            "/permanent": HTTPStatus.BAD_REQUEST,
        }.get(self.path)
        if response_status is None:
            self._send_json(HTTPStatus.NOT_FOUND, {"detail": "not found"})
            return

        content_length = int(self.headers.get("Content-Length", "0"))
        raw_body = self.rfile.read(content_length)
        body = json.loads(raw_body) if raw_body else None

        record = {
            "received_at": time.time(),
            "request_id": self.headers.get("X-Request-ID"),
            "event_id": self.headers.get("X-Webhook-Event-ID"),
            "body": body,
        }

        with self.lock:
            self.records[self.path].append(record)

        if response_status is HTTPStatus.NO_CONTENT:
            self.send_response(response_status)
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        self._send_json(response_status, {"detail": response_status.phrase})

    def log_message(self, format: str, *args: object) -> None:
        del format, args

    def _send_json(self, status: HTTPStatus, payload: object) -> None:
        body = json.dumps(payload).encode()

        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()

        self.wfile.write(body)


def main() -> None:
    server = ThreadingHTTPServer(("0.0.0.0", 8080), WebhookHandler)
    server.serve_forever()


if __name__ == "__main__":
    main()
