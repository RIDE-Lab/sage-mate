"""Native execution evidence stays distinct from business notes and task acks."""

import asyncio
import gc
import json
import weakref
from dataclasses import replace

import pytest
from sage.runtime.execution_observation import ExecutionObserver
from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage.runtime.work_journal import StaleTaskLeaseError, WorkJournal

from sage_faculty_twin.chat_runtime import ChatRuntime
from sage_faculty_twin.retrieval_runtime import RetrievalRuntime
from sage_faculty_twin.workflow_events import WorkflowEventBroker
from test_chat_runtime import TraceContext, ports
from test_retrieval_runtime import Ports


def native_events():
    runtime = ChatRuntime()
    events = []
    try:
        with ExecutionObserver(events.append, trace_id="owned-stream").bind():
            runtime.answer([], ports("synthetic"), timeout=5)
        return events
    finally:
        runtime.close()


def test_chat_retrieval_nested_native_parentage_and_actual_overlapping_starts():
    chat, retrieval = ChatRuntime(), RetrievalRuntime()
    events = []
    try:
        with ExecutionObserver(events.append, trace_id="owned-stream").bind():
            response, _ = chat.answer(TraceContext([], booking=False), ports("synthetic", retrieve=lambda: retrieval.retrieve(
                Ports("synthetic"), memory="private-input", knowledge="wiki-input", timeout=5)), timeout=8)
        assert response
        parent = next(event for event in events if event.operator_key == "retrieve" and event.state == "started")
        branches = [event for event in events if event.operator_key in {"read-memory", "read-knowledge"}]
        starts = [event for event in branches if event.state == "started"]
        ends = [event for event in branches if event.state == "completed"]
        assert len(starts) == len(ends) == 2
        assert max(event.sequence for event in starts) < min(event.sequence for event in ends)
        assert {event.parent_execution_id for event in starts} == {parent.execution_id}
        assert len({event.wave_id for event in starts}) == 1
        assert {event.wave_size for event in starts} == {2}
        assert "private-input" not in repr(events) and "wiki-input" not in repr(events)
    finally:
        chat.close()
        retrieval.close()


def test_execution_broker_reopens_replays_and_rejects_other_scope_and_closed_stream(tmp_path):
    path = str(tmp_path / "events.db")
    events = native_events()
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
    broker = WorkflowEventBroker(lambda: state)
    try:
        with pytest.raises(ValueError, match="scope_conflict"):
            broker.publish_execution("other-owner", events[0])
        assert broker._journal().read("other-owner")["events"] == []
        for event in events:
            broker.publish_execution("owned-stream", event)
        broker.publish_complete("owned-stream")
        with pytest.raises(ValueError, match="event_stream_closed"):
            broker.publish_execution("owned-stream", replace(events[0], event_id="late"))
        before = broker._journal().read("owned-stream")
    finally:
        state.close()
    reopened = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
    try:
        saved = WorkflowEventBroker(lambda: reopened)._journal().read("owned-stream")
        assert saved == before
        assert saved["closed"]
        assert [frame["payload"]["event"]["event_id"] for frame in saved["events"][:-1]] == [
            event.event_id for event in events]
    finally:
        reopened.close()


def test_execution_publication_is_atomically_fenced_and_does_not_acknowledge_task(tmp_path):
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "lease.db"))
    journal = WorkJournal(state, namespace="http")
    broker = WorkflowEventBroker(lambda: state)
    events = native_events()
    try:
        journal.commit("owned-stream", result=None, tasks={"answer": {}})
        lease = journal.claim("owned-stream", "answer", lease_seconds=30)
        guard = journal.prepare_guard(lease)
        broker.publish_execution("owned-stream", events[0], guard=guard)
        assert journal.read("owned-stream")["tasks"]["answer"]["status"] == "running"
        journal.cancel("owned-stream", "answer")
        before = broker._journal().read("owned-stream")
        with pytest.raises(StaleTaskLeaseError):
            broker.publish_execution("owned-stream", events[1], guard=guard)
        assert broker._journal().read("owned-stream") == before
    finally:
        state.close()


def test_real_http_execution_precedes_answer_commit_and_survives_canonical_history(tmp_path, monkeypatch):
    from sage_faculty_twin.config import AppSettings
    from sage_faculty_twin.service import DigitalTwinService
    from test_chat_post_answer_background import _FastLLMClient
    from test_http_workflow_integration import http_client

    service = DigitalTwinService(AppSettings(runtime_dir=tmp_path, model_name="fixture"))
    service._llm_client = _FastLLMClient()
    with http_client(service, monkeypatch) as client:
        response = client.post("/chat?request_id=native-evidence", json={
            "student_name": "Synthetic", "question": "给我一些下一步建议。"})
        assert response.status_code == 200, response.text
        data = response.json()
        native = data["execution_events"]
        assert native and all(event["trace_id"] for event in native)
        assert not any(event["operator_key"] in {"prepare-booking", "execute-booking"} for event in native)
        assert not any(step["key"] in {"booking_prepare", "booking_execute"} for step in data["workflow_trace"])
        assert any(event["operator_key"] == "render" and event["state"] == "completed" for event in native)
        assert all("parallel_group" not in note for note in data["workflow_trace"])
        stream = client.get("/chat/workflow-events?request_id=native-evidence")
        assert stream.status_code == 200
        frames = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")]
        observed = [frame["event"] for frame in frames if frame["type"] == "execution-event"]
        assert observed == native
        answer_index = next(i for i, frame in enumerate(frames) if frame["type"] == "answer_done")
        assert all(i < answer_index for i, frame in enumerate(frames) if frame["type"] == "execution-event")
        assert frames[-1]["type"] == "complete"
        record = service._conversation_store.get_canonical_record(data["exchange_id"])
        assert record is not None
        from sage_faculty_twin.memory_store import ConversationMemoryRecord
        restored = ConversationMemoryRecord.from_dict(record.to_dict())
        assert [event.to_dict() for event in restored.execution_events] == [event.to_dict() for event in record.execution_events]
        replay = client.post("/chat?request_id=native-evidence", json={
            "student_name": "Synthetic", "question": "给我一些下一步建议。"})
        assert replay.status_code == 200
        assert replay.json()["execution_events"] == native
        assert len(service._conversation_store.list_records()) == 1
        from sage_faculty_twin.memory_store import NeuroMemConversationStore
        reopened = NeuroMemConversationStore(service._settings)
        try:
            saved = reopened.get_canonical_record(data["exchange_id"])
            assert saved.to_dict()["execution_events"] == record.to_dict()["execution_events"]
        finally:
            reopened.state_store.close()


def test_finished_request_does_not_root_service_or_resident_runtime(tmp_path):
    from sage_faculty_twin.config import AppSettings
    from sage_faculty_twin.models import ChatRequest
    from sage_faculty_twin.service import DigitalTwinService
    from test_chat_post_answer_background import _FastLLMClient

    async def scenario():
        for index in range(3):
            service = DigitalTwinService(AppSettings(runtime_dir=tmp_path / str(index), model_name="fixture"))
            service._llm_client = _FastLLMClient()
            reference = weakref.ref(service)
            response = await service.answer(ChatRequest(student_name="Synthetic", question="给我一些下一步建议。"))
            assert response.execution_events
            del service
            for _ in range(5):
                await asyncio.sleep(0)
                gc.collect()
                if reference() is None:
                    break
            assert reference() is None, "completed request retained its service and resident executors"

    asyncio.run(scenario())
