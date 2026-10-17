import asyncio
import hashlib
import json

import pytest
from fastapi.testclient import TestClient
from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime

from sage_faculty_twin import api
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.guest_identity import GUEST_COOKIE_NAME, GuestIdentityStore
from sage_faculty_twin.service import DigitalTwinService
from test_chat_post_answer_background import _FastLLMClient


def test_guest_credential_is_hash_only_persistent_and_non_sliding(tmp_path):
    path = str(tmp_path / "guest.sqlite3")
    now = [100.0]
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
    store = GuestIdentityStore(state, ttl_seconds=20, clock=lambda: now[0])
    token, session = store.issue()
    try:
        payload = state.peek(store._slot(token))
        assert token not in json.dumps(payload)
        assert token not in repr(store._slot(token))
        now[0] = 115
        assert store.resolve(token) == (None, session)
        assert state.peek(store._slot(token)) == payload
    finally:
        state.close()
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
    try:
        reopened = GuestIdentityStore(state, ttl_seconds=20, clock=lambda: now[0])
        assert reopened.authenticate(token) == session
        digest = hashlib.sha256(token.encode()).hexdigest()
        assert reopened.resolve_reference(digest) == session
        assert reopened.authenticate(digest) is None
        assert reopened.resolve_reference("../invalid") is None
        assert reopened.authenticate(token + "x") is None
        now[0] = 120
        assert reopened.authenticate(token) is None
        assert reopened.resolve_reference(digest) is None
        replacement, other = reopened.resolve(token)
        assert replacement != token and other.subject_id != session.subject_id
    finally:
        state.close()


@pytest.mark.parametrize("transport", ["json", "stream"])
def test_guest_cookie_precedes_delivery_and_preserves_owner(tmp_path, monkeypatch, transport):
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "memory", context_digest_dir=tmp_path / "digests",
        context_digest_enabled=True, context_digest_llm_enabled=False))
    service._llm_client = _FastLLMClient()
    monkeypatch.setattr(api, "service", service)
    payload = {"student_name": "Same", "student_email": "victim@example.com", "owner_id": "account:forged",
               "conversation_id": "shared-client-id", "question": "first private question"}

    async def scenario():
        import httpx
        flow = api._create_http_workflow(service)
        monkeypatch.setattr(api, "http_workflow", flow)
        await flow.start()
        try:
            asgi_transport = httpx.ASGITransport(app=api.app)
            async with httpx.AsyncClient(transport=asgi_transport, base_url="https://testserver") as first, \
                    httpx.AsyncClient(transport=asgi_transport, base_url="https://testserver") as second:
                path = "/chat" if transport == "json" else "/chat/stream"
                response = await first.post(path, json=payload)
                assert response.status_code == 200
                assert "httponly" in response.headers["set-cookie"].lower()
                assert "secure" in response.headers["set-cookie"].lower()
                assert response.headers["cache-control"] == "no-store"
                if transport == "stream":
                    events = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
                    body = next(event["response"] for event in events if event["type"] == "result")
                else:
                    body = response.json()
                owner = service._conversation_store.get_record(body["exchange_id"]).owner_id
                assert owner.startswith("guest:") and owner != payload["owner_id"]
                assert first.cookies.get(GUEST_COOKIE_NAME)
                again = await first.post("/chat", json={**payload, "question": "second private question"})
                assert again.status_code == 200
                assert service._conversation_store.get_record(again.json()["exchange_id"]).owner_id == owner
                outsider = await second.post("/chat", json={**payload, "question": "outsider private question", "owner_id": owner})
                assert outsider.status_code == 200
                other_owner = service._conversation_store.get_record(outsider.json()["exchange_id"]).owner_id
                assert other_owner != owner
                feedback = {"exchange_id": body["exchange_id"], "rating": "down", "resolved": False}
                before = service._analytics_store.count_feedback()
                forbidden = await second.post("/chat/feedback", json={**feedback, "owner_id": owner})
                missing = await second.post("/chat/feedback", json={**feedback, "exchange_id": "unknown"})
                assert forbidden.status_code == missing.status_code == 404
                assert forbidden.json() == missing.json()
                assert service._analytics_store.count_feedback() == before
                accepted = await first.post("/chat/feedback", json=feedback)
                assert accepted.status_code == 200
                assert service._analytics_store.count_feedback() == before + 1
                compressed = await first.post("/context/compress", json={"conversation_id": "shared-client-id"})
                assert compressed.status_code == 200
                digest = service._digest_store.get_digest("shared-client-id", owner_id=owner)
                assert digest.turns_summarized == 2
                assert "outsider private question" not in digest.digest_text
                assert service._conversation_store.list_conversation_records("shared-client-id") == []
        finally:
            await flow.aclose()
            await service.aclose()

    asyncio.run(scenario())


def test_guest_bootstrap_reuses_persisted_identity(tmp_path, monkeypatch):
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "guest.db"))
    store = GuestIdentityStore(state, ttl_seconds=300)
    monkeypatch.setattr(api.service, "_guest_identity_store", store)
    client = TestClient(api.app, base_url="https://testserver")
    try:
        first = client.get("/auth/user/session")
        assert first.status_code == 200
        token = client.cookies.get(GUEST_COOKIE_NAME)
        assert token
        subject = store.authenticate(token)
        assert subject
        assert client.get("/auth/user/session").status_code == 200
        assert store.authenticate(client.cookies.get(GUEST_COOKIE_NAME)) == subject
    finally:
        state.close()
