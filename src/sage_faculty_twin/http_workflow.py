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
from sage.runtime.work_service import WorkFailedError, WorkWaitTimeoutError
from sage.runtime.effect_cancellation import CancellationService, EffectCancelled
from sage.runtime.event_journal import EventJournal
from sage.runtime.source_audit import AnswerValidationRejected, SourceAuditRejected
from sage.runtime.model_errors import ModelRequestRejected
from sage.runtime.flownet.client.runtime_client import FlowRequestOutcomeError

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
        "answer_validation_rejected": (503, "本轮回答未通过来源或完整性校验，未发布未经校验的答案。可展开处理过程查看失败节点。"),
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

    def __init__(self, state, *, authorize, recover, answer, max_attempts=5,
                 cancellation_targets=None, cancellation_timeout=30, **options):
        if type(max_attempts) is not int or max_attempts < 1:
            raise ValueError("http_max_attempts_must_be_positive")
        self.journal = WorkJournal(state, namespace="twin-http-workflow-v1")
        self.events = EventJournal(state, namespace="twin-workflow-events-v1")
        self._authorize, self._recover, self._answer = authorize, recover, answer
        self._max_attempts = max_attempts
        self._retry_delay = options.get("retry_delay", 5)
        self._cancellation_timeout = cancellation_timeout
        self._cancellations = CancellationService(self.journal, task_id="answer",
            targets={} if cancellation_targets is None else cancellation_targets, protection=self._cancel_protection,
            poll_interval=options.get("poll_interval", 0.05))
        self._task_selector = options.pop("accept_task", None)
        if self._task_selector is not None and not callable(self._task_selector):
            raise TypeError("task_selector_must_be_callable")
        self._work = AsyncWorkService(self.journal, self._handle,
            **{"max_workers": 2, "poll_interval": 0.05,
               "failure_policy": self._failure, "completion_mutations": self._completion_events,
               "accept_task": self._accept_task, **options})
        self._start_lock = asyncio.Lock()
        self._started = False

    def _accept_task(self, task_id, payload):
        if self._task_selector is not None and not self._task_selector(task_id, payload):
            return False
        ingress = HttpIngress.model_validate(payload)
        record = self._cancellations.journal.read(ingress.delivery.stream_id)
        # Freeze retries and terminal publication while distributed cancellation
        # is pending. WorkService will discover settlement on its normal sweep.
        return record is None or all(task["status"] == "done" for task in record["tasks"].values())

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
        if isinstance(error, EffectCancelled) or (
            isinstance(error, FlowRequestOutcomeError) and error.error_type == "EffectCancelled"
        ):
            if self._cancellations.journal.read(lease.request_id) is not None:
                # The native coordinator must finish all seals before the origin
                # fails as request_cancelled and its finalizer publishes that fact.
                return WorkFailure("cancellation_pending", 0.05)
        if isinstance(error, ModelRequestRejected):
            return WorkFailure(error.code)
        if isinstance(error, FlowRequestOutcomeError) and error.error_type == "ModelRequestRejected":
            # Only allow safe codes across the serialized worker boundary.
            code = error.message
            if code not in {"prompt_capacity", "model_request_invalid", "model_access_denied"}:
                code = "model_request_invalid"
            return WorkFailure(code)
        if isinstance(error, SourceAuditRejected) or (
            isinstance(error, FlowRequestOutcomeError)
            and error.error_type == "SourceAuditRejected"
        ):
            return WorkFailure("source_audit_rejected")
        if isinstance(error, AnswerValidationRejected) or (
            isinstance(error, FlowRequestOutcomeError)
            and error.error_type in {"AnswerValidationRejected", "InvalidSourcePlan", "InvalidEvidenceCoverage", "InvalidResearchPlan"}
        ):
            return WorkFailure("answer_validation_rejected")
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
                await self._cancellations.astart()
                await self._work.start()
                self._started = True

    def work_status(self):
        return {"ingress": self._work.status(), "cancellation": self._cancellations.work_status()}

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
        payload = ingress.model_dump(mode="json")
        record = await asyncio.to_thread(self.journal.read, ingress.delivery.stream_id)
        if record is not None:
            stored = record["tasks"]["answer"]["payload"]
            if self._input_identity(stored) != self._input_identity(payload):
                raise ValueError("http_work_input_conflict")
            # Preserve the admitted bytes and journal identity across optional
            # request-field additions; never rewrite durable work on replay.
            payload = stored
        return await self._work.commit(ingress.delivery.stream_id,
            result={"owner_id": ingress.request.owner_id},
            tasks={"answer": payload, "finalize": payload},
            dependencies={"finalize": ["answer"]}, settled=["finalize"])

    @staticmethod
    def _input_identity(payload):
        # V1 requests written before source selection omit this optional field.
        # Missing and null both mean unspecified, but [] and explicit scopes
        # remain distinct. Preserve every other byte-level JSON value/field.
        request = dict(payload["request"])
        if request.get("knowledge_sources") is None:
            request.pop("knowledge_sources", None)
        return {**payload, "request": request}

    async def wait(self, ingress, *, timeout, start_timeout=None):
        ingress = HttpIngress.model_validate_json(ingress.model_dump_json())
        await self._check(ingress)
        record = await asyncio.to_thread(self.journal.read, ingress.delivery.stream_id)
        if record is None or self._input_identity(record["tasks"]["answer"]["payload"]) != self._input_identity(ingress.model_dump(mode="json")):
            raise ValueError("http_work_input_conflict")
        try:
            record = await self._work.wait(ingress.delivery.stream_id, timeout=timeout, start_timeout=start_timeout)
        except WorkFailedError as exc:
            await self._check(ingress)
            record = await asyncio.to_thread(self.journal.read, ingress.delivery.stream_id)
            raise HttpWorkRejected(record["tasks"]["answer"].get("failure_code", "unknown_failure")) from exc
        await self._check(ingress)
        from .workflow_events import WorkflowEventBroker

        response = ChatResponse.model_validate(record["tasks"]["answer"]["result"])
        events = await asyncio.to_thread(WorkflowEventBroker(lambda: self.events._store).read_work_events,
                                         ingress.delivery.stream_id)
        return response.model_copy(update={"work_events": events})

    async def aclose(self):
        await self._work.aclose()
        await self._cancellations.aclose()

    async def cancel(self, *, owner_id, request_id):
        """Cancel only a transport-verified owner's durable task.

        This port never accepts client-supplied authority. The transport must
        authenticate the current caller before providing owner_id.
        """
        delivery = DeliveryIntent(owner_id=owner_id, request_id=request_id)
        record = await asyncio.to_thread(self.journal.read, delivery.stream_id)
        if record is None:
            return "missing"
        ingress = HttpIngress.model_validate(record["tasks"]["answer"]["payload"])
        if ingress.delivery != delivery:
            raise PermissionError("http_cancel_owner_mismatch")
        admission = await self._cancellations.arequest(delivery.stream_id)
        if admission != "accepted":
            return admission
        try:
            return await self._cancellations.await_result(delivery.stream_id,
                                                         timeout=self._cancellation_timeout)
        except WorkWaitTimeoutError:
            return "cancelling"

    def _cancel_protection(self, stream_id):
        # Preparing an append does not publish it. Its native slot identifies
        # the canonical stream protected inside SAGE's cancellation transaction.
        terminal = self.events.prepare_append(stream_id, event_id="terminal",
            payload={"type": "error", "code": "request_cancelled"}, terminal=True)
        return {
            terminal.slot: lambda old: bool(old and any(event["id"] == "answer_done"
                                                       for event in old["events"]))}
