from __future__ import annotations

import hashlib
import re
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from .workflow_context import WorkflowRequestContext
from .workflow_policy import (
    WorkflowPolicy,
    WorkflowPolicyValidator,
    build_default_workflow_policy,
    strongest_side_effect_to_risk_level,
)
from .workflow_steps import WorkflowStepDefinition, get_default_step_registry

_EXECUTION_MODE_PATTERN = "^(shadow_or_template|template_only|live_plan)$"
_PLANNER_MODE_PATTERN = "^(deterministic|llm_shadow|llm_live)$"
_RISK_LEVEL_PATTERN = "^(read_only|draft_write|owner_review|admin_only)$"
_SIDE_EFFECT_PATTERN = "^(none|draft_write|owner_review|admin_only)$"


class EvidenceContract(BaseModel):
    model_config = ConfigDict(extra="forbid")

    requires_citations: bool = True
    allowed_sources: list[str] = Field(default_factory=list)
    forbidden_sources: list[str] = Field(default_factory=list)


class PlanStepSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    step_id: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=256)
    inputs: list[str] = Field(default_factory=list)
    outputs: list[str] = Field(default_factory=list)
    side_effect: str = Field(default="none", pattern=_SIDE_EFFECT_PATTERN)


class ShadowPlanCandidate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    goal: str = Field(min_length=1, max_length=128)
    fallback_template: str = Field(min_length=1, max_length=64)
    step_ids: list[str] = Field(default_factory=list, min_length=1, max_length=12)
    allowed_sources: list[str] = Field(default_factory=list, max_length=8)
    requires_citations: bool = True
    explain_to_operator: str = Field(min_length=1, max_length=512)


class PlanSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan_id: str = Field(min_length=1, max_length=128)
    planner_version: str = Field(min_length=1, max_length=32)
    policy_version: str = Field(min_length=1, max_length=64)
    planner_mode: str = Field(pattern=_PLANNER_MODE_PATTERN)
    execution_mode: str = Field(pattern=_EXECUTION_MODE_PATTERN)
    goal: str = Field(min_length=1, max_length=128)
    risk_level: str = Field(pattern=_RISK_LEVEL_PATTERN)
    profile_context: str = Field(min_length=1, max_length=64)
    journey_state: str = Field(min_length=1, max_length=64)
    estimated_latency_budget_ms: int = Field(ge=0, le=120000)
    requires_owner_review: bool = False
    evidence_contract: EvidenceContract
    steps: list[PlanStepSpec] = Field(default_factory=list)
    fallback_template: str = Field(min_length=1, max_length=64)
    fallback_reason: str | None = Field(default=None, max_length=256)
    explain_to_operator: str = Field(min_length=1, max_length=512)


class PlannerFallback(BaseModel):
    model_config = ConfigDict(extra="forbid")

    fallback_template: str = Field(min_length=1, max_length=64)
    reason: str = Field(min_length=1, max_length=256)


class PlannerDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    plan: PlanSpec
    accepted: bool
    validation_errors: list[str] = Field(default_factory=list)
    fallback: PlannerFallback | None = None


class DeterministicWorkflowPlanner:
    def __init__(
        self,
        *,
        planner_version: str = "v3.0.0",
        policy: WorkflowPolicy | None = None,
        policy_path: Path | None = None,
        step_registry: dict[str, WorkflowStepDefinition] | None = None,
    ) -> None:
        self._planner_version = planner_version
        self._step_registry = step_registry if step_registry is not None else get_default_step_registry()
        self._policy = policy or build_default_workflow_policy(policy_path)
        # Validation is pure for a planner instance. Reusing the validator
        # avoids rebuilding the same policy/registry wrapper on every chat,
        # while keeping per-request ``WorkflowRequestContext`` validation.
        self._validator = WorkflowPolicyValidator(
            policy=self._policy,
            step_registry=self._step_registry,
        )

    @property
    def policy_version(self) -> str:
        return self._policy.policy_version

    @property
    def planner_version(self) -> str:
        return self._planner_version

    def plan(self, context: WorkflowRequestContext) -> PlannerDecision:
        plan = self._build_plan(context)
        return self.evaluate_plan(plan, context)

    def evaluate_plan(
        self, plan: PlanSpec, context: WorkflowRequestContext
    ) -> PlannerDecision:
        validation = self._validator.validate(plan, context)
        fallback = None
        if not validation.accepted:
            fallback = PlannerFallback(
                fallback_template=plan.fallback_template,
                reason="; ".join(validation.errors[:2])[:256],
            )

        return PlannerDecision(
            plan=plan,
            accepted=validation.accepted,
            validation_errors=validation.errors,
            fallback=fallback,
        )

    def evaluate_shadow_candidate(
        self,
        candidate: ShadowPlanCandidate,
        context: WorkflowRequestContext,
    ) -> PlannerDecision:
        steps = [
            self._build_candidate_step_spec(step_id, candidate.goal)
            for step_id in candidate.step_ids
        ]
        estimated_latency_budget_ms = sum(
            self._step_registry[step.step_id].timeout_budget_ms
            for step in steps
            if step.step_id in self._step_registry
        )
        strongest_side_effect = "none"
        for step in steps:
            strongest_side_effect = self._merge_side_effect(
                strongest_side_effect, step.side_effect
            )

        plan = PlanSpec(
            plan_id=self._build_plan_id(f"shadow-{candidate.goal}", context.question),
            planner_version=self._planner_version,
            policy_version=self._policy.policy_version,
            planner_mode="llm_shadow",
            execution_mode="shadow_or_template",
            goal=candidate.goal,
            risk_level=strongest_side_effect_to_risk_level(strongest_side_effect),
            profile_context=context.role_mode,
            journey_state=context.journey_state,
            estimated_latency_budget_ms=estimated_latency_budget_ms,
            requires_owner_review=strongest_side_effect != "none",
            evidence_contract=EvidenceContract(
                requires_citations=candidate.requires_citations,
                allowed_sources=list(candidate.allowed_sources),
                forbidden_sources=["private_student_record_without_consent"],
            ),
            steps=steps,
            fallback_template=candidate.fallback_template,
            fallback_reason=None,
            explain_to_operator=candidate.explain_to_operator,
        )
        return self.evaluate_plan(plan, context)

    def _build_plan(self, context: WorkflowRequestContext) -> PlanSpec:
        question = context.question.lower()
        include_recent_memory = _should_include_recent_memory(context)
        # Collect applicable plugin steps for this query
        plugin_read_steps, plugin_draft_steps = self._plugin_steps_for(question)
        if (
            _looks_like_admin_only_request(question)
            and context.session_identity != "admin"
        ):
            goal = "explain_admin_boundary"
            fallback_template = "review_queue"
            step_ids = [
                "detect_profile_context",
                "classify_intent",
                "assemble_prompt_context",
                "answer_with_citations",
                "render_user_response",
            ]
            evidence_contract = EvidenceContract(requires_citations=False)
            explain = "Normal user attempted an admin-only operation; explain the boundary and route to review."
        elif _looks_like_artifact_record_request(question):
            goal = "record_artifact_followup"
            fallback_template = "advise_only"
            include_artifact_memory = _should_include_artifact_memory(context)
            step_ids = [
                "detect_profile_context",
                "classify_intent",
            ]
            if include_recent_memory:
                step_ids.append("retrieve_recent_memory")
            if include_artifact_memory:
                step_ids.append("retrieve_artifact_memory")
            step_ids.extend(
                [
                    "assemble_prompt_context",
                    "answer_with_citations",
                    "render_user_response",
                    "record_artifact_memory",
                ]
            )
            evidence_contract = self._build_evidence_contract(
                context,
                include_recent_memory=include_recent_memory,
                include_profile_memory=False,
                include_artifact_memory=include_artifact_memory,
            )
            explain = "Explicit artifact archival request should create a reviewable artifact-memory draft with provenance."
        elif _looks_like_booking_preparation(question):
            goal = "prepare_booking_agenda"
            fallback_template = "advise_only"
            include_profile_memory = _should_include_profile_memory(context)
            include_artifact_memory = _should_include_artifact_memory(context)
            step_ids = [
                "detect_profile_context",
                "classify_intent",
            ]
            if include_profile_memory:
                if include_recent_memory:
                    step_ids.append("retrieve_recent_memory")
            else:
                step_ids.append("retrieve_hybrid_knowledge")
                if include_recent_memory:
                    step_ids.append("retrieve_recent_memory")
            if include_artifact_memory:
                step_ids.append("retrieve_artifact_memory")
            if include_profile_memory:
                step_ids.append("retrieve_profile_memory")
            step_ids.extend(
                [
                    "assemble_prompt_context",
                    "answer_with_citations",
                    "score_memory_usefulness",
                    "render_user_response",
                ]
            )
            evidence_contract = self._build_evidence_contract(
                context,
                include_recent_memory=include_recent_memory,
                include_profile_memory=include_profile_memory,
                include_artifact_memory=include_artifact_memory,
            )
            explain = (
                "Meeting-preparation question should gather guidance and evidence without creating a booking action."
                if not include_profile_memory
                else "Meeting-preparation question should ground on current evidence and reuse stable student preferences when the request explicitly references them."
            )
        elif _looks_like_booking_request(question):
            goal = "prepare_booking_request"
            fallback_template = "book_meeting"
            include_artifact_memory = _should_include_artifact_memory(context)
            step_ids = [
                "detect_profile_context",
                "classify_intent",
            ]
            if include_recent_memory:
                step_ids.append("retrieve_recent_memory")
            if include_artifact_memory:
                step_ids.append("retrieve_artifact_memory")
            step_ids.extend(
                [
                    "assemble_prompt_context",
                    "answer_with_citations",
                    "score_memory_usefulness",
                    "render_user_response",
                ]
            )
            evidence_contract = self._build_evidence_contract(
                context,
                include_recent_memory=include_recent_memory,
                include_profile_memory=False,
                include_artifact_memory=include_artifact_memory,
            )
            explain = (
                "Booking request should summarize the request context while leaving live execution to the proven V2 template."
                if not include_artifact_memory
                else "Booking request should use uploaded agendas, drafts, or prior artifacts to prepare a more complete booking handoff before the V2 template executes."
            )
        elif _looks_like_research_question(question):
            include_artifact_memory = _should_include_artifact_memory(context)
            goal = (
                "answer_research_artifact_question"
                if include_artifact_memory
                else "answer_research_question"
            )
            fallback_template = "answer_question"
            step_ids = [
                "detect_profile_context",
                "classify_intent",
                "retrieve_hybrid_knowledge",
            ]
            if include_recent_memory:
                step_ids.append("retrieve_recent_memory")
            if include_artifact_memory:
                step_ids.append("retrieve_artifact_memory")
            if context.profile_memory_available and context.consent_profile_memory:
                step_ids.append("retrieve_profile_memory")
            step_ids.extend(
                [
                    "assemble_prompt_context",
                    "answer_with_citations",
                    "score_memory_usefulness",
                    "render_user_response",
                ]
            )
            evidence_contract = self._build_evidence_contract(
                context,
                include_recent_memory=include_recent_memory,
                include_profile_memory=context.profile_memory_available
                and context.consent_profile_memory,
                include_artifact_memory=include_artifact_memory,
            )
            explain = (
                "Research or collaboration question should ground on research material and optionally profile memory."
                if not include_artifact_memory
                else "Research or collaboration question should combine research knowledge with uploaded or referenced project artifacts before answering."
            )
        elif _looks_like_simple_greeting(question):
            goal = "respond_simple_greeting"
            fallback_template = "answer_question"
            step_ids = [
                "detect_profile_context",
                "classify_intent",
                "assemble_prompt_context",
                "answer_with_citations",
                "render_user_response",
            ]
            evidence_contract = EvidenceContract(requires_citations=False)
            explain = (
                "Greeting should avoid expensive retrieval and keep the plan minimal."
            )
        else:
            include_artifact_memory = _should_include_artifact_memory(context)
            goal = (
                "answer_artifact_grounded_question"
                if include_artifact_memory
                else (
                    "answer_course_question"
                    if context.course_context
                    else "answer_grounded_question"
                )
            )
            fallback_template = "answer_question"
            include_profile_memory = _should_include_profile_memory(context)
            step_ids = [
                "detect_profile_context",
                "classify_intent",
            ]
            if include_profile_memory and include_recent_memory:
                step_ids.append("retrieve_recent_memory")
            else:
                step_ids.append("retrieve_hybrid_knowledge")
                if include_recent_memory:
                    step_ids.append("retrieve_recent_memory")
            if include_artifact_memory:
                step_ids.append("retrieve_artifact_memory")
            if include_profile_memory:
                step_ids.append("retrieve_profile_memory")
            step_ids.extend(
                [
                    "assemble_prompt_context",
                    "answer_with_citations",
                    "score_memory_usefulness",
                    "render_user_response",
                ]
            )
            evidence_contract = self._build_evidence_contract(
                context,
                include_recent_memory=include_recent_memory,
                include_profile_memory=include_profile_memory,
                include_artifact_memory=include_artifact_memory,
            )
            explain = (
                "Grounded answer should use synchronized knowledge and current session context before rendering a reply."
                if not include_profile_memory and not include_artifact_memory
                else "Grounded answer should combine synchronized knowledge, current-session context, uploaded or referenced artifacts, and stable profile memory only when policy and request context justify it."
            )

        steps = [self._build_step_spec(step_id, goal) for step_id in step_ids]

        # Inject plugin read-only steps before assemble_prompt_context
        # and plugin draft_write steps after render_user_response
        if plugin_read_steps or plugin_draft_steps:
            assembled_idx = None
            render_idx = None
            for i, s in enumerate(steps):
                if s.step_id == "assemble_prompt_context" and assembled_idx is None:
                    assembled_idx = i
                if s.step_id == "render_user_response":
                    render_idx = i
            injected_ids: list[str] = []
            for pid in plugin_read_steps:
                if pid in self._step_registry:
                    injected_ids.append(pid)
            if assembled_idx is not None and injected_ids:
                for offset, pid in enumerate(injected_ids):
                    steps.insert(
                        assembled_idx + offset,
                        self._build_step_spec(pid, goal),
                    )
                # Adjust render_idx after insertion
                if render_idx is not None:
                    render_idx += len(injected_ids)
            draft_injected: list[str] = []
            for pid in plugin_draft_steps:
                if pid in self._step_registry:
                    draft_injected.append(pid)
            if render_idx is not None and draft_injected:
                for offset, pid in enumerate(draft_injected):
                    steps.insert(
                        render_idx + 1 + offset,
                        self._build_step_spec(pid, goal),
                    )

        estimated_latency_budget_ms = sum(
            self._step_registry[step.step_id].timeout_budget_ms for step in steps
        )
        return PlanSpec(
            plan_id=self._build_plan_id(goal, context.question),
            planner_version=self._planner_version,
            policy_version=self._policy.policy_version,
            planner_mode="deterministic",
            execution_mode="shadow_or_template",
            goal=goal,
            risk_level=strongest_side_effect_to_risk_level(
                max(
                    (step.side_effect for step in steps),
                    key=lambda value: {
                        "none": 0,
                        "draft_write": 1,
                        "owner_review": 2,
                        "admin_only": 3,
                    }[value],
                )
            ),
            profile_context=context.role_mode,
            journey_state=context.journey_state,
            estimated_latency_budget_ms=estimated_latency_budget_ms,
            requires_owner_review=any(step.side_effect != "none" for step in steps),
            evidence_contract=evidence_contract,
            steps=steps,
            fallback_template=fallback_template,
            fallback_reason=None,
            explain_to_operator=explain,
        )

    def _build_evidence_contract(
        self,
        context: WorkflowRequestContext,
        *,
        include_recent_memory: bool,
        include_profile_memory: bool,
        include_artifact_memory: bool,
    ) -> EvidenceContract:
        allowed_sources = [
            source
            for source in context.available_evidence_sources
            if (source != "recent_memory" or include_recent_memory)
            if (source != "profile_memory" or include_profile_memory)
            and (source != "artifact_memory" or include_artifact_memory)
        ]
        return EvidenceContract(
            requires_citations=bool(allowed_sources),
            allowed_sources=allowed_sources,
            forbidden_sources=["private_student_record_without_consent"],
        )

    def _build_plan_id(self, goal: str, question: str) -> str:
        digest = hashlib.sha1(question.encode("utf-8")).hexdigest()[:10]
        return f"{goal}-{digest}"

    def _build_step_spec(self, step_id: str, goal: str) -> PlanStepSpec:
        definition = self._step_registry[step_id]
        return PlanStepSpec(
            step_id=definition.step_id,
            reason=_default_step_reason(definition, goal),
            inputs=list(definition.required_inputs),
            outputs=list(definition.produces_outputs),
            side_effect=definition.side_effect,
        )

    def _build_candidate_step_spec(self, step_id: str, goal: str) -> PlanStepSpec:
        definition = self._step_registry.get(step_id)
        if definition is None:
            return PlanStepSpec(
                step_id=step_id,
                reason=f"Candidate proposed an unregistered step while planning {goal}.",
                inputs=[],
                outputs=[],
                side_effect="none",
            )
        return self._build_step_spec(step_id, goal)

    def _merge_side_effect(self, current: str, candidate: str) -> str:
        order = {"none": 0, "draft_write": 1, "owner_review": 2, "admin_only": 3}
        return candidate if order[candidate] > order[current] else current

    def _plugin_steps_for(
        self, question: str
    ) -> tuple[list[str], list[str]]:
        """Return (read_only_step_ids, draft_write_step_ids) from enabled plugins that match this query.

        Only returns step IDs that actually exist in the current step registry.
        """
        read_steps: list[str] = []
        draft_steps: list[str] = []

        # meeting_prep: extends booking_preparation
        if _looks_like_booking_preparation(question):
            read_steps.extend([
                "retrieve_team_schedule",
                "retrieve_blocker_memory",
                "retrieve_follow_up_artifacts",
            ])
            draft_steps.append("draft_meeting_agenda")

        # research_mentoring: research questions with mentoring intent
        if _looks_like_research_question(question) and _looks_like_research_mentoring(question):
            read_steps.extend([
                "retrieve_research_overview",
                "match_research_direction",
                "retrieve_reading_methodology",
            ])
            draft_steps.append("draft_research_plan")

        # thesis_review: paper review workflows
        if _looks_like_thesis_review(question):
            read_steps.extend([
                "retrieve_paper_digest",
                "retrieve_paper_writing_guidance",
                "generate_review_checklist",
            ])
            draft_steps.append("draft_review_comments")

        # course_advising: course selection guidance
        if _looks_like_course_advising(question):
            read_steps.extend([
                "retrieve_courseware_index",
                "retrieve_teaching_resources",
            ])
            draft_steps.append("draft_course_plan")

        # paper_feedback: student paper feedback
        if _looks_like_paper_feedback(question):
            read_steps.extend([
                "retrieve_writing_rubric",
                "generate_structured_critique",
            ])
            draft_steps.append("draft_revision_notes")

        return read_steps, draft_steps


def _default_step_reason(definition: WorkflowStepDefinition, goal: str) -> str:
    mapping = {
        "detect_profile_context": f"The planner first needs a normalized profile context for {goal}.",
        "classify_intent": "Intent classification determines whether the request should stay read-only, booking-oriented, or review-gated.",
        "retrieve_knowledge": "The answer should be grounded on synchronized course, homepage, or FAQ knowledge.",
        "retrieve_hybrid_knowledge": "The planner should choose the strongest read-only retrieval mix before answering.",
        "retrieve_recent_memory": "Recent conversation state may affect what the student already prepared or asked.",
        "retrieve_profile_memory": "Longer-term profile memory is useful for recurring research or advising context.",
        "retrieve_artifact_memory": "Prior uploads, agendas, blockers, or follow-up artifacts may carry the needed context.",
        "assemble_prompt_context": "Collected evidence needs to be assembled into a bounded prompt context.",
        "answer_with_citations": "The user-facing answer should explain itself with grounded evidence.",
        "detect_knowledge_gap": "The planner should note whether retrieval looked weak or incomplete.",
        "score_memory_usefulness": "The planner should label whether the selected evidence was actually useful for later evaluation.",
        "render_user_response": "The final response payload should be formatted for the current interface.",
        "draft_booking_request": "The system can prepare a reviewable booking draft without confirming a meeting.",
        "create_escalation_draft": "The system can prepare a handoff draft when human review is required.",
        "draft_follow_up_action": "The system can prepare a reviewable next-step draft instead of acting autonomously.",
        "draft_knowledge_gap": "The system can prepare a reviewable knowledge-gap draft instead of publishing new content.",
        "record_conversation_memory": "Memory writes should be deliberate, reviewable, and policy constrained.",
        "record_artifact_memory": "Artifact memory writes should remain reviewable and provenance-aware before later V3 activation.",
        # ── Plugin: research_mentoring ──
        "retrieve_research_overview": "Plugin step retrieves the lab research overview to ground mentoring advice.",
        "match_research_direction": "Plugin step matches student interests to available research directions.",
        "retrieve_reading_methodology": "Plugin step retrieves paper-reading methodology to guide literature review.",
        "draft_research_plan": "Plugin step drafts a structured research plan for owner review.",
        # ── Plugin: meeting_prep ──
        "retrieve_team_schedule": "Plugin step retrieves team schedule context for meeting preparation.",
        "retrieve_blocker_memory": "Plugin step retrieves prior blockers and unresolved items for the agenda.",
        "retrieve_follow_up_artifacts": "Plugin step retrieves prior follow-up artifacts to carry context forward.",
        "draft_meeting_agenda": "Plugin step drafts a structured meeting agenda for owner review.",
        # ── Plugin: thesis_review ──
        "retrieve_paper_digest": "Plugin step retrieves paper digest entries for quick review grounding.",
        "retrieve_paper_writing_guidance": "Plugin step retrieves writing-course materials to inform review quality.",
        "generate_review_checklist": "Plugin step generates a structured review checklist based on the paper type.",
        "draft_review_comments": "Plugin step drafts structured review comments for owner review.",
        # ── Plugin: course_advising ──
        "retrieve_courseware_index": "Plugin step retrieves the courseware index to ground course recommendations.",
        "retrieve_teaching_resources": "Plugin step retrieves public teaching resources for advising context.",
        "draft_course_plan": "Plugin step drafts a personalized course plan for owner review.",
        # ── Plugin: paper_feedback ──
        "retrieve_writing_rubric": "Plugin step retrieves the writing rubric to ground feedback criteria.",
        "generate_structured_critique": "Plugin step generates structured multi-dimension feedback.",
        "draft_revision_notes": "Plugin step drafts revision notes for owner review.",
    }
    # Fall back to a generic description for unregistered plugin steps
    if definition.step_id in mapping:
        return mapping[definition.step_id]
    return f"Step {definition.step_id} is part of an enabled capability plugin for {goal}."


def _looks_like_admin_only_request(question: str) -> bool:
    markers = ("管理员", "admin", "添加知识", "删除知识", "publish knowledge", "后台")
    return any(marker in question for marker in markers)


def _looks_like_booking_preparation(question: str) -> bool:
    preparation_markers = ("准备什么", "先准备", "提前准备", "预约前", "meeting prep")
    return any(marker in question for marker in preparation_markers)


def _looks_like_artifact_record_request(question: str) -> bool:
    record_markers = (
        "记录成",
        "记录为",
        "归档",
        "存档",
        "保存成",
        "保存为",
        "归纳成后续材料",
        "follow-up material",
        "archive this",
        "save this draft",
    )
    artifact_markers = (
        "附件",
        "上传",
        "proposal",
        "agenda",
        "draft",
        "材料",
        "文档",
        "notes",
        "outline",
    )
    return any(marker in question for marker in record_markers) and any(
        marker in question for marker in artifact_markers
    )


def _looks_like_booking_request(question: str) -> bool:
    request_markers = (
        "预约",
        "约时间",
        "约个时间",
        "约老师",
        "book a meeting",
        "office hour",
    )
    if _looks_like_booking_preparation(question):
        return False
    return any(marker in question for marker in request_markers)


def _looks_like_research_question(question: str) -> bool:
    research_markers = (
        "研究",
        "research",
        "paper",
        "collaboration",
        "proposal",
        "实验计划",
        "milestone",
        "blocker",
        "开题",
        "project",
        "方向",
        "sage",
        "icml",
        "工作流系统",
        "workflow system",
        "llm inference",
        "推理引擎",
    )
    return any(marker in question for marker in research_markers)


def _should_include_profile_memory(context: WorkflowRequestContext) -> bool:
    if not context.profile_memory_available or not context.consent_profile_memory:
        return False

    question = context.question.lower()
    profile_markers = (
        "之前",
        "上次",
        "记得",
        "偏好",
        "习惯",
        "背景",
        "刚才",
        "上下文",
        "沟通偏好",
        "预约习惯",
        "联系方式",
        "邮箱",
        "名字",
    )
    return any(marker in question for marker in profile_markers)


def _should_include_recent_memory(context: WorkflowRequestContext) -> bool:
    if not context.recent_memory_available:
        return False

    if not context.recent_session_context_attached:
        return True

    question = context.question.lower()
    expanded_history_markers = (
        "之前",
        "上次",
        "更早",
        "历史",
        "长期",
        "一直",
        "连续",
        "一路",
        "回顾",
        "总结",
        "梳理",
        "整理",
        "多次",
        "一贯",
        "总共",
        "所有",
        "记得",
    )
    return any(marker in question for marker in expanded_history_markers)


def _should_include_artifact_memory(context: WorkflowRequestContext) -> bool:
    if not context.artifact_context_available:
        return False

    question = context.question.lower()
    artifact_markers = (
        "附件",
        "上传",
        "upload",
        "agenda",
        "proposal",
        "draft",
        "pdf",
        "notes",
        "slide",
        "outline",
        "开题",
        "meeting notes",
    )
    return context.attachment_count > 0 or any(
        marker in question for marker in artifact_markers
    )


def _looks_like_simple_greeting(question: str) -> bool:
    normalized = re.sub(r"\s+", "", question)
    return normalized in {"你好", "您好", "hello", "hi", "早上好"}


# ── Plugin routing helpers ──────────────────────────────────────────────────


def _looks_like_research_mentoring(question: str) -> bool:
    markers = (
        "研究方向",
        "选题",
        "研究计划",
        "文献阅读",
        "怎么读论文",
        "入门",
        "新生",
        "research direction",
        "research plan",
        "how to read",
        "topic selection",
    )
    return any(marker in question for marker in markers)


def _looks_like_thesis_review(question: str) -> bool:
    markers = (
        "审阅",
        "审稿",
        "评审",
        "修改意见",
        "review",
        "reviewer",
        "thesis review",
        "学位论文",
        "论文评审",
        "peer review",
    )
    return any(marker in question for marker in markers)


def _looks_like_course_advising(question: str) -> bool:
    markers = (
        "选课",
        "修课",
        "课程推荐",
        "先修",
        "培养方案",
        "课程计划",
        "course plan",
        "course selection",
        "prerequisite",
        "which course",
    )
    return any(marker in question for marker in markers)


def _looks_like_paper_feedback(question: str) -> bool:
    markers = (
        "批改",
        "打分",
        "评分",
        "写作建议",
        "feedback",
        "rubric",
        "writing feedback",
        "修改建议",
        "论文反馈",
        "paper feedback",
    )
    return any(marker in question for marker in markers)
