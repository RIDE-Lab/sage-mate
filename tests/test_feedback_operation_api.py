import asyncio

from fastapi.testclient import TestClient

from sage_faculty_twin import api
from sage_faculty_twin.chat_identity import ChatIdentity
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import ChatRequest, WebSearchHit
from sage_faculty_twin.service import DigitalTwinService


def test_feedback_http_status_is_owned_historical_and_read_only(tmp_path, monkeypatch):
    service = DigitalTwinService(AppSettings(
        knowledge_base_dir=tmp_path / "knowledge", conversation_memory_dir=tmp_path / "memory",
        knowledge_backend="local", conversation_memory_index_type="segment",
        feedback_writeback_wait_seconds=0))
    monkeypatch.setattr(api, "service", service)
    identity = [ChatIdentity("account", "owner", "public")]
    monkeypatch.setattr(api, "_resolve_chat_identity", lambda *args: identity[0])
    record = service._conversation_store.add_exchange(
        ChatRequest(student_name="Owner", owner_id="account:owner", question="Private question"),
        conversation_id="status", answer="[Source](https://example.org/source)",
        workflow_action="answer", interaction_domain="research", knowledge_hit_count=0,
        booking_result=None, web_search_hits=[WebSearchHit(
            title="Source", url="https://example.org/source", snippet="Evidence")])
    other = service._conversation_store.add_exchange(
        ChatRequest(student_name="Owner", owner_id="account:owner", question="Another question"),
        conversation_id="other", answer="Answer", workflow_action="answer",
        interaction_domain="research", knowledge_hit_count=0, booking_result=None)
    runtime = service._get_feedback_runtime()
    client = TestClient(api.app)
    original_request = {"exchange_id": record.memory_id, "rating": "up", "request_id": "stable-request"}
    try:
        with monkeypatch.context() as patch:
            patch.setattr(runtime._service, "start", lambda: None)
            response = client.post("/chat/feedback", json=original_request)
            assert response.status_code == 503
            operation = response.json()["detail"]["operation_id"]
            retry = client.post("/chat/feedback", json=original_request)
            assert retry.status_code == 503
            assert retry.json()["detail"]["operation_id"] == operation
            url = f"/chat/feedback/{operation}"
            params = {"exchange_id": record.memory_id}
            pending = client.get(url, params=params)
            assert pending.status_code == 200
            assert pending.json()["status"] == "pending"
            assert pending.json()["feedback"]["knowledge_write_backs"] == []
            assert runtime._service._thread is None
            assert service.list_knowledge() == []
            wrong_exchange = client.get(url, params={"exchange_id": other.memory_id})
            missing = client.get("/chat/feedback/missing", params=params)
            identity[0] = ChatIdentity("account", "foreign", "public")
            foreign = client.get(url, params=params)
            assert wrong_exchange.status_code == missing.status_code == foreign.status_code == 404
            assert wrong_exchange.json() == missing.json() == foreign.json()
            identity[0] = ChatIdentity("account", "owner", "public")

        runtime.start()
        runtime.wait(operation, timeout=3)
        completed = client.get(url, params=params).json()
        assert completed["status"] == "completed"
        assert len(completed["feedback"]["knowledge_write_backs"]) == 1
        assert completed["feedback"]["operation_id"] == operation
        later = client.post("/chat/feedback", json={
            "exchange_id": record.memory_id, "rating": "down"})
        assert later.status_code == 200
        assert later.json()["operation_id"] != operation
        assert service._analytics_store.get_feedback(record.memory_id).rating == "down"
        assert client.get(url, params=params).json() == completed
        replay = client.post("/chat/feedback", json=original_request)
        assert replay.status_code == 200
        assert replay.json() == completed["feedback"]
        assert service._analytics_store.get_feedback(record.memory_id).rating == "down"
        conflict = client.post("/chat/feedback", json={**original_request, "rating": "down"})
        assert conflict.status_code == 409
        assert client.get(url, params=params).json() == completed
        another = client.post("/chat/feedback", json={**original_request, "exchange_id": other.memory_id})
        assert another.status_code == 200
        assert another.json()["operation_id"] != operation
        assert "payload" not in completed
        assert "error" not in completed
    finally:
        client.close()
        asyncio.run(service.aclose())
