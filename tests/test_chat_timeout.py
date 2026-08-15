"""Regression tests for the /chat request budget guard.

Cloudflare's free/Pro tier enforces an ~100s edge timeout. The backend wraps
``service.answer`` in :func:`asyncio.wait_for` with a slightly smaller budget
(`CHAT_REQUEST_TIMEOUT_SECONDS`) so we can return a structured 504 *before*
the proxy gives up. These tests pin that contract.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from sage_faculty_twin import api as api_module
from sage_faculty_twin.api import app, service
from sage_faculty_twin.request_context import RequestCancellationController

client = TestClient(app)


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


def test_fast_chat_exposes_request_boundary_timing() -> None:
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
    assert timing["route"] == "fast_path"
    assert timing["total_duration_ms"] <= timing["budget_ms"]
    assert "request_parse" in timing["stage_durations_ms"]
    assert "fast_path_probe" in timing["stage_durations_ms"]


def test_explicit_cancel_endpoint_interrupts_registered_request() -> None:
    request_id = "cancel-endpoint-test"
    controller = RequestCancellationController()
    api_module.active_chat_request_registry.register(request_id, controller)

    response = client.post("/chat/cancel", params={"request_id": request_id})

    assert response.status_code == 200
    assert response.json() == {"request_id": request_id, "cancelled": True}
    assert controller.is_cancelled()
    assert api_module.active_chat_request_registry.cancel(request_id) is False
