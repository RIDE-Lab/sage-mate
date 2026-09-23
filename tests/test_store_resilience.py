"""Resilience tests for per-record JSON store classes.

These tests reproduce the production failure mode that caused 500 errors on the
``/chat`` endpoint: the data directory disappeared at runtime (e.g. during a
layout migration) while the store object was still alive, and the next write
crashed with ``FileNotFoundError`` because the per-record write path did not
defensively recreate its parent directory.

Each test deliberately ``shutil.rmtree``s the on-disk store directory *after*
the store has been initialized, then exercises the public record-writing API
and asserts that:

  1. No exception is raised (the request would have returned HTTP 200).
  2. The target JSON file is materialized on disk.
  3. The directory is recreated.

Coverage spans all 11 modules that received defensive ``mkdir`` patches:
``analytics_store``, ``artifact_memory_draft_store``, ``escalation_store``,
``follow_up_store``, ``knowledge_base`` (3 methods), ``knowledge_gap_draft_store``,
``operations_store``, ``planner_comparison_store``, ``planner_metrics_store``,
``suggestion_store`` and ``user_store``.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from sage_faculty_twin.analytics_store import ConversationAnalyticsStore
from sage_faculty_twin.artifact_memory_draft_store import ArtifactMemoryDraftStore
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.escalation_store import EscalationQueueStore
from sage_faculty_twin.follow_up_store import FollowUpQueueStore
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.knowledge_gap_draft_store import KnowledgeGapDraftStore
from sage_faculty_twin.memory_store import NeuroMemConversationStore
from sage_faculty_twin.models import (
    AnonymousSuggestionCreate,
    ChatRequest,
    KnowledgeDocumentCreate,
    OperationsTaskStateUpdateRequest,
)
from sage_faculty_twin.operations_store import OperationsTaskStateStore
from sage_faculty_twin.planner_comparison_store import PlannerComparisonStore
from sage_faculty_twin.planner_metrics_store import PlannerMetricsStore
from sage_faculty_twin.suggestion_store import SuggestionBoardStore
from sage_faculty_twin.user_store import UserAccountStore


def _make_settings(tmp_path: Path) -> AppSettings:
    """Build an AppSettings rooted entirely under ``tmp_path``.

    Each store-specific directory is pinned to a unique path under ``tmp_path``
    so a per-test ``shutil.rmtree`` only affects the store under test.
    """

    return AppSettings(
        knowledge_base_dir=tmp_path / "knowledge",
        knowledge_backend="none",  # skip neuromem/sagevdb backend init in tests
        conversation_memory_dir=tmp_path / "conversation-memory",
        artifact_memory_draft_dir=tmp_path / "artifact-drafts",
        knowledge_gap_draft_dir=tmp_path / "knowledge-gap-drafts",
        escalation_queue_dir=tmp_path / "escalations",
        follow_up_queue_dir=tmp_path / "follow-up",
        operations_task_state_dir=tmp_path / "operations-state",
        suggestion_board_dir=tmp_path / "suggestions",
        user_account_store_dir=tmp_path / "users",
    )


# ---------------------------------------------------------------------------
# planner_metrics_store.py
# ---------------------------------------------------------------------------


def test_planner_metrics_store_record_entry_survives_runtime_dir_wipe(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    store = PlannerMetricsStore(settings)

    metrics_dir = settings.conversation_memory_dir / "planner-metrics"
    assert metrics_dir.exists(), "store __init__ should create its data dir"

    shutil.rmtree(settings.conversation_memory_dir)
    assert not metrics_dir.exists()

    store.record_entry(
        conversation_id="conv-resilience",
        planner_stage="deterministic",
        planner_mode="deterministic",
        question="Hello?",
        goal="answer",
        accepted=True,
        status="accepted",
        fallback_template=None,
        fallback_reason=None,
        validation_errors=[],
        planned_steps=["step_intro"],
        latency_ms=12.5,
    )

    assert metrics_dir.exists(), "store should have recreated its data dir"
    assert (metrics_dir / "planner_metrics.sqlite3").is_file()
    assert store.count_entries() == 1


# ---------------------------------------------------------------------------
# planner_comparison_store.py
# ---------------------------------------------------------------------------


def test_planner_comparison_store_record_comparison_survives_runtime_dir_wipe(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    store = PlannerComparisonStore(settings)

    comparisons_dir = settings.conversation_memory_dir / "planner-comparisons"
    assert comparisons_dir.exists()

    shutil.rmtree(settings.conversation_memory_dir)
    assert not comparisons_dir.exists()

    store.record_comparison(
        conversation_id="conv-resilience",
        exchange_id="exchange-1",
        workflow_action="answer",
        question="Hello?",
        comparison_status="match",
        deterministic_goal="answer",
        shadow_goal="answer",
        same_goal=True,
        same_fallback_template=True,
        deterministic_only_steps=[],
        shadow_only_steps=[],
        summary="identical plans",
    )

    assert comparisons_dir.exists()
    assert (comparisons_dir / "planner_comparisons.sqlite3").is_file()
    assert store.count_records() == 1


# ---------------------------------------------------------------------------
# analytics_store.py
# ---------------------------------------------------------------------------


def test_analytics_store_submit_feedback_survives_runtime_dir_wipe(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    conversation_store = NeuroMemConversationStore(settings)

    chat_request = ChatRequest(
        student_name="Alice",
        student_email="alice@example.com",
        course_context="科研指导",
        conversation_id="conv-feedback",
        question="弹性测试问题",
    )
    record = conversation_store.add_exchange(
        chat_request,
        conversation_id="conv-feedback",
        answer="ok",
        workflow_action="answer",
        interaction_domain="advising",
        knowledge_hit_count=0,
        booking_result=None,
    )

    analytics_store = ConversationAnalyticsStore(settings, conversation_store)
    feedback_dir = settings.conversation_memory_dir / "feedback"
    assert feedback_dir.exists()

    shutil.rmtree(feedback_dir)
    assert not feedback_dir.exists()

    feedback = analytics_store.submit_feedback(
        exchange_id=record.memory_id,
        rating="up",
        resolved=True,
        needs_human_followup=False,
        issue_summary=None,
    )

    written = feedback_dir / f"{record.memory_id}.json"
    assert feedback_dir.exists()
    assert written.is_file()
    assert feedback.exchange_id == record.memory_id
    assert analytics_store.count_feedback() == 1


# ---------------------------------------------------------------------------
# escalation_store.py
# ---------------------------------------------------------------------------


def test_escalation_store_create_request_survives_runtime_dir_wipe(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    store = EscalationQueueStore(settings)

    queue_dir = settings.escalation_queue_dir
    assert queue_dir.exists()

    shutil.rmtree(queue_dir)
    assert not queue_dir.exists()

    response = store.create_request(
        ChatRequest(
            student_name="Bob",
            student_email="bob@example.com",
            question="人工接管",
            conversation_id="conv-esc",
        ),
        conversation_id="conv-esc",
        route="human_handoff",
        reason="需要老师确认",
    )

    written = queue_dir / f"{response.escalation_id}.json"
    assert queue_dir.exists()
    assert written.is_file()
    assert store.count_records() == 1


def test_escalation_store_deduplicates_pending_retry_in_same_conversation(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    store = EscalationQueueStore(settings)
    request = ChatRequest(
        student_name="Bob",
        student_email="bob@example.com",
        question="我要申诉成绩，请联系老师。",
        conversation_id="conv-esc-retry",
    )

    first = store.create_request(
        request,
        conversation_id="conv-esc-retry",
        route="human_handoff",
        reason="需要老师确认",
    )
    retried = store.create_request(
        request,
        conversation_id="conv-esc-retry",
        route="human_handoff",
        reason="需要老师确认",
    )

    assert retried.escalation_id == first.escalation_id
    assert store.count_records() == 1


# ---------------------------------------------------------------------------
# user_store.py
# ---------------------------------------------------------------------------


def test_user_account_store_register_user_survives_runtime_dir_wipe(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    store = UserAccountStore(settings)

    user_dir = settings.user_account_store_dir
    assert user_dir.exists()

    shutil.rmtree(user_dir)
    assert not user_dir.exists()

    response = store.register_user(
        name="Carol",
        email="carol@example.com",
        visitor_profile="general_visitor",
        password="StrongPass!2026",
    )

    written = user_dir / f"{response.user_id}.json"
    assert user_dir.exists()
    assert written.is_file()
    assert store.count_users() == 1


# ---------------------------------------------------------------------------
# operations_store.py
# ---------------------------------------------------------------------------


def test_operations_task_state_store_update_state_survives_runtime_dir_wipe(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    store = OperationsTaskStateStore(settings)

    state_dir = settings.operations_task_state_dir
    assert state_dir.exists()

    shutil.rmtree(state_dir)
    assert not state_dir.exists()

    response = store.update_state(
        "weekly-review",
        OperationsTaskStateUpdateRequest(
            status="in_progress",
            assigned_to="ops-lead",
            note="弹性测试",
        ),
    )

    # Persisted file name is the sanitized task_key (alphanumeric/_.-).
    written = state_dir / "weekly-review.json"
    assert state_dir.exists()
    assert written.is_file()
    assert response.status == "in_progress"


# ---------------------------------------------------------------------------
# artifact_memory_draft_store.py
# ---------------------------------------------------------------------------


def test_artifact_memory_draft_store_create_draft_survives_runtime_dir_wipe(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    store = ArtifactMemoryDraftStore(settings)

    draft_dir = settings.artifact_memory_draft_dir
    assert draft_dir.exists()

    shutil.rmtree(draft_dir)
    assert not draft_dir.exists()

    record = store.create_draft(
        conversation_id="conv-art",
        source_memory_id="mem-1",
        student_name="Dan",
        student_email="dan@example.com",
        interaction_domain="research",
        question="如何引用这份资料？",
        answer="在论文方法节标注 [1]。",
        artifact_names=["paper.pdf"],
        artifact_sources=["upload"],
        artifact_excerpt_count=1,
        provenance_note="provenance",
    )

    written = draft_dir / f"{record.draft_id}.json"
    assert draft_dir.exists()
    assert written.is_file()
    assert store.count_drafts() == 1


# ---------------------------------------------------------------------------
# knowledge_gap_draft_store.py
# ---------------------------------------------------------------------------


def test_knowledge_gap_draft_store_upsert_survives_runtime_dir_wipe(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    store = KnowledgeGapDraftStore(settings)

    draft_dir = settings.knowledge_gap_draft_dir
    assert draft_dir.exists()

    shutil.rmtree(draft_dir)
    assert not draft_dir.exists()

    response = store.upsert_generated_draft(
        cluster_id="cluster-1",
        interaction_domain="research",
        label="研究类常见空缺",
        reason="近期出现重复点踩。",
        suggested_action="补充相关 FAQ 条目。",
        sample_questions=["怎么对比两种方法？"],
        title="FAQ草稿｜研究方法对比",
        content="# 草稿\n...",
        tags=["analytics-gap", "draft", "research"],
        source_name="analytics-gap:cluster-1",
    )

    written = draft_dir / f"{response.draft_id}.json"
    assert draft_dir.exists()
    assert written.is_file()
    assert store.count_drafts() == 1


# ---------------------------------------------------------------------------
# follow_up_store.py
# ---------------------------------------------------------------------------


def test_follow_up_queue_store_queue_action_survives_runtime_dir_wipe(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    store = FollowUpQueueStore(settings)

    queue_dir = settings.follow_up_queue_dir
    assert queue_dir.exists()

    shutil.rmtree(queue_dir)
    assert not queue_dir.exists()

    response = store.queue_action(
        booking_id="booking-1",
        student_name="Eve",
        student_email="eve@example.com",
        action_type="reminder",
        title="会前提醒",
        detail="带上数据样例。",
        subject="会议提醒",
        lines=["您好,", "请准时参加。"],
        due_at=None,
    )

    written = queue_dir / f"{response.action_id}.json"
    assert queue_dir.exists()
    assert written.is_file()
    assert store.count_actions() == 1


# ---------------------------------------------------------------------------
# suggestion_store.py
# ---------------------------------------------------------------------------


def test_suggestion_board_store_create_suggestion_survives_runtime_dir_wipe(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    store = SuggestionBoardStore(settings)

    suggestion_dir = settings.suggestion_board_dir
    assert suggestion_dir.exists()

    shutil.rmtree(suggestion_dir)
    assert not suggestion_dir.exists()

    response = store.create_suggestion(
        AnonymousSuggestionCreate(message="希望增加更多教学样例。", category="teaching")
    )

    written = suggestion_dir / f"{response.suggestion_id}.json"
    assert suggestion_dir.exists()
    assert written.is_file()
    assert store.count_suggestions() == 1


# ---------------------------------------------------------------------------
# knowledge_base.py — add_document / upsert_document / update_document
# ---------------------------------------------------------------------------


def test_knowledge_store_add_document_survives_runtime_dir_wipe(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    store = LocalKnowledgeStore(settings)

    base_dir = settings.knowledge_base_dir
    assert base_dir.exists()

    shutil.rmtree(base_dir)
    assert not base_dir.exists()

    record = store.add_document(
        KnowledgeDocumentCreate(
            title="测试文档",
            content="弹性测试内容。",
            tags=["resilience"],
            source_name="resilience-add",
        ),
        rebuild_indexes=False,
    )

    written = base_dir / f"{record.document_id}.json"
    assert base_dir.exists()
    assert written.is_file()


def test_knowledge_store_upsert_document_survives_runtime_dir_wipe(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    store = LocalKnowledgeStore(settings)

    # Seed an existing document so upsert_document hits the *update* branch
    # (the *insert* branch delegates to add_document and would not exercise
    # the second defensive mkdir site).
    seeded = store.add_document(
        KnowledgeDocumentCreate(
            title="原始标题",
            content="原始内容。",
            tags=["resilience"],
            source_name="resilience-upsert",
        ),
        rebuild_indexes=False,
    )

    base_dir = settings.knowledge_base_dir
    shutil.rmtree(base_dir)
    assert not base_dir.exists()

    record, inserted = store.upsert_document(
        KnowledgeDocumentCreate(
            title="更新标题",
            content="更新后的内容。",
            tags=["resilience"],
            source_name="resilience-upsert",
        ),
        rebuild_indexes=False,
    )

    assert inserted is False
    assert record.document_id == seeded.document_id
    written = base_dir / f"{record.document_id}.json"
    assert base_dir.exists()
    assert written.is_file()


def test_knowledge_store_update_document_survives_runtime_dir_wipe(
    tmp_path: Path,
) -> None:
    settings = _make_settings(tmp_path)
    store = LocalKnowledgeStore(settings)

    seeded = store.add_document(
        KnowledgeDocumentCreate(
            title="原始标题",
            content="原始内容。",
            tags=["resilience"],
            source_name="resilience-update",
        ),
        rebuild_indexes=False,
    )

    base_dir = settings.knowledge_base_dir
    shutil.rmtree(base_dir)
    assert not base_dir.exists()

    record = store.update_document(
        seeded.document_id,
        KnowledgeDocumentCreate(
            title="新标题",
            content="新内容。",
            tags=["resilience"],
            source_name="resilience-update",
        ),
        rebuild_indexes=False,
    )

    assert record.document_id == seeded.document_id
    written = base_dir / f"{record.document_id}.json"
    assert base_dir.exists()
    assert written.is_file()
