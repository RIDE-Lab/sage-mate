from __future__ import annotations

from pathlib import Path

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from sage_faculty_twin import api as api_module
from sage_faculty_twin.api_token_store import LabMemberApiTokenStore
from sage_faculty_twin.config import AppSettings


def _request(*, authorization: str = "") -> Request:
    headers = []
    if authorization:
        headers.append((b"authorization", authorization.encode()))
    return Request({"type": "http", "method": "POST", "path": "/chat", "headers": headers})


def test_lab_member_api_token_is_hash_only_and_revocable(tmp_path: Path) -> None:
    store = LabMemberApiTokenStore(AppSettings(user_account_store_dir=tmp_path))

    token, identity = store.issue(label="portfolio-audit", ttl_seconds=3600)

    record = (tmp_path / "api_tokens" / f"{identity.token_id}.json").read_text()
    assert token not in record
    assert store.authenticate(token) == identity
    assert store.authenticate(token + "x") is None
    assert store.revoke(identity.token_id)
    assert store.authenticate(token) is None


def test_bearer_token_grants_only_lab_member_profile(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    store = LabMemberApiTokenStore(AppSettings(user_account_store_dir=tmp_path))
    token, _ = store.issue(label="portfolio-audit", ttl_seconds=3600)
    monkeypatch.setattr(api_module, "lab_member_api_token_store", store)

    profile = api_module._resolve_effective_chat_visitor_profile(
        _request(authorization=f"Bearer {token}"),
        "general_visitor",
    )

    assert profile == "lab_member"


def test_invalid_bearer_token_is_rejected(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    store = LabMemberApiTokenStore(AppSettings(user_account_store_dir=tmp_path))
    monkeypatch.setattr(api_module, "lab_member_api_token_store", store)

    with pytest.raises(HTTPException, match="无效或过期") as error:
        api_module._resolve_effective_chat_visitor_profile(
            _request(authorization="Bearer invalid"),
            "lab_member",
        )

    assert error.value.status_code == 401
