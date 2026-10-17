from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient
from starlette.requests import Request

from sage_faculty_twin import api
from sage_faculty_twin.auth import USER_COOKIE_NAME
from sage_faculty_twin.chat_identity import ChatIdentity
from sage_faculty_twin.models import ChatRequest, ChatResponse


def request(*, authorization=None, cookie=False):
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    if cookie:
        headers.append((b"cookie", f"{USER_COOKIE_NAME}=test-session".encode()))
    return Request({"type": "http", "headers": headers})


def account_session():
    return SimpleNamespace(is_authenticated=True, account=SimpleNamespace(
        user_id="account-1", name="Account Name", email="Owner@Example.com", visitor_profile="lab_member"))


def test_account_binds_server_identity_and_rejects_other_email(monkeypatch):
    monkeypatch.setattr(api.service, "get_user_session", lambda _: account_session())
    identity = api._resolve_chat_identity(request(cookie=True), "general_visitor")
    payload = ChatRequest(student_name="Impersonated Name", question="hello", answer_max_tokens=4096)
    bound = identity.bind(payload)
    assert identity.subject_id == "account-1"
    assert bound.student_name == "Account Name"
    assert bound.student_email == "owner@example.com"
    assert bound.visitor_profile == "lab_member"
    assert bound.answer_max_tokens == 4096
    assert payload.student_email is None
    with pytest.raises(HTTPException) as error:
        identity.bind(payload.model_copy(update={"student_email": "victim@example.com"}))
    assert error.value.status_code == 403
    assert identity.bind(payload.model_copy(update={"student_email": " OWNER@example.com "})).student_email == "owner@example.com"


@pytest.mark.parametrize("cookie", [False, True])
def test_anonymous_or_expired_session_does_not_claim_account(monkeypatch, cookie):
    monkeypatch.setattr(api.service, "get_user_session", lambda _: SimpleNamespace(is_authenticated=False, account=None))
    identity = api._resolve_chat_identity(request(cookie=cookie), "lab_member")
    bound = identity.bind(ChatRequest(student_name="Guest", student_email="victim@example.com", question="hello",
                                     answer_max_tokens=4096))
    assert identity.kind == "anonymous"
    assert identity.subject_id
    assert bound.owner_id == f"guest:{identity.subject_id}"
    assert bound.student_email is None
    assert bound.visitor_profile == "general_visitor"
    assert bound.answer_max_tokens is None


def test_bearer_identity_does_not_borrow_cookie_account(monkeypatch):
    monkeypatch.setattr(api.lab_member_api_token_store, "authenticate", lambda _: SimpleNamespace(
        token_id="automation-id", visitor_profile="lab_member"))
    def forbidden_cookie(_):
        raise AssertionError("bearer request borrowed browser identity")
    monkeypatch.setattr(api.service, "get_user_session", forbidden_cookie)
    identity = api._resolve_chat_identity(request(authorization="Bearer synthetic-test-token", cookie=True), None)
    bound = identity.bind(ChatRequest(student_name="Automation", student_email="victim@example.com", question="hello"))
    assert identity.kind == "api_token"
    assert identity.subject_id == "automation-id"
    assert bound.visitor_profile == "lab_member"
    assert bound.student_email is None


def test_non_account_cannot_carry_server_account_fields():
    with pytest.raises(ValueError, match="cannot_claim_personal_account"):
        ChatIdentity("api_token", "id", "lab_member", "victim@example.com").bind(
            ChatRequest(student_name="Guest", question="hello"))


@pytest.mark.parametrize("route,method", [("/chat/conversations", "list_chat_conversations"),
                                         ("/chat/conversations/same", "get_chat_conversation")])
def test_history_owner_comes_from_authenticated_account(monkeypatch, route, method):
    monkeypatch.setattr(api.service, "get_user_session", lambda _: account_session())
    seen = []
    def capture(**kwargs):
        seen.append(kwargs)
        raise HTTPException(status_code=404, detail="test empty history")
    monkeypatch.setattr(api.service, method, capture)
    response = TestClient(api.app).get(route, params={"owner_id": "account:forged"})
    assert response.status_code == 404
    assert seen[0]["owner_id"] == "account:account-1"
    assert seen[0]["student_email"] == "owner@example.com"


@pytest.mark.parametrize("transport", ["json", "multipart"])
@pytest.mark.parametrize("claimed_email,expected", [(None, 200), ("victim@example.com", 403)])
def test_http_binds_before_workflow_and_rejects_impersonation(monkeypatch, transport, claimed_email, expected):
    seen = []
    monkeypatch.setattr(api.service, "get_user_session", lambda _: account_session())
    async def answer(payload, **kwargs):
        seen.append(payload)
        return ChatResponse(answer="ok", owner_name="Owner", used_model="test")
    monkeypatch.setattr(api.service, "answer", answer)
    payload = {"student_name": "Forged", "question": "hello", "visitor_profile": "general_visitor"}
    if claimed_email:
        payload["student_email"] = claimed_email
    client = TestClient(api.app)
    client.cookies.set(USER_COOKIE_NAME, "test-session")
    kwargs = ({"json": payload} if transport == "json" else
              {"data": payload, "files": {"files": ("note.txt", b"safe attachment", "text/plain")}})
    response = client.post("/chat", **kwargs)
    assert response.status_code == expected
    if expected == 403:
        assert seen == []
    else:
        assert len(seen) == 1
        assert seen[0].student_email == "owner@example.com"
        assert seen[0].student_name == "Account Name"
        assert seen[0].visitor_profile == "lab_member"
