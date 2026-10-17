import asyncio
from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from sage_faculty_twin import api
from sage_faculty_twin.chat_identity import ChatIdentity


@pytest.mark.parametrize("change", ["revoke", "role", "owner"])
def test_event_stream_rechecks_before_sending_each_frame(monkeypatch, change):
    async def scenario():
        identity = ChatIdentity("api_token", "owner", "lab_member")
        changed = False
        closed = []
        def resolve(request, profile, refresh=False):
            if not changed:
                return identity
            assert refresh
            if change == "revoke":
                raise HTTPException(401, "private diagnostic")
            return ChatIdentity("api_token", "other" if change == "owner" else "owner", "public")
        async def stream(key, *, after):
            nonlocal changed
            try:
                yield 'data: {"type":"keepalive"}\n\n'
                changed = True
                yield 'data: {"answer":"must never escape"}\n\n'
                pytest.fail("denied observer must stop consuming")
            finally:
                closed.append(True)
        monkeypatch.setattr(api, "_resolve_chat_identity", resolve)
        monkeypatch.setattr(api, "workflow_event_broker", SimpleNamespace(
            stream=stream, validate_cursor=lambda *args: None))
        response = await api.chat_workflow_events(Request({"type": "http", "headers": []}), "r")
        frames = [frame async for frame in response.body_iterator]
        assert len(frames) == 2
        assert "keepalive" in frames[0]
        assert "authority_denied" in frames[1]
        assert "must never escape" not in str(frames) and "private diagnostic" not in str(frames)
        assert closed == [True]
    asyncio.run(scenario())


def test_refresh_ignores_cached_identity_without_issuing_guest(monkeypatch):
    request = Request({"type": "http", "headers": []})
    request.state.chat_identity = ChatIdentity("account", "cached", "lab_member")
    monkeypatch.setattr(api, "service", SimpleNamespace(_guest_identity_store=SimpleNamespace()))
    with pytest.raises(HTTPException) as error:
        api._resolve_chat_identity(request, None, True)
    assert error.value.status_code == 401


def test_real_token_revocation_stops_open_stream(tmp_path, monkeypatch):
    from sage_faculty_twin.api_token_store import LabMemberApiTokenStore
    from sage_faculty_twin.config import AppSettings

    async def scenario():
        settings = AppSettings(user_account_store_dir=tmp_path)
        store = LabMemberApiTokenStore(settings)
        credential, identity = store.issue(label="isolated-stream-test")
        monkeypatch.setattr(api, "lab_member_api_token_store", store)
        closed = []
        async def stream(key, *, after):
            try:
                yield 'data: {"type":"keepalive"}\n\n'
                # Another store instance revokes while this observer is open.
                assert LabMemberApiTokenStore(settings).revoke(identity.token_id)
                yield 'data: {"answer":"revoked private content"}\n\n'
            finally:
                closed.append(True)
        monkeypatch.setattr(api, "workflow_event_broker", SimpleNamespace(
            stream=stream, validate_cursor=lambda *args: None))
        request = Request({"type": "http", "headers": [
            (b"authorization", ("Bearer " + credential).encode())]})
        # A cached initial identity must not bypass revocation.
        request.state.chat_identity = ChatIdentity("api_token", identity.token_id, identity.visitor_profile)
        response = await api.chat_workflow_events(request, "r")
        frames = [frame async for frame in response.body_iterator]
        assert len(frames) == 2 and "authority_denied" in frames[-1]
        assert "revoked private content" not in str(frames)
        assert closed == [True]
    asyncio.run(scenario())
