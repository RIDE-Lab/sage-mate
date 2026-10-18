import threading
import time

import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.knowledge_catalog import KnowledgeCatalog
from sage_faculty_twin.models import KnowledgeDocumentCreate


def test_startup_projection_has_separate_readiness_deadline(tmp_path, monkeypatch):
    observed = []
    original = LocalKnowledgeStore._wait_for_projection

    def inspect(self, revision, **kwargs):
        observed.append(kwargs["deadline"] - time.monotonic())
        return original(self, revision, **kwargs)

    monkeypatch.setattr(LocalKnowledgeStore, "_wait_for_projection", inspect)
    store = LocalKnowledgeStore(AppSettings(
        knowledge_base_dir=tmp_path / "knowledge", knowledge_backend="sagevdb",
        sagevdb_embedding_backend="hash", sagevdb_dimension=128,
        chat_request_timeout_seconds=1, knowledge_startup_timeout_seconds=123,
    ))
    try:
        assert len(observed) == 1
        assert 122 < observed[0] <= 123
        assert store._settings.chat_request_timeout_seconds == 1
    finally:
        store.close()


def store_at(path):
    return LocalKnowledgeStore(AppSettings(
        knowledge_base_dir=path / "knowledge", knowledge_backend="sagevdb",
        sagevdb_embedding_backend="hash", sagevdb_dimension=128,
    ))


def add(store, text="current research evidence"):
    return store.add_document(KnowledgeDocumentCreate(
        title=text, content=text, source_name=text), rebuild_indexes=False)


def stage_without_consumption(store, text="current research evidence"):
    # Fault/recovery fixtures explicitly stage canonical work before workers
    # start. The public mutation API now starts its consumer automatically.
    with store._mutation_lock:
        return store._add_document_locked(KnowledgeDocumentCreate(
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
    stage_without_consumption(store)
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
    old = stage_without_consumption(first, "deleted evidence")
    with first._mutation_lock:
        first._commit_documents({old.document_id: None})
    latest = stage_without_consumption(first)
    requests = operations(first)
    first.close()
    restarted = store_at(tmp_path)
    try:
        restarted.start_background_indexing()
        for request in requests:
            result = restarted._catalog.wait_indexing(request, timeout=5)
            task = result["tasks"]["knowledge_index"]
            assert task["status"] == "done"
            assert task["result"] == {"namespace": "private", "revision": 3,
                                      "projection_key": restarted._sagevdb_persistence.root.name}
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
    first._catalog._index_service.close()
    other = store_at(tmp_path)
    try:
        other.start_background_indexing()
        other.delete_documents([old.document_id], rebuild_indexes=False)
        new = add(other, "new research evidence")
        for request in operations(other):
            other._catalog.wait_indexing(request, timeout=5)
        def forbidden():
            pytest.fail("already committed worker projection must not be rebuilt")
        monkeypatch.setattr(first, "_initialize_projection", forbidden)
        hits = first.search("new research evidence")
        assert hits and hits[0].document_id == new.document_id
        assert old.document_id not in first._document_id_to_vector_id
        assert first._sagevdb_open_result.reason == "refreshed_current"
        assert first._sagevdb_last_sync is None
        assert first._indexed_catalog_revision == 3
    finally:
        other.close()
        first.close()


def test_different_configurations_do_not_claim_targeted_tasks(tmp_path):
    first = store_at(tmp_path)
    other = LocalKnowledgeStore(first._settings.model_copy(update={"sagevdb_dimension": 64}))
    stage_without_consumption(first)
    key = first._sagevdb_persistence.root.name
    request = first._catalog.request_indexing(1, projection_key=key)
    other_request = other._catalog.request_indexing(
        1, projection_key=other._sagevdb_persistence.root.name)
    try:
        other.start_background_indexing()
        other._catalog.wait_indexing(other_request, timeout=5)
        other.close()
        pending = first._catalog.journal.read(request)["tasks"]["knowledge_index"]
        assert pending["attempt"] == 0 and pending["status"] == "pending"
        first.start_background_indexing()
        done = first._catalog.wait_indexing(request, timeout=5)
        assert done["tasks"]["knowledge_index"]["result"]["projection_key"] == key
        assert first._sagevdb_persistence.source_revision == 1
    finally:
        other.close()
        first.close()


def test_active_native_writer_tags_atomic_intent_with_projection_identity(tmp_path):
    store = store_at(tmp_path)
    try:
        store.start_background_indexing()
        add(store)
        request = operations(store)[0]
        payload = store._catalog.journal.read(request)["tasks"]["knowledge_index"]["payload"]
        assert payload["projection_key"] == store._sagevdb_persistence.root.name
        assert payload["revision"] == store._catalog.snapshot()[0] == 1
        with pytest.raises(RuntimeError, match="changed_restart_required"):
            store._catalog.start_indexing(lambda revision: revision, projection_key="wrong")
        with pytest.raises(ValueError, match="not_committed"):
            store._catalog.request_indexing(2, projection_key=payload["projection_key"])
    finally:
        store.close()


def test_failed_projection_is_not_acknowledged_and_recovers_on_restart(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    stage_without_consumption(store)
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
    stage_without_consumption(store)
    entered, release = threading.Event(), threading.Event()
    def project(required):
        entered.set()
        assert release.wait(5)
        return store._catalog.snapshot()[0]
    store._catalog.start_indexing(project, projection_key=store._sagevdb_persistence.root.name)
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
    stage_without_consumption(store)
    request = operations(store)[0]
    called = threading.Event()
    def project(required):
        called.set()
        return actual
    store._catalog.start_indexing(project, projection_key=store._sagevdb_persistence.root.name)
    try:
        assert called.wait(5)
    finally:
        store.close()
    # Inspect the durable receipt without starting a valid recovery worker.
    # Opening LocalKnowledgeStore here can legitimately complete this task
    # before the assertion, hiding the rejected projector's outcome.
    catalog = KnowledgeCatalog(store._base_dir, namespace=store._catalog.catalog.namespace)
    try:
        assert catalog.journal.read(request)["tasks"]["knowledge_index"]["status"] != "done"
    finally:
        catalog.close()
    reopened = store_at(tmp_path)
    try:
        reopened._catalog.wait_indexing(request, timeout=10)
        assert reopened._sagevdb_persistence.source_revision == 1
        assert reopened._catalog.journal.read(request)["tasks"]["knowledge_index"]["status"] == "done"
    finally:
        reopened.close()


def test_native_mutations_wait_without_lock_and_build_only_in_sage_worker(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    builds, waits = [], []
    original_build = store._rebuild_backend_indexes
    original_wait = store._catalog.wait_indexing
    def build():
        assert threading.current_thread().name.startswith("sage-work")
        builds.append(threading.current_thread().name)
        original_build()
    def wait(request, *, timeout):
        acquired = threading.Event()
        def probe():
            if store._mutation_lock.acquire(timeout=0.5):
                acquired.set()
                store._mutation_lock.release()
        thread = threading.Thread(target=probe)
        thread.start()
        thread.join(1)
        assert acquired.is_set(), "foreground held mutation lock while waiting"
        waits.append(request)
        return original_wait(request, timeout=timeout)
    monkeypatch.setattr(store, "_rebuild_backend_indexes", build)
    monkeypatch.setattr(store._catalog, "wait_indexing", wait)
    try:
        record, inserted = store.upsert_document(KnowledgeDocumentCreate(
            title="first", content="first research", source_name="research"))
        assert inserted and store._indexed_catalog_revision == 1
        store.update_document(record.document_id, KnowledgeDocumentCreate(
            title="second", content="second research", source_name="research"))
        assert store.search("second research")[0].document_id == record.document_id
        store.delete_documents([record.document_id])
        assert store.search("second research") == []
        store.rebuild_indexes()
        assert waits and builds
    finally:
        store.close()


def test_native_wait_timeout_retains_committed_body_and_never_serves_old_index(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    store._settings.chat_request_timeout_seconds = 1
    def fail():
        raise OSError("embedding offline")
    monkeypatch.setattr(store, "_rebuild_backend_indexes", fail)
    try:
        with pytest.raises(TimeoutError):
            store.add_document(KnowledgeDocumentCreate(
                title="durable", content="durable research", source_name="durable"))
        assert store.count_documents() == 1
        assert store._catalog.journal.read(operations(store)[0])["tasks"]["knowledge_index"]["status"] != "done"
        with pytest.raises(TimeoutError):
            store.search("durable research")
    finally:
        store.close()
    recovered = store_at(tmp_path)
    try:
        assert recovered.search("durable research")
    finally:
        recovered.close()


def test_search_waits_for_its_configuration_not_another_workers_receipt(tmp_path, monkeypatch):
    writer = store_at(tmp_path)
    reader = LocalKnowledgeStore(writer._settings.model_copy(update={"sagevdb_dimension": 64}))
    original = reader._initialize_projection
    def build():
        assert threading.current_thread().name.startswith("sage-work")
        original()
    monkeypatch.setattr(reader, "_initialize_projection", build)
    try:
        record = writer.add_document(KnowledgeDocumentCreate(
            title="evidence", content="shared canonical evidence", source_name="evidence"))
        assert writer._indexed_catalog_revision == 1
        assert reader._indexed_catalog_revision == 0
        assert reader.search("shared canonical evidence")[0].document_id == record.document_id
        assert reader._sagevdb_persistence.source_revision == 1
        assert reader._sagevdb_persistence.root != writer._sagevdb_persistence.root
        assert any(row["value"]["tasks"]["knowledge_index"]["payload"].get("projection_key")
                   == reader._sagevdb_persistence.root.name
                   for row in reader._catalog.journal.records()[0])
    finally:
        reader.close()
        writer.close()


def test_inactive_bulk_import_defers_consumption_but_binds_all_intents(tmp_path):
    store = store_at(tmp_path)
    try:
        first = add(store, "first evidence")
        second = add(store, "second evidence")
        assert store._catalog._index_service is None
        assert store._indexed_catalog_revision == 0
        rows = store._catalog.journal.records()[0]
        assert len(rows) == 2
        for row in rows:
            task = row["value"]["tasks"]["knowledge_index"]
            assert task["status"] == "pending"
            assert task["payload"]["projection_key"] == store._sagevdb_persistence.root.name
        store.rebuild_indexes()
        assert store._indexed_catalog_revision == 2
        assert set(store._document_id_to_vector_id) == {first.document_id, second.document_id}
    finally:
        store.close()


def test_catalog_rollback_does_not_spin_or_serve_newer_projection(tmp_path, monkeypatch):
    store = store_at(tmp_path)
    try:
        store.add_document(KnowledgeDocumentCreate(
            title="evidence", content="evidence", source_name="evidence"))
        # Model a canonical restore behind the already committed projection.
        monkeypatch.setattr(store._catalog, "snapshot", lambda: (0, {}))
        with pytest.raises(RuntimeError, match="projection_ahead_of_catalog"):
            store.search("evidence")
    finally:
        store.close()


def test_native_cold_start_builds_only_through_sage_worker(tmp_path, monkeypatch):
    config = AppSettings(knowledge_base_dir=tmp_path / "knowledge", knowledge_backend="local")
    seed = LocalKnowledgeStore(config)
    record = seed.add_document(KnowledgeDocumentCreate(
        title="cold evidence", content="cold research evidence", source_name="cold"))
    seed.close()
    original = LocalKnowledgeStore._initialize_projection
    builds = []
    def initialize(self, *, prepare_only=False):
        if not prepare_only:
            assert threading.current_thread().name.startswith("sage-work")
            builds.append(threading.current_thread().name)
        return original(self, prepare_only=prepare_only)
    monkeypatch.setattr(LocalKnowledgeStore, "_initialize_projection", initialize)
    store = store_at(tmp_path)
    try:
        assert builds
        assert store._indexed_catalog_revision == 1
        assert store.search("cold research evidence")[0].document_id == record.document_id
    finally:
        store.close()


def test_failed_native_startup_retires_worker_without_losing_canonical_data(tmp_path, monkeypatch):
    from sage_faculty_twin.knowledge_catalog import KnowledgeCatalog
    seed = LocalKnowledgeStore(AppSettings(
        knowledge_base_dir=tmp_path / "knowledge", knowledge_backend="local"))
    seed.add_document(KnowledgeDocumentCreate(title="saved", content="saved evidence", source_name="saved"))
    seed.close()
    original = KnowledgeCatalog.retire
    retired = []
    def retire(self):
        retired.append(self)
        original(self)
    def fail(self, texts):
        assert threading.current_thread().name.startswith("sage-work")
        raise OSError("embedding unavailable")
    with monkeypatch.context() as patch:
        patch.setattr(KnowledgeCatalog, "retire", retire)
        patch.setattr(LocalKnowledgeStore, "_embed_documents", fail)
        with pytest.raises(TimeoutError):
            LocalKnowledgeStore(AppSettings(
                knowledge_base_dir=tmp_path / "knowledge", knowledge_backend="sagevdb",
                sagevdb_embedding_backend="hash", sagevdb_dimension=128,
                chat_request_timeout_seconds=1, knowledge_startup_timeout_seconds=1))
    assert len(retired) == 1 and retired[0]._closing
    assert retired[0]._index_service._stopped.wait(5)
    assert retired[0]._index_service._closed
    recovered = store_at(tmp_path)
    try:
        assert recovered.count_documents() == 1
        assert recovered.search("saved evidence")
    finally:
        recovered.close()
