from types import SimpleNamespace

from sage_faculty_twin.evidence_ports import TwinEvidencePorts
from sage_faculty_twin.models import ChatRequest, KnowledgeSearchHit
from sage_faculty_twin.service import FacultyTwinWorkflowSupport


def test_repair_search_and_hydration_reuse_captured_verified_scope():
    request = ChatRequest(student_name="guest", question="公开 Wiki 里 B 是什么？", knowledge_sources=["wiki"])
    old = KnowledgeSearchHit(document_id="existing", title="A", excerpt="A", score=1, source_content="A")
    new = KnowledgeSearchHit(document_id="wiki-new", title="B", excerpt="preview", score=1,
        source_content="B complete text", metadata={"wiki_url": "https://example.org/b"})
    context = SimpleNamespace(request=request, knowledge_hits=[old])
    calls = []

    def search(query, **kwargs):
        calls.append(("search", query, kwargs))
        return [old, new]

    def resolve(captured, hits, *, admin_role):
        calls.append(("hydrate", captured, admin_role))
        return hits

    support = SimpleNamespace(_resolve_admin_role=lambda: None,
        _knowledge_selection_kwargs=FacultyTwinWorkflowSupport._knowledge_selection_kwargs,
        _knowledge_retriever=SimpleNamespace(search=search), _resolve_knowledge_evidence=resolve)
    ports = TwinEvidencePorts(support, context)
    # A later mutable context/model proposal cannot widen the captured request.
    request.knowledge_sources = ["private"]
    request.visitor_profile = "lab_member"
    sources = ports.supplement(("B 是什么？",))
    assert calls[0][1] == "B 是什么？"
    assert calls[0][2]["selection"].origins == frozenset({"wiki"})
    assert calls[0][2]["admin_role"] is None
    assert calls[0][2]["visitor_profile"] != "lab_member"
    assert calls[1][1].knowledge_sources == ["wiki"] and calls[1][2] is None
    assert len(sources) == 1 and sources[0].source_id == "knowledge:1"
    assert sources[0].content == new.source_content
    assert context.knowledge_hits == [old, new]
