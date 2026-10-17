import base64
import copy
import json

import pytest

from sage_faculty_twin import auth
from sage_faculty_twin.config import AppSettings


@pytest.mark.parametrize("kind", ["user", "admin"])
def test_work_reference_cannot_login_and_preserves_expiry_and_rotation(monkeypatch, kind):
    now = [1000]
    monkeypatch.setattr(auth.time, "time", lambda: now[0])
    settings = AppSettings(user_session_secret="test-user-secret", admin_session_secret="test-admin-secret")
    token = (auth.build_user_session_token(user_id="alice", email="alice@example.invalid", settings=settings)
             if kind == "user" else auth.build_admin_session_token(settings, username="admin", role="manager"))
    reference = auth.capture_work_session_reference(token, settings, kind=kind)
    assert token not in json.dumps(reference)
    assert token.rsplit(".", 1)[1] != reference["proof"]
    claims = auth.resolve_work_session_reference(reference, settings, kind=kind)
    assert claims["sub"] == ("alice" if kind == "user" else "admin")
    decoder = auth.decode_user_session_token if kind == "user" else auth.decode_admin_session_token
    forged_cookie = base64.urlsafe_b64encode(json.dumps(reference["claims"],
        separators=(",", ":"), sort_keys=True).encode()).decode() + "." + reference["proof"]
    assert decoder(forged_cookie, settings) is None
    assert auth.capture_work_session_reference(forged_cookie, settings, kind=kind) is None
    assert auth.resolve_work_session_reference(reference, settings,
        kind="admin" if kind == "user" else "user") is None
    tampered = copy.deepcopy(reference)
    tampered["claims"]["exp"] += 9999
    assert auth.resolve_work_session_reference(tampered, settings, kind=kind) is None
    rotated = settings.model_copy(update={f"{kind}_session_secret": "rotated-test-secret"})
    assert auth.resolve_work_session_reference(reference, rotated, kind=kind) is None
    now[0] = claims["exp"]
    assert auth.resolve_work_session_reference(reference, settings, kind=kind) is None


@pytest.mark.parametrize("reference", [None, {}, {"version": 9}, "not-an-object"])
def test_invalid_reference_fails_closed(reference):
    assert auth.resolve_work_session_reference(reference, AppSettings(), kind="user") is None
