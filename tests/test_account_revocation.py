import json

import pytest
from fastapi import HTTPException

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.user_store import UserAccountStore


def make_store(tmp_path):
    settings = AppSettings(user_account_store_dir=tmp_path, lab_member_invitation_code_enabled=False)
    store = UserAccountStore(settings)
    user = store.register_user(name="Alice", email="alice@example.com", visitor_profile="lab_member", password="test-only")
    return store, user, tmp_path / f"{user.user_id}.json"


def test_existing_store_observes_external_downgrade_and_deletion(tmp_path):
    store, user, path = make_store(tmp_path)
    assert store.get_user_by_id(user.user_id).visitor_profile == "lab_member"
    payload = json.loads(path.read_text())
    payload["visitor_profile"] = "general_visitor"
    path.write_text(json.dumps(payload))
    assert store.get_user_by_id(user.user_id).visitor_profile == "general_visitor"
    path.unlink()
    assert store.get_user_by_id(user.user_id) is None


@pytest.mark.parametrize("kind", ["malformed", "identity"])
def test_bad_account_record_does_not_fall_back_to_cached_membership(tmp_path, kind):
    store, user, path = make_store(tmp_path)
    payload = json.loads(path.read_text())
    payload["user_id"] = "different-owner"
    path.write_text("not JSON" if kind == "malformed" else json.dumps(payload))
    with pytest.raises(ValueError):
        store.get_user_by_id(user.user_id)
    assert store.get_user_by_id("../other") is None


def test_login_observes_password_rotation_and_deleted_account(tmp_path):
    store, user, path = make_store(tmp_path)
    payload = json.loads(path.read_text())
    payload["password_hash"] = store._hash_password(password="rotated-test-only", salt=payload["password_salt"])
    payload["visitor_profile"] = "general_visitor"
    path.write_text(json.dumps(payload))
    with pytest.raises(HTTPException) as error:
        store.authenticate_user(email=user.email, password="test-only")
    assert error.value.status_code == 401
    assert store.authenticate_user(email=user.email, password="rotated-test-only").visitor_profile == "general_visitor"
    path.unlink()
    with pytest.raises(HTTPException) as error:
        store.authenticate_user(email=user.email, password="rotated-test-only")
    assert error.value.status_code == 401
