from __future__ import annotations

import json
import os
import sqlite3
import threading
import time
from pathlib import Path
from typing import Any


class ConversationTraceStore:
    """Durable full-fidelity chat traces for controlled experimentation."""

    def __init__(
        self,
        db_path: Path,
        *,
        retention_days: int = 365,
        archive_db_path: Path | None = None,
    ) -> None:
        self._db_path = db_path
        self._retention_days = max(0, retention_days)
        self._lock = threading.RLock()
        self._last_prune = 0.0
        self._archive_store: ConversationTraceStore | None = None
        self._db_path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
        os.chmod(self._db_path.parent, 0o700)
        self._initialize()
        resolved_archive = archive_db_path.expanduser().resolve() if archive_db_path else None
        self._archive_store = (
            ConversationTraceStore(resolved_archive, retention_days=self._retention_days)
            if resolved_archive is not None and resolved_archive != self._db_path.resolve()
            else None
        )
        if self._archive_store is not None:
            self.sync_archive()

    @property
    def db_path(self) -> Path:
        return self._db_path

    @property
    def archive_db_path(self) -> Path | None:
        return self._archive_store.db_path if self._archive_store is not None else None

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self._db_path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA journal_mode=WAL")
        connection.execute("PRAGMA synchronous=NORMAL")
        return connection

    def _secure_files(self) -> None:
        for path in (
            self._db_path,
            self._db_path.with_name(self._db_path.name + "-wal"),
            self._db_path.with_name(self._db_path.name + "-shm"),
        ):
            if path.exists():
                os.chmod(path, 0o600)

    def _initialize(self) -> None:
        with self._lock, self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS conversation_traces (
                    trace_id TEXT PRIMARY KEY,
                    request_id TEXT,
                    conversation_id TEXT,
                    student_name TEXT NOT NULL,
                    student_email TEXT,
                    source TEXT NOT NULL,
                    started_at REAL NOT NULL,
                    finished_at REAL,
                    duration_ms INTEGER,
                    status TEXT NOT NULL,
                    request_json TEXT NOT NULL,
                    response_json TEXT,
                    error_json TEXT
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_traces_started_at
                    ON conversation_traces(started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_conversation_traces_conversation_id
                    ON conversation_traces(conversation_id, started_at DESC);
                CREATE INDEX IF NOT EXISTS idx_conversation_traces_request_id
                    ON conversation_traces(request_id);
                CREATE TABLE IF NOT EXISTS conversation_trace_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    trace_id TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    event_type TEXT NOT NULL,
                    created_at REAL NOT NULL,
                    elapsed_ms INTEGER NOT NULL,
                    payload_json TEXT NOT NULL,
                    FOREIGN KEY(trace_id) REFERENCES conversation_traces(trace_id)
                        ON DELETE CASCADE,
                    UNIQUE(trace_id, sequence)
                );
                CREATE INDEX IF NOT EXISTS idx_conversation_trace_events_trace
                    ON conversation_trace_events(trace_id, sequence);
                """
            )
            connection.commit()
        self._secure_files()
        self.prune(force=True)

    def import_trace(self, trace: dict[str, Any]) -> None:
        """Idempotently import one full trace while preserving timestamps and order."""
        trace_id = str(trace["trace_id"])
        events = list(trace.get("events") or [])
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO conversation_traces(
                    trace_id, request_id, conversation_id, student_name, student_email,
                    source, started_at, finished_at, duration_ms, status,
                    request_json, response_json, error_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    trace.get("request_id"),
                    trace.get("conversation_id"),
                    trace.get("student_name") or "unknown",
                    trace.get("student_email"),
                    trace.get("source") or "unknown",
                    trace["started_at"],
                    trace.get("finished_at"),
                    trace.get("duration_ms"),
                    trace.get("status") or "unknown",
                    self._encode(trace.get("request") or {}),
                    self._encode(trace["response"]) if trace.get("response") is not None else None,
                    self._encode(trace["error"]) if trace.get("error") is not None else None,
                ),
            )
            connection.execute(
                "DELETE FROM conversation_trace_events WHERE trace_id = ?", (trace_id,)
            )
            connection.executemany(
                """
                INSERT INTO conversation_trace_events(
                    trace_id, sequence, event_type, created_at, elapsed_ms, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        trace_id,
                        int(event["sequence"]),
                        str(event["event_type"]),
                        float(event["created_at"]),
                        int(event["elapsed_ms"]),
                        self._encode(event.get("payload")),
                    )
                    for event in events
                ],
            )
            connection.commit()
        self._secure_files()
        self.prune()

    def sync_archive(self) -> int:
        """Backfill missing or changed primary traces into the local archive."""
        if self._archive_store is None:
            return 0
        with self._lock, self._connect() as connection:
            primary_rows = connection.execute(
                "SELECT trace_id, status, finished_at FROM conversation_traces"
            ).fetchall()
        with self._archive_store._lock, self._archive_store._connect() as connection:
            archive_state = {
                str(row["trace_id"]): (str(row["status"]), row["finished_at"])
                for row in connection.execute(
                    "SELECT trace_id, status, finished_at FROM conversation_traces"
                ).fetchall()
            }
        copied = 0
        for row in primary_rows:
            trace_id = str(row["trace_id"])
            state = (str(row["status"]), row["finished_at"])
            if archive_state.get(trace_id) == state:
                continue
            trace = self.get_trace(trace_id)
            if trace is not None:
                self._archive_store.import_trace(trace)
                copied += 1
        return copied

    @staticmethod
    def _encode(payload: Any) -> str:
        return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), default=str)

    def begin(
        self,
        *,
        trace_id: str,
        request_id: str | None,
        conversation_id: str | None,
        student_name: str,
        student_email: str | None,
        source: str,
        request_payload: dict[str, Any],
    ) -> None:
        started_at = time.time()
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                INSERT INTO conversation_traces(
                    trace_id, request_id, conversation_id, student_name,
                    student_email, source, started_at, status, request_json
                ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?)
                """,
                (
                    trace_id,
                    request_id,
                    conversation_id,
                    student_name,
                    student_email,
                    source,
                    started_at,
                    self._encode(request_payload),
                ),
            )
            connection.commit()
        self._secure_files()
        self.append_event(trace_id, "request", request_payload)
        self.prune()

    def append_event(self, trace_id: str, event_type: str, payload: Any) -> None:
        now = time.time()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT started_at FROM conversation_traces WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
            if row is None:
                return
            sequence = int(
                connection.execute(
                    """
                    SELECT COALESCE(MAX(sequence), 0) + 1
                    FROM conversation_trace_events WHERE trace_id = ?
                    """,
                    (trace_id,),
                ).fetchone()[0]
            )
            connection.execute(
                """
                INSERT INTO conversation_trace_events(
                    trace_id, sequence, event_type, created_at, elapsed_ms, payload_json
                ) VALUES (?, ?, ?, ?, ?, ?)
                """,
                (
                    trace_id,
                    sequence,
                    event_type,
                    now,
                    max(0, int((now - float(row["started_at"])) * 1000)),
                    self._encode(payload),
                ),
            )
            connection.commit()
        self._secure_files()

    def set_response(self, trace_id: str, response_payload: dict[str, Any]) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                """
                UPDATE conversation_traces
                SET response_json = ?,
                    conversation_id = COALESCE(?, conversation_id),
                    status = CASE WHEN status = 'running' THEN 'answer_ready' ELSE status END
                WHERE trace_id = ?
                """,
                (
                    self._encode(response_payload),
                    response_payload.get("conversation_id"),
                    trace_id,
                ),
            )
            connection.commit()
        self._secure_files()

    def finish(
        self,
        trace_id: str,
        *,
        status: str,
        error: dict[str, Any] | None = None,
    ) -> None:
        finished_at = time.time()
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT started_at FROM conversation_traces WHERE trace_id = ?",
                (trace_id,),
            ).fetchone()
            if row is None:
                return
            connection.execute(
                """
                UPDATE conversation_traces
                SET finished_at = ?, duration_ms = ?, status = ?, error_json = ?
                WHERE trace_id = ?
                """,
                (
                    finished_at,
                    max(0, int((finished_at - float(row["started_at"])) * 1000)),
                    status,
                    self._encode(error) if error is not None else None,
                    trace_id,
                ),
            )
            connection.commit()
        self._secure_files()
        if self._archive_store is not None:
            trace = self.get_trace(trace_id)
            if trace is not None:
                self._archive_store.import_trace(trace)

    def list_traces(
        self,
        *,
        limit: int = 50,
        conversation_id: str | None = None,
    ) -> list[dict[str, Any]]:
        bounded_limit = min(max(limit, 1), 500)
        query = """
            SELECT trace_id, request_id, conversation_id, student_name, student_email,
                   source, started_at, finished_at, duration_ms, status
            FROM conversation_traces
        """
        parameters: list[Any] = []
        if conversation_id:
            query += " WHERE conversation_id = ?"
            parameters.append(conversation_id)
        query += " ORDER BY started_at DESC LIMIT ?"
        parameters.append(bounded_limit)
        with self._lock, self._connect() as connection:
            return [dict(row) for row in connection.execute(query, parameters).fetchall()]

    def get_trace(self, trace_id: str) -> dict[str, Any] | None:
        with self._lock, self._connect() as connection:
            trace = connection.execute(
                "SELECT * FROM conversation_traces WHERE trace_id = ?", (trace_id,)
            ).fetchone()
            if trace is None:
                return None
            events = connection.execute(
                """
                SELECT sequence, event_type, created_at, elapsed_ms, payload_json
                FROM conversation_trace_events
                WHERE trace_id = ? ORDER BY sequence
                """,
                (trace_id,),
            ).fetchall()
        result = dict(trace)
        result["request"] = json.loads(result.pop("request_json"))
        response_json = result.pop("response_json")
        error_json = result.pop("error_json")
        result["response"] = json.loads(response_json) if response_json else None
        result["error"] = json.loads(error_json) if error_json else None
        result["events"] = [
            {
                "sequence": row["sequence"],
                "event_type": row["event_type"],
                "created_at": row["created_at"],
                "elapsed_ms": row["elapsed_ms"],
                "payload": json.loads(row["payload_json"]),
            }
            for row in events
        ]
        return result

    def stats(self) -> dict[str, Any]:
        now = time.time()
        with self._lock, self._connect() as connection:
            total, completed, failed = connection.execute(
                """
                SELECT COUNT(*),
                       COALESCE(SUM(status = 'completed'), 0),
                       COALESCE(SUM(status IN ('error', 'timeout')), 0)
                FROM conversation_traces
                """
            ).fetchone()
            recent = connection.execute(
                "SELECT COUNT(*) FROM conversation_traces WHERE started_at >= ?",
                (now - 86400,),
            ).fetchone()[0]
        return {
            "total_traces": int(total),
            "completed_traces": int(completed),
            "failed_traces": int(failed),
            "traces_24h": int(recent),
            "retention_days": self._retention_days,
        }

    def prune(self, *, force: bool = False) -> None:
        if self._retention_days == 0:
            return
        now = time.time()
        if not force and now - self._last_prune < 3600:
            return
        cutoff = now - self._retention_days * 86400
        with self._lock, self._connect() as connection:
            connection.execute(
                "DELETE FROM conversation_traces WHERE started_at < ?", (cutoff,)
            )
            connection.commit()
        self._last_prune = now
        self._secure_files()
