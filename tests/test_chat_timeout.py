"""HTTP observer budgets and native durable worker admission contracts.

An observer timeout reports 504 without cancelling durable work; native
first-claim backpressure reports 429 and keeps the same request identity.
Producer deadlines and explicit cancellation remain separate boundaries.
"""

from __future__ import annotations

import asyncio
import json

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from sage_faculty_twin import api as api_module
from sage_faculty_twin.api import app, service
from sage_faculty_twin.models import ChatResponse

client = TestClient(app)




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


@pytest.fixture(autouse=True)
def isolated_chat_service(tmp_path, monkeypatch: pytest.MonkeyPatch):
    """Real authority/state and native lifecycle; only model behavior is mocked."""
    from test_http_workflow_integration import http_client
    from sage_faculty_twin.config import AppSettings
    from sage_faculty_twin.service import DigitalTwinService

    instance = DigitalTwinService(AppSettings(runtime_dir=tmp_path, model_name="fixture"))
    monkeypatch.setattr(api_module, "CHAT_MAX_INFLIGHT_REQUESTS", 1)
    monkeypatch.setattr(service, "_instance", instance)
    with http_client(service, monkeypatch, request_budget=None) as active_client:
        monkeypatch.setitem(globals(), "client", active_client)
        yield


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
    assert event == {"type": "error", "status": 503,
                     "detail": "本轮回答未能完整生成或通过校验，请稍后重试。"}


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


def test_observer_timeout_does_not_close_durable_workflow_stream(
    short_chat_budget: float, monkeypatch: pytest.MonkeyPatch
) -> None:
    """HTTP 504 ends this observer, not the persistent producer's event stream."""

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
    from sage_faculty_twin.guest_identity import GUEST_COOKIE_NAME
    guest = service._guest_identity_store.authenticate(client.cookies.get(GUEST_COOKIE_NAME))
    expected_key = api_module._owned_chat_request_key(f"guest:{guest.subject_id}", "test-rid-timeout")
    assert published_errors == []
    assert api_module.http_workflow.journal.read(expected_key) is not None
    assert not api_module.workflow_event_broker._journal().read(expected_key)["closed"]


def test_chat_returns_retryable_429_when_admission_is_full(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Saturated inference capacity must fail fast with a retryable status.

    Returning 429 here is intentional: an edge-visible 504 gives the user no
    indication whether the request was queued or lost, while a bounded
    admission response lets the UI retry without holding a worker for 80s.
    """

    monkeypatch.setattr(api_module, "CHAT_ADMISSION_TIMEOUT_SECONDS", 1)
    client.cookies.clear()
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from sage_faculty_twin.guest_identity import GUEST_COOKIE_NAME

    entered, release = threading.Event(), threading.Event()
    calls = []
    credential, guest = service._guest_identity_store.issue()
    client.cookies.set(GUEST_COOKIE_NAME, credential)

    async def answer(payload, **kwargs):
        calls.append(payload.question)
        if payload.question == "occupy":
            entered.set()
            assert await asyncio.to_thread(release.wait, 5)
        return ChatResponse(answer="complete", owner_name="Twin", used_model="fixture")

    monkeypatch.setattr(service, "answer", answer)
    payload = {"student_name": "Guest", "question": "queued"}
    with ThreadPoolExecutor(max_workers=1) as pool:
        first = pool.submit(client.post, "/chat?request_id=occupy",
                            json={"student_name": "Guest", "question": "occupy"})
        try:
            assert entered.wait(3)
            monkeypatch.setattr(api_module, "CHAT_ADMISSION_TIMEOUT_SECONDS", 0.01)
            response = client.post("/chat?request_id=queued", json=payload)
            assert response.status_code == 429, response.text
            assert response.headers.get("retry-after") == "2"
            assert "正在排队" in response.json().get("detail", "")
            assert response.headers.get("x-sage-trace-id") == "queued"
            # Do not invent a queue position: only native pending state is known.
            assert "x-queue-position" not in response.headers
            key = api_module._owned_chat_request_key(f"guest:{guest.subject_id}", "queued")
            task = api_module.http_workflow.journal.read(key)["tasks"]["answer"]
            assert task["status"] == "pending" and task["attempt"] == 0
        finally:
            release.set()
        assert first.result(timeout=5).status_code == 200
        monkeypatch.setattr(api_module, "CHAT_ADMISSION_TIMEOUT_SECONDS", 1)
        assert client.post("/chat?request_id=queued", json=payload).status_code == 200
        assert calls == ["occupy", "queued"]


def test_greeting_uses_unified_workflow_and_exposes_request_boundary_timing(tmp_path, monkeypatch) -> None:
    # The autouse fixture owns the isolated real service and its worker loop.
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


def test_explicit_cancel_endpoint_interrupts_durable_request(monkeypatch) -> None:
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event
    from sage_faculty_twin.guest_identity import GUEST_COOKIE_NAME
    token, guest = service._guest_identity_store.issue()
    client.cookies.set(GUEST_COOKIE_NAME, token)
    request_id = "cancel-endpoint-test"
    entered, interrupted = Event(), Event()
    async def answer(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Event().wait()
        finally:
            interrupted.set()
    monkeypatch.setattr(service, "answer", answer)
    with ThreadPoolExecutor(max_workers=1) as pool:
        pending = pool.submit(client.post, "/chat", params={"request_id": request_id},
                              json={"student_name": "Guest", "question": "cancel me"})
        assert entered.wait(3)
        response = client.post("/chat/cancel", params={"request_id": request_id})
        assert response.status_code == 200
        assert response.json() == {"request_id": request_id, "cancelled": True}
        assert interrupted.wait(3)
        assert pending.result(timeout=3).status_code == 499
    key = api_module._owned_chat_request_key(f"guest:{guest.subject_id}", request_id)
    task = api_module.http_workflow.journal.read(key)["tasks"]["answer"]
    assert task["status"] == "failed" and task["failure_code"] == "request_cancelled"
    assert client.post("/chat/cancel", params={"request_id": request_id}).json()["cancelled"] is False
