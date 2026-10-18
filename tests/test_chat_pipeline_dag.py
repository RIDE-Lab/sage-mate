"""Trace-contract regression suite for the chat pipeline.

This file pins down the exact `workflow_trace` key sequence produced by
`DigitalTwinService.answer` for representative chat scenarios, plus a smoke
check that the non-chat (admin/knowledge) pipeline still runs through the
legacy linear path. The Faculty Twin DAG Pipeline rewrite must keep these
sequences byte-identical: that is the contract by which Tasks 2-5 are
evaluated.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import (
    ChatRequest,
    InteractionIntent,
    KnowledgeDocumentCreate,
    WorkflowTraceStep,
)
from sage_faculty_twin.service import (
    _CANONICAL_TRACE_ORDER,
    ChatContextMergeFunction,
    DigitalTwinService,
    _canonicalize_workflow_trace,
    _ChatContextMerge2,
    _ChatContextMerge4,
)

# ----------------------------- LLM stand-ins ---------------------------------


class _AdviseOnlyLLMClient:
    """Forces the `answer` action so we exercise the non-booking chat path."""
    model_name = "test-model"

    def __init__(self, answer_text: str) -> None:
        self._answer = answer_text

    def classify_interaction_intent_sync(
        self,
        question: str,
        course_context: str | None = None,
        recent_session_context: str | None = None,
    ) -> InteractionIntent:
        return InteractionIntent(
            action="answer",
            domain="advising",
            retrieval_scopes=["preparation", "meeting_policy", "profile"],
            exclude_scopes=["courseware"],
            decision_mode="advise_only",
            confidence=0.95,
        )

    async def classify_interaction_intent(
        self,
        question: str,
        course_context: str | None = None,
        recent_session_context: str | None = None,
    ) -> InteractionIntent:
        return self.classify_interaction_intent_sync(question, course_context)

    def classify_booking_intent_sync(
        self, question: str, course_context: str | None = None
    ) -> bool:
        return False

    async def classify_booking_intent(
        self, question: str, course_context: str | None = None
    ) -> bool:
        return False

    def answer_question_sync(self, system_prompt: str, user_prompt: str) -> str:
        return self._answer

    async def answer_question(self, system_prompt: str, user_prompt: str) -> str:
        return self._answer


class _BookingLLMClient:
    """Triggers the booking flow so the trace exercises the booking branch."""
    model_name = "test-model"

    def classify_interaction_intent_sync(
        self,
        question: str,
        course_context: str | None = None,
        recent_session_context: str | None = None,
    ) -> InteractionIntent:
        return InteractionIntent(
            action="book_meeting",
            domain="booking",
            retrieval_scopes=["meeting_policy"],
            exclude_scopes=["courseware", "publications"],
            decision_mode="review_queue",
            confidence=0.95,
        )

    async def classify_interaction_intent(
        self,
        question: str,
        course_context: str | None = None,
        recent_session_context: str | None = None,
    ) -> InteractionIntent:
        return self.classify_interaction_intent_sync(question, course_context)

    def classify_booking_intent_sync(
        self, question: str, course_context: str | None = None
    ) -> bool:
        return True

    async def classify_booking_intent(
        self, question: str, course_context: str | None = None
    ) -> bool:
        return True

    def answer_question_sync(self, system_prompt: str, user_prompt: str) -> str:
        raise AssertionError("booking workflow should not call the LLM")

    async def answer_question(self, system_prompt: str, user_prompt: str) -> str:
        raise AssertionError("booking workflow should not call the LLM")


# ----------------------------- Canonical trace -------------------------------

# Trace keys, in the exact order today's linear pipeline emits them. The DAG
# rewrite (Task 4) must reproduce this sequence verbatim. Note that
# `artifact_memory_writeback` is conditional and only appears when the request
# carries attachments, so it is intentionally absent from these baselines.
CANONICAL_CHAT_TRACE = [
    "bootstrap",
    "workflow_plan_preview",
    "interaction_understand",
    "booking_prepare",
    "booking_execute",
    "memory_retrieve",
    "knowledge_retrieve",
    "prompt_build",
    "llm_answer",
    "memory_persist",
    "memory_profile_consolidate",
    "follow_up_plan",
    "memory_usefulness_score",
    "context_digest",
    "response_render",
]


# ---------------------------------- Tests ------------------------------------


def _trace_keys(response) -> list[str]:
    return [step.key for step in response.workflow_trace]


def _trace_statuses(response) -> dict[str, str]:
    return {step.key: step.status for step in response.workflow_trace}


def test_default_chat_executes_sage_graph_without_alternate_executor(tmp_path, monkeypatch):
    from sage.runtime import FlowNetEnvironment

    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))
    service._llm_client = _AdviseOnlyLLMClient("建议先复现 baseline，再围绕评估指标做对比实验。")
    def forbidden_submit(*args, **kwargs):
        raise AssertionError("chat rebuilt the obsolete per-request environment")

    monkeypatch.setattr(FlowNetEnvironment, "submit", forbidden_submit)
    response = asyncio.run(service.answer(ChatRequest(
        student_name="Alice",
        question="我想做一个图神经网络相关的研究，下一步该怎么推进？",
        conversation_id="single-sage-path",
    )))

    assert response.answer
    runtime = service._get_chat_runtime()
    endpoint_id = runtime._endpoint.flow_instance.instance_id
    assert runtime._endpoint._requests == {}
    second = asyncio.run(service.answer(ChatRequest(
        student_name="Bob", question="你好", conversation_id="second-sage-request",
    )))
    assert second.exchange_id and second.exchange_id != response.exchange_id
    assert service._get_chat_runtime()._endpoint.flow_instance.instance_id == endpoint_id
    assert not hasattr(service, "answer_in_process")
    assert not hasattr(service, "_run_chat_in_process")
    assert "chat_runtime_pipeline_enabled" not in AppSettings.model_fields


def test_sage_stage_failure_is_not_retried_through_another_executor(tmp_path, monkeypatch):
    from sage.runtime.flownet import FlowRequestOutcomeError
    from sage_faculty_twin.service import LlmAnswerStage

    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))
    service._llm_client = _AdviseOnlyLLMClient("unused")
    executions = []

    def fail(stage, context):
        executions.append(context.conversation_id)
        raise OSError("stage unavailable")

    monkeypatch.setattr(LlmAnswerStage, "execute", fail)
    with pytest.raises(FlowRequestOutcomeError, match="error_type=OSError.*stage unavailable"):
        asyncio.run(service.answer(ChatRequest(
            student_name="Alice",
            question="我想做一个图神经网络相关的研究，下一步该怎么推进？",
            conversation_id="failed-sage-stage",
        )))
    assert executions == ["failed-sage-stage"]


def test_greeting_keeps_canonical_trace_order(tmp_path: Path) -> None:
    """A bare greeting produces every canonical trace key in order, with the
    booking stages reporting as skipped placeholders."""

    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        fast_intent_classifier_enabled=False,
    )
    service = DigitalTwinService(settings)
    service._llm_client = _AdviseOnlyLLMClient("你好 Alice，今天有什么我可以帮你？")

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context=None,
                conversation_id="conv-greeting",
                question="老师好。",
            )
        )
    )

    assert _trace_keys(response) == CANONICAL_CHAT_TRACE
    statuses = _trace_statuses(response)
    assert statuses["bootstrap"] == "completed"
    assert statuses["response_render"] == "completed"
    # booking_execute is irrelevant for a greeting and must be a skipped placeholder
    assert statuses["booking_execute"] == "skipped"


def test_research_question_keeps_canonical_trace_order(tmp_path: Path) -> None:
    """A research-style question takes the answer path and must still emit
    every canonical trace key in the canonical order."""

    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        fast_intent_classifier_enabled=False,
    )
    service = DigitalTwinService(settings)
    service._llm_client = _AdviseOnlyLLMClient("建议先复现 baseline，再围绕评估指标做对比实验。")

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-research",
                question="我想做一个图神经网络相关的研究，下一步该怎么推进？",
            )
        )
    )

    assert _trace_keys(response) == CANONICAL_CHAT_TRACE
    assert response.workflow_action == "advise_only"


def test_booking_with_full_details_keeps_canonical_trace_order(
    tmp_path: Path,
) -> None:
    """A booking request with all required fields exercises the booking
    branch. The trace shape stays canonical."""

    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        fast_intent_classifier_enabled=False,
    )
    service = DigitalTwinService(settings)
    service._llm_client = _BookingLLMClient()

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-booking",
                question="请帮我预约 2026-05-26 15:00 讨论论文进展",
            )
        )
    )

    assert _trace_keys(response) == CANONICAL_CHAT_TRACE
    assert response.workflow_action == "book_meeting"
    statuses = _trace_statuses(response)
    # booking branch is the active path here
    assert statuses["booking_prepare"] == "completed"
    assert statuses["booking_execute"] == "completed"


def test_admin_knowledge_add_runs_through_legacy_linear_pipeline(
    tmp_path: Path,
) -> None:
    """Smoke-check that non-chat flows continue to produce results. The DAG
    rewrite is scoped to chat environment names; admin/knowledge calls must
    keep using the linear path."""

    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)

    document = KnowledgeDocumentCreate(
        title="导师 office hour 通则",
        content="工作日 09:00-18:00 接受预约，每场 30 分钟。",
        tags=["meeting_policy"],
    )
    record = service.add_knowledge(document)

    assert record is not None
    assert record.title == document.title


# ------------------ Unit tests for _canonicalize_workflow_trace --------------


def _stub_step(key: str, status: str = "completed") -> WorkflowTraceStep:
    return WorkflowTraceStep(
        key=key,
        title=key,
        summary=key,
        detail=key,
        status=status,
        duration_ms=0.0,
    )


def test_canonicalize_orders_known_keys_into_canonical_sequence() -> None:
    # Feed in the canonical order shuffled
    shuffled = [
        _stub_step("response_render"),
        _stub_step("bootstrap"),
        _stub_step("knowledge_retrieve"),
        _stub_step("memory_retrieve"),
        _stub_step("prompt_build"),
        _stub_step("workflow_plan_preview"),
        _stub_step("interaction_understand"),
        _stub_step("booking_prepare"),
        _stub_step("booking_execute"),
        _stub_step("llm_answer"),
        _stub_step("memory_persist"),
        _stub_step("memory_profile_consolidate"),
        _stub_step("follow_up_plan"),
        _stub_step("memory_usefulness_score"),
    ]
    canonical = _canonicalize_workflow_trace(shuffled)
    assert [step.key for step in canonical] == [
        "bootstrap",
        "workflow_plan_preview",
        "interaction_understand",
        "booking_prepare",
        "booking_execute",
        "memory_retrieve",
        "knowledge_retrieve",
        "prompt_build",
        "llm_answer",
        "memory_persist",
        "memory_profile_consolidate",
        "follow_up_plan",
        "memory_usefulness_score",
        "response_render",
    ]


def test_canonicalize_is_idempotent_when_already_in_order() -> None:
    sequence = [_stub_step(key) for key in CANONICAL_CHAT_TRACE]
    canonical = _canonicalize_workflow_trace(sequence)
    assert [step.key for step in canonical] == CANONICAL_CHAT_TRACE


def test_canonicalize_keeps_unknown_keys_at_the_end_in_relative_order() -> None:
    sequence = [
        _stub_step("unknown_b"),
        _stub_step("response_render"),
        _stub_step("unknown_a"),
        _stub_step("bootstrap"),
    ]
    canonical = _canonicalize_workflow_trace(sequence)
    assert [step.key for step in canonical] == [
        "bootstrap",
        "response_render",
        "unknown_b",
        "unknown_a",
    ]


def test_canonical_trace_order_matches_published_constant() -> None:
    # Sanity check: the constant exposed by service.py contains the same
    # keys our fixture asserts plus conditional memory and full-source audits.
    assert tuple(CANONICAL_CHAT_TRACE) == tuple(
        key for key in _CANONICAL_TRACE_ORDER
        if key not in {"artifact_memory_writeback", "source_audit"}
    )
    assert _CANONICAL_TRACE_ORDER.index("source_audit") < _CANONICAL_TRACE_ORDER.index("memory_persist")


# ------------------------- ChatContextMergeFunction --------------------------


class _MergeStub:
    """Lightweight stand-in for `ChatWorkflowContext` used by merge tests.

    The chat DAG fans branches out over the same context object; each branch
    mutates a disjoint set of fields. The merger only emits when every branch
    has reported, so a tiny dataclass-shaped stub is enough to exercise the
    fan-in semantics without bringing up the full service.
    """

    def __init__(self, conversation_id: str) -> None:
        self.conversation_id = conversation_id
        # Disjoint fields populated by independent branches.
        self.knowledge_hits: list[str] = []
        self.memory_hits: list[str] = []
        self.follow_up_actions: list[str] = []
        self.persisted_memory_record: str | None = None
        self.memory_usefulness_signal: str | None = None


def test_chat_context_merge2_emits_only_after_both_branches_arrive() -> None:
    merger = _ChatContextMerge2()
    ctx = _MergeStub(conversation_id="conv-1")

    # First branch (knowledge) arrives -> no emission yet.
    ctx.knowledge_hits = ["hit-knowledge"]
    assert merger.map0(ctx) is None

    # Second branch (memory) arrives -> emit the merged context once.
    ctx.memory_hits = ["hit-memory"]
    emitted = merger.map1(ctx)
    assert emitted is ctx
    # Both branch contributions are visible on the emitted context.
    assert emitted.knowledge_hits == ["hit-knowledge"]
    assert emitted.memory_hits == ["hit-memory"]


def test_chat_context_merge2_handles_arrivals_in_reverse_order() -> None:
    merger = _ChatContextMerge2()
    ctx = _MergeStub(conversation_id="conv-2")

    ctx.memory_hits = ["m"]
    assert merger.map1(ctx) is None

    ctx.knowledge_hits = ["k"]
    emitted = merger.map0(ctx)
    assert emitted is ctx
    assert emitted.memory_hits == ["m"]
    assert emitted.knowledge_hits == ["k"]


def test_chat_context_merge2_filters_out_none_branches() -> None:
    merger = _ChatContextMerge2()
    # A branch may legitimately yield None (e.g. upstream filter trimmed it).
    assert merger.map0(None) is None
    assert merger.map1(None) is None


def test_chat_context_merge2_isolates_state_per_conversation() -> None:
    merger = _ChatContextMerge2()
    ctx_a = _MergeStub(conversation_id="conv-A")
    ctx_b = _MergeStub(conversation_id="conv-B")

    # conv-A's first branch arrives.
    assert merger.map0(ctx_a) is None
    # conv-B fires both branches in interleaved order; should still emit on its
    # second arrival without being affected by conv-A's pending state.
    assert merger.map0(ctx_b) is None
    assert merger.map1(ctx_b) is ctx_b
    # conv-A still pending until its second branch arrives.
    assert merger.map1(ctx_a) is ctx_a


def test_chat_context_merge2_resets_after_emission_for_reuse() -> None:
    merger = _ChatContextMerge2()
    ctx = _MergeStub(conversation_id="conv-reuse")

    assert merger.map0(ctx) is None
    assert merger.map1(ctx) is ctx

    # A subsequent round with the same conversation_id (e.g. a new request)
    # must reset the pending-set, otherwise the merger would emit on the very
    # first branch arrival of the next round.
    ctx2 = _MergeStub(conversation_id="conv-reuse")
    assert merger.map0(ctx2) is None
    assert merger.map1(ctx2) is ctx2


def test_chat_context_merge4_emits_only_after_all_four_branches() -> None:
    merger = _ChatContextMerge4()
    ctx = _MergeStub(conversation_id="conv-fanout")

    ctx.persisted_memory_record = "persisted"
    assert merger.map0(ctx) is None

    # Profile-consolidate branch (no payload field, but still must report).
    assert merger.map1(ctx) is None

    ctx.follow_up_actions = ["call-back"]
    assert merger.map2(ctx) is None

    ctx.memory_usefulness_signal = "useful"
    emitted = merger.map3(ctx)
    assert emitted is ctx
    assert emitted.persisted_memory_record == "persisted"
    assert emitted.follow_up_actions == ["call-back"]
    assert emitted.memory_usefulness_signal == "useful"


def test_chat_context_merge_subclasses_expose_class_level_map_methods() -> None:
    # SAGE's comap() validator inspects `hasattr(function, "mapN")` against
    # the class itself before any instance exists, so the mapN methods MUST
    # live on the class (not be installed dynamically in __init__).
    assert hasattr(_ChatContextMerge2, "map0")
    assert hasattr(_ChatContextMerge2, "map1")
    assert not hasattr(_ChatContextMerge2, "map2")

    for name in ("map0", "map1", "map2", "map3"):
        assert hasattr(_ChatContextMerge4, name)
    assert not hasattr(_ChatContextMerge4, "map4")

    assert _ChatContextMerge2.n_inputs == 2
    assert _ChatContextMerge4.n_inputs == 4
    assert ChatContextMergeFunction.is_comap is True
