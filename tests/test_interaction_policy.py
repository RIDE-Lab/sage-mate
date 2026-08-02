from __future__ import annotations

import pytest

from sage_faculty_twin.interaction_policy import (
    InteractionPolicyEngine,
    asks_for_booking_information,
    requires_human_handoff,
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


def test_engine_overrides_model_booking_action_for_information_question() -> None:
    result = InteractionPolicyEngine().apply(
        ChatRequest(student_name="Visitor", question="预约前需要准备什么？"),
        _proposed(action="book_meeting", domain="booking", decision_mode="review_queue"),
    )

    assert result.changed is True
    assert result.intent.action == "answer"
    assert result.intent.decision_mode == "direct_answer"
    assert result.reasons == ("booking_information_is_not_booking_action",)


def test_engine_never_allows_model_to_bypass_faculty_review() -> None:
    result = InteractionPolicyEngine().apply(
        ChatRequest(student_name="Visitor", question="您能收我吗？"),
        _proposed(),
    )

    assert result.intent.action == "review_queue"
    assert result.intent.decision_mode == "review_queue"
