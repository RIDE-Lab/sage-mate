import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest

from sage_faculty_twin.slack_link_store import SlackUserLinkRecord, SlackUserLinkStore
from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage.runtime.work_journal import WorkJournal


def issue(store, user="account"):
    return store.create_code(user_id=user, email=f"{user}@example.com", visitor_profile="lab_member")


def test_binding_and_notice_rollback_together(tmp_path, monkeypatch):
    store = SlackUserLinkStore(tmp_path / "legacy")
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "shared.db"))
    store.migrate_to(state)
    journal = WorkJournal(state, namespace="notices")
    code = issue(store)
    mutations = store.prepare_code_consumption(code.code, team_id="T1", slack_user_id="U1", operation_id="event")
    original = state._backend._put_uncommitted
    writes = []
    def fail(*args):
        original(*args)
        writes.append(True)
        if len(writes) == 2:
            raise OSError("binding write failed")
    try:
        with monkeypatch.context() as patch:
            patch.setattr(state._backend, "_put_uncommitted", fail)
            with pytest.raises(OSError):
                journal.commit("event", result="bound", tasks={"notice": {}}, mutations=mutations)
        assert journal.read("event") is None
        assert store.get_link_for_user("account") is None
        journal.commit("event", result="bound", tasks={"notice": {}}, mutations=mutations)
        assert store.get_link_for_user("account").slack_user_id == "U1"
        journal.commit("event", result="bound", tasks={"notice": {}}, mutations=mutations)
        changed = store.prepare_code_consumption("OTHER", team_id="T1", slack_user_id="U1", operation_id="event")
        with pytest.raises(ValueError, match="operation_conflict"):
            journal.commit("event", result="bound", tasks={"notice": {}}, mutations=changed)
    finally:
        store.close()
        state.close()


def test_migration_keeps_new_target_state_and_rejects_changed_source(tmp_path):
    root = tmp_path / "legacy"
    store = SlackUserLinkStore(root)
    store.consume_code(issue(store).code, team_id="T1", slack_user_id="U1")
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "shared.db"))
    try:
        store.migrate_to(state)
        store.consume_code(issue(store).code, team_id="T1", slack_user_id="U2")
        reopened = SlackUserLinkStore(root)
        reopened.migrate_to(state)
        assert reopened.get_link_for_user("account").slack_user_id == "U2"
        reopened.close()
        assert store.get_link_for_user("account").slack_user_id == "U2"
        stale_writer = SlackUserLinkStore(root)
        try:
            issue(stale_writer, "unexpected")
            with pytest.raises(ValueError, match="migration_conflict"):
                stale_writer.migrate_to(state)
        finally:
            stale_writer.close()
        assert store.get_link_for_user("account").slack_user_id == "U2"
    finally:
        store.close()
        state.close()


def test_workspace_binding_does_not_grant_other_workspace_access(tmp_path):
    store = SlackUserLinkStore(tmp_path)
    try:
        store.consume_code(issue(store).code, team_id="T1", slack_user_id="U1")
        assert store.get_link_for_slack_user("U1", team_id="T2") is None
        store.consume_code(issue(store, "other").code, team_id="T2", slack_user_id="U1")
        assert store.get_link_for_slack_user("U1", team_id="T1").user_id == "account"
        assert store.get_link_for_slack_user("U1", team_id="T2").user_id == "other"
        store.consume_code(issue(store).code, team_id="T3", slack_user_id="U2")
        assert store.get_link_for_slack_user("U1", team_id="T1") is None
        assert store.get_link_for_slack_user("U1", team_id="T2").user_id == "other"
    finally:
        store.close()


def test_v1_bindings_are_preserved_without_guessing_workspace(tmp_path):
    store = SlackUserLinkStore(tmp_path)
    legacy = SlackUserLinkRecord("U1", "account", "a@example.com", "lab_member", datetime.now(UTC)).to_dict()
    legacy.pop("team_id")
    store._state.update(store._slot, lambda _: {"version": 1, "links": {"U1": legacy}, "codes": {}})
    store.close()
    reopened = SlackUserLinkStore(tmp_path)
    try:
        assert reopened.get_link_for_slack_user("U1", team_id="T1") is None
        assert reopened.get_link_for_user("account") is None
        assert reopened._snapshot()["unscoped_links"] == {"U1": legacy}
        reopened.consume_code(issue(reopened).code, team_id="T1", slack_user_id="U1")
        assert reopened.get_link_for_user("account").team_id == "T1"
    finally:
        reopened.close()


def test_unknown_state_version_cannot_be_mutated(tmp_path):
    store = SlackUserLinkStore(tmp_path)
    try:
        store._state.update(store._slot, lambda _: {"version": 99, "links": {}, "codes": {}})
        for action in (lambda: issue(store), lambda: store.consume_code("CODE", team_id="T123", slack_user_id="U1"),
                       store.prune_expired_codes):
            with pytest.raises(ValueError, match="unsupported_slack_binding_version"):
                action()
        assert store._state.peek(store._slot)["version"] == 99
    finally:
        store.close()


def test_conflicting_legacy_codes_fail_before_import(tmp_path):
    from datetime import timedelta
    from sage_faculty_twin.slack_link_store import SlackLinkCodeRecord

    directory = tmp_path / "codes"
    directory.mkdir()
    now = datetime.now(UTC)
    for user in ("first", "second"):
        record = SlackLinkCodeRecord("SAMECODE", user, f"{user}@example.com", "lab_member",
                                     now + timedelta(minutes=10), now)
        (directory / f"{user}.json").write_text(json.dumps(record.to_dict()))
    with pytest.raises(ValueError, match="legacy_slack_code_conflict"):
        SlackUserLinkStore(tmp_path)
    assert len(list(directory.glob("*.json"))) == 2
    assert not (tmp_path / "legacy-imported").exists()


def test_rebinding_revokes_old_identity_across_open_instances(tmp_path):
    first, second = SlackUserLinkStore(tmp_path), SlackUserLinkStore(tmp_path)
    try:
        first.consume_code(issue(first).code, team_id="T123", slack_user_id="U1")
        assert second.get_link_for_slack_user("U1", team_id="T123") is not None
        first.consume_code(issue(first).code, team_id="T123", slack_user_id="U2")
        assert second.get_link_for_slack_user("U1", team_id="T123") is None
        assert second.get_link_for_user("account").slack_user_id == "U2"
        first.consume_code(issue(first, "other").code, team_id="T123", slack_user_id="U2")
        assert second.get_link_for_user("account") is None
        assert second.get_link_for_user("other").slack_user_id == "U2"
    finally:
        first.close()
        second.close()


def test_concurrent_code_consumption_has_one_winner(tmp_path):
    first, second = SlackUserLinkStore(tmp_path), SlackUserLinkStore(tmp_path)
    try:
        code = issue(first)
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(store.consume_code, code.code, team_id="T123", slack_user_id=user)
                       for store, user in [(first, "U1"), (second, "U2")]]
            results = [future.result() for future in futures]
        assert sum(result is not None for result in results) == 1
        assert first.get_link_for_user("account").slack_user_id in {"U1", "U2"}
    finally:
        first.close()
        second.close()


def test_failed_transaction_does_not_consume_code_or_replace_binding(tmp_path, monkeypatch):
    store = SlackUserLinkStore(tmp_path)
    try:
        store.consume_code(issue(store).code, team_id="T123", slack_user_id="U1")
        code = issue(store)
        original = store._state._backend._put_uncommitted
        def fail(*args):
            original(*args)
            raise OSError("injected SQL failure")
        with monkeypatch.context() as patch:
            patch.setattr(store._state._backend, "_put_uncommitted", fail)
            with pytest.raises(OSError):
                store.consume_code(code.code, team_id="T123", slack_user_id="U2")
        assert store.get_link_for_user("account").slack_user_id == "U1"
        assert store.consume_code(code.code, team_id="T123", slack_user_id="U2") is not None
        assert store.get_link_for_slack_user("U1", team_id="T123") is None
    finally:
        store.close()


def test_new_code_revokes_previous_and_only_digest_is_persisted(tmp_path):
    store = SlackUserLinkStore(tmp_path)
    try:
        old, current = issue(store), issue(store)
        assert store.consume_code(old.code, team_id="T123", slack_user_id="U1") is None
        assert current.code not in json.dumps(store._snapshot())
        assert len(store._snapshot()["codes"]) == 1
        assert store.consume_code(current.code.lower(), team_id="T123", slack_user_id="U1") is not None
    finally:
        store.close()


def test_lost_ack_preserves_complete_binding_on_reopen(tmp_path, monkeypatch):
    store = SlackUserLinkStore(tmp_path)
    code = issue(store)
    original = store._state.update
    def lose_ack(*args):
        original(*args)
        raise ConnectionError("lost ack")
    with monkeypatch.context() as patch:
        patch.setattr(store._state, "update", lose_ack)
        with pytest.raises(ConnectionError):
            store.consume_code(code.code, team_id="T123", slack_user_id="U1")
    store.close()
    reopened = SlackUserLinkStore(tmp_path)
    try:
        assert reopened.get_link_for_user("account").slack_user_id == "U1"
        assert reopened.consume_code(code.code, team_id="T123", slack_user_id="U2") is None
    finally:
        reopened.close()


def test_legacy_archive_resumes_after_commit_without_reimport(tmp_path, monkeypatch):
    path = tmp_path / "links" / "U1.json"
    path.parent.mkdir()
    payload = SlackUserLinkRecord("U1", "account", "account@example.com", "lab_member", datetime.now(UTC)).to_dict()
    raw = json.dumps(payload, indent=4).encode()
    path.write_bytes(raw)
    def fail(*args):
        raise OSError("archive interrupted")
    with monkeypatch.context() as patch:
        patch.setattr(SlackUserLinkStore, "_archive_legacy", fail)
        with pytest.raises(OSError):
            SlackUserLinkStore(tmp_path)
    assert path.read_bytes() == raw
    reopened = SlackUserLinkStore(tmp_path)
    try:
        assert reopened.get_link_for_user("account") is None
        assert reopened._snapshot()["unscoped_links"]["U1"]["user_id"] == "account"
        assert not path.exists()
        assert (tmp_path / "legacy-imported/links/U1.json").read_bytes() == raw
    finally:
        reopened.close()
