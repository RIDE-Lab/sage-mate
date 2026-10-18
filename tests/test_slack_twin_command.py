from __future__ import annotations

import hashlib
import hmac
import json
import time
import asyncio
from threading import Event
from functools import partial
from types import SimpleNamespace
from urllib.parse import urlencode

import pytest
from fastapi.testclient import TestClient
from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime

from sage_faculty_twin import api as api_module
from sage_faculty_twin.chat_delivery import ChatDeliveryGate
from sage_faculty_twin.models import ChatResponse, UserAccountResponse, UserSessionResponse
from sage_faculty_twin.slack_channel_context import SlackChannelContext, SlackHistoryUnavailable
from sage_faculty_twin.slack_link_store import SlackUserLinkStore
from sage_faculty_twin.slack_workflow import SlackAnswerRecovery, SlackWorkflow
from sage_faculty_twin.workflow_events import WorkflowEventBroker


def signed(body, *, json_body=False):
    stamp = str(int(time.time()))
    digest = hmac.new(b"secret", b"v0:" + stamp.encode() + b":" + body, hashlib.sha256).hexdigest()
    return {"content-type": "application/json" if json_body else "application/x-www-form-urlencoded",
            "x-slack-request-timestamp": stamp, "x-slack-signature": "v0=" + digest}


def command(**changes):
    payload = {"team_id": "T123", "channel_id": "D123", "user_id": "U1",
               "trigger_id": "trigger-1", "text": "研究路线是什么？",
               "response_url": "https://hooks.slack.test/private-credential"}
    payload.update(changes)
    return urlencode(payload).encode()


def event(*, team_id="T123", event_id="Ev1", **changes):
    value = {"type": "message", "channel_type": "im", "channel": "D123",
             "user": "U1", "text": "研究路线是什么？", "ts": "1719200000.000100"}
    value.update(changes)
    return json.dumps({"type": "event_callback", "team_id": team_id,
                       "event_id": event_id, "event": value}).encode()


@pytest.fixture
def harness(monkeypatch, tmp_path):
    state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(tmp_path / "state.db"))
    links = SlackUserLinkStore(tmp_path / "links")
    h = SimpleNamespace(state=state, links=links, sent=[], calls=[], lifecycle=[], accounts={},
                        fail_delivery=0, history=[], commit_then_fail=False, accept=True,
                        block=False, entered=Event(), release=None)
    broker = WorkflowEventBroker(lambda: state)
    monkeypatch.setattr(api_module, "SLACK_TWIN_SIGNING_SECRET", "secret")
    monkeypatch.setattr(api_module, "SLACK_TWIN_TEAM_ID", "T123")
    monkeypatch.setattr(api_module, "SLACK_TWIN_BOT_TOKEN", "fixture-token")
    monkeypatch.setattr(api_module, "SLACK_TWIN_ALLOWED_USER_IDS", {"T123:U1"})
    monkeypatch.setattr(api_module, "slack_link_store", links)
    monkeypatch.setattr(api_module, "slack_workflow", None)
    monkeypatch.setattr(api_module, "settings", SimpleNamespace(warm_service_on_startup=False))
    async def answer(request, *, delivery, admission_guard):
        h.calls.append(request)
        if h.block:
            h.release = asyncio.Event()
            h.entered.set()
            await h.release.wait()
        response = ChatResponse(answer="完整的研究回答。", owner_name="Twin", used_model="fixture-model")
        delivered = ChatDeliveryGate().deliver(response=response, original_question=request.question)
        broker.finish_immediate(delivery.stream_id, delivered, guard=admission_guard)
        if h.commit_then_fail:
            h.commit_then_fail = False
            raise ConnectionError("answer acknowledgement lost")
        return delivered
    async def close():
        assert api_module.slack_workflow is None
        h.lifecycle.append("service-close")
    instance = SimpleNamespace(_conversation_store=SimpleNamespace(state_store=state),
                               start_background_work=lambda: h.lifecycle.append("service-start"))
    # HTTP and Slack share startup. Keep a real idle native HTTP workflow so
    # this Slack-only fixture exercises both workers' lifecycle without models.
    from sage_faculty_twin.config import AppSettings
    from sage_faculty_twin.guest_identity import GuestIdentityStore
    from sage_faculty_twin.user_store import UserAccountStore
    instance._settings = AppSettings(runtime_dir=tmp_path)
    instance._user_store = UserAccountStore(instance._settings)
    instance._guest_identity_store = GuestIdentityStore(state, ttl_seconds=300)
    h.service = SimpleNamespace(answer=answer, ensure_initialized=lambda: instance,
        is_initialized=lambda: True, aclose=close, get_slack_link_account=lambda uid: h.accounts.get(uid))
    monkeypatch.setattr(api_module, "service", h.service)
    def post(method, payload):
        if method == "auth.test":
            return {"ok": True, "team_id": "T123"}
        if h.fail_delivery:
            h.fail_delivery -= 1
            raise OSError("simulated network failure")
        h.sent.append((method, payload))
        return {"ok": True, "message_ts": "123.456"}
    monkeypatch.setattr(api_module, "_post_slack_api", post)
    def history(**kwargs):
        h.history.append(kwargs)
        return SlackChannelContext(text="Earlier lab discussion", message_count=2)
    monkeypatch.setattr(api_module, "fetch_slack_channel_context", history)
    def factory(shared):
        return SlackWorkflow(shared, authorize=api_module._authorize_slack_ingress,
            recover=SlackAnswerRecovery(shared, render=api_module._render_slack_reply),
            answer=api_module._produce_slack_reply, deliver=api_module._deliver_slack_reply,
            poll_interval=0.01, retry_delay=0.01, accept_task=lambda *_: h.accept)
    monkeypatch.setattr(api_module, "_create_slack_workflow", factory)
    def bind(role="lab_member"):
        h.accounts["account"] = SimpleNamespace(visitor_profile=role, email="member@example.com")
        code = links.create_code(user_id="account", email="old@example.com", visitor_profile="lab_member")
        links.consume_code(code.code, team_id="T123", slack_user_id="UOTHER")
    h.bind = bind
    with TestClient(api_module.llm_app) as client:
        h.client = client
        def flush():
            rows, _ = api_module.slack_workflow.journal.records(limit=100)
            for row in rows:
                client.portal.call(partial(api_module.slack_workflow.wait, row["key"], timeout=3))
        h.flush = flush
        yield h
    assert h.lifecycle == ["service-start", "service-close"] * (len(h.lifecycle) // 2)
    links.close()
    state.close()


def test_normalization_and_owned_memory():
    assert api_module._normalize_slack_twin_question("<@BOT> <@BOT> question") == "question"
    owner = api_module._slack_conversation_owner("T1", "U1", "C1")
    assert owner == api_module._slack_conversation_owner("T1", "U1", "C1")
    for ids in [("T2", "U1", "C1"), ("T1", "U2", "C1"), ("T1", "U1", "C2")]:
        assert api_module._slack_conversation_owner(*ids) != owner


@pytest.mark.parametrize("event_type", ["message", "app_mention"])
def test_event_status_is_private_durable_and_does_not_call_model(harness, event_type):
    original = event(type=event_type)
    assert harness.client.post("/slack/events", content=original, headers=signed(original, json_body=True)).status_code == 200
    harness.flush()
    rows, _ = api_module.slack_workflow.journal.records(limit=10)
    request_id = rows[0]["key"]
    assert request_id in harness.sent[0][1]["text"]
    query = event(event_id="EvStatus", type=event_type, text=f"status {request_id}")
    for _ in range(2):
        assert harness.client.post("/slack/events", content=query, headers=signed(query, json_body=True)).status_code == 200
        harness.flush()
    assert len(harness.calls) == 1
    assert len(harness.sent) == 2
    method, payload = harness.sent[-1]
    assert method == "chat.postEphemeral"
    assert payload["user"] == "U1"
    assert "处理完成" in payload["text"]
    assert "完整的研究回答" not in payload["text"]


@pytest.mark.parametrize("transport", ["command", "message", "mention"])
def test_terminal_answer_failure_sends_private_notice_without_question(harness, monkeypatch, transport):
    async def fail(*args, **kwargs):
        raise ValueError("private model exception")
    monkeypatch.setattr(harness.service, "answer", fail)
    body = command() if transport == "command" else event(type="app_mention" if transport == "mention" else "message")
    endpoint = "/slack/commands/twin" if transport == "command" else "/slack/events"
    response = harness.client.post(endpoint, content=body, headers=signed(body, json_body=transport != "command"))
    assert response.status_code == 200
    async def observe():
        while True:
            rows, _ = api_module.slack_workflow.journal.records(limit=10)
            if rows[0]["value"]["tasks"]["finalize"]["status"] == "done":
                return rows[0]["value"]
            await asyncio.sleep(0.01)
    record = harness.client.portal.call(partial(asyncio.wait_for, observe(), timeout=3))
    assert record["tasks"]["answer"]["status"] == "failed"
    assert record["tasks"]["answer"]["attempt"] == 1
    assert record["tasks"]["deliver"]["status"] == "blocked"
    assert len(harness.sent) == 1
    method, payload = harness.sent[0]
    assert method == "chat.postEphemeral"
    assert payload["user"] == "U1"
    assert "已停止自动重试" in payload["text"]
    assert "private model exception" not in payload["text"]
    assert "研究路线" not in payload["text"]


def test_signed_status_reports_terminal_failure_without_new_work_or_cross_channel_disclosure(harness):
    from sage.runtime.work_service import WorkFailedError
    harness.fail_delivery = 10
    body = command()
    response = harness.client.post("/slack/commands/twin", content=body, headers=signed(body))
    assert response.status_code == 200
    rows, _ = api_module.slack_workflow.journal.records(limit=10)
    request_id = rows[0]["key"]
    assert request_id in response.json()["text"]
    with pytest.raises(WorkFailedError):
        harness.flush()
    for channel, expected in [("D123", "处理已失败"), ("COTHER", "未找到")]:
        query = command(text=f"status {request_id}", trigger_id="status-query", channel_id=channel)
        reply = harness.client.post("/slack/commands/twin", content=query, headers=signed(query))
        assert reply.status_code == 200
        assert reply.json()["response_type"] == "ephemeral"
        assert expected in reply.json()["text"]
        assert "完整的研究回答" not in reply.json()["text"]
    rows, _ = api_module.slack_workflow.journal.records(limit=10)
    assert len(rows) == 1
    assert rows[0]["value"]["tasks"]["deliver"]["attempt"] == 5
    assert len(harness.calls) == 1
    replay = harness.client.post("/slack/commands/twin", content=body, headers=signed(body))
    assert "已失败并停止重试" in replay.json()["text"]
    assert "我正在问" not in replay.json()["text"]


@pytest.mark.parametrize("stop", ["timeout", "cancel", "error"])
def test_slack_answer_propagates_deadline_and_interrupts_active_io(harness, monkeypatch, stop):
    from sage_faculty_twin.request_context import (
        register_request_cancel_callback,
        request_has_cancellation_controller,
        request_remaining_seconds,
    )
    from sage_faculty_twin.slack_workflow import SlackIngress

    entered, interrupted, finished = Event(), Event(), Event()
    observed = []
    monkeypatch.setattr(api_module, "SLACK_TWIN_RESPONSE_TIMEOUT_SECONDS", 0.15)

    def io():
        unregister = register_request_cancel_callback(interrupted.set)
        observed.append((request_has_cancellation_controller(), request_remaining_seconds()))
        entered.set()
        try:
            interrupted.wait(2)  # Safety bound if cancellation propagation regresses.
        finally:
            unregister()
            finished.set()

    async def answer(*args, **kwargs):
        if stop == "error":
            register_request_cancel_callback(interrupted.set)
            raise ValueError("fixture failure")
        await asyncio.to_thread(io)
        raise AssertionError("cancelled I/O must not return a successful answer")

    monkeypatch.setattr(harness.service, "answer", answer)
    ingress = SlackIngress(team_id="T123", user_id="U1", channel_id="D123",
                           transport_id="cancellation", kind="command", question="research")

    async def scenario():
        task = asyncio.create_task(api_module._produce_slack_reply(ingress, admission_guard=None))
        if stop == "cancel":
            assert await asyncio.to_thread(entered.wait, 1)
            task.cancel()
        expected = {"timeout": TimeoutError, "cancel": asyncio.CancelledError, "error": ValueError}[stop]
        with pytest.raises(expected):
            await task
        assert interrupted.is_set()
        assert not request_has_cancellation_controller()
        assert request_remaining_seconds() is None
        if stop != "error":
            assert await asyncio.to_thread(finished.wait, 1)
            assert observed[0][0] is True
            assert 0 < observed[0][1] <= 0.15
    asyncio.run(scenario())
    assert harness.sent == []


@pytest.mark.parametrize("transport", ["command", "event"])
def test_bad_signature_never_admits(harness, transport):
    body = command() if transport == "command" else event()
    headers = signed(body, json_body=transport == "event")
    headers["x-slack-signature"] = "v0=bad"
    response = harness.client.post("/slack/commands/twin" if transport == "command" else "/slack/events",
                                   content=body, headers=headers)
    assert response.status_code == 401
    assert api_module.slack_workflow.journal.records(limit=10)[0] == []


@pytest.mark.parametrize("transport", ["command", "event"])
@pytest.mark.parametrize("team", ["", "TFOREIGN"])
def test_missing_or_foreign_workspace_rejected(harness, transport, team):
    body = command(team_id=team) if transport == "command" else event(team_id=team)
    response = harness.client.post("/slack/commands/twin" if transport == "command" else "/slack/events",
                                   content=body, headers=signed(body, json_body=transport == "event"))
    assert response.status_code == (400 if not team else 403)
    assert harness.calls == []


@pytest.mark.parametrize("role", [None, "general_visitor", "lab_member"])
def test_link_role_is_current_even_when_allowlisted(harness, monkeypatch, role):
    harness.bind()
    monkeypatch.setattr(api_module, "SLACK_TWIN_ALLOWED_USER_IDS", {"T123:UOTHER"})
    if role is None:
        del harness.accounts["account"]
    else:
        harness.accounts["account"].visitor_profile = role
    profile, email, denial = api_module._resolve_slack_twin_identity("UOTHER", team_id="T123")
    assert (denial is None) == (role == "lab_member")
    if role == "lab_member":
        assert (profile, email) == ("lab_member", "member@example.com")


@pytest.mark.parametrize("transport", ["command", "event"])
def test_bare_allowlist_is_not_authorization(harness, monkeypatch, transport):
    monkeypatch.setattr(api_module, "SLACK_TWIN_ALLOWED_USER_IDS", {"U1"})
    body = command() if transport == "command" else event()
    response = harness.client.post("/slack/commands/twin" if transport == "command" else "/slack/events",
                                   content=body, headers=signed(body, json_body=transport == "event"))
    assert response.status_code == 200
    harness.flush()
    text = response.json()["text"] if transport == "command" else harness.sent[0][1]["text"]
    assert "生成 Slack 绑定码" in text
    assert harness.calls == []


@pytest.mark.parametrize("transport", ["command", "event", "mention"])
def test_signed_requests_use_durable_answer_and_correct_visibility(harness, transport):
    harness.bind()
    body = (command(user_id="UOTHER", owner_id="account:forged") if transport == "command"
            else event(user="UOTHER", **({"type": "app_mention", "channel_type": "channel",
                                         "text": "<@BOT> 研究路线是什么？"} if transport == "mention" else {})))
    response = harness.client.post("/slack/commands/twin" if transport == "command" else "/slack/events",
                                   content=body, headers=signed(body, json_body=transport != "command"))
    assert response.status_code == 200
    harness.flush()
    assert len(harness.calls) == len(harness.sent) == 1
    request = harness.calls[0]
    assert request.owner_id == api_module._slack_conversation_owner("T123", "UOTHER", "D123")
    assert request.student_email == "member@example.com"
    assert request.visitor_profile == "lab_member"
    assert request.slack_channel_context == "Earlier lab discussion"
    assert "slack_channel_context" not in request.model_dump()
    method, payload = harness.sent[0]
    assert method == ("chat.postEphemeral" if transport == "command" else "chat.postMessage")
    assert "完整的研究回答。" in payload["text"]
    if transport == "command":
        assert payload["user"] == "UOTHER"
    if transport == "mention":
        assert payload["thread_ts"] == "1719200000.000100"
    records, _ = api_module.slack_workflow.journal.records(limit=10)
    assert records[0]["value"]["dependencies"] == {"deliver": ["answer"], "finalize": ["answer", "deliver"]}
    assert records[0]["value"]["tasks"]["deliver"]["attempt"] == 1
    saved = json.dumps(records)
    assert "private-credential" not in saved
    assert "fixture-token" not in saved
    assert "Earlier lab discussion" not in saved


def test_event_retry_and_lost_answer_ack_do_not_regenerate(harness):
    harness.commit_then_fail = True
    harness.fail_delivery = 1
    body = event()
    headers = signed(body, json_body=True)
    assert harness.client.post("/slack/events", content=body, headers=headers).status_code == 200
    headers["x-slack-retry-num"] = "1"
    assert harness.client.post("/slack/events", content=body, headers=headers).status_code == 200
    harness.flush()
    assert len(harness.calls) == len(harness.sent) == 1


def test_startup_recovers_http_admission_after_service_reconstruction(harness):
    harness.accept = False
    body = event()
    headers = signed(body, json_body=True)
    assert harness.client.post("/slack/events", content=body, headers=headers).status_code == 200
    assert harness.calls == []
    harness.client.portal.call(api_module.shutdown_event)
    assert api_module.slack_workflow is None
    harness.accept = True
    harness.client.portal.call(api_module.startup_event)
    harness.flush()
    assert len(harness.calls) == len(harness.sent) == 1
    headers["x-slack-retry-num"] = "1"
    assert harness.client.post("/slack/events", content=body, headers=headers).status_code == 200
    harness.flush()
    assert len(harness.calls) == len(harness.sent) == 1


def test_shutdown_drains_handler_before_closing_model_and_recovers_delivery(harness):
    harness.block = True
    body = command()
    assert harness.client.post("/slack/commands/twin", content=body, headers=signed(body)).status_code == 200
    assert harness.entered.wait(3)
    closing = harness.client.portal.start_task_soon(api_module.shutdown_event)
    try:
        time.sleep(0.03)
        assert not closing.done()
        assert "service-close" not in harness.lifecycle
    finally:
        harness.block = False
        harness.client.portal.call(harness.release.set)
        closing.result(timeout=3)
        harness.client.portal.call(api_module.startup_event)
    harness.flush()
    assert len(harness.calls) == len(harness.sent) == 1


def test_unconfigured_workspace_cannot_admit(harness, monkeypatch):
    monkeypatch.setattr(api_module, "SLACK_TWIN_TEAM_ID", "")
    body = command()
    assert harness.client.post("/slack/commands/twin", content=body, headers=signed(body)).status_code == 503
    assert harness.calls == []


@pytest.mark.parametrize("missing", [
    "SLACK_TWIN_SIGNING_SECRET", "SLACK_TWIN_TEAM_ID", "SLACK_TWIN_BOT_TOKEN",
])
def test_partial_slack_configuration_fails_before_service_start(harness, monkeypatch, missing):
    harness.client.portal.call(api_module.shutdown_event)
    starts = harness.lifecycle.count("service-start")
    with monkeypatch.context() as patch:
        patch.setattr(api_module, missing, "")
        with pytest.raises(RuntimeError, match=f"slack_configuration_incomplete:{missing}"):
            harness.client.portal.call(api_module.startup_event)
        assert harness.lifecycle.count("service-start") == starts
    harness.client.portal.call(api_module.startup_event)


def test_wrong_bot_workspace_is_rejected_before_service_start(harness, monkeypatch):
    harness.client.portal.call(api_module.shutdown_event)
    starts = harness.lifecycle.count("service-start")
    with monkeypatch.context() as patch:
        patch.setattr(api_module, "_post_slack_api", lambda *_: {"ok": True, "team_id": "TOTHER"})
        with pytest.raises(RuntimeError, match="workspace_mismatch"):
            harness.client.portal.call(api_module.startup_event)
        assert harness.lifecycle.count("service-start") == starts
    harness.client.portal.call(api_module.startup_event)


def test_public_binding_notice_is_private(harness):
    harness.accounts["account"] = SimpleNamespace(visitor_profile="lab_member", email="private@example.com")
    code = harness.links.create_code(user_id="account", email="private@example.com", visitor_profile="lab_member")
    body = event(type="app_mention", channel_type="channel", user="UOTHER", text=f"<@BOT> bind {code.code}")
    assert harness.client.post("/slack/events", content=body, headers=signed(body, json_body=True)).status_code == 200
    harness.flush()
    method, payload = harness.sent[0]
    assert method == "chat.postEphemeral"
    assert payload["user"] == "UOTHER"


def test_worker_revalidates_revocation_after_http_admission(harness):
    harness.bind()
    harness.accept = False
    body = event(user="UOTHER")
    assert harness.client.post("/slack/events", content=body, headers=signed(body, json_body=True)).status_code == 200
    del harness.accounts["account"]
    harness.accept = True
    harness.flush()
    assert harness.calls == []
    assert "不是课题组成员" in harness.sent[0][1]["text"]


def test_same_transport_id_with_different_input_conflicts(harness):
    for text, status in [("first", 200), ("different", 409)]:
        body = command(text=text)
        assert harness.client.post("/slack/commands/twin", content=body, headers=signed(body)).status_code == status
    harness.flush()
    assert len(harness.calls) == 1


@pytest.mark.parametrize("transport", ["command", "event"])
def test_missing_transport_id_rejected(harness, transport):
    body = command(trigger_id="") if transport == "command" else event(event_id="")
    response = harness.client.post("/slack/commands/twin" if transport == "command" else "/slack/events",
                                   content=body, headers=signed(body, json_body=transport == "event"))
    assert response.status_code == 400


def test_admission_failure_is_not_acknowledged(harness, monkeypatch):
    async def fail(*args, **kwargs):
        raise RuntimeError("storage unavailable")
    monkeypatch.setattr(api_module.slack_workflow, "submit", fail)
    body = command()
    with pytest.raises(RuntimeError, match="storage unavailable"):
        harness.client.post("/slack/commands/twin", content=body, headers=signed(body))
    assert harness.sent == []


@pytest.mark.parametrize("transport", ["command", "event"])
@pytest.mark.parametrize("binding", [False, True])
def test_capacity_rejection_is_not_acknowledged(harness, monkeypatch, transport, binding):
    from sage.runtime.work_journal import WorkCapacityError

    async def reject(*args, **kwargs):
        raise WorkCapacityError("work_capacity_exhausted")

    monkeypatch.setattr(api_module.slack_workflow, "bind" if binding else "submit", reject)
    question = "bind fixture-code" if binding else "研究路线是什么？"
    body = command(text=question) if transport == "command" else event(text=question)
    response = harness.client.post("/slack/commands/twin" if transport == "command" else "/slack/events",
        content=body, headers=signed(body, json_body=transport == "event"))
    assert response.status_code == 503
    assert "未接收" in response.json()["detail"]
    assert response.headers["retry-after"] == "2"
    assert harness.sent == []
    assert harness.calls == []
    assert api_module.slack_workflow.journal.records()[0] == []


def test_context_request_without_history_never_calls_model(harness, monkeypatch):
    def missing(**kwargs):
        raise SlackHistoryUnavailable("not_in_channel")
    monkeypatch.setattr(api_module, "fetch_slack_channel_context", missing)
    body = command(text="总结一下前面的上下文")
    assert harness.client.post("/slack/commands/twin", content=body, headers=signed(body)).status_code == 200
    harness.flush()
    assert harness.calls == []
    assert "读不到当前 Slack 会话的历史消息" in harness.sent[0][1]["text"]


@pytest.mark.parametrize("transport", ["command", "event"])
def test_bind_success_and_event_retry_do_not_persist_code(harness, transport):
    harness.accounts["account"] = SimpleNamespace(visitor_profile="lab_member", email="member@example.com")
    code = harness.links.create_code(user_id="account", email="member@example.com", visitor_profile="lab_member")
    body = command(user_id="UOTHER", text=f"bind {code.code}") if transport == "command" else event(user="UOTHER", text=f"bind {code.code}")
    endpoint = "/slack/commands/twin" if transport == "command" else "/slack/events"
    headers = signed(body, json_body=transport == "event")
    response = harness.client.post(endpoint, content=body, headers=headers)
    assert response.status_code == 200
    if transport == "event":
        headers["x-slack-retry-num"] = "1"
        assert harness.client.post(endpoint, content=body, headers=headers).status_code == 200
    harness.flush()
    assert "绑定成功" in harness.sent[0][1]["text"]
    assert harness.links.get_link_for_slack_user("UOTHER", team_id="T123").user_id == "account"
    rows, _ = api_module.slack_workflow.journal.records(limit=10)
    assert code.code not in json.dumps(rows)


def test_invalid_bind_and_usage(harness):
    for text, expected in [("bind BADCODE", "收到绑定请求"), ("bind", "用法"), ("", "用法")]:
        body = command(text=text)
        response = harness.client.post("/slack/commands/twin", content=body, headers=signed(body))
        assert response.status_code == 200
        assert expected in response.json()["text"]
    body = event(type="app_mention", text="<@BOT>", channel_type="channel")
    assert harness.client.post("/slack/events", content=body, headers=signed(body, json_body=True)).status_code == 200
    harness.flush()
    assert any("无效或已过期" in item[1]["text"] for item in harness.sent)
    assert any("用法" in item[1]["text"] for item in harness.sent)


def test_binding_lost_commit_ack_recovers_notice_after_restart(harness, monkeypatch):
    harness.accept = False
    harness.accounts["account"] = SimpleNamespace(visitor_profile="lab_member", email="member@example.com")
    code = harness.links.create_code(user_id="account", email="member@example.com", visitor_profile="lab_member")
    body = event(user="UOTHER", text=f"bind {code.code}")
    original = harness.state.update_many
    def lose_ack(*args, **kwargs):
        original(*args, **kwargs)
        raise ConnectionError("injected lost transaction acknowledgement")
    with monkeypatch.context() as patch:
        patch.setattr(harness.state, "update_many", lose_ack)
        with pytest.raises(ConnectionError):
            harness.client.post("/slack/events", content=body, headers=signed(body, json_body=True))
    assert harness.links.get_link_for_slack_user("UOTHER", team_id="T123") is not None
    harness.client.portal.call(api_module.shutdown_event)
    harness.accept = True
    harness.client.portal.call(api_module.startup_event)
    harness.flush()
    assert len(harness.sent) == 1
    assert "绑定成功" in harness.sent[0][1]["text"]
    assert harness.client.post("/slack/events", content=body, headers=signed(body, json_body=True)).status_code == 200
    harness.flush()
    assert len(harness.sent) == 1


def test_invalid_binding_retry_replays_same_notice(harness):
    body = event(user="UOTHER", text="bind INVALID")
    headers = signed(body, json_body=True)
    assert harness.client.post("/slack/events", content=body, headers=headers).status_code == 200
    harness.flush()
    headers["x-slack-retry-num"] = "1"
    assert harness.client.post("/slack/events", content=body, headers=headers).status_code == 200
    harness.flush()
    assert len(harness.sent) == 1
    assert "无效或已过期" in harness.sent[0][1]["text"]


def test_url_verification(harness):
    body = json.dumps({"type": "url_verification", "challenge": "challenge-value"}).encode()
    response = harness.client.post("/slack/events", content=body, headers=signed(body, json_body=True))
    assert response.json() == {"challenge": "challenge-value"}


@pytest.mark.parametrize("role", ["lab_member", "general_visitor"])
def test_web_link_code_membership_gate(harness, monkeypatch, role):
    session = UserSessionResponse(is_authenticated=True, mode="user", account=UserAccountResponse(user_id="account",
        name="Member", email="member@example.com", visitor_profile=role, created_at="2026-06-23T00:00:00+00:00"))
    monkeypatch.setattr(harness.service, "get_user_session", lambda _: session, raising=False)
    response = harness.client.post("/slack/twin-link/code")
    assert response.status_code == 200
    assert response.json()["can_link"] == (role == "lab_member")
    if role == "lab_member":
        assert len(response.json()["code"]) == 8
