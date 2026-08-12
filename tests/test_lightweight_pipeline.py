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


def test_fast_answer_persists_exchange_for_follow_up(tmp_path) -> None:
    from sage_faculty_twin.config import AppSettings
    from sage_faculty_twin.models import ChatRequest
    from sage_faculty_twin.service import DigitalTwinService

    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))
    request = ChatRequest(student_name="guest", question="你好", deep_thinking=False)
    response = service.try_fast_answer(request)
    assert response is not None

    persisted = service.persist_fast_answer(request, response)
    assert persisted.exchange_id
    assert persisted.conversation_id == response.conversation_id
    record = service._conversation_store.get_record(persisted.exchange_id)
    assert record is not None
    assert record.question == "你好"
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
