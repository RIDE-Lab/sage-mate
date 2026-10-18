"""Slack transport contract over SAGE's one durable work engine.

Only verified identity/destination data crosses this boundary, never a token or
bearer response URL. The host supplies authorized answer/recovery/delivery ports.
External delivery is at-least-once; journal leases cannot fence Slack effects.
"""

import asyncio
import hashlib
import json
import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sage.runtime.async_work_service import AsyncWorkService
from sage.runtime.delivery_sequence import DeliveryPlan, DeliveryReceipt, DeliverySequence
from sage.runtime.markdown_delivery import character_units, plan_markdown
from sage.runtime.event_journal import EventJournal
from sage.runtime.work_journal import WorkJournal
from sage.runtime.work_dispatcher import WorkFailure
from sage.runtime.source_audit import AnswerValidationRejected
from sage.runtime.model_errors import ModelRequestRejected
from sage.runtime.flownet.client.runtime_client import FlowRequestOutcomeError

from .post_answer_contract import DeliveryIntent
from .models import ChatResponse

_ANSWER_DEPENDENCIES = {"deliver": ["answer"], "finalize": ["answer", "deliver"]}
_SLACK_MARKDOWN_LIMIT = 12000


def _destination(ingress, *, requires_member):
    return {"protocol": "slack-markdown-v1", "visibility": "requester",
            "authorization": "member" if requires_member else "requester_notice",
            "team_id": ingress.team_id, "channel_id": ingress.channel_id, "user_id": ingress.user_id}


def _capture_reply(ingress, reply, *, requires_member):
    """Transport policy only; parsing/formatting/framing belongs to SAGE."""
    if type(requires_member) is not bool:
        raise ValueError("invalid_slack_delivery_policy")
    text = SlackReply.model_validate(reply).text
    controls = tuple(dict.fromkeys(re.findall(r"<[@#!][^>\r\n]*>", text)))
    markdown = plan_markdown(text, max_chars=_SLACK_MARKDOWN_LIMIT, literal_tokens=controls)
    plan = DeliveryPlan.capture(destination=_destination(ingress, requires_member=requires_member),
        payloads=[{"markdown_text": frame.text} for frame in markdown.frames])
    return {"version": 1, "requires_member": requires_member, "delivery_plan": plan.to_dict()}


def _restore_reply(ingress, captured):
    if (not isinstance(captured, dict)
            or set(captured) != {"version", "requires_member", "delivery_plan"}
            or type(captured["version"]) is not int or captured["version"] != 1
            or type(captured["requires_member"]) is not bool):
        raise ValueError("invalid_slack_delivery_plan")
    plan = DeliveryPlan.from_dict(captured["delivery_plan"])
    inputs = plan.to_dict()
    if inputs["destination"] != _destination(ingress, requires_member=captured["requires_member"]):
        raise PermissionError("slack_delivery_destination_mismatch")
    for payload in inputs["payloads"]:
        if (set(payload) != {"markdown_text"} or not isinstance(payload["markdown_text"], str)
                or not payload["markdown_text"]
                or character_units(payload["markdown_text"]) > _SLACK_MARKDOWN_LIMIT):
            raise ValueError("invalid_slack_delivery_frame")
    return plan


class SlackIngress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    team_id: str = Field(pattern=r"^[A-Za-z0-9]+$")
    user_id: str = Field(pattern=r"^[A-Za-z0-9]+$")
    channel_id: str = Field(pattern=r"^[A-Za-z0-9]+$")
    transport_id: str = Field(min_length=1, max_length=256)
    kind: Literal["command", "message", "mention"]
    private_reply: bool = False
    question: str = Field(min_length=1)
    thread_ts: str | None = Field(default=None, pattern=r"^[0-9]+\.[0-9]+$")
    latest_ts: str = Field(default="", pattern=r"^(?:[0-9]+(?:\.[0-9]+)?)?$")

    @property
    def owner_id(self):
        identity = json.dumps([self.team_id, self.user_id, self.channel_id], separators=(",", ":"))
        return "slack:" + hashlib.sha256(identity.encode()).hexdigest()

    @property
    def request_id(self):
        identity = json.dumps([self.team_id, self.kind, self.transport_id], separators=(",", ":"))
        return "slack-" + hashlib.sha256(identity.encode()).hexdigest()

    @property
    def delivery(self):
        return DeliveryIntent(owner_id=self.owner_id, request_id=self.request_id)


class SlackReply(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    text: str = Field(min_length=1)


class SlackReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    accepted: Literal[True]
    message_ts: str | None = Field(default=None, pattern=r"^[0-9]+\.[0-9]+$")


class SlackAnswerRecovery:
    """Read the canonical answer admitted with Twin's memory/work transaction."""

    def __init__(self, state, *, render):
        self._events = EventJournal(state, namespace="twin-workflow-events-v1")
        self._render = render

    async def __call__(self, ingress):
        cursor = 0
        while True:
            page = await asyncio.to_thread(self._events.read, ingress.delivery.stream_id,
                                           after=cursor, limit=100)
            for event in page["events"]:
                if event["id"] == "answer_done":
                    response = ChatResponse.model_validate(event["payload"]["response"])
                    return SlackReply(text=self._render(response, ingress))
            if page["caught_up"]:
                return None
            cursor = page["cursor"]


class SlackWorkflow:
    def __init__(self, state, *, authorize, recover, answer, deliver, max_attempts=5, **options):
        """Ports are async. authorize returns denial text or None, never a role cache.

        recover must read the canonical answer using ingress.delivery before
        answer can run again. answer must commit its answer through that same
        delivery identity. deliver must raise on failure and return a JSON receipt
        on success. Its SlackReply is one already captured Markdown frame, not
        the raw answer; the port sends markdown_text without rerendering. Resolve
        configured credentials only inside the delivery port. Admission captures
        notice/failure plans, answer settlement captures generated/recovered plans.
        The borrowed state and all ports must outlive aclose's successful drain.
        """
        self.journal = WorkJournal(state, namespace="twin-slack-workflow-v1")
        self._sequence = DeliverySequence(self.journal, namespace="twin-slack-delivery-v1")
        self._state = state
        self._authorize, self._recover = authorize, recover
        self._answer, self._deliver = answer, deliver
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("slack_max_attempts_must_be_positive")
        self._max_attempts = max_attempts
        self._retry_delay = options.get("retry_delay", 5)
        self._work = AsyncWorkService(self.journal, self._handle,
                                      **{"max_workers": 2, "poll_interval": 0.05,
                                         "failure_policy": self._classify_failure, **options})
        self._start_lock = asyncio.Lock()
        self._started = False

    def work_status(self):
        return self._work.status()

    def _classify_failure(self, lease, error):
        if isinstance(error, ModelRequestRejected):
            return WorkFailure(error.code)
        if isinstance(error, FlowRequestOutcomeError) and error.error_type == "ModelRequestRejected":
            code = error.message
            if code not in {"prompt_capacity", "model_request_invalid", "model_access_denied"}:
                code = "model_request_invalid"
            return WorkFailure(code)
        if isinstance(error, AnswerValidationRejected) or (
            isinstance(error, FlowRequestOutcomeError)
            and error.error_type in {"SourceAuditRejected", "AnswerValidationRejected", "InvalidSourcePlan"}
        ):
            return WorkFailure("answer_validation_rejected")
        status = getattr(error, "status_code", getattr(error, "code", None))
        permanent = isinstance(error, (ValueError, PermissionError)) or (
            isinstance(status, int) and 400 <= status < 500 and status not in {408, 429}
        )
        code = "invalid_request" if permanent else "temporary_failure"
        if permanent or lease.attempt >= self._max_attempts:
            return WorkFailure(code if permanent else "retry_exhausted")
        return WorkFailure(code, min(60, self._retry_delay * 2 ** min(lease.attempt - 1, 10)))

    async def status(self, ingress, request_id):
        """Owner-scoped operational metadata only; never return a stored prompt."""
        if await self._authorize(ingress) is not None:
            return None
        record = await asyncio.to_thread(self.journal.read, request_id)
        if record is None or record["result"].get("owner_id") != ingress.owner_id:
            return None
        tasks = {name: {key: task[key] for key in ("status", "attempt", "failure_code") if key in task}
                 for name, task in record["tasks"].items()}
        failed = any(task["status"] in {"failed", "blocked"} for task in tasks.values())
        done = all(task["status"] == "done" for task in tasks.values())
        return {"status": "failed" if failed else "done" if done else "pending", "tasks": tasks}

    async def start(self):
        async with self._start_lock:
            if not self._started:
                await asyncio.to_thread(self._check_delivery_cutover)
                await self._work.start()
                self._started = True

    def _check_delivery_cutover(self):
        cursor = 0
        while True:
            rows, cursor = self.journal.records(cursor=cursor, limit=100)
            for row in rows:
                record = row["value"]
                version = record["result"].get("workflow_version")
                if (type(version) is not int or version != 2) and any(
                        task["status"] in {"pending", "running"} for task in record["tasks"].values()):
                    raise RuntimeError("slack_legacy_work_requires_drain")
            if cursor is None:
                return

    async def submit(self, ingress: SlackIngress):
        captured = SlackIngress.model_validate_json(ingress.model_dump_json())
        payload = captured.model_dump(mode="json")
        failure = SlackReply(text=(
            "本次 Twin 请求处理或投递失败，已停止自动重试。请联系管理员检查。\n"
            f"查询状态：`/twin status {captured.request_id}`"))
        failure_plan = await asyncio.to_thread(_capture_reply, captured, failure, requires_member=False)
        await self.start()
        return await self._work.commit(captured.request_id,
            result={"workflow_version": 2, "owner_id": captured.owner_id, "failure_plan": failure_plan},
            tasks={"answer": payload, "deliver": payload, "finalize": payload},
            dependencies=_ANSWER_DEPENDENCIES, settled=["finalize"])

    async def submit_notice(self, ingress: SlackIngress, reply: SlackReply):
        captured = SlackIngress.model_validate_json(ingress.model_dump_json())
        planned = await asyncio.to_thread(_capture_reply, captured, reply, requires_member=False)
        await self.start()
        return await self._work.commit(captured.request_id,
            result={"workflow_version": 2, "owner_id": captured.owner_id, "notice": planned},
            tasks={"deliver": captured.model_dump(mode="json")})

    async def bind(self, ingress, bindings, code):
        if bindings.state_store is not self._state:
            raise ValueError("slack_binding_and_work_must_share_state")
        captured = SlackIngress.model_validate_json(ingress.model_dump_json())
        if captured.question != "Slack binding status":
            raise ValueError("binding_credential_must_not_be_persisted")
        notice = SlackReply(text="绑定成功。使用 /twin 时仍会核对当前课题组成员权限。")
        planned = await asyncio.to_thread(_capture_reply, captured, notice, requires_member=False)
        await self.start()
        await self._work.commit(captured.request_id,
            result={"workflow_version": 2, "owner_id": captured.owner_id, "notice": planned},
            tasks={"deliver": captured.model_dump(mode="json")},
            mutations=bindings.prepare_code_consumption(code, slack_user_id=captured.user_id,
                team_id=captured.team_id, operation_id=captured.request_id))
        return notice

    async def resume_if_admitted(self, ingress: SlackIngress):
        await self.start()
        record = await asyncio.to_thread(self.journal.read, ingress.request_id)
        if record is None:
            return False
        payload = SlackIngress.model_validate_json(ingress.model_dump_json()).model_dump(mode="json")
        tasks = {name: task["payload"] for name, task in record["tasks"].items()}
        try:
            same_payload = all(SlackIngress.model_validate(value).model_dump(mode="json") == payload
                               for value in tasks.values())
        except ValueError:
            raise ValueError("journal_commit_conflict") from None
        if not same_payload or record["result"].get("owner_id") != ingress.owner_id:
            raise ValueError("journal_commit_conflict")
        # Preserve the original journal bytes, including absent optional defaults
        # in terminal history. This is metadata replay, never an old sender.
        await self._work.commit(ingress.request_id, result=record["result"], tasks=tasks,
                                dependencies=record.get("dependencies"), settled=record.get("settled"))
        return True

    async def _handle(self, lease):
        ingress = SlackIngress.model_validate(lease.payload)
        if ingress.request_id != lease.request_id:
            raise ValueError("slack_request_identity_mismatch")
        record = await asyncio.to_thread(self.journal.read, lease.request_id)
        if record["result"].get("owner_id") != ingress.owner_id:
            raise PermissionError("slack_work_owner_mismatch")
        if lease.task_id == "answer":
            denial = await self._authorize(ingress)
            if denial is not None:
                reply = SlackReply(text=denial)
            else:
                reply = await self._recover(ingress)
                if reply is None:
                    try:
                        reply = await self._answer(ingress, admission_guard=self.journal.prepare_guard(lease))
                    except Exception:
                        # A lost acknowledgement can follow a committed answer,
                        # including on the final allowed attempt. Recover that
                        # answer before classifying the attempt as failed.
                        reply = await self._recover(ingress)
                        if reply is None:
                            raise
                reply = SlackReply.model_validate(reply)
            # This becomes the native answer-task result before its ACK. A
            # delivery retry must never call the formatter or model again.
            return await asyncio.to_thread(_capture_reply, ingress, reply, requires_member=denial is None)
        if lease.task_id not in {"deliver", "finalize"}:
            raise ValueError("unknown_slack_work_kind")
        notice = record["result"].get("notice")
        if lease.task_id == "finalize":
            if not any(record["tasks"][name]["status"] in {"failed", "blocked"}
                       for name in ("answer", "deliver")):
                return {"skipped": True}
            planned = record["result"]["failure_plan"]
            ingress = ingress.model_copy(update={"private_reply": True})
        elif notice is not None:
            planned = notice
        else:
            planned = record["tasks"]["answer"]["result"]
        plan = _restore_reply(ingress, planned)

        async def authorize():
            denial = await self._authorize(ingress)
            if denial is not None and (planned["requires_member"] or not isinstance(denial, str)):
                raise PermissionError("slack_delivery_not_authorized")

        async def send(frame):
            receipt = await self._deliver(ingress, SlackReply(text=frame.payload["markdown_text"]))
            if not isinstance(receipt, dict) or receipt.get("accepted") is not True:
                raise RuntimeError("slack_delivery_requires_accepted_receipt")
            accepted = SlackReceipt.model_validate(receipt)
            return DeliveryReceipt(external_id=accepted.message_ts)

        saved = await self._sequence.deliver(lease, plan, send=send, authorize=authorize)
        return {"accepted": True, "parts": len(saved["receipts"])}

    async def wait(self, request_id, *, timeout):
        return await self._work.wait(request_id, timeout=timeout)

    async def aclose(self, *, timeout=30):
        await self._work.aclose(timeout=timeout)
