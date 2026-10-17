import asyncio

import pytest
from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime

from sage_faculty_twin.http_workflow import HttpWorkflow, HttpWorkRejected
from test_http_workflow import ingress


async def wait_finalized(flow, item):
    deadline = asyncio.get_running_loop().time() + 3
    while asyncio.get_running_loop().time() < deadline:
        record = flow.journal.read(item.delivery.stream_id)
        if record["tasks"].get("finalize", {}).get("status") == "done":
            return record
        await asyncio.sleep(0.01)
    pytest.fail("durable HTTP finalizer did not complete")


def test_failed_answer_publishes_one_safe_terminal_event_and_reopens(tmp_path):
    async def scenario():
        path = str(tmp_path / "state.db")
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
        calls = []

        async def authorize(item):
            return True

        async def recover(item):
            return None

        async def answer(item, *, admission_guard):
            calls.append(1)
            raise OSError("private upstream diagnostic must not escape")

        item = ingress()
        flow = HttpWorkflow(state, authorize=authorize, recover=recover, answer=answer,
                            max_attempts=1, poll_interval=0.01)
        try:
            await flow.admit(item)
            with pytest.raises(HttpWorkRejected, match="retry_exhausted"):
                await flow.wait(item, timeout=3)
            await wait_finalized(flow, item)
            page = flow.events.read(item.delivery.stream_id)
            assert page["closed"] and len(page["events"]) == 1
            event = page["events"][0]
            assert event["id"] == "terminal"
            assert event["payload"]["type"] == "error"
            assert event["payload"]["code"] == "retry_exhausted"
            assert "private upstream" not in str(page)
            assert calls == [1]
        finally:
            await flow.aclose()
            state.close()
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
        reopened = HttpWorkflow(state, authorize=authorize, recover=recover, answer=answer)
        try:
            await reopened.admit(item)
            with pytest.raises(HttpWorkRejected):
                await reopened.wait(item, timeout=3)
            assert reopened.events.read(item.delivery.stream_id) == page
            assert calls == [1]
        finally:
            await reopened.aclose()
            state.close()

    asyncio.run(scenario())


@pytest.mark.parametrize("canonical", [False, True])
def test_legacy_failed_turn_gets_finalizer_without_overwriting_committed_answer(tmp_path, canonical):
    async def scenario():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))

        async def forbidden(*args, **kwargs):
            pytest.fail("failure migration must not regenerate or disclose the question")

        flow = HttpWorkflow(state, authorize=forbidden, recover=forbidden, answer=forbidden, poll_interval=0.01)
        item = ingress()
        key = item.delivery.stream_id
        flow.journal.commit(key, result={"owner_id": item.request.owner_id},
                            tasks={"answer": item.model_dump(mode="json")})
        lease = flow.journal.claim(key, "answer", lease_seconds=10)
        flow.journal.fail(lease, code="retry_exhausted")
        if canonical:
            flow.events.append(key, event_id="answer_done", payload={"type": "answer_done", "response": "canonical"})
        try:
            await flow.start()
            await wait_finalized(flow, item)
            page = flow.events.read(key)
            assert len(page["events"]) == 1
            assert page["closed"] is not canonical
            assert page["events"][0]["payload"]["type"] == ("answer_done" if canonical else "error")
        finally:
            await flow.aclose()
            state.close()

    asyncio.run(scenario())


def test_finalizer_migration_rejects_live_legacy_worker(tmp_path):
    async def scenario():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))

        async def unused(*args, **kwargs):
            pytest.fail("migration must fail before dispatch")

        flow = HttpWorkflow(state, authorize=unused, recover=unused, answer=unused)
        item = ingress()
        key = item.delivery.stream_id
        flow.journal.commit(key, result={}, tasks={"answer": item.model_dump(mode="json")})
        flow.journal.claim(key, "answer", lease_seconds=60)
        try:
            with pytest.raises(ValueError, match="live_leases"):
                await flow.start()
            assert set(flow.journal.read(key)["tasks"]) == {"answer"}
        finally:
            await flow.aclose()
            state.close()

    asyncio.run(scenario())


def test_failure_event_and_finalizer_ack_roll_back_together(tmp_path, monkeypatch):
    async def scenario():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))

        async def unused(*args, **kwargs):
            pytest.fail("manual fenced completion must not invoke a model")

        flow = HttpWorkflow(state, authorize=unused, recover=unused, answer=unused)
        item = ingress()
        key = item.delivery.stream_id
        payload = item.model_dump(mode="json")
        flow.journal.commit(key, result={}, tasks={"answer": payload, "finalize": payload},
                            dependencies={"finalize": ["answer"]}, settled=["finalize"])
        flow.journal.fail(flow.journal.claim(key, "answer", lease_seconds=60), code="retry_exhausted")
        lease = flow.journal.claim(key, "finalize", lease_seconds=60)
        result = await flow._handle(lease)
        mutations = flow._completion_events(lease, result)
        original = state._backend._put_uncommitted

        def fail_event(slot, entry):
            if slot[0] == "sage-event-journal-v1":
                raise OSError("injected event write failure")
            return original(slot, entry)

        try:
            monkeypatch.setattr(state._backend, "_put_uncommitted", fail_event)
            with pytest.raises(OSError, match="injected"):
                flow.journal.finish(lease, result=result, mutations=mutations)
            assert flow.journal.read(key)["tasks"]["finalize"]["status"] == "running"
            assert flow.events.read(key)["events"] == []
            monkeypatch.setattr(state._backend, "_put_uncommitted", original)
            flow.journal.finish(lease, result=result, mutations=mutations)
            assert flow.journal.read(key)["tasks"]["finalize"]["status"] == "done"
            assert flow.events.read(key)["closed"]
            assert len(flow.events.read(key)["events"]) == 1
        finally:
            await flow.aclose()
            state.close()

    asyncio.run(scenario())
