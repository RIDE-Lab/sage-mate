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
from sage_faculty_twin.memory_store import ConversationMemoryRecord
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


def test_digest_port_does_not_keep_finalized_service_alive(tmp_path):
    import gc
    import weakref
    from types import SimpleNamespace
    from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime

    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))
    service = DigitalTwinService.__new__(DigitalTwinService)
    service._post_answer_runtime_lock = threading.RLock()
    service._post_answer_runtime = None
    service._settings = SimpleNamespace(conversation_memory_dir=tmp_path)
    service._conversation_store = SimpleNamespace(consolidate_profiles=lambda record: 0, state_store=state)
    service._artifact_memory_draft_store = SimpleNamespace(create_draft=lambda **kwargs: None)
    runtime = service._get_post_answer_runtime()
    owner = weakref.ref(service)
    del service
    gc.collect()
    assert owner() is None
    assert runtime._service._closed
    assert state.peek(("test", "owner", "alive")) is None
    state.close()


class _FastLLMClient:
    """LLM stand-in that replies immediately without booking flow."""
    model_name = "test-model"

    async def aclose(self):
        pass

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
    traces = []
    runtime = service._get_post_answer_runtime()

    def fail(*args, **kwargs):
        raise OSError("injected memory persistence failure")

    monkeypatch.setattr(service._conversation_store, "persist_exchange", fail)

    async def run():
        with pytest.raises(OSError, match="injected memory persistence failure"):
            await service.answer(
                ChatRequest(student_name="Alice", question="给我一些下一步建议。",
                            conversation_id="commit-failure"),
                trace_callback=traces.append if background else None,
                on_post_answer_complete=lambda: completed.append(True),
            )
        await asyncio.sleep(0)
        await service.aclose()

    asyncio.run(run())
    assert runtime._service._thread is None
    assert service._conversation_store.list_records() == []
    assert not any(step.key == "memory_persist" and step.status == "completed" for step in traces)
    assert completed == []


@pytest.mark.parametrize("background", [True, False])
@pytest.mark.parametrize("lost_ack", [True, False])
def test_answer_atomic_failure_and_restart_recovery(tmp_path, monkeypatch, background, lost_ack):
    settings = AppSettings(knowledge_base_dir=tmp_path, post_answer_background=background)
    service = DigitalTwinService(settings)
    service._llm_client = _FastLLMClient()
    memory = service._conversation_store
    runtime = service._get_post_answer_runtime()
    captured, traces, completed = [], [], []
    original_capture = service._capture_post_answer_task

    def capture(*args):
        task = original_capture(*args)
        captured.append(task)
        return task

    monkeypatch.setattr(service, "_capture_post_answer_task", capture)
    original_put = memory.state_store._backend._put_uncommitted
    original_update = memory.state_store.update_many

    def fail_write(slot, entry):
        original_put(slot, entry)
        if slot[0] == "sage-work-journal-v1":
            raise OSError("injected journal SQL failure")

    def lose_ack(slots, transform, **kwargs):
        result = original_update(slots, transform, **kwargs)
        if any(slot[0] == "sage-work-journal-v1" for slot in slots):
            raise ConnectionError("injected commit acknowledgement loss")
        return result

    async def driver():
        with monkeypatch.context() as patch:
            if lost_ack:
                patch.setattr(memory.state_store, "update_many", lose_ack)
            else:
                patch.setattr(memory.state_store._backend, "_put_uncommitted", fail_write)
            with pytest.raises(OSError, match="injected"):
                await service.answer(ChatRequest(
                    student_name="Alice", question="给我一些下一步建议。",
                    conversation_id="atomic-service"), trace_callback=traces.append,
                    on_post_answer_complete=lambda: completed.append(True))
        assert len(captured) == 1
        task = captured[0]
        assert completed == []
        assert not any(step.key == "memory_persist" and step.status == "completed" for step in traces)
        assert memory.list_records() == []  # No acknowledgement, no local publication.
        assert runtime._service._thread is None
        saved = runtime.journal.read(task.request_id)
        sources = memory.list_conversation_records("atomic-service")
        if lost_ack:
            assert saved["result"] == task.response.model_dump(mode="json")
            assert [record.to_dict() for record in sources] == [task.record.to_dict()]
        else:
            assert saved is None
            assert sources == []
        await service.aclose()

        recovered = DigitalTwinService(settings)
        def no_chat_replay(*args, **kwargs):
            raise AssertionError("recovery replayed upstream chat")
        monkeypatch.setattr(recovered, "_get_chat_runtime", no_chat_replay)
        worker = recovered._get_post_answer_runtime()
        worker.start()
        try:
            if lost_ack:
                result = await asyncio.to_thread(worker.wait, task.request_id, timeout=5)
                assert result["response"]["answer"] == task.response.answer
                assert result["response"]["exchange_id"] == task.request_id
                assert worker.journal.read(task.request_id)["tasks"]["post_answer"]["status"] == "done"
                assert len(recovered._conversation_store.list_conversation_records("atomic-service")) == 1
            else:
                assert worker.journal.read(task.request_id) is None
                assert recovered._conversation_store.list_conversation_records("atomic-service") == []
        finally:
            await recovered.aclose()

    asyncio.run(driver())


@pytest.mark.parametrize("background", [True, False])
def test_memory_success_trace_observes_atomic_committed_snapshot(tmp_path, background):
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path,
                                            post_answer_background=background))
    service._llm_client = _FastLLMClient()
    committed = []

    def trace(step):
        if step.key != "memory_persist" or step.status != "completed":
            return
        records = service._conversation_store.list_conversation_records("atomic-success")
        assert len(records) == 1
        record = records[0]
        saved = service._get_post_answer_runtime().journal.read(record.memory_id)
        assert saved["result"]["exchange_id"] == record.memory_id
        assert saved["result"]["answer"] == record.answer
        assert saved["result"]["memory_write_back"] is True
        snapshot = ConversationMemoryRecord.from_dict(saved["tasks"]["post_answer"]["payload"]["record"])
        assert snapshot.to_dict() == record.to_dict()
        assert step.duration_ms is None  # Immutable pre-commit snapshot cannot know it.
        committed.append(record.memory_id)

    async def driver():
        try:
            response = await service.answer(ChatRequest(student_name="Alice",
                question="给我一些下一步建议。", conversation_id="atomic-success"), trace_callback=trace)
            assert committed == [response.exchange_id]
            assert response.memory_write_back
        finally:
            await service.aclose()

    asyncio.run(driver())


def test_unexpected_legacy_journal_is_preserved_and_blocks_admission(tmp_path):
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))
    legacy = service._settings.conversation_memory_dir / "post_answer_work.sqlite3"
    legacy.write_bytes(b"legacy journal must not be silently abandoned")
    try:
        with pytest.raises(RuntimeError, match="legacy_work_journal_requires_offline_migration"):
            service._get_post_answer_runtime()
        assert service._post_answer_runtime is None
        assert legacy.read_bytes() == b"legacy journal must not be silently abandoned"
        assert service._conversation_store.list_records() == []
    finally:
        asyncio.run(service.aclose())


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


def test_artifact_intent_is_durable_before_background_destination_finishes(tmp_path):
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "memory", artifact_memory_draft_dir=tmp_path / "drafts"))
    service._llm_client = _FastLLMClient()
    gate, started, completed = threading.Event(), threading.Event(), threading.Event()
    original = service._artifact_memory_draft_store.create_draft
    traces = []

    def gated(**kwargs):
        started.set()
        if not gate.wait(timeout=5):
            raise TimeoutError("test artifact gate")
        return original(**kwargs)

    service._artifact_memory_draft_store.create_draft = gated

    async def driver():
        try:
            response = await asyncio.wait_for(service.answer(ChatRequest(
                student_name="Alice", student_email="alice@example.com", visitor_profile="lab_member",
                conversation_id="archive-background",
                question="把我上传的 proposal draft 记录成后续跟进材料，后面继续讨论实验计划。",
                attachments=[{"file_name": "proposal-draft.md", "media_type": "text/markdown",
                              "text_content": "# Full evidence\n\n- experiment plan"}],
            ), trace_callback=lambda step: traces.append(step), on_post_answer_complete=completed.set), timeout=3)
            assert await asyncio.to_thread(started.wait, 2)
            state = service._get_post_answer_runtime().journal.read(response.exchange_id)
            assert state["tasks"]["post_answer"]["payload"]["artifact_draft"]["artifact_names"] == ["proposal-draft.md"]
            assert state["tasks"]["post_answer"]["status"] != "done"
            assert service._artifact_memory_draft_store.count_drafts() == 0
            assert not any(step.key == "artifact_memory_writeback" for step in traces)
            assert not completed.is_set()
            gate.set()
            assert await asyncio.to_thread(completed.wait, 5)
            assert service._artifact_memory_draft_store.count_drafts() == 1
            artifact_traces = [step for step in traces if step.key == "artifact_memory_writeback"]
            assert len(artifact_traces) == 1 and artifact_traces[0].status == "completed"
        finally:
            gate.set()
            if service._post_answer_observers:
                await asyncio.gather(*service._post_answer_observers, return_exceptions=True)

    asyncio.run(driver())


def test_digest_is_durable_before_background_generation_finishes(tmp_path):
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "memory", context_digest_dir=tmp_path / "digests",
        context_digest_turn_threshold=2))
    service._llm_client = _FastLLMClient()
    first = service._conversation_store.prepare_exchange(
        ChatRequest(student_name="Alice", question="earlier question"),
        conversation_id="digest-background", answer="earlier decision", workflow_action="answer",
        interaction_domain=None, knowledge_hit_count=0, booking_result=None)
    service._conversation_store.persist_exchange(first)
    gate, started, completed = threading.Event(), threading.Event(), threading.Event()
    original = service._refresh_saved_digest
    traces = []
    def gated(conversation_id, intent):
        started.set()
        if not gate.wait(timeout=5):
            raise TimeoutError("test digest gate")
        return original(conversation_id, intent)
    service._refresh_saved_digest = gated

    async def driver():
        try:
            response = await asyncio.wait_for(service.answer(ChatRequest(
                student_name="Alice", conversation_id="digest-background",
                question="给我一些下一步建议。"), trace_callback=traces.append,
                on_post_answer_complete=completed.set), timeout=3)
            assert await asyncio.to_thread(started.wait, 2)
            state = service._get_post_answer_runtime().journal.read(response.exchange_id)
            saved = state["tasks"]["post_answer"]
            assert saved["payload"]["digest"] == {"threshold": 2, "target_chars": 1500, "use_llm": False}
            assert saved["status"] != "done"
            assert service._digest_store.get_digest("digest-background") is None
            assert not any(step.key == "context_digest" for step in traces)
            assert not completed.is_set()
            # A config edit after admission must not change the saved policy.
            service._settings.context_digest_llm_enabled = True
            service._settings.context_digest_max_chars = 200
            gate.set()
            assert await asyncio.to_thread(completed.wait, 5)
            assert service._digest_store.get_digest("digest-background").turns_summarized == 2
            digest_traces = [step for step in traces if step.key == "context_digest"]
            assert len(digest_traces) == 1 and digest_traces[0].status == "completed"
        finally:
            gate.set()
            await service.aclose()
    asyncio.run(driver())


def test_manual_service_uses_same_journal_worker_and_respects_disabled_mode(tmp_path):
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path,
        conversation_memory_dir=tmp_path / "memory", context_digest_dir=tmp_path / "digests"))
    service._llm_client = _FastLLMClient()
    record = service._conversation_store.prepare_exchange(
        ChatRequest(student_name="Alice", question="manual refresh"), conversation_id="manual",
        answer="complete answer", workflow_action="answer", interaction_domain=None,
        knowledge_hit_count=0, booking_result=None)
    service._conversation_store.persist_exchange(record)
    caller = threading.get_ident()
    calls = []
    original = service._refresh_saved_digest
    def observe(conversation_id, intent):
        calls.append(threading.get_ident())
        return original(conversation_id, intent)
    service._refresh_saved_digest = observe
    try:
        service._settings.context_digest_enabled = False
        assert not service.compress_conversation_context("manual")["ok"]
        assert not calls
        service._settings.context_digest_enabled = True
        assert not service.compress_conversation_context("")["ok"]
        result = service.compress_conversation_context("manual")
        assert result["ok"] and result["turns_incorporated"] == 1
        assert len(calls) == 1 and calls[0] != caller
        assert service._digest_store.get_digest("manual").turns_summarized == 1
    finally:
        asyncio.run(service.aclose())


def test_chat_runs_post_answer_inline_when_no_trace_callback(
    tmp_path: Path,
) -> None:
    """Without ``trace_callback`` (test/batch path) the inline post-answer
    runner must execute before ``service.answer`` returns, so the response
    still carries the canonical trace and the populated
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
