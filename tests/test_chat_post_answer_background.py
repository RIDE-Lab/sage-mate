"""Regression tests for the post-answer background-execution split.

The latency split keeps the conversation-memory write on the critical path
so an immediate follow-up cannot race its preceding turn. The remaining
post-answer stages (`memory_profile_consolidate`, `follow_up_plan`, and
`memory_usefulness_score`) stay off the critical path. With
``DIGITAL_TWIN_POST_ANSWER_BACKGROUND=true`` (default) and a trace callback
attached, ``service.answer`` returns the rendered ``ChatResponse``
after the durable exchange write and runs the remaining side-effects
on a background ``asyncio.create_task`` instead of blocking the HTTP
response.
"""

from __future__ import annotations

import asyncio
import threading
from pathlib import Path

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import ChatRequest, InteractionIntent
from sage_faculty_twin.service import (
    _CANONICAL_TRACE_ORDER,
    DigitalTwinService,
)


class _FastLLMClient:
    """LLM stand-in that replies immediately without booking flow."""
    model_name = "test-model"

    def __init__(self, answer_text: str = "已收到，下面给你建议。") -> None:
        self._answer = answer_text

    def classify_interaction_intent_sync(
        self,
        question: str,
        course_context: str | None = None,
    ) -> InteractionIntent:
        return InteractionIntent(
            action="answer",
            domain="advising",
            retrieval_scopes=["preparation", "meeting_policy", "profile"],
            exclude_scopes=["courseware"],
            decision_mode="advise_only",
            confidence=0.9,
        )

    async def classify_interaction_intent(
        self, question: str, course_context: str | None = None
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


def test_chat_returns_before_post_answer_completes_when_background_enabled(
    tmp_path: Path,
) -> None:
    """With background mode + trace_callback, ``service.answer`` resolves
    while the three non-critical post-answer stages are still in flight on a background
    ``asyncio.create_task``. The interim response carries the critical-path
    trace (10 keys) and an empty ``follow_up_actions`` list."""

    settings = AppSettings(knowledge_base_dir=tmp_path, chat_runtime_pipeline_enabled=True)
    service = DigitalTwinService(settings)
    service._llm_client = _FastLLMClient()

    # Use a threading.Event to gate the post-answer runner so we can observe
    # whether ``service.answer`` resolves before the side-effects finish.
    persist_gate = threading.Event()

    original_run_post_answer = service._run_post_answer_inline_blocking
    started_event = threading.Event()
    finished_event = threading.Event()

    def gated_run(context, support, **kwargs):
        started_event.set()
        # Wait until the test releases the gate or 5s budget elapses.
        persist_gate.wait(timeout=5.0)
        try:
            return original_run_post_answer(context, support, **kwargs)
        finally:
            finished_event.set()

    service._run_post_answer_inline_blocking = gated_run  # type: ignore[method-assign]

    captured_steps: list[str] = []

    async def driver():
        response = await service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-bg-task2",
                question="给我一些下一步建议。",
            ),
            trace_callback=lambda step: captured_steps.append(step.key),
        )
        # Give the asyncio.create_task a chance to start the background
        # post-answer thread before we assert on it.
        for _ in range(50):
            if started_event.is_set():
                break
            await asyncio.sleep(0.02)
        # The HTTP/SSE caller can return its response now — confirm post-answer
        # has started in the background but has not yet completed.
        assert started_event.is_set(), "post-answer background task should have started"
        assert not finished_event.is_set(), (
            "post-answer should still be running on the background task — "
            "service.answer must not block on it when "
            "DIGITAL_TWIN_POST_ANSWER_BACKGROUND is on"
        )
        recent = service._conversation_store.list_recent_conversation_records(
            "conv-bg-task2", limit=1
        )
        assert recent and recent[0].question == "给我一些下一步建议。", (
            "the exchange must be durable before service.answer returns"
        )
        # Release the gate and wait for the background task to finish so we
        # can also confirm the trace_callback eventually surfaced the
        # post-answer steps.
        persist_gate.set()
        for _ in range(50):
            if finished_event.is_set():
                break
            await asyncio.sleep(0.05)
        assert finished_event.is_set()
        # Give the background callback a final tick to flush trace events.
        await asyncio.sleep(0.05)
        return response

    response = asyncio.run(driver())

    # Critical-path response: trace ends at response_render. The four
    # post-answer keys are NOT in the response itself even though they
    # eventually flowed through ``trace_callback`` (and hence SSE).
    response_keys = [step.key for step in response.workflow_trace]
    post_answer_keys = {
        "memory_persist",
        "memory_profile_consolidate",
        "follow_up_plan",
        "memory_usefulness_score",
    }
    assert post_answer_keys.isdisjoint(response_keys), (
        "Background mode must NOT include post-answer steps in the immediate "
        f"chat response. Got: {response_keys}"
    )
    assert "response_render" in response_keys
    assert "llm_answer" in response_keys

    # The trace callback emits memory_persist before the response and the
    # remaining post-answer steps after it.
    assert post_answer_keys.issubset(set(captured_steps)), (
        "Background post-answer must still publish trace steps via the "
        f"callback so the SSE stream stays informative. Got: {captured_steps}"
    )


def test_chat_runs_post_answer_inline_when_no_trace_callback(
    tmp_path: Path,
) -> None:
    """Without ``trace_callback`` (test/batch path) the inline post-answer
    runner must execute before ``service.answer`` returns, so the response
    still carries the canonical 14-step trace and the populated
    ``follow_up_actions`` / ``exchange_id`` fields."""

    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    service._llm_client = _FastLLMClient()

    response = asyncio.run(
        service.answer(
            ChatRequest(
                student_name="Alice",
                student_email="alice@example.com",
                course_context="科研指导",
                conversation_id="conv-inline-task2",
                question="给我一些下一步建议。",
            )
        )
    )

    keys = [step.key for step in response.workflow_trace]
    assert keys[-1] == "response_render"
    for required in (
        "memory_persist",
        "memory_profile_consolidate",
        "follow_up_plan",
        "memory_usefulness_score",
    ):
        assert required in keys, keys

    # Trace order matches the canonical sequence (filtered for keys present).
    canonical_filtered = [k for k in _CANONICAL_TRACE_ORDER if k in set(keys)]
    seen_filtered = [k for k in keys if k in set(canonical_filtered)]
    assert seen_filtered == canonical_filtered
