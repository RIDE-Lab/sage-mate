from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from sage_faculty_twin.memory_store import ConversationMemoryHit, ConversationMemoryRecord
from sage_faculty_twin.models import AvailabilitySchedule, ChatRequest, ChatResponse
from sage_faculty_twin.post_answer_contract import PostAnswerTask
from sage_faculty_twin.post_answer_handler import PostAnswerHandler
from sage_faculty_twin.service import FacultyTwinWorkflowSupport


def make_task():
    record = ConversationMemoryRecord(
        memory_id="turn", conversation_id="conversation", student_name="Alice",
        student_email=None, course_context=None, question="research checklist",
        answer="complete answer", workflow_action="answer", interaction_domain=None,
        knowledge_hit_count=0, booking_summary=None, created_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    return PostAnswerTask.capture(
        request_id="turn", request=ChatRequest(student_name="Alice", question=record.question),
        response=ChatResponse(answer=record.answer, owner_name="owner", used_model="fixed-model",
                              conversation_id="conversation", exchange_id="turn", memory_write_back=True),
        record=record, availability=AvailabilitySchedule(), evaluate_memory=True,
        evaluated_at=record.created_at,
        memory_hits=[ConversationMemoryHit("old", "conversation", "evidence", 0.2,
                                           datetime(2026, 5, 31, tzinfo=UTC), memory_type="long_term")],
    )


def score(task):
    context = SimpleNamespace(memory_hits=task.memory_hits, knowledge_hits=task.response.knowledge_hits,
                              recent_session_context=task.recent_session_context)
    return FacultyTwinWorkflowSupport._evaluate_memory_usefulness(
        None, context, evaluated_at=task.evaluated_at
    )


def test_handler_replay_uses_captured_time_and_preserves_answer():
    task = make_task()
    writes = []
    handler = PostAnswerHandler(consolidate_profiles=lambda record: writes.append(record.memory_id) or 1,
                                score=score)
    lease = SimpleNamespace(task_id="post_answer", request_id="turn", payload=task.model_dump(mode="json"))
    first = handler(lease)
    second = handler(lease)
    assert first == second
    assert first["response"]["answer"] == "complete answer"
    assert first["response"]["used_model"] == "fixed-model"
    assert first["memory_usefulness"]["signal"] == "review_worthy"
    assert first["response"]["follow_up_actions"]
    assert writes == ["turn", "turn"]  # Destination owns idempotence, not the worker.


def test_failure_propagates_and_later_stages_are_not_executed():
    task = make_task()

    def fail(record):
        raise OSError("destination unavailable")

    def forbidden(task):
        raise AssertionError("later stage must not run")

    handler = PostAnswerHandler(consolidate_profiles=fail, score=forbidden)
    lease = SimpleNamespace(task_id="post_answer", request_id="turn", payload=task.model_dump(mode="json"))
    with pytest.raises(OSError, match="destination unavailable"):
        handler(lease)
    lease.request_id = "another-turn"
    with pytest.raises(ValueError, match="lease_identity_conflict"):
        handler(lease)


def test_sage_retry_after_profile_commit_preserves_result_and_profile(tmp_path):
    from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
    from sage.runtime.work_dispatcher import WorkDispatcher
    from sage.runtime.work_journal import WorkJournal
    from sage_faculty_twin.config import AppSettings
    from sage_faculty_twin.memory_store import NeuroMemConversationStore

    task = make_task()
    settings = AppSettings(conversation_memory_dir=tmp_path / "memory",
                           conversation_memory_index_type="segment")
    memory = NeuroMemConversationStore(settings)
    memory.persist_exchange(task.record)
    runtime = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "work.db"))
    journal = WorkJournal(runtime, namespace="post-answer")
    journal.commit(task.request_id, **task.journal_inputs())
    attempts = []

    def fail_once(task):
        attempts.append(task.request_id)
        if len(attempts) == 1:
            raise OSError("after profile commit")
        return score(task)

    worker = WorkDispatcher(journal, PostAnswerHandler(
        consolidate_profiles=memory.consolidate_profiles, score=fail_once
    ), retry_delay=0)
    try:
        assert worker.run_once() == 1
        with pytest.raises(OSError, match="after profile commit"):
            next(iter(worker._active)).result(timeout=5)
        profiles = memory._durable_profiles.load()
        assert profiles
        assert worker.run_once() == 1
        next(iter(worker._active)).result(timeout=5)
        worker.run_once(admit=False)
        state = journal.read("turn")
        assert state["result"]["answer"] == "complete answer"
        assert state["tasks"]["post_answer"]["status"] == "done"
        assert state["tasks"]["post_answer"]["attempt"] == 2
        assert state["tasks"]["post_answer"]["result"]["response"]["answer"] == "complete answer"
        assert memory._durable_profiles.load() == profiles
        assert worker.failed == 1 and worker.completed == 1
    finally:
        worker.close()
        runtime.close()
