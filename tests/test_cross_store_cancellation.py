"""Real Twin destinations, independent stores, and stale synchronous writers."""

import asyncio
from datetime import datetime
from threading import Event

import pytest
from sage.runtime.effect_cancellation import EffectCancelled

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.http_workflow import HttpWorkflow
from sage_faculty_twin.models import BookingRequest, ChatResponse, KnowledgeDocumentCreate
from sage_faculty_twin.service import DigitalTwinService
from test_http_failure_delivery import wait_finalized
from test_http_workflow import ingress


def settings(root):
    return AppSettings(runtime_dir=root, knowledge_base_dir=root / "knowledge",
        model_name="fixture",
        knowledge_backend="local", sagevdb_embedding_backend="hash",
        booking_state_path=root / "bookings.sqlite3", conversation_memory_dir=root / "memory",
        conversation_memory_index_type="bm25")


def booking():
    return BookingRequest(student_name="Fixture", student_email="fixture@example.test",
        topic="Synthetic", preferred_start=datetime(2030, 1, 7, 10),
        preferred_end=datetime(2030, 1, 7, 11))


@pytest.mark.parametrize("kind", ["capture", "booking", "settle", "knowledge"])
def test_acknowledged_cancel_blocks_real_destination_writes_from_stale_thread(tmp_path, kind):
    async def scenario():
        instance = DigitalTwinService(settings(tmp_path))
        state = instance._conversation_store.state_store
        item = ingress()
        scope = item.delivery.stream_id
        meeting = instance._meeting_service
        entered, release, finished = Event(), Event(), Event()
        denied = []
        draft = booking().model_dump()
        prior = None
        if kind == "settle":
            meeting.workflows.capture(item.request.owner_id, "conversation", scope,
                inputs={"request": "fixture"}, fields=draft)
            prior = meeting.book(booking(), operation_id="booking-create:" + scope,
                                 cancellation_scope=scope)
            assert prior.accepted

        def stale_write():
            entered.set()
            assert release.wait(8)
            try:
                if kind == "capture":
                    meeting.workflows.capture(item.request.owner_id, "conversation", scope,
                        inputs={"request": "fixture"}, fields=draft,
                        guard=meeting.effect_fences.prepare_guard(scope))
                elif kind == "booking":
                    meeting.book(booking(), operation_id="booking-create:" + scope,
                                 cancellation_scope=scope)
                elif kind == "settle":
                    meeting.workflows.settle(item.request.owner_id, scope, prior,
                        guard=meeting.effect_fences.prepare_guard(scope))
                else:
                    instance._knowledge_store.add_document(KnowledgeDocumentCreate(
                        title="Synthetic", content="No production data", source_name="fixture"),
                        operation_id="knowledge-write:" + scope, cancellation_scope=scope)
            except EffectCancelled:
                denied.append(kind)
            finally:
                finished.set()
            return ChatResponse(answer="must not commit", owner_name="Twin", used_model="fixture")

        async def authorize(_):
            return True
        async def recover(_):
            return None
        async def answer(_, *, admission_guard):
            return await asyncio.to_thread(stale_write)

        flow = HttpWorkflow(state, authorize=authorize, recover=recover, answer=answer,
            cancellation_targets=instance.cancellation_targets(), poll_interval=0.01)
        try:
            await flow.admit(item)
            assert await asyncio.to_thread(entered.wait, 3)
            assert await flow.cancel(owner_id=item.request.owner_id, request_id=item.request_id) == "cancelled"
            assert meeting.effect_fences.is_sealed(scope)
            assert instance._knowledge_store._catalog.effect_fences.is_sealed(scope)
            release.set()
            assert await asyncio.to_thread(finished.wait, 3)
            assert denied == [kind]
            assert len(meeting.list_bookings()) == int(kind == "settle")
            assert len(meeting._notifications.journal.records()[0]) == int(kind == "settle")
            assert instance._knowledge_store.count_documents() == 0
            assert instance._knowledge_store._catalog.journal.records()[0] == []
            if kind == "settle":
                assert meeting.workflows.peek(item.request.owner_id, "conversation") is not None
            elif kind == "capture":
                assert meeting.workflows.peek(item.request.owner_id, "conversation") is None
            await wait_finalized(flow, item)
            page = flow.events.read(scope)
            assert page["closed"]
            assert [event["payload"]["code"] for event in page["events"]] == ["request_cancelled"]
        finally:
            release.set()
            await flow.aclose()
            await instance.aclose()
    asyncio.run(scenario())


def test_partial_seal_is_pending_and_publishes_no_terminal_event(tmp_path, monkeypatch):
    async def scenario():
        instance = DigitalTwinService(settings(tmp_path))
        item = ingress()
        entered, release = asyncio.Event(), asyncio.Event()
        available = Event()
        target = instance._knowledge_store._catalog.effect_fences
        seal = target.seal

        def temporary(scope):
            if not available.is_set():
                raise OSError("isolated destination outage")
            seal(scope)

        monkeypatch.setattr(target, "seal", temporary)
        async def authorize(_):
            return True
        async def recover(_):
            return None
        async def answer(_, *, admission_guard):
            entered.set()
            await release.wait()
            return ChatResponse(answer="fixture", owner_name="Twin", used_model="fixture")

        flow = HttpWorkflow(instance._conversation_store.state_store, authorize=authorize,
            recover=recover, answer=answer, cancellation_targets=instance.cancellation_targets(),
            cancellation_timeout=0.05, poll_interval=0.01)
        try:
            await flow.admit(item)
            await asyncio.wait_for(entered.wait(), 3)
            assert await flow.cancel(owner_id=item.request.owner_id, request_id=item.request_id) == "cancelling"
            assert flow.events.read(item.delivery.stream_id)["events"] == []
            assert flow.journal.read(item.delivery.stream_id)["tasks"]["answer"]["status"] == "running"
            available.set()
            outcome = await asyncio.to_thread(flow._cancellations.wait, item.delivery.stream_id, timeout=5)
            assert outcome == "cancelled"
            await wait_finalized(flow, item)
            assert flow.events.read(item.delivery.stream_id)["closed"]
        finally:
            available.set()
            release.set()
            await flow.aclose()
            await instance.aclose()
    asyncio.run(scenario())


@pytest.mark.parametrize("partial", [False, True])
def test_actual_booking_pipeline_propagates_scope_into_stale_business_commit(tmp_path, monkeypatch, partial):
    class BookingClient:
        model_name = "not-called"
        def classify_booking_intent_sync(self, *args, **kwargs):
            return True
        async def classify_booking_intent(self, *args, **kwargs):
            return True
        def answer_question_sync(self, *args, **kwargs):
            raise AssertionError("booking must not generate an answer")
        async def answer_question(self, *args, **kwargs):
            raise AssertionError("booking must not generate an answer")
        async def aclose(self):
            pass

    async def scenario():
        instance = DigitalTwinService(settings(tmp_path))
        instance._llm_client = BookingClient()
        item = ingress("请帮我预约 2030-01-07 10:00 讨论论文进展，邮箱 fixture@example.test")
        item.request.skill_routing = False
        item.request.deep_thinking = False
        entered, release, finished = Event(), Event(), Event()
        available = Event()
        target = instance._knowledge_store._catalog.effect_fences
        seal = target.seal
        def temporary(scope):
            if not available.is_set():
                raise OSError("isolated destination offline")
            seal(scope)
        if partial:
            monkeypatch.setattr(target, "seal", temporary)
        else:
            available.set()
        original = instance._meeting_service.prepare_proposal
        seen = []

        def paused(*args, **kwargs):
            entered.set()
            assert release.wait(8)
            try:
                seen.append(kwargs.get("cancellation_scope"))
                return original(*args, **kwargs)
            finally:
                finished.set()

        monkeypatch.setattr(instance._meeting_service, "prepare_proposal", paused)
        async def authorize(_):
            return True
        async def recover(_):
            return None
        async def answer(value, *, admission_guard):
            return await instance.answer(value.request, delivery=value.delivery, admission_guard=admission_guard)

        flow = HttpWorkflow(instance._conversation_store.state_store, authorize=authorize,
            recover=recover, answer=answer, cancellation_targets=instance.cancellation_targets(), poll_interval=0.01,
            cancellation_timeout=0.05 if partial else 30)
        try:
            await flow.admit(item)
            assert await asyncio.to_thread(entered.wait, 5), "real SAGE booking stage was not entered"
            assert await flow.cancel(owner_id=item.request.owner_id, request_id=item.request_id) == (
                "cancelling" if partial else "cancelled")
            release.set()
            assert await asyncio.to_thread(finished.wait, 3)
            assert seen == [item.delivery.stream_id]
            if partial:
                for _ in range(300):
                    task = flow.journal.read(item.delivery.stream_id)["tasks"]["answer"]
                    if task["status"] == "pending":
                        break
                    await asyncio.sleep(0.01)
                assert task["status"] == "pending"
                assert task["failure_code"] == "cancellation_pending"
                await asyncio.sleep(0.05)
                assert flow.journal.read(item.delivery.stream_id)["tasks"]["answer"]["attempt"] == 1
                observed = flow.events.read(item.delivery.stream_id)
                assert not observed["closed"]
                assert observed["events"]
                assert all(frame["payload"]["type"] == "execution-event" for frame in observed["events"])
                assert not any(frame["payload"]["event"]["operator_key"] == "execute-booking"
                               and frame["payload"]["event"]["state"] == "completed"
                               for frame in observed["events"])
                available.set()
                assert await flow._cancellations.await_result(item.delivery.stream_id, timeout=5) == "cancelled"
            assert instance._meeting_service.list_bookings() == []
            assert instance._meeting_service._notifications.journal.records()[0] == []
            assert not any(event["id"] == "answer_done" for event in flow.events.read(item.delivery.stream_id)["events"])
        finally:
            available.set()
            release.set()
            await flow.aclose()
            await instance.aclose()
    asyncio.run(scenario())


def test_restart_recovers_partial_cancel_without_restarting_business_work(tmp_path, monkeypatch):
    async def scenario():
        config = settings(tmp_path)
        instance = DigitalTwinService(config)
        item = ingress()
        target = instance._knowledge_store._catalog.effect_fences
        def unavailable(_):
            raise OSError("isolated destination offline")
        monkeypatch.setattr(target, "seal", unavailable)
        async def authorize(_):
            return True
        async def forbidden(*args, **kwargs):
            raise AssertionError("cancellation recovery re-ran business work")
        async def recover(_):
            return None
        first = HttpWorkflow(instance._conversation_store.state_store, authorize=authorize,
            recover=recover, answer=forbidden, cancellation_targets=instance.cancellation_targets(),
            cancellation_timeout=0.05, poll_interval=0.01, accept_task=lambda *_: False)
        try:
            await first.admit(item)
            assert await first.cancel(owner_id=item.request.owner_id, request_id=item.request_id) == "cancelling"
        finally:
            await first.aclose()
            await instance.aclose()
        recovered = DigitalTwinService(config)
        second = HttpWorkflow(recovered._conversation_store.state_store, authorize=authorize,
            recover=recover, answer=forbidden, cancellation_targets=recovered.cancellation_targets(),
            poll_interval=0.01)
        try:
            await second.start()
            assert await asyncio.to_thread(second._cancellations.wait, item.delivery.stream_id, timeout=5) == "cancelled"
            await wait_finalized(second, item)
            assert second.events.read(item.delivery.stream_id)["closed"]
            assert recovered._meeting_service.list_bookings() == []
            assert recovered._knowledge_store.count_documents() == 0
            assert recovered._meeting_service.effect_fences.is_sealed(item.delivery.stream_id)
            assert recovered._knowledge_store._catalog.effect_fences.is_sealed(item.delivery.stream_id)
        finally:
            await second.aclose()
            await recovered.aclose()
    asyncio.run(scenario())


def test_real_http_cancel_reports_202_until_all_targets_acknowledge(tmp_path, monkeypatch):
    import hashlib
    import httpx
    from sage_faculty_twin import api
    from sage_faculty_twin.guest_identity import GUEST_COOKIE_NAME
    from sage_faculty_twin.chat_identity import ChatIdentity
    from sage_faculty_twin.http_authority import HttpAuthority
    from sage_faculty_twin.http_workflow import HttpIngress
    from sage_faculty_twin.models import ChatRequest

    async def scenario():
        instance = DigitalTwinService(settings(tmp_path))
        credential, guest = instance._guest_identity_store.issue()
        item = HttpIngress(request_id="http-cancel", request=ChatIdentity(
            "anonymous", guest.subject_id, "general_visitor").bind(ChatRequest(
                student_name="Fixture", question="isolated")),
            authority=HttpAuthority(kind="guest", digest=hashlib.sha256(credential.encode()).hexdigest()))
        available = Event()
        target = instance._knowledge_store._catalog.effect_fences
        seal = target.seal
        def temporary(scope):
            if not available.is_set():
                raise OSError("isolated destination outage")
            seal(scope)
        monkeypatch.setattr(target, "seal", temporary)
        monkeypatch.setattr(api, "service", instance)
        flow = api._create_http_workflow(instance)
        flow._task_selector = lambda *_: False
        flow._cancellation_timeout = 0.05
        monkeypatch.setattr(api, "http_workflow", flow)
        try:
            await flow.admit(item)
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=api.llm_app),
                base_url="https://testserver", cookies={GUEST_COOKIE_NAME: credential}) as client:
                response = await client.post("/chat/cancel?request_id=http-cancel")
                assert response.status_code == 202
                assert response.json() == {"request_id": "http-cancel", "status": "cancelling", "cancelled": False}
                assert flow.events.read(item.delivery.stream_id)["events"] == []
                available.set()
                assert await asyncio.to_thread(flow._cancellations.wait, item.delivery.stream_id, timeout=5) == "cancelled"
                replay = await client.post("/chat/cancel?request_id=http-cancel")
                assert replay.status_code == 200
                assert replay.json()["cancelled"] is True
        finally:
            available.set()
            await flow.aclose()
            await instance.aclose()
    asyncio.run(scenario())
