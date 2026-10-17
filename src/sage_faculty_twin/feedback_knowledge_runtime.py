"""Feedback admission and knowledge effects composed on SAGE durable work.

The service owner supplies an authorized, source-only candidate selection.
This adapter has no model, web-fetch, notification or conversation-reading port.
It borrows canonical state and must drain before its destinations are closed.
"""

from sage.runtime.work_journal import WorkJournal
from sage.runtime.work_service import WorkService

from .models import (
    ChatFeedbackOperationResponse, ChatFeedbackResponse,
    KnowledgeDocumentCreate, KnowledgeWriteBackResult,
)


class FeedbackKnowledgeRuntime:
    def __init__(self, *, analytics, knowledge):
        self._analytics = analytics
        self.journal = WorkJournal(analytics.state_store, namespace="twin-feedback-knowledge-v1")

        def write_source(lease):
            candidate = KnowledgeDocumentCreate.model_validate(lease.payload)
            stored, created = knowledge.upsert_document(candidate, preserve_existing=True)
            return KnowledgeWriteBackResult(
                document_id=stored.document_id, title=stored.title,
                source_name=stored.source_name, created=created,
            ).model_dump(mode="json")

        self._service = WorkService(
            self.journal, write_source, max_workers=1, poll_interval=0.05)

    def submit(self, *, operation_id, candidates, **feedback):
        """Atomically save feedback and immutable per-source work intents.

        Completed tasks are not repeated. A lost destination acknowledgement
        can repeat an upsert; preserve_existing protects curator edits. The
        result's created flag describes the acknowledged attempt, not a claim
        of exactly-once execution across the two destination stores.
        """
        tasks = {}
        for candidate in candidates:
            payload = KnowledgeDocumentCreate.model_validate(candidate)
            if not payload.source_name or payload.source_name in tasks:
                raise ValueError("feedback_sources_require_unique_identity")
            tasks[payload.source_name] = payload.model_dump(mode="json")
        return self._analytics.submit_feedback(
            **feedback, operation_id=operation_id,
            commit_work=lambda mutations: self._service.commit(
                operation_id, result={"accepted": True}, tasks=tasks, mutations=mutations),
        )

    def start(self):
        self._service.start()

    def status(self, operation_id, *, exchange_id):
        """Read saved progress; callers must authorize the exchange first.

        Do not expose task payloads, leases or internal error text. A receipt
        from another exchange is indistinguishable from a missing operation.
        Reading never starts work or retries a producer.
        """
        feedback = self._analytics.get_operation_feedback(operation_id)
        if feedback is None or feedback.exchange_id != exchange_id:
            return None
        record = self.journal.read(operation_id)
        if record is None:
            return None
        completed = all(task["status"] == "done" for task in record["tasks"].values())
        results = [KnowledgeWriteBackResult.model_validate(task["result"])
                   for task in record["tasks"].values() if task["status"] == "done"]
        return ChatFeedbackOperationResponse(
            operation_id=operation_id, status="completed" if completed else "pending",
            feedback=ChatFeedbackResponse(
                operation_id=operation_id, **feedback.to_dict(), knowledge_write_backs=results),
        )

    def wait(self, operation_id, *, timeout):
        record = self._service.wait(operation_id, timeout=timeout)
        return [KnowledgeWriteBackResult.model_validate(task["result"])
                for task in record["tasks"].values()]

    def close(self, *, timeout=30):
        """Drain active effects; queued work remains durable for recovery."""
        self._service.close(timeout=timeout)

    def retire(self, on_drained):
        self._service.retire(on_drained)
