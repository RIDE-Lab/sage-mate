from __future__ import annotations

import threading
from time import perf_counter

import pytest

from sage_faculty_twin.models import ChatResponse, WorkflowTraceStep
from sage_faculty_twin.request_context import (
    RequestCancelledError,
    bounded_request_timeout,
    raise_if_request_cancelled,
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


def test_timing_ledger_attaches_reconcilable_public_diagnostics() -> None:
    ledger = RequestTimingLedger(trace_id="trace-123", budget_seconds=80.0)
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
    assert "request_parse" in attached.request_timing.stage_durations_ms
    assert attached.request_timing.total_duration_ms <= attached.request_timing.budget_ms
