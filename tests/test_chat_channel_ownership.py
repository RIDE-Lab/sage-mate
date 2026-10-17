from types import SimpleNamespace

from fastapi.testclient import TestClient

from sage_faculty_twin import api
from sage_faculty_twin.chat_identity import ChatIdentity
from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime


def test_event_and_cancel_routes_scope_same_public_id_to_trusted_owner(monkeypatch):
    cancelled = []
    async def cancel(*, owner_id, request_id):
        cancelled.append((owner_id, request_id))
        return "cancelled"
    monkeypatch.setattr(api, "http_workflow", SimpleNamespace(cancel=cancel))
    monkeypatch.setattr(api, "_resolve_chat_identity", lambda request, *_:
                        ChatIdentity("account", request.headers["x-test-owner"], "public"))
    keys = []

    async def stream(key, *, after):
        keys.append(key)
        yield 'data: {"type":"keepalive"}\n\n'

    monkeypatch.setattr(api, "workflow_event_broker", SimpleNamespace(stream=stream, validate_cursor=lambda *args: None))
    stranger_key = api._owned_chat_request_key("account:stranger", "same-id")
    client = TestClient(api.llm_app)
    try:
        response = client.get("/chat/workflow-events", params={"request_id": "same-id"},
                              headers={"x-test-owner": "stranger"})
        assert response.status_code == 200
        assert keys == [stranger_key]
        assert cancelled == []
        response = client.post("/chat/cancel", params={"request_id": "same-id"},
                               headers={"x-test-owner": "stranger"})
        assert response.json()["cancelled"] is True
        assert cancelled == [("account:stranger", "same-id")]
        response = client.post("/chat/cancel", params={"request_id": "same-id"},
                               headers={"x-test-owner": "owner"})
        assert response.json()["cancelled"] is True
        assert cancelled == [("account:stranger", "same-id"), ("account:owner", "same-id")]
    finally:
        client.close()


def test_owned_http_replay_cursor_and_disconnect_are_safe(monkeypatch):
    state = StatefulProcessRuntime(backend="memory")
    broker = api.WorkflowEventBroker(lambda: state)
    monkeypatch.setattr(api, "workflow_event_broker", broker)
    monkeypatch.setattr(api, "_resolve_chat_identity", lambda request, *_:
                        ChatIdentity("account", request.headers["x-test-owner"], "public"))
    key = api._owned_chat_request_key("account:owner", "replay")
    broker.publish_complete(key)
    client = TestClient(api.llm_app)
    try:
        headers = {"x-test-owner": "owner"}
        params = {"request_id": "replay"}
        response = client.get("/chat/workflow-events", params=params, headers=headers)
        assert response.status_code == 200
        assert response.text.startswith("id: 1\ndata: ")
        assert '"type": "complete"' in response.text
        resumed = client.get("/chat/workflow-events", params=params,
                             headers={**headers, "Last-Event-ID": "1"})
        assert resumed.status_code == 200 and resumed.text == ""
        for invalid in ("-1", "NaN", "9" * 21):
            assert client.get("/chat/workflow-events", params=params,
                              headers={**headers, "Last-Event-ID": invalid}).status_code == 400
        assert client.get("/chat/workflow-events", params=params,
                          headers={**headers, "Last-Event-ID": "2"}).status_code == 409
        assert client.get("/chat/workflow-events", params=params,
                          headers={"x-test-owner": "other", "Last-Event-ID": "1"}).status_code == 409
    finally:
        client.close()
        state.close()
