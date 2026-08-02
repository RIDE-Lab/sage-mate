#!/usr/bin/env python3

from __future__ import annotations

import http.client
import os
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
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


def _normalize_header_name(name: str) -> str:
    return "-".join(part.capitalize() for part in name.split("-"))


class ProxyHandler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    app_host = ""
    app_port = 0

    def do_GET(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_HEAD(self) -> None:  # noqa: N802
        self._proxy_request(include_body=False)

    def do_POST(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_PUT(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_PATCH(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_DELETE(self) -> None:  # noqa: N802
        self._proxy_request()

    def do_OPTIONS(self) -> None:  # noqa: N802
        self._proxy_request()

    def _proxy_request(self, include_body: bool = True) -> None:
        if self.path in ("/", "/home"):
            self.send_response(302)
            self.send_header("Location", "/home/")
            self.send_header("Content-Length", "0")
            self.end_headers()
            return

        parsed_url = urlsplit(self.path)
        upstream_path = parsed_url.path or "/"
        if parsed_url.query:
            upstream_path = f"{upstream_path}?{parsed_url.query}"

        request_headers: dict[str, str] = {}
        for name, value in self.headers.items():
            if name.lower() in HOP_BY_HOP_HEADERS or name.lower() == "host":
                continue
            request_headers[name] = value
        request_headers["Host"] = f"{self.app_host}:{self.app_port}"
        request_headers["X-Forwarded-For"] = self.client_address[0]
        request_headers["X-Forwarded-Host"] = self.headers.get("Host", "")
        request_headers["X-Forwarded-Proto"] = "http"

        body = b""
        if include_body:
            content_length = int(self.headers.get("Content-Length", "0") or "0")
            if content_length > 0:
                body = self.rfile.read(content_length)

        connection = http.client.HTTPConnection(self.app_host, self.app_port, timeout=180)
        try:
            connection.request(
                self.command,
                upstream_path,
                body=body if include_body else None,
                headers=request_headers,
            )
            upstream = connection.getresponse()
            response_body = upstream.read()

            self.send_response(upstream.status, upstream.reason)
            for name, value in upstream.getheaders():
                lower_name = name.lower()
                if lower_name in HOP_BY_HOP_HEADERS or lower_name == "content-length":
                    continue
                self.send_header(_normalize_header_name(name), value)
            self.send_header("Content-Length", str(len(response_body)))
            self.end_headers()
            if include_body and self.command != "HEAD":
                self.wfile.write(response_body)
        finally:
            connection.close()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A003
        message = format % args
        print(f"[{self.log_date_time_string()}] {self.client_address[0]} {message}", flush=True)


def main() -> None:
    repo_root = Path(__file__).resolve().parents[1]
    runtime_dir = repo_root / ".runtime"
    required_names = ("SITE_HOST", "SITE_PORT", "APP_HOST", "APP_PORT")
    missing = [name for name in required_names if not os.environ.get(name)]
    if missing:
        raise SystemExit(f"Missing required deployment settings: {', '.join(missing)}")

    site_host = os.environ["SITE_HOST"]
    site_port = int(os.environ["SITE_PORT"])
    ProxyHandler.app_host = os.environ["APP_HOST"]
    ProxyHandler.app_port = int(os.environ["APP_PORT"])

    runtime_dir.mkdir(parents=True, exist_ok=True)
    (runtime_dir / "site-proxy.pid").write_text(str(os.getpid()), encoding="utf-8")
    server = ThreadingHTTPServer((site_host, site_port), ProxyHandler)
    server.daemon_threads = True
    try:
        print(f"Local site proxy listening on {site_host}:{site_port}", flush=True)
        server.serve_forever()
    finally:
        try:
            (runtime_dir / "site-proxy.pid").unlink()
        except FileNotFoundError:
            pass
        server.server_close()


if __name__ == "__main__":
    main()
