"""Planner telemetry must not hold a validated chat answer hostage."""

from __future__ import annotations

import logging
import threading
import time
from pathlib import Path

import sage_faculty_twin.service as service_module
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import (
    ChatRequest,
    ChatResponse,
    WorkflowPlanComparison,
    WorkflowPlanPreview,
)
from sage_faculty_twin.service import DigitalTwinService
from sage_faculty_twin.planner_comparison_store import PlannerComparisonStore


def _service_with_background_slot() -> DigitalTwinService:
    service = object.__new__(DigitalTwinService)
    service._planner_comparison_write_slot = threading.BoundedSemaphore(1)
    return service


def _request_and_response() -> tuple[ChatRequest, ChatResponse]:
    request = ChatRequest(student_name="Test", question="What is the plan?")
    response = ChatResponse(
        answer="The plan is ready.",
        owner_name="Test",
        used_model="test-model",
        planner_preview=WorkflowPlanPreview(
            planner_version="v1",
            policy_version="test",
            planner_mode="deterministic",
            execution_mode="direct",
            goal="answer",
            fallback_template="answer_question",
            explain_to_operator="Test plan.",
        ),
        planner_comparison=WorkflowPlanComparison(
            comparison_status="shadow_disabled",
            summary="Test comparison.",
        ),
    )
    return request, response


def test_slow_planner_write_never_blocks_answer_and_backpressure_is_visible(
    caplog, monkeypatch
) -> None:
    service = _service_with_background_slot()
    monkeypatch.setattr(service_module, "_PLANNER_COMPARISON_SLOW_WRITE_MS", 0.0)
    request, response = _request_and_response()
    entered = threading.Event()
    release = threading.Event()
    finished = threading.Event()
    calls = 0

    def slow_write(_request: ChatRequest, _response: ChatResponse) -> None:
        nonlocal calls
        calls += 1
        entered.set()
        assert release.wait(3)
        finished.set()

    service._persist_planner_comparison_result = slow_write
    started = time.perf_counter()
    service._persist_planner_comparison_background(request, response)
    assert time.perf_counter() - started < 0.5
    assert entered.wait(2)
    try:
        with caplog.at_level(logging.WARNING):
            service._persist_planner_comparison_background(request, response)
        assert "previous write still pending" in caplog.text
        assert calls == 1
    finally:
        release.set()
    assert finished.wait(2)
    deadline = time.monotonic() + 2
    while "planner comparison background write slow duration_ms=" not in caplog.text:
        assert time.monotonic() < deadline
        time.sleep(0.01)


def test_failed_planner_write_is_logged_and_next_write_can_start(caplog) -> None:
    service = _service_with_background_slot()
    request, response = _request_and_response()
    failed = threading.Event()
    succeeded = threading.Event()

    def fail(_request: ChatRequest, _response: ChatResponse) -> None:
        failed.set()
        raise RuntimeError("synthetic planner write failure")

    service._persist_planner_comparison_result = fail
    with caplog.at_level(logging.ERROR):
        service._persist_planner_comparison_background(request, response)
        assert failed.wait(2)
        deadline = time.monotonic() + 2
        while not service._planner_comparison_write_slot.acquire(blocking=False):
            assert time.monotonic() < deadline
            time.sleep(0.01)
        service._planner_comparison_write_slot.release()
    assert "planner comparison background write failed" in caplog.text

    service._persist_planner_comparison_result = lambda _request, _response: succeeded.set()
    service._persist_planner_comparison_background(request, response)
    assert succeeded.wait(2)


def test_planner_store_read_does_not_wait_for_slow_disk_write(tmp_path: Path) -> None:
    store = PlannerComparisonStore(
        AppSettings(planner_comparison_dir=tmp_path / "planner-comparisons")
    )
    entered = threading.Event()
    release = threading.Event()

    def slow_disk_write(_entry) -> None:
        entered.set()
        assert release.wait(3)

    store._persist_entry = slow_disk_write
    writer = threading.Thread(
        target=lambda: store.record_comparison(
            conversation_id="conv-test",
            exchange_id=None,
            workflow_action="answer",
            question="What is the plan?",
            comparison_status="shadow_disabled",
            deterministic_goal="answer",
            shadow_goal=None,
            same_goal=True,
            same_fallback_template=True,
            deterministic_only_steps=[],
            shadow_only_steps=[],
            summary="Test comparison.",
        ),
        daemon=True,
    )
    writer.start()
    assert entered.wait(2)
    try:
        started = time.perf_counter()
        assert store.count_records() == 1
        assert len(store.list_records()) == 1
        assert time.perf_counter() - started < 0.5
    finally:
        release.set()
        writer.join(2)
    assert not writer.is_alive()
