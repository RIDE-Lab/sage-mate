"""Exercise real publication and service paths, not copied arithmetic."""
import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from threading import RLock
from types import SimpleNamespace

import pytest

from sage_faculty_twin.memory_store import (
    ConversationDigestRecord, ConversationDigestStore, ConversationMemoryRecord,
    NeuroMemConversationStore,
)
from sage_faculty_twin.service import FacultyTwinWorkflowSupport


def record(index, *, conversation="conversation", answer=None):
    return ConversationMemoryRecord(
        memory_id=f"turn-{index}", conversation_id=conversation,
        student_name="Test", student_email=None, course_context=None,
        question=f"question-{index}", answer=answer or f"answer-{index}",
        workflow_action="answer", interaction_domain=None, knowledge_hit_count=0,
        booking_summary=None, created_at=datetime(2026, 1, 1, tzinfo=UTC) + timedelta(seconds=index),
    )


@pytest.fixture
def support(tmp_path):
    obj = FacultyTwinWorkflowSupport.__new__(FacultyTwinWorkflowSupport)
    obj._settings = SimpleNamespace(context_digest_enabled=True, context_digest_turn_threshold=4,
                                    context_digest_max_chars=200, context_digest_llm_enabled=False)
    obj._digest_store = ConversationDigestStore(tmp_path / "digests")
    memory = NeuroMemConversationStore.__new__(NeuroMemConversationStore)
    memory._records = {}
    memory._durable_memory = None
    memory._memory_write_lock = RLock()
    obj._conversation_store = memory
    yield obj
    obj._digest_store.close()


def add(support, *records):
    support._conversation_store._records.update({item.memory_id: item for item in records})


def refresh(support, *, threshold=1):
    return support._refresh_conversation_digest("conversation", threshold=threshold)


def test_record_round_trip():
    original = ConversationDigestRecord("one", "text", 3, datetime.now(UTC), revision=2)
    assert ConversationDigestRecord.from_dict(original.to_dict()) == original


def test_digest_sources_plans_and_restart_are_owner_scoped(support, tmp_path):
    owners = ("account:a", "account:b", None)
    for index, owner in enumerate(owners):
        source = replace(record(index, answer=f"private-{index}"), owner_id=owner)
        add(support, source)
        result = support._refresh_conversation_digest("conversation", threshold=1, owner_id=owner)
        assert result["turns_incorporated"] == 1
    reopened = ConversationDigestStore(tmp_path / "digests")
    try:
        for index, owner in enumerate(owners):
            saved = reopened.get_digest("conversation", owner_id=owner)
            assert saved.turns_summarized == 1
            assert f"private-{index}" in saved.digest_text
            assert all(f"private-{other}" not in saved.digest_text for other in range(3) if other != index)
        source = replace(record(4), owner_id="account:a")
        with pytest.raises(ValueError, match="digest_owner_mismatch"):
            reopened.prepare("conversation", [source], owner_id="account:b")
        plan = reopened.prepare("conversation", [replace(record(0, answer="private-0"), owner_id="account:a"), source], owner_id="account:a")
        with pytest.raises(ValueError):
            reopened.commit("conversation", plan, "wrong owner publication", owner_id="account:b")
        assert reopened.get_digest("conversation", owner_id="account:b").turns_summarized == 1
    finally:
        reopened.close()


def test_refresh_threshold_continues_after_64_and_preserves_earliest_answer(support):
    for index in range(100):
        add(support, record(index))
        refresh(support, threshold=4)
        saved = support._digest_store.get_digest("conversation")
        expected = ((index + 1) // 4) * 4
        assert (saved.turns_summarized if saved else 0) == expected
    saved = support._digest_store.get_digest("conversation")
    payloads = [json.loads(line) for line in saved.digest_text.splitlines()]
    assert [item["memory_id"] for item in payloads] == [f"turn-{i}" for i in range(100)]
    assert payloads[0]["answer"] == "answer-0"
    assert saved.revision == 25


def test_initial_backlog_manual_and_repeat_do_not_omit_or_duplicate(support):
    add(support, *(record(i) for i in range(100)))
    result = refresh(support)
    assert result["ok"] and result["total_turns"] == result["turns_incorporated"] == 100
    assert result["turns_compressed"] == 0
    assert result["mode"] == "lossless"
    assert refresh(support)["turns_compressed"] == 0
    assert support._digest_store.get_digest("conversation").revision == 1


def test_no_truncation_or_markdown_flattening(support):
    answer = "# Heading\n\n- parent\n  - child\n" * 300
    add(support, record(0, answer=answer))
    refresh(support)
    add(support, record(1, answer="new decision"))
    refresh(support)
    saved = support._digest_store.get_digest("conversation")
    payloads = [json.loads(line) for line in saved.digest_text.splitlines()]
    assert payloads[0]["answer"] == answer
    assert payloads[1]["answer"] == "new decision"
    assert len(saved.digest_text) > support._settings.context_digest_max_chars


def test_legacy_count_cannot_claim_coverage_or_poison_rebuild(support, tmp_path):
    path = tmp_path / "digests" / "conversation.json"
    legacy = '{"conversation_id":"conversation","turns_summarized":999,"digest_text":"lossy"}'
    path.write_text(legacy)
    support._digest_store.close()
    support._digest_store = ConversationDigestStore(path.parent)
    assert support._digest_store.get_digest("conversation") is None
    add(support, record(1))
    refresh(support)
    assert path.read_text() == legacy
    assert support._digest_store.get_digest("conversation").turns_summarized == 1


def test_reopen_and_late_source_are_identity_based(support, tmp_path):
    add(support, record(2))
    refresh(support)
    support._digest_store.close()
    support._digest_store = ConversationDigestStore(tmp_path / "digests")
    add(support, record(1))
    assert refresh(support)["turns_incorporated"] == 1
    assert support._digest_store.get_digest("conversation").turns_summarized == 2


def test_stale_generation_does_not_overwrite(support):
    add(support, record(1))
    def competing_writer(**kwargs):
        store = support._digest_store
        plan = store.prepare("conversation", [record(1)])
        store.commit("conversation", plan, "winner")
        return "stale result"
    support._summarize_digest_turns = competing_writer
    result = refresh(support)
    assert not result["ok"] and "retry" in result["error"]
    assert support._digest_store.get_digest("conversation").digest_text == "winner"


def test_full_llm_input_previous_output_and_no_posthoc_clipping(support):
    support._settings.context_digest_llm_enabled = True
    answer = "full evidence " * 500
    add(support, record(1, answer=answer))
    prompts = []
    def generate(system, user, **kwargs):
        prompts.append((user, kwargs))
        return "complete summary " * 100
    support._llm_client = SimpleNamespace(answer_question_sync=generate)
    refresh(support)
    add(support, record(2))
    refresh(support)
    assert answer in prompts[0][0]
    assert ("complete summary " * 100).strip() in prompts[1][0]
    assert prompts[0][1]["max_tokens"] is None
    assert support._digest_store.get_digest("conversation").digest_text == ("complete summary " * 100).strip()


def test_model_failure_does_not_advance_coverage(support):
    support._settings.context_digest_llm_enabled = True
    add(support, record(1))
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure")
    support._llm_client = SimpleNamespace(answer_question_sync=fail)
    assert not refresh(support)["ok"]
    assert support._digest_store.get_digest("conversation") is None


def test_snapshot_detached_chronological_and_conversation_scoped(support):
    add(support, record(2), record(0), record(1, conversation="other"))
    snapshot = support._conversation_store.list_conversation_records("conversation")
    assert [item.memory_id for item in snapshot] == ["turn-0", "turn-2"]
    snapshot[0].answer = "mutation"
    assert support._conversation_store._records["turn-0"].answer == "answer-0"
    with pytest.raises(ValueError, match="conversation_mismatch"):
        support._digest_store.prepare("conversation", [record(3, conversation="other")])
