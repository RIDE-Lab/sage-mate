from types import SimpleNamespace

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import WebSearchHit
from sage_faculty_twin.service import DigitalTwinService
from test_memory_ownership import persist


def test_feedback_only_promotes_cited_snapshots_without_private_conversation(tmp_path):
    service = DigitalTwinService(AppSettings(
        knowledge_base_dir=tmp_path / "knowledge", conversation_memory_dir=tmp_path / "memory",
        knowledge_backend="local", conversation_memory_index_type="segment"))
    support = service._build_support()
    record = persist(service._conversation_store, "account:private-owner")
    record.question = "PRIVATE_QUESTION_123"
    record.answer = "PRIVATE_ANSWER_456 [reference](https://example.org/three)"
    record.web_search_hits = [WebSearchHit(title=f"Reference {i}", url=f"https://example.org/{i}",
                                          snippet=f"Full source evidence {i}")
                              for i in ("one", "two", "three", "four")]
    positive = SimpleNamespace(rating="up", resolved=True)
    results = support._write_back_feedback_knowledge(record, positive)
    assert len(results) == 1
    document = service.list_knowledge()[0]
    serialized = document.model_dump_json()
    assert "Full source evidence three" in serialized
    for forbidden in (record.question, "PRIVATE_ANSWER_456", record.memory_id, record.owner_id):
        assert forbidden not in serialized
    assert "Full source evidence one" not in serialized

    # Same immutable source snapshot cannot overwrite an administrator's edits.
    reviewed = service._knowledge_store.get_document(document.document_id)
    from sage_faculty_twin.models import KnowledgeDocumentCreate
    service._knowledge_store.update_document(document.document_id, KnowledgeDocumentCreate(
        title="Curator's corrected title", content="Curator's verified material",
        source_name=reviewed.source_name, tags=reviewed.tags,
        metadata={**reviewed.metadata, "review_status": "approved"}))
    repeated = support._write_back_feedback_knowledge(record, positive)
    assert not repeated[0].created
    kept = service._knowledge_store.get_document(document.document_id)
    assert kept.title == "Curator's corrected title"
    assert kept.review_status == "approved"

    # A changed source is a new review candidate, never a rewrite of the approved version.
    record.web_search_hits[2].snippet = "Changed source evidence"
    changed = support._write_back_feedback_knowledge(record, positive)
    assert changed[0].created
    assert len(service.list_knowledge()) == 2
    assert service._knowledge_store.get_document(document.document_id).review_status == "approved"


def test_source_match_is_exact_and_payload_is_lossless(tmp_path):
    service = DigitalTwinService(AppSettings(
        knowledge_base_dir=tmp_path / "knowledge", conversation_memory_dir=tmp_path / "memory",
        knowledge_backend="local", conversation_memory_index_type="segment"))
    support = service._build_support()
    hit = WebSearchHit(title="Reference Documentation", url="https://example.org/a", snippet="Evidence")
    for answer in ("Reference Documentation Evidence", "https://example.org/ab", "https://example.org/a/other"):
        assert not support._answer_references_web_hit(answer, hit)
    assert support._answer_references_web_hit("[source](https://example.org/a)", hit)
    assert support._answer_references_web_hit("先看[source](https://example.org/a)，然后继续讨论。", hit)
    record = persist(service._conversation_store, "account:owner")
    hit = WebSearchHit(title="T" * 300, url="https://example.org/" + "x" * 900, snippet="S" * 500)
    payload = support._build_feedback_web_knowledge_payload(record, hit, index=3)
    assert hit.title in payload.content
    assert hit.snippet in payload.content
    assert hit.url in payload.content
    assert payload.metadata["source_url"] == hit.url
    assert payload.metadata["collected_at"] == record.created_at.isoformat()
