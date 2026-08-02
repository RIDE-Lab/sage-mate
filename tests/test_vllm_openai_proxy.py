from __future__ import annotations

import json

import httpx
import pytest
from fastapi.testclient import TestClient

from sage_faculty_twin.vllm_openai_proxy import ProxySettings, create_app


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
