import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.memory_store import NeuroMemConversationStore
from sage_faculty_twin.models import AvailabilitySchedule, ChatRequest, ChatResponse
from sage_faculty_twin.post_answer_contract import PostAnswerTask
from sage_faculty_twin.post_answer_runtime import PostAnswerRuntime

EVIDENCE = "## Evidence\n\n- parent\n  - nested\n" * 250


def runtime_for(memory, **kwargs):
    return PostAnswerRuntime(state=memory.state_store, consolidate_profiles=lambda record: 0,
                             score=lambda task: (None, None), **kwargs)


def setup_turn(tmp_path, owner_id=None):
    settings = AppSettings(conversation_memory_dir=tmp_path / "memory",
                           conversation_memory_index_type="segment")
    memory = NeuroMemConversationStore(settings)
    request = ChatRequest(owner_id=owner_id, student_name="Alice", question="research question", attachments=[{
        "file_name": "research.txt", "media_type": "text/plain", "size_bytes": len(EVIDENCE.encode()),
        "text_content": EVIDENCE,
    }])
    record = memory.prepare_exchange(request, conversation_id="conversation", answer="complete answer",
        workflow_action="answer", interaction_domain=None, knowledge_hit_count=0, booking_result=None)
    task = PostAnswerTask.capture(request_id=record.memory_id, request=request, record=record,
        response=ChatResponse(answer=record.answer, owner_name="owner", used_model="fixed",
                              conversation_id=record.conversation_id, exchange_id=record.memory_id,
                              memory_write_back=True),
        availability=AvailabilitySchedule(), evaluate_memory=False, skip_profile=True,
        skip_follow_up=True)
    return settings, memory, record, task


def test_journal_sql_failure_rolls_back_memory_and_history(tmp_path, monkeypatch):
    settings, memory, record, task = setup_turn(tmp_path)
    runtime = runtime_for(memory)
    before = memory._durable_memory.load().export_state()
    backend = memory.state_store._backend
    original = backend._put_uncommitted
    def fail(slot, entry):
        original(slot, entry)
        if slot[0] == "sage-work-journal-v1":
            raise OSError("journal write failed")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(backend, "_put_uncommitted", fail)
            with pytest.raises(OSError, match="journal write failed"):
                runtime.submit_turn(task, memory)
        assert runtime.journal.read(record.memory_id) is None
        assert memory.list_records() == []
        assert memory._durable_memory.load().export_state() == before
        reopened = NeuroMemConversationStore(settings)
        assert reopened.list_records() == []
        assert runtime._service._thread is None
    finally:
        runtime.close()


def test_lost_ack_then_reopen_and_retry_preserves_training_and_complete_task(tmp_path, monkeypatch):
    settings, memory, record, task = setup_turn(tmp_path)
    runtime = runtime_for(memory)
    original = memory.state_store.update_many
    def lost_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError("ack lost")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(memory.state_store, "update_many", lost_ack)
            with pytest.raises(ConnectionError, match="ack lost"):
                runtime.submit_turn(task, memory)
        assert memory.list_records() == []  # No acknowledgement-based cache publication.
        # Derivation reads canonical committed state, not the stale local cache.
        assert memory.list_conversation_records("conversation")[0].to_dict() == record.to_dict()
        snapshot = memory._durable_memory.load().export_state()
        lease = runtime.journal.claim(record.memory_id, "post_answer", lease_seconds=30)
        runtime.journal.finish(lease, result={"done": True})
    finally:
        runtime.close()
    # Borrowed runtime shutdown must leave the canonical memory store alive.
    assert memory._durable_memory.load().export_state() == snapshot
    reopened = NeuroMemConversationStore(settings)
    recovered = runtime_for(reopened)
    try:
        recovered.submit_turn(task, reopened)
        assert reopened._durable_memory.load().export_state() == snapshot
        saved = reopened.list_recent_conversation_records("conversation")
        assert len(saved) == 1 and saved[0].to_dict() == record.to_dict()
        assert saved[0].attachments[0].text_excerpt == EVIDENCE
        assert recovered.journal.read(record.memory_id)["tasks"]["post_answer"]["result"] == {"done": True}
    finally:
        recovered.close()


def test_journal_identity_conflict_does_not_publish_exchange(tmp_path):
    _, memory, record, task = setup_turn(tmp_path)
    runtime = runtime_for(memory)
    try:
        runtime.journal.commit(record.memory_id, result="conflict", tasks={})
        before = memory._durable_memory.load().export_state()
        with pytest.raises(ValueError, match="journal_commit_conflict"):
            runtime.submit_turn(task, memory)
        assert memory.list_records() == []
        assert memory._durable_memory.load().export_state() == before
    finally:
        runtime.close()


def test_different_work_store_is_rejected_before_any_write(tmp_path):
    _, memory, record, task = setup_turn(tmp_path)
    runtime = PostAnswerRuntime(tmp_path / "wrong.db", consolidate_profiles=lambda record: 0,
                                score=lambda task: (None, None))
    try:
        with pytest.raises(ValueError, match="share_state_store"):
            runtime.submit_turn(task, memory)
        assert runtime.journal.read(record.memory_id) is None
        assert memory.list_conversation_records("conversation") == []
    finally:
        runtime.close()


def test_worker_waits_for_local_publication_and_reads_committed_sources(tmp_path, monkeypatch):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    from sage_faculty_twin.post_answer_contract import DigestIntent

    _, memory, record, task = setup_turn(tmp_path)
    task = task.model_copy(update={"digest": DigestIntent(threshold=1, target_chars=1500, use_llm=False)})
    publishing, release, worker_entered, worker_finished = Event(), Event(), Event(), Event()
    original = memory._prepend_conversation_timeline
    def gated(record):
        publishing.set()
        assert release.wait(5)
        original(record)
    monkeypatch.setattr(memory, "_prepend_conversation_timeline", gated)
    def read_sources(conversation_id, intent, *, owner_id):
        worker_entered.set()
        sources = memory.list_conversation_records(conversation_id, owner_id=owner_id)
        assert [item.memory_id for item in sources] == [record.memory_id]
        assert memory.list_recent_conversation_records(conversation_id)[0].memory_id == record.memory_id
        worker_finished.set()
        return {"ok": True, "turns_incorporated": 1}
    runtime = runtime_for(memory, refresh_digest=read_sources)
    try:
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(runtime.submit_turn, task, memory)
            assert publishing.wait(3)
            assert worker_entered.wait(3)
            assert not worker_finished.is_set()
            release.set()
            assert pending.result(timeout=3).memory_id == record.memory_id
        assert runtime.wait(task.request_id, timeout=3)["response"]["answer"] == record.answer
        assert worker_finished.is_set()
    finally:
        release.set()
        runtime.close()


def test_memory_and_intent_use_same_detached_admission_snapshot(tmp_path):
    _, memory, record, task = setup_turn(tmp_path)
    runtime = runtime_for(memory)
    class CapturedTask:
        def journal_inputs(self):
            snapshot = task.journal_inputs()
            task.record.answer = "mutation after capture"
            return snapshot
    try:
        saved = runtime.submit_turn(CapturedTask(), memory)
        assert saved.answer == record.answer
        result = runtime.wait(record.memory_id, timeout=3)
        assert result["response"]["answer"] == record.answer
        payload = runtime.journal.read(record.memory_id)["tasks"]["post_answer"]["payload"]
        assert payload["record"]["answer"] == record.answer
        assert memory.list_conversation_records("conversation")[0].answer == record.answer
    finally:
        runtime.close()


def test_source_enumeration_never_writes_or_loads_neural_model(tmp_path, monkeypatch):
    _, memory, record, _ = setup_turn(tmp_path)
    memory.persist_exchange(record)
    def forbidden(*args, **kwargs):
        pytest.fail("source enumeration attempted writing or model reconstruction")
    monkeypatch.setattr(memory.state_store, "update", forbidden)
    monkeypatch.setattr(memory._durable_memory, "load", forbidden)
    assert memory.list_conversation_records("conversation")[0].to_dict() == record.to_dict()
