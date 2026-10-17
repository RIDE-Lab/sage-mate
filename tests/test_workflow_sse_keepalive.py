"""SAGE-backed workflow SSE heartbeat, replay and independent delivery."""
import asyncio
import json

from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage_faculty_twin.workflow_events import WorkflowEventBroker


def make_broker():
    state = StatefulProcessRuntime(backend="memory")
    return WorkflowEventBroker(lambda: state, heartbeat_provider=lambda: 0.1)


def decode(chunk):
    return json.loads(chunk.split("data: ", 1)[1].rstrip("\n"))


def test_workflow_stream_emits_keepalive_when_idle():
    broker = make_broker()
    async def run():
        stream = broker.stream("idle")
        assert decode(await asyncio.wait_for(anext(stream), 2)) == {"type": "keepalive"}
        broker.publish_complete("idle")
        assert [decode(chunk) async for chunk in stream] == [{"type": "complete"}]
    asyncio.run(run())


def test_publish_before_subscription_is_not_lost_and_has_cursor():
    broker = make_broker()
    class Step:
        def model_dump(self, mode):
            return {"key": "demo", "title": "Demo"}
    broker.publish_step("stream", Step())
    async def run():
        stream = broker.stream("stream")
        chunk = await asyncio.wait_for(anext(stream), 2)
        assert chunk.startswith("id: 1\n")
        assert decode(chunk)["step"] == {"key": "demo", "title": "Demo"}
        assert decode(await asyncio.wait_for(anext(stream), 2)) == {"type": "keepalive"}
        await stream.aclose()
        broker.publish_complete("stream")
        assert [decode(chunk) async for chunk in broker.stream("stream", after=1)] == [{"type": "complete"}]
    asyncio.run(run())


def test_client_disconnect_does_not_cancel_or_replace_other_subscriber(tmp_path):
    path = str(tmp_path / "state.db")
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
    broker = WorkflowEventBroker(lambda: state, heartbeat_provider=lambda: 0.01)
    async def run():
        one, two = broker.stream("stream"), broker.stream("stream")
        assert decode(await anext(one))["type"] == "keepalive"
        assert decode(await anext(two))["type"] == "keepalive"
        await one.aclose()
        broker.publish_complete("stream")
        assert [decode(chunk) async for chunk in two] == [{"type": "complete"}]
    try:
        asyncio.run(run())
    finally:
        state.close()
    reopened = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
    try:
        recovered = WorkflowEventBroker(lambda: reopened)
        async def replay():
            assert [decode(chunk) async for chunk in recovered.stream("stream")] == [{"type": "complete"}]
            assert [chunk async for chunk in recovered.stream("stream", after=1)] == []
        asyncio.run(replay())
    finally:
        reopened.close()
