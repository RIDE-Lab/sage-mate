from __future__ import annotations

import threading
from time import perf_counter

import pytest

from sage_faculty_twin.models import ChatResponse, WorkflowTraceStep
from sage_faculty_twin.request_context import (
    RequestCancellationController,
    RequestCancelledError,
    bounded_request_timeout,
    raise_if_request_cancelled,
    register_request_cancel_callback,
    request_cancellation_scope,
    request_remaining_seconds,
)
from sage_faculty_twin.request_timing import RequestTimingLedger


def test_request_scope_caps_io_timeout_by_absolute_deadline() -> None:
    event = threading.Event()
    with request_cancellation_scope(event, deadline_at=perf_counter() + 2.0):
        remaining = request_remaining_seconds()
        assert remaining is not None
        assert 0.0 < remaining <= 2.0
        assert 0.1 <= bounded_request_timeout(60.0, reserve_seconds=0.25) <= 1.75


def test_request_scope_rejects_cancelled_and_expired_work() -> None:
    event = threading.Event()
    event.set()
    with request_cancellation_scope(event, deadline_at=perf_counter() + 10.0):
        with pytest.raises(RequestCancelledError, match="cancelled"):
            raise_if_request_cancelled()

    with request_cancellation_scope(threading.Event(), deadline_at=perf_counter() - 0.01):
        with pytest.raises(RequestCancelledError, match="deadline"):
            bounded_request_timeout(60.0)


def test_request_controller_interrupts_active_io_but_not_unregistered_io() -> None:
    controller = RequestCancellationController()
    interrupted: list[str] = []
    completed: list[str] = []

    with request_cancellation_scope(controller, deadline_at=perf_counter() + 10.0):
        unregister_completed = register_request_cancel_callback(
            lambda: completed.append("closed")
        )
        register_request_cancel_callback(lambda: interrupted.append("closed"))
        unregister_completed()
        controller.cancel()

    assert interrupted == ["closed"]
    assert completed == []
    assert controller.is_cancelled()


def test_timing_ledger_attaches_reconcilable_public_diagnostics() -> None:
    ledger = RequestTimingLedger(trace_id="trace-123", budget_seconds=80.0)
    ledger.runtime_diagnostics.record_llm_call()
    ledger.runtime_diagnostics.record_llm_ttft(42.5)
    ledger.runtime_diagnostics.record_llm_complete(125.0)
    stage_started = perf_counter()
    ledger.record("request_parse", stage_started)
    response = ChatResponse(
        answer="ok",
        owner_name="owner",
        used_model="model",
        workflow_trace=[
            WorkflowTraceStep(
                key="llm_answer",
                title="answer",
                summary="done",
                detail="done",
                duration_ms=125,
            )
        ],
    )

    attached = ledger.attach(response, route="sage_workflow")

    assert attached.request_timing is not None
    assert attached.request_timing.trace_id == "trace-123"
    assert attached.request_timing.route == "sage_workflow"
    assert attached.request_timing.workflow_trace_reported_ms == 125
    assert attached.request_timing.llm_call_count == 1
    assert attached.request_timing.llm_ttft_ms == 42.5
    assert attached.request_timing.llm_total_duration_ms == 125.0
    assert attached.request_timing.unattributed_duration_ms < 2000
    assert "request_parse" in attached.request_timing.stage_durations_ms
    assert attached.request_timing.total_duration_ms <= attached.request_timing.budget_ms
