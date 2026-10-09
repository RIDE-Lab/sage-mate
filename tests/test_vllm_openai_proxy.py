from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from fastapi.testclient import TestClient

from sage_faculty_twin.vllm_openai_proxy import (
    ProxySettings,
    _enter_upstream_until_disconnect,
    _iter_upstream_with_keepalive,
    _openai_completion_from_sage,
    _stream_completed_sage_response,
    create_app,
)


class _FakeStreamResponse:
    def __init__(self, status_code: int = 200, headers: dict[str, str] | None = None) -> None:
        self.status_code = status_code
        self.headers = headers or {"content-type": "text/event-stream"}
        self.closed = False

    async def __aenter__(self) -> "_FakeStreamResponse":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        self.closed = True

    async def aiter_raw(self):
        yield b"data: {\"choices\":[{\"delta\":{\"content\":\"hello\"}}]}\n\n"
        yield b"data: [DONE]\n\n"

    async def aread(self) -> bytes:
        return b'{"object":"chat.completion","choices":[]}'


class _FakeAsyncClient:
    last_request: dict[str, object] | None = None

    def __init__(self, *args, **kwargs) -> None:
        self.stream_response = _FakeStreamResponse()

    async def __aenter__(self) -> "_FakeAsyncClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def aclose(self) -> None:
        pass

    def stream(self, method: str, url: str, **kwargs):
        _FakeAsyncClient.last_request = {"method": method, "url": url, **kwargs}
        return self.stream_response


class _FailingAsyncClient(_FakeAsyncClient):
    def stream(self, method: str, url: str, **kwargs):
        raise httpx.ConnectError("connection refused")


class _FakeSageMateClient:
    last_request: dict[str, object] | None = None

    def __init__(self, *args, **kwargs) -> None:
        pass

    async def __aenter__(self) -> "_FakeSageMateClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        return None

    async def post(self, url: str, **kwargs) -> httpx.Response:
        _FakeSageMateClient.last_request = {"url": url, **kwargs}
        return httpx.Response(
            200,
            json={
                "answer": "按七问法，先明确输入、输出和约束。",
                "conversation_id": "conv-1",
                "workflow_action": "answer",
                "decision_mode": "direct_answer",
                "knowledge_hits": [{"document_id": "method-1"}],
                "answer_basis": [],
                "token_usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 10,
                    "total_tokens": 30,
                },
            },
            request=httpx.Request("POST", url),
        )


class _SlowStreamResponse:
    async def aiter_raw(self):
        await asyncio.Event().wait()
        yield b"unreachable"


class _SlowEnterStream:
    def __init__(self) -> None:
        self.cancelled = False

    async def __aenter__(self):
        try:
            await asyncio.Event().wait()
        except asyncio.CancelledError:
            self.cancelled = True
            raise


class _DisconnectedRequest:
    async def receive(self) -> dict[str, str]:
        return {"type": "http.disconnect"}


def test_proxy_cancels_pre_header_upstream_when_downstream_disconnects() -> None:
    stream = _SlowEnterStream()

    async def run() -> tuple[object | None, bool]:
        return await _enter_upstream_until_disconnect(
            stream,
            _DisconnectedRequest(),  # type: ignore[arg-type]
        )

    upstream, disconnected = asyncio.run(run())
    assert upstream is None
    assert disconnected is True
    assert stream.cancelled is True


def test_streaming_proxy_emits_keepalive_before_slow_upstream_first_token() -> None:
    async def collect() -> bytes:
        stream = _iter_upstream_with_keepalive(
            _SlowStreamResponse(),  # type: ignore[arg-type]
            keepalive_seconds=0.01,
        )
        try:
            return await asyncio.wait_for(stream.__anext__(), timeout=0.5)
        finally:
            await stream.aclose()

    assert asyncio.run(collect()) == b": proxy-keepalive\n\n"


def test_proxy_requires_a_real_api_key() -> None:
    with pytest.raises(RuntimeError, match="DIGITAL_TWIN_API_KEY"):
        create_app(
            ProxySettings(
                listen_host="127.0.0.1",
                listen_port=18001,
                upstream_base_url="http://127.0.0.1:18000/v1",
                path_prefix="/v1",
                api_key="",
                upstream_api_key="",
            )
        )


def test_proxy_rejects_bad_key(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(
        ProxySettings(
            listen_host="127.0.0.1",
            listen_port=18001,
            upstream_base_url="http://127.0.0.1:18000/v1",
            path_prefix="/v1",
            api_key="secret",
            upstream_api_key="",
        )
    )
    monkeypatch.setattr("sage_faculty_twin.vllm_openai_proxy.httpx.AsyncClient", _FakeAsyncClient)

    with TestClient(app) as client:
        response = client.get("/v1/models")

    assert response.status_code == 401
    assert response.json()["error"]["code"] == "invalid_api_key"


def test_proxy_forwards_streaming_request_with_auth(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(
        ProxySettings(
            listen_host="127.0.0.1",
            listen_port=18001,
            upstream_base_url="https://inference.example.test/v1",
            path_prefix="/v1",
            api_key="secret",
            upstream_api_key="upstream-secret",
        )
    )
    monkeypatch.setattr("sage_faculty_twin.vllm_openai_proxy.httpx.AsyncClient", _FakeAsyncClient)

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer secret"},
            json={
                "model": "Qwen3-32B",
                "messages": [{"role": "user", "content": "hi"}],
                "stream": True,
            },
        )

    assert response.status_code == 200
    assert "text/event-stream" in response.headers.get("content-type", "")
    assert response.text.count("data:") == 2
    assert _FakeAsyncClient.last_request is not None
    assert _FakeAsyncClient.last_request["url"] == "https://inference.example.test/v1/chat/completions"
    assert _FakeAsyncClient.last_request["headers"]["Authorization"] == "Bearer upstream-secret"
    payload = json.loads(_FakeAsyncClient.last_request["content"])
    assert payload["stream"] is True


def test_proxy_forwards_configured_upstream_key_to_loopback_vllm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        ProxySettings(
            listen_host="127.0.0.1",
            listen_port=18001,
            upstream_base_url="http://127.0.0.1:18000/v1",
            path_prefix="/v1",
            api_key="secret",
            upstream_api_key="local-vllm-secret",
        )
    )
    monkeypatch.setattr("sage_faculty_twin.vllm_openai_proxy.httpx.AsyncClient", _FakeAsyncClient)

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer secret"},
            json={
                "model": "zai-org/GLM-4-32B-0414",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert response.status_code == 200
    assert _FakeAsyncClient.last_request is not None
    assert _FakeAsyncClient.last_request["headers"]["Authorization"] == "Bearer local-vllm-secret"


def test_proxy_returns_503_when_upstream_is_not_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    app = create_app(
        ProxySettings(
            listen_host="127.0.0.1",
            listen_port=18001,
            upstream_base_url="http://127.0.0.1:18000/v1",
            path_prefix="/v1",
            api_key="secret",
            upstream_api_key="",
        )
    )
    monkeypatch.setattr("sage_faculty_twin.vllm_openai_proxy.httpx.AsyncClient", _FailingAsyncClient)

    with TestClient(app) as client:
        response = client.get("/v1/models", headers={"Authorization": "Bearer secret"})

    assert response.status_code == 503
    payload = response.json()
    assert payload["error"]["code"] == "upstream_unavailable"


def test_member_token_lists_sage_mate_model() -> None:
    app = create_app(
        ProxySettings(
            listen_host="127.0.0.1",
            listen_port=18001,
            upstream_base_url="http://127.0.0.1:18000/v1",
            path_prefix="/v1",
            api_key="engine-secret",
            upstream_api_key="",
        ),
        client_factory=lambda _settings: _FakeAsyncClient(),
        token_authenticator=lambda token: object() if token == "member-token" else None,
    )

    with TestClient(app) as client:
        response = client.get(
            "/v1/models", headers={"Authorization": "Bearer member-token"}
        )

    assert response.status_code == 200
    assert response.json()["data"][0]["id"] == "sage-mate"


def test_member_token_cannot_bypass_sage_mate_with_a_raw_model() -> None:
    app = create_app(
        ProxySettings(
            listen_host="127.0.0.1",
            listen_port=18001,
            upstream_base_url="http://127.0.0.1:18000/v1",
            path_prefix="/v1",
            api_key="engine-secret",
            upstream_api_key="",
        ),
        client_factory=lambda _settings: _FakeAsyncClient(),
        token_authenticator=lambda token: object() if token == "member-token" else None,
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer member-token"},
            json={
                "model": "Qwen/Qwen3.8-27B",
                "messages": [{"role": "user", "content": "hi"}],
            },
        )

    assert response.status_code == 403
    assert response.json()["error"]["code"] == "model_not_allowed"


def test_member_openai_completion_routes_through_sage_mate(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    app = create_app(
        ProxySettings(
            listen_host="127.0.0.1",
            listen_port=18001,
            upstream_base_url="http://127.0.0.1:18000/v1",
            path_prefix="/v1",
            api_key="engine-secret",
            upstream_api_key="",
            sage_mate_base_url="http://127.0.0.1:55601",
        ),
        client_factory=lambda _settings: _FakeAsyncClient(),
        token_authenticator=lambda token: object() if token == "member-token" else None,
    )
    monkeypatch.setattr(
        "sage_faculty_twin.vllm_openai_proxy.httpx.AsyncClient",
        _FakeSageMateClient,
    )

    with TestClient(app) as client:
        response = client.post(
            "/v1/chat/completions",
            headers={"Authorization": "Bearer member-token"},
            json={
                "model": "sage-mate",
                "messages": [
                    {"role": "system", "content": "按课题组方法审查。"},
                    {"role": "user", "content": "评价这个研究课题。"},
                ],
                "max_tokens": 512,
                "sage_mate": {"deep_thinking": False, "skill_routing": False},
            },
        )

    assert response.status_code == 200
    completion = response.json()
    assert completion["model"] == "sage-mate"
    assert completion["choices"][0]["message"]["role"] == "assistant"
    assert completion["usage"] == {
        "prompt_tokens": 20,
        "completion_tokens": 10,
        "total_tokens": 30,
    }
    assert completion["sage_mate"]["knowledge_hits"] == [
        {"document_id": "method-1"}
    ]
    assert _FakeSageMateClient.last_request is not None
    assert _FakeSageMateClient.last_request["url"] == "http://127.0.0.1:55601/chat"
    assert _FakeSageMateClient.last_request["headers"] == {
        "Authorization": "Bearer member-token"
    }
    payload = _FakeSageMateClient.last_request["json"]
    assert payload["visitor_profile"] == "lab_member"
    assert payload["question"] == "评价这个研究课题。"
    assert payload["course_context"] == "system: 按课题组方法审查。"
    assert payload["answer_max_tokens"] == 512
    assert payload["skill_routing"] is False


def test_openai_adapter_preserves_sage_finish_reason() -> None:
    completion = _openai_completion_from_sage(
        {
            "answer": "E. 回答尚未完成……",
            "finish_reason": "length",
            "token_usage": {"prompt_tokens": 5, "completion_tokens": 8},
        }
    )

    assert completion["choices"][0]["finish_reason"] == "length"


def test_openai_stream_preserves_non_stop_finish_reason() -> None:
    completion = _openai_completion_from_sage(
        {"answer": "内容被过滤。", "finish_reason": "content_filter"}
    )

    async def collect() -> str:
        response = _stream_completed_sage_response(completion)
        chunks = [chunk async for chunk in response.body_iterator]
        return b"".join(
            chunk.encode() if isinstance(chunk, str) else chunk for chunk in chunks
        ).decode()

    payload = asyncio.run(collect())
    first_event = json.loads(payload.splitlines()[0].removeprefix("data: "))
    assert first_event["choices"][0]["finish_reason"] == "content_filter"
