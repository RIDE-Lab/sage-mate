"""Emit content-free REAL SAGE events using synthetic, side-effect-free ports.

PYTHONPATH=src:tests python tools/export_native_workflow_fixture.py > events.json
This is a visual fixture, never production/model/booking acceptance evidence.
"""

import json
from threading import Barrier
from types import SimpleNamespace

from sage.runtime.adaptive_workflow import WorkflowCapabilities
from sage.runtime.execution_observation import ExecutionObserver

from sage_faculty_twin.chat_runtime import ChatRuntime
from sage_faculty_twin.knowledge_source_runtime import KnowledgeSourceRuntime
from sage_faculty_twin.retrieval_runtime import RetrievalRuntime
from test_chat_runtime import TraceContext, ports

events = []
runtime, retrieval, sources = ChatRuntime(), RetrievalRuntime(), KnowledgeSourceRuntime()
barrier = Barrier(2)


class NoEffectStore:
    def search(self, *args, **kwargs):
        return []


def memory(value):
    barrier.wait(timeout=3)
    return value


def knowledge(value):
    barrier.wait(timeout=3)
    return sources.search(wiki=NoEffectStore(), private=NoEffectStore(), timeout=5,
        request={"query": "synthetic", "limit": 3, "wiki": True, "private": True,
                 "visitor_profile": "lab_member", "admin_role": None})


try:
    with ExecutionObserver(events.append).bind():
        runtime.answer(TraceContext([], booking=False, capabilities=WorkflowCapabilities(
            "account", ("wiki", "private"), True)), ports("visual-fixture", retrieve=lambda:
            retrieval.retrieve(SimpleNamespace(retrieve_memory=memory, retrieve_knowledge=knowledge),
                memory="synthetic-memory", knowledge="synthetic-knowledge", timeout=8)), timeout=10)
finally:
    runtime.close()
    retrieval.close()
    sources.close()
print(json.dumps([event.to_dict() for event in events]))
