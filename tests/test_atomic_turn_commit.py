import pytest

from sage.runtime.work_journal import WorkJournal
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.memory_store import NeuroMemConversationStore
from sage_faculty_twin.models import ChatRequest

EVIDENCE = "## Evidence\n\n- parent\n  - nested\n" * 250


def setup_turn(tmp_path):
    settings = AppSettings(conversation_memory_dir=tmp_path / "memory",
                           conversation_memory_index_type="segment")
    memory = NeuroMemConversationStore(settings)
    record = memory.prepare_exchange(
        ChatRequest(student_name="Alice", question="research question", attachments=[{
            "file_name": "research.txt", "media_type": "text/plain", "size_bytes": len(EVIDENCE.encode()),
            "text_content": EVIDENCE,
        }]), conversation_id="conversation", answer="complete answer",
        workflow_action="answer", interaction_domain=None, knowledge_hit_count=0,
        booking_result=None,
    )
    journal = WorkJournal(memory._memory_state_runtime, namespace="twin-post-answer-v1")
    commit = journal.prepare_commit(record.memory_id, result={"answer": record.answer},
                                    tasks={"post_answer": record.to_dict()})
    return settings, memory, record, journal, commit


def test_journal_sql_failure_rolls_back_memory_and_history(tmp_path, monkeypatch):
    settings, memory, record, journal, commit = setup_turn(tmp_path)
    before = memory._durable_memory.load().export_state()
    backend = memory._memory_state_runtime._backend
    original = backend._put_uncommitted

    def fail(slot, entry):
        original(slot, entry)
        if slot == commit.slot:
            raise OSError("journal write failed")

    with monkeypatch.context() as patch:
        patch.setattr(backend, "_put_uncommitted", fail)
        with pytest.raises(OSError, match="journal write failed"):
            memory.persist_exchange(record, work_commit=commit)
    assert journal.read(record.memory_id) is None
    assert memory.list_records() == []
    assert memory._durable_memory.load().export_state() == before
    reopened = NeuroMemConversationStore(settings)
    assert reopened.list_records() == []


def test_lost_ack_then_reopen_and_retry_preserves_training_and_complete_task(tmp_path, monkeypatch):
    settings, memory, record, journal, commit = setup_turn(tmp_path)
    original = memory._memory_state_runtime.update_many

    def lost_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError("ack lost")

    with monkeypatch.context() as patch:
        patch.setattr(memory._memory_state_runtime, "update_many", lost_ack)
        with pytest.raises(ConnectionError, match="ack lost"):
            memory.persist_exchange(record, work_commit=commit)
    assert memory.list_records() == []  # No premature in-process publication.
    snapshot = memory._durable_memory.load().export_state()
    lease = journal.claim(record.memory_id, "post_answer", lease_seconds=30)
    journal.finish(lease, result={"done": True})
    reopened = NeuroMemConversationStore(settings)
    reopened.persist_exchange(record, work_commit=commit)
    assert reopened._durable_memory.load().export_state() == snapshot
    saved = reopened.list_recent_conversation_records("conversation")
    assert len(saved) == 1 and saved[0].to_dict() == record.to_dict()
    assert saved[0].attachments[0].text_excerpt == EVIDENCE
    assert journal.read(record.memory_id)["tasks"]["post_answer"]["result"] == {"done": True}


def test_journal_identity_conflict_does_not_publish_exchange(tmp_path):
    _, memory, record, journal, commit = setup_turn(tmp_path)
    journal.commit(record.memory_id, result="conflict", tasks={})
    before = memory._durable_memory.load().export_state()
    with pytest.raises(ValueError, match="journal_commit_conflict"):
        memory.persist_exchange(record, work_commit=commit)
    assert memory.list_records() == []
    assert memory._durable_memory.load().export_state() == before
