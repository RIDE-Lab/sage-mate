from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage.runtime.work_journal import WorkJournal

from sage_faculty_twin.models import AvailabilitySchedule, ChatRequest, ChatResponse
from sage_faculty_twin.memory_store import AttachmentMemoryRecord, ConversationMemoryRecord
from sage_faculty_twin.post_answer_contract import PostAnswerTask


def make_task(**overrides):
    values = dict(
        request_id="turn", request=ChatRequest(student_name="Alice", question="research",
                                               slack_channel_context="ephemeral channel history"),
        response=ChatResponse(answer="complete answer", owner_name="owner", used_model="model",
                              conversation_id="conversation"),
        availability=AvailabilitySchedule(), evaluate_memory=True,
        evaluated_at=datetime(2026, 6, 1, tzinfo=UTC),
    )
    values.update(overrides)
    return PostAnswerTask.capture(**values)


def test_snapshot_excludes_transient_context_and_detaches_inputs():
    request = ChatRequest(student_name="Alice", question="research",
                          slack_channel_context="ephemeral channel history")
    response = ChatResponse(answer="complete answer", owner_name="owner", used_model="model",
                            conversation_id="conversation")
    task = make_task(request=request, response=response)
    request.question = "later mutation"
    response.answer = "later mutation"
    assert task.request.question == "research"
    assert task.response.answer == "complete answer"
    assert task.request.slack_channel_context is None
    assert "ephemeral channel history" not in task.model_dump_json()
    assert "slack_channel_context" not in task.model_dump_json()
    assert PostAnswerTask.model_validate_json(task.model_dump_json()) == task


def test_artifact_intent_cannot_exist_without_stable_source_identity():
    with pytest.raises(ValidationError, match="requires_source_record"):
        make_task(artifact_draft={"artifact_names": ["paper.md"], "artifact_sources": ["upload"],
                                  "artifact_excerpt_count": 1, "provenance_note": "source"})


@pytest.mark.parametrize("field,value", [
    ("version", 2), ("task_kind", "book_meeting"),
    ("system_prompt", "not permitted"), ("runtime", {}),
    ("evaluated_at", "2026-06-01T00:00:00"),
])
def test_unrecognized_or_unsafe_task_fields_fail_closed(field, value):
    payload = make_task().model_dump(mode="json")
    payload[field] = value
    with pytest.raises(ValidationError):
        PostAnswerTask.model_validate(payload)


def test_result_and_exact_task_inputs_survive_journal_reopen(tmp_path):
    task = make_task()
    path = str(tmp_path / "state.db")
    runtime = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
    try:
        journal = WorkJournal(runtime, namespace="post-answer")
        journal.commit(task.request_id, **task.journal_inputs())
    finally:
        runtime.close()
    runtime = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
    try:
        journal = WorkJournal(runtime, namespace="post-answer")
        lease = journal.claim("turn", "post_answer", lease_seconds=30)
        restored = PostAnswerTask.model_validate(lease.payload)
        assert restored == task
        assert journal.records()[0][0]["value"]["result"] == task.response.model_dump(mode="json")
    finally:
        runtime.close()


def test_committed_record_identity_and_full_attachment_roundtrip():
    record = ConversationMemoryRecord(
        memory_id="turn", conversation_id="conversation", student_name="Alice",
        student_email=None, course_context=None, question="research", answer="complete answer",
        workflow_action="answer", interaction_domain=None, knowledge_hit_count=0,
        booking_summary=None, created_at=datetime(2026, 6, 1, tzinfo=UTC),
        attachments=[AttachmentMemoryRecord("research.txt", "text/plain", "full evidence " * 1000)],
    )
    response = ChatResponse(answer="complete answer", owner_name="owner", used_model="model",
                            conversation_id="conversation", exchange_id="turn", memory_write_back=True)
    task = make_task(record=record, response=response)
    restored = PostAnswerTask.model_validate_json(task.model_dump_json())
    assert restored.record.to_dict() == record.to_dict()
    for key, value in [("memory_id", "other"), ("answer", "changed"),
                       ("student_name", "other"), ("conversation_id", "other")]:
        payload = task.model_dump(mode="json")
        payload["record"][key] = value
        with pytest.raises(ValidationError, match="record_identity_conflict"):
            PostAnswerTask.model_validate(payload)
    task.response.answer = "mutated after capture"
    with pytest.raises(ValidationError, match="record_identity_conflict"):
        task.journal_inputs()
