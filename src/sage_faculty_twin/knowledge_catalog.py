"""Twin document schema over SageVDB canonical storage and SAGE task admission."""
from __future__ import annotations

import hashlib
import json
import threading
import weakref
from uuid import uuid4

from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage.runtime.work_journal import WorkJournal
from sage.runtime.work_service import WorkService
from sagevdb import VersionedDocumentCatalog

from .models import KnowledgeDocumentRecord


def _retire_resources(state, services):
    if services:
        # A finalizer may run inside the last active projector. SAGE retires
        # without joining that same task, then closes state after its ack.
        services[0].retire(state.close)
    else:
        state.close()


class KnowledgeCatalog:
    def __init__(self, path, *, namespace):
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        # Keep canonical state outside the legacy JSON/rebuildable-index directory.
        self.state_path = path.parent / f".{path.name}.knowledge-state.sqlite3"
        self.state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(self.state_path))
        stat = self.state_path.stat()
        self._storage_identity = (stat.st_dev, stat.st_ino)
        self._services = []
        self._finalizer = weakref.finalize(self, _retire_resources, self.state, self._services)
        self._lifecycle_lock = threading.RLock()
        self._index_service = None
        self._projection_key = None
        self._closed = False
        self._closing = False
        self.catalog = VersionedDocumentCatalog(self.state, namespace=namespace)
        self.journal = WorkJournal(self.state, namespace=f"knowledge-index:{namespace}")
        self._imports_slot = ("twin-knowledge-import-v1", namespace, "files")

    def bind_projection(self, projection_key):
        """Bind admission identity without starting a batch consumer."""
        with self._lifecycle_lock:
            self._assert_open()
            if projection_key is not None and (
                    not isinstance(projection_key, str) or not projection_key
                    or projection_key != projection_key.strip()):
                raise ValueError("invalid_knowledge_projection_key")
            if ((self._index_service is not None or self._projection_key is not None)
                    and projection_key != self._projection_key):
                raise RuntimeError("knowledge_projection_changed_restart_required")
            self._projection_key = projection_key

    def start_indexing(self, project, *, projection_key=None):
        """Recover admitted intents using SAGE's bounded worker lifecycle.

        project receives the minimum required revision and returns the actual
        committed projection revision. It must read current canonical data,
        never reconstruct documents from the task payload.
        """
        with self._lifecycle_lock:
            self.bind_projection(projection_key)
            if self._index_service is None:
                namespace = self.catalog.namespace

                def accepts(task_id, payload):
                    return (isinstance(payload, dict)
                            and payload.get("projection_key") in (None, projection_key))

                def handle(lease):
                    payload = lease.payload
                    if (lease.task_id != "knowledge_index" or not isinstance(payload, dict)
                            or payload.get("namespace") != namespace
                            or not accepts(lease.task_id, payload)):
                        raise ValueError("invalid_knowledge_index_task")
                    required = payload.get("revision")
                    if type(required) is not int or required < 0:
                        raise ValueError("invalid_knowledge_index_revision")
                    actual = project(required)
                    if type(actual) is not int or actual < required:
                        raise ValueError("knowledge_index_revision_not_reached")
                    result = {"namespace": namespace, "revision": actual}
                    if projection_key is not None:
                        result["projection_key"] = projection_key
                    return result

                self._index_service = WorkService(
                    self.journal, handle, max_workers=1, poll_interval=0.05,
                    accept_task=accepts,
                )
                self._services.append(self._index_service)
            self._index_service.start()

    def wait_indexing(self, operation_id, *, timeout):
        if self._index_service is None:
            raise RuntimeError("knowledge_index_service_not_started")
        return self._index_service.wait(operation_id, timeout=timeout)

    def request_indexing(self, revision, *, projection_key):
        """Admit a refresh intent without editing canonical documents."""
        with self._lifecycle_lock:
            self._assert_open()
            if (not isinstance(projection_key, str) or not projection_key
                    or projection_key != projection_key.strip()):
                raise ValueError("invalid_knowledge_projection_key")
            if type(revision) is not int or revision < 0:
                raise ValueError("invalid_knowledge_index_revision")
            if revision > self.snapshot()[0]:
                raise ValueError("knowledge_index_revision_not_committed")
            request_id = str(uuid4())
            admission = self._index_service or self.journal
            admission.commit(request_id, result={"revision": revision}, tasks={
                "knowledge_index": {"namespace": self.catalog.namespace,
                                    "revision": revision, "projection_key": projection_key}})
            return request_id

    def close(self, *, timeout=30):
        # Never hold the lifecycle lock while draining: an active projector may
        # need canonical reads. On timeout keep state open and allow close retry.
        with self._lifecycle_lock:
            if self._closed:
                return
            self._closing = True
            service = self._index_service
        if service is not None:
            service.close(timeout=timeout)
        with self._lifecycle_lock:
            self._finalizer()
            self._closed = True

    def retire(self):
        """Failed startup can stop admission without joining an active task."""
        with self._lifecycle_lock:
            self._closing = True
            self._finalizer()

    def _assert_open(self):
        if self._closing or self._closed:
            raise RuntimeError("knowledge_catalog_closing")

    def snapshot(self):
        self._assert_storage()
        saved = self.catalog.snapshot()
        return saved["revision"], {
            key: KnowledgeDocumentRecord.model_validate(item["document"])
            for key, item in saved["records"].items() if item["document"] is not None
        }

    def _assert_storage(self):
        if self._closed:
            raise RuntimeError("knowledge_catalog_closed")
        try:
            stat = self.state_path.stat()
        except FileNotFoundError as exc:
            raise RuntimeError("canonical_knowledge_storage_removed") from exc
        if (stat.st_dev, stat.st_ino) != self._storage_identity:
            raise RuntimeError("canonical_knowledge_storage_replaced_restart_required")

    def commit(self, changes, *, expected_revision, extra_mutations=None):
        with self._lifecycle_lock:
            self._assert_open()
            return self._commit(changes, expected_revision=expected_revision,
                                extra_mutations=extra_mutations)

    def _commit(self, changes, *, expected_revision, extra_mutations=None):
        self._assert_storage()
        self.path.mkdir(parents=True, exist_ok=True)
        operation_id = str(uuid4())
        slot, transform = self.catalog.prepare(
            operation_id,
            {key: record.model_dump(mode="json") if record is not None else None
             for key, record in changes.items()}, expected_revision=expected_revision)
        mutations = dict(extra_mutations or {})
        mutations[slot] = transform
        admission = self._index_service or self.journal
        payload = {"namespace": self.catalog.namespace, "revision": expected_revision + 1}
        if self._projection_key is not None:
            payload["projection_key"] = self._projection_key
        admission.commit(operation_id, result={"revision": expected_revision + 1},
                            tasks={"knowledge_index": payload},
                            mutations=mutations)
        return operation_id

    def import_legacy(self, decode):
        """Import with same-transaction receipts, then archive original bytes."""
        files = [(path, path.read_bytes()) for path in sorted(self.path.glob("*.json"))]
        if not files:
            return
        receipts = self.state.peek(self._imports_slot) or {}
        revision, existing = self.snapshot()
        changes, new_receipts = {}, {}
        for path, raw in files:
            fingerprint = hashlib.sha256(raw).hexdigest()
            if path.name in receipts:
                if receipts[path.name] != fingerprint:
                    raise ValueError("knowledge_legacy_source_changed")
                continue
            record = decode(json.loads(raw))
            if record.document_id in existing or record.document_id in changes:
                raise ValueError("knowledge_legacy_identity_conflict")
            changes[record.document_id] = record
            new_receipts[path.name] = fingerprint
        if changes:
            def save_receipts(value):
                saved = dict(value or {})
                for name, fingerprint in new_receipts.items():
                    if name in saved and saved[name] != fingerprint:
                        raise ValueError("knowledge_legacy_source_changed")
                    saved[name] = fingerprint
                return saved
            self.commit(changes, expected_revision=revision,
                        extra_mutations={self._imports_slot: save_receipts})
        archive = self.path / "legacy-imported"
        archive.mkdir(exist_ok=True)
        for path, raw in files:
            target = archive / path.name
            if target.exists() and target.read_bytes() != raw:
                raise ValueError("knowledge_legacy_archive_conflict")
            try:
                if path.read_bytes() != raw:
                    raise ValueError("knowledge_legacy_source_changed")
                path.replace(target)
            except FileNotFoundError:
                if not target.exists() or target.read_bytes() != raw:
                    raise
