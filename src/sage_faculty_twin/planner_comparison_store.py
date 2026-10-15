from __future__ import annotations

import json
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import uuid4

from .config import AppSettings

_ACTIONABLE_STATUSES = {"different_steps", "different_goal", "shadow_error"}


@dataclass(slots=True)
class PlannerComparisonEntry:
    record_id: str
    conversation_id: str
    exchange_id: str | None
    workflow_action: str
    question: str
    comparison_status: str
    deterministic_goal: str
    shadow_goal: str | None
    same_goal: bool
    same_fallback_template: bool
    deterministic_only_steps: list[str]
    shadow_only_steps: list[str]
    summary: str
    created_at: datetime

    @property
    def actionable(self) -> bool:
        return self.comparison_status in _ACTIONABLE_STATUSES

    def to_dict(self) -> dict[str, object]:
        return {
            "record_id": self.record_id,
            "conversation_id": self.conversation_id,
            "exchange_id": self.exchange_id,
            "workflow_action": self.workflow_action,
            "question": self.question,
            "comparison_status": self.comparison_status,
            "deterministic_goal": self.deterministic_goal,
            "shadow_goal": self.shadow_goal,
            "same_goal": self.same_goal,
            "same_fallback_template": self.same_fallback_template,
            "deterministic_only_steps": list(self.deterministic_only_steps),
            "shadow_only_steps": list(self.shadow_only_steps),
            "summary": self.summary,
            "created_at": self.created_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> PlannerComparisonEntry:
        return cls(
            record_id=str(payload["record_id"]),
            conversation_id=str(payload["conversation_id"]),
            exchange_id=(str(payload["exchange_id"]) if payload.get("exchange_id") else None),
            workflow_action=str(payload.get("workflow_action") or "answer"),
            question=str(payload.get("question") or ""),
            comparison_status=str(payload.get("comparison_status") or "shadow_disabled"),
            deterministic_goal=str(payload.get("deterministic_goal") or ""),
            shadow_goal=(str(payload["shadow_goal"]) if payload.get("shadow_goal") else None),
            same_goal=bool(payload.get("same_goal", True)),
            same_fallback_template=bool(payload.get("same_fallback_template", True)),
            deterministic_only_steps=[
                str(item) for item in payload.get("deterministic_only_steps", [])
            ],
            shadow_only_steps=[str(item) for item in payload.get("shadow_only_steps", [])],
            summary=str(payload.get("summary") or ""),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
        )


class PlannerComparisonStore:
    def __init__(self, settings: AppSettings) -> None:
        self._path = settings.planner_comparison_dir or (
            settings.conversation_memory_dir / "planner-comparisons"
        )
        self._path.mkdir(parents=True, exist_ok=True)
        self._db_path = self._path / "planner_comparisons.sqlite3"
        self._ensure_database()
        self._entries: dict[str, PlannerComparisonEntry] = {}
        self._entries_lock = threading.RLock()
        self._load_from_db()
        self._migrate_legacy_json_files()

    def record_comparison(
        self,
        *,
        conversation_id: str,
        exchange_id: str | None,
        workflow_action: str,
        question: str,
        comparison_status: str,
        deterministic_goal: str,
        shadow_goal: str | None,
        same_goal: bool,
        same_fallback_template: bool,
        deterministic_only_steps: list[str],
        shadow_only_steps: list[str],
        summary: str,
    ) -> PlannerComparisonEntry:
        entry = PlannerComparisonEntry(
            record_id=str(uuid4()),
            conversation_id=conversation_id,
            exchange_id=exchange_id,
            workflow_action=workflow_action,
            question=question.strip(),
            comparison_status=comparison_status,
            deterministic_goal=deterministic_goal,
            shadow_goal=shadow_goal,
            same_goal=same_goal,
            same_fallback_template=same_fallback_template,
            deterministic_only_steps=list(deterministic_only_steps),
            shadow_only_steps=list(shadow_only_steps),
            summary=summary.strip(),
            created_at=datetime.now(UTC),
        )
        with self._entries_lock:
            self._entries[entry.record_id] = entry
        self._persist_entry(entry)
        return entry

    def list_records(
        self,
        *,
        limit: int | None = None,
        actionable_only: bool = False,
    ) -> list[PlannerComparisonEntry]:
        with self._entries_lock:
            snapshot = list(self._entries.values())
        entries = sorted(snapshot, key=lambda item: item.created_at, reverse=True)
        if actionable_only:
            entries = [entry for entry in entries if entry.actionable]
        if limit is not None:
            entries = entries[: max(0, limit)]
        return entries

    def count_records(self) -> int:
        with self._entries_lock:
            return len(self._entries)

    def count_actionable_records(self) -> int:
        with self._entries_lock:
            return sum(1 for entry in self._entries.values() if entry.actionable)

    def count_status(self, comparison_status: str) -> int:
        with self._entries_lock:
            return sum(
                1 for entry in self._entries.values() if entry.comparison_status == comparison_status
            )

    def _persist_entry(self, entry: PlannerComparisonEntry) -> None:
        self._ensure_database()
        payload = json.dumps(entry.to_dict(), ensure_ascii=False)
        with sqlite3.connect(self._db_path) as connection:
            connection.execute(
                """
                INSERT INTO planner_comparison_entries (record_id, created_at, payload)
                VALUES (?, ?, ?)
                ON CONFLICT(record_id) DO UPDATE SET
                    created_at = excluded.created_at,
                    payload = excluded.payload
                """,
                (entry.record_id, entry.created_at.isoformat(), payload),
            )
            connection.commit()

    def _ensure_database(self) -> None:
        # Defensive: re-create the directory in case it was wiped at runtime.
        self._path.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self._db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS planner_comparison_entries (
                    record_id TEXT PRIMARY KEY,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def _load_from_db(self) -> None:
        self._ensure_database()
        with sqlite3.connect(self._db_path) as connection:
            rows = connection.execute(
                "SELECT payload FROM planner_comparison_entries"
            ).fetchall()
        for (payload_raw,) in rows:
            payload = json.loads(str(payload_raw))
            entry = PlannerComparisonEntry.from_dict(payload)
            self._entries[entry.record_id] = entry

    def _migrate_legacy_json_files(self) -> None:
        for file_path in sorted(self._path.glob("*.json")):
            payload = json.loads(file_path.read_text(encoding="utf-8"))
            entry = PlannerComparisonEntry.from_dict(payload)
            if entry.record_id not in self._entries:
                self._entries[entry.record_id] = entry
                self._persist_entry(entry)
            file_path.unlink(missing_ok=True)
