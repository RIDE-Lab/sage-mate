"""Actual native journals/workers: captured plans, partial recovery and scope."""

import asyncio

import pytest
from sage.runtime.delivery_sequence import DeliveryPlan
from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage.runtime.work_journal import WorkJournal
from sage.runtime.work_service import WorkFailedError

from sage_faculty_twin import slack_workflow as module
from sage_faculty_twin.slack_workflow import SlackIngress, SlackReply, SlackWorkflow


def ingress():
    return SlackIngress(team_id="T1", user_id="U1", channel_id="C1", transport_id="multipart",
                        kind="mention", question="fixture private question")


def long_reply():
    return SlackReply(text="**" + " ".join(f"token{i:04}" for i in range(3000)) + " FINAL**")


async def until(predicate):
    async def observe():
        while not predicate():
            await asyncio.sleep(0.01)
    await asyncio.wait_for(observe(), timeout=20)


def test_actual_reopen_resumes_captured_parts_without_model_or_formatter(tmp_path, monkeypatch):
    async def run():
        path = str(tmp_path / "state.db")
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
        item = ingress()
        accepting = [True]
        calls, generated = [], []

        async def authorize(_):
            return None

        async def recover(_):
            return None

        async def answer(_, **kwargs):
            generated.append(True)
            return long_reply()

        async def send(_, reply):
            calls.append(reply.text)
            if len(calls) == 2:
                accepting[0] = False
                raise OSError("uncertain transport outcome")
            return {"accepted": True, "message_ts": f"123.{len(calls)}"}

        first = SlackWorkflow(state, authorize=authorize, recover=recover, answer=answer, deliver=send,
                              accept_task=lambda *_: accepting[0], poll_interval=0.01, retry_delay=0)
        try:
            await first.submit(item)
            await until(lambda: first.journal.read(item.request_id)["tasks"]["deliver"]["attempt"] == 1
                        and first.journal.read(item.request_id)["tasks"]["deliver"]["status"] == "pending")
            before = first._sequence.read(item.request_id, "deliver")
            assert len(before["receipts"]) >= 3
            assert before["receipts"][0]["accepted"] is True
            assert all(receipt is None for receipt in before["receipts"][1:])
            captured = first.journal.read(item.request_id)["tasks"]["answer"]["result"]
        finally:
            await first.aclose()
            state.close()

        def no_format(*args, **kwargs):
            pytest.fail("recovery must restore, not reformat a captured plan")

        async def no_model(*args, **kwargs):
            pytest.fail("recovery must not regenerate or reread an answer")

        monkeypatch.setattr(module, "_capture_reply", no_format)
        reopened = StatefulProcessRuntime(backend="sqlite", sqlite_path=path)
        workflow = SlackWorkflow(reopened, authorize=authorize, recover=no_model, answer=no_model,
                                 deliver=send, poll_interval=0.01, retry_delay=0)
        try:
            await workflow.start()
            record = await workflow.wait(item.request_id, timeout=10)
            saved = workflow._sequence.read(item.request_id, "deliver")
            assert generated == [True]
            assert record["tasks"]["answer"]["result"] == captured
            assert record["tasks"]["answer"]["attempt"] == 1
            assert saved["plan"] == before["plan"]
            assert saved["receipts"][0] == before["receipts"][0]
            assert saved["attempts"] == [1, 2, *([1] * (len(saved["receipts"]) - 2))]
            assert calls[1] == calls[2] and calls.count(calls[0]) == 1
            assert len(calls) == len(saved["receipts"]) + 1
            assert "FINAL" in calls[-1]
            assert all(receipt["accepted"] is True for receipt in saved["receipts"])
        finally:
            await workflow.aclose()
            reopened.close()

    asyncio.run(run())


def test_mid_sequence_revocation_stops_private_remainder_and_finalizes_safely(tmp_path):
    async def run():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))
        revoked = [False]
        sent = []

        async def authorize(_):
            return "membership revoked" if revoked[0] else None

        async def recover(_):
            return None

        async def answer(_, **kwargs):
            return long_reply()

        async def send(item, reply):
            sent.append((item.private_reply, reply.text))
            revoked[0] = True
            return {"accepted": True}

        item = ingress()
        workflow = SlackWorkflow(state, authorize=authorize, recover=recover, answer=answer,
                                 deliver=send, poll_interval=0.01, retry_delay=0)
        try:
            await workflow.submit(item)
            await until(lambda: workflow.journal.read(item.request_id)["tasks"]["finalize"]["status"] == "done")
            with pytest.raises(WorkFailedError):
                await workflow.wait(item.request_id, timeout=3)
            assert len(sent) == 2
            assert sent[0][0] is False and sent[1][0] is True
            assert "已停止自动重试" in sent[1][1]
            assert "token" not in sent[1][1] and "fixture private question" not in sent[1][1]
            ledger = workflow._sequence.read(item.request_id, "deliver")
            assert ledger["receipts"][0]["accepted"] is True
            assert all(receipt is None for receipt in ledger["receipts"][1:])
            assert ledger["attempts"] == [1, *([0] * (len(ledger["receipts"]) - 1))]
        finally:
            await workflow.aclose()
            state.close()

    asyncio.run(run())


@pytest.mark.parametrize("running", [False, True])
def test_pending_legacy_format_is_a_nonmutating_cutover_barrier(tmp_path, running):
    async def run():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))
        item = ingress()
        journal = WorkJournal(state, namespace="twin-slack-workflow-v1")
        payload = item.model_dump(mode="json")
        journal.commit(item.request_id, result={"owner_id": item.owner_id},
                       tasks={"answer": payload, "deliver": payload}, dependencies={"deliver": ["answer"]})
        if running:
            journal.claim(item.request_id, "answer", lease_seconds=30)
        before = journal.read(item.request_id)

        async def forbidden(*args, **kwargs):
            pytest.fail("legacy cutover must neither send nor generate")

        workflow = SlackWorkflow(state, authorize=forbidden, recover=forbidden,
                                 answer=forbidden, deliver=forbidden)
        try:
            with pytest.raises(RuntimeError, match="slack_legacy_work_requires_drain"):
                await workflow.start()
            assert journal.read(item.request_id) == before
        finally:
            await workflow.aclose()
            state.close()
    asyncio.run(run())


@pytest.mark.parametrize("missing_optional_default", [False, True])
@pytest.mark.parametrize("verified_dm", [False, True])
def test_completed_legacy_replay_is_preserved_without_resend(tmp_path, missing_optional_default, verified_dm):
    async def run():
        state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))
        item = ingress()
        if verified_dm:
            item = SlackIngress.model_validate({**item.model_dump(), "kind": "message", "conversation_type": "im"})
        journal = WorkJournal(state, namespace="twin-slack-workflow-v1")
        payload = item.model_dump(mode="json")
        if missing_optional_default:
            payload.pop("private_reply")
            payload.pop("conversation_type")
        journal.commit(item.request_id, result={"owner_id": item.owner_id},
                       tasks={"answer": payload, "deliver": payload}, dependencies={"deliver": ["answer"]})
        for name in ("answer", "deliver"):
            journal.finish(journal.claim(item.request_id, name, lease_seconds=30), result={"text": "old"})
        before = journal.read(item.request_id)

        async def forbidden(*args, **kwargs):
            pytest.fail("completed history is not a new delivery")

        workflow = SlackWorkflow(state, authorize=forbidden, recover=forbidden,
                                 answer=forbidden, deliver=forbidden)
        try:
            assert await workflow.resume_if_admitted(item) is True
            assert await workflow.submit(item) == before
            assert journal.read(item.request_id) == before
        finally:
            await workflow.aclose()
            state.close()
    asyncio.run(run())


def test_valid_generic_fingerprint_cannot_override_trusted_recipient():
    item = ingress()
    captured = module._capture_reply(item, SlackReply(text="fixture"), requires_member=True)
    inputs = captured["delivery_plan"]
    inputs["destination"]["user_id"] = "UOTHER"
    captured["delivery_plan"] = DeliveryPlan.capture(destination=inputs["destination"],
                                                    payloads=inputs["payloads"]).to_dict()
    with pytest.raises(PermissionError, match="slack_delivery_destination_mismatch"):
        module._restore_reply(item, captured)


def test_captured_policy_cannot_be_silently_downgraded_to_notice():
    item = ingress()
    captured = module._capture_reply(item, SlackReply(text="fixture"), requires_member=True)
    captured["requires_member"] = False
    with pytest.raises(PermissionError, match="slack_delivery_destination_mismatch"):
        module._restore_reply(item, captured)


def test_generic_message_kind_does_not_authorize_a_persistent_channel_post():
    item = SlackIngress.model_validate({**ingress().model_dump(), "kind": "message"})
    assert module.slack_reply_method(item) == "chat.postEphemeral"
    captured = module._capture_reply(item, SlackReply(text="fixture"), requires_member=True)
    inputs = captured["delivery_plan"]
    inputs["destination"].update(method="chat.postMessage", visibility="bot_dm")
    captured["delivery_plan"] = DeliveryPlan.capture(destination=inputs["destination"],
                                                    payloads=inputs["payloads"]).to_dict()
    with pytest.raises(PermissionError, match="slack_delivery_destination_mismatch"):
        module._restore_reply(item, captured)
