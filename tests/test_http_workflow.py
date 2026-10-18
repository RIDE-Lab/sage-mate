import asyncio

import pytest
from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage.runtime.event_journal import EventJournal
from sage.runtime.work_journal import WorkCapacityError

from sage_faculty_twin.http_workflow import HttpIngress, HttpWorkflow
from sage_faculty_twin.auth import build_user_session_token, capture_work_session_reference
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.http_authority import HttpAuthority
from sage_faculty_twin.models import ChatRequest, ChatResponse


def ingress(question="question", owner="account:alice"):
    settings = AppSettings()
    token = build_user_session_token(user_id=owner.split(":", 1)[1], email="alice@example.com", settings=settings)
    return HttpIngress(request_id="r", request=ChatRequest(
        owner_id=owner, student_name="Alice", question=question),
        authority=HttpAuthority(kind="account", session=capture_work_session_reference(token, settings, kind="user")))


def test_duplicate_conflict_and_cancelled_observer_share_one_producer(tmp_path):
    async def scenario():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "work.db"))
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def authorize(item):
            return True
        async def recover(item):
            return None
        async def answer(item, *, admission_guard):
            calls.append(item.request.question)
            entered.set()
            await release.wait()
            return ChatResponse(answer="canonical", owner_name="Twin", used_model="fake")
        flow = HttpWorkflow(state, authorize=authorize, recover=recover, answer=answer)
        # Offline policy installation, before any worker or ingress starts.
        flow.journal.configure_capacity(max_active_requests=1)
        try:
            item = ingress()
            await asyncio.gather(flow.admit(item), flow.admit(item))
            await asyncio.wait_for(entered.wait(), 3)
            rejected = item.model_copy(update={"request_id": "capacity-rejected"})
            with pytest.raises(WorkCapacityError):
                await flow.admit(rejected)
            assert flow.journal.read(rejected.delivery.stream_id) is None
            with pytest.raises(ValueError, match="conflict"):
                await flow.admit(ingress("changed"))
            waiter = asyncio.create_task(flow.wait(item, timeout=3))
            await asyncio.sleep(0)
            waiter.cancel()
            with pytest.raises(asyncio.CancelledError):
                await waiter
            release.set()
            assert (await flow.wait(item, timeout=3)).answer == "canonical"
            await flow.admit(item)
            assert (await flow.wait(item, timeout=3)).answer == "canonical"
            assert calls == ["question"]
            assert ingress(owner="account:bob").delivery.stream_id != item.delivery.stream_id
        finally:
            release.set()
            await flow.aclose()
            state.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("legacy_source_field", [False, True])
def test_restart_recovers_canonical_answer_and_rechecks_authority(tmp_path, legacy_source_field):
    path = str(tmp_path / "work.db")
    async def scenario():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
        permitted = [True]
        async def authorize(item):
            return permitted[0]
        async def recover(item):
            return ChatResponse(answer="already committed", owner_name="Twin", used_model="fake")
        async def answer(item, *, admission_guard):
            pytest.fail("canonical answer must not regenerate")
        flow = HttpWorkflow(state, authorize=authorize, recover=recover, answer=answer)
        item = ingress()
        captured = item.model_dump(mode="json")
        if legacy_source_field:
            captured["request"].pop("knowledge_sources")
        # Durable pre-crash admission with no producer started.
        flow.journal.commit(item.delivery.stream_id, result={"owner_id": item.request.owner_id},
                            tasks={"answer": captured})
        await flow.aclose()
        state.close()
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
        flow = HttpWorkflow(state, authorize=authorize, recover=recover, answer=answer)
        try:
            await flow.start()
            await flow.admit(item)
            assert (await flow.wait(item, timeout=3)).answer == "already committed"
            assert flow.journal.read(item.delivery.stream_id)["tasks"]["answer"]["payload"] == captured
            for selection in ([], ["wiki"], ["private"]):
                changed = item.model_copy(update={"request": item.request.model_copy(
                    update={"knowledge_sources": selection})})
                with pytest.raises(ValueError, match="conflict"):
                    await flow.admit(changed)
                with pytest.raises(ValueError, match="conflict"):
                    await flow.wait(changed, timeout=3)
            permitted[0] = False
            with pytest.raises(PermissionError):
                await flow.wait(item, timeout=3)
            with pytest.raises(PermissionError):
                await flow.admit(item)
        finally:
            await flow.aclose()
            state.close()
    asyncio.run(scenario())


def test_two_instances_share_lease_and_recover_lost_answer_ack(tmp_path):
    async def scenario():
        path = str(tmp_path / "shared.db")
        states = [StatefulProcessRuntime(backend="sqlite", sqlite_path=path) for _ in range(2)]
        entered, release = asyncio.Event(), asyncio.Event()
        calls = []
        async def authorize(item):
            return True
        def ports(state):
            events = EventJournal(state, namespace="canonical")
            async def recover(item):
                page = events.read(item.delivery.stream_id)
                for event in page["events"]:
                    if event["id"] == "answer_done":
                        return ChatResponse.model_validate(event["payload"])
                return None
            async def answer(item, *, admission_guard):
                calls.append(item.request.question)
                entered.set()
                await release.wait()
                response = ChatResponse(answer="stored", owner_name="Twin", used_model="fake")
                events.append(item.delivery.stream_id, event_id="answer_done",
                              payload=response.model_dump(mode="json"))
                raise ConnectionError("acknowledgement lost after canonical commit")
            return recover, answer
        flows = [HttpWorkflow(state, authorize=authorize, recover=ports(state)[0],
                              answer=ports(state)[1], retry_delay=0) for state in states]
        try:
            item = ingress()
            await asyncio.gather(*(flow.admit(item) for flow in flows))
            await asyncio.wait_for(entered.wait(), 3)
            release.set()
            replies = await asyncio.gather(*(flow.wait(item, timeout=3) for flow in flows))
            assert [reply.answer for reply in replies] == ["stored", "stored"]
            assert calls == ["question"]
            assert flows[0].journal.read(item.delivery.stream_id)["tasks"]["answer"]["attempt"] == 1
        finally:
            release.set()
            for flow in flows:
                await flow.aclose()
            for state in states:
                state.close()
    asyncio.run(scenario())
