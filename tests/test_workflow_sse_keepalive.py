"""Regression tests for the Chat Latency Optimizations Task 4 SSE keepalive.

When the chat workflow is in-flight, the LLM stage may not emit a trace
step for tens of seconds. Without a heartbeat, Cloudflare's idle proxy
timeout (~100s on the free plan) can drop the ``/chat/workflow-events``
SSE connection mid-answer. Task 4 ensures the broker emits a typed
``{"type": "keepalive"}`` event every ``CHAT_SSE_KEEPALIVE_SECONDS`` so
the connection stays warm.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from sage_faculty_twin import api as api_module


@pytest.fixture
def short_keepalive(monkeypatch: pytest.MonkeyPatch) -> float:
    """Tighten the keepalive cadence so the test completes quickly."""

    monkeypatch.setattr(api_module, "CHAT_SSE_KEEPALIVE_SECONDS", 0.1)
    return 0.1


def test_workflow_stream_emits_keepalive_when_idle(
    short_keepalive: float,
) -> None:
    """When no event is published within the keepalive window the stream
    yields a typed ``keepalive`` JSON event so Cloudflare sees regular
    bytes from the same client."""

    request_id = "req-keepalive-idle"
    broker = api_module.WorkflowEventBroker()

    async def collect_until_keepalive() -> list[dict]:
        events: list[dict] = []
        agen = broker.stream(request_id).__aiter__()
        # Pull the first chunk; the broker has nothing queued so it should
        # yield a keepalive after ~100ms.
        chunk = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
        # SSE framing: ``data: {json}\n\n``
        assert chunk.startswith("data: ")
        payload_text = chunk[len("data: ") :].rstrip("\n")
        events.append(json.loads(payload_text))
        # Close the stream so the test does not hang.
        broker.close(request_id)
        try:
            async for chunk in agen:
                payload_text = chunk[len("data: ") :].rstrip("\n")
                events.append(json.loads(payload_text))
        except StopAsyncIteration:
            pass
        return events

    events = asyncio.run(collect_until_keepalive())
    assert events, "stream must yield at least one keepalive while idle"
    assert events[0] == {"type": "keepalive"}


def test_workflow_stream_keepalive_does_not_clobber_real_events(
    short_keepalive: float,
) -> None:
    """A real trace-step published before the keepalive timer fires is
    delivered first; the keepalive only kicks in during quiet windows."""

    request_id = "req-keepalive-mixed"
    broker = api_module.WorkflowEventBroker()

    class _StubStep:
        def model_dump(self, mode: str) -> dict[str, str]:
            return {"key": "demo", "title": "Demo"}

    async def driver() -> list[dict]:
        agen = broker.stream(request_id).__aiter__()

        async def fetch_one() -> dict:
            chunk = await asyncio.wait_for(agen.__anext__(), timeout=2.0)
            return json.loads(chunk[len("data: ") :].rstrip("\n"))

        # Kick off the first __anext__() so the stream coroutine registers
        # itself with the broker before we publish anything; otherwise the
        # publish_step would race the registration and silently drop.
        first = asyncio.create_task(fetch_one())
        # Yield enough times for ``stream`` to enter its main loop and
        # register the queue with the broker. ``stream`` registers under
        # ``self._lock`` synchronously before the first ``await`` on
        # ``queue.get``, so a single tick is enough.
        for _ in range(5):
            await asyncio.sleep(0)
            with broker._lock:  # type: ignore[attr-defined]
                if request_id in broker._streams:  # type: ignore[attr-defined]
                    break
        broker.publish_step(request_id, _StubStep())
        events: list[dict] = [await first]
        # Wait past the keepalive window for an idle heartbeat.
        events.append(await fetch_one())
        broker.close(request_id)
        try:
            async for chunk in agen:
                events.append(json.loads(chunk[len("data: ") :].rstrip("\n")))
        except StopAsyncIteration:
            pass
        return events

    events = asyncio.run(driver())
    assert events[0]["type"] == "trace-step"
    assert events[0]["step"] == {"key": "demo", "title": "Demo"}
    # The second event must be a keepalive (no other publish happened).
    assert events[1] == {"type": "keepalive"}


def test_workflow_stream_client_close_triggers_request_cancellation() -> None:
    request_id = "req-client-disconnect"
    broker = api_module.WorkflowEventBroker()
    disconnected: list[str] = []

    async def driver() -> None:
        agen = broker.stream(
            request_id,
            on_disconnect=lambda: disconnected.append(request_id),
        ).__aiter__()
        first = asyncio.create_task(agen.__anext__())
        for _ in range(5):
            await asyncio.sleep(0)
            with broker._lock:  # type: ignore[attr-defined]
                if request_id in broker._streams:  # type: ignore[attr-defined]
                    break
        first.cancel()
        await asyncio.gather(first, return_exceptions=True)
        await agen.aclose()

    asyncio.run(driver())
    assert disconnected == [request_id]
