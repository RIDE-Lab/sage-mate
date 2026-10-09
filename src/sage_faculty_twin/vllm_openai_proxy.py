from __future__ import annotations

import asyncio
import json
import os
import time
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, AsyncIterator, Callable
from uuid import uuid4

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
    sage_mate_base_url: str = "http://127.0.0.1:55601"


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
    app_host = os.environ.get("APP_HOST", "127.0.0.1").strip() or "127.0.0.1"
    if app_host in {"0.0.0.0", "::"}:
        app_host = "127.0.0.1"
    app_port = os.environ.get("APP_PORT", "55601").strip() or "55601"

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
        sage_mate_base_url=f"http://{app_host}:{app_port}",
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


def _authenticate_lab_member_token(token: str) -> object | None:
    """Resolve a Sage Mate member token without sharing the engine key."""

    if not token:
        return None
    from .api_token_store import LabMemberApiTokenStore
    from .config import settings as app_settings

    return LabMemberApiTokenStore(app_settings).authenticate(token)


def _extract_text_content(content: object) -> str:
    if isinstance(content, str):
        return content.strip()
    if not isinstance(content, list):
        return ""
    parts: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") in {"text", "input_text"}:
            text = str(item.get("text") or "").strip()
            if text:
                parts.append(text)
    return "\n".join(parts)


def _to_sage_mate_chat_payload(payload: dict[str, Any]) -> dict[str, Any]:
    messages = payload.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ValueError("messages must be a non-empty array")
    normalized: list[tuple[str, str]] = []
    for message in messages[-24:]:
        if not isinstance(message, dict):
            continue
        role = str(message.get("role") or "").strip().lower()
        text = _extract_text_content(message.get("content"))
        if role in {"system", "user", "assistant"} and text:
            normalized.append((role, text))
    user_positions = [index for index, item in enumerate(normalized) if item[0] == "user"]
    if not user_positions:
        raise ValueError("messages must contain a non-empty user message")
    last_user = user_positions[-1]
    question = normalized[last_user][1][:4000]
    context_lines = [f"{role}: {text}" for role, text in normalized[:last_user]]
    options = payload.get("sage_mate")
    if not isinstance(options, dict):
        options = {}
    max_tokens = payload.get("max_tokens")
    if max_tokens is None:
        max_tokens = payload.get("max_completion_tokens")
    answer_max_tokens = None
    if isinstance(max_tokens, int):
        answer_max_tokens = max(128, min(2048, max_tokens))
    metadata = payload.get("metadata")
    conversation_id = None
    if isinstance(metadata, dict):
        candidate = str(metadata.get("conversation_id") or "").strip()
        conversation_id = candidate[:128] or None
    return {
        "student_name": str(payload.get("user") or "Lab member")[:128],
        "question": question,
        "course_context": "\n".join(context_lines)[-512:] or None,
        "conversation_id": conversation_id,
        "visitor_profile": "lab_member",
        "deep_thinking": bool(options.get("deep_thinking", True)),
        "deep_thinking_explicit": bool(options.get("deep_thinking", False)),
        "skill_routing": bool(options.get("skill_routing", True)),
        "web_search": bool(options.get("web_search", False)),
        "answer_max_tokens": answer_max_tokens,
    }


def _openai_completion_from_sage(result: dict[str, Any]) -> dict[str, Any]:
    token_usage = result.get("token_usage")
    if not isinstance(token_usage, dict):
        token_usage = {}
    finish_reason = str(result.get("finish_reason") or "stop")
    if finish_reason not in {"stop", "length", "content_filter", "tool_calls"}:
        finish_reason = "stop"
    return {
        "id": f"chatcmpl-sage-{uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": "sage-mate",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": str(result.get("answer") or "")},
                "finish_reason": finish_reason,
            }
        ],
        "usage": {
            "prompt_tokens": int(token_usage.get("prompt_tokens") or 0),
            "completion_tokens": int(token_usage.get("completion_tokens") or 0),
            "total_tokens": int(token_usage.get("total_tokens") or 0),
        },
        "sage_mate": {
            "conversation_id": result.get("conversation_id"),
            "workflow_action": result.get("workflow_action"),
            "decision_mode": result.get("decision_mode"),
            "knowledge_hits": result.get("knowledge_hits") or [],
            "answer_basis": result.get("answer_basis") or [],
            "request_timing": result.get("request_timing"),
        },
    }


def _stream_completed_sage_response(completion: dict[str, Any]) -> StreamingResponse:
    async def events() -> AsyncIterator[bytes]:
        choice = completion["choices"][0]
        chunk = {
            "id": completion["id"],
            "object": "chat.completion.chunk",
            "created": completion["created"],
            "model": completion["model"],
            "choices": [
                {
                    "index": 0,
                    "delta": choice["message"],
                    "finish_reason": choice.get("finish_reason") or "stop",
                }
            ],
        }
        yield f"data: {json.dumps(chunk, ensure_ascii=False)}\n\n".encode()
        yield b"data: [DONE]\n\n"

    return StreamingResponse(events(), media_type="text/event-stream")


async def _iter_upstream_with_keepalive(
    upstream: httpx.Response,
    *,
    keepalive_seconds: float = 1.0,
) -> AsyncIterator[bytes]:
    """Relay upstream bytes while probing the downstream socket every second.

    ASGI 2.4 streaming responses detect a disconnected client only during a
    send.  vLLM can spend many seconds before its first token, so an SSE
    comment keeps the proxy's downstream send active without changing the
    OpenAI event stream observed by clients.
    """

    iterator = upstream.aiter_raw().__aiter__()
    next_chunk = asyncio.create_task(iterator.__anext__())
    try:
        while True:
            done, _ = await asyncio.wait({next_chunk}, timeout=keepalive_seconds)
            if not done:
                yield b": proxy-keepalive\n\n"
                continue
            try:
                chunk = next_chunk.result()
            except StopAsyncIteration:
                break
            if chunk:
                yield chunk
            next_chunk = asyncio.create_task(iterator.__anext__())
    finally:
        if not next_chunk.done():
            next_chunk.cancel()
            await asyncio.gather(next_chunk, return_exceptions=True)


async def _enter_upstream_until_disconnect(
    stream_cm: Any,
    request: Request,
) -> tuple[httpx.Response | None, bool]:
    """Open the upstream stream unless the downstream socket closes first."""

    async def wait_for_disconnect() -> None:
        while True:
            message = await request.receive()
            if message.get("type") == "http.disconnect":
                return

    enter_task = asyncio.create_task(stream_cm.__aenter__())
    disconnect_task = asyncio.create_task(wait_for_disconnect())
    try:
        done, _ = await asyncio.wait(
            {enter_task, disconnect_task},
            return_when=asyncio.FIRST_COMPLETED,
        )
        if enter_task in done:
            return enter_task.result(), False
        enter_task.cancel()
        await asyncio.gather(enter_task, return_exceptions=True)
        return None, True
    finally:
        if not disconnect_task.done():
            disconnect_task.cancel()
        await asyncio.gather(disconnect_task, return_exceptions=True)


def create_app(
    settings: ProxySettings | None = None,
    client_factory: Callable[[ProxySettings], httpx.AsyncClient] | None = None,
    token_authenticator: Callable[[str], object | None] | None = None,
) -> FastAPI:
    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        proxy_settings = app.state.proxy_settings or load_proxy_settings()
        _validate_settings(proxy_settings)
        app.state.proxy_settings = proxy_settings
        app.state.proxy_client = client_factory(proxy_settings)
        try:
            yield
        finally:
            proxy_client = app.state.proxy_client
            if proxy_client is not None:
                await proxy_client.aclose()

    app = FastAPI(title="Sage Mate vLLM OpenAI Proxy", lifespan=lifespan)
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
    if token_authenticator is None:
        token_authenticator = _authenticate_lab_member_token

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

    @app.api_route("/{full_path:path}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"])
    async def proxy(full_path: str, request: Request) -> Response:
        proxy_settings = app.state.proxy_settings or load_proxy_settings()
        upstream_url = _build_upstream_url(proxy_settings, request.url.path)
        if upstream_url is None:
            return JSONResponse(status_code=404, content={"detail": "Not Found"})

        client_key = _extract_client_key(request)
        member_identity = token_authenticator(client_key)
        if client_key != proxy_settings.api_key and member_identity is None:
            return _build_auth_error()

        body = await request.body()
        streaming_requested = False
        payload: object = None
        if body:
            content_type = request.headers.get("content-type", "")
            if content_type.startswith("application/json"):
                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    payload = None
                if isinstance(payload, dict):
                    streaming_requested = bool(payload.get("stream", False))

        is_member_models_request = (
            member_identity is not None
            and request.method == "GET"
            and request.url.path.rstrip("/") == f"{proxy_settings.path_prefix}/models"
        )
        if is_member_models_request:
            return JSONResponse(
                {
                    "object": "list",
                    "data": [
                        {
                            "id": "sage-mate",
                            "object": "model",
                            "created": 0,
                            "owned_by": "vllm-hust",
                        }
                    ],
                }
            )

        is_sage_completion = (
            member_identity is not None
            and request.method == "POST"
            and request.url.path.rstrip("/")
            == f"{proxy_settings.path_prefix}/chat/completions"
            and isinstance(payload, dict)
            and str(payload.get("model") or "") == "sage-mate"
        )
        if (
            member_identity is not None
            and client_key != proxy_settings.api_key
            and not is_member_models_request
            and not is_sage_completion
        ):
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "message": "This member token may access only the sage-mate model.",
                        "type": "permission_error",
                        "param": "model",
                        "code": "model_not_allowed",
                    }
                },
            )
        if is_sage_completion:
            try:
                sage_payload = _to_sage_mate_chat_payload(payload)
            except ValueError as exc:
                return JSONResponse(
                    status_code=400,
                    content={"error": {"message": str(exc), "type": "invalid_request_error"}},
                )
            try:
                async with httpx.AsyncClient(
                    timeout=proxy_settings.timeout_seconds,
                    trust_env=proxy_settings.trust_env,
                ) as sage_client:
                    sage_response = await sage_client.post(
                        f"{proxy_settings.sage_mate_base_url.rstrip('/')}/chat",
                        headers={"Authorization": f"Bearer {client_key}"},
                        json=sage_payload,
                    )
                if sage_response.status_code >= 400:
                    detail = sage_response.text[:1000]
                    return JSONResponse(
                        status_code=sage_response.status_code,
                        content={
                            "error": {
                                "message": detail,
                                "type": "sage_mate_error",
                                "code": "sage_mate_error",
                            }
                        },
                    )
                completion = _openai_completion_from_sage(sage_response.json())
                if streaming_requested:
                    return _stream_completed_sage_response(completion)
                return JSONResponse(completion)
            except (httpx.HTTPError, ValueError, json.JSONDecodeError) as exc:
                return _build_upstream_unavailable_error(exc)

        if member_identity is not None and client_key != proxy_settings.api_key:
            return JSONResponse(
                status_code=403,
                content={
                    "error": {
                        "message": "This member token may only use the sage-mate model.",
                        "type": "permission_error",
                        "param": "model",
                        "code": "model_not_allowed",
                    }
                },
            )

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
            upstream, downstream_disconnected = await _enter_upstream_until_disconnect(
                stream_cm,
                request,
            )
            if downstream_disconnected or upstream is None:
                return Response(status_code=499)
            response_headers = _normalize_headers(upstream.headers)

            if streaming_requested and upstream.status_code < 400:

                async def body_iter() -> AsyncIterator[bytes]:
                    try:
                        async for chunk in _iter_upstream_with_keepalive(upstream):
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
