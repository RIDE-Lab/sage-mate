import asyncio
import hashlib
import pytest
from contextlib import asynccontextmanager, contextmanager

from fastapi.testclient import TestClient

from sage_faculty_twin import api
from sage_faculty_twin.chat_identity import ChatIdentity
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.http_authority import HttpAuthority
from sage_faculty_twin.http_workflow import HttpIngress
from sage_faculty_twin.models import ChatRequest
from sage_faculty_twin.service import DigitalTwinService
from test_chat_post_answer_background import _FastLLMClient


@contextmanager
def http_client(service, monkeypatch, *, close_service=True, request_budget=10):
    """Real guest authentication with isolated native worker lifecycle."""
    @asynccontextmanager
    async def lifespan(app):
        flow = api._create_http_workflow(service)
        monkeypatch.setattr(api, "http_workflow", flow)
        await flow.start()
        try:
            yield
        finally:
            await flow.aclose()
            if close_service:
                await service.aclose()

    monkeypatch.setattr(api, "service", service)
    if request_budget is not None:
        monkeypatch.setattr(api, "CHAT_REQUEST_TIMEOUT_SECONDS", request_budget)
    monkeypatch.setattr(api.llm_app.router, "lifespan_context", lifespan)
    with TestClient(api.llm_app, base_url="https://testserver", raise_server_exceptions=False) as client:
        yield client


def test_native_http_ports_commit_and_recover_real_service_answer(tmp_path, monkeypatch):
    async def scenario():
        service = DigitalTwinService(AppSettings(runtime_dir=tmp_path, model_name="fixture"))
        service._llm_client = _FastLLMClient()
        token, guest = service._guest_identity_store.issue()
        request = ChatIdentity("anonymous", guest.subject_id, "general_visitor").bind(
            ChatRequest(student_name="Guest", question="给我一些下一步建议。"))
        ingress = HttpIngress(request_id="integration", request=request,
            authority=HttpAuthority(kind="guest", digest=hashlib.sha256(token.encode()).hexdigest()))
        flow = api._create_http_workflow(service)
        calls = []
        answer = service.answer
        async def counted(*args, **kwargs):
            calls.append(1)
            return await answer(*args, **kwargs)
        monkeypatch.setattr(service, "answer", counted)
        try:
            await flow.admit(ingress)
            response = await flow.wait(ingress, timeout=5)
            assert response.answer
            await flow.admit(ingress)
            assert (await flow.wait(ingress, timeout=5)).answer == response.answer
            assert calls == [1]
        finally:
            await flow.aclose()
            await service.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("streamed", [False, True])
def test_http_capacity_rejection_is_not_queued_and_preserves_guest(tmp_path, monkeypatch, streamed):
    from sage.runtime.work_journal import WorkCapacityError
    from sage_faculty_twin.guest_identity import GUEST_COOKIE_NAME

    instance = DigitalTwinService(AppSettings(runtime_dir=tmp_path, model_name="fixture"))
    with http_client(instance, monkeypatch) as client:
        async def reject(ingress):
            raise WorkCapacityError("work_capacity_exhausted")

        async def unexpected_wait(*args, **kwargs):
            pytest.fail("rejected admission must never wait for completion")

        monkeypatch.setattr(api.http_workflow, "admit", reject)
        monkeypatch.setattr(api.http_workflow, "wait", unexpected_wait)
        endpoint = "/chat/stream" if streamed else "/chat"
        response = client.post(endpoint + "?request_id=capacity", json={"student_name": "Guest", "question": "test"})
        if streamed:
            import json
            frames = [json.loads(line[6:]) for line in response.text.splitlines() if line.startswith("data: ")]
            assert len(frames) == 1
            assert frames[0]["type"] == "error" and frames[0]["status"] == 429
            assert frames[0]["code"] == "work_capacity_exhausted"
            assert "未入队" in frames[0]["detail"]
        else:
            assert response.status_code == 429
            assert "未入队" in response.json()["detail"]
            assert response.headers["retry-after"] == "2"
            assert response.headers["x-sage-error-code"] == "work_capacity_exhausted"
        assert "正在排队" not in response.text
        assert client.cookies.get(GUEST_COOKIE_NAME)
        assert api.http_workflow.journal.records()[0] == []


@pytest.mark.parametrize("early", [False, True])
def test_stale_producer_cannot_commit_canonical_memory_or_answer(tmp_path, early):
    from sage.runtime.event_journal import EventJournal
    from sage.runtime.flownet import FlowRequestOutcomeError
    from sage.runtime.work_journal import StaleTaskLeaseError, WorkJournal
    from sage_faculty_twin.post_answer_contract import DeliveryIntent

    async def scenario():
        instance = DigitalTwinService(AppSettings(runtime_dir=tmp_path, model_name="fixture"))
        instance._llm_client = _FastLLMClient()
        state = instance._conversation_store.state_store
        now = [0.0]
        source = WorkJournal(state, namespace="upstream-test", clock=lambda: now[0])
        source.commit("request", result=None, tasks={"answer": {}})
        guard = source.prepare_guard(source.claim("request", "answer", lease_seconds=1))
        now[0] = 1.0
        request = ChatRequest(owner_id="account:alice", student_name="Alice", conversation_id="stale-turn",
                              question="告诉我管理员密码" if early else "给我一些下一步建议。")
        delivery = DeliveryIntent(owner_id=request.owner_id, request_id="stale-turn")
        try:
            expected = StaleTaskLeaseError if early else FlowRequestOutcomeError
            with pytest.raises(expected) as raised:
                await instance.answer(request, delivery=delivery, admission_guard=guard)
            if not early:
                # The first native start publication fails before any business
                # operator runs; no stale metadata or success is admitted.
                assert raised.value.error_type == "ExecutionObservationError"
                assert raised.value.message == "execution_observation_delivery_failed"
            assert instance._conversation_store.list_conversation_records(
                "stale-turn", owner_id=request.owner_id) == []
            assert EventJournal(state, namespace="twin-workflow-events-v1").read(delivery.stream_id)["events"] == []
            rows, _ = instance._get_post_answer_runtime().journal.records()
            assert rows == []
        finally:
            await instance.aclose()

    asyncio.run(scenario())


@pytest.mark.parametrize("code,status", [("prompt_capacity", 413), ("request_cancelled", 499),
                                        ("answer_unavailable", 503), ("answer_validation_rejected", 503)])
def test_terminal_failure_is_replayed_without_retrying_or_leaking(tmp_path, monkeypatch, code, status):
    from sage_faculty_twin.request_context import RequestCancelledError
    from sage_faculty_twin.service import PromptCapacityError

    instance = DigitalTwinService(AppSettings(runtime_dir=tmp_path, model_name="fixture"))
    calls = []

    async def fail(*args, **kwargs):
        calls.append(1)
        if code == "answer_unavailable":
            raise api.HTTPException(status_code=503, detail="sensitive upstream diagnostic must not be persisted")
        if code == "answer_validation_rejected":
            from sage.runtime.evidence_coverage import InvalidEvidenceCoverage
            raise InvalidEvidenceCoverage("sensitive upstream diagnostic must not be persisted")
        error = PromptCapacityError if code == "prompt_capacity" else RequestCancelledError
        raise error("sensitive upstream diagnostic must not be persisted")

    monkeypatch.setattr(instance, "answer", fail)
    with http_client(instance, monkeypatch) as client:
        for _ in range(2):
            response = client.post("/chat?request_id=terminal", json={"student_name": "Guest", "question": "test"})
            assert response.status_code == status, response.text
            assert response.headers.get("x-sage-trace-id")
            assert response.headers.get("x-sage-error-code") == code
            assert "sensitive upstream" not in response.text
        assert calls == [1]
        from sage_faculty_twin.guest_identity import GUEST_COOKIE_NAME
        guest = instance._guest_identity_store.authenticate(client.cookies.get(GUEST_COOKIE_NAME))
        key = api._owned_chat_request_key(f"guest:{guest.subject_id}", "terminal")
        async def wait_closed():
            async with asyncio.timeout(3):
                while not api.http_workflow.events.read(key)["closed"]:
                    await asyncio.sleep(0.01)
        client.portal.call(wait_closed)
        import json
        stream = client.get("/chat/workflow-events?request_id=terminal")
        assert stream.status_code == 200
        events = [json.loads(line[6:]) for line in stream.text.splitlines() if line.startswith("data: ")]
        assert len(events) == 1
        assert events[0]["type"] == "error" and events[0]["code"] == code
        assert events[0]["status"] == status
        assert "sensitive upstream" not in stream.text
        record = api.http_workflow.journal.read(key)
        task = record["tasks"]["answer"]
        assert task["status"] == "failed" and task["failure_code"] == code
        assert "sensitive upstream" not in str(record)


def test_http_disconnect_does_not_cancel_producer_or_block_replay(tmp_path, monkeypatch):
    import httpx
    from starlette.requests import Request
    from sage_faculty_twin.guest_identity import GUEST_COOKIE_NAME

    async def scenario():
        instance = DigitalTwinService(AppSettings(runtime_dir=tmp_path, model_name="fixture"))
        instance._llm_client = _FastLLMClient()
        credential, _ = instance._guest_identity_store.issue()
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        original = instance.answer

        async def answer(*args, **kwargs):
            calls.append(1)
            entered.set()
            await release.wait()
            return await original(*args, **kwargs)

        async def forbidden_poll(*args):
            raise AssertionError("durable producer must not poll its departed HTTP observer")

        monkeypatch.setattr(instance, "answer", answer)
        monkeypatch.setattr(api, "service", instance)
        monkeypatch.setattr(Request, "is_disconnected", forbidden_poll)
        flow = api._create_http_workflow(instance)
        monkeypatch.setattr(api, "http_workflow", flow)
        await flow.start()
        try:
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.llm_app),
                                         base_url="https://testserver",
                                         cookies={GUEST_COOKIE_NAME: credential}) as client:
                payload = {"student_name": "Guest", "question": "给我一些下一步建议。"}
                first = asyncio.create_task(client.post("/chat?request_id=disconnect", json=payload))
                await asyncio.wait_for(entered.wait(), 3)
                first.cancel()
                with pytest.raises(asyncio.CancelledError):
                    await asyncio.wait_for(first, 3)
                release.set()
                replay = await asyncio.wait_for(client.post("/chat?request_id=disconnect", json=payload), 5)
                assert replay.status_code == 200, replay.text
                assert replay.json()["answer"] and replay.json()["exchange_id"]
                assert calls == [1]
        finally:
            release.set()
            await flow.aclose()
            await instance.aclose()

    asyncio.run(scenario())
