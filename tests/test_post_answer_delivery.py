import pytest

from sage_faculty_twin.memory_store import NeuroMemConversationStore
from sage_faculty_twin.post_answer_contract import DeliveryIntent
from test_atomic_turn_commit import runtime_for, setup_turn


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


def test_recovered_worker_publishes_terminal_without_request_callback(tmp_path, monkeypatch):
    settings, memory, record, task = delivery_turn(tmp_path)
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
