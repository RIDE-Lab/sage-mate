"""Compose SAGE-owned persistence/lifecycle with Twin's post-answer handler."""

from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage.runtime.work_journal import WorkJournal
from sage.runtime.work_service import WorkService

from .post_answer_handler import PostAnswerHandler


class PostAnswerRuntime:
    def __init__(self, path, *, consolidate_profiles, score, create_artifact_draft=None):
        self._state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(path))
        self.journal = WorkJournal(self._state, namespace="twin-post-answer-v1")
        self._service = WorkService(
            self.journal, PostAnswerHandler(consolidate_profiles=consolidate_profiles, score=score,
                                          create_artifact_draft=create_artifact_draft),
            max_workers=2, poll_interval=0.05,
        )

    def start(self):
        self._service.start()

    def submit(self, task):
        return self._service.commit(task.request_id, **task.journal_inputs())

    def wait(self, request_id, *, timeout):
        record = self._service.wait(request_id, timeout=timeout)
        return record["tasks"]["post_answer"]["result"]

    def drain(self):
        """Stop admissions and finish active work, keeping results readable."""
        self._service.close()

    def close(self):
        self.drain()
        self._state.close()
