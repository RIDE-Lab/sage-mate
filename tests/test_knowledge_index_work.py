import threading

import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.models import KnowledgeDocumentCreate


def store_at(path):
    return LocalKnowledgeStore(AppSettings(
        knowledge_base_dir=path / "knowledge", knowledge_backend="sagevdb",
        sagevdb_embedding_backend="hash", sagevdb_dimension=128,
    ))


def add(store, text="current research evidence"):
    return store.add_document(KnowledgeDocumentCreate(
        title=text, content=text, source_name=text), rebuild_indexes=False)


def operations(store):
    return [row["key"] for row in store._catalog.journal.records()[0]]


def test_idle_worker_does_not_retain_store_and_finalizer_drains_it(tmp_path):
    import gc
    import weakref
    store = store_at(tmp_path)
    store.start_background_indexing()
    reference = weakref.ref(store)
    service = store._catalog._index_service
    del store
    gc.collect()
    assert reference() is None
    assert service._stopped.wait(5)
    assert service._closed


def test_last_store_reference_released_inside_projector_retires_after_ack(tmp_path, monkeypatch):
    import weakref
    entered, release = threading.Event(), threading.Event()
    original = LocalKnowledgeStore._rebuild_backend_indexes
    store = store_at(tmp_path)
    add(store)
    request = operations(store)[0]
    def blocked(self):
        entered.set()
        assert release.wait(5)
        original(self)
    monkeypatch.setattr(LocalKnowledgeStore, "_rebuild_backend_indexes", blocked)
    store.start_background_indexing()
    service = store._catalog._index_service
    reference = weakref.ref(store)
    assert entered.wait(5)
    del store
    assert reference() is not None  # Worker temporarily owns its projection target.
    release.set()
    assert service._stopped.wait(5)
    assert reference() is None
    assert service._closed and service._error is None
    monkeypatch.setattr(LocalKnowledgeStore, "_rebuild_backend_indexes", original)
    reopened = store_at(tmp_path)
    try:
        assert reopened._catalog.journal.read(request)["tasks"]["knowledge_index"]["status"] == "done"
    finally:
        reopened.close()


def test_recovery_consumes_pending_native_index_intents(tmp_path):
    first = store_at(tmp_path)
    old = add(first, "deleted evidence")
    first.delete_documents([old.document_id], rebuild_indexes=False)
    latest = add(first)
    requests = operations(first)
    first.close()
    restarted = store_at(tmp_path)
    try:
        restarted.start_background_indexing()
        for request in requests:
            result = restarted._catalog.wait_indexing(request, timeout=5)
            task = result["tasks"]["knowledge_index"]
            assert task["status"] == "done"
            assert task["result"] == {"namespace": "private", "revision": 3}
        assert restarted._sagevdb_persistence.source_revision == 3
        assert set(restarted._document_id_to_vector_id) == {latest.document_id}
        assert restarted.get_document(old.document_id) is None
    finally:
        restarted.close()


def test_running_worker_projects_new_admission_without_foreground_rebuild(tmp_path):
    store = store_at(tmp_path)
    try:
        store.start_background_indexing()
        record = add(store)
        store._catalog.wait_indexing(operations(store)[0], timeout=5)
        assert store._sagevdb_persistence.source_revision == 1
        assert set(store._document_id_to_vector_id) == {record.document_id}
    finally:
        store.close()


def test_foreground_reads_other_workers_generation_without_rebuilding(tmp_path, monkeypatch):
    first = store_at(tmp_path)
    old = add(first, "old research evidence")
    first.rebuild_indexes()
    other = store_at(tmp_path)
    try:
        other.start_background_indexing()
        other.delete_documents([old.document_id], rebuild_indexes=False)
        new = add(other, "new research evidence")
        for request in operations(other):
            other._catalog.wait_indexing(request, timeout=5)
        def forbidden():
            pytest.fail("already committed worker projection must not be rebuilt")
        monkeypatch.setattr(first, "_initialize_sagevdb", forbidden)
        hits = first.search("new research evidence")
        assert hits and hits[0].document_id == new.document_id
        assert old.document_id not in first._document_id_to_vector_id
        assert first._sagevdb_open_result.reason == "refreshed_current"
        assert first._sagevdb_last_sync is None
        assert first._indexed_catalog_revision == 3
    finally:
        other.close()
        first.close()


def test_failed_projection_is_not_acknowledged_and_recovers_on_restart(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    add(store)
    request = operations(store)[0]
    called = threading.Event()
    def fail():
        called.set()
        raise OSError("embedding unavailable")
    monkeypatch.setattr(store, "_rebuild_backend_indexes", fail)
    store.start_background_indexing()
    assert called.wait(5)
    store.close()
    restarted = store_at(tmp_path)
    try:
        assert restarted._catalog.journal.read(request)["tasks"]["knowledge_index"]["status"] != "done"
        restarted.start_background_indexing()
        restarted._catalog.wait_indexing(request, timeout=10)
        assert restarted._sagevdb_persistence.source_revision == 1
    finally:
        restarted.close()


def test_close_timeout_keeps_canonical_state_available_for_active_worker(tmp_path):
    store = store_at(tmp_path)
    add(store)
    entered, release = threading.Event(), threading.Event()
    def project(required):
        entered.set()
        assert release.wait(5)
        return store._catalog.snapshot()[0]
    store._catalog.start_indexing(project)
    try:
        assert entered.wait(5)
        with pytest.raises(TimeoutError, match="drain_timeout"):
            store.close(timeout=0)
        assert store._catalog.snapshot()[0] == 1
        with pytest.raises(RuntimeError, match="catalog_closing"):
            add(store, "must not admit after close")
    finally:
        release.set()
        store.close()
    assert store._catalog._closed


@pytest.mark.parametrize("actual", [0, True, None])
def test_projection_must_prove_required_revision_before_acknowledgement(tmp_path, actual):
    store = store_at(tmp_path)
    add(store)
    called = threading.Event()
    def project(required):
        called.set()
        return actual
    store._catalog.start_indexing(project)
    try:
        assert called.wait(5)
    finally:
        store.close()
    reopened = store_at(tmp_path)
    try:
        assert reopened._catalog.journal.read(operations(reopened)[0])["tasks"]["knowledge_index"]["status"] != "done"
    finally:
        reopened.close()
