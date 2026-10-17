from pathlib import Path

import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.models import KnowledgeDocumentCreate


def settings(path):
    return AppSettings(knowledge_base_dir=path / "knowledge", knowledge_backend="local")


def payload(text="original"):
    return KnowledgeDocumentCreate(title="Knowledge", content=text, source_name="source")


def test_body_and_index_intent_are_atomic_and_reopen_without_json(tmp_path, monkeypatch):
    config = settings(tmp_path)
    store = LocalKnowledgeStore(config)
    update = store._catalog.state.update_many
    def fail(slots, transform):
        def mutate_then_fail(values):
            transform(values)
            raise RuntimeError("transaction failed")
        return update(slots, mutate_then_fail)
    with monkeypatch.context() as patch:
        patch.setattr(store._catalog.state, "update_many", fail)
        with pytest.raises(RuntimeError, match="transaction failed"):
            store.add_document(payload())
    assert store.count_documents() == 0
    assert store._catalog.journal.records()[0] == []
    record = store.add_document(payload())
    assert list(config.knowledge_base_dir.glob("*.json")) == []
    rows, _ = store._catalog.journal.records()
    assert len(rows) == 1
    task = rows[0]["value"]["tasks"]["knowledge_index"]
    assert task["payload"]["revision"] == 1
    reopened = LocalKnowledgeStore(config)
    assert reopened.get_document(record.document_id) == record
    assert reopened._catalog.journal.records()[0] == rows


def test_index_failure_leaves_canonical_record_and_recoverable_intent(tmp_path, monkeypatch):
    config = settings(tmp_path)
    store = LocalKnowledgeStore(config)
    def fail():
        raise OSError("index unavailable")
    monkeypatch.setattr(store, "_rebuild_backend_indexes", fail)
    with pytest.raises(OSError, match="index unavailable"):
        store.add_document(payload())
    reopened = LocalKnowledgeStore(config)
    assert reopened.count_documents() == 1
    assert reopened._catalog.journal.records()[0][0]["value"]["tasks"]["knowledge_index"]["status"] == "pending"


def test_other_instance_delete_invalidates_cached_search_and_source_lookup(tmp_path):
    config = settings(tmp_path)
    first = LocalKnowledgeStore(config)
    record = first.add_document(payload("canonical GPU policy"))
    second = LocalKnowledgeStore(config)
    assert second.search("canonical GPU policy")
    first.delete_documents([record.document_id])
    assert second.get_document(record.document_id) is None
    assert second.search("canonical GPU policy") == []
    assert second.count_documents() == 0
    assert second._catalog.catalog.snapshot()["records"][record.document_id]["document"] is None


def test_import_receipts_preserve_updates_after_archive_interruption(tmp_path, monkeypatch):
    config = settings(tmp_path)
    seed = LocalKnowledgeStore(settings(tmp_path / "seed"))
    record = seed.add_document(payload())
    config.knowledge_base_dir.mkdir()
    source = config.knowledge_base_dir / "legacy.json"
    raw = record.model_dump_json()
    source.write_text(raw)
    with monkeypatch.context() as patch:
        def fail(*args):
            raise OSError("archive unavailable")
        patch.setattr(Path, "replace", fail)
        with pytest.raises(OSError, match="archive unavailable"):
            LocalKnowledgeStore(config)
    # Canonical data and receipts already committed despite archive failure.
    from sage_faculty_twin.knowledge_catalog import KnowledgeCatalog
    catalog = KnowledgeCatalog(config.knowledge_base_dir, namespace="private")
    revision, records = catalog.snapshot()
    changed = records[record.document_id].model_copy(update={"content": "reviewed update"})
    catalog.commit({record.document_id: changed}, expected_revision=revision)
    catalog.close()
    reopened = LocalKnowledgeStore(config)
    assert reopened.get_document(record.document_id).content == "reviewed update"
    assert not source.exists()
    assert (config.knowledge_base_dir / "legacy-imported" / source.name).read_text() == raw


def test_removed_canonical_database_never_acknowledges_new_write(tmp_path):
    store = LocalKnowledgeStore(settings(tmp_path))
    store._catalog.state_path.unlink()
    with pytest.raises(RuntimeError, match="canonical_knowledge_storage_removed"):
        store.add_document(payload())
    with pytest.raises(RuntimeError, match="canonical_knowledge_storage_removed"):
        store.list_documents()


def test_replaced_database_requires_restart_instead_of_using_old_handle(tmp_path):
    store = LocalKnowledgeStore(settings(tmp_path))
    store.add_document(payload())
    replacement = tmp_path / "replacement"
    replacement.write_bytes(b"replacement database")
    replacement.replace(store._catalog.state_path)
    with pytest.raises(RuntimeError, match="storage_replaced_restart_required"):
        store.list_documents()


def test_concurrent_canonical_edit_rejects_stale_update(tmp_path, monkeypatch):
    from sagevdb import DocumentConflict
    config = settings(tmp_path)
    first = LocalKnowledgeStore(config)
    record = first.add_document(payload())
    second = LocalKnowledgeStore(config)
    commit = first._catalog.commit
    def concurrent(changes, **kwargs):
        second.update_document(record.document_id, payload("newer curator change"))
        return commit(changes, **kwargs)
    monkeypatch.setattr(first._catalog, "commit", concurrent)
    with pytest.raises(DocumentConflict, match="revision_conflict"):
        first.update_document(record.document_id, payload("stale overwrite"))
    assert first.get_document(record.document_id).content == "newer curator change"
    assert len(first._catalog.journal.records()[0]) == 2


def test_legacy_import_cannot_bypass_source_namespace(tmp_path):
    config = settings(tmp_path)
    seed = LocalKnowledgeStore(settings(tmp_path / "seed"))
    record = seed.add_document(payload()).model_copy(update={"source_name": "wiki:private-injection"})
    config.knowledge_base_dir.mkdir()
    source = config.knowledge_base_dir / "legacy.json"
    source.write_text(record.model_dump_json())
    with pytest.raises(ValueError, match="independent Wiki index"):
        LocalKnowledgeStore(config)
    assert source.exists()
    from sage_faculty_twin.knowledge_catalog import KnowledgeCatalog
    catalog = KnowledgeCatalog(config.knowledge_base_dir, namespace="private")
    assert catalog.snapshot() == (0, {})
    assert catalog.journal.records()[0] == []
    catalog.close()
