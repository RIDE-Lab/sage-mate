from concurrent.futures import ThreadPoolExecutor
from threading import Barrier

import pytest
from sage.runtime.flownet.client.runtime_client import FlowRequestOutcomeError

from sage_faculty_twin.retrieval_runtime import RetrievalRuntime


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
