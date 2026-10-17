from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
import json

import pytest

from sage_faculty_twin.artifact_memory_draft_store import ArtifactMemoryDraftRecord, ArtifactMemoryDraftStore
from sage_faculty_twin.config import AppSettings


def settings_for(tmp_path):
    return AppSettings(conversation_memory_dir=tmp_path / "memory",
                       artifact_memory_draft_dir=tmp_path / "drafts")


def inputs():
    return dict(conversation_id="conversation", source_memory_id="source-turn",
                student_name="Alice", student_email="alice@example.com",
                interaction_domain="research", question="archive evidence", answer="complete answer",
                artifact_names=["paper.md"], artifact_sources=["upload"],
                artifact_excerpt_count=1, provenance_note="original source")


def test_replay_and_independent_reader_preserve_reviewed_state(tmp_path):
    settings = settings_for(tmp_path)
    first, other = ArtifactMemoryDraftStore(settings), ArtifactMemoryDraftStore(settings)
    record = first.create_draft(**inputs())
    accepted = other.mark_accepted(record.draft_id)
    assert first.create_draft(**inputs()).to_dict() == accepted.to_dict()
    assert first.get_draft(record.draft_id).status == "accepted"
    assert first.count_drafts() == 1
    with pytest.raises(ValueError, match="identity_conflict"):
        first.create_draft(**{**inputs(), "answer": "changed answer"})
    with pytest.raises(ValueError, match="accepted"):
        first.mark_rejected(record.draft_id)
    reopened = ArtifactMemoryDraftStore(settings)
    assert reopened.list_drafts()[0].to_dict() == accepted.to_dict()
    detached = first.get_draft(record.draft_id)
    detached.artifact_names.append("uncommitted")
    assert reopened.get_draft(record.draft_id).artifact_names == ["paper.md"]


def test_concurrent_same_source_creates_only_one_record(tmp_path):
    settings = settings_for(tmp_path)
    stores = [ArtifactMemoryDraftStore(settings) for _ in range(2)]
    with ThreadPoolExecutor(max_workers=2) as pool:
        records = list(pool.map(lambda index: stores[index % 2].create_draft(**inputs()), range(12)))
    assert len({record.draft_id for record in records}) == 1
    assert stores[0].count_drafts() == stores[1].count_drafts() == 1


def test_write_failure_never_publishes_creation_or_review(tmp_path, monkeypatch):
    store = ArtifactMemoryDraftStore(settings_for(tmp_path))
    backend = store._state._backend
    original = backend._put_uncommitted

    def fail(*args):
        original(*args)
        raise OSError("before commit")

    with monkeypatch.context() as patch:
        patch.setattr(backend, "_put_uncommitted", fail)
        with pytest.raises(OSError, match="before commit"):
            store.create_draft(**inputs())
    assert store.count_drafts() == 0
    record = store.create_draft(**inputs())
    with monkeypatch.context() as patch:
        patch.setattr(backend, "_put_uncommitted", fail)
        with pytest.raises(OSError, match="before commit"):
            store.mark_accepted(record.draft_id)
    assert store.get_draft(record.draft_id).to_dict() == record.to_dict()


def test_lost_commit_ack_reopens_without_duplicate(tmp_path, monkeypatch):
    settings = settings_for(tmp_path)
    store = ArtifactMemoryDraftStore(settings)
    original = store._state.update

    def lose_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError("ack lost")

    monkeypatch.setattr(store._state, "update", lose_ack)
    with pytest.raises(ConnectionError, match="ack lost"):
        store.create_draft(**inputs())
    reopened = ArtifactMemoryDraftStore(settings)
    record = reopened.create_draft(**inputs())
    assert reopened.count_drafts() == 1
    assert reopened.get_draft(record.draft_id).to_dict() == record.to_dict()


def test_legacy_migration_preserves_id_and_review_and_archives_source(tmp_path):
    settings = settings_for(tmp_path)
    settings.artifact_memory_draft_dir.mkdir(parents=True)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    record = ArtifactMemoryDraftRecord(**inputs(), draft_id="legacy-id",
        retention_label="project_followup", status="draft", created_at=now, updated_at=now)
    source = settings.artifact_memory_draft_dir / "legacy-id.json"
    raw = json.dumps(record.to_dict(), ensure_ascii=False)
    source.write_text(raw)
    store = ArtifactMemoryDraftStore(settings)
    assert store.create_draft(**inputs()).to_dict() == record.to_dict()
    assert not source.exists()
    assert (source.parent / "legacy-imported" / source.name).read_text() == raw
    accepted = store.mark_accepted(record.draft_id)
    # An interrupted archive can leave the old file; its import receipt must
    # keep replay from resetting a later review state or adding a duplicate.
    source.write_text(raw)
    reopened = ArtifactMemoryDraftStore(settings)
    assert reopened.count_drafts() == 1
    assert reopened.get_draft("legacy-id").to_dict() == accepted.to_dict()


def test_conflicting_legacy_sources_abort_whole_migration(tmp_path):
    from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime

    settings = settings_for(tmp_path)
    settings.artifact_memory_draft_dir.mkdir(parents=True)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    for draft_id in ("one", "two"):
        record = ArtifactMemoryDraftRecord(**inputs(), draft_id=draft_id,
            retention_label="project_followup", status="draft", created_at=now, updated_at=now)
        (settings.artifact_memory_draft_dir / f"{draft_id}.json").write_text(json.dumps(record.to_dict()))
    with pytest.raises(ValueError, match="duplicate_legacy_artifact_source"):
        ArtifactMemoryDraftStore(settings)
    assert len(list(settings.artifact_memory_draft_dir.glob("*.json"))) == 2
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(settings.conversation_memory_dir / "neural_state.sqlite3"))
    try:
        assert state.peek(("twin-artifact-drafts-v1", "artifact-drafts", "records")) is None
    finally:
        state.close()


def test_concurrent_opposing_reviews_have_one_winner(tmp_path):
    settings = settings_for(tmp_path)
    left, right = ArtifactMemoryDraftStore(settings), ArtifactMemoryDraftStore(settings)
    record = left.create_draft(**inputs())

    def review(method):
        try:
            return method(record.draft_id).status
        except ValueError:
            return "conflict"

    with ThreadPoolExecutor(max_workers=2) as pool:
        outcomes = list(pool.map(review, [left.mark_accepted, right.mark_rejected]))
    assert outcomes.count("conflict") == 1
    assert left.get_draft(record.draft_id).status == right.get_draft(record.draft_id).status
