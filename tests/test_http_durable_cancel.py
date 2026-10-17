import asyncio

import pytest
from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime

from sage_faculty_twin.http_workflow import HttpWorkflow, HttpWorkRejected
from sage_faculty_twin.models import ChatResponse
from test_http_failure_delivery import wait_finalized
from test_http_workflow import ingress


@pytest.mark.parametrize("committed", [False, True])
def test_other_instance_cancels_owned_work_unless_answer_committed(tmp_path, committed):
    async def scenario():
        path = str(tmp_path / "state.db")
        first = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
        second = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
        entered, release, interrupted = asyncio.Event(), asyncio.Event(), asyncio.Event()
        item = ingress()
        response = ChatResponse(answer="canonical", owner_name="Twin", used_model="fake")

        async def authorize(item):
            return True
        async def recover(item):
            return None
        async def answer(item, *, admission_guard):
            if committed:
                prepared = flow.events.prepare_append(item.delivery.stream_id, event_id="answer_done",
                    payload={"type": "answer_done", "response": response.model_dump(mode="json")})
                def apply(values):
                    result = admission_guard.apply(values)
                    result[prepared.slot] = prepared.transform(values[prepared.slot])
                    return result
                first.update_many((admission_guard.slot, prepared.slot), apply)
            entered.set()
            try:
                await release.wait()
            except asyncio.CancelledError:
                interrupted.set()
                raise
            return response
        flow = HttpWorkflow(first, authorize=authorize, recover=recover, answer=answer, poll_interval=0.01)
        remote = HttpWorkflow(second, authorize=authorize, recover=recover, answer=answer, poll_interval=0.01)
        try:
            await flow.admit(item)
            await asyncio.wait_for(entered.wait(), 3)
            assert await remote.cancel(owner_id="account:someone_else", request_id=item.request_id) == "missing"
            assert not interrupted.is_set()
            result = await remote.cancel(owner_id=item.request.owner_id, request_id=item.request_id)
            assert result == ("protected" if committed else "cancelled")
            if committed:
                assert not interrupted.is_set()
                release.set()
                assert (await flow.wait(item, timeout=3)).answer == "canonical"
            else:
                await asyncio.wait_for(interrupted.wait(), 3)
                with pytest.raises(HttpWorkRejected, match="request_cancelled"):
                    await flow.wait(item, timeout=3)
                await wait_finalized(flow, item)
                page = flow.events.read(item.delivery.stream_id)
                assert page["closed"] and len(page["events"]) == 1
                assert page["events"][0]["payload"]["code"] == "request_cancelled"
        finally:
            release.set()
            await remote.aclose()
            await flow.aclose()
            second.close()
            first.close()
    asyncio.run(scenario())
