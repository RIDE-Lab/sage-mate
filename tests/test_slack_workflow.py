import asyncio

import pytest
from pydantic import ValidationError
from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage.runtime.event_journal import EventJournal

from sage_faculty_twin.models import ChatResponse
from sage_faculty_twin.slack_workflow import SlackAnswerRecovery, SlackIngress, SlackReply, SlackWorkflow


def ingress(**changes):
    return SlackIngress(team_id="T1", user_id="U1", channel_id="C1", transport_id="event-1",
                        kind="command", question="Research question", **changes)


def test_contract_rejects_credentials_and_identity_changes():
    item = ingress()
    with pytest.raises(ValidationError):
        SlackIngress.model_validate({**item.model_dump(), "response_url": "secret-placeholder"})
    with pytest.raises(ValidationError):
        SlackIngress.model_validate({**item.model_dump(), "token": "secret-placeholder"})
    other = SlackIngress.model_validate({**item.model_dump(), "team_id": "T2"})
    assert item.owner_id != other.owner_id
    assert item.request_id != other.request_id


def test_failure_notification_exhaustion_does_not_create_more_work(tmp_path):
    async def scenario():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))
        calls = []
        async def authorize(item):
            return None
        async def recover(item):
            return None
        async def answer(item, *, admission_guard):
            raise ValueError("invalid fixture")
        async def deliver(item, reply):
            calls.append(item.request_id)
            raise OSError("Slack unavailable")
        workflow = SlackWorkflow(state, authorize=authorize, recover=recover, answer=answer,
                                 deliver=deliver, max_attempts=2, retry_delay=0, poll_interval=0.01)
        try:
            item = ingress()
            await workflow.submit(item)
            async def observe():
                while workflow.journal.read(item.request_id)["tasks"]["finalize"]["status"] != "failed":
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(observe(), 3)
            record = workflow.journal.read(item.request_id)
            assert set(record["tasks"]) == {"answer", "deliver", "finalize"}
            assert record["tasks"]["finalize"]["attempt"] == 2
            assert await workflow.resume_if_admitted(item)
            await asyncio.sleep(0.03)
            assert calls == [item.request_id] * 2
        finally:
            await workflow.aclose()
            state.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("denial", [None, "membership revoked"])
def test_legacy_failure_recovers_private_notice_once_and_preserves_failure(tmp_path, denial):
    from sage.runtime.work_journal import WorkJournal
    async def scenario():
        path = str(tmp_path / "state.db")
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
        item = ingress()
        journal = WorkJournal(state, namespace="twin-slack-workflow-v1")
        payload = item.model_dump(mode="json")
        journal.commit(item.request_id, result={"owner_id": item.owner_id},
                       tasks={"answer": payload, "deliver": payload}, dependencies={"deliver": ["answer"]})
        lease = journal.claim(item.request_id, "answer", lease_seconds=30)
        journal.fail(lease, code="retry_exhausted")
        before = journal.read(item.request_id)["tasks"]["answer"]
        state.close()
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
        sent = []
        async def authorize(item):
            return denial
        async def forbidden(*args):
            pytest.fail("terminal failure must not regenerate an answer")
        async def deliver(item, reply):
            assert item.private_reply is True
            assert item.question not in reply.text
            sent.append(reply.text)
            return {"accepted": True}
        def workflow():
            return SlackWorkflow(state, authorize=authorize, recover=forbidden, answer=forbidden,
                                 deliver=deliver, poll_interval=0.01, retry_delay=0)
        current = workflow()
        try:
            await current.start()
            async def observed():
                while current.journal.read(item.request_id)["tasks"]["finalize"]["status"] != "done":
                    await asyncio.sleep(0.01)
            await asyncio.wait_for(observed(), 3)
            assert current.journal.read(item.request_id)["tasks"]["answer"] == before
            assert len(sent) == 1
            assert sent[0] == denial if denial else "已停止自动重试" in sent[0]
            await current.aclose()
            current = workflow()
            await current.start()
            assert await current.resume_if_admitted(item)
            await asyncio.sleep(0.03)
            assert len(sent) == 1
        finally:
            await current.aclose()
            state.close()
    asyncio.run(scenario())


@pytest.mark.parametrize("error, attempts", [(OSError("private detail"), 2), (ValueError("private detail"), 1)])
def test_failure_is_bounded_durable_and_status_is_owner_scoped(tmp_path, error, attempts):
    from sage.runtime.work_service import WorkFailedError
    async def scenario():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))
        calls = []
        async def authorize(item):
            return None
        async def recover(item):
            return None
        async def answer(item, *, admission_guard):
            calls.append(item.request_id)
            raise error
        async def deliver(item, reply):
            assert item.private_reply
            assert "已停止自动重试" in reply.text
            assert "private" not in reply.text
            return {"accepted": True}
        item = ingress()
        workflow = SlackWorkflow(state, authorize=authorize, recover=recover, answer=answer,
                                 deliver=deliver, max_attempts=2, retry_delay=0, poll_interval=0.01)
        try:
            await workflow.submit(item)
            with pytest.raises(WorkFailedError):
                await workflow.wait(item.request_id, timeout=3)
            status = await workflow.status(item, item.request_id)
            assert status["status"] == "failed"
            assert status["tasks"]["answer"]["attempt"] == attempts
            assert status["tasks"]["deliver"]["status"] == "blocked"
            assert "private" not in str(status)
            other = SlackIngress.model_validate({**item.model_dump(), "user_id": "U2"})
            assert await workflow.status(other, item.request_id) is None
            await workflow.submit(item)
            await asyncio.sleep(0.03)
            assert calls == [item.request_id] * attempts
        finally:
            await workflow.aclose()
            state.close()
    asyncio.run(scenario())


def test_final_attempt_recovers_committed_answer_after_lost_ack(tmp_path):
    async def scenario():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))
        committed, sent = [], []
        async def authorize(item):
            return None
        async def recover(item):
            return committed[0] if committed else None
        async def answer(item, *, admission_guard):
            committed.append(SlackReply(text="canonical answer"))
            raise ConnectionError("lost acknowledgement")
        async def deliver(item, reply):
            sent.append(reply.text)
            return {"accepted": True}
        workflow = SlackWorkflow(state, authorize=authorize, recover=recover, answer=answer,
                                 deliver=deliver, max_attempts=1, poll_interval=0.01)
        try:
            item = ingress()
            await workflow.submit(item)
            result = await workflow.wait(item.request_id, timeout=3)
            assert result["tasks"]["answer"]["attempt"] == 1
            assert sent == ["canonical answer"]
        finally:
            await workflow.aclose()
            state.close()
    asyncio.run(scenario())


def test_delivery_retry_does_not_regenerate_answer_and_duplicate_ingress_is_idempotent(tmp_path):
    async def scenario():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))
        calls, deliveries = [], []
        async def authorize(item):
            return None
        async def recover(item):
            return None
        async def answer(item, *, admission_guard):
            calls.append(item.request_id)
            return SlackReply(text="full answer")
        async def deliver(item, reply):
            deliveries.append(reply.text)
            if len(deliveries) == 1:
                raise OSError("temporary transport failure")
            return {"accepted": True, "message_ts": "123.456"}
        workflow = SlackWorkflow(state, authorize=authorize, recover=recover, answer=answer,
                                 deliver=deliver, poll_interval=0.01, retry_delay=0)
        try:
            item = ingress()
            await workflow.submit(item)
            await workflow.submit(item)
            result = await workflow.wait(item.request_id, timeout=3)
            assert calls == [item.request_id]
            assert deliveries == ["full answer", "full answer"]
            assert result["tasks"]["deliver"]["result"]["message_ts"] == "123.456"
            assert result["tasks"]["deliver"]["attempt"] == 2  # One real network failure only.
            with pytest.raises(ValueError, match="commit_conflict"):
                await workflow.submit(SlackIngress.model_validate({**item.model_dump(), "question": "changed"}))
        finally:
            await workflow.aclose()
            state.close()
    asyncio.run(scenario())


def test_restart_recovers_committed_answer_without_running_producer(tmp_path):
    async def scenario():
        path = str(tmp_path / "state.db")
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
        item = ingress()
        async def authorize(item):
            return None
        events = EventJournal(state, namespace="twin-workflow-events-v1")
        response = ChatResponse(answer="canonical answer", owner_name="Twin", used_model="fixture")
        # Pagination must not hide the answer behind earlier trace events.
        for index in range(101):
            events.append(item.delivery.stream_id, event_id=f"trace-{index}", payload={"type": "trace-step"})
        events.append(item.delivery.stream_id, event_id="answer_done",
                      payload={"type": "answer_done", "response": response.model_dump(mode="json")})
        recover = SlackAnswerRecovery(state, render=lambda response, _: response.answer)
        async def answer(item, *, admission_guard):
            pytest.fail("recovery must not regenerate canonical answer")
        sent = []
        async def deliver(item, reply):
            sent.append(reply.text)
            return {"accepted": True}
        first = SlackWorkflow(state, authorize=authorize, recover=recover, answer=answer, deliver=deliver)
        payload = item.model_dump(mode="json")
        first.journal.commit(item.request_id, result={"owner_id": item.owner_id},
                             tasks={"answer": payload, "deliver": payload})
        await first.aclose()
        state.close()
        reopened = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
        recover = SlackAnswerRecovery(reopened, render=lambda response, _: response.answer)
        workflow = SlackWorkflow(reopened, authorize=authorize, recover=recover, answer=answer,
                                 deliver=deliver, poll_interval=0.01, retry_delay=0)
        try:
            await workflow.start()
            completed = await workflow.wait(item.request_id, timeout=3)
            assert completed["version"] == 5
            assert completed["dependencies"] == {"deliver": ["answer"], "finalize": ["answer", "deliver"]}
            assert completed["settled"] == ["finalize"]
            assert completed["tasks"]["finalize"]["result"] == {"skipped": True}
            assert completed["tasks"]["deliver"]["attempt"] == 1
            assert sent == ["canonical answer"]
        finally:
            await workflow.aclose()
            reopened.close()
    asyncio.run(scenario())


def test_revocation_before_delivery_never_sends_saved_private_answer(tmp_path):
    async def scenario():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))
        revoked = False
        async def authorize(item):
            return "membership revoked" if revoked else None
        async def recover(item):
            return None
        async def answer(item, *, admission_guard):
            nonlocal revoked
            revoked = True
            return SlackReply(text="private answer")
        sent = []
        async def deliver(item, reply):
            sent.append(reply.text)
            return {"accepted": True}
        workflow = SlackWorkflow(state, authorize=authorize, recover=recover, answer=answer,
                                 deliver=deliver, poll_interval=0.01, retry_delay=0, max_workers=1)
        try:
            item = ingress()
            await workflow.submit(item)
            await workflow.wait(item.request_id, timeout=3)
            assert sent == ["membership revoked"]
        finally:
            await workflow.aclose()
            state.close()
    asyncio.run(scenario())


def test_denial_skips_recovery_and_model_and_false_receipt_is_retried(tmp_path):
    async def scenario():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))
        async def authorize(item):
            return "access denied"
        async def forbidden(item):
            pytest.fail("denied user must not read an answer")
        receipts = []
        async def deliver(item, reply):
            receipts.append(reply.text)
            return {"accepted": len(receipts) > 1}
        workflow = SlackWorkflow(state, authorize=authorize, recover=forbidden, answer=forbidden,
                                 deliver=deliver, poll_interval=0.01, retry_delay=0)
        try:
            item = ingress()
            await workflow.submit(item)
            result = await workflow.wait(item.request_id, timeout=3)
            assert receipts == ["access denied", "access denied"]
            assert result["tasks"]["deliver"]["result"]["accepted"] is True
        finally:
            await workflow.aclose()
            state.close()
    asyncio.run(scenario())


def test_canonical_recovery_is_owner_scoped(tmp_path):
    async def scenario():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))
        item = ingress()
        events = EventJournal(state, namespace="twin-workflow-events-v1")
        response = ChatResponse(answer="private answer", owner_name="Twin", used_model="fixture")
        events.append(item.delivery.stream_id, event_id="answer_done",
                      payload={"type": "answer_done", "response": response.model_dump(mode="json")})
        recovery = SlackAnswerRecovery(state, render=lambda response, _: response.answer)
        try:
            assert (await recovery(item)).text == "private answer"
            foreign = SlackIngress.model_validate({**item.model_dump(), "channel_id": "C2"})
            assert await recovery(foreign) is None
        finally:
            state.close()
    asyncio.run(scenario())
