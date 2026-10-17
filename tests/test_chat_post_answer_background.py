"""Regression tests for delivery modes sharing one SAGE durable worker.

The latency split keeps the conversation-memory write on the critical path
so an immediate follow-up cannot race its preceding turn. The remaining
post-answer stages (`memory_profile_consolidate`, `follow_up_plan`, and
`memory_usefulness_score`) stay off the critical path. With
``DIGITAL_TWIN_POST_ANSWER_BACKGROUND=true`` (default) and a trace callback
attached, ``service.answer`` returns the rendered ``ChatResponse``
after the durable exchange write and runs the remaining side-effects
on the native SAGE worker instead of blocking the HTTP response. An asyncio
observer only publishes committed results; it does not execute those stages.
"""

from __future__ import annotations

import asyncio
import threading
import pytest
from pathlib import Path

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import ChatRequest, InteractionIntent
from sage_faculty_twin.service import (
    _CANONICAL_TRACE_ORDER,
    DigitalTwinService,
)


def test_shutdown_keeps_journal_open_until_result_observers_finish():
    from types import SimpleNamespace

    calls = []

    async def driver():
        async def observe():
            while "drain" not in calls:
                await asyncio.sleep(0)
            assert "close" not in calls
            calls.append("observed")

        async def close_llm():
            calls.append("llm_close")

        fake = SimpleNamespace(
            _post_answer_runtime=SimpleNamespace(
                drain=lambda: calls.append("drain"), close=lambda: calls.append("close")),
            _post_answer_finalizer=SimpleNamespace(detach=lambda: calls.append("detach")),
            _post_answer_observers={asyncio.create_task(observe())},
            _chat_finalizer=None, _retrieval_finalizer=None,
            _llm_client=SimpleNamespace(aclose=close_llm),
        )
        await DigitalTwinService.aclose(fake)

    asyncio.run(driver())
    assert calls == ["drain", "observed", "close", "detach", "llm_close"]


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


def test_fact_question_uses_unified_execution_and_commit(tmp_path, monkeypatch):
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))
    service._llm_client = _FastLLMClient("当前证据不足以确认，请查看公开 Wiki。")

    def forbidden_shortcut(*args, **kwargs):
        raise AssertionError("fact response bypassed SAGE workflow")

    monkeypatch.setattr(service, "_build_lightweight_fact_response", forbidden_shortcut)
    response = asyncio.run(service.answer(ChatRequest(
        student_name="Alice", question="张老师是谁？", conversation_id="fact-unified",
    )))
    assert response.exchange_id
    assert response.memory_write_back
    keys = {step.key for step in response.workflow_trace}
    assert {"bootstrap", "knowledge_retrieve", "response_render", "memory_persist"} <= keys
    record = service._conversation_store.get_record(response.exchange_id)
    assert record.question == "张老师是谁？"
    assert record.answer == response.answer


@pytest.mark.parametrize("background", [True, False])
def test_required_memory_failure_prevents_success_and_background_work(tmp_path, monkeypatch, background):
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))
    service._llm_client = _FastLLMClient()
    completed = []
    post_answer = []

    def fail(*args, **kwargs):
        raise OSError("injected memory persistence failure")

    monkeypatch.setattr(service._conversation_store, "persist_exchange", fail)
    monkeypatch.setattr(service, "_get_post_answer_runtime",
                        lambda *args, **kwargs: post_answer.append(True))

    async def run():
        with pytest.raises(OSError, match="injected memory persistence failure"):
            await service.answer(
                ChatRequest(student_name="Alice", question="给我一些下一步建议。",
                            conversation_id="commit-failure"),
                trace_callback=(lambda step: None) if background else None,
                on_post_answer_complete=lambda: completed.append(True),
            )
        await asyncio.sleep(0)

    asyncio.run(run())
    assert post_answer == []
    assert completed == []


def test_chat_returns_before_post_answer_completes_when_background_enabled(
    tmp_path: Path,
) -> None:
    """With background mode + trace_callback, ``service.answer`` resolves
    while the three post-answer stages are still in flight on the SAGE worker.
    The interim response carries the critical-path
    trace (10 keys) and an empty ``follow_up_actions`` list."""

    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    service._llm_client = _FastLLMClient()

    # Use a threading.Event to gate the post-answer runner so we can observe
    # whether ``service.answer`` resolves before the side-effects finish.
    persist_gate = threading.Event()

    original_run_post_answer = service._conversation_store.consolidate_profiles
    started_event = threading.Event()
    finished_event = threading.Event()
    committed_event = threading.Event()

    def gated_run(record):
        started_event.set()
        # Wait until the test releases the gate or 5s budget elapses.
        persist_gate.wait(timeout=5.0)
        try:
            return original_run_post_answer(record)
        finally:
            finished_event.set()

    service._conversation_store.consolidate_profiles = gated_run  # type: ignore[method-assign]

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
            on_post_answer_complete=committed_event.set,
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
            if committed_event.is_set():
                break
            await asyncio.sleep(0.05)
        assert finished_event.is_set()
        assert committed_event.is_set()
        # Give the background callback a final tick to flush trace events.
        await asyncio.sleep(0.05)
        return response

    response = asyncio.run(driver())

    # The required memory commit is reflected immediately; remaining
    # background stages are reported only after they actually finish.
    response_keys = [step.key for step in response.workflow_trace]
    post_answer_keys = {
        "memory_persist",
        "memory_profile_consolidate",
        "follow_up_plan",
        "memory_usefulness_score",
    }
    assert (post_answer_keys - {"memory_persist"}).isdisjoint(response_keys), (
        "Background mode must NOT include post-answer steps in the immediate "
        f"chat response. Got: {response_keys}"
    )
    assert "response_render" in response_keys
    assert "llm_answer" in response_keys
    assert "memory_persist" in response_keys
    assert response.exchange_id
    assert response.memory_write_back

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
