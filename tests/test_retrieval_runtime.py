from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from time import monotonic

import pytest
from sage.runtime.flownet.client.runtime_client import FlowRequestOutcomeError

from sage_faculty_twin.retrieval_runtime import RetrievalRuntime
from sage_faculty_twin.request_context import (
    RequestCancellationController, RequestCancelledError, request_cancellation_scope,
)


class Ports:
    def __init__(self, marker):
        self.marker = marker
        self.barrier = Barrier(2)

    def retrieve_memory(self, value):
        self.barrier.wait(timeout=3)
        return self.marker, value

    def retrieve_knowledge(self, value):
        self.barrier.wait(timeout=3)
        return self.marker, value


def test_persistent_native_graph_parallelism_and_request_scope():
    runtime = RetrievalRuntime()
    try:
        endpoint_id = runtime._endpoint.flow_instance.instance_id

        def request(index):
            return runtime.retrieve(Ports(index), memory="m", knowledge="k", timeout=5)

        with ThreadPoolExecutor(max_workers=2) as callers:
            assert list(callers.map(request, range(6))) == [
                ((index, "m"), (index, "k")) for index in range(6)
            ]
        assert runtime._endpoint.flow_instance.instance_id == endpoint_id
        assert runtime._endpoint._requests == {}
    finally:
        runtime.close()


def test_failed_branch_releases_request_and_endpoint_recovers():
    class FailingPorts(Ports):
        def retrieve_knowledge(self, value):
            self.barrier.wait(timeout=3)
            raise ValueError("retrieval_test_failure")

    runtime = RetrievalRuntime()
    try:
        with pytest.raises(FlowRequestOutcomeError):
            runtime.retrieve(FailingPorts("bad"), memory="m", knowledge="k", timeout=5)
        assert runtime._endpoint._requests == {}
        assert runtime.retrieve(Ports("next"), memory="m", knowledge="k", timeout=5) == (
            ("next", "m"), ("next", "k"),
        )
        assert runtime._endpoint._requests == {}
    finally:
        runtime.close()


def test_concurrent_failure_does_not_mix_or_poison_other_request():
    """One endpoint serves distinct request ports even when a peer fails."""
    entered, release = Event(), Event()

    class FailingPorts(Ports):
        def retrieve_knowledge(self, value):
            self.barrier.wait(timeout=3)
            entered.set()
            assert release.wait(5)
            raise ValueError("isolated_peer_failure")

    runtime = RetrievalRuntime()
    try:
        with ThreadPoolExecutor(max_workers=2) as callers:
            failed = callers.submit(runtime.retrieve, FailingPorts("failed-owner"),
                                    memory="private-a", knowledge="wiki-a", timeout=8)
            assert entered.wait(3)
            healthy = callers.submit(runtime.retrieve, Ports("healthy-owner"),
                                     memory="private-b", knowledge="wiki-b", timeout=8)
            try:
                assert healthy.result(timeout=3) == (
                    ("healthy-owner", "private-b"), ("healthy-owner", "wiki-b"))
            finally:
                release.set()
            with pytest.raises(FlowRequestOutcomeError):
                failed.result(timeout=3)
        assert runtime._endpoint._requests == {}
        assert runtime.retrieve(Ports("after"), memory="m", knowledge="k", timeout=5) == (
            ("after", "m"), ("after", "k"))
    finally:
        release.set()
        runtime.close()


def test_http_scope_cancellation_aborts_native_request_and_unregisters():
    entered = Event()
    release = Event()
    controller = RequestCancellationController()

    class SlowPorts(Ports):
        def retrieve_knowledge(self, value):
            self.barrier.wait(timeout=3)
            entered.set()
            assert release.wait(3)
            return value

    runtime = RetrievalRuntime()
    try:
        def request():
            with request_cancellation_scope(controller):
                return runtime.retrieve(SlowPorts("slow"), memory="m", knowledge="k", timeout=5)

        with ThreadPoolExecutor(max_workers=1) as caller:
            running = caller.submit(request)
            try:
                assert entered.wait(3)
                controller.cancel()
            finally:
                release.set()
            with pytest.raises(RequestCancelledError):
                running.result(timeout=3)
        assert controller._callbacks == {}
        assert runtime._endpoint._requests == {}
        assert runtime.retrieve(Ports("next"), memory="m", knowledge="k", timeout=5) == (
            ("next", "m"), ("next", "k"),
        )
    finally:
        release.set()
        runtime.close()


def test_deadline_discards_late_branch_without_poisoning_next_owner():
    """Cooperative deadlines do not kill blocked code or mix request results."""
    entered, release, finished = Event(), Event(), Event()

    class LatePorts(Ports):
        def retrieve_knowledge(self, value):
            self.barrier.wait(timeout=3)
            entered.set()
            try:
                assert release.wait(5)
                return self.marker, value
            finally:
                finished.set()

    runtime = RetrievalRuntime()
    try:
        endpoint_id = runtime._endpoint.flow_instance.instance_id
        with ThreadPoolExecutor(max_workers=1) as callers:
            started = monotonic()
            expired = callers.submit(runtime.retrieve, LatePorts("expired-owner"),
                                     memory="private-old", knowledge="wiki-old", timeout=0.5)
            try:
                assert entered.wait(3)
                assert runtime.retrieve(Ports("new-owner"), memory="private-new",
                                        knowledge="wiki-new", timeout=3) == (
                    ("new-owner", "private-new"), ("new-owner", "wiki-new"))
                # Deliberately keep the uncooperative reader past its deadline.
                Event().wait(max(0, started + 0.6 - monotonic()))
            finally:
                release.set()
            assert finished.wait(3)
            with pytest.raises((TimeoutError, FlowRequestOutcomeError)):
                expired.result(timeout=3)
            assert expired.done()  # Do not mistake Future.result timeout for runtime failure.
        assert runtime.retrieve(Ports("after-late"), memory="m", knowledge="k", timeout=3) == (
            ("after-late", "m"), ("after-late", "k"))
        assert runtime._endpoint.flow_instance.instance_id == endpoint_id
        assert runtime._endpoint._requests == {}
    finally:
        release.set()
        runtime.close()
