from __future__ import annotations

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import threading
from time import perf_counter

import httpx

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.llm_client import VllmChatClient
from sage_faculty_twin.request_context import (
    RequestCancellationController,
    request_cancellation_scope,
)


class _SlowHeaderHandler(BaseHTTPRequestHandler):
    entered = threading.Event()
    release = threading.Event()

    def do_POST(self) -> None:  # noqa: N802 - stdlib handler API
        self.entered.set()
        self.release.wait(timeout=10.0)
        try:
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"{}")
        except OSError:
            pass

    def log_message(self, *_args) -> None:
        return


def test_request_cancellation_interrupts_pre_header_http_read_within_five_seconds() -> None:
    _SlowHeaderHandler.entered.clear()
    _SlowHeaderHandler.release.clear()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _SlowHeaderHandler)
    server.daemon_threads = True
    threading.Thread(target=server.serve_forever, daemon=True).start()

    settings = AppSettings(
        llm_base_url=f"http://127.0.0.1:{server.server_port}",
        api_key="test-key",
        model_name="test-model",
        llm_timeout_seconds=60,
    )
    client = VllmChatClient.__new__(VllmChatClient)
    client._settings = settings
    client._client = client._build_completion_client()
    controller = RequestCancellationController()
    errors: list[Exception] = []

    def run_blocked_request() -> None:
        try:
            with request_cancellation_scope(
                controller,
                deadline_at=perf_counter() + 60.0,
            ):
                with client._request_completion_client() as request_client:
                    request_client.post("/chat/completions", json={"model": "test"})
        except Exception as exc:
            errors.append(exc)

    worker = threading.Thread(target=run_blocked_request)
    worker.start()
    try:
        assert _SlowHeaderHandler.entered.wait(timeout=2.0)
        cancelled_at = perf_counter()
        controller.cancel()
        worker.join(timeout=5.0)
        cleanup_seconds = perf_counter() - cancelled_at

        assert not worker.is_alive()
        assert cleanup_seconds < 5.0
        assert errors and isinstance(errors[0], httpx.TransportError)
    finally:
        _SlowHeaderHandler.release.set()
        client._client.close()
        server.shutdown()
        server.server_close()
