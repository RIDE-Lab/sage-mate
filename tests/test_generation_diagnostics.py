import asyncio

import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import ChatRequest, ChatResponse
from sage_faculty_twin.post_answer_contract import DeliveryIntent
from sage_faculty_twin.request_context import RequestCancellationController, request_cancellation_scope
from sage_faculty_twin.request_timing import RequestTimingLedger
from sage_faculty_twin.service import DigitalTwinService
from test_chat_post_answer_background import _FastLLMClient


@pytest.mark.parametrize("early", [False, True])
def test_producer_metrics_survive_canonical_event_and_new_observer(tmp_path, early):
    async def scenario():
        service = DigitalTwinService(AppSettings(runtime_dir=tmp_path,
            lab_member_invitation_code_enabled=True, lab_member_invitation_code="SAGE-LAB-2099"))
        service._llm_client = _FastLLMClient()
        controller = RequestCancellationController()
        controller.diagnostics.record_llm_call()
        controller.diagnostics.record_llm_retry()
        controller.diagnostics.record_llm_ttft(123)
        controller.diagnostics.record_llm_complete(456)
        controller.diagnostics.record_token_usage(prompt_tokens=17, completion_tokens=9, total_tokens=26)
        request = ChatRequest(owner_id="account:alice", student_name="Alice",
                              question="SAGE-LAB-2099" if early else "给我一些下一步建议。")
        delivery = DeliveryIntent(owner_id=request.owner_id, request_id="metrics")
        try:
            with request_cancellation_scope(controller):
                response = await service.answer(request, delivery=delivery, trace_callback=lambda step: None)
            events = service._get_post_answer_runtime().events.read(delivery.stream_id)["events"]
            saved = ChatResponse.model_validate(next(e["payload"]["response"] for e in events
                                                    if e["id"] == "answer_done"))
            assert saved.generation_diagnostics == response.generation_diagnostics
            assert saved.generation_diagnostics.total_tokens == 26
            # An unrelated observer's counters cannot replace the producer's.
            observer = RequestTimingLedger(trace_id="replay", budget_seconds=30)
            for _ in range(5):
                observer.runtime_diagnostics.record_llm_call()
            replay = observer.attach(saved, route="sage_workflow")
            assert replay.request_timing.llm_call_count == 1
            assert replay.request_timing.llm_retry_count == 1
            assert replay.request_timing.llm_ttft_ms == 123
            assert replay.request_timing.llm_total_duration_ms == 456
            assert replay.request_timing.llm_metrics_recorded is True
            assert replay.generation_diagnostics == saved.generation_diagnostics
        finally:
            await service.aclose()
    asyncio.run(scenario())


def test_legacy_response_does_not_claim_recorded_generation_metrics():
    response = ChatResponse(answer="old answer", owner_name="Twin", used_model="fixture")
    delivered = RequestTimingLedger(trace_id="legacy", budget_seconds=30).attach(response, route="sage_workflow")
    assert delivered.generation_diagnostics is None
    assert delivered.request_timing.llm_metrics_recorded is False


def test_real_http_replay_preserves_worker_measurements(tmp_path, monkeypatch):
    from sage_faculty_twin.request_context import request_runtime_diagnostics
    from test_http_workflow_integration import http_client

    class MeasuredModel(_FastLLMClient):
        def answer_question_sync(self, system_prompt, user_prompt):
            diagnostics = request_runtime_diagnostics()
            assert diagnostics is not None
            diagnostics.record_llm_call()
            diagnostics.record_llm_complete(42)
            diagnostics.record_token_usage(prompt_tokens=10, completion_tokens=5, total_tokens=15)
            return super().answer_question_sync(system_prompt, user_prompt)

    service = DigitalTwinService(AppSettings(runtime_dir=tmp_path, model_name="fixture"))
    service._llm_client = MeasuredModel()
    with http_client(service, monkeypatch) as client:
        kwargs = {"params": {"request_id": "measured"},
                  "json": {"student_name": "Guest", "question": "给我一些下一步建议。"}}
        first = client.post("/chat", **kwargs)
        second = client.post("/chat", **kwargs)
        assert first.status_code == second.status_code == 200
        original, replay = first.json(), second.json()
        measured = original["generation_diagnostics"]
        assert measured["llm_call_count"] > 0
        assert measured["total_tokens"] > 0
        assert replay["generation_diagnostics"] == measured
        for response in (original, replay):
            assert response["request_timing"]["llm_call_count"] == measured["llm_call_count"]
            assert response["request_timing"]["llm_metrics_recorded"] is True
