"""Twin document schema over SageVDB canonical storage and SAGE task admission."""
from __future__ import annotations

import hashlib
import json
import weakref
from uuid import uuid4

from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime
from sage.runtime.work_journal import WorkJournal
from sagevdb import VersionedDocumentCatalog

from .models import KnowledgeDocumentRecord


class KnowledgeCatalog:
    def __init__(self, path, *, namespace):
        self.path = path
        path.mkdir(parents=True, exist_ok=True)
        # Keep canonical state outside the legacy JSON/rebuildable-index directory.
        self.state_path = path.parent / f".{path.name}.knowledge-state.sqlite3"
        self.state = StatefulProcessRuntime(backend="sqlite", sqlite_path=str(self.state_path))
        stat = self.state_path.stat()
        self._storage_identity = (stat.st_dev, stat.st_ino)
        self._finalizer = weakref.finalize(self, self.state.close)
        self.catalog = VersionedDocumentCatalog(self.state, namespace=namespace)
        self.journal = WorkJournal(self.state, namespace=f"knowledge-index:{namespace}")
        self._imports_slot = ("twin-knowledge-import-v1", namespace, "files")

    def close(self):
        self._finalizer()

    def snapshot(self):
        self._assert_storage()
        saved = self.catalog.snapshot()
        return saved["revision"], {
            key: KnowledgeDocumentRecord.model_validate(item["document"])
            for key, item in saved["records"].items() if item["document"] is not None
        }

    def _assert_storage(self):
        try:
            stat = self.state_path.stat()
        except FileNotFoundError as exc:
            raise RuntimeError("canonical_knowledge_storage_removed") from exc
        if (stat.st_dev, stat.st_ino) != self._storage_identity:
            raise RuntimeError("canonical_knowledge_storage_replaced_restart_required")

    def commit(self, changes, *, expected_revision, extra_mutations=None):
        self._assert_storage()
        self.path.mkdir(parents=True, exist_ok=True)
        operation_id = str(uuid4())
        slot, transform = self.catalog.prepare(
            operation_id,
            {key: record.model_dump(mode="json") if record is not None else None
             for key, record in changes.items()}, expected_revision=expected_revision)
        mutations = dict(extra_mutations or {})
        mutations[slot] = transform
        self.journal.commit(operation_id, result={"revision": expected_revision + 1},
                            tasks={"knowledge_index": {"namespace": self.catalog.namespace,
                                                       "revision": expected_revision + 1}},
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
