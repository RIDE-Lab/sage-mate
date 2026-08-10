from sage_faculty_twin.models import ChatRequest
from sage_faculty_twin.service import DigitalTwinService


def test_greeting_uses_direct_fast_path() -> None:
    response = DigitalTwinService._build_lightweight_chat_response(
        ChatRequest(
            student_name="guest",
            student_email="guest@example.com",
            question="你好",
            visitor_profile="general_visitor",
        )
    )

    assert response is not None
    assert response.decision_mode == "direct_fast_path"
    assert response.used_model == "sage-fast-path"
    assert "课题组" in response.answer


def test_light_request_skips_shadow_only_for_low_risk_questions() -> None:
    simple = ChatRequest(
        student_name="guest",
        student_email="guest@example.com",
        question="张老师是谁？",
        visitor_profile="general_visitor",
    )
    deep = simple.model_copy(update={"deep_thinking_explicit": True})
    web = simple.model_copy(update={"web_search": True})

    assert DigitalTwinService._is_light_request(simple)
    assert not DigitalTwinService._is_light_request(deep)
    assert not DigitalTwinService._is_light_request(web)


def test_recent_context_only_loads_for_followups() -> None:
    assert not DigitalTwinService._question_needs_recent_context("张老师是谁？")
    assert DigitalTwinService._question_needs_recent_context("结合我上次提到的方向继续")
