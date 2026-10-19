"""Emit content-free REAL SAGE events using synthetic, side-effect-free ports.

PYTHONPATH=src:tests python tools/export_native_workflow_fixture.py > events.json
This is a visual fixture, never production/model/booking acceptance evidence.
"""

import json
import argparse
from threading import Barrier
from types import SimpleNamespace

from sage.runtime.adaptive_workflow import WorkflowCapabilities
from sage.runtime.execution_observation import ExecutionObserver
from sage.runtime.evidence_workflow import EvidenceWorkflowRuntime
from sage.runtime.source_answer_plan import PlanSource

from sage_faculty_twin.chat_runtime import ChatRuntime
from sage_faculty_twin.knowledge_source_runtime import KnowledgeSourceRuntime
from sage_faculty_twin.retrieval_runtime import RetrievalRuntime
from test_chat_runtime import TraceContext, ports

events = []
parser = argparse.ArgumentParser()
parser.add_argument("--repair", action="store_true")
parser.add_argument("--research", action="store_true")
options = parser.parse_args()
runtime, retrieval, sources, quality = ChatRuntime(), RetrievalRuntime(), KnowledgeSourceRuntime(), EvidenceWorkflowRuntime()
barrier = Barrier(2)


class NoEffectStore:
    def search(self, *args, **kwargs):
        return []


class NoEffectQuality:
    def assess(self, request):
        return json.dumps({"digest": request.digest, "items": [{"requirement_id": r.requirement_id,
            "witnesses": [] if options.repair and len(request.sources)==1 else [
                {"source_id": s.source_id, "quote": s.content} for s in request.sources]}
            for r in request.requirements]})

    def supplement(self, queries):
        return (PlanSource("synthetic-b", "B", "", "Synthetic B source."),)

    def compose(self, request, assessment):
        return json.dumps({"digest": request.digest, "source_ids": [s.source_id for s in request.sources],
            "inferences": ["Synthetic prospective proposal, not a model-quality result."] if options.research else []})

    def review(self, request, plan):
        return json.dumps({"digest": request.digest, "source_ids": plan.source_ids, "inferences": plan.inferences})


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
                memory="synthetic-memory", knowledge="synthetic-knowledge", timeout=8),
            answer=lambda: quality.run("Synthetic A and B?",
                (PlanSource("synthetic-a", "A", "", "Synthetic A source."),), NoEffectQuality(),
                timeout=8, research=options.research)), timeout=15)
finally:
    runtime.close()
    retrieval.close()
    sources.close()
    quality.close()
print(json.dumps([event.to_dict() for event in events]))
