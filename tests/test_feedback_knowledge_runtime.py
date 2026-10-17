from threading import Event
from types import SimpleNamespace

import pytest
from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime

from sage_faculty_twin.analytics_store import ConversationAnalyticsStore
from sage_faculty_twin.feedback_knowledge_runtime import FeedbackKnowledgeRuntime
from sage_faculty_twin.models import KnowledgeDocumentCreate


@pytest.fixture
def stores(tmp_path):
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.sqlite3"))
    memory = SimpleNamespace(state_store=state, get_canonical_record=lambda key: object())
    analytics = ConversationAnalyticsStore(SimpleNamespace(conversation_memory_dir=tmp_path), memory)
    yield state, analytics
    state.close()


def candidate():
    return KnowledgeDocumentCreate(title="Public source", content="Source text",
                                   source_name="feedback-web:snapshot-v1:test")


def submit(runtime, **overrides):
    values = dict(operation_id="op", candidates=[candidate()], exchange_id="turn",
                  rating="up", resolved=True, needs_human_followup=False, issue_summary=None)
    values.update(overrides)
    return runtime.submit(**values)


class Destination:
    def __init__(self):
        self.documents = {}
        self.calls = 0

    def upsert_document(self, value, *, preserve_existing):
        assert preserve_existing is True
        self.calls += 1
        created = value.source_name not in self.documents
        if created:
            self.documents[value.source_name] = SimpleNamespace(
                document_id="doc", title=value.title, source_name=value.source_name)
        return self.documents[value.source_name], created


def test_atomic_admission_replay_and_detached_source(stores):
    _, analytics = stores
    destination = Destination()
    runtime = FeedbackKnowledgeRuntime(analytics=analytics, knowledge=destination)
    try:
        first = submit(runtime)
        result = runtime.wait("op", timeout=3)
        assert result[0].created is True
        assert submit(runtime) == first
        assert runtime.wait("op", timeout=3) == result
        assert destination.calls == 1
        payload = next(iter(runtime.journal.read("op")["tasks"].values()))["payload"]
        assert payload == candidate().model_dump(mode="json")
        assert "exchange_id" not in payload
        assert analytics.get_feedback("turn") == first
    finally:
        runtime.close()


def test_failed_atomic_commit_has_no_effect(stores, monkeypatch):
    state, analytics = stores
    destination = Destination()
    runtime = FeedbackKnowledgeRuntime(analytics=analytics, knowledge=destination)
    original = state.update_many

    def fail(slots, transform, **kwargs):
        def abort(values):
            transform(values)
            raise RuntimeError("rollback")
        return original(slots, abort, **kwargs)

    try:
        with monkeypatch.context() as patch:
            patch.setattr(state, "update_many", fail)
            with pytest.raises(RuntimeError, match="rollback"):
                submit(runtime)
        assert runtime.journal.read("op") is None
        assert analytics.get_feedback("turn") is None
        assert destination.calls == 0
    finally:
        runtime.close()


def test_committed_intent_recovers_without_producer(stores, monkeypatch, tmp_path):
    _, analytics = stores
    destination = Destination()
    runtime = FeedbackKnowledgeRuntime(analytics=analytics, knowledge=destination)
    with monkeypatch.context() as patch:
        patch.setattr(runtime._service, "start", lambda: None)
        submit(runtime)
    runtime.close()
    assert destination.calls == 0
    # Existing curated destination survives an admission-acknowledgement loss.
    destination.upsert_document(candidate(), preserve_existing=True)
    destination.documents[candidate().source_name].title = "Curator revision"
    reopened = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.sqlite3"))
    recovered_analytics = ConversationAnalyticsStore(
        SimpleNamespace(conversation_memory_dir=tmp_path),
        SimpleNamespace(state_store=reopened, get_canonical_record=lambda key: object()))
    recovered = FeedbackKnowledgeRuntime(analytics=recovered_analytics, knowledge=destination)
    try:
        recovered.start()
        result = recovered.wait("op", timeout=3)
        assert result[0].title == "Curator revision"
        assert result[0].created is False
    finally:
        recovered.close()
        reopened.close()


def test_timeout_does_not_cancel_and_drain_keeps_state_alive(stores):
    state, analytics = stores
    entered, release = Event(), Event()
    destination = Destination()

    def write(value, **kwargs):
        entered.set()
        assert release.wait(5)
        return destination.upsert_document(value, **kwargs)

    runtime = FeedbackKnowledgeRuntime(analytics=analytics, knowledge=SimpleNamespace(upsert_document=write))
    try:
        submit(runtime)
        assert entered.wait(3)
        with pytest.raises(TimeoutError):
            runtime.wait("op", timeout=0)
        with pytest.raises(TimeoutError):
            runtime.close(timeout=0)
        assert analytics.state_store is state
        assert analytics.get_feedback("turn").rating == "up"
        release.set()
        runtime.close()
        assert runtime.wait("op", timeout=0)[0].document_id == "doc"
    finally:
        release.set()
        runtime.close()


def test_empty_selection_and_conflicting_source_replay(stores):
    _, analytics = stores
    destination = Destination()
    runtime = FeedbackKnowledgeRuntime(analytics=analytics, knowledge=destination)
    try:
        submit(runtime, candidates=[], rating="down")
        assert runtime.wait("op", timeout=3) == []
        with pytest.raises(ValueError):
            submit(runtime, rating="down")
        assert analytics.get_feedback("turn").rating == "down"
        assert destination.calls == 0
    finally:
        runtime.close()
