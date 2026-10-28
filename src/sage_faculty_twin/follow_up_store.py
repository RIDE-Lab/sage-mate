from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import NAMESPACE_URL, uuid4, uuid5

from .config import AppSettings
from .models import FollowUpQueueRecord


@dataclass(slots=True)
class FollowUpQueueEntry:
    action_id: str
    booking_id: str | None
    student_name: str
    student_email: str
    action_type: str
    title: str
    detail: str
    subject: str
    lines: list[str]
    status: str
    due_at: datetime | None
    created_at: datetime
    sent_at: datetime | None

    def to_dict(self) -> dict[str, object]:
        return {
            "action_id": self.action_id,
            "booking_id": self.booking_id,
            "student_name": self.student_name,
            "student_email": self.student_email,
            "action_type": self.action_type,
            "title": self.title,
            "detail": self.detail,
            "subject": self.subject,
            "lines": self.lines,
            "status": self.status,
            "due_at": self.due_at.isoformat() if self.due_at else None,
            "created_at": self.created_at.isoformat(),
            "sent_at": self.sent_at.isoformat() if self.sent_at else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> FollowUpQueueEntry:
        due_at = payload.get("due_at")
        sent_at = payload.get("sent_at")
        return cls(
            action_id=str(payload["action_id"]),
            booking_id=(str(payload["booking_id"]) if payload.get("booking_id") else None),
            student_name=str(payload["student_name"]),
            student_email=str(payload["student_email"]),
            action_type=str(payload["action_type"]),
            title=str(payload["title"]),
            detail=str(payload["detail"]),
            subject=str(payload["subject"]),
            lines=[str(item) for item in payload.get("lines", [])],
            status=str(payload["status"]),
            due_at=datetime.fromisoformat(str(due_at)) if due_at else None,
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            sent_at=datetime.fromisoformat(str(sent_at)) if sent_at else None,
        )

    def to_response(self) -> FollowUpQueueRecord:
        return FollowUpQueueRecord(
            action_id=self.action_id,
            booking_id=self.booking_id,
            student_name=self.student_name,
            student_email=self.student_email,
            action_type=self.action_type,
            title=self.title,
            detail=self.detail,
            subject=self.subject,
            status=self.status,
            due_at=self.due_at,
            created_at=self.created_at,
            sent_at=self.sent_at,
        )


class FollowUpQueueStore:
    def __init__(self, settings: AppSettings) -> None:
        self._path = settings.follow_up_queue_dir
        self._path.mkdir(parents=True, exist_ok=True)
        self._entries: dict[str, FollowUpQueueEntry] = {}
        self._load_from_disk()

    def queue_action(
        self,
        *,
        booking_id: str | None,
        student_name: str,
        student_email: str,
        action_type: str,
        title: str,
        detail: str,
        subject: str,
        lines: list[str],
        due_at: datetime | None,
    ) -> FollowUpQueueRecord:
        existing = self._find_existing(booking_id=booking_id, action_type=action_type)
        if existing is not None:
            # Replaying a booking decision must not replace its immutable mail
            # intent or turn sent/uncertain delivery back into queued work.
            return existing.to_response()
        now = datetime.now(UTC)
        entry = FollowUpQueueEntry(
            action_id=(str(uuid5(NAMESPACE_URL, json.dumps(["twin-follow-up", booking_id, action_type])))
                       if booking_id else str(uuid4())),
            booking_id=booking_id,
            student_name=student_name,
            student_email=student_email,
            action_type=action_type,
            title=title,
            detail=detail,
            subject=subject,
            lines=list(lines),
            status="queued",
            due_at=self._normalize_datetime(due_at),
            created_at=existing.created_at if existing else now,
            sent_at=None,
        )
        self._entries[entry.action_id] = entry
        self._persist_entry(entry)
        return entry.to_response()

    def list_actions(
        self,
        *,
        status: str | None = None,
        action_type: str | None = None,
    ) -> list[FollowUpQueueRecord]:
        normalized_status = status.strip() if status else None
        normalized_action_type = action_type.strip() if action_type else None
        entries = sorted(self._entries.values(), key=lambda item: item.created_at, reverse=True)
        if normalized_status:
            entries = [entry for entry in entries if entry.status == normalized_status]
        if normalized_action_type:
            entries = [entry for entry in entries if entry.action_type == normalized_action_type]
        return [entry.to_response() for entry in entries]

    def list_due_actions(self, now: datetime | None = None) -> list[FollowUpQueueEntry]:
        current = self._normalize_datetime(now) or datetime.now(UTC)
        due_entries = []
        for entry in self._entries.values():
            entry.due_at = self._normalize_datetime(entry.due_at)
            entry.created_at = self._normalize_datetime(entry.created_at) or entry.created_at
            entry.sent_at = self._normalize_datetime(entry.sent_at)
            if entry.status == "queued" and (entry.due_at is None or entry.due_at <= current):
                due_entries.append(entry)
        due_entries.sort(key=lambda item: item.created_at)
        return due_entries

    def mark_sent(self, action_id: str, sent_at: datetime | None = None) -> FollowUpQueueRecord:
        entry = self._entries[action_id]
        entry.status = "sent"
        entry.sent_at = sent_at or datetime.now(UTC)
        self._persist_entry(entry)
        return entry.to_response()

    def mark_unknown(self, action_id: str) -> FollowUpQueueRecord:
        entry = self._entries[action_id]
        entry.status = "unknown"
        self._persist_entry(entry)
        return entry.to_response()

    def count_actions(self) -> int:
        return len(self._entries)

    def _find_existing(
        self, *, booking_id: str | None, action_type: str
    ) -> FollowUpQueueEntry | None:
        if booking_id is None:
            return None
        for entry in self._entries.values():
            if entry.booking_id == booking_id and entry.action_type == action_type:
                return entry
        return None

    def _persist_entry(self, entry: FollowUpQueueEntry) -> None:
        # Defensive: re-create the directory in case it was wiped at runtime.
        self._path.mkdir(parents=True, exist_ok=True)
        (self._path / f"{entry.action_id}.json").write_text(
            json.dumps(entry.to_dict(), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def _load_from_disk(self) -> None:
        for file_path in sorted(self._path.glob("*.json")):
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            entry = FollowUpQueueEntry.from_dict(payload)
            entry.due_at = self._normalize_datetime(entry.due_at)
            entry.created_at = self._normalize_datetime(entry.created_at) or entry.created_at
            entry.sent_at = self._normalize_datetime(entry.sent_at)
            self._entries[entry.action_id] = entry

    def _normalize_datetime(self, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)
