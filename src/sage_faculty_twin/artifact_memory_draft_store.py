from __future__ import annotations

import json
import hashlib
import weakref
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from sage.runtime.flownet.runtime.operator_runtime.stateful import StatefulProcessRuntime

from .config import AppSettings
from .models import ArtifactMemoryDraftRecordResponse


@dataclass(slots=True)
class ArtifactMemoryDraftRecord:
    draft_id: str
    conversation_id: str
    source_memory_id: str | None
    student_name: str
    student_email: str | None
    interaction_domain: str | None
    question: str
    answer: str
    artifact_names: list[str]
    artifact_sources: list[str]
    artifact_excerpt_count: int
    provenance_note: str
    retention_label: str
    status: str
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "draft_id": self.draft_id,
            "conversation_id": self.conversation_id,
            "source_memory_id": self.source_memory_id,
            "student_name": self.student_name,
            "student_email": self.student_email,
            "interaction_domain": self.interaction_domain,
            "question": self.question,
            "answer": self.answer,
            "artifact_names": list(self.artifact_names),
            "artifact_sources": list(self.artifact_sources),
            "artifact_excerpt_count": self.artifact_excerpt_count,
            "provenance_note": self.provenance_note,
            "retention_label": self.retention_label,
            "status": self.status,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    def to_response(self) -> ArtifactMemoryDraftRecordResponse:
        return ArtifactMemoryDraftRecordResponse(
            draft_id=self.draft_id,
            conversation_id=self.conversation_id,
            source_memory_id=self.source_memory_id,
            student_name=self.student_name,
            student_email=self.student_email,
            interaction_domain=self.interaction_domain,
            question=self.question,
            answer=self.answer,
            artifact_names=list(self.artifact_names),
            artifact_sources=list(self.artifact_sources),
            artifact_excerpt_count=self.artifact_excerpt_count,
            provenance_note=self.provenance_note,
            retention_label=self.retention_label,
            status=self.status,
            created_at=self.created_at,
            updated_at=self.updated_at,
        )

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> ArtifactMemoryDraftRecord:
        return cls(
            draft_id=str(payload["draft_id"]),
            conversation_id=str(payload["conversation_id"]),
            source_memory_id=(
                str(payload["source_memory_id"]) if payload.get("source_memory_id") else None
            ),
            student_name=str(payload["student_name"]),
            student_email=(str(payload["student_email"]) if payload.get("student_email") else None),
            interaction_domain=(
                str(payload["interaction_domain"]) if payload.get("interaction_domain") else None
            ),
            question=str(payload["question"]),
            answer=str(payload["answer"]),
            artifact_names=[str(item) for item in payload.get("artifact_names", [])],
            artifact_sources=[str(item) for item in payload.get("artifact_sources", [])],
            artifact_excerpt_count=int(payload.get("artifact_excerpt_count", 0)),
            provenance_note=str(payload.get("provenance_note") or ""),
            retention_label=str(payload.get("retention_label") or "project_followup"),
            status=str(payload.get("status") or "draft"),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            updated_at=datetime.fromisoformat(str(payload["updated_at"])),
        )


class ArtifactMemoryDraftStore:
    def __init__(self, settings: AppSettings) -> None:
        self._path = settings.artifact_memory_draft_dir
        self._path.mkdir(parents=True, exist_ok=True)
        self._state = StatefulProcessRuntime(
            backend="sqlite", sqlite_path=str(settings.conversation_memory_dir / "neural_state.sqlite3")
        )
        self._slot = ("twin-artifact-drafts-v1", "artifact-drafts", "records")
        self._finalizer = weakref.finalize(self, self._state.close)
        try:
            self._load_from_disk()
        except BaseException:
            self._finalizer()
            raise

    @staticmethod
    def _collection(value):
        if value is None:
            return {"version": 1, "records": {}, "sources": {}, "imports": {}}
        if value.get("version") != 1:
            raise ValueError("unsupported_artifact_draft_state")
        return value

    @staticmethod
    def _fingerprint(payload):
        identity = {key: value for key, value in payload.items()
                    if key not in {"draft_id", "status", "created_at", "updated_at"}}
        return hashlib.sha256(json.dumps(identity, sort_keys=True, ensure_ascii=False,
                                        allow_nan=False).encode()).hexdigest()

    @classmethod
    def _insert(cls, state, payload):
        source = payload["source_memory_id"]
        fingerprint = cls._fingerprint(payload)
        if source is not None and source in state["sources"]:
            receipt = state["sources"][source]
            if receipt["fingerprint"] != fingerprint:
                raise ValueError("artifact_source_identity_conflict")
            return receipt["draft_id"]
        draft_id = payload["draft_id"]
        if draft_id in state["records"]:
            raise ValueError("artifact_draft_identity_conflict")
        state["records"][draft_id] = payload
        if source is not None:
            state["sources"][source] = {"draft_id": draft_id, "fingerprint": fingerprint}
        return draft_id

    def create_draft(
        self,
        *,
        conversation_id: str,
        source_memory_id: str | None,
        student_name: str,
        student_email: str | None,
        interaction_domain: str | None,
        question: str,
        answer: str,
        artifact_names: list[str],
        artifact_sources: list[str],
        artifact_excerpt_count: int,
        provenance_note: str,
        retention_label: str = "project_followup",
    ) -> ArtifactMemoryDraftRecord:
        now = datetime.now(UTC)
        record = ArtifactMemoryDraftRecord(
            draft_id=(str(uuid5(NAMESPACE_URL, f"twin-artifact:{source_memory_id}"))
                      if source_memory_id else str(uuid4())),
            conversation_id=conversation_id,
            source_memory_id=source_memory_id,
            student_name=student_name,
            student_email=student_email,
            interaction_domain=interaction_domain,
            question=question,
            answer=answer,
            artifact_names=list(artifact_names),
            artifact_sources=list(artifact_sources),
            artifact_excerpt_count=artifact_excerpt_count,
            provenance_note=provenance_note,
            retention_label=retention_label,
            status="draft",
            created_at=now,
            updated_at=now,
        )
        if source_memory_id is not None and (not source_memory_id or source_memory_id != source_memory_id.strip()):
            raise ValueError("invalid_artifact_source_identity")
        payload = record.to_dict()

        def apply(value):
            state = self._collection(value)
            self._insert(state, payload)
            return state

        committed = self._state.update(self._slot, apply)
        chosen = committed["sources"][source_memory_id]["draft_id"] if source_memory_id is not None else record.draft_id
        return ArtifactMemoryDraftRecord.from_dict(committed["records"][chosen])

    def list_drafts(self) -> list[ArtifactMemoryDraftRecord]:
        state = self._collection(self._state.peek(self._slot))
        records = [ArtifactMemoryDraftRecord.from_dict(value) for value in state["records"].values()]
        return sorted(records, key=lambda item: item.updated_at, reverse=True)

    def get_draft(self, draft_id: str) -> ArtifactMemoryDraftRecord | None:
        state = self._collection(self._state.peek(self._slot))
        value = state["records"].get(draft_id)
        return ArtifactMemoryDraftRecord.from_dict(value) if value is not None else None

    def mark_accepted(self, draft_id: str) -> ArtifactMemoryDraftRecord:
        return self._mark_status(draft_id, status="accepted")

    def mark_rejected(self, draft_id: str) -> ArtifactMemoryDraftRecord:
        return self._mark_status(draft_id, status="rejected")

    def count_drafts(self) -> int:
        return len(self._collection(self._state.peek(self._slot))["records"])

    def _mark_status(self, draft_id: str, *, status: str) -> ArtifactMemoryDraftRecord:
        def apply(value):
            state = self._collection(value)
            record = state["records"].get(draft_id)
            if record is None:
                raise KeyError(draft_id)
            if record["status"] == status:
                return state
            if record["status"] != "draft":
                raise ValueError(record["status"])
            record.update(status=status, updated_at=datetime.now(UTC).isoformat())
            return state

        state = self._state.update(self._slot, apply)
        return ArtifactMemoryDraftRecord.from_dict(state["records"][draft_id])

    def _load_from_disk(self) -> None:
        sources = [(path, path.read_text(encoding="utf-8")) for path in sorted(self._path.glob("*.json"))]
        if not sources:
            return
        records = [(path, raw, ArtifactMemoryDraftRecord.from_dict(json.loads(raw)).to_dict())
                   for path, raw in sources]

        def migrate(value):
            state = self._collection(value)
            for path, raw, payload in records:
                fingerprint = hashlib.sha256(raw.encode()).hexdigest()
                imported = state["imports"].get(path.name)
                if imported is not None:
                    if imported != fingerprint:
                        raise ValueError("artifact_legacy_source_changed")
                    continue
                draft_id = self._insert(state, payload)
                if draft_id != payload["draft_id"]:
                    raise ValueError("duplicate_legacy_artifact_source")
                state["imports"][path.name] = fingerprint
            return state

        self._state.update(self._slot, migrate)
        archive = self._path / "legacy-imported"
        archive.mkdir(exist_ok=True)
        for path, raw, _ in records:
            target = archive / path.name
            if target.exists() and target.read_text(encoding="utf-8") != raw:
                raise ValueError("artifact_legacy_archive_conflict")
            try:
                if path.read_text(encoding="utf-8") != raw:
                    raise ValueError("artifact_legacy_source_changed")
                path.replace(target)
            except FileNotFoundError:
                if not target.exists() or target.read_text(encoding="utf-8") != raw:
                    raise
                # Another importer archived this exact committed input.
