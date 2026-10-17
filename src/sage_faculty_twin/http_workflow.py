"""Owned HTTP ingress ports over SAGE's existing durable work service.

The host resolves current authority without persisting bearer credentials.
Observers do not own producers; disconnecting one waiter cannot cancel another.
Canonical answer recovery must precede retry after an acknowledgement loss.
"""

import asyncio
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator
from sage.runtime.async_work_service import AsyncWorkService
from sage.runtime.work_dispatcher import WorkFailure
from sage.runtime.work_journal import WorkJournal
from sage.runtime.work_service import WorkFailedError
from sage.runtime.event_journal import EventJournal

from .models import ChatRequest, ChatResponse
from .http_authority import HttpAuthority
from .post_answer_contract import DeliveryIntent


class HttpWorkRejected(RuntimeError):
    """Public failure classification; never persist exception text or secrets."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


def public_http_failure(code):
    """One safe public contract shared by HTTP errors and durable SSE errors."""
    return {
        "prompt_capacity": (413, "完整材料超出模型上下文容量；未截断原文，请调整问题或材料后重试。"),
        "request_cancelled": (499, "任务已取消，未继续生成回答。"),
        "authority_denied": (403, "当前身份或权限已失效，请重新验证。"),
        "invalid_request": (400, "请求内容无效，请检查后重试。"),
    }.get(code, (503, "本轮回答未能完整生成或通过校验，请稍后重试。"))


class HttpIngress(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    request_id: str = Field(min_length=1, max_length=128)
    request: ChatRequest
    authority: HttpAuthority

    @model_validator(mode="after")
    def owned_request(self):
        if not self.request.owner_id:
            raise ValueError("http_ingress_requires_verified_owner")
        if not self.request.owner_id.startswith(self.authority.kind + ":"):
            raise ValueError("http_ingress_authority_owner_mismatch")
        if self.request.slack_channel_context is not None:
            raise ValueError("http_ingress_cannot_capture_slack_context")
        return self

    @property
    def delivery(self):
        return DeliveryIntent(owner_id=self.request.owner_id, request_id=self.request_id)


class HttpWorkflow:
    """Application ports only; SAGE owns storage, leases, workers and retries.

    authorize(ingress) must return exactly True from fresh authority, including
    the captured access scope. recover/answer return validated ChatResponse.
    answer(ingress, admission_guard=...) must commit through ingress.delivery
    with that guard in the same state transaction before acknowledging success.
    Borrowed state and ports must remain alive until aclose drains successfully.
    """

    def __init__(self, state, *, authorize, recover, answer, max_attempts=5, **options):
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("http_max_attempts_must_be_positive")
        self.journal = WorkJournal(state, namespace="twin-http-workflow-v1")
        self.events = EventJournal(state, namespace="twin-workflow-events-v1")
        self._authorize, self._recover, self._answer = authorize, recover, answer
        self._max_attempts = max_attempts
        self._retry_delay = options.get("retry_delay", 5)
        self._work = AsyncWorkService(self.journal, self._handle,
            **{"max_workers": 2, "poll_interval": 0.05,
               "failure_policy": self._failure, "completion_mutations": self._completion_events, **options})
        self._start_lock = asyncio.Lock()
        self._started = False

    def _completion_events(self, lease, result):
        if lease.task_id != "finalize" or not result["failed"]:
            return {}
        ingress = HttpIngress.model_validate(lease.payload)
        status, message = public_http_failure(result["code"])
        terminal = self.events.prepare_append(ingress.delivery.stream_id, event_id="terminal", terminal=True,
            payload={"type": "error", "code": result["code"], "status": status, "message": message})

        def close(old):
            # A canonical answer's post-answer worker owns its completion.
            # Check inside the fenced transaction, not in a prior snapshot.
            if old and any(event["id"] == "answer_done" for event in old["events"]):
                return old
            return terminal.transform(old)

        return {terminal.slot: close}

    def _failure(self, lease, error):
        if isinstance(error, HttpWorkRejected):
            return WorkFailure(error.code)
        if isinstance(error, PermissionError):
            return WorkFailure("authority_denied")
        if isinstance(error, ValueError):
            return WorkFailure("invalid_request")
        if lease.attempt >= self._max_attempts:
            return WorkFailure("retry_exhausted")
        return WorkFailure("temporary_failure", min(60, self._retry_delay * 2 ** min(lease.attempt - 1, 10)))

    async def _check(self, ingress):
        if await self._authorize(ingress) is not True:
            raise PermissionError("http_work_authority_denied")

    async def _handle(self, lease):
        ingress = HttpIngress.model_validate(lease.payload)
        if lease.request_id != ingress.delivery.stream_id:
            raise ValueError("http_work_identity_conflict")
        if lease.task_id == "finalize":
            record = await asyncio.to_thread(self.journal.read, lease.request_id)
            task = record["tasks"]["answer"]
            return {"failed": task["status"] in {"failed", "blocked"},
                    "code": task.get("failure_code", "unknown_failure")}
        if lease.task_id != "answer":
            raise ValueError("unknown_http_work_kind")
        await self._check(ingress)
        response = await self._recover(ingress)
        if response is None:
            try:
                response = await self._answer(ingress, admission_guard=self.journal.prepare_guard(lease))
            except Exception:
                response = await self._recover(ingress)
                if response is None:
                    raise
        return ChatResponse.model_validate(response).model_dump(mode="json")

    async def start(self):
        async with self._start_lock:
            if not self._started:
                await asyncio.to_thread(self._upgrade_finalizers)
                await self._work.start()
                self._started = True

    def _upgrade_finalizers(self):
        # Offline graph migration uses SAGE's guarded native transition. Old
        # workers must be drained; a live legacy lease rejects this upgrade.
        cursor = 0
        while True:
            rows, cursor = self.journal.records(cursor=cursor, limit=100)
            for row in rows:
                if set(row["value"]["tasks"]) == {"answer"}:
                    self.journal.extend_tasks(row["key"], tasks={"finalize": row["value"]["tasks"]["answer"]["payload"]},
                                              dependencies={"finalize": ["answer"]}, settled=["finalize"])
            if cursor is None:
                return

    async def admit(self, ingress):
        # Detach nested request data before the first await.
        ingress = HttpIngress.model_validate_json(ingress.model_dump_json())
        await self._check(ingress)
        await self.start()
        return await self._work.commit(ingress.delivery.stream_id,
            result={"owner_id": ingress.request.owner_id},
            tasks={"answer": ingress.model_dump(mode="json"), "finalize": ingress.model_dump(mode="json")},
            dependencies={"finalize": ["answer"]}, settled=["finalize"])

    async def wait(self, ingress, *, timeout, start_timeout=None):
        ingress = HttpIngress.model_validate_json(ingress.model_dump_json())
        await self._check(ingress)
        record = await asyncio.to_thread(self.journal.read, ingress.delivery.stream_id)
        if record is None or record["tasks"]["answer"]["payload"] != ingress.model_dump(mode="json"):
            raise ValueError("http_work_input_conflict")
        try:
            record = await self._work.wait(ingress.delivery.stream_id, timeout=timeout, start_timeout=start_timeout)
        except WorkFailedError as exc:
            await self._check(ingress)
            record = await asyncio.to_thread(self.journal.read, ingress.delivery.stream_id)
            raise HttpWorkRejected(record["tasks"]["answer"].get("failure_code", "unknown_failure")) from exc
        await self._check(ingress)
        return ChatResponse.model_validate(record["tasks"]["answer"]["result"])

    async def aclose(self):
        await self._work.aclose()
