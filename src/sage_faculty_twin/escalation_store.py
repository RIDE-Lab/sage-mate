from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from .config import AppSettings
from .models import ChatRequest, EscalationRecord


@dataclass(slots=True)
class EscalationQueueRecord:
    escalation_id: str
    conversation_id: str
    student_name: str
    student_email: str | None
    course_context: str | None
    question: str
    route: str
    status: str
    reason: str | None
    resolution_note: str | None
    created_at: datetime
    resolved_at: datetime | None

    def to_dict(self) -> dict[str, object]:
        return {
            "escalation_id": self.escalation_id,
            "conversation_id": self.conversation_id,
            "student_name": self.student_name,
            "student_email": self.student_email,
            "course_context": self.course_context,
            "question": self.question,
            "route": self.route,
            "status": self.status,
            "reason": self.reason,
            "resolution_note": self.resolution_note,
            "created_at": self.created_at.isoformat(),
            "resolved_at": self.resolved_at.isoformat() if self.resolved_at else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> EscalationQueueRecord:
        resolved_at = payload.get("resolved_at")
        return cls(
            escalation_id=str(payload["escalation_id"]),
            conversation_id=str(payload["conversation_id"]),
            student_name=str(payload["student_name"]),
            student_email=(str(payload["student_email"]) if payload.get("student_email") else None),
            course_context=(
                str(payload["course_context"]) if payload.get("course_context") else None
            ),
            question=str(payload["question"]),
            route=str(payload["route"]),
            status=str(payload["status"]),
            reason=(str(payload["reason"]) if payload.get("reason") else None),
            resolution_note=(
                str(payload["resolution_note"]) if payload.get("resolution_note") else None
            ),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            resolved_at=datetime.fromisoformat(str(resolved_at)) if resolved_at else None,
        )

    def to_response(self) -> EscalationRecord:
        return EscalationRecord(
            escalation_id=self.escalation_id,
            conversation_id=self.conversation_id,
            student_name=self.student_name,
            student_email=self.student_email,
            course_context=self.course_context,
            question=self.question,
            route=self.route,
            status=self.status,
            reason=self.reason,
            resolution_note=self.resolution_note,
            created_at=self.created_at,
            resolved_at=self.resolved_at,
        )


class EscalationQueueStore:
    def __init__(self, settings: AppSettings) -> None:
        self._path = settings.escalation_queue_dir
        self._path.mkdir(parents=True, exist_ok=True)
        self._records: dict[str, EscalationQueueRecord] = {}
        self._load_from_disk()

    def create_request(
        self,
        request: ChatRequest,
        *,
        conversation_id: str,
        route: str,
        reason: str | None = None,
    ) -> EscalationRecord:
        normalized_question = " ".join(request.question.split())
        for existing in self._records.values():
            if (
                existing.conversation_id == conversation_id
                and existing.route == route
                and existing.status == "待处理"
                and " ".join(existing.question.split()) == normalized_question
            ):
                return existing.to_response()

        record = EscalationQueueRecord(
            escalation_id=str(uuid4()),
            conversation_id=conversation_id,
            student_name=request.student_name,
            student_email=request.student_email,
            course_context=request.course_context,
            question=request.question,
            route=route,
            status="待处理",
            reason=reason.strip() if reason else None,
            resolution_note=None,
            created_at=datetime.now(UTC),
            resolved_at=None,
        )
        self._persist_record(record)
        self._records[record.escalation_id] = record
        return record.to_response()

    def list_requests(
        self,
        *,
        status: str | None = None,
        route: str | None = None,
    ) -> list[EscalationRecord]:
        normalized_status = status.strip() if status else None
        normalized_route = route.strip() if route else None
        records = sorted(self._records.values(), key=lambda item: item.created_at, reverse=True)
        if normalized_status:
            records = [record for record in records if record.status == normalized_status]
        if normalized_route:
            records = [record for record in records if record.route == normalized_route]
        return [record.to_response() for record in records]

    def resolve_request(
        self, escalation_id: str, resolution_note: str | None = None
    ) -> EscalationRecord:
        record = self._records.get(escalation_id)
        if record is None:
            raise KeyError(escalation_id)

        record.status = "已处理"
        record.resolution_note = resolution_note.strip() if resolution_note else None
        record.resolved_at = datetime.now(UTC)
        self._persist_record(record)
        return record.to_response()

    def count_records(self) -> int:
        return len(self._records)

    def _persist_record(self, record: EscalationQueueRecord) -> None:
        # Defensive: re-create the directory in case it was wiped at runtime.
        self._path.mkdir(parents=True, exist_ok=True)
        (self._path / f"{record.escalation_id}.json").write_text(
            json.dumps(record.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_from_disk(self) -> None:
        for file_path in sorted(self._path.glob("*.json")):
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            record = EscalationQueueRecord.from_dict(payload)
            self._records[record.escalation_id] = record
