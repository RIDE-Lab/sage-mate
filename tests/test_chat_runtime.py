from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from sage.runtime.execution_observation import ExecutionObserver
from sage.runtime.flownet import FlowRequestOutcomeError
from sage.runtime.adaptive_workflow import WorkflowCapabilities, WorkflowRequirements, plan_workflow

from sage_faculty_twin.chat_runtime import ChatRuntime
from sage_faculty_twin.request_context import (
    RequestCancellationController, RequestCancelledError, request_cancellation_scope,
)


STAGES = ("bootstrap", "understand", "plan", "prepare_booking", "execute_booking",
          "write_knowledge", "retrieve", "prompt", "answer", "render")


def expected_stages(booking, complete=True):
    return [name for name in STAGES if name != "write_knowledge" and
            (name != "prepare_booking" or booking) and
            (name != "execute_booking" or booking and complete) and
            (name not in {"retrieve", "prompt", "answer"} or not booking)]


class TraceContext(list):
    def __init__(self, values, *, booking=True, complete=True, requirements=None):
        super().__init__(values)
        self.interaction_intent = SimpleNamespace(action="book_meeting" if booking else "answer")
        self.booking_decision = SimpleNamespace(request=object() if complete else None)
        self.booking = booking
        self.complete = complete
        self.requirements = requirements
        self.adaptive_plan = plan_workflow(
            requirements or WorkflowRequirements(action="book_meeting" if booking else "answer"),
            WorkflowCapabilities("guest", ("wiki",), True))


class Stage:
    def __init__(self, name, marker, hook=None):
        self.name = name
        self.marker = marker
        self.hook = hook

    def execute(self, value):
        if self.hook:
            self.hook()
        return TraceContext([*value, (self.name, self.marker)],
                            booking=getattr(value, "booking", True),
                            complete=getattr(value, "complete", True),
                            requirements=getattr(value, "requirements", None))


def ports(marker, **hooks):
    return {name: Stage(name, marker, hooks.get(name)) for name in STAGES}


def test_graph_is_reused_and_concurrent_ports_do_not_cross_requests():
    runtime = ChatRuntime()
    barrier = Barrier(2)
    try:
        identity = runtime._endpoint.flow_instance.instance_id

        def request(index):
            return runtime.answer(TraceContext([], booking=bool(index % 2)),
                                  ports(index, understand=lambda: barrier.wait(3)), timeout=5)

        with ThreadPoolExecutor(max_workers=2) as callers:
            outputs = list(callers.map(request, range(6)))
        for index, (response, context) in enumerate(outputs):
            assert response == [(name, index) for name in expected_stages(bool(index % 2))]
            assert context == response[:-1]
        assert runtime._endpoint.flow_instance.instance_id == identity
        assert runtime._endpoint._requests == {}
    finally:
        runtime.close()


@pytest.mark.parametrize("booking,complete", [(False, True), (True, False), (True, True)])
def test_native_intent_branch_does_not_execute_rejected_booking_nodes(booking, complete):
    runtime = ChatRuntime()
    events, effects = [], []
    try:
        with ExecutionObserver(events.append).bind():
            response, _ = runtime.answer(TraceContext([], booking=booking, complete=complete),
                ports("branch", prepare_booking=lambda: effects.append("prepare"),
                      execute_booking=lambda: effects.append("submit")), timeout=5)
        assert effects == (["prepare", "submit"] if booking and complete else ["prepare"] if booking else [])
        observed = [e.operator_key for e in events if e.state == "started"]
        assert observed.count("prepare-booking") == int(booking)
        assert observed.count("execute-booking") == int(booking and complete)
        assert observed.count("render") == 1
        expected = expected_stages(booking, complete)
        assert response == [(name,"branch") for name in expected]
        assert runtime._endpoint._requests == {}
    finally:
        runtime.close()


def test_failed_stage_is_not_replayed_and_graph_recovers():
    runtime = ChatRuntime()
    calls = []

    def fail():
        calls.append("failed")
        raise ValueError("injected_chat_failure")

    try:
        with pytest.raises(FlowRequestOutcomeError):
            runtime.answer([], ports("bad", execute_booking=fail,
                                     write_knowledge=lambda: calls.append("after")), timeout=3)
        assert calls == ["failed"]
        response, _ = runtime.answer([], ports("good"), timeout=3)
        assert response == [(name, "good") for name in expected_stages(True)]
        assert runtime._endpoint._requests == {}
    finally:
        runtime.close()


@pytest.mark.parametrize("requirements,executed", [
    (WorkflowRequirements(resolved=True), []),
    (WorkflowRequirements(self_contained=True), ["prompt", "answer"]),
    (WorkflowRequirements(research_review=True), ["retrieve", "prompt", "answer"]),
])
def test_native_adaptive_routes_do_not_execute_unselected_work(requirements, executed):
    runtime = ChatRuntime()
    events = []
    try:
        with ExecutionObserver(events.append).bind():
            response, _ = runtime.answer(TraceContext([], booking=False, requirements=requirements),
                                         ports("adaptive"), timeout=5)
        expected = ["bootstrap", "understand", "plan", *executed, "render"]
        assert response == [(name, "adaptive") for name in expected]
        observed = [event.operator_key for event in events if event.state == "started"]
        for name in {"retrieve", "prompt", "answer", "write_knowledge"}:
            assert observed.count(name.replace("_", "-")) == int(name in executed)
        assert observed.count("render") == 1
        assert runtime._endpoint._requests == {}
    finally:
        runtime.close()


def test_cancelled_request_drains_without_running_later_side_effects():
    runtime = ChatRuntime()
    controller = RequestCancellationController()
    entered, release = Event(), Event()
    later = []

    def block():
        entered.set()
        assert release.wait(3)

    def request():
        with request_cancellation_scope(controller):
            return runtime.answer([], ports("cancel", understand=block,
                                            execute_booking=lambda: later.append(True)), timeout=5)

    try:
        with ThreadPoolExecutor(max_workers=1) as caller:
            future = caller.submit(request)
            try:
                assert entered.wait(3)
                controller.cancel()
            finally:
                release.set()
            with pytest.raises(RequestCancelledError):
                future.result(timeout=3)
        assert later == []
        assert controller._callbacks == {}
        assert runtime._endpoint._requests == {}
        runtime.answer([], ports("next"), timeout=3)
    finally:
        release.set()
        runtime.close()
