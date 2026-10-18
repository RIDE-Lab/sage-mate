"""Real loopback socket faults; never contact configured model services."""
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Event, Thread
from time import monotonic

import httpx
import numpy as np
import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import OpenAIReranker, OpenAITextEmbedder


@pytest.mark.parametrize("kind", ["embedding", "reranker"])
def test_read_timeout_then_same_client_recovers(kind):
    entered, release = Event(), Event()
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def do_POST(self):
            payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            calls.append((self.path, payload))
            if len(calls) == 1:
                entered.set()
                release.wait(10)
            body = (
                {"data": [{"index": 0, "embedding": [1.0] * 32}]}
                if kind == "embedding"
                else {"results": [{"index": 0, "relevance_score": 0.75}]}
            )
            encoded = json.dumps(body).encode()
            try:
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(encoded)))
                self.end_headers()
                self.wfile.write(encoded)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The timed-out client's connection is already closed.

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    worker = Thread(target=server.serve_forever, daemon=True)
    worker.start()
    base = f"http://127.0.0.1:{server.server_port}/v1"
    settings = AppSettings(
        _env_file=None,
        sagevdb_embedding_base_url=base, sagevdb_embedding_api_key="",
        sagevdb_embedding_timeout_seconds=1, sagevdb_dimension=32,
        sagevdb_reranker_base_url=base, sagevdb_reranker_api_key="",
        sagevdb_reranker_timeout_seconds=1,
    )
    client = OpenAITextEmbedder(settings, np) if kind == "embedding" else OpenAIReranker(settings)
    invoke = (lambda: client.encode("synthetic public fixture")) if kind == "embedding" else (
        lambda: client.rerank("synthetic query", ["synthetic document"]))
    try:
        for phase in ("connect", "read", "write", "pool"):
            assert getattr(client._client.timeout, phase) == 1
        started = monotonic()
        with pytest.raises(httpx.ReadTimeout):
            invoke()
        assert entered.is_set()
        assert monotonic() - started < 5
        # The old server handler is still blocked; a healthy request must recover.
        result = invoke()
        if kind == "embedding":
            assert result.shape == (32,)
            assert np.isclose(np.linalg.norm(result), 1)
        else:
            assert result == [0.75]
        assert len(calls) == 2  # No hidden retries or fallback model calls.
        expected_path = "/v1/embeddings" if kind == "embedding" else "/v1/rerank"
        assert all(path == expected_path for path, _ in calls)
    finally:
        release.set()
        client._client.close()
        server.shutdown()
        server.server_close()
        worker.join(timeout=3)
        assert not worker.is_alive()
