import pytest
from sagevdb import PersistenceError, PersistentRecord, stable_content_hash

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.models import KnowledgeDocumentCreate


def settings(path):
    return AppSettings(knowledge_base_dir=path / "knowledge", knowledge_backend="sagevdb",
                       sagevdb_embedding_backend="hash", sagevdb_dimension=128,
                       knowledge_sagevdb_persistence_dir=path / "index")


def payload(text):
    return KnowledgeDocumentCreate(title=text, content=text, source_name="canonical-note")


def test_versioned_cutover_does_not_read_or_replace_old_index(tmp_path):
    config = settings(tmp_path)
    config.knowledge_sagevdb_persistence_dir.mkdir()
    old = config.knowledge_sagevdb_persistence_dir / "CURRENT"
    old.write_text("unversioned-offline-backup")
    store = LocalKnowledgeStore(config)
    store.add_document(payload("Canonical research evidence"))
    assert old.read_text() == "unversioned-offline-backup"
    assert store._sagevdb_persistence.root.parent == config.knowledge_sagevdb_persistence_dir / "canonical-v1"
    assert store._sagevdb_persistence.source_revision == store._catalog_revision == 1
    assert store._indexed_catalog_revision == 1


@pytest.mark.parametrize("damage", ["vectors", "pointer", "manifest"])
def test_damaged_index_rebuild_uses_latest_body_and_omits_deleted_source(tmp_path, damage):
    config = settings(tmp_path)
    first = LocalKnowledgeStore(config)
    old = first.add_document(payload("Obsolete GPU policy"))
    root = first._sagevdb_persistence.root
    generation = first._sagevdb_persistence.generation
    first.close()
    second = LocalKnowledgeStore(config)
    # Stage the crash window explicitly; public mutations now auto-start work.
    with second._mutation_lock:
        second._commit_documents({old.document_id: None})
        current = second._add_document_locked(KnowledgeDocumentCreate(
            title="Current GPU policy", content="Current GPU policy verified access procedure",
            source_name="current-policy"), rebuild_indexes=False)
    second.close()
    if damage == "pointer":
        (root / "CURRENT").unlink()
    else:
        file = "index.vectors" if damage == "vectors" else "manifest.json"
        (root / f"generation-{generation}" / file).write_text("corrupt")
    restarted = LocalKnowledgeStore(config)
    assert restarted._sagevdb_recovery_count == 1
    assert restarted._sagevdb_persistence.source_revision == 3
    assert restarted._indexed_catalog_revision == 3
    assert set(restarted._document_id_to_vector_id) == {current.document_id}
    assert restarted.get_document(old.document_id) is None
    hits = restarted.search("Current GPU policy verified access procedure")
    assert hits and hits[0].document_id == current.document_id


def test_old_instance_cannot_publish_old_snapshot(tmp_path):
    config = settings(tmp_path)
    first = LocalKnowledgeStore(config)
    old = first.add_document(payload("Old research notes"))
    old_projection = first._sagevdb_persistence
    old_record = PersistentRecord(old.document_id, stable_content_hash(first._compose_retrieval_text(old)),
                                  first._document_metadata(old))
    second = LocalKnowledgeStore(config)
    second.delete_documents([old.document_id])
    with pytest.raises(PersistenceError, match="stale_source_revision"):
        old_projection.sync([old_record], lambda _: pytest.fail("must reject before embedding"),
                            source_revision=1)
    assert first.search("Old research notes") == []
    assert first._indexed_catalog_revision == 2


def test_rebuild_embedding_failure_does_not_publish_success(tmp_path, monkeypatch):
    config = settings(tmp_path)
    store = LocalKnowledgeStore(config)
    record = store.add_document(payload("Saved canonical evidence"))
    root = store._sagevdb_persistence.root
    before = (root / "CURRENT").read_text()
    store.close()
    (root / f"generation-{store._sagevdb_persistence.generation}" / "index.vectors").write_text("corrupt")
    with monkeypatch.context() as patch:
        def fail(self, texts):
            raise OSError("embedding offline")
        patch.setattr(LocalKnowledgeStore, "_embed_documents", fail)
        with pytest.raises(TimeoutError):
            LocalKnowledgeStore(config.model_copy(update={"knowledge_startup_timeout_seconds": 1}))
    assert (root / "CURRENT").read_text() == before
    recovered = LocalKnowledgeStore(config)
    assert recovered.get_document(record.document_id) is not None
    assert recovered.search("Saved canonical evidence")


def test_different_embedding_configurations_do_not_replace_each_others_index(tmp_path):
    config = settings(tmp_path)
    first = LocalKnowledgeStore(config)
    first.add_document(payload("Shared canonical evidence"))
    first_root = first._sagevdb_persistence.root
    first_generation = first._sagevdb_persistence.generation
    other = LocalKnowledgeStore(config.model_copy(update={"sagevdb_dimension": 64}))
    assert other._sagevdb_persistence.root != first_root
    assert other._catalog.catalog.snapshot() == first._catalog.catalog.snapshot()
    assert other.search("Shared canonical evidence")
    assert (first_root / "CURRENT").read_text().strip() == first_generation
    reopened = LocalKnowledgeStore(config)
    assert reopened._sagevdb_persistence.generation == first_generation
    assert reopened._sagevdb_last_sync is None


def test_different_catalogs_cannot_share_a_live_generation(tmp_path):
    config = settings(tmp_path)
    first = LocalKnowledgeStore(config)
    first.add_document(payload("First dataset"))
    second = LocalKnowledgeStore(config.model_copy(update={"knowledge_base_dir": tmp_path / "other"}))
    assert first._sagevdb_persistence.root != second._sagevdb_persistence.root
    assert second.search("First dataset") == []
    assert first.search("First dataset")
