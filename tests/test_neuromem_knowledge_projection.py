import threading

import numpy as np
import pytest

from sage.neuromem.memory_collection.collection_projection import CollectionProjection
from sage_faculty_twin import knowledge_base
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.models import KnowledgeDocumentCreate


@pytest.mark.parametrize("kind", ["segment", "bm25", "faiss"])
def test_neuromem_shared_worker_restart_and_canonical_recovery(tmp_path, monkeypatch, kind):
    class TestEmbedder:
        dimension = 2
        def __init__(self, *args):
            self._model = self
        def encode(self, texts, **kwargs):
            if isinstance(texts, str):
                return np.array([1.0, 0.0])
            return np.array([[1.0, 0.0] for _ in texts])
    monkeypatch.setattr(knowledge_base, "NeuromemBgeEmbedder", TestEmbedder)
    calls = []
    original = CollectionProjection.build_records
    def build(self, payloads, metadata):
        assert threading.current_thread() is not threading.main_thread()
        calls.append(len(payloads))
        return original(self, payloads, metadata)
    monkeypatch.setattr(CollectionProjection, "build_records", build)
    settings = AppSettings(knowledge_base_dir=tmp_path / "knowledge",
                           knowledge_backend="neuromem", neuromem_index_type=kind)
    first = LocalKnowledgeStore(settings)
    record = first.add_document(KnowledgeDocumentCreate(
        title="old research", content="obsolete research evidence", source_name="old"))
    root = first._sagevdb_persistence.root
    first.close()
    before = list(calls)
    warm = LocalKnowledgeStore(settings)
    assert calls == before
    assert warm.search("obsolete research evidence")[0].document_id == record.document_id
    # Explicit canonical-only crash window; no projection consumer is started.
    with warm._mutation_lock:
        warm._commit_documents({record.document_id: None})
        current = warm._add_document_locked(KnowledgeDocumentCreate(
            title="current research", content="verified current research evidence",
            source_name="current"), rebuild_indexes=False)
    warm.close()
    (root / "CURRENT").write_bytes(b"\xffinvalid")
    recovered = LocalKnowledgeStore(settings)
    try:
        assert recovered._sagevdb_recovery_count == 1
        assert recovered._sagevdb_persistence.source_revision == 3
        assert recovered.get_document(record.document_id) is None
        assert recovered.search("verified current research evidence")[0].document_id == current.document_id
        recovered.delete_documents([current.document_id])
        assert recovered._sagevdb_persistence.source_revision == 4
    finally:
        recovered.close()
    empty = LocalKnowledgeStore(settings)
    try:
        assert empty.search("research") == []
        assert empty._neuromem_collection.size() == 0
    finally:
        empty.close()


def test_neuromem_configuration_switch_does_not_overwrite_other_projection(tmp_path):
    base = dict(knowledge_base_dir=tmp_path / "knowledge", knowledge_backend="neuromem")
    first = LocalKnowledgeStore(AppSettings(**base, neuromem_index_type="segment"))
    record = first.add_document(KnowledgeDocumentCreate(title="research", content="research evidence"))
    first.close()
    root = first._sagevdb_persistence.root
    generation = (root / "CURRENT").read_text()
    second = LocalKnowledgeStore(AppSettings(**base, neuromem_index_type="bm25"))
    try:
        assert second._sagevdb_persistence.root != root
        assert second._sagevdb_persistence.source_revision == 1
        assert (root / "CURRENT").read_text() == generation
        assert second.search("research evidence")[0].document_id == record.document_id
    finally:
        second.close()
