from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import AsyncIterator, Callable

import httpx
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse, Response, StreamingResponse


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


@dataclass(frozen=True, slots=True)
class ProxySettings:
    listen_host: str
    listen_port: int
    upstream_base_url: str
    path_prefix: str
    api_key: str
    upstream_api_key: str
    timeout_seconds: float = 180.0
    trust_env: bool = False


def load_proxy_settings() -> ProxySettings:
    api_key = os.environ.get("DIGITAL_TWIN_API_KEY", "").strip()
    if not api_key or api_key.upper() == "EMPTY":
        raise RuntimeError(
            "DIGITAL_TWIN_API_KEY must be set to a real secret before starting the vLLM proxy."
        )

    listen_host = os.environ.get("VLLM_PROXY_HOST", "").strip()
    listen_port_text = os.environ.get("VLLM_PROXY_PORT", "").strip()
    upstream_base_url = os.environ.get("VLLM_PROXY_UPSTREAM_BASE_URL", "").strip()
    path_prefix = os.environ.get("VLLM_PROXY_PATH_PREFIX", "/v1")
    upstream_api_key = os.environ.get("VLLM_PROXY_UPSTREAM_API_KEY", "").strip()
    trust_env = os.environ.get("VLLM_PROXY_TRUST_ENV", "false").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }

    if not listen_host:
        raise RuntimeError("VLLM_PROXY_HOST must be configured.")
    if not listen_port_text:
        raise RuntimeError("VLLM_PROXY_PORT must be configured.")
    if not path_prefix.startswith("/"):
        raise RuntimeError("VLLM_PROXY_PATH_PREFIX must start with '/'.")
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

    if not upstream_base_url.startswith(("http://", "https://")):
        raise RuntimeError("VLLM_PROXY_UPSTREAM_BASE_URL must be an absolute HTTP(S) URL.")

    try:
        listen_port = int(listen_port_text)
    except ValueError as exc:  # pragma: no cover - guarded by systemd config
        raise RuntimeError("VLLM_PROXY_PORT must be an integer.") from exc

    return ProxySettings(
        listen_host=listen_host,
        listen_port=listen_port,
        upstream_base_url=upstream_base_url.rstrip("/"),
        path_prefix=path_prefix.rstrip("/"),
        api_key=api_key,
        upstream_api_key=upstream_api_key,
        trust_env=trust_env,
    )


def _normalize_headers(headers: httpx.Headers | dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for name, value in headers.items():
        lower_name = name.lower()
        if lower_name in HOP_BY_HOP_HEADERS or lower_name == "content-length":
            continue
        normalized[name] = value
    return normalized


def _extract_client_key(request: Request) -> str:
    authorization = request.headers.get("authorization", "")
    if authorization.lower().startswith("bearer "):
        return authorization.split(" ", 1)[1].strip()
    return request.headers.get("x-api-key", "").strip()


def _build_auth_error() -> JSONResponse:
    return JSONResponse(
        status_code=401,
        content={
            "error": {
                "message": "Invalid API key",
                "type": "authentication_error",
                "param": None,
                "code": "invalid_api_key",
            }
        },
    )


def _build_upstream_unavailable_error(exc: Exception) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content={
            "error": {
                "message": "vLLM upstream is not ready; retry after the engine finishes starting.",
                "type": "upstream_unavailable",
                "param": None,
                "code": "upstream_unavailable",
                "detail": exc.__class__.__name__,
            }
        },
    )


def _map_upstream_path(request_path: str, prefix: str) -> str | None:
    normalized_prefix = prefix.rstrip("/")
    if request_path == normalized_prefix:
        return ""
    prefix_with_slash = f"{normalized_prefix}/"
    if request_path.startswith(prefix_with_slash):
        return request_path[len(normalized_prefix) :]
    return None


def _build_upstream_url(settings: ProxySettings, request_path: str) -> str | None:
    suffix = _map_upstream_path(request_path, settings.path_prefix)
    if suffix is None:
        return None
    return f"{settings.upstream_base_url}{suffix}"


def _validate_settings(proxy_settings: ProxySettings) -> None:
    if not proxy_settings.api_key or proxy_settings.api_key.upper() == "EMPTY":
        raise RuntimeError(
            "DIGITAL_TWIN_API_KEY must be set to a real secret before starting the vLLM proxy."
        )


def create_app(
    settings: ProxySettings | None = None,
    client_factory: Callable[[ProxySettings], httpx.AsyncClient] | None = None,
) -> FastAPI:
    app = FastAPI(title="Sage Mate vLLM OpenAI Proxy")
    app.state.proxy_settings = settings
    app.state.proxy_client = None

    if client_factory is None:

        def client_factory(settings: ProxySettings) -> httpx.AsyncClient:
            return httpx.AsyncClient(
                timeout=settings.timeout_seconds,
                follow_redirects=False,
                trust_env=settings.trust_env,
            )

    if settings is not None:
        _validate_settings(settings)

    @app.get("/health")
    async def health() -> dict[str, str]:
        proxy_settings = app.state.proxy_settings or load_proxy_settings()
        return {
            "status": "ok",
            "upstream_base_url": proxy_settings.upstream_base_url,
            "path_prefix": proxy_settings.path_prefix,
        }

    @app.get("/")
    async def root() -> dict[str, str]:
        proxy_settings = app.state.proxy_settings or load_proxy_settings()
        return {
            "service": "sage-mate-vllm-openai-proxy",
            "upstream_base_url": proxy_settings.upstream_base_url,
            "path_prefix": proxy_settings.path_prefix,
        }

    @app.on_event("startup")
    async def _startup() -> None:
        proxy_settings = app.state.proxy_settings or load_proxy_settings()
        _validate_settings(proxy_settings)
        app.state.proxy_settings = proxy_settings
        app.state.proxy_client = client_factory(proxy_settings)

    @app.on_event("shutdown")
    async def _shutdown() -> None:
        proxy_client = app.state.proxy_client
        if proxy_client is not None:
            await proxy_client.aclose()

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    async def proxy(full_path: str, request: Request) -> Response:
        proxy_settings = app.state.proxy_settings or load_proxy_settings()
        upstream_url = _build_upstream_url(proxy_settings, request.url.path)
        if upstream_url is None:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})

        client_key = _extract_client_key(request)
        if client_key != proxy_settings.api_key:
            return _build_auth_error()

        body = await request.body()
        streaming_requested = False
        if body:
            content_type = request.headers.get("content-type", "")
            if content_type.startswith("application/json"):
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    streaming_requested = bool(payload.get("stream", False))

        forward_headers: dict[str, str] = {}
        for name, value in request.headers.items():
            lower_name = name.lower()
            if lower_name in HOP_BY_HOP_HEADERS or lower_name in {"host", "content-length"}:
                continue
            if lower_name == "authorization":
                continue
            forward_headers[name] = value

        forward_headers["Host"] = httpx.URL(proxy_settings.upstream_base_url).netloc or "127.0.0.1"
        forward_headers["X-Forwarded-For"] = request.client.host if request.client else "127.0.0.1"
        forward_headers["X-Forwarded-Proto"] = request.url.scheme
        forward_headers["X-Forwarded-Host"] = request.headers.get("host", "")
        if proxy_settings.upstream_api_key:
            forward_headers["Authorization"] = f"Bearer {proxy_settings.upstream_api_key}"

        proxy_client = app.state.proxy_client
        created_client = False
        if proxy_client is None:
            proxy_client = client_factory(proxy_settings)
            created_client = True

        try:
            stream_cm = proxy_client.stream(
                request.method,
                upstream_url,
                headers=forward_headers,
                content=body if body else None,
                params=request.query_params,
            )
            upstream = await stream_cm.__aenter__()
            response_headers = _normalize_headers(upstream.headers)

            if streaming_requested and upstream.status_code < 400:

                async def body_iter() -> AsyncIterator[bytes]:
                    try:
                        async for chunk in upstream.aiter_raw():
                            if chunk:
                                yield chunk
                    finally:
                        await stream_cm.__aexit__(None, None, None)

                return StreamingResponse(
                    body_iter(),
                    status_code=upstream.status_code,
                    headers=response_headers,
                )

            response_body = await upstream.aread()
            await stream_cm.__aexit__(None, None, None)
            return Response(
                content=response_body,
                status_code=upstream.status_code,
                headers=response_headers,
            )
        except (
            httpx.ConnectError,
            httpx.ConnectTimeout,
            httpx.ReadTimeout,
            httpx.RemoteProtocolError,
            httpx.PoolTimeout,
            httpx.WriteError,
        ) as exc:
            return _build_upstream_unavailable_error(exc)
        finally:
            if created_client:
                await proxy_client.aclose()

    return app


app = create_app()
