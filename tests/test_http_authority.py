import hashlib
import json

import pytest
from pydantic import ValidationError
from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime

from sage_faculty_twin.api_token_store import LabMemberApiTokenStore
from sage_faculty_twin.auth import build_user_session_token, capture_work_session_reference
from sage_faculty_twin.chat_identity import ChatIdentity
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.guest_identity import GuestIdentityStore
from sage_faculty_twin.http_authority import HttpAuthority, HttpAuthorityResolver
from sage_faculty_twin.models import ChatRequest
from sage_faculty_twin.user_store import UserAccountStore


def test_authority_uses_current_accounts_tokens_and_guests(tmp_path):
    settings = AppSettings(user_account_store_dir=tmp_path / "accounts")
    users = UserAccountStore(settings)
    tokens = LabMemberApiTokenStore(settings)
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))
    now = [10.0]
    guests = GuestIdentityStore(state, ttl_seconds=5, clock=lambda: now[0])
    resolver = HttpAuthorityResolver(settings=settings, users=users, tokens=tokens, guests=guests)
    request = ChatRequest(student_name="Alice", question="test")
    try:
        token, identity = tokens.issue(label="test")
        authority = HttpAuthority(kind="api_token", token_id=identity.token_id,
                                  digest=hashlib.sha256(token.encode()).hexdigest())
        bound = ChatIdentity("api_token", identity.token_id, "lab_member").bind(request)
        assert resolver.resolve(authority, bound)[0].owner_id == bound.owner_id
        with pytest.raises(PermissionError):
            resolver.resolve(authority, bound.model_copy(update={"owner_id": "account:other"}))
        tokens.revoke(identity.token_id)
        with pytest.raises(PermissionError):
            resolver.resolve(authority, bound)
        token, guest = guests.issue()
        authority = HttpAuthority(kind="guest", digest=hashlib.sha256(token.encode()).hexdigest())
        bound = ChatIdentity("anonymous", guest.subject_id, "general_visitor").bind(request)
        assert resolver.resolve(authority, bound)[0].owner_id == bound.owner_id
        now[0] = 15
        with pytest.raises(PermissionError):
            resolver.resolve(authority, bound)
        account = users.register_user(name="Alice", email="alice@example.com", password="Test-password-123",
                                      visitor_profile="general_visitor")
        cookie = build_user_session_token(user_id=account.user_id, email=account.email, settings=settings)
        authority = HttpAuthority(kind="account", session=capture_work_session_reference(cookie, settings, kind="user"))
        bound = ChatIdentity("account", account.user_id, account.visitor_profile, account.email, account.name).bind(request)
        assert resolver.resolve(authority, bound)[0].owner_id == bound.owner_id
        path = settings.user_account_store_dir / f"{account.user_id}.json"
        original = json.loads(path.read_text())
        path.write_text(json.dumps({**original, "visitor_profile": "lab_member"}))
        with pytest.raises(PermissionError, match="scope_changed"):
            resolver.resolve(authority, bound)
        path.unlink()
        with pytest.raises(PermissionError):
            resolver.resolve(authority, bound)
    finally:
        state.close()


def test_authority_contract_rejects_raw_credentials_and_ambiguous_shapes():
    with pytest.raises(ValidationError):
        HttpAuthority(kind="guest", digest="a" * 64, token="not-persistable")
    with pytest.raises(ValidationError):
        HttpAuthority(kind="account", digest="a" * 64)
