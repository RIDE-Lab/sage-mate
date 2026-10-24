from __future__ import annotations

import pytest

from sage_faculty_twin.interaction_policy import (
    InteractionPolicyEngine,
    asks_for_booking_information,
    forbids_booking_action,
    requires_human_handoff,
    requests_booking_draft,
)
from sage_faculty_twin.models import ChatRequest, InteractionIntent


def _proposed(**updates) -> InteractionIntent:
    payload = {
        "action": "answer",
        "domain": "general",
        "decision_mode": "direct_answer",
        "confidence": 0.5,
    }
    payload.update(updates)
    return InteractionIntent.model_validate(payload)


@pytest.mark.parametrize("question", ["请准备一份预约草稿，不提交", "帮我生成预约草稿，不发邮件",
    "请准备预约草稿：多页放弃E。不要提交或发邮件。姓名 Synthetic Booking QA，邮箱 booking-history-qa@example.test，2030年1月22日10:00到10:30。"])
def test_explicit_draft_survives_answer_misclassification(question):
    result = InteractionPolicyEngine().apply(ChatRequest(student_name="QA", question=question), _proposed())
    assert result.intent.action == "book_meeting"
    assert result.intent.decision_mode == "review_queue"


@pytest.mark.parametrize("question", ["预约草稿是什么？", "如何生成预约草稿？", "不要生成预约草稿", "文档示例：请准备一份预约草稿"])
def test_draft_description_or_negation_is_not_instruction(question):
    assert not requests_booking_draft(question)


@pytest.mark.parametrize(
    ("question", "expected"),
    [
        ("我想了解 office hours 的开放时段", True),
        ("预约前需要准备什么？", True),
        ("请帮我预约明天下午三点", False),
    ],
)
def test_booking_information_policy_is_centralized(question: str, expected: bool) -> None:
    assert asks_for_booking_information(question) is expected


def test_human_handoff_policy_covers_sensitive_requests() -> None:
    assert requires_human_handoff("我要申诉成绩并尽快联系老师") is True


@pytest.mark.parametrize(
    "question",
    (
        "事实卡记载紧急禁用后恢复原生策略，请评价证据。",
        "请分析隐私预算和安全机制之间的冲突。",
        "比较冲突检测与误会消解机制。",
    ),
)
def test_human_handoff_requires_explicit_personal_escalation(question: str) -> None:
    assert requires_human_handoff(question) is False


def test_booking_negation_is_recognized_in_research_review() -> None:
    question = "请只读评审附件中的仓库，不执行工具写入、预约、待办或邮件。"

    assert forbids_booking_action(question) is True
    result = InteractionPolicyEngine().apply(
        ChatRequest(student_name="Reviewer", question=question),
        _proposed(action="book_meeting", domain="booking", decision_mode="review_queue"),
    )

    assert result.intent.action == "answer"
    assert result.intent.domain == "research"
    assert result.intent.decision_mode == "direct_answer"
    assert result.reasons == ("booking_explicitly_forbidden",)


def test_engine_overrides_model_booking_action_for_information_question() -> None:
    result = InteractionPolicyEngine().apply(
        ChatRequest(student_name="Visitor", question="预约前需要准备什么？"),
        _proposed(action="book_meeting", domain="booking", decision_mode="review_queue"),
    )

    assert result.changed is True
    assert result.intent.action == "answer"
    assert result.intent.decision_mode == "direct_answer"
    assert result.reasons == ("booking_information_is_not_booking_action",)


def test_engine_requires_explicit_booking_action_before_creating_booking_flow() -> None:
    result = InteractionPolicyEngine().apply(
        ChatRequest(
            student_name="Reviewer",
            question="请评价这个研究课题的调度机制和实验设计。",
        ),
        _proposed(action="book_meeting", domain="booking", decision_mode="review_queue"),
    )

    assert result.changed is True
    assert result.intent.action == "answer"
    assert result.intent.domain == "research"
    assert result.intent.retrieval_scopes == ["publications", "profile"]
    assert result.reasons == ("booking_requires_explicit_user_action",)


def test_engine_never_allows_model_to_bypass_faculty_review() -> None:
    result = InteractionPolicyEngine().apply(
        ChatRequest(student_name="Visitor", question="您能收我吗？"),
        _proposed(),
    )

    assert result.intent.action == "review_queue"
    assert result.intent.decision_mode == "review_queue"
