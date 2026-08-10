import asyncio
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from sage_faculty_twin.benchmark_adapter import LampQuestionSample, build_lamp_request
from sage_faculty_twin.config import AppSettings, settings
from sage_faculty_twin.auth import build_admin_session_token
from sage_faculty_twin.models import (
    ChatRequest,
    InteractionIntent,
    KnowledgeDocumentCreate,
)
from sage_faculty_twin.notifications import BookingNotificationError
from sage_faculty_twin.service import DigitalTwinService
from sage_faculty_twin.web_search import WebSearchResult


class FailingLLMClient:
    model_name = "test-model"

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


def _without_optional_recent_memory(step_ids: list[str]) -> list[str]:
    return [step_id for step_id in step_ids if step_id != "retrieve_recent_memory"]


class IntentAwareLLMClient:
    def __init__(self, booking_intent: bool, answer: str) -> None:
        self._booking_intent = booking_intent
        self._answer = answer
        self.model_name = "test-model"

    def classify_interaction_intent_sync(
        self,
        question: str,
        course_context: str | None = None,
        recent_session_context: str | None = None,
    ) -> InteractionIntent:
        if self._booking_intent:
            return InteractionIntent(
                action="book_meeting",
                domain="booking",
                retrieval_scopes=["meeting_policy"],
                exclude_scopes=["courseware", "publications"],
                decision_mode="review_queue",
                confidence=0.95,
            )
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
        return self._booking_intent

    async def classify_booking_intent(
        self, question: str, course_context: str | None = None
    ) -> bool:
        return self._booking_intent

    def answer_question_sync(self, system_prompt: str, user_prompt: str) -> str:
        return self._answer

    async def answer_question(self, system_prompt: str, user_prompt: str) -> str:
        return self._answer


class RecordingLLMClient(IntentAwareLLMClient):
    def __init__(self, booking_intent: bool, answer: str) -> None:
        super().__init__(booking_intent=booking_intent, answer=answer)
        self.prompts: list[str] = []

    def answer_question_sync(self, system_prompt: str, user_prompt: str) -> str:
        self.prompts.append(user_prompt)
        return super().answer_question_sync(system_prompt, user_prompt)


class IntentClassifierFailingLLMClient(RecordingLLMClient):
    def classify_interaction_intent_sync(
        self,
        question: str,
        course_context: str | None = None,
        recent_session_context: str | None = None,
    ) -> InteractionIntent:
        raise AssertionError("high-confidence fast intent path should skip LLM classification")


class ShadowPlanningLLMClient(RecordingLLMClient):
    def __init__(self, booking_intent: bool, answer: str, shadow_plan_candidate: dict) -> None:
        super().__init__(booking_intent=booking_intent, answer=answer)
        self.shadow_prompts: list[str] = []
        self._shadow_plan_candidate = shadow_plan_candidate

    def propose_shadow_plan_candidate_sync(self, context, deterministic_plan):
        self.shadow_prompts.append(context.question)
        from sage_faculty_twin.workflow_planner import ShadowPlanCandidate

        return ShadowPlanCandidate.model_validate(self._shadow_plan_candidate)


class KeywordIntentLLMClient(RecordingLLMClient):
    def __init__(self, answer: str) -> None:
        super().__init__(booking_intent=False, answer=answer)

    def classify_interaction_intent_sync(
        self,
        question: str,
        course_context: str | None = None,
        recent_session_context: str | None = None,
    ) -> InteractionIntent:
        if self.classify_booking_intent_sync(question, course_context):
            return InteractionIntent(
                action="book_meeting",
                domain="booking",
                retrieval_scopes=["meeting_policy"],
                exclude_scopes=["courseware", "publications"],
                decision_mode="review_queue",
                confidence=0.95,
            )
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
        lowered = question.lower()
        booking_keywords = (
            "请帮我预约",
            "帮我预约",
            "请预约",
            "预定",
            "约时间",
            "book",
            "schedule a meeting",
        )
        return any(keyword in lowered for keyword in booking_keywords)

    async def classify_booking_intent(
        self, question: str, course_context: str | None = None
    ) -> bool:
        return self.classify_booking_intent_sync(question, course_context)


class TeachingIntentLLMClient(RecordingLLMClient):
    def classify_interaction_intent_sync(
        self,
        question: str,
        course_context: str | None = None,
        recent_session_context: str | None = None,
    ) -> InteractionIntent:
        return InteractionIntent(
            action="answer",
            domain="teaching",
            retrieval_scopes=["courseware", "tutorial", "profile"],
            exclude_scopes=["publications"],
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


class RecordingNotifier:
    def __init__(self) -> None:
        self.request_bookings = []
        self.approved_bookings = []
        self.rejected_bookings = []
        self.follow_up_emails = []

    def send_booking_request_notification(self, booking) -> str:
        self.request_bookings.append(booking)
        return settings.booking_notification_email

    def send_booking_approved_notification(self, booking) -> str:
        self.approved_bookings.append(booking)
        return booking.student_email

    def send_booking_rejected_notification(self, booking) -> str:
        self.rejected_bookings.append(booking)
        return booking.student_email

    def send_follow_up_email(self, recipient: str, subject: str, lines: list[str]) -> str:
        self.follow_up_emails.append(
            {"recipient": recipient, "subject": subject, "lines": list(lines)}
        )
        return recipient


class FailingNotifier:
    def send_booking_request_notification(self, booking) -> str:
        raise BookingNotificationError("SMTP 未配置")

    def send_booking_approved_notification(self, booking) -> str:
        raise BookingNotificationError("SMTP 未配置")

    def send_booking_rejected_notification(self, booking) -> str:
        raise BookingNotificationError("SMTP 未配置")


def _trace_step(response, key: str):
    return next(step for step in response.workflow_trace if step.key == key)


def test_chat_books_meeting_when_details_are_complete(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path, knowledge_backend="bm25")
    service = DigitalTwinService(settings)
    service._llm_client = FailingLLMClient()
    notifier = RecordingNotifier()
    service._email_notifier = notifier

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-complete",
                question="请帮我预约 2026-05-26 15:00 讨论论文进展",
            )
        )
    )

    assert response.workflow_action == "book_meeting"
    assert response.booking_result is not None
    assert response.booking_result.accepted is True
    assert response.booking_result.booking is not None
    assert response.booking_result.booking.topic == "论文进展"
    assert response.booking_result.booking.status == "待确认"
    assert response.booking_result.notification is not None
    assert response.booking_result.notification.status == "sent"
    assert response.booking_result.notification.recipient == settings.booking_notification_email
    assert [step.key for step in response.workflow_trace] == [
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
    assert response.workflow_trace[1].status == "completed"
    assert all(step.duration_ms is not None for step in response.workflow_trace)
    assert (
        response.workflow_trace[1].summary == "已生成 deterministic 规划：prepare_booking_request。"
    )
    assert "planned steps" in response.workflow_trace[1].detail
    assert response.planner_preview is not None
    assert response.planner_preview.goal == "prepare_booking_request"
    assert response.planner_preview.accepted is True
    assert response.planner_preview.fallback_template == "book_meeting"
    assert response.shadow_planner_preview is not None
    assert response.shadow_planner_preview.planner_mode == "llm_shadow"
    assert response.shadow_planner_preview.accepted is False
    assert (
        response.shadow_planner_preview.fallback_reason
        == "LLM shadow planner not enabled yet."
    )
    assert response.shadow_planner_preview.planned_steps == []
    assert response.planner_comparison is not None
    assert response.planner_comparison.comparison_status == "shadow_disabled"
    assert response.planner_comparison.same_goal is True
    assert response.planner_comparison.same_fallback_template is True
    assert response.planner_comparison.shadow_only_steps == []
    assert _without_optional_recent_memory(
        response.planner_comparison.deterministic_only_steps
    ) == [
        "detect_profile_context",
        "classify_intent",
        "assemble_prompt_context",
        "answer_with_citations",
        "score_memory_usefulness",
        "render_user_response",
    ]
    assert _without_optional_recent_memory(response.planner_preview.planned_steps) == [
        "detect_profile_context",
        "classify_intent",
        "assemble_prompt_context",
        "answer_with_citations",
        "score_memory_usefulness",
        "render_user_response",
    ]
    assert response.workflow_trace[2].summary == "已识别当前交互意图：book_meeting/booking。"
    assert response.workflow_trace[3].summary == "预约字段已经齐备。"
    assert response.workflow_trace[4].summary == "预约申请已提交，等待管理员确认。"
    assert settings.booking_notification_email in response.workflow_trace[4].detail
    assert any(item.basis_label == "预约规则与时段" for item in response.answer_basis)
    assert any(item.source_label == "当前预约安排配置" for item in response.answer_basis)
    assert len(notifier.request_bookings) == 1
    assert settings.booking_notification_email in response.answer
    assert "待确认" in response.answer


def test_benchmark_requests_skip_memory_retrieval_and_persistence(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    service._llm_client = ShadowPlanningLLMClient(
        booking_intent=False,
        answer="这是一个 benchmark 回复。",
        shadow_plan_candidate={
            "goal": "answer_grounded_question",
            "fallback_template": "answer_question",
            "step_ids": [
                "detect_profile_context",
                "classify_intent",
                "assemble_prompt_context",
                "answer_with_citations",
                "render_user_response",
            ],
            "allowed_sources": ["public_homepage", "faq"],
            "requires_citations": False,
            "explain_to_operator": "Benchmark requests should stay on the main execution lane without extra planner latency.",
        },
    )
    initial_record_count = service._conversation_store.count_records()

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="CharacterEval Runner",
                question="你正在参与一个 CharacterEval 风格的多轮角色一致性评测。请只输出目标角色的下一句回复。",
                course_context="CharacterEval role-play benchmark",
                visitor_profile="general_visitor",
                conversation_id="charactereval-memory-isolation",
            )
        )
    )

    memory_retrieve_step = next(
        step for step in response.workflow_trace if step.key == "memory_retrieve"
    )
    memory_persist_step = next(
        step for step in response.workflow_trace if step.key == "memory_persist"
    )

    assert memory_retrieve_step.status == "skipped"
    assert memory_retrieve_step.summary == "benchmark 评测请求跳过对话记忆检索。"
    assert memory_persist_step.status == "skipped"
    assert memory_persist_step.summary == "benchmark 评测请求不写入对话记忆。"
    assert response.memory_used is False
    assert response.memory_write_back is False
    assert response.retrieved_items == []
    assert service._llm_client.shadow_prompts == []
    assert response.shadow_planner_preview is not None
    assert response.shadow_planner_preview.fallback_reason == (
        "Benchmark evaluation request skips shadow planner to keep latency and scoring focused on the main execution lane."
    )
    usefulness_step = next(
        step for step in response.workflow_trace if step.key == "memory_usefulness_score"
    )
    assert usefulness_step.status == "completed"
    assert "低置信度" in usefulness_step.summary
    assert response.planner_comparison is not None
    assert response.planner_comparison.comparison_status == "shadow_disabled"
    assert service._conversation_store.count_records() == initial_record_count


def test_simple_greeting_plan_skips_live_retrieval_stages(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    service._llm_client = RecordingLLMClient(booking_intent=False, answer="你好，我可以继续帮你。")

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Visitor",
                question="你好",
            )
        )
    )

    assert response.decision_mode == "direct_fast_path"
    assert response.used_model == "sage-fast-path"
    assert response.workflow_trace == []
    assert response.planner_preview is None
    assert service._conversation_store.get_telemetry_summary()["query_count"] == 0


def test_chat_books_meeting_with_chinese_relative_time(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        availability_schedule_path=tmp_path / "availability.json",
    )
    service = DigitalTwinService(settings)
    service._llm_client = FailingLLMClient()
    service._email_notifier = RecordingNotifier()

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="数据库实验课",
                conversation_id="conv-cn-time",
                question="请帮我预约明天下午三点讨论数据库实验课报告。",
            )
        )
    )

    assert response.workflow_action == "book_meeting"
    assert response.pending_fields == []
    assert response.booking_result is not None
    assert response.booking_result.booking is not None
    booking = response.booking_result.booking
    assert booking.topic == "数据库实验课报告"
    assert booking.start_at.date() == (datetime.now() + timedelta(days=1)).date()
    assert booking.start_at.hour == 15
    assert booking.start_at.minute == 0
    assert booking.end_at.hour == 15
    assert booking.end_at.minute == 30


def test_admin_confirmation_sends_student_email(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    notifier = RecordingNotifier()
    service._email_notifier = notifier

    created = service.book_meeting(
        {
            "student_name": "Alice",
            "student_email": "alice@example.com",
            "topic": "论文进展",
            "preferred_start": "2026-05-26T15:00:00",
            "preferred_end": "2026-05-26T15:45:00",
        }
    )

    assert created.booking is not None
    assert created.notification is not None
    assert created.notification.status == "sent"
    assert created.notification.recipient == settings.booking_notification_email
    confirmed = service.confirm_booking(created.booking.booking_id)

    assert confirmed.accepted is True
    assert confirmed.booking is not None
    assert confirmed.booking.status == "已确认"
    assert confirmed.notification is not None
    assert confirmed.notification.status == "sent"
    assert confirmed.notification.recipient == "alice@example.com"
    assert len(notifier.approved_bookings) == 1
    assert "alice@example.com" in confirmed.message


def test_admin_rejection_sends_student_email(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    notifier = RecordingNotifier()
    service._email_notifier = notifier

    created = service.book_meeting(
        {
            "student_name": "Alice",
            "student_email": "alice@example.com",
            "topic": "论文进展",
            "preferred_start": "2026-05-26T15:00:00",
            "preferred_end": "2026-05-26T15:45:00",
        }
    )

    assert created.booking is not None
    assert created.notification is not None
    assert created.notification.status == "sent"
    rejected = service.reject_booking(created.booking.booking_id)

    assert rejected.accepted is True
    assert rejected.booking is not None
    assert rejected.booking.status == "已拒绝"
    assert rejected.notification is not None
    assert rejected.notification.status == "sent"
    assert rejected.notification.recipient == "alice@example.com"
    assert len(notifier.rejected_bookings) == 1
    assert "alice@example.com" in rejected.message


def test_admin_rejection_includes_reason_in_booking(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    notifier = RecordingNotifier()
    service._email_notifier = notifier

    created = service.book_meeting(
        {
            "student_name": "Alice",
            "student_email": "alice@example.com",
            "topic": "论文进展",
            "preferred_start": "2026-05-26T15:00:00",
            "preferred_end": "2026-05-26T15:45:00",
        }
    )

    assert created.booking is not None
    assert created.notification is not None
    rejected = service.reject_booking(
        created.booking.booking_id,
        {"rejection_reason": "这周安排已满，请改约下周。"},
    )

    assert rejected.accepted is True
    assert rejected.booking is not None
    assert rejected.booking.rejection_reason == "这周安排已满，请改约下周。"


def test_direct_booking_keeps_record_when_notification_fails(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    service._email_notifier = FailingNotifier()

    created = service.book_meeting(
        {
            "student_name": "Alice",
            "student_email": "alice@example.com",
            "topic": "论文进展",
            "preferred_start": "2026-05-26T15:00:00",
            "preferred_end": "2026-05-26T15:45:00",
        }
    )

    assert created.accepted is True
    assert created.booking is not None
    assert created.booking.status == "待确认"
    assert created.notification is not None
    assert created.notification.status == "failed"
    assert "SMTP 未配置" in created.notification.summary
    assert created.notification.detail is not None


def test_chat_collects_missing_booking_details_and_then_books(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    service._llm_client = FailingLLMClient()
    service._email_notifier = RecordingNotifier()

    first = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                course_context="科研指导",
                conversation_id="conv-follow-up",
                question="请帮我预约 2026-05-26 15:00 讨论论文进展",
            )
        )
    )

    assert first.workflow_action == "collect_booking_details"
    assert first.pending_fields == ["student_email"]
    booking_prepare_step = _trace_step(first, "booking_prepare")
    _trace_step(first, "interaction_understand")
    _trace_step(first, "memory_retrieve")
    assert "邮箱" in booking_prepare_step.detail
    assert booking_prepare_step.duration_ms is not None
    assert booking_prepare_step.summary == "仍缺少：邮箱。"

    second = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                course_context="科研指导",
                conversation_id="conv-follow-up",
                question="我的邮箱是 alice@example.com",
            )
        )
    )

    assert second.workflow_action == "book_meeting"
    assert second.booking_result is not None
    assert second.booking_result.accepted is True
    assert second.booking_result.booking is not None
    assert second.booking_result.booking.status == "待确认"
    assert _trace_step(second, "booking_execute").status == "completed"


def test_chat_booking_reports_notification_failure_without_rolling_back_booking(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    service._llm_client = FailingLLMClient()
    service._email_notifier = FailingNotifier()

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-notify-fail",
                question="请帮我预约 2026-05-26 15:00 讨论论文进展",
            )
        )
    )

    assert response.workflow_action == "book_meeting"
    assert response.booking_result is not None
    assert response.booking_result.accepted is True
    assert response.booking_result.notification is not None
    assert response.booking_result.notification.status == "failed"
    assert "SMTP 未配置" in response.booking_result.notification.summary
    assert response.booking_result.notification.detail is not None
    assert "预约记录已经保存" in response.booking_result.notification.detail
    booking_execute_step = _trace_step(response, "booking_execute")
    assert booking_execute_step.summary == "预约申请已提交，但提醒邮件发送失败。"
    assert "邮件通知失败" in booking_execute_step.detail
    assert "SMTP 未配置" in response.answer


def test_chat_uses_llm_intent_to_avoid_false_booking_positive(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    service._llm_client = IntentAwareLLMClient(
        booking_intent=False,
        answer="预约前建议先准备 agenda、当前 blocker 和你最想讨论的问题。",
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                question="和老师约时间前，我应该先准备什么？",
            )
        )
    )

    assert response.workflow_action == "advise_only"
    assert response.decision_mode == "advise_only"
    assert response.pending_fields == []
    assert response.booking_result is None
    assert any(action.action_type == "todo_review" for action in response.follow_up_actions)
    assert any(
        action.action_type == "office_hour_recommendation" for action in response.follow_up_actions
    )
    assert "agenda" in response.answer
    interaction_step = _trace_step(response, "interaction_understand")
    booking_prepare_step = _trace_step(response, "booking_prepare")
    _trace_step(response, "memory_retrieve")
    _trace_step(response, "knowledge_retrieve")
    assert interaction_step.status == "completed"
    assert booking_prepare_step.status == "skipped"
    assert all(step.duration_ms is not None for step in response.workflow_trace)
    assert interaction_step.summary == "已识别当前交互意图：answer/advising，仅提供建议。"
    assert booking_prepare_step.summary == "未进入预约流程。"


def test_chat_fast_paths_obvious_research_route_without_llm_classifier(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    service._llm_client = IntentClassifierFailingLLMClient(
        booking_intent=False,
        answer="课题组更关注开放研究问题、系统机制和可发表的长期路线。",
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                visitor_profile="general_visitor",
                question="这个课题组的研究路线和一般企业 R&D 有什么区别？",
                deep_thinking=False,
            )
        )
    )

    assert response.workflow_action == "answer"
    assert response.decision_mode == "direct_answer"
    interaction_step = _trace_step(response, "interaction_understand")
    assert "answer/research" in interaction_step.summary
    assert "heuristic-fast" in interaction_step.detail


def test_chat_fast_paths_obvious_teaching_route_without_llm_classifier(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    service._llm_client = IntentClassifierFailingLLMClient(
        booking_intent=False,
        answer="可以先补并行程序基础、操作系统和基础性能分析方法。",
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                visitor_profile="general_visitor",
                question="这门课如果我基础一般，应该先补哪些内容？",
                deep_thinking=False,
            )
        )
    )

    assert response.workflow_action == "answer"
    assert response.decision_mode == "direct_answer"
    interaction_step = _trace_step(response, "interaction_understand")
    assert "answer/teaching" in interaction_step.summary
    assert "heuristic-fast" in interaction_step.detail


def test_chat_answers_office_hour_query_without_starting_booking_workflow(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    service._llm_client = IntentAwareLLMClient(
        booking_intent=True,
        answer="本周默认开放预约时间仍按工作日 09:00-18:00 处理；如果你想继续预约，再告诉我具体时间即可。",
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                question="能否告诉我老师这周的 office hours？我想了解以便预约。",
            )
        )
    )

    assert response.workflow_action == "answer"
    assert response.pending_fields == []
    assert response.booking_result is None
    assert "09:00-18:00" in response.answer
    assert (
        _trace_step(response, "interaction_understand").summary
        == "已识别当前交互意图：answer/advising。"
    )
    assert _trace_step(response, "booking_prepare").summary == "未进入预约流程。"


def test_chat_emits_trace_steps_via_callback(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # Chat Latency Optimizations Task 2 introduced background dispatch of
    # post-answer side-effects when ``trace_callback`` is set. This test
    # predates that change and asserts post-answer keys are received via the
    # callback before ``service.answer`` returns; pin the inline path so the
    # synchronous emission contract still holds.
    del monkeypatch
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        post_answer_background=False,
    )
    service = DigitalTwinService(settings)
    service._llm_client = FailingLLMClient()
    emitted_steps = []

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-callback",
                question="请帮我预约 2026-05-26 15:00 讨论论文进展",
            ),
            trace_callback=emitted_steps.append,
        )
    )

    # Trace callback receives every step that ends up in the canonicalized
    # response trace; order may differ because the response trace is
    # canonicalized (response_render last) while the callback emits in
    # runtime order (response_render before post-answer side-effects).
    assert {step.key for step in emitted_steps} == {step.key for step in response.workflow_trace}
    assert emitted_steps[0].summary == "已建立当前会话。"
    assert emitted_steps[1].key == "workflow_plan_preview"
    assert response.planner_preview is not None
    assert response.planner_preview.goal == "prepare_booking_request"
    assert response.shadow_planner_preview is not None
    assert response.shadow_planner_preview.planner_mode == "llm_shadow"
    assert response.planner_comparison is not None
    assert response.planner_comparison.comparison_status == "shadow_disabled"
    # In inline post-answer mode response_render is emitted at runtime BEFORE
    # the post-answer steps; the canonicalized response.workflow_trace puts
    # response_render last but the runtime emission order does not.
    assert "response_render" in {step.key for step in emitted_steps}
    assert any(step.key == "memory_profile_consolidate" for step in emitted_steps)


def test_chat_surfaces_llm_shadow_planner_comparison_without_affecting_execution(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        planner_comparison_dir=tmp_path / "planner-comparisons",
        shadow_planner_enabled=True,
    )
    service = DigitalTwinService(settings)
    service._llm_client = ShadowPlanningLLMClient(
        booking_intent=False,
        answer="你可以先准备 agenda、当前进展和两个具体问题。",
        shadow_plan_candidate={
            "goal": "prepare_booking_agenda",
            "fallback_template": "advise_only",
            "step_ids": [
                "detect_profile_context",
                "classify_intent",
                "retrieve_knowledge",
                "assemble_prompt_context",
                "answer_with_citations",
                "render_user_response",
            ],
            "allowed_sources": ["public_homepage", "faq", "booking_policy"],
            "requires_citations": True,
            "explain_to_operator": "Shadow planner suggests adding booking policy grounding for meeting preparation guidance.",
        },
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-shadow-live",
                question="预约前我应该准备什么材料？",
            ),
            admin_session_token=build_admin_session_token(
                settings, username=settings.admin_username, role="super_admin"
            ),
        )
    )

    assert response.workflow_action == "advise_only"
    assert response.planner_preview is not None
    assert response.planner_preview.goal == "prepare_booking_agenda"
    assert response.shadow_planner_preview is not None
    assert response.shadow_planner_preview.goal == "prepare_booking_agenda"
    assert response.shadow_planner_preview.accepted is True
    assert response.shadow_planner_preview.planned_steps == [
        "detect_profile_context",
        "classify_intent",
        "retrieve_knowledge",
        "assemble_prompt_context",
        "answer_with_citations",
        "render_user_response",
    ]
    assert response.planner_comparison is not None
    assert response.planner_comparison.comparison_status == "different_steps"
    assert response.planner_comparison.same_goal is True
    assert response.planner_comparison.same_fallback_template is True
    assert response.planner_comparison.shadow_only_steps == ["retrieve_knowledge"]
    assert _without_optional_recent_memory(
        response.planner_comparison.deterministic_only_steps
    ) == [
        "retrieve_hybrid_knowledge",
        "score_memory_usefulness",
    ]
    persisted = service._planner_comparison_store.list_records(limit=1)
    assert len(persisted) == 1
    assert persisted[0].comparison_status == "different_steps"
    assert persisted[0].deterministic_goal == "prepare_booking_agenda"


def test_chat_surfaces_artifact_aware_planner_preview_for_uploaded_follow_up(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    service._llm_client = RecordingLLMClient(
        booking_intent=False,
        answer="我会结合你上传的 proposal draft 和当前实验计划继续给出建议。",
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                visitor_profile="lab_member",
                conversation_id="conv-artifact-preview",
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
    )

    assert response.planner_preview is not None
    assert response.planner_preview.goal == "answer_research_artifact_question"
    assert response.planner_preview.accepted is True
    assert response.planner_preview.fallback_template == "answer_question"
    assert _without_optional_recent_memory(response.planner_preview.planned_steps) == [
        "detect_profile_context",
        "classify_intent",
        "retrieve_hybrid_knowledge",
        "retrieve_artifact_memory",
        "retrieve_profile_memory",
        "assemble_prompt_context",
        "answer_with_citations",
        "score_memory_usefulness",
        "render_user_response",
    ]
    assert response.memory_used is True
    assert any(item.source_label == "上传材料" for item in response.retrieved_items)
    assert any(item.basis_label == "上传材料" for item in response.answer_basis)
    assert any(item.topic == "artifact_memory" for item in response.retrieved_items)
    assert any(step.key == "workflow_plan_preview" for step in response.workflow_trace)


def test_chat_reuses_persisted_artifact_memory_across_conversations(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "conversation-memory",
    )
    service = DigitalTwinService(settings)
    llm = RecordingLLMClient(
        booking_intent=False,
        answer="我会继续根据你上次上传的 proposal draft 来回答。",
    )
    service._llm_client = llm

    asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                visitor_profile="lab_member",
                conversation_id="conv-artifact-seed",
                question="我上传了 proposal draft，先帮我记住里面的实验计划。",
                attachments=[
                    {
                        "file_name": "proposal-draft.md",
                        "media_type": "text/markdown",
                        "text_content": "Draft agenda: experiment plan, milestones, blockers.",
                    }
                ],
            )
        )
    )

    persisted_records = service._conversation_store.list_records()
    assert persisted_records
    assert len(persisted_records[0].attachments) == 1
    assert persisted_records[0].attachments[0].file_name == "proposal-draft.md"

    follow_up = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                visitor_profile="lab_member",
                conversation_id="conv-artifact-follow-up",
                question="我上次上传的 proposal draft 里关于 experiment plan 和 blockers 的部分是什么？",
            )
        )
    )

    assert len(llm.prompts) == 2
    assert "Uploaded artifacts and referenced materials:" in llm.prompts[-1]
    assert "proposal-draft.md" in llm.prompts[-1]
    assert "experiment plan, milestones, blockers" in llm.prompts[-1]
    assert follow_up.memory_used is True
    assert any(item.topic == "artifact_memory" for item in follow_up.retrieved_items)
    assert any(item.source_label == "上传材料" for item in follow_up.retrieved_items)
    assert any(item.basis_label == "上传材料" for item in follow_up.answer_basis)


def test_chat_records_artifact_memory_draft_for_explicit_archive_request(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "conversation-memory",
        artifact_memory_draft_dir=tmp_path / "artifact-memory-drafts",
    )
    service = DigitalTwinService(settings)
    llm = RecordingLLMClient(
        booking_intent=False,
        answer="我已经整理成后续跟进材料草稿，后面可以继续补充实验计划。",
    )
    service._llm_client = llm

    asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                visitor_profile="lab_member",
                conversation_id="conv-artifact-seed",
                question="我上传了 proposal draft，先帮我记住里面的实验计划。",
                attachments=[
                    {
                        "file_name": "proposal-draft.md",
                        "media_type": "text/markdown",
                        "text_content": "Draft agenda: experiment plan, milestones, blockers.",
                    }
                ],
            )
        )
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                visitor_profile="lab_member",
                conversation_id="conv-artifact-archive",
                question="把我上次上传的 proposal draft 记录成后续跟进材料，后面继续讨论实验计划。",
            )
        )
    )

    assert response.planner_preview is not None
    assert response.planner_preview.goal == "record_artifact_followup"
    assert "record_artifact_memory" in response.planner_preview.planned_steps
    assert "Uploaded artifacts and referenced materials:" in llm.prompts[-1]
    drafts = service._artifact_memory_draft_store.list_drafts()
    assert len(drafts) == 1
    assert drafts[0].artifact_names == ["proposal-draft.md"]
    assert drafts[0].artifact_excerpt_count >= 1
    assert any(source.startswith("historical_artifact:") for source in drafts[0].artifact_sources)
    draft_trace = next(
        step for step in response.workflow_trace if step.key == "artifact_memory_writeback"
    )
    assert draft_trace.status == "completed"
    assert "材料记忆草稿" in draft_trace.summary
    health = service.health()
    assert health["artifact_memory_drafts"] == "1"


def test_chat_reuses_neuromem_conversation_memory_in_follow_up_prompt(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "conversation-memory",
    )
    service = DigitalTwinService(settings)
    llm = RecordingLLMClient(booking_intent=False, answer="好的，我继续回答。")
    service._llm_client = llm

    asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-memory",
                question="我之前说我想讨论什么主题？",
            )
        )
    )

    follow_up = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-memory",
                question="再根据刚才的上下文给我一个建议。",
            )
        )
    )

    assert len(llm.prompts) == 2
    assert "Immediate session context (same conversation):" in llm.prompts[-1]
    assert "Stable student profile memory:" not in llm.prompts[-1]
    assert "Recent conversation memory:" not in llm.prompts[-1]
    assert "我之前说我想讨论什么主题？" in llm.prompts[-1]
    assert "科研指导" in llm.prompts[-1]
    # "近期交流记录" is stripped by safety net (session context must never be cited)
    assert not any(item.basis_label == "近期交流记录" for item in follow_up.answer_basis)
    assert follow_up.memory_used is True
    assert follow_up.memory_write_back is True
    assert len(follow_up.retrieved_items) >= 1
    assert {item.memory_type for item in follow_up.retrieved_items} == {"short_term"}
    assert any(item.source_label == "同会话上下文" for item in follow_up.retrieved_items)
    assert all(item.entry_id for item in follow_up.retrieved_items)
    usefulness_step = next(
        step for step in follow_up.workflow_trace if step.key == "memory_usefulness_score"
    )
    assert usefulness_step.status == "completed"
    assert "有帮助" in usefulness_step.summary

    health = service.health()
    assert health["conversation_memory_backend"] == "neuromem-layered"
    assert int(health["conversation_memory_profiles"]) >= 1
    assert health["chat_pipeline_stages"] == "13"
    assert health["planner_deterministic_total"] == "2"
    assert health["planner_deterministic_accepted"] == "2"
    assert health["planner_shadow_total"] == "2"
    assert health["planner_shadow_disabled"] == "2"
    assert int(health["planner_metric_records"]) >= 4


def test_chat_includes_immediate_session_context_even_without_memory_hits(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "conversation-memory",
    )
    service = DigitalTwinService(settings)
    llm = RecordingLLMClient(booking_intent=False, answer="我会接着刚才的话题继续回答。")
    service._llm_client = llm

    asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-immediate-context",
                question="我现在在推理引擎和推理服务系统之间摇摆。",
            )
        )
    )

    original_search = service._conversation_store.search
    service._conversation_store.search = lambda *args, **kwargs: []
    try:
        response = asyncio.run(
            service.answer(
                ChatRequest(
                    student_name="Alice",
                    student_email="alice@example.com",
                    course_context="科研指导",
                    conversation_id="conv-immediate-context",
                    question="那如果按刚才那个继续，下一步我先做哪块？",
                )
            )
        )
    finally:
        service._conversation_store.search = original_search

    assert len(llm.prompts) == 2
    assert "Immediate session context (same conversation):" in llm.prompts[-1]
    assert "User: 我现在在推理引擎和推理服务系统之间摇摆。" in llm.prompts[-1]
    assert "Assistant: 我会接着刚才的话题继续回答。" in llm.prompts[-1]
    assert response.answer == "我会接着刚才的话题继续回答。"


def test_chat_answers_previous_question_recall_without_asking_user_to_repeat(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "conversation-memory",
    )
    service = DigitalTwinService(settings)
    llm = RecordingLLMClient(booking_intent=False, answer="这条不该被调用")
    service._llm_client = llm

    asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                conversation_id="conv-question-recall",
                question="我想讨论推理引擎和推理服务系统之间怎么选。",
            )
        )
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                conversation_id="conv-question-recall",
                question="我刚刚问的是什么问题",
            )
        )
    )

    assert response.answer == "你刚刚问的是：我想讨论推理引擎和推理服务系统之间怎么选。"
    assert response.workflow_action == "answer"
    assert len(llm.prompts) == 1
    interaction_step = next(
        step for step in response.workflow_trace if step.key == "interaction_understand"
    )
    assert "直接读取同会话最近一轮内容" in interaction_step.summary
    prompt_step = next(step for step in response.workflow_trace if step.key == "prompt_build")
    assert prompt_step.status == "skipped"


def test_chat_answers_previous_question_recall_even_when_classifier_wants_clarification(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "conversation-memory",
    )
    service = DigitalTwinService(settings)

    class ClarificationBiasedLLM(RecordingLLMClient):
        def classify_interaction_intent_sync(
            self,
            question: str,
            course_context: str | None = None,
            recent_session_context: str | None = None,
        ) -> InteractionIntent:
            if question != "我刚刚问的是什么问题":
                return super().classify_interaction_intent_sync(question, course_context)
            return InteractionIntent(
                action="ask_followup",
                domain="advising",
                retrieval_scopes=["profile"],
                exclude_scopes=["courseware"],
                decision_mode="direct_answer",
                needs_clarification=True,
                clarification_message="请告诉我你之前具体问了什么问题。",
                confidence=0.95,
            )

    llm = ClarificationBiasedLLM(booking_intent=False, answer="这条不该被调用")
    service._llm_client = llm

    asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                conversation_id="conv-question-recall-guard",
                question="我想讨论推理引擎和推理服务系统之间怎么选。",
            )
        )
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                conversation_id="conv-question-recall-guard",
                question="我刚刚问的是什么问题",
            )
        )
    )

    assert response.answer == "你刚刚问的是：我想讨论推理引擎和推理服务系统之间怎么选。"
    assert response.workflow_action == "answer"
    assert len(llm.prompts) == 1
    interaction_step = next(
        step for step in response.workflow_trace if step.key == "interaction_understand"
    )
    assert "直接读取同会话最近一轮内容" in interaction_step.summary


def test_chat_answers_previous_answer_recall_from_same_conversation(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "conversation-memory",
    )
    service = DigitalTwinService(settings)
    llm = RecordingLLMClient(booking_intent=False, answer="上一轮回答会被回忆")
    service._llm_client = llm

    asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                conversation_id="conv-answer-recall",
                question="我想了解一下推理服务系统和推理引擎的区别。",
            )
        )
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                conversation_id="conv-answer-recall",
                question="你刚刚回答的是什么",
            )
        )
    )

    assert response.answer == "我刚刚回答的是：上一轮回答会被回忆"
    assert response.workflow_action == "answer"
    assert len(llm.prompts) == 1
    interaction_step = next(
        step for step in response.workflow_trace if step.key == "interaction_understand"
    )
    assert "直接读取同会话最近一轮内容" in interaction_step.summary
    prompt_step = next(step for step in response.workflow_trace if step.key == "prompt_build")
    assert prompt_step.status == "skipped"


def test_chat_passes_recent_session_context_into_intent_classification(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "conversation-memory",
    )
    service = DigitalTwinService(settings)

    class FollowUpContextAwareLLM(RecordingLLMClient):
        def __init__(self) -> None:
            super().__init__(booking_intent=False, answer="建议先把需求边界和系统职责拆开。")
            self.classification_contexts: list[str | None] = []

        def classify_interaction_intent_sync(
            self,
            question: str,
            course_context: str | None = None,
            recent_session_context: str | None = None,
        ) -> InteractionIntent:
            self.classification_contexts.append(recent_session_context)
            if question == "那如果按刚才那个继续，下一步我先做哪块？":
                if (
                    recent_session_context
                    and "Immediate session context (same conversation):" in recent_session_context
                ):
                    return InteractionIntent(
                        action="answer",
                        domain="advising",
                        retrieval_scopes=["profile", "preparation"],
                        exclude_scopes=["courseware"],
                        decision_mode="advise_only",
                        confidence=0.95,
                    )
                return InteractionIntent(
                    action="ask_followup",
                    domain="advising",
                    retrieval_scopes=["profile"],
                    exclude_scopes=["courseware"],
                    decision_mode="direct_answer",
                    needs_clarification=True,
                    clarification_message="请先告诉我你刚才说的是哪件事。",
                    confidence=0.95,
                )
            return InteractionIntent(
                action="answer",
                domain="advising",
                retrieval_scopes=["profile", "preparation"],
                exclude_scopes=["courseware"],
                decision_mode="advise_only",
                confidence=0.95,
            )

    llm = FollowUpContextAwareLLM()
    service._llm_client = llm

    asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-classifier-context",
                question="我现在在推理引擎和推理服务系统之间摇摆。",
            )
        )
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-classifier-context",
                question="那如果按刚才那个继续，下一步我先做哪块？",
            )
        )
    )

    assert len(llm.classification_contexts) >= 2
    assert llm.classification_contexts[-1] is not None
    assert "Immediate session context (same conversation):" in llm.classification_contexts[-1]
    assert response.answer == "建议先把需求边界和系统职责拆开。"
    interaction_step = next(
        step for step in response.workflow_trace if step.key == "interaction_understand"
    )
    assert "ask_followup" not in interaction_step.summary
    prompt_step = next(step for step in response.workflow_trace if step.key == "prompt_build")
    assert prompt_step.status == "completed"
    assert "Immediate session context (same conversation):" in llm.prompts[-1]


def test_follow_up_planner_preview_treats_recent_session_context_as_hard_input(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "conversation-memory",
    )
    service = DigitalTwinService(settings)
    llm = RecordingLLMClient(
        booking_intent=False,
        answer="建议先把问题拆成职责边界、服务形态和实验验证三块。",
    )
    service._llm_client = llm

    asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-followup-planner-preview",
                question="我现在在推理引擎和推理服务系统之间摇摆。",
            )
        )
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-followup-planner-preview",
                question="那如果按刚才那个继续，下一步我先做哪块？",
            )
        )
    )

    assert response.planner_preview is not None
    assert response.planner_preview.goal == "answer_course_question"
    assert "retrieve_recent_memory" not in response.planner_preview.planned_steps
    assert "retrieve_profile_memory" in response.planner_preview.planned_steps
    assert any(step.key == "workflow_plan_preview" for step in response.workflow_trace)
    memory_retrieve_step = next(
        step for step in response.workflow_trace if step.key == "memory_retrieve"
    )
    assert memory_retrieve_step.status == "completed"


def test_natural_follow_up_phrasings_reuse_recent_session_context(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "conversation-memory",
    )

    class NaturalFollowUpLLM(RecordingLLMClient):
        def __init__(self) -> None:
            super().__init__(booking_intent=False, answer="这是兜底回答。")

        def classify_interaction_intent_sync(
            self,
            question: str,
            course_context: str | None = None,
            recent_session_context: str | None = None,
        ) -> InteractionIntent:
            follow_up_questions = {
                "那这个方向值得继续吗？": "值得继续，但要先明确你更关心引擎本身还是服务落地。",
                "按前面那个方案的话风险是什么？": "主要风险是问题边界不清，容易把引擎实现和服务编排混在一起。",
            }
            if question in follow_up_questions:
                if (
                    recent_session_context
                    and "Immediate session context (same conversation):" in recent_session_context
                ):
                    self._answer = follow_up_questions[question]
                    return InteractionIntent(
                        action="answer",
                        domain="advising",
                        retrieval_scopes=["profile", "preparation"],
                        exclude_scopes=["courseware"],
                        decision_mode="advise_only",
                        confidence=0.95,
                    )
                return InteractionIntent(
                    action="ask_followup",
                    domain="advising",
                    retrieval_scopes=["profile"],
                    exclude_scopes=["courseware"],
                    decision_mode="direct_answer",
                    needs_clarification=True,
                    clarification_message="请先告诉我你指的是前面的哪个方向或方案。",
                    confidence=0.95,
                )
            self._answer = "我会先帮你梳理方向差异。"
            return InteractionIntent(
                action="answer",
                domain="advising",
                retrieval_scopes=["profile", "preparation"],
                exclude_scopes=["courseware"],
                decision_mode="advise_only",
                confidence=0.95,
            )

    cases = [
        (
            "conv-natural-followup-worth",
            "那这个方向值得继续吗？",
            "值得继续，但要先明确你更关心引擎本身还是服务落地。",
        ),
        (
            "conv-natural-followup-risk",
            "按前面那个方案的话风险是什么？",
            "主要风险是问题边界不清，容易把引擎实现和服务编排混在一起。",
        ),
    ]

    for conversation_id, follow_up_question, expected_answer in cases:
        service = DigitalTwinService(settings)
        llm = NaturalFollowUpLLM()
        service._llm_client = llm

        asyncio.run(
            service.answer(
                ChatRequest(
                    student_name="Alice",
                    student_email="alice@example.com",
                    course_context="科研指导",
                    conversation_id=conversation_id,
                    question="我现在在推理引擎和推理服务系统之间摇摆。",
                )
            )
        )

        response = asyncio.run(
            service.answer(
                ChatRequest(
                    student_name="Alice",
                    student_email="alice@example.com",
                    course_context="科研指导",
                    conversation_id=conversation_id,
                    question=follow_up_question,
                )
            )
        )

        assert response.answer == expected_answer
        interaction_step = next(
            step for step in response.workflow_trace if step.key == "interaction_understand"
        )
        assert "ask_followup" not in interaction_step.summary
        assert "Immediate session context (same conversation):" in llm.prompts[-1]


def test_lamp_prompt_adds_profile_grounding_guidance(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    llm = RecordingLLMClient(booking_intent=False, answer="好的。")
    service._llm_client = llm

    request = build_lamp_request(
        LampQuestionSample(
            id="lamp-guidance",
            input="按我现在的情况，如果想参与项目，最先应该补哪块？",
            profile=[
                {"id": "p1", "identity": "本科三年级", "strength": "编码还可以"},
                {"id": "p2", "weakness": "阅读论文慢", "goal": "想尽快参与项目"},
            ],
        ),
        task_name="LaMP-Local",
    )

    asyncio.run(service.answer(request))

    assert "Profile grounding guidance:" in llm.prompts[-1]
    assert "LaMP profile grounding:" in llm.prompts[-1]
    assert "Advice phrasing guidance:" in llm.prompts[-1]


def test_advising_prompt_adds_project_fit_profile_constraints(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    llm = RecordingLLMClient(booking_intent=False, answer="好的。")
    service._llm_client = llm

    request = build_lamp_request(
        LampQuestionSample(
            id="lamp-project-fit",
            input="按我现在的情况，如果想参与项目，最先应该补哪块？",
            profile=[
                {"id": "p1", "identity": "本科三年级", "strength": "编码还可以"},
                {"id": "p2", "weakness": "阅读论文慢", "goal": "想尽快参与项目"},
            ],
        ),
        task_name="LaMP-Local",
    )

    asyncio.run(service.answer(request))

    assert (
        "Before the checklist, briefly restate one concrete strength, weakness, or constraint"
        in llm.prompts[-1]
    )
    assert "建议先..." in llm.prompts[-1]


def test_advising_prompt_adds_collaboration_preparation_constraints(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    llm = RecordingLLMClient(booking_intent=False, answer="好的。")
    service._llm_client = llm

    request = build_lamp_request(
        LampQuestionSample(
            id="lamp-collaboration-prep",
            input="如果我想先和老师讨论有没有合作空间，最应该先准备哪几类信息？",
            profile=[
                {"id": "p1", "identity": "外部合作方", "topic": "也在做推理服务"},
                {
                    "id": "p2",
                    "goal": "判断目标是否对齐",
                    "constraint": "暂时还没完全收敛需求",
                },
            ],
        ),
        task_name="LaMP-Local",
    )

    asyncio.run(service.answer(request))

    assert "Collaboration preparation guidance:" in llm.prompts[-1]
    assert "推理服务" in llm.prompts[-1]
    assert "目标是否对齐" in llm.prompts[-1]
    assert "合作目标', '当前问题', '边界', and '资源或现有条件'" in llm.prompts[-1]


def test_teaching_prompt_adds_course_understanding_grounding_guidance(
    tmp_path: Path,
) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    llm = TeachingIntentLLMClient(booking_intent=False, answer="好的。")
    service._llm_client = llm

    request = build_lamp_request(
        LampQuestionSample(
            id="lamp-teaching-grounding",
            input="按我的问题类型，您会建议我先理解哪一层？",
            profile=[
                {
                    "id": "p1",
                    "course": "推理引擎课程",
                    "question": "为什么局部 kernel 优化不一定带来端到端收益",
                },
                {
                    "id": "p2",
                    "goal": "先把课上概念理解清楚",
                    "constraint": "不是来问选题",
                },
            ],
        ),
        task_name="LaMP-Local",
    )

    asyncio.run(service.answer(request))

    assert "Teaching grounding guidance:" in llm.prompts[-1]
    assert "端到端" in llm.prompts[-1]
    assert "kernel 优化" in llm.prompts[-1]
    assert "不必转成科研方向讨论" in llm.prompts[-1]


def test_chat_returns_traceable_basis_for_knowledge_hits(tmp_path: Path) -> None:
    settings = AppSettings(knowledge_base_dir=tmp_path, knowledge_backend="local")
    service = DigitalTwinService(settings)
    service._llm_client = IntentAwareLLMClient(
        booking_intent=False,
        answer="FlowRAG 主要解决检索与生成协同时的信息流组织问题。",
    )
    service.add_knowledge(
        KnowledgeDocumentCreate(
            title="FlowRAG 论文解读",
            content="FlowRAG 关注在复杂问答流程中组织检索结果和生成步骤，提高回答质量与可控性。",
            tags=["research", "paper-digest"],
            source_name="homepage:contents/research_papers/flowrag.pdf",
        )
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                question="FlowRAG 主要做什么？",
            )
        )
    )

    assert any(item.basis_label == "论文资料" for item in response.answer_basis)
    assert any(
        item.source_label == "个人主页 / 研究论文 PDF / flowrag.pdf"
        for item in response.answer_basis
    )


def test_chat_reuses_booking_preference_profile_memory_in_follow_up_prompt(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "conversation-memory",
    )
    service = DigitalTwinService(settings)
    llm = KeywordIntentLLMClient(answer="好的，我会结合你的历史预约偏好来提醒你。")
    service._llm_client = llm
    service._email_notifier = RecordingNotifier()

    booking_response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-booking-profile",
                question="请帮我预约 2026-05-26 15:00 讨论论文进展",
            )
        )
    )

    assert booking_response.workflow_action == "book_meeting"

    follow_up = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-booking-profile",
                question="根据你记得的我的预约习惯，提醒我还要准备什么？",
            )
        )
    )

    assert follow_up.workflow_action == "advise_only"
    assert len(llm.prompts) == 1
    assert "Stable student profile memory:" in llm.prompts[-1]
    assert "常见预约主题：论文进展" in llm.prompts[-1]
    assert "最近一次明确给出的时间偏好：2026-05-26 15:00" in llm.prompts[-1]
    assert "预约联系邮箱：alice@example.com" in llm.prompts[-1]


def test_chat_routes_sensitive_issue_to_human_handoff(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path, escalation_queue_dir=tmp_path / "escalations"
    )
    service = DigitalTwinService(settings)
    service._llm_client = IntentAwareLLMClient(booking_intent=False, answer="不应调用")

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-human-handoff",
                question="我想正式投诉这次成绩处理有误，请你马上联系老师。",
            )
        )
    )

    assert response.workflow_action == "human_handoff"
    assert response.decision_mode == "human_handoff"
    assert response.escalation_record is not None
    assert response.escalation_record.route == "human_handoff"
    assert response.escalation_record.status == "待处理"
    assert "本人直接处理" in response.answer


def test_chat_routes_review_request_to_pending_queue(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path, escalation_queue_dir=tmp_path / "escalations"
    )
    service = DigitalTwinService(settings)
    service._llm_client = IntentAwareLLMClient(booking_intent=False, answer="不应调用")

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-review-queue",
                question="能不能破例帮我把截止时间延期一天？",
            )
        )
    )

    assert response.workflow_action == "review_queue"
    assert response.decision_mode == "review_queue"
    assert response.escalation_record is not None
    assert response.escalation_record.route == "review_queue"
    assert response.escalation_record.status == "待处理"
    assert "待审核队列" in response.answer


def test_chat_reuses_collaboration_preference_profile_memory_in_follow_up_prompt(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "conversation-memory",
    )
    service = DigitalTwinService(settings)
    llm = RecordingLLMClient(
        booking_intent=False,
        answer="建议先准备 agenda、当前 blocker、相关 draft，再带上你最想讨论的问题。",
    )
    service._llm_client = llm

    asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-collaboration-profile",
                question="和老师约时间前，我应该先准备什么？",
            )
        )
    )

    asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-collaboration-profile",
                question="按你记得的我的沟通偏好，我这次找你前应该先准备什么？",
            )
        )
    )

    assert len(llm.prompts) == 2
    assert "Stable student profile memory:" in llm.prompts[-1]
    assert "该用户适合按清单式准备沟通材料" in llm.prompts[-1]
    assert "agenda、current blocker、draft、问题清单" in llm.prompts[-1]


def test_chat_returns_recommended_reading_and_resource_actions(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
    )
    service = DigitalTwinService(settings)
    service._llm_client = RecordingLLMClient(
        booking_intent=False,
        answer="建议先阅读相关课程资料，再准备 agenda 和问题清单。",
    )
    service.add_knowledge(
        {
            "title": "Tutorial 7：和老师约时间前的准备指南",
            "content": "和老师约时间前，建议先准备 agenda、当前 blocker 和性能分析方法。",
            "tags": ["teaching", "tutorial", "courseware", "advising"],
            "source_name": "tutorials/week7.md",
        }
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-followup-actions",
                question="和老师约时间前，我应该先准备什么？",
            )
        )
    )

    action_types = {action.action_type for action in response.follow_up_actions}
    assert "recommended_reading" in action_types
    assert "todo_review" in action_types
    assert "office_hour_recommendation" in action_types
    assert "course_resource_recommendation" in action_types
    assert any("Tutorial 7" in action.title for action in response.follow_up_actions)


def test_chat_sanitizes_internalish_follow_up_reading_titles(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
    )
    service = DigitalTwinService(settings)
    service._llm_client = RecordingLLMClient(
        booking_intent=False,
        answer="建议你先看看这条 FAQ，再准备 agenda 和问题清单。",
    )
    service.add_knowledge(
        {
            "title": "FAQ草稿 | 指导建议 | 和老 / 备什 / 我应",
            "content": "和老师约时间前，建议先准备 agenda、当前 blocker、已有 draft/结果，以及最想确认的 2-3 个问题。",
            "tags": ["advising", "faq"],
            "source_name": "analytics-gap:demo",
        }
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-followup-sanitized-reading",
                question="和老师约时间前，我应该先准备什么？",
            )
        )
    )

    reading_actions = [
        action
        for action in response.follow_up_actions
        if action.action_type == "recommended_reading"
    ]
    assert reading_actions
    assert reading_actions[0].title == "先看：FAQ草稿 · 指导建议"
    assert "和老 / 备什 / 我应" not in reading_actions[0].title


def test_service_normalizes_published_gap_documents_on_boot(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
        knowledge_gap_draft_dir=tmp_path / "gap-drafts",
    )
    service = DigitalTwinService(settings)
    draft = service._knowledge_gap_draft_store.upsert_generated_draft(
        cluster_id="demo-cluster",
        interaction_domain="advising",
        label="指导建议｜和老 / 备什 / 我应",
        reason="近期多次问到会前准备，但标准答复还不够稳定。",
        suggested_action="补充会前准备模板、议程模板和常见指导边界说明。",
        sample_questions=["和老师约时间前，我应该先准备什么？"],
        title="FAQ草稿｜指导建议｜和老 / 备什 / 我应",
        content="建议 FAQ/知识正文草稿：先给 checklist。",
        tags=["analytics-gap", "draft", "faq-draft", "advising"],
        source_name="analytics-gap:demo-cluster",
    )
    legacy_document = service.add_knowledge(
        {
            "title": "FAQ草稿｜指导建议｜和老 / 备什 / 我应",
            "content": "建议 FAQ/知识正文草稿：先给 checklist。",
            "tags": ["analytics-gap", "draft", "faq-draft", "advising"],
            "source_name": "analytics-gap:demo-cluster",
        }
    )
    service._knowledge_gap_draft_store.mark_published(
        draft.draft_id, document_id=legacy_document.document_id
    )

    reloaded = DigitalTwinService(settings)
    documents = reloaded.list_knowledge()
    normalized = next(item for item in documents if item.document_id == legacy_document.document_id)
    assert normalized.source_name.startswith("knowledge-gap:advising:")
    assert normalized.title.startswith("常见问题：")
    assert "faq-draft" not in normalized.tags


def test_publish_gap_document_upserts_same_semantic_topic(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
        knowledge_gap_draft_dir=tmp_path / "knowledge-gap-drafts",
        knowledge_backend="local",
    )
    service = DigitalTwinService(settings)

    first = service._knowledge_gap_draft_store.upsert_generated_draft(
        cluster_id="cluster-a",
        interaction_domain="advising",
        label="指导建议｜会前准备",
        reason="这类问题反复出现。",
        suggested_action="补充会前准备模板、议程模板和边界说明。",
        sample_questions=["和老师约时间前，我应该先准备什么？"],
        title="FAQ草稿｜指导建议｜会前准备",
        content="建议 FAQ/知识正文草稿：先给 checklist。",
        tags=["analytics-gap", "draft", "faq-draft", "advising"],
        source_name="analytics-gap:cluster-a",
    )
    first_published = service.publish_knowledge_gap_draft(first.draft_id)

    second = service._knowledge_gap_draft_store.upsert_generated_draft(
        cluster_id="cluster-b",
        interaction_domain="advising",
        label="指导建议｜会前准备",
        reason="同类问题在不同轮次仍反复出现。",
        suggested_action="补充会前准备模板、议程模板和边界说明。",
        sample_questions=[
            "和老师约时间前，我应该先准备什么？",
            "约讨论前需要准备哪些材料？",
        ],
        title="FAQ草稿｜指导建议｜会前准备",
        content="建议 FAQ/知识正文草稿：先给 checklist。",
        tags=["analytics-gap", "draft", "faq-draft", "advising"],
        source_name="analytics-gap:cluster-b",
    )
    second_published = service.publish_knowledge_gap_draft(second.draft_id)

    documents = [
        document for document in service.list_knowledge() if "knowledge-gap" in document.tags
    ]
    assert len(documents) == 1
    assert second_published.published_document_id == first_published.published_document_id
    assert documents[0].document_id == first_published.published_document_id
    assert documents[0].source_name.startswith("knowledge-gap:advising:")


def test_service_normalizes_duplicate_published_gap_documents_on_startup(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
        knowledge_backend="local",
    )
    service = DigitalTwinService(settings)
    for suffix in ("a", "b", "c"):
        service.add_knowledge(
            KnowledgeDocumentCreate(
                title="常见问题：和老师约时间前，我应该先准备什么",
                content="主题：指导建议\n适用问题：\n- 和老师约时间前，我应该先准备什么？\n\n标准说明：补充会前准备模板、议程模板和常见指导边界说明。\n补充背景：指导建议类问题经常需要追问或人工接管。\n使用边界：涉及老师本人审批、例外政策或个性化承诺时，需要人工确认。",
                tags=["advising", "faq", "knowledge-gap"],
                source_name=f"knowledge-gap:{suffix}",
            )
        )

    reloaded = DigitalTwinService(settings)
    documents = [
        document for document in reloaded.list_knowledge() if "knowledge-gap" in document.tags
    ]
    assert len(documents) == 1
    assert documents[0].source_name.startswith("knowledge-gap:advising:")


def test_service_normalizes_repeated_gap_title_prefixes_on_startup(
    tmp_path: Path,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
        knowledge_backend="local",
    )
    service = DigitalTwinService(settings)
    for title, source_name in (
        ("常见问题：和老师约时间前，我应该先准备什么", "knowledge-gap:a"),
        ("常见问题：常见问题：和老师约时间前，我应该先准备什么", "knowledge-gap:b"),
    ):
        service.add_knowledge(
            KnowledgeDocumentCreate(
                title=title,
                content="主题：指导建议\n适用问题：\n- 和老师约时间前，我应该先准备什么？\n\n标准说明：补充与当前问题直接相关的标准说明、准备清单和边界提醒。\n补充背景：近期多次出现相似提问，现有标准材料还不够集中。\n使用边界：涉及老师本人审批、例外政策或个性化承诺时，需要人工确认。",
                tags=["advising", "faq", "knowledge-gap"],
                source_name=source_name,
            )
        )

    reloaded = DigitalTwinService(settings)
    documents = [
        document for document in reloaded.list_knowledge() if "knowledge-gap" in document.tags
    ]
    assert len(documents) == 1
    assert documents[0].title == "常见问题：和老师约时间前，我应该先准备什么"


def test_chat_auto_web_searches_when_local_knowledge_is_empty(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
        knowledge_backend="local",
        conversation_memory_index_type="segment",
        web_search_enabled=True,
        web_search_auto_trigger=True,
    )
    service = DigitalTwinService(settings)
    service._llm_client = RecordingLLMClient(
        booking_intent=False,
        answer="可以先从 vLLM、SGLang、PagedAttention 和 prefix caching 这些关键词入手。",
    )

    monkeypatch.setattr(
        "sage_faculty_twin.web_search.WebSearchClient.search",
        lambda self, query, max_results=None: [
            WebSearchResult(
                title="vLLM Documentation",
                url="https://docs.vllm.ai/",
                snippet="vLLM is a fast and easy-to-use library for LLM inference.",
                score=9.5,
            )
        ],
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-auto-web-search",
                question="如果我想系统了解大模型推理系统，建议先看哪些系统或关键词？",
            )
        )
    )

    assert [hit.title for hit in response.web_search_hits] == ["vLLM Documentation"]
    assert any(item.basis_label == "联网检索" for item in response.answer_basis)


def test_positive_feedback_writes_web_sources_back_to_knowledge_base(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
        knowledge_backend="local",
        conversation_memory_index_type="segment",
        web_search_enabled=True,
        web_search_auto_trigger=True,
    )
    service = DigitalTwinService(settings)
    service._llm_client = RecordingLLMClient(
        booking_intent=False,
        answer="我建议先看 vLLM 文档，再对照 SGLang 的调度与缓存机制。",
    )

    monkeypatch.setattr(
        "sage_faculty_twin.web_search.WebSearchClient.search",
        lambda self, query, max_results=None: [
            WebSearchResult(
                title="vLLM Documentation",
                url="https://docs.vllm.ai/",
                snippet="Overview of serving, PagedAttention, prefix caching, and scheduling.",
                score=9.5,
            )
        ],
    )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-feedback-web-writeback",
                question="如果我想系统了解大模型推理系统，建议先看哪些系统或关键词？",
            )
        )
    )

    assert response.exchange_id is not None

    feedback = service.submit_chat_feedback(
        {
            "exchange_id": response.exchange_id,
            "rating": "up",
        }
    )

    assert len(feedback.knowledge_write_backs) == 1
    assert feedback.knowledge_write_backs[0].created is True
    documents = service.list_knowledge()
    assert len(documents) == 1
    assert documents[0].title == "联网补充：vLLM Documentation"
    assert documents[0].source_name is not None
    assert documents[0].source_name.startswith("feedback-web:")
    assert "review:pending" in documents[0].tags
    assert documents[0].metadata["review_status"] == "pending"
    assert "https://docs.vllm.ai" in documents[0].content
    assert "如果我想系统了解大模型推理系统" in documents[0].content

    second_response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Bob",
                student_email="bob@example.com",
                course_context="科研指导",
                conversation_id="conv-feedback-web-writeback-2",
                question="我想先快速理解 vLLM 的推理执行路径，应该看什么？",
                web_search=True,
            )
        )
    )
    second_feedback = service.submit_chat_feedback(
        {
            "exchange_id": second_response.exchange_id,
            "rating": "up",
        }
    )

    assert len(second_feedback.knowledge_write_backs) == 1
    assert second_feedback.knowledge_write_backs[0].created is False
    assert len(service.list_knowledge()) == 1


def test_review_feedback_web_document_and_delete_it(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
        knowledge_backend="local",
    )
    service = DigitalTwinService(settings)

    created = service.add_knowledge(
        KnowledgeDocumentCreate(
            title="联网补充：vLLM Documentation",
            content="用于补充推理系统入门链接。",
            tags=["feedback-web", "review:pending", "freshness:web"],
            source_name="feedback-web:https://docs.vllm.ai/",
            metadata={
                "source_url": "https://docs.vllm.ai/",
                "review_status": "pending",
                "freshness_status": "web",
            },
        )
    )

    reviewed = service.review_knowledge_document(
        created.document_id,
        {"action": "approve"},
    )
    assert reviewed.action == "approve"
    assert reviewed.document is not None
    assert reviewed.document.is_feedback_web is True
    assert reviewed.document.review_status == "approved"
    assert reviewed.document.freshness_status == "web"
    assert reviewed.document.reviewable is False
    assert reviewed.document.metadata["review_status"] == "approved"
    assert reviewed.document.metadata["freshness_status"] == "web"
    assert "review:approved" in reviewed.document.tags
    assert "review:pending" not in reviewed.document.tags

    summary = service.list_knowledge_review_summary(limit=10)
    assert summary.total_documents == 1
    assert summary.feedback_web_documents == 1
    assert summary.pending_documents == 0
    assert summary.approved_documents == 1
    assert summary.reviewable_documents == 0
    assert summary.pending_items == []

    deleted = service.delete_knowledge_document(created.document_id)
    assert deleted.action == "delete"
    assert deleted.deleted_count == 1
    assert service.list_knowledge() == []


def test_pending_feedback_web_is_downgraded_for_non_admin_search(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
        knowledge_backend="local",
    )
    service = DigitalTwinService(settings)

    service.add_knowledge(
        KnowledgeDocumentCreate(
            title="推理系统入门",
            content="PagedAttention 与 prefix caching 的基础说明。",
            tags=["research", "overview"],
            source_name="manual:notes",
        )
    )
    service.add_knowledge(
        KnowledgeDocumentCreate(
            title="推理系统入门",
            content="PagedAttention 与 prefix caching 的基础说明。",
            tags=["research", "overview", "feedback-web", "review:pending"],
            source_name="feedback-web:https://docs.vllm.ai/",
            metadata={
                "review_status": "pending",
                "freshness_status": "web",
            },
        )
    )

    visitor_hits = service.search_knowledge("PagedAttention prefix caching", admin_role=None).hits
    assert len(visitor_hits) >= 1
    # The manual document must be visible and rank first.
    assert visitor_hits[0].source_name == "manual:notes"

    admin_hits = service.search_knowledge(
        "PagedAttention prefix caching",
        admin_role="super_admin",
    ).hits
    assert len(admin_hits) >= 2
    admin_sources = [h.source_name for h in admin_hits]
    assert any(str(s).startswith("feedback-web:") for s in admin_sources)


def test_service_normalizes_truncated_gap_titles_on_startup(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
        knowledge_backend="local",
    )
    service = DigitalTwinService(settings)
    for title, source_name in (
        ("常见问题：和老师约时间前，我应该先准备什么", "knowledge-gap:a"),
        ("常见问题：常见问…", "knowledge-gap:b"),
    ):
        service.add_knowledge(
            KnowledgeDocumentCreate(
                title=title,
                content="主题：指导建议\n适用问题：\n- 和老师约时间前，我应该先准备什么？\n- 和老师约时间前，我应该先准备什么？\n\n标准说明：补充与当前问题直接相关的标准说明、准备清单和边界提醒。\n补充背景：近期多次出现相似提问，现有标准材料还不够集中。\n使用边界：涉及老师本人审批、例外政策或个性化承诺时，需要人工确认。",
                tags=["advising", "faq", "knowledge-gap"],
                source_name=source_name,
            )
        )

    reloaded = DigitalTwinService(settings)
    documents = [
        document for document in reloaded.list_knowledge() if "knowledge-gap" in document.tags
    ]
    assert len(documents) == 1
    assert documents[0].title == "常见问题：和老师约时间前，我应该先准备什么"


def test_chat_dedupes_same_title_published_gap_hits(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
        knowledge_backend="local",
    )
    service = DigitalTwinService(settings)
    service._llm_client = RecordingLLMClient(
        booking_intent=False,
        answer="建议你先按会前准备清单整理材料。",
    )
    for suffix in ("a", "b", "c"):
        service.add_knowledge(
            KnowledgeDocumentCreate(
                title="常见问题：和老师约时间前，我应该先准备什么",
                content="主题：指导建议\n适用问题：\n- 和老师约时间前，我应该先准备什么？\n\n标准说明：补充会前准备模板、议程模板和常见指导边界说明。",
                tags=["advising", "faq", "knowledge-gap"],
                source_name=f"knowledge-gap:{suffix}",
            )
        )

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                question="和老师约时间前，我应该先准备什么？",
            )
        )
    )

    knowledge_basis = [
        item
        for item in response.answer_basis
        if item.title == "常见问题：和老师约时间前，我应该先准备什么"
    ]
    assert len(knowledge_basis) == 1


def test_confirm_booking_queues_post_meeting_follow_ups(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
        follow_up_queue_dir=tmp_path / "follow-ups",
    )
    service = DigitalTwinService(settings)
    service._email_notifier = RecordingNotifier()
    service.add_knowledge(
        {
            "title": "Meeting Prep Note",
            "content": "讨论论文时建议准备 draft 和关键问题。",
            "tags": ["teaching", "resources"],
            "source_name": "notes/meeting-prep.md",
        }
    )

    created = service.book_meeting(
        {
            "student_name": "Alice",
            "student_email": "alice@example.com",
            "topic": "论文进展",
            "preferred_start": "2026-05-26T15:00:00",
            "preferred_end": "2026-05-26T15:45:00",
        }
    )
    assert created.booking is not None

    confirmed = service.confirm_booking(created.booking.booking_id)
    assert confirmed.booking is not None
    assert confirmed.booking.status == "已确认"

    queued = service.list_follow_up_actions(status="queued")
    action_types = {item.action_type for item in queued}
    assert "post_meeting_summary" in action_types
    assert "todo_review" in action_types


def test_dispatch_due_follow_ups_sends_email(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
        follow_up_queue_dir=tmp_path / "follow-ups",
        availability_schedule_path=tmp_path / "availability.json",
    )
    service = DigitalTwinService(settings)
    notifier = RecordingNotifier()
    service._email_notifier = notifier

    created = service.book_meeting(
        {
            "student_name": "Alice",
            "student_email": "alice@example.com",
            "topic": "论文进展",
            "preferred_start": "2026-05-20T10:00:00",
            "preferred_end": "2026-05-20T10:45:00",
        }
    )
    assert created.booking is not None
    service.confirm_booking(created.booking.booking_id)

    dispatch = service.dispatch_due_follow_ups()
    assert dispatch.sent_count >= 2
    assert len(notifier.follow_up_emails) >= 2
    assert all(item.status == "sent" for item in service.list_follow_up_actions(status="sent"))


def test_health_does_not_dispatch_follow_ups(tmp_path: Path) -> None:
    settings = AppSettings(
        knowledge_base_dir=tmp_path / "knowledge-base",
        conversation_memory_dir=tmp_path / "conversation-memory",
        follow_up_queue_dir=tmp_path / "follow-ups",
    )
    service = DigitalTwinService(settings)
    notifier = RecordingNotifier()
    service._email_notifier = notifier

    service._follow_up_store.queue_action(
        booking_id="booking-1",
        student_name="Alice",
        student_email="alice@example.com",
        action_type="todo_review",
        title="会后整理 TODO",
        detail="整理讨论后的任务清单。",
        subject="请整理讨论后 TODO",
        lines=["请在会后补充 TODO 清单。"],
        due_at=None,
    )

    health = service.health()

    assert health["status"] == "ok"
    assert health["follow_up_dispatch_sent"] == "0"
    assert health["follow_up_dispatch_due"] == "1"
    assert notifier.follow_up_emails == []
    assert len(service.list_follow_up_actions(status="queued")) == 1


def test_chat_detects_invitation_code_and_short_circuits(tmp_path: Path) -> None:
    """When a student pastes the invitation code into the chat, the service
    should return an onboarding hint immediately without calling the LLM.
    """
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        lab_member_invitation_code="SAGE-LAB-2026",
        lab_member_invitation_code_enabled=True,
    )
    service = DigitalTwinService(settings)
    # Use a FailingLLMClient that raises if the LLM is called.
    service._llm_client = FailingLLMClient()

    request = ChatRequest(
        student_name="Guest",
        question="SAGE-LAB-2026",
        visitor_profile="general_visitor",
    )
    response = asyncio.run(service.answer(request))

    assert response.workflow_action == "invitation_code_detected"
    assert response.decision_mode == "onboarding"
    assert "注册" in response.answer or "邀请码" in response.answer


def test_chat_invitation_code_pattern_match(tmp_path: Path) -> None:
    """The invitation code pattern should match even when embedded in text."""
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        lab_member_invitation_code="SAGE-LAB-2026",
        lab_member_invitation_code_enabled=True,
    )
    service = DigitalTwinService(settings)
    service._llm_client = FailingLLMClient()

    request = ChatRequest(
        student_name="Guest",
        question="我的邀请码是 SAGE-LAB-2026，怎么用？",
        visitor_profile="general_visitor",
    )
    response = asyncio.run(service.answer(request))

    assert response.workflow_action == "invitation_code_detected"


def test_chat_no_invitation_code_when_disabled(tmp_path: Path) -> None:
    """When invitation code detection is disabled, codes pass through to LLM."""
    settings = AppSettings(
        knowledge_base_dir=tmp_path,
        lab_member_invitation_code="SAGE-LAB-2026",
        lab_member_invitation_code_enabled=False,
    )
    service = DigitalTwinService(settings)
    llm = RecordingLLMClient(booking_intent=False, answer="这是一篇论文。")
    service._llm_client = llm

    request = ChatRequest(
        student_name="Guest",
        question="SAGE-LAB-2026",
        visitor_profile="general_visitor",
    )
    response = asyncio.run(service.answer(request))

    # Should NOT short-circuit; goes through the normal pipeline.
    assert response.workflow_action != "invitation_code_detected"
