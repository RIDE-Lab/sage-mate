"""Twin's retrieval graph on the native SAGE local runtime.

Request-scoped application ports stay out of messages. SAGE owns scheduling,
request accounting and actor lifetime; this module defines business operators.
"""

from contextvars import ContextVar
from typing import Any

from sage.runtime.flownet import actor, bootstrap_runtime, build_runtime_client, flow


_ports: ContextVar[Any] = ContextVar("twin_retrieval_ports")


@actor(uri="actor://twin/retrieval/memory", namespace="twin")
class MemoryReader:
    def run(self, inputs):
        return "memory", _ports.get().retrieve_memory(inputs["memory"])


@actor(uri="actor://twin/retrieval/knowledge", namespace="twin")
class KnowledgeReader:
    def run(self, inputs):
        return "knowledge", _ports.get().retrieve_knowledge(inputs["knowledge"])


@flow(uri="flow://twin/retrieval", namespace="twin")
def retrieval_flow(init_stream):
    return init_stream.map(MemoryReader().run).connect(init_stream.map(KnowledgeReader().run))


class RetrievalRuntime:
    def __init__(self):
        self._bootstrap = bootstrap_runtime(node_mode="local")
        try:
            client = build_runtime_client(owner="twin", runtime_host=self._bootstrap.runtime_host)
            self._endpoint = retrieval_flow.bind("twin-retrieval-in", "twin-retrieval-out").endpoint(
                client=client, reuse_existing=False,
            )
        except BaseException:
            self._bootstrap.shutdown()
            raise

    def retrieve(self, support, *, memory, knowledge, timeout: float):
        token = _ports.set(support)
        try:
            outputs = self._endpoint.call({"memory": memory, "knowledge": knowledge}, timeout=timeout)
        finally:
            _ports.reset(token)
        results = {}
        for name, result in outputs:
            if name not in {"memory", "knowledge"} or name in results:
                raise RuntimeError("invalid_retrieval_branch_output")
            results[name] = result
        if set(results) != {"memory", "knowledge"}:
            raise RuntimeError("incomplete_retrieval_branch_outputs")
        return results["memory"], results["knowledge"]

    def close(self):
        self._endpoint.close()
        self._bootstrap.shutdown()
