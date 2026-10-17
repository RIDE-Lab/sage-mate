from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event

import pytest
from sage.runtime.flownet import FlowRequestOutcomeError

from sage_faculty_twin.chat_runtime import ChatRuntime
from sage_faculty_twin.request_context import (
    RequestCancellationController, RequestCancelledError, request_cancellation_scope,
)


STAGES = ("bootstrap", "understand", "prepare_booking", "execute_booking",
          "write_knowledge", "retrieve", "prompt", "answer", "render")


class Stage:
    def __init__(self, name, marker, hook=None):
        self.name = name
        self.marker = marker
        self.hook = hook

    def execute(self, value):
        if self.hook:
            self.hook()
        return [*value, (self.name, self.marker)]


def ports(marker, **hooks):
    return {name: Stage(name, marker, hooks.get(name)) for name in STAGES}


def test_graph_is_reused_and_concurrent_ports_do_not_cross_requests():
    runtime = ChatRuntime()
    barrier = Barrier(2)
    try:
        identity = runtime._endpoint.flow_instance.instance_id

        def request(index):
            return runtime.answer([], ports(index, understand=lambda: barrier.wait(3)), timeout=5)

        with ThreadPoolExecutor(max_workers=2) as callers:
            outputs = list(callers.map(request, range(6)))
        for index, (response, context) in enumerate(outputs):
            assert response == [(name, index) for name in STAGES]
            assert context == response[:-1]
        assert runtime._endpoint.flow_instance.instance_id == identity
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
        assert response == [(name, "good") for name in STAGES]
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
