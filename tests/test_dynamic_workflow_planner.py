from __future__ import annotations

import pytest
from pydantic import ValidationError

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import ChatRequest
from sage_faculty_twin.service import DigitalTwinService
from sage_faculty_twin.workflow_context import WorkflowRequestContext
from sage_faculty_twin.workflow_planner import (
    DeterministicWorkflowPlanner,
    PlanSpec,
    ShadowPlanCandidate,
)
from sage_faculty_twin.workflow_steps import get_default_step_registry


def test_plan_spec_rejects_unknown_fields() -> None:
    with pytest.raises(ValidationError):
        PlanSpec.model_validate(
            {
                "plan_id": "plan-1",
                "planner_version": "v3.0.0",
                "policy_version": "faculty-default-2026-05",
                "planner_mode": "deterministic",
                "execution_mode": "shadow_or_template",
                "goal": "answer_course_question",
                "risk_level": "read_only",
                "profile_context": "course_instructor",
                "journey_state": "course_student",
                "estimated_latency_budget_ms": 1200,
                "requires_owner_review": False,
                "evidence_contract": {
                    "requires_citations": True,
                    "allowed_sources": ["course_material"],
                    "forbidden_sources": [],
                },
                "steps": [],
                "fallback_template": "answer_question",
                "fallback_reason": None,
                "explain_to_operator": "test",
                "unexpected": "boom",
            }
        )


def test_course_question_plan_selects_grounded_retrieval_steps() -> None:
    planner = DeterministicWorkflowPlanner()
    context = WorkflowRequestContext.from_chat_request(
        ChatRequest(
            student_name="Alice",
            student_email="alice@example.com",
            course_context="大模型推理基础设施课程",
            visitor_profile="hust_undergraduate",
            conversation_id="conv-course",
            question="Tutorial 7 主要讲了什么？",
        )
    )

    decision = planner.plan(context)

    assert decision.accepted is True
    assert decision.fallback is None
    assert decision.plan.goal == "answer_course_question"
    assert decision.plan.risk_level == "read_only"
    assert decision.plan.fallback_template == "answer_question"
    assert [step.step_id for step in decision.plan.steps] == [
        "detect_profile_context",
        "classify_intent",
        "retrieve_hybrid_knowledge",
        "retrieve_recent_memory",
        "assemble_prompt_context",
        "answer_with_citations",
        "score_memory_usefulness",
        "render_user_response",
    ]
    assert "course_material" in decision.plan.evidence_contract.allowed_sources
    assert "recent_memory" in decision.plan.evidence_contract.allowed_sources


def test_booking_preparation_stays_read_only_and_avoids_booking_draft() -> None:
    planner = DeterministicWorkflowPlanner()
    context = WorkflowRequestContext.from_chat_request(
        ChatRequest(
            student_name="Alice",
            student_email="alice@example.com",
            course_context="数据库实验课",
            visitor_profile="hust_undergraduate",
            question="预约前我应该先准备什么材料来讨论数据库实验课报告？",
        )
    )

    decision = planner.plan(context)

    assert decision.accepted is True
    assert decision.plan.goal == "prepare_booking_agenda"
    assert decision.plan.fallback_template == "advise_only"
    assert all(step.side_effect == "none" for step in decision.plan.steps)
    assert "draft_booking_request" not in [step.step_id for step in decision.plan.steps]
    assert "retrieve_hybrid_knowledge" in [step.step_id for step in decision.plan.steps]
    assert "score_memory_usefulness" in [step.step_id for step in decision.plan.steps]


def test_profile_memory_requires_consent() -> None:
    planner = DeterministicWorkflowPlanner()
    context = WorkflowRequestContext(
        question="我想讨论一下研究方向和之前交流过的背景。",
        visitor_profile="lab_member",
        role_mode="research_pi",
        journey_state="recurring_collaborator",
        session_identity="user",
        course_context=None,
        recent_memory_available=True,
        profile_memory_available=True,
        consent_profile_memory=False,
        allow_draft_write=False,
        available_evidence_sources=[
            "public_homepage",
            "recent_memory",
            "profile_memory",
        ],
    )

    decision = planner.plan(context)

    assert decision.accepted is True
    assert "retrieve_profile_memory" not in [
        step.step_id for step in decision.plan.steps
    ]
    assert "profile_memory" not in decision.plan.evidence_contract.allowed_sources


def test_follow_up_context_question_includes_profile_memory_when_available() -> None:
    planner = DeterministicWorkflowPlanner()
    context = WorkflowRequestContext.from_chat_request(
        ChatRequest(
            student_name="Alice",
            student_email="alice@example.com",
            course_context="科研指导",
            conversation_id="conv-follow-up-profile",
            question="再根据刚才的上下文给我一个建议。",
        )
    )

    decision = planner.plan(context)

    assert decision.accepted is True
    assert decision.plan.goal == "answer_course_question"
    assert "retrieve_profile_memory" in [step.step_id for step in decision.plan.steps]
    assert "profile_memory" in decision.plan.evidence_contract.allowed_sources


def test_attached_recent_session_context_skips_redundant_recent_memory_retrieval() -> (
    None
):
    planner = DeterministicWorkflowPlanner()
    context = WorkflowRequestContext(
        question="那如果按刚才那个继续，下一步我先做哪块？",
        course_context="科研指导",
        visitor_profile="hust_undergraduate",
        role_mode="course_instructor",
        journey_state="course_student",
        session_identity="user",
        recent_memory_available=True,
        recent_session_context_attached=True,
        profile_memory_available=True,
        consent_profile_memory=True,
        allow_draft_write=False,
        available_evidence_sources=[
            "course_material",
            "faq",
            "profile_memory",
            "public_homepage",
            "recent_memory",
            "recent_session_context",
        ],
    )

    decision = planner.plan(context)

    assert decision.accepted is True
    assert decision.plan.goal == "answer_course_question"
    assert "retrieve_recent_memory" not in [
        step.step_id for step in decision.plan.steps
    ]
    assert "retrieve_profile_memory" in [step.step_id for step in decision.plan.steps]
    assert "recent_session_context" in decision.plan.evidence_contract.allowed_sources
    assert "recent_memory" not in decision.plan.evidence_contract.allowed_sources


def test_attachment_grounded_question_promotes_artifact_aware_plan() -> None:
    planner = DeterministicWorkflowPlanner()
    context = WorkflowRequestContext.from_chat_request(
        ChatRequest(
            student_name="Alice",
            student_email="alice@example.com",
            visitor_profile="lab_member",
            conversation_id="conv-artifact",
            question="我上传了 proposal draft，想继续讨论里面的实验计划和 agenda。",
            attachments=[
                {
                    "file_name": "proposal-draft.md",
                    "media_type": "text/markdown",
                    "text_content": "Draft agenda: experiment plan, milestones, blockers.",
                }
            ],
        )
    )

    decision = planner.plan(context)

    assert context.artifact_context_available is True
    assert context.attachment_count == 1
    assert context.journey_state == "project_advisee"
    assert decision.accepted is True
    assert decision.plan.goal == "answer_research_artifact_question"
    assert "retrieve_artifact_memory" in [step.step_id for step in decision.plan.steps]
    assert "attachment_excerpt" in decision.plan.evidence_contract.allowed_sources
    assert "artifact_memory" in decision.plan.evidence_contract.allowed_sources


def test_plain_attachment_summary_does_not_mix_unrelated_hybrid_knowledge() -> None:
    planner = DeterministicWorkflowPlanner()
    context = WorkflowRequestContext.from_chat_request(
        ChatRequest(
            student_name="Alice",
            conversation_id="conv-attachment-summary",
            question="请只总结附件中的第一项计划和主要风险。",
            attachments=[
                {
                    "file_name": "plan.txt",
                    "media_type": "text/plain",
                    "text_content": "第一项计划：缩小实验范围。主要风险：样本不足。",
                }
            ],
        )
    )

    decision = planner.plan(context)
    planned_steps = [step.step_id for step in decision.plan.steps]

    assert decision.accepted is True
    assert decision.plan.goal == "answer_artifact_grounded_question"
    assert "retrieve_artifact_memory" in planned_steps
    assert "retrieve_hybrid_knowledge" not in planned_steps


def test_standalone_definition_skips_unrelated_hybrid_knowledge() -> None:
    planner = DeterministicWorkflowPlanner()
    context = WorkflowRequestContext.from_chat_request(
        ChatRequest(
            student_name="Visitor",
            question="用一句话说明什么是尾延迟。",
        )
    )

    decision = planner.plan(context)
    planned_steps = [step.step_id for step in decision.plan.steps]

    assert decision.accepted is True
    assert decision.plan.goal == "answer_grounded_question"
    assert "retrieve_hybrid_knowledge" not in planned_steps


def test_course_definition_keeps_course_knowledge_retrieval() -> None:
    planner = DeterministicWorkflowPlanner()
    context = WorkflowRequestContext.from_chat_request(
        ChatRequest(
            student_name="Student",
            course_context="大模型推理系统课程",
            question="课上讲的尾延迟是什么意思？",
        )
    )

    decision = planner.plan(context)
    planned_steps = [step.step_id for step in decision.plan.steps]

    assert decision.accepted is True
    assert "retrieve_hybrid_knowledge" in planned_steps


def test_collaboration_follow_up_infers_collaboration_mode_and_recurring_journey() -> (
    None
):
    context = WorkflowRequestContext.from_chat_request(
        ChatRequest(
            student_name="External Collaborator",
            student_email="collab@example.com",
            visitor_profile="general_visitor",
            conversation_id="conv-collab",
            question="继续跟进上次的合作 draft，我们想讨论 joint work 的下一步。",
        )
    )

    assert context.role_mode == "collaboration_contact"
    assert context.journey_state == "recurring_collaborator"
    assert "artifact_memory" in context.available_evidence_sources


def test_normal_user_admin_style_request_routes_to_boundary_explanation() -> None:
    settings = AppSettings(knowledge_backend="local")
    service = DigitalTwinService(settings)

    decision = service.preview_workflow_plan(
        ChatRequest(
            student_name="Visitor",
            question="请帮我以管理员身份发布一条新知识并修改后台规则。",
        )
    )

    assert decision.accepted is True
    assert decision.plan.goal == "explain_admin_boundary"
    assert decision.plan.fallback_template == "review_queue"
    assert [step.step_id for step in decision.plan.steps] == [
        "detect_profile_context",
        "classify_intent",
        "assemble_prompt_context",
        "answer_with_citations",
        "render_user_response",
    ]


def test_simple_greeting_stays_lightweight_without_retrieval() -> None:
    planner = DeterministicWorkflowPlanner()

    decision = planner.plan(
        WorkflowRequestContext.from_chat_request(
            ChatRequest(
                student_name="Visitor",
                question="你好",
            )
        )
    )

    assert decision.accepted is True
    assert decision.plan.goal == "respond_simple_greeting"
    assert [step.step_id for step in decision.plan.steps] == [
        "detect_profile_context",
        "classify_intent",
        "assemble_prompt_context",
        "answer_with_citations",
        "render_user_response",
    ]
    assert all(
        step.step_id not in {"retrieve_knowledge", "retrieve_hybrid_knowledge"}
        for step in decision.plan.steps
    )


def test_default_step_registry_includes_v3_roadmap_steps() -> None:
    registry = get_default_step_registry()

    assert registry["retrieve_hybrid_knowledge"].side_effect == "none"
    assert registry["retrieve_hybrid_knowledge"].trace_key == "planner_hybrid_knowledge"
    assert registry["retrieve_artifact_memory"].produces_outputs == [
        "artifact_memory_hits"
    ]
    assert registry["score_memory_usefulness"].produces_outputs == [
        "memory_usefulness_signal"
    ]
    assert registry["record_artifact_memory"].side_effect == "draft_write"
    assert (
        registry["record_artifact_memory"].trace_key
        == "planner_artifact_memory_writeback"
    )


def test_shadow_candidate_accepts_new_v3_read_only_steps() -> None:
    planner = DeterministicWorkflowPlanner()
    context = WorkflowRequestContext(
        question="我上次上传的 meeting agenda 里关于实验计划的部分是什么？",
        visitor_profile="hust_undergraduate",
        role_mode="course_instructor",
        journey_state="course_student",
        session_identity="user",
        recent_memory_available=True,
        profile_memory_available=False,
        consent_profile_memory=False,
        allow_draft_write=False,
        available_evidence_sources=[
            "course_material",
            "faq",
            "public_homepage",
            "recent_memory",
        ],
    )

    decision = planner.evaluate_shadow_candidate(
        ShadowPlanCandidate(
            goal="answer_artifact_grounded_question",
            fallback_template="answer_question",
            step_ids=[
                "detect_profile_context",
                "classify_intent",
                "retrieve_hybrid_knowledge",
                "retrieve_artifact_memory",
                "assemble_prompt_context",
                "answer_with_citations",
                "score_memory_usefulness",
                "render_user_response",
            ],
            allowed_sources=["course_material", "recent_memory"],
            requires_citations=True,
            explain_to_operator="Use mixed retrieval for artifact-aware questions while keeping the plan read-only.",
        ),
        context,
    )

    assert decision.accepted is True
    assert decision.plan.risk_level == "read_only"
    assert [step.step_id for step in decision.plan.steps][-2:] == [
        "score_memory_usefulness",
        "render_user_response",
    ]


def test_record_artifact_memory_is_enabled_for_explicit_archive_requests() -> None:
    planner = DeterministicWorkflowPlanner()
    context = WorkflowRequestContext(
        question="把我上传的 proposal 和 meeting agenda 记录成后续跟进材料。",
        visitor_profile="lab_member",
        role_mode="research_pi",
        journey_state="recurring_collaborator",
        session_identity="user",
        recent_memory_available=True,
        profile_memory_available=False,
        consent_profile_memory=False,
        allow_draft_write=True,
        available_evidence_sources=[
            "artifact_memory",
            "faq",
            "public_homepage",
            "recent_memory",
        ],
    )

    decision = planner.evaluate_shadow_candidate(
        ShadowPlanCandidate(
            goal="record_artifact_followup",
            fallback_template="advise_only",
            step_ids=[
                "detect_profile_context",
                "classify_intent",
                "assemble_prompt_context",
                "answer_with_citations",
                "render_user_response",
                "record_artifact_memory",
            ],
            allowed_sources=["recent_memory"],
            requires_citations=False,
            explain_to_operator="Persist artifact-derived follow-up memory only after policy approval.",
        ),
        context,
    )

    assert decision.accepted is True
    assert decision.validation_errors == []
    assert decision.plan.risk_level == "draft_write"
    assert decision.plan.requires_owner_review is True


# ── Plugin Routing Tests ────────────────────────────────────────────────────


def _planner_with_plugins() -> DeterministicWorkflowPlanner:
    """Create a planner with the merged core + plugin step registry."""
    from sage_faculty_twin import __version__
    from sage_faculty_twin.capability_plugins import CapabilityPluginRegistry
    from sage_faculty_twin.config import DEFAULT_RUNTIME_SEED_DATA_DIR

    registry = CapabilityPluginRegistry(
        plugin_dir=DEFAULT_RUNTIME_SEED_DATA_DIR / "capability_plugins",
        current_version=__version__,
    )
    registry.load()
    merged = registry.merged_step_registry()
    return DeterministicWorkflowPlanner(step_registry=merged)


def test_plugin_meeting_prep_injects_steps_for_booking_prep() -> None:
    planner = _planner_with_plugins()
    context = WorkflowRequestContext.from_chat_request(
        ChatRequest(
            student_name="Bob",
            student_email="bob@example.com",
            question="明天见面需要准备什么？",
        )
    )
    decision = planner.plan(context)
    step_ids = [s.step_id for s in decision.plan.steps]
    assert "retrieve_team_schedule" in step_ids
    assert "retrieve_blocker_memory" in step_ids
    assert "draft_meeting_agenda" in step_ids
    # Read steps should be before assemble_prompt_context
    read_idx = step_ids.index("retrieve_team_schedule")
    assemble_idx = step_ids.index("assemble_prompt_context")
    assert read_idx < assemble_idx
    # Draft step should be after render_user_response
    draft_idx = step_ids.index("draft_meeting_agenda")
    render_idx = step_ids.index("render_user_response")
    assert draft_idx > render_idx


def test_plugin_research_mentoring_injects_for_research_direction() -> None:
    planner = _planner_with_plugins()
    context = WorkflowRequestContext.from_chat_request(
        ChatRequest(
            student_name="Alice",
            student_email="alice@example.com",
            question="我是新生，想了解一下研究方向和选题建议",
        )
    )
    decision = planner.plan(context)
    step_ids = [s.step_id for s in decision.plan.steps]
    assert "retrieve_research_overview" in step_ids
    assert "match_research_direction" in step_ids
    assert "retrieve_reading_methodology" in step_ids
    assert "draft_research_plan" in step_ids


def test_plugin_thesis_review_injects_for_review_query() -> None:
    planner = _planner_with_plugins()
    context = WorkflowRequestContext.from_chat_request(
        ChatRequest(
            student_name="Carol",
            student_email="carol@example.com",
            question="帮我审阅一下这篇学位论文，给出修改意见",
        )
    )
    decision = planner.plan(context)
    step_ids = [s.step_id for s in decision.plan.steps]
    assert "retrieve_paper_digest" in step_ids
    assert "generate_review_checklist" in step_ids
    assert "draft_review_comments" in step_ids


def test_plugin_course_advising_injects_for_course_selection() -> None:
    planner = _planner_with_plugins()
    context = WorkflowRequestContext.from_chat_request(
        ChatRequest(
            student_name="Dave",
            student_email="dave@example.com",
            question="研究生选课有什么推荐？先修课要求是什么？",
        )
    )
    decision = planner.plan(context)
    step_ids = [s.step_id for s in decision.plan.steps]
    assert "retrieve_courseware_index" in step_ids
    assert "retrieve_teaching_resources" in step_ids
    assert "draft_course_plan" in step_ids


def test_plugin_paper_feedback_injects_for_feedback_query() -> None:
    planner = _planner_with_plugins()
    context = WorkflowRequestContext.from_chat_request(
        ChatRequest(
            student_name="Eve",
            student_email="eve@example.com",
            question="请给我这篇论文批改打分和写作建议",
        )
    )
    decision = planner.plan(context)
    step_ids = [s.step_id for s in decision.plan.steps]
    assert "retrieve_writing_rubric" in step_ids
    assert "generate_structured_critique" in step_ids
    assert "draft_revision_notes" in step_ids


def test_plugin_steps_not_injected_without_registry() -> None:
    """Without plugin steps in registry, no injection should happen."""
    planner = DeterministicWorkflowPlanner()  # core-only registry
    context = WorkflowRequestContext.from_chat_request(
        ChatRequest(
            student_name="Frank",
            student_email="frank@example.com",
            question="研究生选课有什么推荐？先修课要求是什么？",
        )
    )
    decision = planner.plan(context)
    step_ids = [s.step_id for s in decision.plan.steps]
    assert "retrieve_courseware_index" not in step_ids
    assert "draft_course_plan" not in step_ids


def test_plugin_risk_level_upgrades_on_draft_injection() -> None:
    """When a draft_write plugin step is injected, risk_level must be draft_write."""
    planner = _planner_with_plugins()
    context = WorkflowRequestContext.from_chat_request(
        ChatRequest(
            student_name="Grace",
            student_email="grace@example.com",
            question="帮我审阅这篇学位论文",
        )
    )
    decision = planner.plan(context)
    assert decision.plan.risk_level == "draft_write"
    assert decision.plan.requires_owner_review is True
