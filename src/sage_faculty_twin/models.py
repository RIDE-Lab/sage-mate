from __future__ import annotations

from datetime import date, datetime
from typing import Any

from pydantic import BaseModel, Field, field_validator, model_validator


class ChatAttachment(BaseModel):
    file_name: str = Field(min_length=1, max_length=256)
    media_type: str = Field(min_length=1, max_length=128)
    text_content: str = Field(min_length=1, max_length=12000)
    size_bytes: int | None = Field(default=None, ge=0)


class ChatRequest(BaseModel):
    student_name: str = Field(min_length=1, max_length=128)
    student_email: str | None = Field(default=None, max_length=256)
    question: str = Field(min_length=1, max_length=4000)
    course_context: str | None = Field(default=None, max_length=512)
    visitor_profile: str | None = Field(
        default=None,
        max_length=64,
        pattern="^(hust_undergraduate|paper_writing_student|lab_member|general_visitor)$",
    )
    conversation_id: str | None = Field(default=None, max_length=128)
    attachments: list[ChatAttachment] = Field(default_factory=list, max_length=4)
    deep_thinking: bool = Field(default=True)
    deep_thinking_explicit: bool = Field(default=False)
    web_search: bool = Field(default=False)
    code_approval_mode: str = Field(default="ask", pattern="^(ask|auto|full)$")


class OnlinePresenceHeartbeatRequest(BaseModel):
    client_id: str = Field(min_length=1, max_length=128)
    conversation_id: str | None = Field(default=None, max_length=128)
    student_email: str | None = Field(default=None, max_length=256)
    is_authenticated: bool = Field(default=False)


class OnlinePresenceHeartbeatResponse(BaseModel):
    window_seconds: int = Field(ge=60)
    online_visitors: int = Field(ge=0)
    online_authenticated_users: int = Field(ge=0)
    active_conversations: int = Field(ge=0)


class InteractionIntent(BaseModel):
    action: str = Field(
        default="answer",
        pattern="^(answer|book_meeting|ask_followup|review_queue|human_handoff|admin_add_knowledge)$",
    )
    domain: str = Field(default="general", pattern="^(general|research|teaching|advising|booking)$")
    retrieval_scopes: list[str] = Field(default_factory=list)
    exclude_scopes: list[str] = Field(default_factory=list)
    decision_mode: str = Field(
        default="direct_answer",
        pattern="^(direct_answer|advise_only|review_queue|human_handoff)$",
    )
    needs_clarification: bool = False
    clarification_message: str | None = Field(default=None, max_length=512)
    escalation_reason: str | None = Field(default=None, max_length=512)
    confidence: float = Field(default=0.0, ge=0.0, le=1.0)


class WorkflowTraceStep(BaseModel):
    key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=256)
    detail: str = Field(min_length=1, max_length=512)
    status: str = Field(default="completed", pattern="^(completed|skipped)$")
    duration_ms: int | None = Field(default=None, ge=0)
    # Identifies the DAG fan-out branch this step belongs to. ``None`` means
    # the step is part of the linear backbone and renders as a single chip in
    # the workflow rail. Steps that share the same non-null ``parallel_group``
    # ran concurrently in the chat DAG (e.g. memory + knowledge retrieval, or
    # the four post-answer side-effect stages) and are visualised together as
    # a vertical fan-out cluster on the client. Kept short so it round-trips
    # cheaply in streaming partial traces.
    parallel_group: str | None = Field(default=None, max_length=32)


class AnswerBasisItem(BaseModel):
    basis_label: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    source_label: str = Field(min_length=1, max_length=256)
    detail: str = Field(min_length=1, max_length=1000)


class FollowUpAction(BaseModel):
    action_id: str | None = None
    booking_id: str | None = None
    action_type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    detail: str = Field(min_length=1, max_length=512)
    channel: str = Field(default="chat", pattern="^(chat|email)$")
    status: str = Field(default="suggested", pattern="^(suggested|queued|pending|sent|skipped)$")
    source_label: str | None = Field(default=None, max_length=256)
    due_at: datetime | None = None


class FollowUpQueueRecord(BaseModel):
    action_id: str
    booking_id: str | None = None
    student_name: str
    student_email: str
    action_type: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    detail: str = Field(min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=256)
    status: str = Field(pattern="^(queued|sent|skipped)$")
    due_at: datetime | None = None
    created_at: datetime
    sent_at: datetime | None = None


class FollowUpDispatchResponse(BaseModel):
    processed_count: int = 0
    sent_count: int = 0
    pending_count: int = 0


class EscalationRecord(BaseModel):
    escalation_id: str
    conversation_id: str
    student_name: str
    student_email: str | None = None
    course_context: str | None = None
    question: str
    route: str = Field(pattern="^(review_queue|human_handoff)$")
    status: str = Field(pattern="^(待处理|已处理)$")
    reason: str | None = Field(default=None, max_length=512)
    resolution_note: str | None = Field(default=None, max_length=512)
    created_at: datetime
    resolved_at: datetime | None = None


class EscalationDecisionRequest(BaseModel):
    resolution_note: str | None = Field(default=None, max_length=512)


class NotificationDeliveryStatus(BaseModel):
    channel: str = Field(default="email", pattern="^(email)$")
    status: str = Field(default="skipped", pattern="^(sent|failed|skipped)$")
    recipient: str | None = Field(default=None, max_length=256)
    summary: str = Field(min_length=1, max_length=256)
    detail: str | None = Field(default=None, max_length=512)


class WorkflowPlanPreview(BaseModel):
    planner_version: str = Field(min_length=1, max_length=32)
    policy_version: str = Field(min_length=1, max_length=64)
    planner_mode: str = Field(min_length=1, max_length=32)
    execution_mode: str = Field(min_length=1, max_length=32)
    goal: str = Field(min_length=1, max_length=128)
    accepted: bool = True
    fallback_template: str = Field(min_length=1, max_length=64)
    fallback_reason: str | None = Field(default=None, max_length=256)
    planned_steps: list[str] = Field(default_factory=list)
    validation_errors: list[str] = Field(default_factory=list)
    explain_to_operator: str = Field(min_length=1, max_length=512)


class WorkflowPlanComparison(BaseModel):
    comparison_status: str = Field(
        pattern="^(shadow_disabled|shadow_error|equivalent|different_steps|different_goal)$"
    )
    same_goal: bool = True
    same_fallback_template: bool = True
    shared_steps: list[str] = Field(default_factory=list)
    deterministic_only_steps: list[str] = Field(default_factory=list)
    shadow_only_steps: list[str] = Field(default_factory=list)
    summary: str = Field(min_length=1, max_length=512)


class MemoryAuditItem(BaseModel):
    entry_id: str = Field(min_length=1, max_length=128)
    memory_type: str = Field(pattern="^(short_term|long_term)$")
    source: str = Field(min_length=1, max_length=64)
    topic: str = Field(min_length=1, max_length=64)
    source_label: str = Field(min_length=1, max_length=128)
    summary: str = Field(min_length=1, max_length=1200)
    score: float | None = None


class WebSearchHit(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    url: str = Field(min_length=1, max_length=1000)
    snippet: str = Field(default="", max_length=500)
    score: float = Field(default=0.0)


class KnowledgeWriteBackResult(BaseModel):
    document_id: str
    title: str = Field(min_length=1, max_length=256)
    source_name: str | None = Field(default=None, max_length=256)
    created: bool = True


class TokenUsage(BaseModel):
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    max_context_length: int = 0


class ChatRequestTiming(BaseModel):
    """Request-boundary timings used to reconcile UI latency with workflow work."""

    trace_id: str = Field(min_length=1, max_length=128)
    route: str = Field(pattern="^(boundary|fast_path|sage_workflow)$")
    total_duration_ms: float = Field(ge=0)
    budget_ms: float = Field(gt=0)
    remaining_budget_ms: float = Field(ge=0)
    stage_durations_ms: dict[str, float] = Field(default_factory=dict)
    workflow_trace_reported_ms: float = Field(default=0, ge=0)
    accounted_duration_ms: float = Field(default=0, ge=0)
    unattributed_duration_ms: float = Field(default=0, ge=0)
    llm_call_count: int = Field(default=0, ge=0)
    llm_retry_count: int = Field(default=0, ge=0)
    llm_ttft_ms: float | None = Field(default=None, ge=0)
    llm_total_duration_ms: float = Field(default=0, ge=0)
    llm_cache_hits: int = Field(default=0, ge=0)
    llm_cache_misses: int = Field(default=0, ge=0)


class ChatResponse(BaseModel):
    answer: str
    owner_name: str
    used_model: str
    exchange_id: str | None = None
    knowledge_hits: list[KnowledgeSearchHit] = Field(default_factory=list)
    web_search_hits: list[WebSearchHit] = Field(default_factory=list)
    answer_basis: list[AnswerBasisItem] = Field(default_factory=list)
    follow_up_actions: list[FollowUpAction] = Field(default_factory=list)
    conversation_id: str | None = None
    workflow_action: str = Field(default="answer")
    decision_mode: str = Field(default="direct_answer")
    pending_fields: list[str] = Field(default_factory=list)
    booking_result: BookingResponse | None = None
    escalation_record: EscalationRecord | None = None
    planner_preview: WorkflowPlanPreview | None = None
    shadow_planner_preview: WorkflowPlanPreview | None = None
    planner_comparison: WorkflowPlanComparison | None = None
    workflow_trace: list[WorkflowTraceStep] = Field(default_factory=list)
    memory_used: bool = False
    memory_write_back: bool = False
    retrieved_items: list[MemoryAuditItem] = Field(default_factory=list)
    token_usage: TokenUsage | None = None
    request_timing: ChatRequestTiming | None = None


class ConversationHistoryItemResponse(BaseModel):
    conversation_id: str
    title: str = Field(min_length=1, max_length=256)
    preview: str = Field(min_length=1, max_length=512)
    student_name: str = Field(min_length=1, max_length=128)
    student_email: str | None = Field(default=None, max_length=256)
    course_context: str | None = Field(default=None, max_length=512)
    exchange_count: int = Field(ge=1)
    last_message_at: datetime


class ConversationHistoryListResponse(BaseModel):
    conversations: list[ConversationHistoryItemResponse] = Field(default_factory=list)


class ConversationExchangeResponse(BaseModel):
    exchange_id: str
    question: str = Field(min_length=1, max_length=4000)
    answer: str = Field(min_length=1, max_length=12000)
    workflow_action: str = Field(min_length=1, max_length=64)
    knowledge_hit_count: int = Field(ge=0)
    created_at: datetime


class ConversationTranscriptResponse(BaseModel):
    conversation_id: str
    title: str = Field(min_length=1, max_length=256)
    preview: str = Field(min_length=1, max_length=512)
    student_name: str = Field(min_length=1, max_length=128)
    student_email: str | None = Field(default=None, max_length=256)
    course_context: str | None = Field(default=None, max_length=512)
    exchanges: list[ConversationExchangeResponse] = Field(default_factory=list)


class BookingRequest(BaseModel):
    student_name: str = Field(min_length=1, max_length=128)
    student_email: str = Field(min_length=3, max_length=256)
    topic: str = Field(min_length=1, max_length=256)
    preferred_start: datetime
    preferred_end: datetime


class BookingRecord(BaseModel):
    booking_id: str
    student_name: str
    student_email: str
    topic: str
    start_at: datetime
    end_at: datetime
    status: str
    rejection_reason: str | None = Field(default=None, max_length=512)


class BookingResponse(BaseModel):
    accepted: bool
    message: str
    booking: BookingRecord | None = None
    alternative_slots: list[str] = Field(default_factory=list)
    notification: NotificationDeliveryStatus | None = None


class BookingStatusFilter(BaseModel):
    status: str | None = Field(default=None, max_length=32)


class BookingDecisionRequest(BaseModel):
    rejection_reason: str | None = Field(default=None, max_length=512)


class AvailabilityWindow(BaseModel):
    start: str = Field(pattern=r"^\d{2}:\d{2}$")
    end: str = Field(pattern=r"^\d{2}:\d{2}$")

    @field_validator("start", "end")
    @classmethod
    def validate_clock_text(cls, value: str) -> str:
        hour, minute = value.split(":", 1)
        hour_int = int(hour)
        minute_int = int(minute)
        if not (0 <= hour_int <= 23 and 0 <= minute_int <= 59):
            raise ValueError("time must be within 00:00-23:59")
        return f"{hour_int:02d}:{minute_int:02d}"


class AvailabilityDay(BaseModel):
    date: date
    windows: list[AvailabilityWindow] = Field(default_factory=list)
    note: str | None = Field(default=None, max_length=256)


class AvailabilitySchedule(BaseModel):
    week_of: date | None = None
    timezone: str | None = Field(default=None, max_length=64)
    days: list[AvailabilityDay] = Field(default_factory=list)


class KnowledgeDocumentCreate(BaseModel):
    title: str = Field(min_length=1, max_length=256)
    content: str = Field(min_length=1, max_length=20000)
    tags: list[str] = Field(default_factory=list, max_length=16)
    source_name: str | None = Field(default=None, max_length=256)
    metadata: dict[str, str] = Field(default_factory=dict)


class KnowledgeDocumentRecord(BaseModel):
    document_id: str
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source_name: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)
    created_at: datetime
    is_feedback_web: bool = False
    review_status: str = Field(default="unknown", pattern="^(unknown|pending|approved|stale)$")
    freshness_status: str = Field(default="unknown", pattern="^(unknown|web|stale)$")
    reviewed_at: datetime | None = None
    reviewable: bool = False

    @model_validator(mode="after")
    def _sync_review_state(self) -> KnowledgeDocumentRecord:
        source_name = str(self.source_name or "").lower()
        tags = {str(tag).lower() for tag in self.tags}
        is_feedback_web = source_name.startswith("feedback-web:") or "feedback-web" in tags

        review_status = str(self.metadata.get("review_status") or "").strip().lower()
        if review_status not in {"unknown", "pending", "approved", "stale"}:
            review_status = "pending" if is_feedback_web else "unknown"
        if not review_status:
            review_status = "pending" if is_feedback_web else "unknown"

        freshness_status = str(self.metadata.get("freshness_status") or "").strip().lower()
        if freshness_status not in {"unknown", "web", "stale"}:
            freshness_status = (
                "web"
                if review_status == "approved"
                else "stale"
                if review_status == "stale"
                else "unknown"
            )

        reviewed_at = self.reviewed_at
        reviewed_at_raw = self.metadata.get("reviewed_at")
        if reviewed_at is None and reviewed_at_raw:
            try:
                reviewed_at = datetime.fromisoformat(reviewed_at_raw)
            except ValueError:
                reviewed_at = None

        self.is_feedback_web = is_feedback_web
        self.review_status = review_status
        self.freshness_status = freshness_status
        self.reviewed_at = reviewed_at
        self.reviewable = is_feedback_web and review_status == "pending"
        return self


class KnowledgeDocumentReviewRequest(BaseModel):
    action: str = Field(pattern="^(approve|stale)$")


class KnowledgeDocumentReviewSummary(BaseModel):
    total_documents: int = 0
    feedback_web_documents: int = 0
    pending_documents: int = 0
    approved_documents: int = 0
    stale_documents: int = 0
    reviewable_documents: int = 0
    pending_items: list[KnowledgeDocumentRecord] = Field(default_factory=list)


class KnowledgeDocumentActionResponse(BaseModel):
    document_id: str
    action: str
    document: KnowledgeDocumentRecord | None = None
    deleted_count: int = 0


class KnowledgeSearchHit(BaseModel):
    document_id: str
    title: str
    excerpt: str
    score: float
    tags: list[str] = Field(default_factory=list)
    source_name: str | None = None
    metadata: dict[str, str] = Field(default_factory=dict)


class KnowledgeSearchResponse(BaseModel):
    hits: list[KnowledgeSearchHit] = Field(default_factory=list)


class MemoryProfileRecordResponse(BaseModel):
    profile_id: str
    student_key: str
    student_name: str
    student_email: str | None = None
    category: str
    summary: str
    evidence: str
    updated_at: datetime


class MemoryProfileListResponse(BaseModel):
    available_categories: list[str] = Field(default_factory=list)
    category_counts: dict[str, int] = Field(default_factory=dict)
    profiles: list[MemoryProfileRecordResponse] = Field(default_factory=list)


class ChatFeedbackRequest(BaseModel):
    exchange_id: str = Field(min_length=1, max_length=128)
    rating: str = Field(pattern="^(up|down)$")
    resolved: bool | None = None
    needs_human_followup: bool = False
    issue_summary: str | None = Field(default=None, max_length=512)


class ChatFeedbackResponse(BaseModel):
    exchange_id: str
    rating: str
    resolved: bool
    needs_human_followup: bool
    issue_summary: str | None = None
    knowledge_write_backs: list[KnowledgeWriteBackResult] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class AnonymousSuggestionCreate(BaseModel):
    message: str = Field(min_length=1, max_length=1000)
    category: str | None = Field(default=None, max_length=64)


class AnonymousSuggestionRecord(BaseModel):
    suggestion_id: str
    message: str
    category: str | None = None
    created_at: datetime


class QuestionAnalyticsOverview(BaseModel):
    total_exchanges: int = 0
    feedback_count: int = 0
    positive_feedback_count: int = 0
    negative_feedback_count: int = 0
    unresolved_count: int = 0
    human_handoff_count: int = 0


class QuestionClusterSummary(BaseModel):
    cluster_id: str
    label: str
    interaction_domain: str
    interaction_domain_label: str
    count: int
    unresolved_count: int = 0
    negative_feedback_count: int = 0
    human_handoff_count: int = 0
    sample_questions: list[str] = Field(default_factory=list)


class KnowledgeGapSuggestion(BaseModel):
    cluster_id: str
    label: str
    interaction_domain: str
    reason: str
    suggested_action: str
    supporting_signals: dict[str, int] = Field(default_factory=dict)
    sample_questions: list[str] = Field(default_factory=list)
    draft_id: str | None = None
    draft_status: str | None = None


class UnresolvedQuestionItem(BaseModel):
    exchange_id: str
    student_name: str
    question: str
    interaction_domain: str
    interaction_domain_label: str
    issue_summary: str | None = None
    needs_human_followup: bool = False
    created_at: datetime


class HandoffCategorySummary(BaseModel):
    category: str
    category_label: str
    count: int
    share: float
    sample_questions: list[str] = Field(default_factory=list)


class QuestionAnalyticsReportResponse(BaseModel):
    window_days: int
    window_start: datetime
    window_end: datetime
    overview: QuestionAnalyticsOverview
    top_clusters: list[QuestionClusterSummary] = Field(default_factory=list)
    knowledge_gap_suggestions: list[KnowledgeGapSuggestion] = Field(default_factory=list)
    unresolved_questions: list[UnresolvedQuestionItem] = Field(default_factory=list)
    handoff_categories: list[HandoffCategorySummary] = Field(default_factory=list)


class OperationsQueueSummary(BaseModel):
    queue_key: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=128)
    open_count: int = Field(default=0, ge=0)
    total_count: int = Field(default=0, ge=0)
    action_url: str = Field(min_length=1, max_length=128)


class NeuroMemOperationsSnapshot(BaseModel):
    backend: str = Field(default="")
    conversation_stats: dict[str, Any] = Field(default_factory=dict)
    profile_stats: dict[str, Any] = Field(default_factory=dict)
    recent_events: list[dict[str, Any]] = Field(default_factory=list)


class PlannerMetricsSnapshot(BaseModel):
    record_count: int = 0
    deterministic_total: int = 0
    deterministic_accepted: int = 0
    deterministic_fallbacks: int = 0
    deterministic_acceptance_rate: float = 0.0
    deterministic_fallback_rate: float = 0.0
    shadow_total: int = 0
    shadow_ready: int = 0
    shadow_accepted: int = 0
    shadow_rejected: int = 0
    shadow_disabled: int = 0
    shadow_errors: int = 0
    shadow_acceptance_rate: float = 0.0
    shadow_error_rate: float = 0.0
    avg_deterministic_latency_ms: float = 0.0
    avg_shadow_latency_ms: float = 0.0
    max_deterministic_latency_ms: float = 0.0
    max_shadow_latency_ms: float = 0.0
    rejection_reasons: dict[str, int] = Field(default_factory=dict)
    rejected_steps: dict[str, int] = Field(default_factory=dict)
    fallback_templates: dict[str, int] = Field(default_factory=dict)


class OperationsOverviewResponse(BaseModel):
    generated_at: datetime
    window_days: int
    health: dict[str, str] = Field(default_factory=dict)
    totals: dict[str, int] = Field(default_factory=dict)
    queues: list[OperationsQueueSummary] = Field(default_factory=list)
    question_analytics: QuestionAnalyticsOverview
    neuromem: NeuroMemOperationsSnapshot = Field(default_factory=NeuroMemOperationsSnapshot)
    planner_metrics: PlannerMetricsSnapshot = Field(default_factory=PlannerMetricsSnapshot)


class StudentOperationsProfile(BaseModel):
    student_key: str
    student_name: str
    student_email: str | None = None
    segment: str
    profile_count: int = Field(default=0, ge=0)
    interaction_count: int = Field(default=0, ge=0)
    categories: list[str] = Field(default_factory=list)
    recent_questions: list[str] = Field(default_factory=list)
    key_summaries: list[MemoryProfileRecordResponse] = Field(default_factory=list)
    suggested_next_action: str
    latest_profile_at: datetime
    latest_interaction_at: datetime | None = None


class OperationsTaskStateUpdateRequest(BaseModel):
    status: str | None = Field(default=None, pattern="^(open|in_progress|done|deferred)$")
    assigned_to: str | None = Field(default=None, max_length=128)
    note: str | None = Field(default=None, max_length=1000)


class RuntimeFeatureFlagsResponse(BaseModel):
    shadow_planner_enabled: bool
    updated_at: datetime | None = None


class RuntimeFeatureFlagsUpdateRequest(BaseModel):
    shadow_planner_enabled: bool


class OperationsTaskStateRecord(BaseModel):
    task_key: str
    status: str = Field(pattern="^(open|in_progress|done|deferred)$")
    assigned_to: str | None = None
    note: str | None = None
    updated_at: datetime


class OperationsTaskItem(BaseModel):
    task_key: str
    task_type: str
    title: str
    detail: str
    source_status: str
    operations_status: str = Field(pattern="^(open|in_progress|done|deferred)$")
    priority: int = Field(default=0, ge=0, le=100)
    action_url: str
    student_name: str | None = None
    student_email: str | None = None
    assigned_to: str | None = None
    note: str | None = None
    created_at: datetime | None = None
    due_at: datetime | None = None


class SatisfactionReasonSummary(BaseModel):
    reason_key: str
    reason_label: str
    count: int = Field(default=0, ge=0)
    share: float = Field(default=0.0, ge=0.0, le=1.0)
    sample_issues: list[str] = Field(default_factory=list)


class SatisfactionTrendPoint(BaseModel):
    date: date
    feedback_count: int = Field(default=0, ge=0)
    positive_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    unresolved_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    human_handoff_rate: float = Field(default=0.0, ge=0.0, le=1.0)


class OperationsSatisfactionSummary(BaseModel):
    feedback_count: int = Field(default=0, ge=0)
    positive_count: int = Field(default=0, ge=0)
    negative_count: int = Field(default=0, ge=0)
    positive_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    unresolved_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    human_handoff_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    feedback_coverage_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    reason_summaries: list[SatisfactionReasonSummary] = Field(default_factory=list)
    trend: list[SatisfactionTrendPoint] = Field(default_factory=list)


class OperationsWorkbenchResponse(BaseModel):
    overview: OperationsOverviewResponse
    operational_tasks: list[OperationsTaskItem] = Field(default_factory=list)
    satisfaction: OperationsSatisfactionSummary
    pending_bookings: list[BookingRecord] = Field(default_factory=list)
    student_profiles: list[StudentOperationsProfile] = Field(default_factory=list)
    artifact_memory_drafts: list[ArtifactMemoryDraftRecordResponse] = Field(default_factory=list)
    knowledge_gap_drafts: list[KnowledgeGapDraftRecordResponse] = Field(default_factory=list)
    escalations: list[EscalationRecord] = Field(default_factory=list)
    follow_up_actions: list[FollowUpQueueRecord] = Field(default_factory=list)
    anonymous_suggestions: list[AnonymousSuggestionRecord] = Field(default_factory=list)
    question_analytics: QuestionAnalyticsReportResponse


class WorkflowReplayScenarioResultResponse(BaseModel):
    scenario_id: str = Field(min_length=1, max_length=64)
    title: str = Field(min_length=1, max_length=256)
    passed: bool
    accepted: bool
    goal: str = Field(min_length=1, max_length=128)
    fallback_template: str = Field(min_length=1, max_length=64)
    step_ids: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)


class WorkflowReplayReportResponse(BaseModel):
    generated_at: datetime
    planner_version: str = Field(min_length=1, max_length=32)
    policy_version: str = Field(min_length=1, max_length=64)
    scenario_source: str = Field(min_length=1, max_length=512)
    total_scenarios: int = Field(default=0, ge=0)
    passed_scenarios: int = Field(default=0, ge=0)
    failed_scenarios: int = Field(default=0, ge=0)
    results: list[WorkflowReplayScenarioResultResponse] = Field(default_factory=list)


class KnowledgeGapDraftCreateRequest(BaseModel):
    cluster_id: str = Field(min_length=1, max_length=128)
    days: int = Field(default=7, ge=1, le=90)


class KnowledgeGapDraftRecordResponse(BaseModel):
    draft_id: str
    cluster_id: str
    interaction_domain: str
    label: str
    reason: str
    suggested_action: str
    sample_questions: list[str] = Field(default_factory=list)
    title: str
    content: str
    tags: list[str] = Field(default_factory=list)
    source_name: str
    status: str
    published_document_id: str | None = None
    created_at: datetime
    updated_at: datetime
    published_at: datetime | None = None


class ArtifactMemoryDraftRecordResponse(BaseModel):
    draft_id: str
    conversation_id: str
    source_memory_id: str | None = None
    student_name: str
    student_email: str | None = None
    interaction_domain: str | None = None
    question: str
    answer: str
    artifact_names: list[str] = Field(default_factory=list)
    artifact_sources: list[str] = Field(default_factory=list)
    artifact_excerpt_count: int = Field(default=0, ge=0)
    provenance_note: str
    retention_label: str
    status: str
    created_at: datetime
    updated_at: datetime


class AdminLoginRequest(BaseModel):
    username: str = Field(min_length=1, max_length=128)
    password: str = Field(min_length=1, max_length=256)


class UserRegisterRequest(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    email: str = Field(min_length=3, max_length=256)
    visitor_profile: str = Field(
        pattern="^(hust_undergraduate|paper_writing_student|lab_member|general_visitor)$"
    )
    password: str = Field(min_length=8, max_length=256)
    invitation_code: str = Field(default="", max_length=128)


class UserLoginRequest(BaseModel):
    email: str = Field(min_length=3, max_length=256)
    password: str = Field(min_length=1, max_length=256)
    invitation_code: str = Field(default="", max_length=128)


class UserAccountResponse(BaseModel):
    user_id: str
    name: str
    email: str
    visitor_profile: str = Field(
        pattern="^(hust_undergraduate|paper_writing_student|lab_member|general_visitor)$"
    )
    created_at: datetime


class UserSessionResponse(BaseModel):
    is_authenticated: bool
    mode: str = Field(pattern="^(guest|user)$")
    account: UserAccountResponse | None = None


class SlackTwinLinkStatusResponse(BaseModel):
    is_authenticated: bool
    is_lab_member: bool
    can_link: bool
    linked: bool = False
    slack_user_id: str | None = Field(default=None, max_length=64)
    linked_at: datetime | None = None
    message: str = Field(min_length=1, max_length=256)


class SlackTwinLinkCodeResponse(BaseModel):
    is_authenticated: bool
    is_lab_member: bool
    can_link: bool
    code: str | None = Field(default=None, max_length=16)
    expires_at: datetime | None = None
    message: str = Field(min_length=1, max_length=256)


class AdminSessionResponse(BaseModel):
    is_admin: bool
    mode: str = Field(pattern="^(user|admin)$")
    username: str | None = None
    role: str | None = Field(default=None, pattern="^(super_admin|manager)$")


class ManagedServiceStatus(BaseModel):
    name: str = Field(min_length=1, max_length=64)
    unit: str = Field(min_length=1, max_length=128)
    active_state: str = Field(min_length=1, max_length=64)
    sub_state: str = Field(min_length=1, max_length=64)
    description: str | None = Field(default=None, max_length=256)


class ServiceControlResponse(BaseModel):
    action: str = Field(pattern="^(status|start|stop|restart)$")
    success: bool
    message: str = Field(min_length=1, max_length=512)
    services: list[ManagedServiceStatus] = Field(default_factory=list)


class CodeWorkspaceSummary(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64)
    label: str = Field(min_length=1, max_length=128)
    root: str = Field(min_length=1, max_length=1000)
    exists: bool = True


class CodeWorkspaceListResponse(BaseModel):
    workspaces: list[CodeWorkspaceSummary] = Field(default_factory=list)


class CodeSearchRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64)
    query: str = Field(min_length=1, max_length=256)
    glob: str | None = Field(default=None, max_length=128)
    max_results: int = Field(default=20, ge=1, le=100)


class CodeSearchHit(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    line_number: int = Field(ge=1)
    preview: str = Field(default="", max_length=1000)


class CodeSearchResponse(BaseModel):
    workspace_id: str
    query: str
    hits: list[CodeSearchHit] = Field(default_factory=list)
    truncated: bool = False


class CodeFileReadRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64)
    path: str = Field(min_length=1, max_length=1000)
    start_line: int = Field(default=1, ge=1)
    max_lines: int = Field(default=200, ge=1, le=1000)


class CodeFileReadResponse(BaseModel):
    workspace_id: str
    path: str
    start_line: int = Field(ge=1)
    end_line: int = Field(ge=0)
    content: str
    truncated: bool = False


class CodeDirectoryListRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64)
    path: str = Field(default=".", min_length=1, max_length=1000)
    max_entries: int = Field(default=200, ge=1, le=1000)


class CodeDirectoryEntry(BaseModel):
    path: str = Field(min_length=1, max_length=1000)
    name: str = Field(min_length=1, max_length=256)
    kind: str = Field(pattern="^(file|directory|symlink|other)$")
    size_bytes: int | None = Field(default=None, ge=0)
    modified_at: datetime | None = None
    hidden: bool = False


class CodeDirectoryListResponse(BaseModel):
    workspace_id: str
    path: str
    entries: list[CodeDirectoryEntry] = Field(default_factory=list)
    truncated: bool = False


class CodeGitStatusRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64)


class CodeGitStatusResponse(BaseModel):
    workspace_id: str
    is_git_repo: bool
    branch: str = Field(default="", max_length=256)
    clean: bool = False
    porcelain: str = Field(default="", max_length=20000)
    message: str = Field(default="", max_length=512)


class CodeGitDiffRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64)
    path: str | None = Field(default=None, max_length=1000)
    staged: bool = False
    max_chars: int = Field(default=20000, ge=1000, le=50000)


class CodeGitDiffResponse(BaseModel):
    workspace_id: str
    path: str | None = None
    staged: bool = False
    diff: str = Field(default="", max_length=50000)
    truncated: bool = False
    message: str = Field(default="", max_length=512)


class CodeContextRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64)
    paths: list[str] = Field(default_factory=list, max_length=16)
    include_git_status: bool = True
    max_context_chars: int = Field(default=20000, ge=1000, le=50000)


class CodeContextResponse(BaseModel):
    workspace_id: str
    context: str = Field(default="", max_length=50000)
    context_paths: list[str] = Field(default_factory=list)
    truncated: bool = False


class CodeCommandRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64)
    command: str = Field(min_length=1, max_length=1000)
    timeout_seconds: int | None = Field(default=None, ge=1, le=60)
    allow_write: bool = False
    code_approval_mode: str = Field(default="ask", pattern="^(ask|auto|full)$")


class CodeCommandResponse(BaseModel):
    workspace_id: str
    command: str
    exit_code: int
    stdout: str = Field(default="", max_length=20000)
    stderr: str = Field(default="", max_length=12000)
    duration_ms: int = Field(ge=0)
    timed_out: bool = False
    blocked: bool = False
    message: str = Field(default="", max_length=512)


class CodeAssistRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64)
    task: str = Field(min_length=1, max_length=4000)
    paths: list[str] = Field(default_factory=list, max_length=8)
    max_context_chars: int = Field(default=12000, ge=1000, le=30000)


class CodeAssistResponse(BaseModel):
    workspace_id: str
    answer: str
    context_paths: list[str] = Field(default_factory=list)
    used_model: str
    workflow_trace: list[WorkflowTraceStep] = Field(default_factory=list)


class CodeProposeRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64)
    task: str = Field(min_length=1, max_length=4000)
    paths: list[str] = Field(default_factory=list, max_length=8)
    max_context_chars: int = Field(default=20000, ge=2000, le=50000)


class CodeProposeResponse(BaseModel):
    workspace_id: str
    summary: str = Field(default="", max_length=4000)
    affected_files: list[str] = Field(default_factory=list, max_length=32)
    unified_diff: str = Field(default="", max_length=50000)
    risks: str = Field(default="", max_length=4000)
    suggested_tests: list[str] = Field(default_factory=list, max_length=32)
    proposal: str = Field(default="", max_length=60000)
    context_paths: list[str] = Field(default_factory=list)
    used_model: str
    workflow_trace: list[WorkflowTraceStep] = Field(default_factory=list)


class CodeSessionMessage(BaseModel):
    role: str = Field(pattern="^(user|assistant|system|tool)$")
    content: str = Field(min_length=1, max_length=20000)
    created_at: datetime | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class CodeSessionSummary(BaseModel):
    session_id: str = Field(min_length=1, max_length=128)
    workspace_id: str = Field(min_length=1, max_length=64)
    backend: str = Field(pattern="^(internal|claude_hust)$")
    title: str = Field(min_length=1, max_length=256)
    created_at: datetime
    updated_at: datetime
    message_count: int = Field(ge=0)
    last_proposal_summary: str | None = Field(default=None, max_length=4000)


class CodeSessionRecord(CodeSessionSummary):
    messages: list[CodeSessionMessage] = Field(default_factory=list, max_length=500)


class CodeSessionListResponse(BaseModel):
    sessions: list[CodeSessionSummary] = Field(default_factory=list)


class CodeSessionCreateRequest(BaseModel):
    workspace_id: str = Field(min_length=1, max_length=64)
    backend: str | None = Field(default=None, pattern="^(internal|claude_hust)$")
    title: str | None = Field(default=None, max_length=256)
    initial_message: CodeSessionMessage | None = None


class CodeSessionAppendMessageRequest(BaseModel):
    role: str = Field(pattern="^(user|assistant|system|tool)$")
    content: str = Field(min_length=1, max_length=20000)
    last_proposal_summary: str | None = Field(default=None, max_length=4000)
    metadata: dict[str, Any] = Field(default_factory=dict)


class LocalCodeConfigRequest(BaseModel):
    app_profile: str = Field(
        default="code_assistant",
        pattern="^(faculty_twin|code_assistant|auto_scientist)$",
    )
    llm_base_url: str = Field(min_length=1, max_length=1000)
    api_key: str | None = Field(default=None, max_length=4000)
    model_name: str | None = Field(default=None, max_length=256)
    runtime_dir: str = Field(min_length=1, max_length=1000)
    workspace_roots: list[str] = Field(default_factory=list, max_length=16)
    code_agent_backend: str = Field(default="internal", pattern="^(internal|claude_hust)$")
    claude_hust_cli_path: str | None = Field(default=None, max_length=1000)


class LocalCodeConfigResponse(BaseModel):
    app_name: str = "Sage Mate"
    app_profile: str
    deployment_mode: str
    code_workbench_enabled: bool
    llm_base_url: str
    api_key: str = ""
    api_key_set: bool
    model_name: str = ""
    runtime_dir: str
    workspace_roots: list[str] = Field(default_factory=list)
    code_agent_backend: str = "internal"
    claude_hust_cli_path: str = ""
    claude_hust_cli_available: bool = False
    claude_hust_cli_resolved_path: str = ""
    claude_hust_cli_issue: str = ""
    message: str = Field(default="", max_length=512)
