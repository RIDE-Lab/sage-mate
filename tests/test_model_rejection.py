"""Deterministic provider rejection survives HTTP, streaming and worker boundaries."""
import asyncio
from types import SimpleNamespace

import httpx
import pytest
from sage.runtime.model_errors import ModelRequestRejected
from sage.runtime.flownet.client.runtime_client import FlowRequestOutcomeError
from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime

from sage_faculty_twin.http_workflow import HttpWorkflow
from sage_faculty_twin.llm_client import _raise_completion_status
from sage_faculty_twin.slack_workflow import SlackWorkflow


@pytest.mark.parametrize("streaming", [False, True])
@pytest.mark.parametrize("status,code", [(400, "prompt_capacity"), (401, "model_access_denied"),
                                        (422, "model_request_invalid"), (429, None), (503, None)])
def test_completion_status(streaming, status, code):
    transport = httpx.MockTransport(lambda request: httpx.Response(status, json={"error": {
        "message": "No backend can safely fit the estimated 158759-token request context"}}))
    with httpx.Client(transport=transport) as client:
        if streaming:
            with client.stream("POST", "https://fixture/v1/responses") as response:
                with pytest.raises(ModelRequestRejected if code else httpx.HTTPStatusError) as caught:
                    _raise_completion_status(response)
        else:
            response = client.post("https://fixture/v1/responses")
            with pytest.raises(ModelRequestRejected if code else httpx.HTTPStatusError) as caught:
                _raise_completion_status(response)
    if code:
        # 422 is a capacity error too when the gateway explicitly reports it.
        assert caught.value.code == ("prompt_capacity" if status == 422 else code)
        assert "158759" not in str(caught.value)


@pytest.mark.parametrize("workflow,method", [(HttpWorkflow, "_failure"), (SlackWorkflow, "_classify_failure")])
@pytest.mark.parametrize("wrapped", [False, True])
def test_terminal_rejection_policy(workflow, method, wrapped):
    instance = object.__new__(workflow)
    instance._max_attempts, instance._retry_delay = 5, 0
    error = ModelRequestRejected("prompt_capacity")
    if wrapped:
        error = FlowRequestOutcomeError(request_id="r", outcome_status="failed",
            error_type=type(error).__name__, message=str(error), error_stage="answer",
            outcome_metadata={}, outputs=[])
    result = getattr(instance, method)(SimpleNamespace(attempt=1), error)
    assert result.code == "prompt_capacity"
    assert result.retry_delay is None


def test_http_rejected_producer_runs_once(tmp_path):
    from test_http_workflow import ingress

    async def scenario():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "work.db"))
        calls = []
        async def authorize(item):
            return True
        async def recover(item):
            return None
        async def answer(item, **kwargs):
            calls.append(item.request_id)
            raise FlowRequestOutcomeError(request_id="r", outcome_status="failed",
                error_type="ModelRequestRejected", message="prompt_capacity",
                error_stage="answer", outcome_metadata={}, outputs=[])
        flow = HttpWorkflow(state, authorize=authorize, recover=recover, answer=answer, retry_delay=0)
        try:
            item = ingress()
            await flow.admit(item)
            for _ in range(100):
                record = flow.journal.read(item.delivery.stream_id)
                if record["tasks"]["finalize"]["status"] == "done":
                    break
                await asyncio.sleep(0.02)
            assert record["tasks"]["finalize"]["status"] == "done"
            assert record["tasks"]["answer"]["attempt"] == 1
            assert record["tasks"]["answer"]["failure_code"] == "prompt_capacity"
            assert len(calls) == 1
        finally:
            await flow.aclose()
            state.close()
    asyncio.run(scenario())
