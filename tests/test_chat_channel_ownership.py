from types import SimpleNamespace

from fastapi.testclient import TestClient

from sage_faculty_twin import api
from sage_faculty_twin.chat_identity import ChatIdentity
from sage_faculty_twin.request_context import RequestCancellationController


def test_event_and_cancel_routes_scope_same_public_id_to_trusted_owner(monkeypatch):
    registry = api.ActiveChatRequestRegistry()
    monkeypatch.setattr(api, "active_chat_request_registry", registry)
    monkeypatch.setattr(api, "_resolve_chat_identity", lambda request, *_:
                        ChatIdentity("account", request.headers["x-test-owner"], "public"))
    keys = []

    async def stream(key, *, on_disconnect):
        keys.append(key)
        yield 'data: {"type":"keepalive"}\n\n'
        on_disconnect()

    monkeypatch.setattr(api, "workflow_event_broker", SimpleNamespace(stream=stream))
    owner = RequestCancellationController()
    stranger = RequestCancellationController()
    owner_key = api._owned_chat_request_key("account:owner", "same-id")
    stranger_key = api._owned_chat_request_key("account:stranger", "same-id")
    registry.register(owner_key, owner)
    registry.register(stranger_key, stranger)
    assert not owner.is_cancelled()  # Other owner's registration cannot replace it.
    client = TestClient(api.llm_app)
    try:
        response = client.get("/chat/workflow-events", params={"request_id": "same-id"},
                              headers={"x-test-owner": "stranger"})
        assert response.status_code == 200
        assert keys == [stranger_key]
        assert stranger.is_cancelled()
        assert not owner.is_cancelled()
        response = client.post("/chat/cancel", params={"request_id": "same-id"},
                               headers={"x-test-owner": "stranger"})
        assert response.json()["cancelled"] is False
        assert not owner.is_cancelled()
        response = client.post("/chat/cancel", params={"request_id": "same-id"},
                               headers={"x-test-owner": "owner"})
        assert response.json()["cancelled"] is True
        assert owner.is_cancelled()
    finally:
        client.close()
