"""Booking decisions across restart, retries and newer conversational drafts."""

import asyncio
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime

import pytest
from sage.runtime.operation_receipts import OperationConflict

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.meeting import MeetingService

OWNER = "guest:fixture"
CONVERSATION = "fixture-conversation"


def store(tmp_path):
    return MeetingService(AppSettings(runtime_dir=tmp_path, booking_state_path=tmp_path / "booking.sqlite3"))


def fields():
    return dict(student_name="Fixture", student_email="fixture@example.test", topic="Synthetic",
                preferred_start=datetime(2030, 1, 7, 10), preferred_end=datetime(2030, 1, 7, 11))


def capture(meeting, operation="turn", *, changes=None, inputs=None, owner=OWNER):
    return meeting.workflows.capture(owner, CONVERSATION, operation,
        inputs=inputs or {"question": operation}, fields=fields() if changes is None else changes)


def test_missing_details_survive_reconstruction_and_are_owner_scoped(tmp_path):
    meeting = store(tmp_path)
    initial = fields()
    initial.pop("student_email")
    first = capture(meeting, changes=initial)
    assert first.pending_fields == ["student_email"]
    assert first.request is None
    meeting.close()
    meeting = store(tmp_path)
    try:
        assert meeting.workflows.peek(OWNER, CONVERSATION).topic == "Synthetic"
        assert meeting.workflows.peek("guest:other", CONVERSATION) is None
        assert meeting.workflows.peek(None, CONVERSATION) is None
        with pytest.raises(ValueError, match="verified_owner"):
            capture(meeting, owner=None)
        second = capture(meeting, "email", changes={"student_email": "fixture@example.test"})
        assert second.request is not None
        assert second.request.preferred_start == datetime(2030, 1, 7, 10)
        assert second.request.topic == "Synthetic"
        assert meeting.list_bookings() == []  # Capture is not an effect.
        other = capture(meeting, owner="guest:other")
        assert other.request is not None
    finally:
        meeting.close()


def test_retry_uses_original_plan_and_does_not_erase_a_newer_draft(tmp_path):
    meeting = store(tmp_path)
    try:
        plan = capture(meeting)
        response = meeting.book(plan.request, operation_id="booking-create:turn")
        newer = capture(meeting, "later", changes={"topic": "Later topic", "student_email": None})
        assert newer.pending_fields == ["student_email"]
        assert meeting.workflows.settle(OWNER, "turn", response) == {"settled_current_draft": False}
        # Simulate a new parser's different result: it cannot replace the saved plan.
        replay = capture(meeting, changes={"student_name": "Changed", "topic": "Wrong topic"})
        assert replay == plan
        assert meeting.workflows.peek(OWNER, CONVERSATION).topic == "Later topic"
        meeting.confirm_booking(response.booking.booking_id)
        assert meeting.book(replay.request, operation_id="booking-create:turn") == response
        meeting.workflows.settle(OWNER, "turn", response)
        assert meeting.list_bookings()[0].status == "已确认"
        assert meeting.workflows.peek(OWNER, CONVERSATION).topic == "Later topic"
    finally:
        meeting.close()


def test_rejected_plan_retains_contact_but_collects_a_new_time(tmp_path):
    meeting = store(tmp_path)
    try:
        first = capture(meeting)
        assert meeting.book(first.request).accepted
        conflict = capture(meeting, "conflict")
        response = meeting.book(conflict.request, operation_id="booking-create:conflict")
        assert not response.accepted
        meeting.workflows.settle(OWNER, "conflict", response)
        draft = meeting.workflows.peek(OWNER, CONVERSATION)
        assert draft.preferred_start is None and draft.preferred_end is None
        assert draft.student_email == "fixture@example.test"
        assert draft.topic == "Synthetic"
        next_plan = capture(meeting, "retry", changes={"preferred_start": datetime(2030, 1, 7, 14),
                                                       "preferred_end": datetime(2030, 1, 7, 15)})
        assert next_plan.request is not None
        meeting.workflows.settle(OWNER, "conflict", response)
        assert meeting.workflows.peek(OWNER, CONVERSATION).preferred_start.hour == 14
    finally:
        meeting.close()


def test_fallback_topic_cannot_replace_a_previously_captured_value(tmp_path):
    meeting = store(tmp_path)
    try:
        capture(meeting, changes={**fields(), "student_email": None})
        plan = meeting.workflows.capture(OWNER, CONVERSATION, "email", inputs={"question": "email"},
            fields={"student_email": "fixture@example.test"}, topic_default="Generic course context")
        assert plan.request.topic == "Synthetic"
    finally:
        meeting.close()


def test_conflicting_input_and_failed_capture_leave_no_partial_plan(tmp_path, monkeypatch):
    meeting = store(tmp_path)
    try:
        original = capture(meeting)
        with pytest.raises(OperationConflict):
            capture(meeting, inputs={"question": "different"})
        state = meeting._storage()
        update = state.update_many

        def fail(slots, transition):
            def rollback(values):
                transition(values)
                raise OSError("injected before commit")
            return update(slots, rollback)

        with monkeypatch.context() as patch:
            patch.setattr(state, "update_many", fail)
            with pytest.raises(OSError, match="before commit"):
                capture(meeting, "failed", changes={"topic": "must roll back"})
        assert meeting.workflows.decision(OWNER, "failed") is None
        assert meeting.workflows.peek(OWNER, CONVERSATION) == original.state
        assert meeting.workflows.decision(OWNER, "turn") == original
    finally:
        meeting.close()


def test_concurrent_independent_connections_capture_one_plan_and_effect(tmp_path):
    meetings = [store(tmp_path), store(tmp_path)]
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            plans = list(pool.map(lambda pair: capture(pair[1], changes={**fields(),
                "student_name": f"Candidate {pair[0]}"}), enumerate(meetings)))
        assert plans[0] == plans[1]
        with ThreadPoolExecutor(max_workers=2) as pool:
            responses = list(pool.map(lambda meeting: meeting.book(plans[0].request,
                operation_id="booking-create:turn"), meetings))
        assert responses[0] == responses[1]
        for meeting in meetings:
            meeting.workflows.settle(OWNER, "turn", responses[0])
        assert len(meetings[0].list_bookings()) == 1
        assert meetings[1].workflows.peek(OWNER, CONVERSATION) is None
        rows, _ = meetings[0]._notifications.journal.records()
        assert len(rows) == 1
    finally:
        for meeting in meetings:
            meeting.close()


@pytest.mark.parametrize("phase", ["before_capture", "after_capture", "after_effect"])
def test_actual_process_death_recovers_one_booking_and_one_notification(tmp_path, phase):
    script = '''
import os, sys
from pathlib import Path
from datetime import datetime
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.meeting import MeetingService
root = Path(sys.argv[1])
meeting = MeetingService(AppSettings(runtime_dir=root, booking_state_path=root / 'booking.sqlite3'))
state = meeting._storage()
if sys.argv[2] == 'before_capture':
    update = state.update_many
    def crash(slots, transition):
        def before(values):
            transition(values)
            os._exit(23)
        return update(slots, before)
    state.update_many = crash
plan = meeting.workflows.capture('guest:fixture', 'fixture-conversation', 'turn',
    inputs={'question':'turn'}, fields=dict(student_name='Fixture', student_email='fixture@example.test',
        topic='Synthetic', preferred_start=datetime(2030,1,7,10), preferred_end=datetime(2030,1,7,11)))
if sys.argv[2] == 'after_effect':
    meeting.book(plan.request, operation_id='booking-create:turn')
os._exit(23)
'''
    died = subprocess.run([sys.executable, "-c", script, str(tmp_path), phase],
                          capture_output=True, text=True, timeout=30)
    assert died.returncode == 23, died.stderr
    meeting = store(tmp_path)
    try:
        assert (meeting.workflows.decision(OWNER, "turn") is not None) == (phase != "before_capture")
        assert len(meeting.list_bookings()) == int(phase == "after_effect")
        plan = capture(meeting)
        response = meeting.book(plan.request, operation_id="booking-create:turn")
        assert response.accepted
        meeting.workflows.settle(OWNER, "turn", response)
        assert meeting.workflows.peek(OWNER, CONVERSATION) is None
        assert capture(meeting) == plan
        assert meeting.book(plan.request, operation_id="booking-create:turn") == response
        assert len(meeting.list_bookings()) == 1
        rows, _ = meeting._notifications.journal.records()
        assert len(rows) == 1
    finally:
        meeting.close()


@pytest.mark.parametrize("with_draft", [False, True])
def test_real_chat_restart_retries_captured_plan_without_reparsing(tmp_path, monkeypatch, with_draft):
    from sage.runtime.flownet.client.runtime_client import FlowRequestOutcomeError
    from sage_faculty_twin.models import ChatRequest
    from sage_faculty_twin.post_answer_contract import DeliveryIntent
    from sage_faculty_twin.service import DigitalTwinService, FacultyTwinWorkflowSupport

    class BookingClient:
        model_name = "not-called"

        def classify_booking_intent_sync(self, question, course_context=None):
            return True

        async def classify_booking_intent(self, question, course_context=None):
            return True

        def answer_question_sync(self, *args, **kwargs):
            raise AssertionError("booking must not generate an LLM answer")

        async def answer_question(self, *args, **kwargs):
            raise AssertionError("booking must not generate an LLM answer")

        async def aclose(self):
            pass

    settings = AppSettings(runtime_dir=tmp_path, knowledge_base_dir=tmp_path / "knowledge",
                           knowledge_backend="local", sagevdb_embedding_backend="hash",
                           booking_state_path=tmp_path / "booking.sqlite3",
                           conversation_memory_dir=tmp_path / "memory",
                           conversation_memory_index_type="bm25", post_answer_background=False)

    def fresh_service():
        service = DigitalTwinService(settings)
        service._llm_client = BookingClient()
        return service

    conversation = CONVERSATION if with_draft else None
    if with_draft:
        initial = fresh_service()
        try:
            first = asyncio.run(initial.answer(ChatRequest(owner_id=OWNER, student_name="Fixture",
                conversation_id=conversation, skill_routing=False, deep_thinking=False,
                question="请帮我预约 2030-01-07 10:00 讨论论文进展"),
                delivery=DeliveryIntent(owner_id=OWNER, request_id="initial")))
            assert first.pending_fields == ["student_email"]
        finally:
            asyncio.run(initial.aclose())

    request = ChatRequest(owner_id=OWNER, student_name="Fixture", conversation_id=conversation,
        skill_routing=False, deep_thinking=False,
        question="我的邮箱是 fixture@example.test" if with_draft else
                 "请帮我预约 2030-01-07 10:00 讨论论文进展，邮箱 fixture@example.test")
    delivery = DeliveryIntent(owner_id=OWNER, request_id="effect")
    first_attempt = fresh_service()
    book = first_attempt._meeting_service.book

    def lose_ack(*args, **kwargs):
        book(*args, **kwargs)
        raise ConnectionError("injected after booking commit before answer")

    monkeypatch.setattr(first_attempt._meeting_service, "book", lose_ack)
    try:
        response = asyncio.run(first_attempt.answer(request, delivery=delivery))
        assert response.booking_result.proposal["status"] == "pending"
        assert first_attempt._meeting_service.list_bookings() == []
        action_id = response.booking_result.proposal["action_id"]
        with pytest.raises(ConnectionError, match="injected after booking"):
            first_attempt._meeting_service.decide_proposal(OWNER, action_id, decision="confirm")
        assert len(first_attempt._meeting_service.list_bookings()) == 1
    finally:
        asyncio.run(first_attempt.aclose())

    def forbidden_parser(*args, **kwargs):
        raise AssertionError("replayed booking plan was reparsed")

    monkeypatch.setattr(FacultyTwinWorkflowSupport, "_extract_time_window", forbidden_parser)
    recovered = fresh_service()
    try:
        # The answer already completed before explicit confirmation lost its
        # acknowledgement. Recovery retries only the sealed action, not chat,
        # a parser or another execution-observer stream for the completed turn.
        result = recovered._meeting_service.decide_proposal(OWNER, action_id, decision="confirm")
        assert result.accepted
        assert result.booking.start_at == datetime(2030, 1, 7, 10)
        assert result.booking.topic == "论文进展"
        assert len(recovered._meeting_service.list_bookings()) == 1
        rows, _ = recovered._meeting_service._notifications.journal.records()
        assert len(rows) == 1
        assert recovered._meeting_service.workflows.peek(OWNER, response.conversation_id) is None
    finally:
        asyncio.run(recovered.aclose())
