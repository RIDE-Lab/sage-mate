"""Compose SAGE-owned persistence/lifecycle with Twin's post-answer handler."""

from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage.runtime.work_journal import WorkJournal
from sage.runtime.work_service import WorkService

from .post_answer_handler import PostAnswerHandler
from .memory_store import ConversationMemoryRecord


class PostAnswerRuntime:
    def __init__(self, path=None, *, state=None, consolidate_profiles, score,
                 create_artifact_draft=None, refresh_digest=None):
        if (path is None) == (state is None):
            raise ValueError("work_runtime_requires_exactly_one_state_source")
        self._owns_state = state is None
        self._state = state if state is not None else StatefulProcessRuntime(
            backend="sqlite", sqlite_path=str(path))
        self.journal = WorkJournal(self._state, namespace="twin-post-answer-v1")
        self._service = WorkService(
            self.journal, PostAnswerHandler(consolidate_profiles=consolidate_profiles, score=score,
                                          create_artifact_draft=create_artifact_draft,
                                          refresh_digest=refresh_digest),
            max_workers=2, poll_interval=0.05,
        )

    def start(self):
        self._service.start()

    def submit(self, task):
        return self._service.commit(task.request_id, **task.journal_inputs())

    def submit_turn(self, task, memory):
        """Atomically admit canonical memory and intent on their shared store."""
        if self._state is not memory.state_store:
            raise ValueError("turn_and_work_must_share_state_store")
        inputs = task.journal_inputs()
        payload = inputs["tasks"]["post_answer"]
        request_id = payload["request_id"]
        record = (ConversationMemoryRecord.from_dict(payload["record"])
                  if payload["record"] is not None else None)
        if record is None:
            self._service.commit(request_id, **inputs)
            return None
        return memory.persist_exchange(record, commit_work=lambda mutations:
            self._service.commit(request_id, **inputs, mutations=mutations))

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
