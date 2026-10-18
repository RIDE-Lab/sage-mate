"""Compose SAGE-owned persistence/lifecycle with Twin's post-answer handler."""

from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage.runtime.work_journal import WorkJournal
from sage.runtime.work_service import WorkService
from sage.runtime.event_journal import EventJournal
from sage.runtime.work_observation import WorkEventDestination, WorkObserver

from .post_answer_handler import PostAnswerHandler
from .memory_store import ConversationMemoryRecord
from .post_answer_contract import PostAnswerTask
from .chat_delivery import ChatDeliveryGate
from .workflow_events import WorkflowEventBroker


def work_events_for_exchange(state, exchange_id, *, owner_id):
    return work_evidence_for_exchange(state, exchange_id, owner_id=owner_id)["work_events"]


def work_evidence_for_exchange(state, exchange_id, *, owner_id):
    """Read the committed task's owner-bound delivery, never guess it by email."""
    journal = WorkJournal(state, namespace="twin-post-answer-v1")
    record = journal.read(exchange_id)
    if record is None:
        return {"request_id": None, "work_events": [], "work_status": None,
                "stream_closed": None}  # No invented legacy evidence.
    task = PostAnswerTask.model_validate(record["tasks"]["post_answer"]["payload"])
    if task.request.owner_id != owner_id or task.request_id != exchange_id:
        raise PermissionError("post_answer_observation_owner_conflict")
    if task.delivery is None:
        return {"request_id": None, "work_events": [], "work_status": journal.status(exchange_id),
                "stream_closed": None}
    return {"request_id": task.delivery.request_id,
            **WorkflowEventBroker(lambda: state).read_work_evidence(task.delivery.stream_id),
            "work_status": journal.status(exchange_id)}


class PostAnswerRuntime:
    def __init__(self, path=None, *, state=None, consolidate_profiles, score,
                 create_artifact_draft=None, refresh_digest=None):
        if (path is None) == (state is None):
            raise ValueError("work_runtime_requires_exactly_one_state_source")
        self._owns_state = state is None
        self._state = state if state is not None else StatefulProcessRuntime(
            backend="sqlite", sqlite_path=str(path))
        self.journal = WorkJournal(self._state, namespace="twin-post-answer-v1")
        self.events = EventJournal(self._state, namespace="twin-workflow-events-v1")
        events = self.events  # The service-lifetime port must not retain its owner.

        def destination(lease):
            if lease.task_id != "post_answer":
                return None  # Manual digest jobs have no answer-delivery stream.
            task = PostAnswerTask.model_validate(lease.payload)
            return (WorkEventDestination(events, task.delivery.stream_id)
                    if task.delivery is not None else None)

        self._service = WorkService(
            self.journal, PostAnswerHandler(consolidate_profiles=consolidate_profiles, score=score,
                                          create_artifact_draft=create_artifact_draft,
                                          refresh_digest=refresh_digest),
            max_workers=2, poll_interval=0.05,
            completion_mutations=self._completion_events,
            observer=WorkObserver(self.journal, destination),
        )

    def start(self):
        self._service.start()

    def work_status(self):
        return self._service.status()

    def submit(self, task):
        inputs = task.journal_inputs()
        payload = inputs["tasks"].get("post_answer")
        return self._service.commit(task.request_id, **inputs,
            mutations=self._answer_event(payload) if payload is not None else None)

    def _answer_event(self, payload):
        task = PostAnswerTask.model_validate(payload)
        if task.delivery is None:
            return {}
        response = ChatDeliveryGate().deliver(response=task.response, original_question=task.request.question)
        if response.answer != task.response.answer:
            raise ValueError("delivery_answer_must_be_normalized_before_commit")
        prepared = self.events.prepare_append(task.delivery.stream_id, event_id="answer_done",
            payload={"type": "answer_done", "response": response.model_dump(mode="json")})
        return {prepared.slot: prepared.transform}

    def _completion_events(self, lease, result):
        if lease.task_id != "post_answer":
            return {}
        task = PostAnswerTask.model_validate(lease.payload)
        if task.delivery is None:
            return {}
        # The handler may enrich projections, but may not replace the committed
        # answer. All completion events share the fenced acknowledgement.
        response = result["response"]
        if response["answer"] != task.response.answer:
            raise ValueError("post_answer_cannot_replace_committed_answer")
        initial = task.response.model_dump(mode="json")["workflow_trace"]
        mutations = []
        for index, step in enumerate(response["workflow_trace"]):
            if step not in initial:
                mutations.append(self.events.prepare_append(task.delivery.stream_id,
                    event_id=f"post_answer:{index}:{step['key']}", payload={"type": "trace-step", "step": step}))
        mutations.append(self.events.prepare_append(task.delivery.stream_id, event_id="terminal",
                         terminal=True, payload={"type": "complete"}))

        def apply(old):
            for prepared in mutations:
                old = prepared.transform(old)
            return old

        return {mutations[0].slot: apply}

    def submit_turn(self, task, memory, *, guard=None):
        """Atomically admit canonical memory and intent on their shared store."""
        if self._state is not memory.state_store:
            raise ValueError("turn_and_work_must_share_state_store")
        inputs = task.journal_inputs()
        payload = inputs["tasks"]["post_answer"]
        request_id = payload["request_id"]
        delivery_mutations = self._answer_event(payload)
        record = (ConversationMemoryRecord.from_dict(payload["record"])
                  if payload["record"] is not None else None)
        if record is None:
            self._service.commit(request_id, **inputs, mutations=delivery_mutations, guard=guard)
            return None
        return memory.persist_exchange(record, commit_work=lambda mutations:
            self._service.commit(request_id, **inputs, mutations={**mutations, **delivery_mutations}, guard=guard))

    def wait(self, request_id, *, timeout):
        record = self._service.wait(request_id, timeout=timeout)
        return record["tasks"]["post_answer"]["result"]

    def wait_digest(self, request_id, *, timeout):
        record = self._service.wait(request_id, timeout=timeout)
        return record["tasks"]["context_digest"]["result"]

    def drain(self):
        """Stop admissions and finish active work, keeping results readable."""
        self._service.close()

    def close(self):
        self.drain()
        if self._owns_state:
            self._state.close()
