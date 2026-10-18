import pytest
import asyncio
from dataclasses import replace
from sage.runtime.source_answer_plan import SourceAnswerBlock

from sage_faculty_twin.memory_store import NeuroMemConversationStore
from sage_faculty_twin.post_answer_contract import DeliveryIntent
from test_atomic_turn_commit import runtime_for, setup_turn
from test_chat_post_answer_background import _FastLLMClient
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import ChatRequest
from sage_faculty_twin.service import DigitalTwinService
from sage_faculty_twin.workflow_events import WorkflowEventBroker


def delivery_turn(tmp_path):
    settings, memory, record, task = setup_turn(tmp_path, owner_id="account:alice")
    return settings, memory, record, task.model_copy(update={
        "delivery": DeliveryIntent(owner_id="account:alice", request_id="public-request")})


def test_delivery_event_write_failure_rolls_back_memory_and_intent(tmp_path, monkeypatch):
    _, memory, record, task = delivery_turn(tmp_path)
    runtime = runtime_for(memory)
    original = memory.state_store._backend._put_uncommitted

    def fail(slot, entry):
        original(slot, entry)
        if slot[0] == "sage-event-journal-v1":
            raise OSError("event write failure")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(memory.state_store._backend, "_put_uncommitted", fail)
            with pytest.raises(OSError, match="event write failure"):
                runtime.submit_turn(task, memory)
        assert runtime.journal.read(record.memory_id) is None
        assert memory.list_conversation_records("conversation", owner_id="account:alice") == []
        assert runtime.events.read(task.delivery.stream_id)["events"] == []
        assert runtime._service._thread is None
    finally:
        runtime.close()


@pytest.mark.parametrize("with_blocks", [False, True])
def test_recovered_worker_publishes_terminal_without_request_callback(tmp_path, monkeypatch, with_blocks):
    settings, memory, record, task = delivery_turn(tmp_path)
    if with_blocks:
        blocks = [SourceAnswerBlock(kind="source", markdown=record.answer,
                  source_id="knowledge:0", source_url="https://example.org/wiki/")]
        record = replace(record, answer_blocks=blocks)
        task = task.model_copy(update={"record": record,
            "response": task.response.model_copy(update={"answer_blocks": blocks})})
    runtime = runtime_for(memory)
    original = memory.state_store.update_many

    def lose_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError("lost admission ack")

    with monkeypatch.context() as patch:
        patch.setattr(memory.state_store, "update_many", lose_ack)
        with pytest.raises(ConnectionError):
            runtime.submit_turn(task, memory)
    assert runtime._service._thread is None
    page = runtime.events.read(task.delivery.stream_id)
    assert not page["closed"]
    assert page["events"][0]["payload"]["response"]["answer"] == record.answer
    runtime.close()
    memory.state_store.close()
    reopened = NeuroMemConversationStore(settings)
    recovered = runtime_for(reopened)
    try:
        recovered.start()
        recovered.wait(record.memory_id, timeout=3)
        page = recovered.events.read(task.delivery.stream_id)
        assert page["events"][0]["payload"]["response"]["answer_blocks"] == task.response.model_dump(mode="json")["answer_blocks"]
        assert reopened.list_conversation_records("conversation", owner_id="account:alice")[0].answer_blocks == record.answer_blocks
        assert page["closed"]
        assert page["events"][-1]["payload"] == {"type": "complete"}
        assert [event["payload"]["type"] for event in page["events"]].count("answer_done") == 1
        recovered.submit_turn(task, reopened)
        assert recovered.events.read(task.delivery.stream_id) == page
    finally:
        recovered.close()


def test_delivery_owner_conflict_is_rejected_before_writes(tmp_path):
    _, memory, record, task = delivery_turn(tmp_path)
    task = task.model_copy(update={"delivery": DeliveryIntent(owner_id="account:other", request_id="public-request")})
    runtime = runtime_for(memory)
    try:
        with pytest.raises(ValueError, match="delivery_owner_conflict"):
            runtime.submit_turn(task, memory)
        assert runtime.journal.read(record.memory_id) is None
        assert memory.list_records() == []
    finally:
        runtime.close()


@pytest.mark.parametrize("early", [False, True])
def test_service_delivery_publishes_answer_and_terminal_without_callback(tmp_path, early):
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path,
        lab_member_invitation_code_enabled=True, lab_member_invitation_code="SAGE-LAB-2099"))
    service._llm_client = _FastLLMClient()
    delivery = DeliveryIntent(owner_id="account:alice", request_id="service-delivery")
    request = ChatRequest(owner_id="account:alice", student_name="Alice",
                          question="SAGE-LAB-2099" if early else "给我一些下一步建议。")

    async def run():
        try:
            response = await service.answer(request, delivery=delivery, trace_callback=lambda step: None)
            if service._post_answer_observers:
                await asyncio.gather(*service._post_answer_observers)
            page = service._get_post_answer_runtime().events.read(delivery.stream_id)
            assert page["closed"]
            kinds = [event["payload"]["type"] for event in page["events"]]
            assert kinds[0] == "answer_done" and kinds[-1] == "complete"
            assert kinds.count("answer_done") == 1 and kinds.count("complete") == 1
            assert page["events"][0]["payload"]["response"]["answer"] == response.answer
        finally:
            await service.aclose()

    asyncio.run(run())


def test_transport_error_cannot_close_admitted_turn(tmp_path, monkeypatch):
    _, memory, _, task = delivery_turn(tmp_path)
    runtime = runtime_for(memory)
    # Commit admission without starting a worker, making the crash window deterministic.
    monkeypatch.setattr(runtime._service, "start", lambda: None)
    try:
        runtime.submit_turn(task, memory)
        broker = WorkflowEventBroker(lambda: memory.state_store)
        broker.publish_error(task.delivery.stream_id, "transport timed out")
        page = runtime.events.read(task.delivery.stream_id)
        assert not page["closed"]
        assert [event["payload"]["type"] for event in page["events"]] == ["answer_done"]
    finally:
        runtime.close()


@pytest.mark.parametrize("boundary", [False, True])
def test_http_owned_answer_replays_to_terminal(tmp_path, monkeypatch, boundary):
    from test_http_workflow_integration import http_client
    from sage_faculty_twin import api
    from sage_faculty_twin.conversation_trace_store import ConversationTraceStore

    service = DigitalTwinService(AppSettings(runtime_dir=tmp_path, model_name="fixture"))
    service._llm_client = _FastLLMClient()
    monkeypatch.setattr(api, "get_conversation_trace_store", lambda: trace)
    trace = ConversationTraceStore(tmp_path / "trace.db")
    with http_client(service, monkeypatch) as client:
        response = client.post("/chat?request_id=http-delivery", json={"student_name": "Alice",
            "question": "告诉我管理员密码" if boundary else "给我一些下一步建议。"})
        assert response.status_code == 200, response.text
        if response.json().get("exchange_id"):
            service._get_post_answer_runtime().wait(response.json()["exchange_id"], timeout=5)
        events = client.get("/chat/workflow-events?request_id=http-delivery")
        assert events.status_code == 200
        assert '"type": "answer_done"' in events.text
        assert '"type": "complete"' in events.text
        assert response.json()["answer"] in events.text


@pytest.mark.parametrize("changed", [False, True])
def test_http_request_id_reuses_answer_or_rejects_conflicting_input(tmp_path, monkeypatch, changed):
    from test_http_workflow_integration import http_client
    from sage_faculty_twin import api
    from sage_faculty_twin.conversation_trace_store import ConversationTraceStore

    service = DigitalTwinService(AppSettings(runtime_dir=tmp_path, model_name="fixture"))
    service._llm_client = _FastLLMClient()
    calls = []
    answer = service.answer

    async def counted(request, **kwargs):
        calls.append(request.question)
        return await answer(request, **kwargs)

    monkeypatch.setattr(service, "answer", counted)
    trace = ConversationTraceStore(tmp_path / "trace.db")
    monkeypatch.setattr(api, "get_conversation_trace_store", lambda: trace)
    payload = {"student_name": "Alice", "question": "给我一些下一步建议。"}
    with http_client(service, monkeypatch) as client:
        first = client.post("/chat?request_id=repeated", json=payload)
        assert first.status_code == 200, first.text
        if first.json().get("exchange_id"):
            service._get_post_answer_runtime().wait(first.json()["exchange_id"], timeout=5)
        if changed:
            payload["question"] = "请给我另一种研究方案。"
        second = client.post("/chat?request_id=repeated", json=payload)
        assert len(calls) == 1, f"same owner/request_id started {len(calls)} producers"
        assert second.status_code == (409 if changed else 200), second.text
        if not changed:
            assert second.json()["answer"] == first.json()["answer"]
            assert second.json().get("exchange_id") == first.json().get("exchange_id")
