"""Destination replay under post-commit failures; no production/model calls."""
from concurrent.futures import ThreadPoolExecutor

import pytest

from sage.runtime.operation_receipts import OperationConflict
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.models import KnowledgeDocumentCreate
from sage_faculty_twin.post_answer_contract import DeliveryIntent


def config(path):
    return AppSettings(knowledge_base_dir=path / "knowledge", knowledge_backend="local")


def payload(content="Complete synthetic evidence"):
    return KnowledgeDocumentCreate(title="Fixture", content=content, source_name="fixture")


def test_replay_survives_restart_and_does_not_resurrect_deleted_document(tmp_path):
    store = LocalKnowledgeStore(config(tmp_path))
    original = store.add_document(payload(), operation_id="request-one")
    store.close()
    store = LocalKnowledgeStore(config(tmp_path))
    try:
        assert store.add_document(payload(), operation_id="request-one") == original
        assert store.count_documents() == 1
        assert len(store._catalog.journal.records()[0]) == 1
        store.delete_documents([original.document_id])
        assert store.add_document(payload(), operation_id="request-one") == original
        assert store.count_documents() == 0
    finally:
        store.close()


def test_reused_identity_with_changed_content_fails_closed(tmp_path):
    store = LocalKnowledgeStore(config(tmp_path))
    try:
        store.add_document(payload(), operation_id="same")
        with pytest.raises(OperationConflict):
            store.add_document(payload("Changed evidence"), operation_id="same")
        assert store.count_documents() == 1
    finally:
        store.close()


def test_post_commit_projection_failure_replays_one_document(tmp_path, monkeypatch):
    store = LocalKnowledgeStore(config(tmp_path))
    try:
        with monkeypatch.context() as patch:
            def fail():
                raise OSError("injected projection failure")
            patch.setattr(store, "_rebuild_backend_indexes", fail)
            with pytest.raises(OSError, match="projection failure"):
                store.add_document(payload(), operation_id="same")
        original = store.list_documents()[0]
        assert store.add_document(payload(), operation_id="same") == original
        assert store.count_documents() == 1
        assert len(store._catalog.journal.records()[0]) == 1
    finally:
        store.close()


def test_destination_receipt_and_index_task_rollback_together(tmp_path, monkeypatch):
    store = LocalKnowledgeStore(config(tmp_path))
    update = store._catalog.state.update_many
    def fail(slots, transition):
        def wrapped(values):
            transition(values)
            raise OSError("injected transaction failure")
        return update(slots, wrapped)
    try:
        with monkeypatch.context() as patch:
            patch.setattr(store._catalog.state, "update_many", fail)
            with pytest.raises(OSError, match="transaction failure"):
                store.add_document(payload(), operation_id="same")
        assert store.count_documents() == 0
        assert store._catalog.journal.records()[0] == []
        store.add_document(payload(), operation_id="same")
        assert store.count_documents() == 1
    finally:
        store.close()


def test_owner_scoped_requests_and_unkeyed_writes_remain_distinct(tmp_path):
    store = LocalKnowledgeStore(config(tmp_path))
    try:
        for owner in ("account:one", "account:two"):
            identity = DeliveryIntent(owner_id=owner, request_id="same-client-id").stream_id
            store.add_document(payload(), operation_id=f"knowledge-write:{identity}")
        store.add_document(payload())
        store.add_document(payload())
        assert store.count_documents() == 4
    finally:
        store.close()


def test_independent_connections_racing_same_request_commit_once(tmp_path):
    first = LocalKnowledgeStore(config(tmp_path))
    second = LocalKnowledgeStore(config(tmp_path))
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            calls = [pool.submit(store.add_document, payload(), operation_id="same")
                     for store in (first, second)]
            records = [call.result() for call in calls]
        assert records[0] == records[1]
        assert first.count_documents() == second.count_documents() == 1
        assert len(first._catalog.journal.records()[0]) == 1
    finally:
        first.close()
        second.close()


def test_authorized_write_stage_uses_captured_delivery_not_document_source(tmp_path):
    from sage_faculty_twin.models import ChatRequest
    from sage_faculty_twin.service import ChatWorkflowContext, FacultyTwinWorkflowSupport

    store = LocalKnowledgeStore(config(tmp_path))
    # Exercise the real deterministic parser and write stage without opening
    # model clients or other application stores.
    support = object.__new__(FacultyTwinWorkflowSupport)
    support._knowledge_store = store
    support._delivery = DeliveryIntent(owner_id="account:fixture", request_id="retry")
    support._trace_callback = None
    try:
        records = []
        for _ in range(2):
            context = ChatWorkflowContext(request=ChatRequest(student_name="Fixture",
                question="加入知识库：\n标题：测试\n内容：这是一条完整的隔离测试知识。"),
                conversation_id="fixture", owner_name="Fixture", used_model="not-called",
                is_admin_request=True, workflow_action="admin_add_knowledge")
            support.execute_knowledge_write(context)
            records.append(context.added_knowledge_record)
        assert records[0] is not None and records[0] == records[1]
        assert store.count_documents() == 1
        context.is_admin_request = False
        support.execute_knowledge_write(context)
        assert store.count_documents() == 1
    finally:
        store.close()
