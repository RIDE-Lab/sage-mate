from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage.runtime.work_journal import WorkJournal

from sage_faculty_twin.analytics_store import ConversationAnalyticsStore, ConversationFeedbackRecord


@pytest.fixture
def stores(tmp_path):
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.sqlite3"))
    settings = SimpleNamespace(conversation_memory_dir=tmp_path)
    memory = SimpleNamespace(state_store=state, get_canonical_record=lambda key: object())
    yield settings, memory, ConversationAnalyticsStore(settings, memory)
    state.close()


def submit(store, key="turn", rating="up"):
    return store.submit_feedback(exchange_id=key, rating=rating, resolved=None,
                                 needs_human_followup=False, issue_summary=None)


def test_rollback_does_not_publish_cache(stores, monkeypatch):
    settings, memory, store = stores
    original = submit(store)
    update = memory.state_store.update

    def fail(slot, transform, **kwargs):
        def mutate_then_fail(value):
            transform(value)
            raise RuntimeError("before commit")
        return update(slot, mutate_then_fail, **kwargs)

    monkeypatch.setattr(memory.state_store, "update", fail)
    with pytest.raises(RuntimeError, match="before commit"):
        submit(store, rating="down")
    assert store.get_feedback("turn") == original
    assert ConversationAnalyticsStore(settings, memory).get_feedback("turn") == original


def test_ack_loss_reopen_and_concurrent_writers(stores, monkeypatch):
    settings, memory, store = stores
    update = memory.state_store.update

    def lost_ack(*args, **kwargs):
        update(*args, **kwargs)
        raise RuntimeError("ack lost")

    with monkeypatch.context() as patch:
        patch.setattr(memory.state_store, "update", lost_ack)
        with pytest.raises(RuntimeError, match="ack lost"):
            submit(store)
    other_state = StatefulProcessRuntime(
        backend="sqlite", sqlite_path=str(settings.conversation_memory_dir / "state.sqlite3"))
    try:
        other = ConversationAnalyticsStore(settings, SimpleNamespace(
            state_store=other_state, get_canonical_record=lambda key: object()))
        original = other.get_feedback("turn")
        assert original.rating == "up"
        with ThreadPoolExecutor(max_workers=4) as pool:
            list(pool.map(lambda i: submit(store if i % 2 else other, str(i)), range(40)))
        assert store.count_feedback() == other.count_feedback() == 41
        revised = submit(other, rating="down")
        assert revised.created_at == original.created_at
        assert store.get_feedback("turn") == revised
        revised.rating = "mutated externally"
        assert other.get_feedback("turn").rating == "down"
    finally:
        other_state.close()


def test_legacy_import_receipt_survives_archive_failure(stores, monkeypatch):
    settings, memory, store = stores
    now = datetime.now(UTC)
    record = ConversationFeedbackRecord("legacy", "up", True, False, None, now, now)
    path = settings.conversation_memory_dir / "feedback" / "legacy.json"
    raw = json.dumps(record.to_dict())
    path.write_text(raw)
    with monkeypatch.context() as patch:
        def fail(*args):
            raise OSError("archive failed")
        patch.setattr(Path, "replace", fail)
        with pytest.raises(OSError, match="archive failed"):
            ConversationAnalyticsStore(settings, memory)
    assert store.get_feedback("legacy") == record
    updated = submit(store, "legacy", "down")
    reopened = ConversationAnalyticsStore(settings, memory)
    assert reopened.get_feedback("legacy") == updated
    assert not path.exists()
    assert (path.parent / "legacy-imported" / path.name).read_text() == raw
    path.write_text(raw.replace('"up"', '"down"'))
    with pytest.raises(ValueError, match="feedback_legacy_source_changed"):
        ConversationAnalyticsStore(settings, memory)
    assert store.get_feedback("legacy") == updated


def test_unknown_source_never_creates_feedback(stores):
    _, memory, store = stores
    memory.get_canonical_record = lambda key: None
    with pytest.raises(KeyError):
        submit(store)
    assert store.count_feedback() == 0


def admit(store, journal, operation="feedback-one", rating="up"):
    return store.submit_feedback(
        exchange_id="turn", rating=rating, resolved=None,
        needs_human_followup=False, issue_summary=None, operation_id=operation,
        commit_work=lambda mutations: journal.commit(
            operation, result={"accepted": True},
            tasks={"knowledge": {"sources": [{"title": "source-only"}]}}, mutations=mutations))


def test_feedback_and_work_rollback_as_one_transaction(stores, monkeypatch):
    _, memory, store = stores
    journal = WorkJournal(memory.state_store, namespace="test-feedback-work")
    original = memory.state_store.update_many
    def fail(slots, transform, **kwargs):
        def transformed_then_fail(values):
            transform(values)
            raise OSError("before transaction commit")
        return original(slots, transformed_then_fail, **kwargs)
    with monkeypatch.context() as patch:
        patch.setattr(memory.state_store, "update_many", fail)
        with pytest.raises(OSError, match="before transaction commit"):
            admit(store, journal)
    assert store.get_feedback("turn") is None
    assert journal.read("feedback-one") is None
    saved = admit(store, journal)
    assert saved.rating == "up"
    assert journal.read("feedback-one")["tasks"]["knowledge"]["status"] == "pending"


def test_lost_ack_replay_returns_original_without_reverting_later_feedback(stores, monkeypatch):
    settings, memory, store = stores
    journal = WorkJournal(memory.state_store, namespace="test-feedback-work")
    original = memory.state_store.update_many
    def lost_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise OSError("acknowledgement lost")
    with monkeypatch.context() as patch:
        patch.setattr(memory.state_store, "update_many", lost_ack)
        with pytest.raises(OSError, match="acknowledgement lost"):
            admit(store, journal)
    first = store.get_feedback("turn")
    latest = admit(store, journal, operation="feedback-two", rating="down")
    reopened = ConversationAnalyticsStore(settings, memory)
    replayed = admit(reopened, journal)
    assert replayed == first
    assert reopened.get_feedback("turn") == latest
    assert replayed.created_at == latest.created_at
    with pytest.raises(ValueError, match="feedback_operation_conflict"):
        admit(reopened, journal, rating="down")
    assert reopened.get_feedback("turn") == latest


def test_journal_conflict_rolls_back_feedback_mutation(stores):
    _, memory, store = stores
    journal = WorkJournal(memory.state_store, namespace="test-feedback-work")
    journal.commit("feedback-one", result={"different": True}, tasks={})
    with pytest.raises(ValueError, match="journal_commit_conflict"):
        admit(store, journal)
    assert store.get_feedback("turn") is None
