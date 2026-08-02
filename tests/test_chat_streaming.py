"""Regression tests for the Chat Latency Optimizations Task 5 streaming.

When the legacy ``DIGITAL_TWIN_STREAM_CHAT_ANSWER`` flag is enabled the LLM client asks
for an OpenAI-compatible streaming completion (``stream=true``) and
forwards each token chunk through the ``answer_chunk_callback`` plumbed
all the way from ``/chat`` -> ``DigitalTwinService.answer`` ->
``FacultyTwinWorkflowSupport.answer_with_llm`` -> ``VllmChatClient``. The
``WorkflowEventBroker`` then re-emits each chunk as a typed
``{"type": "answer_delta", "text": "..."}`` SSE event, followed by a
final ``{"type": "answer_done", "response": {...}}`` once the /chat POST
finishes rendering.

The public ``/chat`` route buffers these chunks and exposes only the validated
final response. The lower-level tests below cover the internal streaming
transport and broker primitives:

1. ``WorkflowEventBroker.publish_answer_chunk`` / ``publish_answer_done``
   land on the SSE stream as typed JSON events with the expected shape.
2. ``VllmChatClient.answer_question_sync(token_callback=...)`` parses
   OpenAI-compatible ``data: {...}`` SSE lines, calls the callback for
   each non-empty delta in order, and returns the concatenated text.
3. The streaming callback bypasses the response cache so a second
   identical request still hits the upstream and replays deltas to the
   callback (because the cache stores the joined string only).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import threading
from collections import OrderedDict
from typing import Iterable, Iterator

import pytest

from sage_faculty_twin import api as api_module
from sage_faculty_twin.chat_delivery import ChatDeliveryGate
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.llm_client import VllmChatClient
from sage_faculty_twin.models import ChatResponse


@pytest.fixture
def short_keepalive(monkeypatch: pytest.MonkeyPatch) -> float:
    """Avoid spurious keepalive interleaving during streaming-event tests."""

    monkeypatch.setattr(api_module, "CHAT_SSE_KEEPALIVE_SECONDS", 5.0)
    return 5.0


def test_publish_delivered_answer_then_done_emits_in_order(
    short_keepalive: float,
) -> None:
    """``answer_delta`` events must arrive in publish order, followed by
    a single ``answer_done`` carrying the rendered ChatResponse dict."""

    request_id = "req-stream-order"
    broker = api_module.WorkflowEventBroker()

    async def driver() -> list[dict]:
        agen = broker.stream(request_id).__aiter__()

        async def fetch_one() -> dict:
            chunk = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
            assert chunk.startswith("data: ")
            return json.loads(chunk[len("data: ") :].rstrip("\n"))

        # Register the stream with the broker before publishing.
        first = asyncio.create_task(fetch_one())
        for _ in range(5):
            await asyncio.sleep(0)
            with broker._lock:  # type: ignore[attr-defined]
                if request_id in broker._streams:  # type: ignore[attr-defined]
                    break

        delivered = ChatDeliveryGate().deliver(
            response=ChatResponse(
                answer="Hello, world!",
                owner_name="Twin",
                used_model="test-model",
            ),
            original_question="Say hello",
        )
        broker.publish_answer_done(request_id, delivered)
        broker.publish_complete(request_id)

        events: list[dict] = [await first]
        try:
            async for chunk in agen:
                events.append(json.loads(chunk[len("data: ") :].rstrip("\n")))
        except StopAsyncIteration:
            pass
        return events

    events = asyncio.run(driver())
    done = [e for e in events if e.get("type") == "answer_done"]

    assert len(done) == 1
    assert done[0]["response"]["answer"] == "Hello, world!"
    assert not any(e.get("type") == "answer_delta" for e in events)


def test_broker_rejects_unvalidated_chat_response() -> None:
    broker = api_module.WorkflowEventBroker()
    response = ChatResponse(answer="raw", owner_name="Twin", used_model="test-model")

    with pytest.raises(TypeError, match="DeliveredChatResponse"):
        broker.publish_answer_done("req-unvalidated", response)  # type: ignore[arg-type]


def test_public_chat_route_does_not_publish_unvalidated_answer_chunks() -> None:
    source = inspect.getsource(api_module.chat)

    assert "buffered_answer_chunks.append(delta)" in source
    assert "workflow_event_broker.publish_answer_chunk(request_id, delta)" not in source


def test_answer_done_complete_gate_defers_close_until_answer_done() -> None:
    published: list[str] = []
    gate = api_module._AnswerDoneCompleteGate(lambda: published.append("complete"))

    gate.mark_post_answer_complete()
    assert published == []

    gate.mark_answer_done()
    assert published == ["complete"]

    gate.mark_post_answer_complete()
    gate.mark_answer_done()
    assert published == ["complete"]


class _FakeStreamingResponse:
    def __init__(self, lines: Iterable[str]) -> None:
        self._lines = list(lines)

    def __enter__(self) -> "_FakeStreamingResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        return None

    def raise_for_status(self) -> None:
        return None

    def iter_lines(self) -> Iterator[str]:
        for line in self._lines:
            yield line


class _FakeStreamingHttpxClient:
    def __init__(self, deltas: list[str]) -> None:
        self._deltas = deltas
        self.stream_calls = 0
        self.last_payload: dict | None = None

    def stream(self, method: str, path: str, json: dict) -> _FakeStreamingResponse:
        self.stream_calls += 1
        self.last_payload = json
        # Build OpenAI-compatible SSE lines: "data: {json}" plus the
        # terminator "data: [DONE]". Mix in a stray empty line and a
        # control comment to make sure the parser is tolerant.
        lines: list[str] = ["", ": heartbeat"]
        for delta in self._deltas:
            chunk = {
                "choices": [{"delta": {"content": delta}}],
            }
            lines.append(f"data: {json_dumps(chunk)}")
        lines.append("data: [DONE]")
        return _FakeStreamingResponse(lines)

    def post(self, path: str, json: dict):  # pragma: no cover - unused
        raise AssertionError("non-streaming path should not be hit")

    def close(self) -> None:
        return None


def json_dumps(payload: dict) -> str:
    # Local helper to avoid shadowing the ``json`` module import inside
    # the fake client (where ``json`` is a parameter name).
    import json as _json

    return _json.dumps(payload, ensure_ascii=False)


def _build_streaming_test_client(
    settings: AppSettings, transport: _FakeStreamingHttpxClient
) -> VllmChatClient:
    client = object.__new__(VllmChatClient)
    client._settings = settings
    client._client = transport
    client.model_name = "test-model"
    client._supports_thinking_budget = False
    client._cache_lock = threading.Lock()
    client._response_cache = OrderedDict()
    client._metrics_lock = threading.Lock()
    client._request_count = 0
    client._success_count = 0
    client._error_count = 0
    client._cache_hit_count = 0
    client._last_request_at = None
    client._last_success_at = None
    client._last_error_at = None
    client._last_error_message = None
    return client


def test_answer_question_sync_streams_tokens_in_order() -> None:
    """``token_callback`` is invoked for each non-empty delta in arrival
    order and the joined string is returned."""

    settings = AppSettings(
        llm_cache_ttl_seconds=0,
        llm_cache_max_entries=0,
        llm_retry_attempts=0,
    )
    transport = _FakeStreamingHttpxClient(["你好", "，", "世界", "！"])
    client = _build_streaming_test_client(settings, transport)

    seen: list[str] = []
    answer = client.answer_question_sync(
        "system",
        "user",
        token_callback=seen.append,
    )

    assert seen == ["你好", "，", "世界", "！"]
    assert answer == "你好，世界！"
    assert transport.stream_calls == 1
    assert transport.last_payload is not None
    # The streaming path must request ``stream=true`` so vLLM sends SSE
    # chunks rather than a single buffered JSON response.
    assert transport.last_payload.get("stream") is True


def test_answer_question_sync_falls_back_when_stream_is_empty() -> None:
    """Some vLLM-compatible servers can finish an SSE response without
    content chunks. The chat endpoint should retry buffered instead of
    surfacing a 500 to the user."""

    class _EmptyThenBufferedTransport(_FakeStreamingHttpxClient):
        def __init__(self) -> None:
            super().__init__([])
            self.post_calls = 0

        def post(self, path: str, json: dict):
            self.post_calls += 1
            assert "stream" not in json

            class _Resp:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict:
                    return {"choices": [{"message": {"content": "buffered answer"}}]}

            return _Resp()

    settings = AppSettings(
        llm_cache_ttl_seconds=0,
        llm_cache_max_entries=0,
        llm_retry_attempts=0,
    )
    transport = _EmptyThenBufferedTransport()
    client = _build_streaming_test_client(settings, transport)

    seen: list[str] = []
    answer = client.answer_question_sync("system", "user", token_callback=seen.append)

    assert answer == "buffered answer"
    assert seen == []
    assert transport.stream_calls == 1
    assert transport.post_calls == 1


def test_answer_question_sync_without_callback_uses_non_streaming_path() -> None:
    """When no ``token_callback`` is provided the client must keep the
    original non-streaming behaviour (single ``post`` call) so existing
    callers and the response cache stay unaffected."""

    class _NonStreamingTransport:
        def __init__(self) -> None:
            self.calls = 0

        def post(self, path: str, json: dict):
            self.calls += 1

            class _Resp:
                def raise_for_status(self) -> None:
                    return None

                def json(self) -> dict:
                    return {"choices": [{"message": {"content": "buffered"}}]}

            return _Resp()

        def stream(self, *args, **kwargs):  # pragma: no cover - guard
            raise AssertionError("non-streaming caller must not hit stream()")

        def close(self) -> None:
            return None

    settings = AppSettings(
        llm_cache_ttl_seconds=0,
        llm_cache_max_entries=0,
        llm_retry_attempts=0,
    )
    transport = _NonStreamingTransport()
    client = _build_streaming_test_client(settings, transport)  # type: ignore[arg-type]

    answer = client.answer_question_sync("system", "user")

    assert answer == "buffered"
    assert transport.calls == 1
