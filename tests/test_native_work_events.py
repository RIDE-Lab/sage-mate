"""Actual post-answer acknowledgement, fresh owner-scoped views and recovery."""

import json
from threading import Event

import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.post_answer_handler import PostAnswerHandler
from sage_faculty_twin.post_answer_runtime import work_evidence_for_exchange
from sage_faculty_twin.service import DigitalTwinService
from sage_faculty_twin.workflow_events import WorkflowEventBroker
from test_atomic_turn_commit import runtime_for
from test_chat_post_answer_background import _FastLLMClient
from test_http_workflow_integration import http_client
from test_post_answer_delivery import delivery_turn


def test_answer_return_is_not_background_ack_and_owner_view_refreshes_without_regeneration(tmp_path, monkeypatch):
    entered, release = Event(), Event()
    original = PostAnswerHandler.__call__
    calls = []

    def blocked(self, lease):
        calls.append(lease.request_id)
        entered.set()
        assert release.wait(10), "test did not release post-answer work"
        return original(self, lease)

    monkeypatch.setattr(PostAnswerHandler, "__call__", blocked)
    service = DigitalTwinService(AppSettings(runtime_dir=tmp_path, model_name="fixture"))
    service._llm_client = _FastLLMClient()
    with http_client(service, monkeypatch) as client:
        try:
            body = {"student_name": "Synthetic", "question": "给我一些下一步建议。"}
            response = client.post("/chat?request_id=work-evidence", json=body)
            assert response.status_code == 200, response.text
            assert entered.wait(5)
            data = response.json()
            exchange = data["exchange_id"]
            url = f"/chat/execution?exchange_id={exchange}"
            started = client.get(url)
            assert started.status_code == 200, started.text
            assert started.headers["cache-control"] == "no-store"
            assert set(started.json()) == {"exchange_id", "request_id", "execution_events", "work_events", "work_status", "stream_closed"}
            assert started.json()["stream_closed"] is False
            assert started.json()["work_status"]["tasks"][0]["status"] == "running"
            assert [e["state"] for e in started.json()["work_events"]] == ["started"]
            runtime = service._get_post_answer_runtime()
            task = runtime.journal.read(exchange)["tasks"]["post_answer"]
            assert task["status"] == "running"
            canonical = service._conversation_store.get_canonical_record(exchange).to_dict()
            assert canonical["answer"] == data["answer"]
            from sage_faculty_twin.post_answer_contract import PostAnswerTask
            captured = PostAnswerTask.model_validate(task["payload"])
            assert not runtime.events.read(captured.delivery.stream_id)["closed"]
            cookies = dict(client.cookies)
            client.cookies.clear()
            denied = client.get(url)
            assert denied.status_code == 404
            assert client.get("/chat/execution?exchange_id=nonexistent").status_code == 404
            assert client.get(url + "&owner_id=" + canonical["owner_id"]).status_code == 404
            client.cookies.clear()
            client.cookies.update(cookies)
            release.set()
            runtime.wait(exchange, timeout=5)
            finished = client.get(url)
            assert finished.status_code == 200
            assert [e["state"] for e in finished.json()["work_events"]] == ["started", "completed"]
            assert finished.json()["stream_closed"] is True
            assert finished.json()["work_status"]["tasks"][0]["status"] == "done"
            assert "payload" not in finished.json()["work_status"]["tasks"][0]
            assert "token" not in finished.json()["work_status"]["tasks"][0]
            assert finished.json()["execution_events"] == started.json()["execution_events"]
            assert service._conversation_store.get_canonical_record(exchange).to_dict() == canonical
            replay = client.post("/chat?request_id=work-evidence", json=body)
            assert replay.status_code == 200
            assert replay.json()["answer"] == data["answer"]
            assert replay.json()["work_events"] == finished.json()["work_events"]
            assert calls == [exchange]
            projected = service._work_events_for_exchange(service._conversation_store.get_canonical_record(exchange))
            assert json.loads(json.dumps([e.to_dict() for e in projected])) == finished.json()["work_events"]
            page = client.get("/chat/workflow-events?request_id=work-evidence")
            frames = [json.loads(line[6:]) for line in page.text.splitlines() if line.startswith("data: ")]
            types = [frame["type"] for frame in frames]
            assert types[-1] == "complete"
            assert types.index("answer_done") < types.index("work-event")
            assert [frame["event"]["state"] for frame in frames if frame["type"] == "work-event"] == ["started", "completed"]
        finally:
            release.set()


def test_completion_event_and_task_ack_roll_back_together_and_reopen_exactly(tmp_path, monkeypatch):
    _, memory, record, task = delivery_turn(tmp_path)
    runtime = runtime_for(memory)
    # Keep this deterministic at the commit boundary; the other test exercises
    # the actual managed worker and HTTP path.
    monkeypatch.setattr(runtime._service, "start", lambda: None)
    try:
        runtime.submit_turn(task, memory)
        lease = runtime.journal.claim(record.memory_id, "post_answer", lease_seconds=30)
        dispatcher = runtime._service._dispatcher
        attempt = dispatcher._observer.bind(lease)
        attempt.started()
        result = dispatcher.handler(lease)
        mutations = runtime._completion_events(lease, result)
        observation = attempt.prepare("completed")
        backend = memory.state_store._backend
        original = backend._put_uncommitted

        def fail(slot, entry):
            original(slot, entry)
            if slot[0] == "sage-event-journal-v1":
                raise OSError("injected event storage failure")

        before = runtime.events.read(task.delivery.stream_id)
        with monkeypatch.context() as patch:
            patch.setattr(backend, "_put_uncommitted", fail)
            with pytest.raises(OSError, match="injected"):
                runtime.journal.finish(lease, result=result, mutations=mutations, observation=observation)
        assert runtime.events.read(task.delivery.stream_id) == before
        assert runtime.journal.read(record.memory_id)["tasks"]["post_answer"]["status"] == "running"
        assert [e.state for e in WorkflowEventBroker(lambda: memory.state_store).read_work_events(task.delivery.stream_id)] == ["started"]
        runtime.journal.finish(lease, result=result, mutations=mutations, observation=observation)
        page = runtime.events.read(task.delivery.stream_id)
        assert page["closed"] and page["events"][-1]["payload"] == {"type": "complete"}
        assert runtime.journal.read(record.memory_id)["tasks"]["post_answer"]["status"] == "done"
        from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(memory.state_store._backend._conn.execute("PRAGMA database_list").fetchone()[2]))
        try:
            evidence = work_evidence_for_exchange(state, record.memory_id, owner_id="account:alice")
            assert [e.state for e in evidence["work_events"]] == ["started", "completed"]
            assert evidence["request_id"] == "public-request"
            with pytest.raises(PermissionError):
                work_evidence_for_exchange(state, record.memory_id, owner_id="account:other")
            assert work_evidence_for_exchange(state, "legacy-unrecorded", owner_id="account:alice") == {
                "request_id": None, "work_events": [], "work_status": None, "stream_closed": None}
        finally:
            state.close()
    finally:
        runtime.close()
