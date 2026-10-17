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

from .post_answer_contract import DeliveryIntent
from .models import ChatResponse


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
    def __init__(self, state, *, authorize, recover, answer, deliver, **options):
        """Ports are async. authorize returns denial text or None, never a role cache.

        recover must read the canonical answer using ingress.delivery before
        answer can run again. answer must commit its answer through that same
        delivery identity. deliver must raise on failure and return a JSON receipt
        on success. Resolve configured credentials only inside the delivery port.
        The borrowed state and all ports must outlive aclose's successful drain.
        """
        self.journal = WorkJournal(state, namespace="twin-slack-workflow-v1")
        self._authorize, self._recover = authorize, recover
        self._answer, self._deliver = answer, deliver
        self._work = AsyncWorkService(self.journal, self._handle,
                                      **{"max_workers": 2, "poll_interval": 0.05, **options})

    async def start(self):
        await self._work.start()

    async def submit(self, ingress: SlackIngress):
        captured = SlackIngress.model_validate_json(ingress.model_dump_json())
        payload = captured.model_dump(mode="json")
        return await self._work.commit(captured.request_id,
            result={"owner_id": captured.owner_id}, tasks={"answer": payload, "deliver": payload})

    async def submit_notice(self, ingress: SlackIngress, reply: SlackReply):
        captured = SlackIngress.model_validate_json(ingress.model_dump_json())
        return await self._work.commit(captured.request_id,
            result={"owner_id": captured.owner_id, "notice": SlackReply.model_validate(reply).model_dump(mode="json")},
            tasks={"deliver": captured.model_dump(mode="json")})

    async def resume_if_admitted(self, ingress: SlackIngress):
        record = await asyncio.to_thread(self.journal.read, ingress.request_id)
        if record is None:
            return False
        payload = SlackIngress.model_validate_json(ingress.model_dump_json()).model_dump(mode="json")
        tasks = {name: payload for name in record["tasks"]}
        await self._work.commit(ingress.request_id, result=record["result"], tasks=tasks)
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
                    reply = await self._answer(ingress)
                reply = SlackReply.model_validate(reply)
            return reply.model_dump(mode="json")
        if lease.task_id != "deliver":
            raise ValueError("unknown_slack_work_kind")
        record = await asyncio.to_thread(self.journal.read, lease.request_id)
        notice = record["result"].get("notice")
        if notice is not None:
            reply = SlackReply.model_validate(notice)
        else:
            completed = record["tasks"]["answer"]
            if completed["status"] != "done":
                raise RuntimeError("slack_answer_not_ready")
            denial = await self._authorize(ingress)
            reply = SlackReply(text=denial) if denial is not None else SlackReply.model_validate(completed["result"])
        receipt = await self._deliver(ingress, reply)
        if not isinstance(receipt, dict) or receipt.get("accepted") is not True:
            raise ValueError("slack_delivery_requires_accepted_receipt")
        return SlackReceipt.model_validate(receipt).model_dump(mode="json")

    async def wait(self, request_id, *, timeout):
        return await self._work.wait(request_id, timeout=timeout)

    async def aclose(self, *, timeout=30):
        await self._work.aclose(timeout=timeout)
