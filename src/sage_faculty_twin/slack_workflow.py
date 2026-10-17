"""Slack transport contract over SAGE's one durable work engine.

Only verified identity/destination data crosses this boundary, never a token or
bearer response URL. The host supplies authorized answer/recovery/delivery ports.
External delivery is at-least-once; journal leases cannot fence Slack effects.
"""

import asyncio
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from sage.runtime.async_work_service import AsyncWorkService
from sage.runtime.event_journal import EventJournal
from sage.runtime.work_journal import WorkJournal
from sage.runtime.work_dispatcher import WorkFailure

from .post_answer_contract import DeliveryIntent
from .models import ChatResponse

_ANSWER_DEPENDENCIES = {"deliver": ["answer"], "finalize": ["answer", "deliver"]}


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
        on success. Resolve configured credentials only inside the delivery port.
        The borrowed state and all ports must outlive aclose's successful drain.
        """
        self.journal = WorkJournal(state, namespace="twin-slack-workflow-v1")
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

    def _classify_failure(self, lease, error):
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
                await asyncio.to_thread(self._upgrade_dependencies)
                await self._work.start()
                self._started = True

    def _upgrade_dependencies(self):
        cursor = 0
        while True:
            rows, cursor = self.journal.records(cursor=cursor, limit=100)
            for row in rows:
                if set(row["value"]["tasks"]) == {"answer", "deliver"}:
                    self.journal.upgrade_dependencies(row["key"], {"deliver": ["answer"]})
                    payload = row["value"]["tasks"]["answer"]["payload"]
                    self.journal.extend_tasks(row["key"], tasks={"finalize": payload},
                                              dependencies={"finalize": ["answer", "deliver"]},
                                              settled=["finalize"])
            if cursor is None:
                return

    async def submit(self, ingress: SlackIngress):
        captured = SlackIngress.model_validate_json(ingress.model_dump_json())
        payload = captured.model_dump(mode="json")
        await self.start()
        return await self._work.commit(captured.request_id,
            result={"owner_id": captured.owner_id},
            tasks={"answer": payload, "deliver": payload, "finalize": payload},
            dependencies=_ANSWER_DEPENDENCIES, settled=["finalize"])

    async def submit_notice(self, ingress: SlackIngress, reply: SlackReply):
        captured = SlackIngress.model_validate_json(ingress.model_dump_json())
        await self.start()
        return await self._work.commit(captured.request_id,
            result={"owner_id": captured.owner_id, "notice": SlackReply.model_validate(reply).model_dump(mode="json")},
            tasks={"deliver": captured.model_dump(mode="json")})

    async def bind(self, ingress, bindings, code):
        if bindings.state_store is not self._state:
            raise ValueError("slack_binding_and_work_must_share_state")
        captured = SlackIngress.model_validate_json(ingress.model_dump_json())
        if captured.question != "Slack binding status":
            raise ValueError("binding_credential_must_not_be_persisted")
        notice = SlackReply(text="绑定成功。使用 /twin 时仍会核对当前课题组成员权限。")
        await self.start()
        await self._work.commit(captured.request_id,
            result={"owner_id": captured.owner_id, "notice": notice.model_dump(mode="json")},
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
        tasks = {name: payload for name in record["tasks"]}
        await self._work.commit(ingress.request_id, result=record["result"], tasks=tasks,
                                dependencies=record.get("dependencies"), settled=record.get("settled"))
        return True

    async def _handle(self, lease):
        ingress = SlackIngress.model_validate(lease.payload)
        if ingress.request_id != lease.request_id:
            raise ValueError("slack_request_identity_mismatch")
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
            return reply.model_dump(mode="json")
        if lease.task_id not in {"deliver", "finalize"}:
            raise ValueError("unknown_slack_work_kind")
        record = await asyncio.to_thread(self.journal.read, lease.request_id)
        notice = record["result"].get("notice")
        if lease.task_id == "finalize":
            if not any(record["tasks"][name]["status"] in {"failed", "blocked"}
                       for name in ("answer", "deliver")):
                return {"skipped": True}
            denial = await self._authorize(ingress)
            reply = SlackReply(text=denial or (
                "本次 Twin 请求处理或投递失败，已停止自动重试。请联系管理员检查。\n"
                f"查询状态：`/twin status {ingress.request_id}`"))
            # Failure details must not become a public channel message.
            ingress = ingress.model_copy(update={"private_reply": True})
        elif notice is not None:
            reply = SlackReply.model_validate(notice)
        else:
            completed = record["tasks"]["answer"]
            denial = await self._authorize(ingress)
            reply = SlackReply(text=denial) if denial is not None else SlackReply.model_validate(completed["result"])
        receipt = await self._deliver(ingress, reply)
        if not isinstance(receipt, dict) or receipt.get("accepted") is not True:
            raise RuntimeError("slack_delivery_requires_accepted_receipt")
        return SlackReceipt.model_validate(receipt).model_dump(mode="json")

    async def wait(self, request_id, *, timeout):
        return await self._work.wait(request_id, timeout=timeout)

    async def aclose(self, *, timeout=30):
        await self._work.aclose(timeout=timeout)
