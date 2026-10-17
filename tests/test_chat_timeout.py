"""Regression tests for the /chat request budget guard.

Cloudflare's free/Pro tier enforces an ~100s edge timeout. The backend wraps
``service.answer`` in :func:`asyncio.wait_for` with a slightly smaller budget
(`CHAT_REQUEST_TIMEOUT_SECONDS`) so we can return a structured 504 *before*
the proxy gives up. These tests pin that contract.
"""

from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from sage_faculty_twin import api as api_module
from sage_faculty_twin.api import app, service
from sage_faculty_twin.request_context import RequestCancellationController
from sage_faculty_twin.models import ChatResponse

client = TestClient(app)


def test_completed_answer_does_not_wait_for_disconnect_when_poll_swallows_cancel(monkeypatch):
    """Python 3.11 wait_for can consume cancellation at poll completion.

    A finished answer must stop the watcher cooperatively, not wait until the
    browser eventually disconnects. Keep a release path so the old code fails
    an assertion instead of hanging the test runner.
    """
    async def run():
        polling = asyncio.Event()
        release = asyncio.Event()
        original_wait_for = asyncio.wait_for

        async def wait_for_with_completion_race(coro, timeout):
            if getattr(coro, "__name__", "") != "is_disconnected":
                return await original_wait_for(coro, timeout)
            result = await coro
            try:
                await release.wait()
            except asyncio.CancelledError:
                # Python 3.11 wait_for returns fut.result() when cancellation
                # arrives after its inner future has completed. Force this
                # scheduling window instead of relying on a probabilistic race.
                return result
            return True

        monkeypatch.setattr(asyncio, "wait_for", wait_for_with_completion_race)

        class Request:
            headers = {"content-type": "application/json"}

            async def is_disconnected(self):
                polling.set()
                # Finish in the same loop turn as service.answer. This makes
                # wait_for's inner task complete as its outer watcher is
                # cancelled, the cancellation/completion race in production.
                return release.is_set()

        async def answer(*args, **kwargs):
            await polling.wait()
            return ChatResponse(answer="ready", owner_name="Twin", used_model="test")

        monkeypatch.setattr(service, "answer", answer)
        task = asyncio.create_task(api_module._run_chat_with_cancellation(
            Request(), timeout_seconds=1, payload=object(),
        ))
        try:
            done, _ = await asyncio.wait({task}, timeout=0.15)
            assert task in done, "finished response blocked on cancelled disconnect watcher"
            assert task.result().answer == "ready"
        finally:
            release.set()
            await asyncio.wait_for(task, timeout=2)

    asyncio.run(run())


@pytest.mark.parametrize('suffix', ['', '?request_id=capacity-regression'])
def test_prompt_capacity_failure_is_actionable_not_500(monkeypatch, isolated_chat_service, suffix):
    async def fail(*args, **kwargs):
        raise api_module.PromptCapacityError('Evidence was not truncated')
    monkeypatch.setattr(service, 'answer', fail)
    client.cookies.clear()
    response = client.post('/chat' + suffix, json={
        'student_name': 'Capacity test', 'question': '请解释完整材料',
    })
    assert response.status_code == 413
    assert '未截断原文' in response.json()['detail']
    assert response.headers.get('x-sage-trace-id')


@pytest.fixture
def isolated_chat_service(monkeypatch: pytest.MonkeyPatch) -> None:
    """Exercise the HTTP boundary without loading optional embedding weights."""
    monkeypatch.setattr(service, "_instance", SimpleNamespace(
        _check_sensitive_boundary_request=lambda *_args: None,
        try_fast_answer=lambda *_args: None,
        answer=lambda *_args, **_kwargs: None,
    ))


@pytest.fixture
def short_chat_budget(monkeypatch: pytest.MonkeyPatch) -> float:
    """Shrink the /chat budget so tests run in well under a second."""

    monkeypatch.setattr(api_module, "CHAT_REQUEST_TIMEOUT_SECONDS", 0.2)
    return 0.2


def test_chat_returns_504_when_service_exceeds_budget(
    short_chat_budget: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A slow ``service.answer`` triggers the ``asyncio.wait_for`` guard and
    returns HTTP 504 with the Chinese, user-friendly detail message."""

    async def slow_answer(*_args, **_kwargs):
        await asyncio.sleep(5.0)  # well above the 0.2s budget
        raise AssertionError("wait_for guard should have fired before this runs")

    monkeypatch.setattr(service, "answer", slow_answer)
    client.cookies.clear()

    response = client.post(
        "/chat",
        json={
            "student_name": "Alice",
            "student_email": "alice@example.com",
            "course_context": None,
            "conversation_id": "conv-timeout-test",
            "question": "请耐心等我一下。",
        },
    )

    assert response.status_code == 504
    detail = response.json().get("detail", "")
    assert "未完成响应" in detail
    assert "重试" in detail
    assert response.headers.get("x-sage-trace-id")


def test_streamed_explicit_deep_chat_uses_long_budget_and_delivers_final_response(
    monkeypatch: pytest.MonkeyPatch, isolated_chat_service: None,
) -> None:
    async def answer(*_args, **_kwargs):
        return ChatResponse(answer="完整研究回答", owner_name="Twin", used_model="Qwen")

    monkeypatch.setattr(service, "try_fast_answer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "answer", answer)
    client.cookies.clear()
    response = client.post(
        "/chat/stream",
        json={
            "student_name": "Alice",
            "question": "请推导量化状态误差上界。",
            "deep_thinking": True,
            "deep_thinking_explicit": True,
        },
    )

    assert response.status_code == 200
    events = [
        json.loads(line.removeprefix("data: "))
        for line in response.text.splitlines()
        if line.startswith("data: ")
    ]
    assert len(events) == 1
    assert events[0]["type"] == "result"
    assert events[0]["response"]["answer"] == "完整研究回答"
    assert events[0]["response"]["request_timing"]["budget_ms"] == (
        api_module.CHAT_LONG_REQUEST_TIMEOUT_SECONDS * 1000
    )


def test_streamed_non_deep_chat_keeps_normal_budget(
    monkeypatch: pytest.MonkeyPatch, isolated_chat_service: None,
) -> None:
    async def answer(*_args, **_kwargs):
        return ChatResponse(answer="普通回答", owner_name="Twin", used_model="Qwen")

    monkeypatch.setattr(service, "try_fast_answer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "answer", answer)
    client.cookies.clear()
    response = client.post(
        "/chat/stream",
        json={"student_name": "Alice", "question": "普通问题", "deep_thinking": False},
    )
    event = json.loads(next(
        line.removeprefix("data: ") for line in response.text.splitlines()
        if line.startswith("data: ")
    ))
    assert event["type"] == "result"
    assert event["response"]["request_timing"]["budget_ms"] == (
        api_module.CHAT_REQUEST_TIMEOUT_SECONDS * 1000
    )


def test_streamed_chat_does_not_report_failed_answer_as_result(
    monkeypatch: pytest.MonkeyPatch, isolated_chat_service: None,
) -> None:
    async def failed_answer(*_args, **_kwargs):
        raise HTTPException(status_code=503, detail="生成未完成")

    monkeypatch.setattr(service, "try_fast_answer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "answer", failed_answer)
    client.cookies.clear()
    response = client.post(
        "/chat/stream",
        json={"student_name": "Alice", "question": "请推导量化状态误差上界。", "deep_thinking_explicit": True},
    )
    event = json.loads(next(
        line.removeprefix("data: ") for line in response.text.splitlines()
        if line.startswith("data: ")
    ))
    assert event == {"type": "error", "status": 503, "detail": "生成未完成"}


def test_chat_does_not_report_failed_answer_as_http_200(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def failed_answer(*_args, **_kwargs):
        raise HTTPException(
            status_code=503,
            detail="本轮回答未能完整生成或通过校验，请稍后重试或缩小问题范围。",
            headers={"Retry-After": "2"},
        )

    monkeypatch.setattr(service, "try_fast_answer", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(service, "answer", failed_answer)
    client.cookies.clear()
    response = client.post(
        "/chat",
        json={"student_name": "Alice", "question": "请推导量化状态误差上界。"},
    )

    assert response.status_code == 503
    assert response.headers.get("retry-after") == "2"
    assert "未能完整生成" in response.json()["detail"]
    assert "answer" not in response.json()


def test_chat_with_request_id_publishes_timeout_to_workflow_stream(
    short_chat_budget: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the caller supplies ``request_id``, a timeout must surface on the
    workflow-events SSE stream so the UI can render the 504 inline instead of
    leaving the rail spinning forever."""

    async def slow_answer(*_args, **_kwargs):
        await asyncio.sleep(5.0)
        raise AssertionError("wait_for guard should have fired before this runs")

    monkeypatch.setattr(service, "answer", slow_answer)

    published_errors: list[tuple[str, str]] = []
    real_publish_error = api_module.workflow_event_broker.publish_error

    def spy_publish_error(request_id: str, message: str) -> None:
        published_errors.append((request_id, message))
        real_publish_error(request_id, message)

    monkeypatch.setattr(api_module.workflow_event_broker, "publish_error", spy_publish_error)

    client.cookies.clear()

    response = client.post(
        "/chat?request_id=test-rid-timeout",
        json={
            "student_name": "Alice",
            "student_email": "alice@example.com",
            "course_context": None,
            "conversation_id": "conv-timeout-rid-test",
            "question": "请耐心等我一下。",
        },
    )

    assert response.status_code == 504
    assert any(
        rid == "test-rid-timeout" and "未完成响应" in msg for rid, msg in published_errors
    ), published_errors


def test_chat_returns_retryable_429_when_admission_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Saturated inference capacity must fail fast with a retryable status.

    Returning 429 here is intentional: an edge-visible 504 gives the user no
    indication whether the request was queued or lost, while a bounded
    admission response lets the UI retry without holding a worker for 80s.
    """

    monkeypatch.setattr(api_module, "CHAT_ADMISSION_TIMEOUT_SECONDS", 0.01)
    monkeypatch.setattr(api_module, "_chat_admission", asyncio.Semaphore(0))
    client.cookies.clear()

    response = client.post(
        "/chat",
        json={
            "student_name": "Alice",
            "student_email": "alice@example.com",
            "course_context": None,
            "conversation_id": "conv-admission-full",
            "question": "请稍后回答。",
        },
    )

    assert response.status_code == 429
    assert response.headers.get("retry-after") == "2"
    assert "正在排队" in response.json().get("detail", "")
    assert response.headers.get("x-queue-position") == "1"
    assert response.headers.get("x-sage-trace-id")


def test_greeting_uses_unified_workflow_and_exposes_request_boundary_timing() -> None:
    client.cookies.clear()
    response = client.post(
        "/chat",
        json={
            "student_name": "Alice",
            "conversation_id": "conv-fast-timing",
            "question": "你好",
        },
    )

    assert response.status_code == 200
    timing = response.json()["request_timing"]
    assert timing["trace_id"]
    assert timing["route"] == "sage_workflow"
    assert timing["total_duration_ms"] <= timing["budget_ms"]
    assert "request_parse" in timing["stage_durations_ms"]
    assert "fast_path_probe" not in timing["stage_durations_ms"]
    assert response.json()["exchange_id"]
    assert response.json()["memory_write_back"]
    assert any(step["key"] == "memory_persist" for step in response.json()["workflow_trace"])


def test_explicit_cancel_endpoint_interrupts_registered_request() -> None:
    request_id = "cancel-endpoint-test"
    controller = RequestCancellationController()
    api_module.active_chat_request_registry.register(request_id, controller)

    response = client.post("/chat/cancel", params={"request_id": request_id})

    assert response.status_code == 200
    assert response.json() == {"request_id": request_id, "cancelled": True}
    assert controller.is_cancelled()
    assert api_module.active_chat_request_registry.cancel(request_id) is False
