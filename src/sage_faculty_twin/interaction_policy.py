from __future__ import annotations

from dataclasses import dataclass

from .models import ChatRequest, InteractionIntent


_REVIEW_DECISION_MARKERS = (
    "破例",
    "例外",
    "延期",
    "审批",
    "审核",
    "批准",
    "推荐信",
    "推荐一下",
    "能不能给我",
)

_LAB_JOINING_MARKERS = (
    "加入课题组",
    "加入你们组",
    "加入您的组",
    "加入您的课题组",
)

_DIRECT_LAB_ACCEPTANCE_MARKERS = (
    "能收我吗",
    "能不能收我",
    "可以收我吗",
    "愿意收我吗",
)

_LAB_ACCEPTANCE_DECISION_MARKERS = (
    "可以吗",
    "行吗",
    "能吗",
    "同意",
    "批准",
    "收我",
    "接收我",
    "录取我",
    "给我名额",
    "给个名额",
    "保证名额",
    "承诺",
)


def requires_faculty_review(question: str) -> bool:
    """Return whether the user is asking the faculty owner for a decision.

    Questions about preparing to join a lab are answerable advising requests.
    A lab-joining question enters review only when it also asks for acceptance,
    permission, a slot, or another commitment that the assistant cannot make.
    """

    normalized = question.strip().lower()
    if any(marker in normalized for marker in _REVIEW_DECISION_MARKERS):
        return True
    if any(marker in normalized for marker in _DIRECT_LAB_ACCEPTANCE_MARKERS):
        return True
    asks_to_join = any(marker in normalized for marker in _LAB_JOINING_MARKERS)
    asks_for_decision = any(
        marker in normalized for marker in _LAB_ACCEPTANCE_DECISION_MARKERS
    )
    return asks_to_join and asks_for_decision


_HANDOFF_ACTION_MARKERS = (
    "我要投诉",
    "我想投诉",
    "正式投诉",
    "提出投诉",
    "我要申诉",
    "我想申诉",
    "正式申诉",
    "提出申诉",
    "我要举报",
    "我想举报",
    "正式举报",
    "提出举报",
    "转人工",
    "人工处理",
    "老师本人处理",
)

_HANDOFF_CONTACT_MARKERS = (
    "联系老师",
    "联系张老师",
    "老师本人",
    "马上联系",
    "尽快联系",
)

_HANDOFF_SENSITIVE_MARKERS = (
    "投诉",
    "申诉",
    "举报",
    "成绩",
    "保密",
    "隐私",
    "心理危机",
    "人身安全",
)

_DIRECT_CRISIS_MARKERS = (
    "我有心理危机",
    "我正处于心理危机",
    "我的人身安全",
    "我现在不安全",
)

_EXPLICIT_BOOKING_MARKERS = (
    "请帮我预约",
    "帮我预约",
    "请预约",
    "我要预约",
    "我想预约",
    "申请预约",
    "提交预约",
    "约在",
    "约个会",
    "book me",
    "schedule a meeting",
)


def requests_booking_action(question: str) -> bool:
    normalized = question.strip().lower()
    return any(marker in normalized for marker in _EXPLICIT_BOOKING_MARKERS)

_BOOKING_INFORMATION_MARKERS = (
    "office hour",
    "office hours",
    "想了解",
    "想知道",
    "了解一下",
    "告诉我",
    "能否告诉我",
    "可以告诉我",
    "准备什么",
    "先准备",
    "提前准备",
    "约时间前",
    "预约前",
    "什么时候",
    "什么时间",
    "哪几天",
    "什么时候方便",
    "哪些时候方便",
    "这周",
    "本周",
    "开放时段",
    "可预约时段",
    "预约规则",
    "如何预约",
    "怎么预约",
    "以便预约",
    "方便预约",
    "先发邮件",
    "直接发邮件",
    "发邮件",
    "线下聊",
    "当面聊",
    "更合适",
    "什么类型的问题",
    "适合先邮件",
    "等有更多内容再约",
)

_BOOKING_CONTEXT_MARKERS = (
    "office hour",
    "office hours",
    "预约",
    "约时间",
    "约老师",
    "时间安排",
    "开放时段",
    "找您",
    "发邮件",
    "线下聊",
    "当面聊",
)

_ADVISE_ONLY_MARKERS = (
    "准备什么",
    "提前准备",
    "怎么准备",
    "帮我决定",
    "替我决定",
    "该不该",
    "怎么选",
    "选哪个",
)


def requires_human_handoff(question: str) -> bool:
    """Require an explicit personal escalation request before creating a ticket.

    Research cards and quoted source material routinely contain words such as
    ``紧急``, ``安全``, ``隐私`` and ``冲突``. Their presence alone is not an
    instruction to mutate the faculty queue.
    """

    normalized = question.strip().lower()
    if any(marker in normalized for marker in _DIRECT_CRISIS_MARKERS):
        return True
    if any(marker in normalized for marker in _HANDOFF_ACTION_MARKERS):
        return True
    return any(marker in normalized for marker in _HANDOFF_CONTACT_MARKERS) and any(
        marker in normalized for marker in _HANDOFF_SENSITIVE_MARKERS
    )


def asks_for_booking_information(question: str) -> bool:
    normalized = question.strip().lower()
    if requests_booking_action(question):
        return False
    return any(marker in normalized for marker in _BOOKING_INFORMATION_MARKERS) and any(
        marker in normalized for marker in _BOOKING_CONTEXT_MARKERS
    )


def forbids_booking_action(question: str) -> bool:
    """Recognize an explicit instruction that booking is outside this request."""

    normalized = " ".join(question.strip().lower().split())
    booking_positions = [
        normalized.find(marker)
        for marker in ("预约", "book", "schedule", "meeting")
        if marker in normalized
    ]
    return any(
        any(negation in normalized[max(0, position - 32) : position] for negation in (
            "不执行", "不要", "不得", "无需", "不用", "禁止", "不创建", "do not", "don't",
        ))
        for position in booking_positions
    )


@dataclass(frozen=True, slots=True)
class InteractionPolicyResult:
    intent: InteractionIntent
    changed: bool
    reasons: tuple[str, ...] = ()


class InteractionPolicyEngine:
    """Apply non-bypassable semantic guardrails to any proposed intent."""

    def apply(
        self,
        request: ChatRequest,
        proposed: InteractionIntent,
    ) -> InteractionPolicyResult:
        if request.attachments and proposed.action == "ask_followup":
            clarification = (proposed.clarification_message or "").lower()
            if any(
                marker in clarification
                for marker in (
                    "附件",
                    "上传",
                    "材料",
                    "文件",
                    "pdf",
                    "document",
                    "upload",
                    "attach",
                )
            ):
                return InteractionPolicyResult(
                    intent=proposed.model_copy(
                        update={
                            "action": "answer",
                            "domain": proposed.domain
                            if proposed.domain != "general"
                            else "advising",
                            "needs_clarification": False,
                            "clarification_message": None,
                            "decision_mode": "advise_only"
                            if proposed.decision_mode == "direct_answer"
                            else proposed.decision_mode,
                        }
                    ),
                    changed=True,
                    reasons=("attached_evidence_already_present",),
                )

        if requires_human_handoff(request.question):
            return InteractionPolicyResult(
                intent=InteractionIntent(
                    action="human_handoff",
                    domain="advising",
                    decision_mode="human_handoff",
                    escalation_reason="涉及敏感、紧急或必须由老师本人直接处理的事项。",
                    confidence=max(proposed.confidence, 0.95),
                ),
                changed=True,
                reasons=("human_handoff_required",),
            )

        if requires_faculty_review(request.question):
            return InteractionPolicyResult(
                intent=InteractionIntent(
                    action="review_queue",
                    domain="advising",
                    retrieval_scopes=["meeting_policy", "profile"],
                    exclude_scopes=["courseware"],
                    decision_mode="review_queue",
                    escalation_reason="这是需要老师审核后才能正式答复的请求。",
                    confidence=max(proposed.confidence, 0.9),
                ),
                changed=True,
                reasons=("faculty_review_required",),
            )

        if proposed.action == "book_meeting" and asks_for_booking_information(
            request.question
        ):
            return InteractionPolicyResult(
                intent=InteractionIntent(
                    action="answer",
                    domain="advising",
                    retrieval_scopes=["meeting_policy", "profile"],
                    exclude_scopes=["courseware"],
                    decision_mode="direct_answer",
                    confidence=max(proposed.confidence, 0.9),
                ),
                changed=True,
                reasons=("booking_information_is_not_booking_action",),
            )

        if proposed.action == "book_meeting" and forbids_booking_action(request.question):
            research_review = any(
                marker in request.question
                for marker in ("研究", "论文", "仓库", "证据", "审计", "评审")
            )
            return InteractionPolicyResult(
                intent=InteractionIntent(
                    action="answer",
                    domain="research" if research_review else "general",
                    retrieval_scopes=["publications", "profile"] if research_review else [],
                    exclude_scopes=["courseware"] if research_review else [],
                    decision_mode="direct_answer",
                    confidence=max(proposed.confidence, 0.95),
                ),
                changed=True,
                reasons=("booking_explicitly_forbidden",),
            )

        if proposed.action == "book_meeting" and not requests_booking_action(
            request.question
        ):
            research_request = any(
                marker in request.question.lower()
                for marker in ("研究", "课题", "论文", "实验", "research", "paper")
            )
            return InteractionPolicyResult(
                intent=InteractionIntent(
                    action="answer",
                    domain="research" if research_request else "general",
                    retrieval_scopes=(
                        ["publications", "profile"] if research_request else []
                    ),
                    exclude_scopes=["courseware"] if research_request else [],
                    decision_mode="direct_answer",
                    confidence=max(proposed.confidence, 0.9),
                ),
                changed=True,
                reasons=("booking_requires_explicit_user_action",),
            )

        if proposed.action == "book_meeting" and proposed.decision_mode != "review_queue":
            return InteractionPolicyResult(
                intent=proposed.model_copy(update={"decision_mode": "review_queue"}),
                changed=True,
                reasons=("booking_requires_review",),
            )

        if proposed.action == "answer" and proposed.decision_mode == "direct_answer":
            if any(marker in request.question for marker in _ADVISE_ONLY_MARKERS):
                return InteractionPolicyResult(
                    intent=proposed.model_copy(update={"decision_mode": "advise_only"}),
                    changed=True,
                    reasons=("advice_must_not_be_presented_as_decision",),
                )

        return InteractionPolicyResult(intent=proposed, changed=False)
