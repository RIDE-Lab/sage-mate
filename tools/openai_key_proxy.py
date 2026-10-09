#!/usr/bin/env python3

from __future__ import annotations

import http.client
import json
import os
import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
}


def _extract_token(headers: BaseHTTPRequestHandler.headers.__class__) -> str | None:
    authorization = headers.get("Authorization", "")
    if authorization.lower().startswith("bearer "):
        token = authorization.split(" ", 1)[1].strip()
        return token or None
    x_api_key = headers.get("X-API-Key", "").strip()
    return x_api_key or None


def _safe_json_bytes(payload: dict) -> bytes:
    return json.dumps(payload, ensure_ascii=False).encode("utf-8")


def _upstream_unavailable_payload(exc: Exception) -> bytes:
    return _safe_json_bytes(
        {
            "error": {
                "message": "vLLM upstream is not ready; retry after the engine finishes starting.",
                "type": "upstream_unavailable",
                "param": None,
                "code": "upstream_unavailable",
                "detail": exc.__class__.__name__,
            }
        }
    )


class OpenAIKeyProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    upstream_host = "127.0.0.1"
    upstream_port = 8000
    path_prefix = "/v1"
    required_api_key = ""
    upstream_api_key = ""

    def do_GET(self) -> None:  # noqa: N802
        self._proxy_request(include_body=False)

    def do_HEAD(self) -> None:  # noqa: N802
        self._proxy_request(include_body=False)

    def do_POST(self) -> None:  # noqa: N802
        self._proxy_request(include_body=True)

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy_request(include_body=True)

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy_request(include_body=True)

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy_request(include_body=True)

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._proxy_request(include_body=False)

    def _proxy_request(self, include_body: bool) -> None:
        if self.path == "/health":
            payload = {
                "status": "ok",
                "proxy": "openai-key-proxy",
                "upstream": f"http://{self.upstream_host}:{self.upstream_port}{self.path_prefix}",
            }
            body = _safe_json_bytes(payload)
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return

        if not self.path.startswith(self.path_prefix):
            payload = _safe_json_bytes({"detail": "Not Found"})
            self.send_response(404)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        token = _extract_token(self.headers)
        if token != self.required_api_key:
            payload = _safe_json_bytes(
                {
                    "error": {
                        "message": "Invalid API key",
                        "type": "authentication_error",
                        "param": None,
                        "code": "invalid_api_key",
                    }
                }
            )
            self.send_response(401)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)
            return

        parsed = urlsplit(self.path)
        upstream_path = parsed.path
        if parsed.query:
            upstream_path = f"{upstream_path}?{parsed.query}"

        request_headers: dict[str, str] = {}
        for name, value in self.headers.items():
            lower_name = name.lower()
            if lower_name in HOP_BY_HOP_HEADERS or lower_name in {"host", "authorization", "x-api-key"}:
                continue
            request_headers[name] = value
        request_headers["Host"] = f"{self.upstream_host}:{self.upstream_port}"
        request_headers["X-Forwarded-For"] = self.client_address[0]
        request_headers["X-Forwarded-Proto"] = "http"
        request_headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        if self.upstream_api_key:
            request_headers["Authorization"] = f"Bearer {self.upstream_api_key}"

        body = None
        if include_body:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            if content_length > 0:
                body = self.rfile.read(content_length)

        connection = http.client.HTTPConnection(self.upstream_host, self.upstream_port, timeout=180)
        try:
            connection.request(
                method=self.command,
                url=upstream_path,
                body=body,
                headers=request_headers,
            )
            upstream_response = connection.getresponse()
            response_body = upstream_response.read()

            self.send_response(upstream_response.status, upstream_response.reason)
            for name, value in upstream_response.getheaders():
                lower_name = name.lower()
                if lower_name in HOP_BY_HOP_HEADERS or lower_name == "content-length":
                    continue
                self.send_header(name, value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(response_body)
        except (
            ConnectionError,
            TimeoutError,
            http.client.HTTPException,
            OSError,
            socket.timeout,
        ) as exc:
            payload = _upstream_unavailable_payload(exc)
            self.send_response(503)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(payload)
        finally:
            connection.close()

    def log_message(self, fmt: str, *args: object) -> None:
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} {fmt % args}", flush=True)


def main() -> None:
    required_api_key = os.environ.get("DIGITAL_TWIN_API_KEY", "").strip()
    if not required_api_key or required_api_key.upper() == "EMPTY":
        raise SystemExit("DIGITAL_TWIN_API_KEY must be set to a non-EMPTY value.")

    listen_host = os.environ.get("VLLM_PROXY_HOST", "127.0.0.1")
    listen_port = int(os.environ.get("VLLM_PROXY_PORT", "18001"))
    upstream_base_url = os.environ.get("VLLM_PROXY_UPSTREAM_BASE_URL", "").strip()
    if not upstream_base_url:
        connect_host = (
            os.environ.get("VLLM_ENGINE_CONNECT_HOST", "").strip()
            or os.environ.get("VLLM_PROXY_CONNECT_HOST", "").strip()
            or "127.0.0.1"
        )
        connect_port = (
            os.environ.get("VLLM_ENGINE_CONNECT_PORT", "").strip()
            or os.environ.get("VLLM_ENGINE_PORT", "").strip()
            or "8000"
        )
        upstream_base_url = f"http://{connect_host}:{connect_port}/v1"
    parsed = urlsplit(upstream_base_url)
    if parsed.scheme not in {"http", ""}:
        raise SystemExit("VLLM_PROXY_UPSTREAM_BASE_URL must be an http URL.")
    if not parsed.hostname:
        raise SystemExit("VLLM_PROXY_UPSTREAM_BASE_URL is missing hostname.")

    OpenAIKeyProxyHandler.required_api_key = required_api_key
    OpenAIKeyProxyHandler.upstream_api_key = os.environ.get("VLLM_PROXY_UPSTREAM_API_KEY", "").strip()
    OpenAIKeyProxyHandler.upstream_host = parsed.hostname
    OpenAIKeyProxyHandler.upstream_port = parsed.port or 80
    OpenAIKeyProxyHandler.path_prefix = os.environ.get("VLLM_PROXY_PATH_PREFIX", "/v1").rstrip("/") or "/v1"

    server = ThreadingHTTPServer((listen_host, listen_port), OpenAIKeyProxyHandler)
    server.daemon_threads = True
    print(
        f"OpenAI key proxy listening on {listen_host}:{listen_port}, upstream={upstream_base_url}",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
