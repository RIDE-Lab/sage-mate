from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import HTTPException

from sage_faculty_twin.memory_store import ConversationMemoryRecord
from sage_faculty_twin.service import DigitalTwinService


@dataclass
class _ConversationStoreStub:
    records: list[ConversationMemoryRecord]

    def list_records(self) -> list[ConversationMemoryRecord]:
        return list(self.records)


def _build_record(
    *,
    memory_id: str,
    conversation_id: str,
    student_name: str,
    student_email: str | None,
    question: str,
    answer: str,
    offset_seconds: int,
) -> ConversationMemoryRecord:
    return ConversationMemoryRecord(
        memory_id=memory_id,
        conversation_id=conversation_id,
        student_name=student_name,
        student_email=student_email,
        course_context="科研指导",
        question=question,
        answer=answer,
        workflow_action="answer",
        interaction_domain="advising",
        knowledge_hit_count=0,
        booking_summary=None,
        created_at=datetime.now(UTC) + timedelta(seconds=offset_seconds),
    )


def _build_service(records: list[ConversationMemoryRecord]) -> DigitalTwinService:
    service = object.__new__(DigitalTwinService)
    service._conversation_store = _ConversationStoreStub(records)
    return service


def test_history_requires_owner_as_well_as_matching_email():
    records = [replace(_build_record(memory_id=str(index), conversation_id="same",
        student_name="Same", student_email="same@example.com", question=f"private-{index}",
        answer=f"answer-{index}", offset_seconds=index), owner_id=owner)
        for index, owner in enumerate(("account:a", "account:b", None))]
    service = _build_service(records)
    for index, owner in enumerate(("account:a", "account:b", None)):
        history = service.list_chat_conversations(student_email="same@example.com", owner_id=owner)
        assert len(history.conversations) == 1
        assert history.conversations[0].exchange_count == 1
        transcript = service.get_chat_conversation(conversation_id="same", student_email="same@example.com", owner_id=owner)
        assert len(transcript.exchanges) == 1
        assert transcript.exchanges[0].question == f"private-{index}"
    with pytest.raises(HTTPException) as error:
        service.get_chat_conversation(conversation_id="same", student_email="same@example.com", owner_id="account:other")
    assert error.value.status_code == 404


def test_list_chat_conversations_only_returns_exact_email_matches() -> None:
    service = _build_service(
        [
            _build_record(
                memory_id="legacy-anon",
                conversation_id="conv-legacy-anon",
                student_name="Legacy Guest",
                student_email=None,
                question="这是匿名遗留会话",
                answer="匿名记录不应暴露给已登录账号。",
                offset_seconds=0,
            ),
            _build_record(
                memory_id="alice-1",
                conversation_id="conv-alice-only",
                student_name="Alice",
                student_email="alice@example.com",
                question="Alice 的历史问题",
                answer="Alice 的历史回答",
                offset_seconds=1,
            ),
            _build_record(
                memory_id="bob-1",
                conversation_id="conv-bob-only",
                student_name="Bob",
                student_email="bob@example.com",
                question="Bob 的历史问题",
                answer="Bob 的历史回答",
                offset_seconds=2,
            ),
        ]
    )

    alice_history = service.list_chat_conversations(student_email="alice@example.com")
    alice_ids = {item.conversation_id for item in alice_history.conversations}
    assert alice_ids == {"conv-alice-only"}

    bob_history = service.list_chat_conversations(student_email="bob@example.com")
    bob_ids = {item.conversation_id for item in bob_history.conversations}
    assert bob_ids == {"conv-bob-only"}


def test_history_preserves_long_generated_answer_without_schema_failure():
    answer = "完整研究分析与引用。" * 4000
    service = _build_service([_build_record(
        memory_id="long-answer", conversation_id="long-conversation",
        student_name="Alice", student_email="alice@example.com",
        question="请详细分析", answer=answer, offset_seconds=0,
    )])
    transcript = service.get_chat_conversation(
        conversation_id="long-conversation", student_email="alice@example.com")
    assert transcript.exchanges[0].answer == answer
    assert transcript.model_dump(mode="json")["exchanges"][0]["answer"] == answer


def test_get_chat_conversation_rejects_legacy_anonymous_records_for_logged_in_users() -> None:
    service = _build_service(
        [
            _build_record(
                memory_id="legacy-anon",
                conversation_id="conv-legacy-anon",
                student_name="Legacy Guest",
                student_email=None,
                question="这是匿名遗留会话",
                answer="匿名记录不应暴露给已登录账号。",
                offset_seconds=0,
            ),
            _build_record(
                memory_id="alice-1",
                conversation_id="conv-alice-only",
                student_name="Alice",
                student_email="alice@example.com",
                question="Alice 的历史问题",
                answer="Alice 的历史回答",
                offset_seconds=1,
            ),
        ]
    )

    transcript = service.get_chat_conversation(
        conversation_id="conv-alice-only",
        student_email="alice@example.com",
    )
    assert transcript.conversation_id == "conv-alice-only"
    assert transcript.student_email == "alice@example.com"

    with pytest.raises(HTTPException) as exc_info:
        service.get_chat_conversation(
            conversation_id="conv-legacy-anon",
            student_email="alice@example.com",
        )

    assert exc_info.value.status_code == 404
