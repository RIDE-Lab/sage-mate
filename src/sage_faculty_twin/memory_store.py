from __future__ import annotations

import json
import re
import sqlite3
import time
from collections import Counter
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from sage.neuromem import (
    MemoryEntry,
    QueryRequest,
    RetrievalResult,
    ServiceStats,
    TelemetryEvent,
)

from .config import AppSettings
from .models import BookingResponse, ChatAttachment, ChatRequest, WebSearchHit
from .profile_summarizer import ConversationProfileSummarizer


@dataclass(slots=True)
class AttachmentMemoryRecord:
    file_name: str
    media_type: str
    text_excerpt: str
    size_bytes: int | None = None

    def to_dict(self) -> dict[str, object]:
        return {
            "file_name": self.file_name,
            "media_type": self.media_type,
            "text_excerpt": self.text_excerpt,
            "size_bytes": self.size_bytes,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> AttachmentMemoryRecord:
        return cls(
            file_name=str(payload.get("file_name") or "attachment"),
            media_type=str(payload.get("media_type") or "application/octet-stream"),
            text_excerpt=str(payload.get("text_excerpt") or ""),
            size_bytes=(
                int(payload["size_bytes"]) if payload.get("size_bytes") is not None else None
            ),
        )


@dataclass(slots=True)
class ConversationMemoryRecord:
    memory_id: str
    conversation_id: str
    student_name: str
    student_email: str | None
    course_context: str | None
    question: str
    answer: str
    workflow_action: str
    interaction_domain: str | None
    knowledge_hit_count: int
    booking_summary: str | None
    created_at: datetime
    attachments: list[AttachmentMemoryRecord] = field(default_factory=list)
    web_search_hits: list[WebSearchHit] = field(default_factory=list)

    def to_dict(self) -> dict[str, object]:
        return {
            "memory_id": self.memory_id,
            "conversation_id": self.conversation_id,
            "student_name": self.student_name,
            "student_email": self.student_email,
            "course_context": self.course_context,
            "question": self.question,
            "answer": self.answer,
            "workflow_action": self.workflow_action,
            "interaction_domain": self.interaction_domain,
            "knowledge_hit_count": self.knowledge_hit_count,
            "booking_summary": self.booking_summary,
            "created_at": self.created_at.isoformat(),
            "attachments": [attachment.to_dict() for attachment in self.attachments],
            "web_search_hits": [hit.model_dump(mode="json") for hit in self.web_search_hits],
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> ConversationMemoryRecord:
        return cls(
            memory_id=str(payload["memory_id"]),
            conversation_id=str(payload["conversation_id"]),
            student_name=str(payload["student_name"]),
            student_email=(str(payload["student_email"]) if payload.get("student_email") else None),
            course_context=(
                str(payload["course_context"]) if payload.get("course_context") else None
            ),
            question=str(payload["question"]),
            answer=str(payload["answer"]),
            workflow_action=str(payload["workflow_action"]),
            interaction_domain=(
                str(payload["interaction_domain"]) if payload.get("interaction_domain") else None
            ),
            knowledge_hit_count=int(payload.get("knowledge_hit_count", 0)),
            booking_summary=(
                str(payload["booking_summary"]) if payload.get("booking_summary") else None
            ),
            created_at=datetime.fromisoformat(str(payload["created_at"])),
            attachments=[
                AttachmentMemoryRecord.from_dict(item)
                for item in list(payload.get("attachments") or [])
                if isinstance(item, dict)
            ],
            web_search_hits=[
                WebSearchHit.model_validate(item)
                for item in list(payload.get("web_search_hits") or [])
                if isinstance(item, dict)
            ],
        )

    def retrieval_text(self) -> str:
        parts = [
            f"student {self.student_name}",
            f"question {self.question}",
            f"answer {self.answer}",
            f"workflow {self.workflow_action}",
        ]
        if self.interaction_domain:
            parts.append(f"domain {self.interaction_domain}")
        parts.append(f"knowledge_hits {self.knowledge_hit_count}")
        if self.student_email:
            parts.append(f"email {self.student_email}")
        if self.course_context:
            parts.append(f"course {self.course_context}")
        if self.booking_summary:
            parts.append(f"booking {self.booking_summary}")
        if self.attachments:
            attachment_labels = ", ".join(attachment.file_name for attachment in self.attachments)
            parts.append(f"attachments {attachment_labels}")
        if self.web_search_hits:
            parts.append(f"web_search_hits {len(self.web_search_hits)}")
        return " ".join(parts)


@dataclass(slots=True)
class ConversationMemoryHit:
    memory_id: str
    conversation_id: str
    summary: str
    score: float
    created_at: datetime
    memory_type: str = "short_term"
    source: str = "user_message"
    topic: str = "conversation_exchange"
    source_label: str = "近期对话"


@dataclass(slots=True)
class ProfileMemoryRecord:
    profile_id: str
    student_key: str
    student_name: str
    student_email: str | None
    category: str
    summary: str
    evidence: str
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "profile_id": self.profile_id,
            "student_key": self.student_key,
            "student_name": self.student_name,
            "student_email": self.student_email,
            "category": self.category,
            "summary": self.summary,
            "evidence": self.evidence,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> ProfileMemoryRecord:
        return cls(
            profile_id=str(payload["profile_id"]),
            student_key=str(payload["student_key"]),
            student_name=str(payload["student_name"]),
            student_email=(str(payload["student_email"]) if payload.get("student_email") else None),
            category=str(payload["category"]),
            summary=str(payload["summary"]),
            evidence=str(payload["evidence"]),
            updated_at=datetime.fromisoformat(str(payload["updated_at"])),
        )

    def retrieval_text(self) -> str:
        category_aliases = {
            "identity": "用户身份 姓名 邮箱 联系方式",
            "course_context": "课程场景 交流背景 科研指导",
            "recent_topic": "近期话题 关注主题 讨论重点",
            "booking_preference": "预约偏好 预约习惯 预约需求 meeting booking preference",
            "collaboration_preference": "沟通偏好 协作偏好 会前准备 准备材料 agenda blocker draft collaboration preference",
        }
        parts = [
            f"student {self.student_name}",
            f"category {self.category}",
            category_aliases.get(self.category, ""),
            self.summary,
            self.evidence,
        ]
        if self.student_email:
            parts.append(f"email {self.student_email}")
        return " ".join(parts)


@dataclass(slots=True)
class MemorySearchPlan:
    policy_name: str
    short_term_limit: int
    long_term_limit: int


@dataclass(slots=True)
class ConversationDigestRecord:
    """Rolling digest of a conversation's older turns.

    The digest accumulates compressed summaries of earlier exchanges so
    that the prompt builder can inject long-range context without the
    token cost of verbatim history.  Each update merges new turns into
    the existing digest text via an LLM summarization call.
    """

    conversation_id: str
    digest_text: str
    turns_summarized: int
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "conversation_id": self.conversation_id,
            "digest_text": self.digest_text,
            "turns_summarized": self.turns_summarized,
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> ConversationDigestRecord:
        return cls(
            conversation_id=str(payload["conversation_id"]),
            digest_text=str(payload["digest_text"]),
            turns_summarized=int(payload.get("turns_summarized", 0)),
            updated_at=datetime.fromisoformat(str(payload["updated_at"])),
        )


class ConversationDigestStore:
    """Persistent store for per-conversation rolling digests.

    Each digest is a single JSON file under ``<digest_dir>/`` keyed by
    conversation ID.  The store provides simple get/update semantics and
    delegates the actual summarization text to an external caller (the
    service layer calls the LLM client and passes the result here).
    """

    def __init__(self, digest_dir: Path) -> None:
        self._digest_dir = digest_dir
        self._digest_dir.mkdir(parents=True, exist_ok=True)
        self._cache: dict[str, ConversationDigestRecord] = {}
        self._load_all()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_digest(self, conversation_id: str) -> ConversationDigestRecord | None:
        return self._cache.get(conversation_id)

    def update_digest(
        self,
        conversation_id: str,
        digest_text: str,
        turns_summarized: int,
    ) -> ConversationDigestRecord:
        record = ConversationDigestRecord(
            conversation_id=conversation_id,
            digest_text=digest_text,
            turns_summarized=turns_summarized,
            updated_at=datetime.now(UTC),
        )
        self._cache[conversation_id] = record
        self._persist(record)
        return record

    def count(self) -> int:
        return len(self._cache)

    # ------------------------------------------------------------------
    # Persistence helpers
    # ------------------------------------------------------------------

    def _digest_path(self, conversation_id: str) -> Path:
        safe_id = re.sub(r"[^\w\-]", "_", conversation_id)
        return self._digest_dir / f"{safe_id}.json"

    def _persist(self, record: ConversationDigestRecord) -> None:
        path = self._digest_path(record.conversation_id)
        path.write_text(json.dumps(record.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")

    def _load_all(self) -> None:
        if not self._digest_dir.exists():
            return
        for path in self._digest_dir.glob("*.json"):
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                record = ConversationDigestRecord.from_dict(payload)
                self._cache[record.conversation_id] = record
            except (json.JSONDecodeError, KeyError, ValueError):
                continue


class NeuroMemConversationStore:
    def __init__(self, settings: AppSettings) -> None:
        self._settings = settings
        self._base_dir = settings.conversation_memory_dir
        self._base_dir.mkdir(parents=True, exist_ok=True)
        self._sqlite_db_path = self._base_dir / "memory_store.sqlite3"
        self._initialize_sqlite_store()
        self._collections_dir = self._base_dir / "collections"
        self._collections_dir.mkdir(parents=True, exist_ok=True)
        self._conversation_collection_dir = self._collections_dir / "conversation-memory"
        self._profile_collection_dir = self._collections_dir / "conversation-profile-memory"
        self._conversation_collection_type = self._resolve_conversation_collection_type()
        self._records: dict[str, ConversationMemoryRecord] = {}
        self._profiles: dict[str, ProfileMemoryRecord] = {}
        self._conversation_timelines: dict[str, list[str]] = {}
        self._telemetry_limit = 24
        self._telemetry_events: list[dict[str, Any]] = []
        self._telemetry_type_counts: Counter[str] = Counter()
        self._profile_summarizer = ConversationProfileSummarizer()
        self._conversation_collection = self._load_or_create_collection(
            "conversation-memory",
            self._conversation_collection_dir,
            collection_role="conversation",
        )
        self._profile_collection = self._load_or_create_collection(
            "conversation-profile-memory",
            self._profile_collection_dir,
            collection_role="profile",
        )
        self._rebuild_runtime_state()
        if self._migrate_legacy_disk_layout():
            self._rebuild_runtime_state()
        if self._canonicalize_profile_collection():
            self._rebuild_runtime_state()

    def _default_collection_index_type(self) -> str:
        configured = (self._settings.conversation_memory_index_type or "").strip().lower()
        try:
            from sage.neuromem.memory_collection.indexes import IndexFactory

            available = set(IndexFactory.list_types())
        except Exception:
            available = set()

        def _is_candidate_declared(index_type: str) -> bool:
            return (not available) or (index_type in available)

        def _check_index_readiness(index_type: str) -> tuple[bool, str]:
            if index_type in {"sage_vdb_ann", "sagedb_ann"}:
                try:
                    import sage_anns  # noqa: F401
                except Exception:
                    return (
                        False,
                        "Index '{index_type}' requires optional dependency 'sage_anns'. "
                        "Install it or set DIGITAL_TWIN_CONVERSATION_MEMORY_INDEX_TYPE=auto.",
                    )
            return True, ""

        if configured in {"", "auto"}:
            for candidate in (
                "sage_vdb_ann",
                "sagedb_ann",
                "faiss",
                "segment",
                "fifo",
            ):
                if not _is_candidate_declared(candidate):
                    continue
                ready, _ = _check_index_readiness(candidate)
                if ready:
                    return candidate

            for candidate in sorted(available):
                ready, _ = _check_index_readiness(candidate)
                if ready:
                    return candidate

            if available:
                raise RuntimeError(
                    "No auto-selectable conversation memory index is ready in current environment. "
                    f"Declared indexes: {sorted(available)}"
                )
            return "segment"

        ready, reason = _check_index_readiness(configured)
        if not ready:
            raise RuntimeError(reason.format(index_type=configured))

        if configured and not _is_candidate_declared(configured):
            if available:
                raise ValueError(
                    "DIGITAL_TWIN_CONVERSATION_MEMORY_INDEX_TYPE is not declared by neuromem: "
                    f"'{configured}'. Available: {sorted(available)}"
                )
            raise ValueError(
                "DIGITAL_TWIN_CONVERSATION_MEMORY_INDEX_TYPE is invalid or unavailable: "
                f"'{configured}'."
            )

        return configured

    def _resolve_conversation_collection_type(self) -> str:
        configured = (self._settings.conversation_memory_collection_type or "").strip().lower()
        if configured in {"", "auto"}:
            return "neural_continual"
        if configured in {"neural_continual", "trainable", "online_continual_memory"}:
            return "neural_continual"
        if configured in {"unified", "standard"}:
            return "unified"
        raise ValueError(
            "DIGITAL_TWIN_CONVERSATION_MEMORY_COLLECTION_TYPE must be one of "
            "auto|neural_continual|online_continual_memory|trainable|unified|standard"
        )

    def _build_neural_collection_config(self) -> dict[str, Any]:
        return {
            "collection_type": "neural_continual",
            "feature_dim": int(self._settings.conversation_memory_neural_feature_dim),
            "learning_rate": float(self._settings.conversation_memory_neural_learning_rate),
            "weight_decay": float(self._settings.conversation_memory_neural_weight_decay),
            "replay_buffer_size": int(self._settings.conversation_memory_neural_replay_buffer_size),
            "replay_batch_size": int(self._settings.conversation_memory_neural_replay_batch_size),
            "score_blend": float(self._settings.conversation_memory_neural_score_blend),
            "recency_bias": float(self._settings.conversation_memory_neural_recency_bias),
            "query_overlap_bias": float(self._settings.conversation_memory_neural_query_overlap_bias),
        }

    def _default_collection_index_config(self, index_type: str) -> dict[str, Any]:
        if index_type == "bm25":
            return {"backend": "numpy", "csc_backend": "numpy"}
        if index_type == "faiss":
            return {
                "dim": int(self._settings.sagevdb_dimension),
                "metric": "cosine",
            }
        if index_type in {"sage_vdb_ann", "sagedb_ann"}:
            algorithm = self._settings.sagevdb_anns_algorithm.strip().lower()
            if not algorithm:
                algorithm = "faiss_hnsw"
            return {
                "dim": int(self._settings.sagevdb_dimension),
                "metric": "cosine",
                "ann_algorithm": algorithm,
                "backend_name": self._settings.sagevdb_backend,
                "allow_faiss_fallback": True,
            }
        return {}

    def _normalize_index_config(self, index_type: str, config: dict[str, Any]) -> dict[str, Any]:
        normalized = dict(config)
        if index_type == "bm25":
            if not normalized.get("backend"):
                normalized["backend"] = "numpy"
            if not normalized.get("csc_backend"):
                normalized["csc_backend"] = "numpy"
        return normalized

    def add_exchange(
        self,
        request: ChatRequest,
        *,
        conversation_id: str,
        answer: str,
        workflow_action: str,
        interaction_domain: str | None,
        knowledge_hit_count: int,
        booking_result: BookingResponse | None,
        web_search_hits: list[WebSearchHit] | None = None,
    ) -> ConversationMemoryRecord:
        started_at = time.time()
        record = ConversationMemoryRecord(
            memory_id=str(uuid4()),
            conversation_id=conversation_id,
            student_name=request.student_name,
            student_email=request.student_email,
            course_context=request.course_context,
            question=request.question,
            answer=answer,
            workflow_action=workflow_action,
            interaction_domain=interaction_domain,
            knowledge_hit_count=knowledge_hit_count,
            booking_summary=self._booking_summary(booking_result),
            created_at=datetime.now(UTC),
            attachments=self._extract_attachment_records(request.attachments),
            web_search_hits=[hit.model_copy(deep=True) for hit in (web_search_hits or [])],
        )
        self._records[record.memory_id] = record
        self._prepend_conversation_timeline(record)
        self._store_conversation_entry(record)
        self._persist_collection(self._conversation_collection, self._conversation_collection_dir)
        self._record_telemetry_event(
            "write_conversation",
            collection_name=self._conversation_collection.name,
            duration_ms=(time.time() - started_at) * 1000.0,
            attributes={
                "conversation_id": conversation_id,
                "memory_id": record.memory_id,
                "workflow_action": workflow_action,
                "student_email_present": bool(request.student_email),
            },
        )
        return record

    def consolidate_profiles(self, record: ConversationMemoryRecord) -> int:
        started_at = time.time()
        profiles = self._derive_profiles(record)
        for profile in profiles:
            self._upsert_profile(profile)
        if profiles:
            self._persist_collection(self._profile_collection, self._profile_collection_dir)
            self._record_telemetry_event(
                "write_profile",
                collection_name=self._profile_collection.name,
                duration_ms=(time.time() - started_at) * 1000.0,
                attributes={
                    "profile_count": len(profiles),
                    "student_name": record.student_name,
                    "student_email_present": bool(record.student_email),
                    "categories": [profile.category for profile in profiles],
                },
            )
        return len(profiles)

    def search(
        self,
        request: ChatRequest,
        *,
        conversation_id: str,
        top_k: int | None = None,
        include_short_term: bool = True,
        include_long_term: bool = True,
    ) -> list[ConversationMemoryHit]:
        if not self._records:
            return []

        started_at = time.time()
        plan = self._select_search_plan(request, top_k)
        short_term_hits = (
            self._search_short_term(
                request,
                conversation_id=conversation_id,
                limit=plan.short_term_limit,
            )
            if include_short_term
            else []
        )
        long_term_hits = (
            self._search_long_term(request, limit=plan.long_term_limit) if include_long_term else []
        )
        hits = short_term_hits + long_term_hits
        self._record_telemetry_event(
            "retrieve",
            collection_name=self._conversation_collection.name,
            duration_ms=(time.time() - started_at) * 1000.0,
            attributes={
                "conversation_id": conversation_id,
                "policy_name": plan.policy_name,
                "short_term_limit": plan.short_term_limit,
                "long_term_limit": plan.long_term_limit,
                "include_short_term": include_short_term,
                "include_long_term": include_long_term,
                "result_count": len(hits),
                "short_term_hits": len(short_term_hits),
                "long_term_hits": len(long_term_hits),
            },
        )
        return hits

    def search_artifacts(
        self,
        request: ChatRequest,
        *,
        conversation_id: str,
        top_k: int | None = None,
    ) -> list[ConversationMemoryHit]:
        if not self._records:
            return []

        started_at = time.time()
        limit = max(1, top_k or self._settings.conversation_memory_top_k)
        query_request = self._build_artifact_query_request(request, limit=limit)
        results = self._retrieve_from_collection(self._conversation_collection, query_request)
        retrieval_results = self._coerce_retrieval_results(results)
        hits: list[ConversationMemoryHit] = []
        seen_keys: set[tuple[str, int]] = set()

        for (
            score,
            record,
            attachment_index,
            attachment,
        ) in self._rank_artifact_candidates(
            request,
            conversation_id=conversation_id,
        ):
            dedupe_key = (record.memory_id, attachment_index)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            hits.append(
                ConversationMemoryHit(
                    memory_id=f"{record.memory_id}:artifact:{attachment_index + 1}",
                    conversation_id=record.conversation_id,
                    summary=self._format_attachment_summary(record, attachment, attachment_index),
                    score=score,
                    created_at=record.created_at,
                    memory_type="short_term",
                    source="attachment_excerpt",
                    topic="artifact_memory",
                    source_label="上传材料",
                )
            )
            if len(hits) >= limit:
                break

        for rank, result in enumerate(retrieval_results, start=1):
            metadata = dict(result.metadata or {})
            if str(metadata.get("topic") or "") != "artifact_memory":
                continue
            memory_id = str(metadata.get("memory_id") or "")
            attachment_index = int(metadata.get("attachment_index") or -1)
            if not memory_id or attachment_index < 0:
                continue
            record = self._records.get(memory_id)
            if record is None or not self._should_include_record(record, request, conversation_id):
                continue
            if attachment_index >= len(record.attachments):
                continue
            dedupe_key = (memory_id, attachment_index)
            if dedupe_key in seen_keys:
                continue
            seen_keys.add(dedupe_key)
            attachment = record.attachments[attachment_index]
            hits.append(
                ConversationMemoryHit(
                    memory_id=f"{memory_id}:artifact:{attachment_index + 1}",
                    conversation_id=record.conversation_id,
                    summary=self._format_attachment_summary(record, attachment, attachment_index),
                    score=float(result.score if result.score is not None else (1.0 / float(rank))),
                    created_at=record.created_at,
                    memory_type="short_term",
                    source="attachment_excerpt",
                    topic="artifact_memory",
                    source_label="上传材料",
                )
            )
            if len(hits) >= limit:
                break

        self._record_telemetry_event(
            "retrieve_artifact",
            collection_name=self._conversation_collection.name,
            duration_ms=(time.time() - started_at) * 1000.0,
            attributes={
                "conversation_id": conversation_id,
                "result_count": len(hits),
                "query": query_request.query,
            },
        )
        return hits

    def count_records(self) -> int:
        return len(self._records)

    def get_record(self, memory_id: str) -> ConversationMemoryRecord | None:
        return self._records.get(memory_id)

    def list_records(self, *, since: datetime | None = None) -> list[ConversationMemoryRecord]:
        records = sorted(self._records.values(), key=lambda record: record.created_at, reverse=True)
        if since is not None:
            records = [record for record in records if record.created_at >= since]
        return records

    def list_recent_conversation_records(
        self,
        conversation_id: str,
        *,
        exclude_question: str | None = None,
        limit: int = 20,
    ) -> list[ConversationMemoryRecord]:
        normalized_conversation_id = conversation_id.strip()
        if not normalized_conversation_id or limit <= 0:
            return []

        normalized_excluded_question = (exclude_question or "").strip()
        timeline = self._conversation_timelines.get(normalized_conversation_id, [])
        records: list[ConversationMemoryRecord] = []
        for memory_id in timeline:
            record = self._records.get(memory_id)
            if record is None:
                continue
            if (
                normalized_excluded_question
                and record.question.strip() == normalized_excluded_question
            ):
                continue
            records.append(record)
            if len(records) >= limit:
                break
        return records

    def count_profiles(self) -> int:
        return len(self._profiles)

    def available_profile_categories(self) -> list[str]:
        return self._profile_summarizer.available_categories()

    def list_profiles(
        self,
        *,
        category: str | None = None,
        student_query: str | None = None,
        limit: int = 50,
    ) -> list[ProfileMemoryRecord]:
        normalized_category = category.strip() if category else None
        normalized_query = student_query.strip().lower() if student_query else None
        profiles = sorted(
            self._profiles.values(),
            key=lambda profile: profile.updated_at,
            reverse=True,
        )
        if normalized_category:
            profiles = [profile for profile in profiles if profile.category == normalized_category]
        if normalized_query:
            profiles = [
                profile
                for profile in profiles
                if normalized_query in profile.student_name.lower()
                or normalized_query in profile.student_key.lower()
                or (profile.student_email and normalized_query in profile.student_email.lower())
            ]
        return profiles[: max(1, limit)]

    def list_profiles_for_student(
        self,
        *,
        student_name: str,
        student_email: str | None,
        limit: int = 20,
    ) -> list[ProfileMemoryRecord]:
        student_key = self._student_key(student_name, student_email)
        if student_key is None:
            return []
        profiles = [
            profile for profile in self._profiles.values() if profile.student_key == student_key
        ]
        profiles.sort(key=lambda profile: profile.updated_at, reverse=True)
        return profiles[: max(1, limit)]

    def backend_name(self) -> str:
        return "neuromem-layered"

    def get_telemetry_events(self, limit: int | None = None) -> list[dict[str, Any]]:
        if limit is None or limit <= 0:
            return [dict(event) for event in self._telemetry_events]
        return [dict(event) for event in self._telemetry_events[-limit:]]

    def get_telemetry_summary(self) -> dict[str, Any]:
        by_type = {
            event_type: {"count": count}
            for event_type, count in sorted(self._telemetry_type_counts.items())
        }
        recent_events = self.get_telemetry_events(limit=min(5, self._telemetry_limit))
        query_count = sum(
            count
            for event_type, count in self._telemetry_type_counts.items()
            if event_type.startswith("retrieve")
        )
        write_count = sum(
            count
            for event_type, count in self._telemetry_type_counts.items()
            if event_type.startswith("write")
        )
        last_event = self._telemetry_events[-1] if self._telemetry_events else None
        usefulness_events = [
            event
            for event in self._telemetry_events
            if str(event.get("event_type") or "") == "score_memory_usefulness"
        ]
        usefulness_by_signal = Counter(
            str((event.get("attributes") or {}).get("signal") or "unknown")
            for event in usefulness_events
        )
        last_usefulness_event = usefulness_events[-1] if usefulness_events else None
        last_usefulness_attributes = (
            dict(last_usefulness_event.get("attributes") or {}) if last_usefulness_event else {}
        )
        return {
            "event_count": len(self._telemetry_events),
            "query_count": query_count,
            "write_count": write_count,
            "recent_event_count": len(recent_events),
            "last_event_type": str(last_event.get("event_type") or "") if last_event else "",
            "last_event_at": float(last_event.get("timestamp") or 0.0) if last_event else 0.0,
            "by_type": by_type,
            "memory_usefulness": {
                "score_count": len(usefulness_events),
                "by_signal": dict(sorted(usefulness_by_signal.items())),
                "memory_used_count": sum(
                    1
                    for event in usefulness_events
                    if bool((event.get("attributes") or {}).get("memory_used"))
                ),
                "knowledge_used_count": sum(
                    1
                    for event in usefulness_events
                    if bool((event.get("attributes") or {}).get("knowledge_used"))
                ),
                "last_signal": str(last_usefulness_attributes.get("signal") or "")
                if last_usefulness_event
                else "",
                "last_reason": str(last_usefulness_attributes.get("reason") or "")
                if last_usefulness_event
                else "",
                "last_scored_at": float(last_usefulness_event.get("timestamp") or 0.0)
                if last_usefulness_event
                else 0.0,
            },
        }

    def runtime_snapshot(self) -> dict[str, Any]:
        return {
            "backend": self.backend_name(),
            "conversation_stats": self._build_service_stats(
                self._conversation_collection,
                memory_scope="short_term",
            ),
            "profile_stats": self._build_service_stats(
                self._profile_collection,
                memory_scope="long_term",
            ),
            "recent_events": self.get_telemetry_events(limit=8),
        }

    def _select_search_plan(self, request: ChatRequest, top_k: int | None) -> MemorySearchPlan:
        limit = top_k or self._settings.conversation_memory_top_k
        question = request.question.lower()
        if any(keyword in question for keyword in ("预约", "meeting", "schedule", "book", "时间")):
            return MemorySearchPlan(
                policy_name="booking-first",
                short_term_limit=max(2, limit),
                long_term_limit=1,
            )
        if any(keyword in question for keyword in ("之前", "上次", "记得", "偏好", "邮箱", "名字")):
            return MemorySearchPlan(
                policy_name="profile-aware",
                short_term_limit=max(2, limit // 2 + 1),
                long_term_limit=max(1, limit // 2),
            )
        return MemorySearchPlan(
            policy_name="balanced",
            short_term_limit=max(2, limit - 1),
            long_term_limit=1,
        )

    def _search_short_term(
        self,
        request: ChatRequest,
        *,
        conversation_id: str,
        limit: int,
    ) -> list[ConversationMemoryHit]:
        query_request = self._build_short_term_query_request(
            request, conversation_id=conversation_id, limit=limit
        )
        results = self._retrieve_from_collection(self._conversation_collection, query_request)
        retrieval_results = self._coerce_retrieval_results(results)
        hits: list[ConversationMemoryHit] = []
        seen_memory_ids: set[str] = set()

        recent_same_conversation = self.list_recent_conversation_records(
            conversation_id,
            exclude_question=request.question,
            limit=limit,
        )
        for rank, record in enumerate(recent_same_conversation, start=1):
            seen_memory_ids.add(record.memory_id)
            entry = self._build_conversation_memory_entry(record)
            hits.append(
                ConversationMemoryHit(
                    memory_id=record.memory_id,
                    conversation_id=record.conversation_id,
                    summary=self._format_summary(record),
                    score=1.0 + (1.0 / float(rank)),
                    created_at=record.created_at,
                    memory_type="short_term",
                    source=str(entry.metadata.get("source") or "user_message"),
                    topic=str(entry.metadata.get("topic") or "conversation_exchange"),
                    source_label="近期对话",
                )
            )

        for rank, result in enumerate(retrieval_results, start=1):
            metadata = dict(result.metadata or {})
            if str(metadata.get("topic") or "") == "artifact_memory":
                continue
            memory_id = str(metadata.get("memory_id") or "")
            if not memory_id or memory_id in seen_memory_ids:
                continue
            record = self._records.get(memory_id)
            if record is None or not self._should_include_record(record, request, conversation_id):
                continue
            seen_memory_ids.add(memory_id)
            hits.append(
                ConversationMemoryHit(
                    memory_id=record.memory_id,
                    conversation_id=record.conversation_id,
                    summary=self._format_summary(record),
                    score=float(result.score if result.score is not None else (1.0 / float(rank))),
                    created_at=record.created_at,
                    memory_type="short_term",
                    source=str(metadata.get("source") or "user_message"),
                    topic=str(metadata.get("topic") or "conversation_exchange"),
                    source_label="近期对话",
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def _search_long_term(self, request: ChatRequest, *, limit: int) -> list[ConversationMemoryHit]:
        student_key = self._student_key(request.student_name, request.student_email)
        if limit <= 0 or student_key is None or not self._profiles:
            return []

        query_parts = [request.question, request.student_name]
        if request.course_context:
            query_parts.append(request.course_context)
        if request.student_email:
            query_parts.append(request.student_email)

        query_request = self._build_long_term_query_request(
            request, student_key=student_key, limit=limit
        )
        results = self._retrieve_from_collection(self._profile_collection, query_request)
        retrieval_results = self._coerce_retrieval_results(results)
        hits: list[ConversationMemoryHit] = []
        seen_profile_ids: set[str] = set()

        preferred_categories = self._preferred_profile_categories(request.question)
        if preferred_categories:
            prioritized_profiles = sorted(
                (
                    profile
                    for profile in self._profiles.values()
                    if profile.student_key == student_key
                    and profile.category in preferred_categories
                ),
                key=lambda profile: profile.updated_at,
                reverse=True,
            )
            for profile in prioritized_profiles:
                seen_profile_ids.add(profile.profile_id)
                entry = self._build_profile_memory_entry(profile)
                hits.append(
                    ConversationMemoryHit(
                        memory_id=profile.profile_id,
                        conversation_id="profile",
                        summary=self._format_profile_summary(profile),
                        score=1.5,
                        created_at=profile.updated_at,
                        memory_type="long_term",
                        source=str(entry.metadata.get("source") or "system_event"),
                        topic=str(entry.metadata.get("topic") or profile.category),
                        source_label="长期记录",
                    )
                )
                if len(hits) >= limit:
                    return hits

        for rank, result in enumerate(retrieval_results, start=1):
            metadata = dict(result.metadata or {})
            profile_id = str(metadata.get("profile_id") or "")
            if not profile_id or profile_id in seen_profile_ids:
                continue
            profile = self._profiles.get(profile_id)
            if profile is None or profile.student_key != student_key:
                continue
            seen_profile_ids.add(profile_id)
            hits.append(
                ConversationMemoryHit(
                    memory_id=profile.profile_id,
                    conversation_id="profile",
                    summary=self._format_profile_summary(profile),
                    score=float(result.score if result.score is not None else (1.0 / float(rank))),
                    created_at=profile.updated_at,
                    memory_type="long_term",
                    source=str(metadata.get("source") or "system_event"),
                    topic=str(metadata.get("topic") or profile.category),
                    source_label="长期记录",
                )
            )
            if len(hits) >= limit:
                break
        return hits

    def _preferred_profile_categories(self, question: str) -> list[str]:
        lowered = question.lower()
        categories: list[str] = []
        if ("预约" in question or "booking" in lowered or "meeting" in lowered) and (
            "习惯" in question or "偏好" in question or "preference" in lowered
        ):
            categories.append("booking_preference")
        if (
            "沟通偏好" in question
            or "协作偏好" in question
            or ("准备" in question and ("偏好" in question or "习惯" in question))
            or ("prepare" in lowered and "preference" in lowered)
        ):
            categories.append("collaboration_preference")
        if "邮箱" in question or "名字" in question or "联系方式" in question:
            categories.append("identity")
        if "课程" in question or "研究方向" in question or "科研" in question:
            categories.append("course_context")
        return categories

    def _collection_type_for_role(self, collection_role: str) -> str:
        if collection_role == "conversation":
            return self._conversation_collection_type
        return "unified"

    def _is_neural_collection(self, collection: Any) -> bool:
        return str(getattr(collection, "collection_type", "")).strip().lower() == "neural_continual"

    def _initialize_collection(
        self,
        name: str,
        *,
        collection_type: str,
        config_override: dict[str, Any] | None = None,
    ):
        if collection_type == "neural_continual":
            try:
                from sage.neuromem.memory_collection import NeuralMemoryCollection
            except ImportError as exc:
                raise RuntimeError(
                    "Conversation memory trainable mode requires NeuralMemoryCollection from isage-neuromem."
                ) from exc
            config = self._build_neural_collection_config()
            config.update(dict(config_override or {}))
            config["collection_type"] = "neural_continual"
            return NeuralMemoryCollection(name=name, config=config)

        try:
            from sage.neuromem import UnifiedCollection
        except ImportError as exc:
            raise RuntimeError(
                "Conversation memory requires isage-neuromem to be installed."
            ) from exc

        collection = UnifiedCollection(name, config=dict(config_override or {}))
        collection.config.setdefault("collection_type", "unified")
        index_type = self._default_collection_index_type()
        collection.add_index("search", index_type, self._default_collection_index_config(index_type))
        return collection

    def _load_or_create_collection(self, name: str, snapshot_dir: Path, *, collection_role: str):
        if self._sqlite_has_collection_snapshot(name):
            return self._load_collection_from_sqlite(name, collection_role=collection_role)
        if self._has_collection_snapshot(snapshot_dir):
            collection = self._load_collection_snapshot(
                name,
                snapshot_dir,
                collection_role=collection_role,
            )
            # Migrate one-time legacy JSON snapshot into SQLite-backed persistence.
            self._persist_collection(collection, snapshot_dir)
            return collection
        return self._initialize_collection(
            name,
            collection_type=self._collection_type_for_role(collection_role),
        )

    def _initialize_sqlite_store(self) -> None:
        with sqlite3.connect(self._sqlite_db_path) as connection:
            connection.execute(
                """
                CREATE TABLE IF NOT EXISTS collection_snapshots (
                    name TEXT PRIMARY KEY,
                    raw_data TEXT NOT NULL,
                    config TEXT NOT NULL,
                    index_metadata TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                )
                """
            )
            connection.commit()

    def _sqlite_has_collection_snapshot(self, name: str) -> bool:
        with sqlite3.connect(self._sqlite_db_path) as connection:
            row = connection.execute(
                "SELECT 1 FROM collection_snapshots WHERE name = ? LIMIT 1",
                (name,),
            ).fetchone()
        return row is not None

    def _load_collection_from_sqlite(self, name: str, *, collection_role: str):
        row: tuple[str, str, str] | None
        with sqlite3.connect(self._sqlite_db_path) as connection:
            row = connection.execute(
                """
                SELECT raw_data, config, index_metadata
                FROM collection_snapshots
                WHERE name = ?
                LIMIT 1
                """,
                (name,),
            ).fetchone()

        if row is None:
            return self._initialize_collection(
                name,
                collection_type=self._collection_type_for_role(collection_role),
            )

        raw_data = json.loads(row[0])
        config_payload = json.loads(row[1])
        index_metadata = json.loads(row[2])

        stored_type = str(dict(config_payload).get("collection_type") or "").strip().lower()
        if stored_type not in {"neural_continual", "unified"}:
            stored_type = self._collection_type_for_role(collection_role)

        collection = self._initialize_collection(
            name,
            collection_type=stored_type,
            config_override=dict(config_payload),
        )
        collection.config = dict(config_payload)
        collection.storage.clear()
        for data_id, payload in dict(raw_data).items():
            collection.storage.put(str(data_id), dict(payload))

        if self._is_neural_collection(collection):
            return collection

        collection.indexes = {}
        collection.index_metadata = {}

        for index_name, metadata in dict(index_metadata).items():
            index_type = str(dict(metadata).get("type") or "bm25")
            index_config = self._normalize_index_config(
                index_type,
                dict(dict(metadata).get("config") or {}),
            )
            try:
                collection.add_index(str(index_name), index_type, index_config)
            except ValueError:
                fallback_type = self._default_collection_index_type()
                fallback_config = self._default_collection_index_config(fallback_type)
                collection.add_index(str(index_name), fallback_type, fallback_config)

        if not collection.indexes:
            index_type = self._default_collection_index_type()
            collection.add_index(
                "search",
                index_type,
                self._default_collection_index_config(index_type),
            )

        for data_id in collection.storage.keys():
            for index_name in list(collection.indexes.keys()):
                collection.insert_to_index(str(data_id), index_name)

        return collection

    def _has_collection_snapshot(self, snapshot_dir: Path) -> bool:
        return (snapshot_dir / "raw_data.json").exists() and (
            snapshot_dir / "index_metadata.json"
        ).exists()

    def _load_collection_snapshot(self, name: str, snapshot_dir: Path, *, collection_role: str):
        config_payload: dict[str, Any] = {}
        config_path = snapshot_dir / "config.json"
        if config_path.exists():
            config_payload = json.loads(config_path.read_text(encoding="utf-8"))

        stored_type = str(dict(config_payload).get("collection_type") or "").strip().lower()
        if stored_type not in {"neural_continual", "unified"}:
            stored_type = self._collection_type_for_role(collection_role)

        collection = self._initialize_collection(
            name,
            collection_type=stored_type,
            config_override=dict(config_payload),
        )
        raw_data = json.loads((snapshot_dir / "raw_data.json").read_text(encoding="utf-8"))
        if config_payload:
            collection.config = dict(config_payload)

        collection.storage.clear()
        for data_id, payload in raw_data.items():
            collection.storage.put(str(data_id), dict(payload))

        if self._is_neural_collection(collection):
            return collection

        collection.indexes = {}
        collection.index_metadata = json.loads(
            (snapshot_dir / "index_metadata.json").read_text(encoding="utf-8")
        )

        from sage.neuromem.memory_collection.indexes import IndexFactory

        for index_name, metadata in collection.index_metadata.items():
            index_type = str(metadata["type"])
            index_config = self._normalize_index_config(
                index_type,
                dict(metadata.get("config") or {}),
            )
            index = IndexFactory.create(index_type, index_config)
            index_path = snapshot_dir / f"index_{index_name}"
            if index_path.exists():
                index.load(index_path)
            collection.indexes[index_name] = index

        if "search" not in collection.indexes:
            index_type = self._default_collection_index_type()
            collection.add_index("search", index_type, self._default_collection_index_config(index_type))
            for data_id in collection.storage.keys():
                collection.insert_to_index(str(data_id), "search")
        return collection

    def _persist_collection(self, collection: Any, snapshot_dir: Path) -> None:
        del snapshot_dir
        raw_data = {
            str(data_id): dict(collection.storage.get(data_id) or {})
            for data_id in collection.storage.keys()
        }
        config_payload = dict(getattr(collection, "config", {}) or {})
        collection_type = str(
            config_payload.get("collection_type")
            or getattr(collection, "collection_type", "")
        ).strip().lower()
        if collection_type not in {"neural_continual", "unified"}:
            collection_type = "neural_continual" if self._is_neural_collection(collection) else "unified"
        config_payload["collection_type"] = collection_type
        with sqlite3.connect(self._sqlite_db_path) as connection:
            connection.execute(
                """
                INSERT INTO collection_snapshots (name, raw_data, config, index_metadata, updated_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(name) DO UPDATE SET
                    raw_data = excluded.raw_data,
                    config = excluded.config,
                    index_metadata = excluded.index_metadata,
                    updated_at = excluded.updated_at
                """,
                (
                    str(collection.name),
                    json.dumps(raw_data, ensure_ascii=False),
                    json.dumps(config_payload, ensure_ascii=False),
                    json.dumps(dict(collection.index_metadata), ensure_ascii=False),
                    datetime.now(UTC).isoformat(),
                ),
            )
            connection.commit()

    def _rebuild_runtime_state(self) -> None:
        self._records.clear()
        self._profiles.clear()
        self._conversation_timelines.clear()
        self._load_records_from_collection()
        self._load_profiles_from_collection()

    def _load_records_from_collection(self) -> None:
        for data_id in self._conversation_collection.storage.keys():
            payload = self._conversation_collection.storage.get(data_id)
            metadata = dict((payload or {}).get("metadata") or {})
            if str(metadata.get("entry_kind") or "") != "conversation_record":
                continue
            record_payload = metadata.get("record_payload")
            if not isinstance(record_payload, dict):
                raise RuntimeError(
                    "Conversation memory snapshot entry is missing record_payload metadata."
                )
            record = ConversationMemoryRecord.from_dict(record_payload)
            self._records[record.memory_id] = record
            self._append_conversation_timeline(record)

    def _load_profiles_from_collection(self) -> None:
        for data_id in self._profile_collection.storage.keys():
            payload = self._profile_collection.storage.get(data_id)
            metadata = dict((payload or {}).get("metadata") or {})
            if str(metadata.get("entry_kind") or "") != "profile_record":
                continue
            profile_payload = metadata.get("profile_payload")
            if not isinstance(profile_payload, dict):
                raise RuntimeError(
                    "Profile memory snapshot entry is missing profile_payload metadata."
                )
            profile = ProfileMemoryRecord.from_dict(profile_payload)
            self._profiles[profile.profile_id] = profile

    def _migrate_legacy_disk_layout(self) -> bool:
        migrated = False
        for path in self._legacy_conversation_record_paths():
            payload = self._read_json_file(path)
            if payload is None:
                continue
            try:
                record = ConversationMemoryRecord.from_dict(payload)
            except Exception:
                continue
            if record.memory_id not in self._records:
                self._records[record.memory_id] = record
                self._prepend_conversation_timeline(record)
                self._store_conversation_entry(record)
                migrated = True
            path.unlink(missing_ok=True)

        for path in self._legacy_profile_record_paths():
            payload = self._read_json_file(path)
            if payload is None:
                continue
            try:
                profile = ProfileMemoryRecord.from_dict(payload)
            except Exception:
                continue
            if profile.profile_id not in self._profiles:
                self._profiles[profile.profile_id] = profile
                self._store_profile_entry(profile)
                migrated = True
            path.unlink(missing_ok=True)

        self._cleanup_legacy_directory(self._base_dir / "records")
        self._cleanup_legacy_directory(self._base_dir / "profiles")

        if migrated:
            self._persist_collection(
                self._conversation_collection, self._conversation_collection_dir
            )
            self._persist_collection(self._profile_collection, self._profile_collection_dir)
        return migrated

    def _legacy_conversation_record_paths(self) -> list[Path]:
        legacy_paths: list[Path] = []
        records_dir = self._base_dir / "records"
        if records_dir.exists():
            legacy_paths.extend(sorted(records_dir.glob("*.json")))
        legacy_paths.extend(sorted(path for path in self._base_dir.glob("*.json")))
        return legacy_paths

    def _legacy_profile_record_paths(self) -> list[Path]:
        profiles_dir = self._base_dir / "profiles"
        if not profiles_dir.exists():
            return []
        return sorted(profiles_dir.glob("*.json"))

    def _read_json_file(self, path: Path) -> dict[str, object] | None:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return None
        return payload if isinstance(payload, dict) else None

    def _cleanup_legacy_directory(self, path: Path) -> None:
        if not path.exists():
            return
        if any(path.iterdir()):
            raise RuntimeError(f"Legacy memory directory must be empty after migration: {path}")
        path.rmdir()

    def _record_telemetry_event(
        self,
        event_type: str,
        *,
        collection_name: str,
        duration_ms: float | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        event = TelemetryEvent(
            event_type=event_type,
            service_type=self.__class__.__name__,
            collection_name=collection_name,
            timestamp=time.time(),
            duration_ms=duration_ms,
            attributes=dict(attributes or {}),
        )
        payload = event.to_dict()
        self._telemetry_events.append(payload)
        self._telemetry_type_counts[event_type] += 1
        if self._telemetry_limit > 0 and len(self._telemetry_events) > self._telemetry_limit:
            overflow = len(self._telemetry_events) - self._telemetry_limit
            if overflow > 0:
                removed = self._telemetry_events[:overflow]
                self._telemetry_events = self._telemetry_events[overflow:]
                for item in removed:
                    removed_type = str(item.get("event_type") or "")
                    if not removed_type:
                        continue
                    next_count = self._telemetry_type_counts[removed_type] - 1
                    if next_count <= 0:
                        self._telemetry_type_counts.pop(removed_type, None)
                    else:
                        self._telemetry_type_counts[removed_type] = next_count
        return payload

    def record_memory_usefulness(
        self,
        *,
        conversation_id: str,
        signal: str,
        reason: str,
        memory_used: bool,
        knowledge_used: bool,
        memory_hit_count: int,
        short_term_hit_count: int,
        long_term_hit_count: int,
        knowledge_hit_count: int,
        top_memory_score: float | None,
        workflow_action: str,
        duration_ms: float | None = None,
    ) -> dict[str, Any]:
        return self._record_telemetry_event(
            "score_memory_usefulness",
            collection_name=self._conversation_collection.name,
            duration_ms=duration_ms,
            attributes={
                "conversation_id": conversation_id,
                "signal": signal,
                "reason": reason,
                "memory_used": memory_used,
                "knowledge_used": knowledge_used,
                "memory_hit_count": memory_hit_count,
                "short_term_hit_count": short_term_hit_count,
                "long_term_hit_count": long_term_hit_count,
                "knowledge_hit_count": knowledge_hit_count,
                "top_memory_score": top_memory_score,
                "workflow_action": workflow_action,
            },
        )

    def _build_service_stats(self, collection: Any, *, memory_scope: str) -> dict[str, Any]:
        storage_stats: dict[str, Any] = dict(collection.get_storage_stats())
        collection_type = str(
            getattr(collection, "collection_type", "")
            or dict(getattr(collection, "config", {}) or {}).get("collection_type")
            or "unified"
        ).strip().lower()
        if collection_type not in {"neural_continual", "unified"}:
            collection_type = "unified"

        service_type = self.__class__.__name__
        if memory_scope in {"conversation", "short_term"} and collection_type == "neural_continual":
            service_type = "online_continual_memory"

        stats = ServiceStats(
            service_type=service_type,
            collection_name=collection.name,
            total_entries=collection.size(),
            index_count=len(collection.indexes),
            indexes=collection.list_indexes(),
            storage=storage_stats,
            extra={
                "memory_scope": memory_scope,
                "collection_type": collection_type,
                "telemetry": self.get_telemetry_summary(),
            },
        )
        return stats.to_dict()

    def _insert_collection_entry(self, collection: Any, entry: MemoryEntry) -> None:
        if self._is_neural_collection(collection):
            collection.insert(entry.text, entry.metadata)
            return
        collection.insert(entry.text, entry.metadata, index_names=["search"])

    def _retrieve_from_collection(
        self,
        collection: Any,
        query_request: QueryRequest,
    ) -> list[RetrievalResult]:
        if self._is_neural_collection(collection):
            return collection.retrieve(
                query=query_request.query,
                top_k=query_request.top_k,
                filters=query_request.filters,
            )
        return collection.retrieve(
            "search",
            query_request.query,
            top_k=query_request.top_k,
        )

    def _should_include_record(
        self,
        record: ConversationMemoryRecord,
        request: ChatRequest,
        conversation_id: str,
    ) -> bool:
        if record.conversation_id == conversation_id:
            return True
        if request.student_email and record.student_email == request.student_email:
            return True
        if request.student_name.strip().lower() == "guest":
            return False
        return record.student_name == request.student_name

    def _derive_profiles(self, record: ConversationMemoryRecord) -> list[ProfileMemoryRecord]:
        student_key = self._student_key(record.student_name, record.student_email)
        if student_key is None:
            return []

        suggestions = self._profile_summarizer.summarize(record)
        return [
            ProfileMemoryRecord(
                profile_id=self._profile_id(student_key, suggestion.category),
                student_key=student_key,
                student_name=record.student_name,
                student_email=record.student_email,
                category=suggestion.category,
                summary=suggestion.summary,
                evidence=suggestion.evidence,
                updated_at=record.created_at,
            )
            for suggestion in suggestions
        ]

    def _upsert_profile(self, profile: ProfileMemoryRecord) -> None:
        self._profiles[profile.profile_id] = profile
        self._delete_profile_entries(profile.profile_id)
        self._store_profile_entry(profile)

    def _append_conversation_timeline(self, record: ConversationMemoryRecord) -> None:
        timeline = self._conversation_timelines.setdefault(record.conversation_id, [])
        timeline.append(record.memory_id)
        timeline.sort(key=lambda memory_id: self._records[memory_id].created_at, reverse=True)

    def _prepend_conversation_timeline(self, record: ConversationMemoryRecord) -> None:
        timeline = self._conversation_timelines.setdefault(record.conversation_id, [])
        if record.memory_id in timeline:
            timeline.remove(record.memory_id)
        timeline.insert(0, record.memory_id)

    def _build_conversation_memory_entry(self, record: ConversationMemoryRecord):
        metadata = {
            "entry_kind": "conversation_record",
            "memory_id": record.memory_id,
            "conversation_id": record.conversation_id,
            "student_name": record.student_name,
            "student_email": record.student_email or "",
            "source": "user_message",
            "topic": record.interaction_domain or record.workflow_action or "conversation_exchange",
            "session_id": record.conversation_id,
            "importance": self._conversation_importance(record),
            "timestamp": record.created_at.isoformat(),
            "memory_scope": "short_term",
            "record_payload": record.to_dict(),
        }
        if record.course_context:
            metadata["course_context"] = record.course_context
        if record.booking_summary:
            metadata["booking_summary"] = record.booking_summary
        return MemoryEntry(
            entry_id=record.memory_id,
            text=record.retrieval_text(),
            metadata=metadata,
            created_at=record.created_at.timestamp(),
            updated_at=record.created_at.timestamp(),
        )

    def _build_attachment_memory_entries(self, record: ConversationMemoryRecord):
        entries = []
        for index, attachment in enumerate(record.attachments):
            metadata = {
                "entry_kind": "attachment_excerpt",
                "memory_id": record.memory_id,
                "conversation_id": record.conversation_id,
                "student_name": record.student_name,
                "student_email": record.student_email or "",
                "source": "attachment_excerpt",
                "topic": "artifact_memory",
                "session_id": record.conversation_id,
                "importance": "high",
                "timestamp": record.created_at.isoformat(),
                "memory_scope": "short_term",
                "attachment_index": index,
                "attachment_name": attachment.file_name,
                "attachment_media_type": attachment.media_type,
            }
            if record.course_context:
                metadata["course_context"] = record.course_context
            entries.append(
                MemoryEntry(
                    entry_id=f"{record.memory_id}:artifact:{index + 1}",
                    text=self._attachment_retrieval_text(record, attachment),
                    metadata=metadata,
                    created_at=record.created_at.timestamp(),
                    updated_at=record.created_at.timestamp(),
                )
            )
        return entries

    def _build_profile_memory_entry(self, profile: ProfileMemoryRecord):
        metadata = {
            "entry_kind": "profile_record",
            "profile_id": profile.profile_id,
            "student_key": profile.student_key,
            "student_name": profile.student_name,
            "student_email": profile.student_email or "",
            "category": profile.category,
            "source": "system_event",
            "topic": profile.category,
            "session_id": profile.student_key,
            "importance": self._profile_importance(profile),
            "timestamp": profile.updated_at.isoformat(),
            "memory_scope": "long_term",
            "profile_payload": profile.to_dict(),
        }
        return MemoryEntry(
            entry_id=profile.profile_id,
            text=profile.retrieval_text(),
            metadata=metadata,
            created_at=profile.updated_at.timestamp(),
            updated_at=profile.updated_at.timestamp(),
        )

    def _store_conversation_entry(self, record: ConversationMemoryRecord) -> None:
        entry = self._build_conversation_memory_entry(record)
        self._insert_collection_entry(self._conversation_collection, entry)
        for attachment_entry in self._build_attachment_memory_entries(record):
            self._insert_collection_entry(self._conversation_collection, attachment_entry)

    def _store_profile_entry(self, profile: ProfileMemoryRecord) -> None:
        entry = self._build_profile_memory_entry(profile)
        self._insert_collection_entry(self._profile_collection, entry)

    def _delete_profile_entries(self, profile_id: str, *, keep_data_id: str | None = None) -> int:
        deleted = 0
        for data_id in list(self._profile_collection.storage.keys()):
            payload = self._profile_collection.storage.get(data_id)
            metadata = dict((payload or {}).get("metadata") or {})
            if str(metadata.get("entry_kind") or "") != "profile_record":
                continue
            if str(metadata.get("profile_id") or "") != profile_id:
                continue
            if keep_data_id is not None and str(data_id) == keep_data_id:
                continue
            if self._profile_collection.delete(str(data_id)):
                deleted += 1
        return deleted

    def _canonicalize_profile_collection(self) -> bool:
        latest_by_profile_id: dict[str, tuple[str, ProfileMemoryRecord]] = {}
        duplicate_data_ids: list[str] = []

        for data_id in list(self._profile_collection.storage.keys()):
            payload = self._profile_collection.storage.get(data_id)
            metadata = dict((payload or {}).get("metadata") or {})
            if str(metadata.get("entry_kind") or "") != "profile_record":
                continue
            profile_payload = metadata.get("profile_payload")
            if not isinstance(profile_payload, dict):
                continue
            profile = ProfileMemoryRecord.from_dict(profile_payload)
            current = latest_by_profile_id.get(profile.profile_id)
            if current is None:
                latest_by_profile_id[profile.profile_id] = (str(data_id), profile)
                continue
            current_data_id, current_profile = current
            if profile.updated_at >= current_profile.updated_at:
                duplicate_data_ids.append(current_data_id)
                latest_by_profile_id[profile.profile_id] = (str(data_id), profile)
            else:
                duplicate_data_ids.append(str(data_id))

        if not duplicate_data_ids:
            return False

        for data_id in duplicate_data_ids:
            self._profile_collection.delete(data_id)
        self._persist_collection(self._profile_collection, self._profile_collection_dir)
        return True

    def _build_short_term_query_request(
        self,
        request: ChatRequest,
        *,
        conversation_id: str,
        limit: int,
    ):
        query_parts = [request.question]
        if request.course_context:
            query_parts.append(request.course_context)
        if request.student_email:
            query_parts.append(request.student_email)
        elif request.student_name.strip().lower() != "guest":
            query_parts.append(request.student_name)

        return QueryRequest(
            query=" ".join(query_parts),
            top_k=max(limit * 3, limit),
            filters={
                "memory_scope": "short_term",
                "conversation_id": conversation_id,
                "student_email": request.student_email or "",
            },
            hints={
                "purpose": "recent_context_recall",
                "conversation_id": conversation_id,
                "course_context": request.course_context or "",
            },
        )

    def _build_artifact_query_request(self, request: ChatRequest, *, limit: int):
        query_parts = [request.question]
        if request.course_context:
            query_parts.append(request.course_context)
        if request.student_email:
            query_parts.append(request.student_email)
        elif request.student_name.strip().lower() != "guest":
            query_parts.append(request.student_name)

        return QueryRequest(
            query=" ".join(query_parts),
            top_k=max(limit * 4, limit),
            filters={
                "memory_scope": "short_term",
                "source": "attachment_excerpt",
            },
            hints={
                "purpose": "artifact_context_recall",
                "course_context": request.course_context or "",
            },
        )

    def _build_long_term_query_request(
        self,
        request: ChatRequest,
        *,
        student_key: str,
        limit: int,
    ):
        query_parts = [request.question, request.student_name]
        if request.course_context:
            query_parts.append(request.course_context)
        if request.student_email:
            query_parts.append(request.student_email)

        return QueryRequest(
            query=" ".join(query_parts),
            top_k=max(limit * 3, limit),
            filters={
                "memory_scope": "long_term",
                "student_key": student_key,
            },
            hints={
                "purpose": "stable_profile_recall",
                "preferred_categories": self._preferred_profile_categories(request.question),
            },
        )

    def _coerce_retrieval_results(self, results: list[dict[str, Any]]):
        coerced = []
        for rank, result in enumerate(results, start=1):
            payload = dict(result)
            metadata = dict(payload.get("metadata") or {})
            if "id" not in payload:
                payload["id"] = metadata.get("memory_id") or metadata.get("profile_id") or ""
            coerced.append(RetrievalResult.from_service_record(payload, rank=rank))
        return coerced

    def _conversation_importance(self, record: ConversationMemoryRecord) -> str:
        if record.booking_summary or record.workflow_action in {
            "book_meeting",
            "advise_only",
            "human_handoff",
        }:
            return "high"
        if record.student_email or record.course_context or record.knowledge_hit_count > 0:
            return "medium"
        return "low"

    def _profile_importance(self, profile: ProfileMemoryRecord) -> str:
        if profile.category in {
            "identity",
            "booking_preference",
            "collaboration_preference",
        }:
            return "high"
        if profile.category in {"course_context", "recent_topic"}:
            return "medium"
        return "low"

    def _format_profile_summary(self, profile: ProfileMemoryRecord) -> str:
        timestamp = profile.updated_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        return f"[{timestamp}] {profile.summary}\n证据：{profile.evidence}"

    def _student_key(self, student_name: str, student_email: str | None) -> str | None:
        if student_email:
            return student_email.strip().lower()
        normalized_name = student_name.strip().lower()
        if not normalized_name or normalized_name == "guest":
            return None
        return normalized_name

    def _profile_id(self, student_key: str, category: str) -> str:
        safe_key = re.sub(r"[^a-z0-9]+", "-", student_key.lower()).strip("-") or "profile"
        return f"{safe_key}-{category}"

    def _extract_attachment_records(
        self, attachments: list[ChatAttachment]
    ) -> list[AttachmentMemoryRecord]:
        records: list[AttachmentMemoryRecord] = []
        for attachment in attachments:
            excerpt = self._clip_attachment_excerpt(attachment.text_content)
            if not excerpt:
                continue
            records.append(
                AttachmentMemoryRecord(
                    file_name=attachment.file_name,
                    media_type=attachment.media_type,
                    text_excerpt=excerpt,
                    size_bytes=attachment.size_bytes,
                )
            )
        return records

    def _attachment_retrieval_text(
        self, record: ConversationMemoryRecord, attachment: AttachmentMemoryRecord
    ) -> str:
        parts = [
            f"student {record.student_name}",
            f"question {record.question}",
            f"attachment {attachment.file_name}",
            f"media_type {attachment.media_type}",
            f"attachment_excerpt {attachment.text_excerpt}",
        ]
        if record.student_email:
            parts.append(f"email {record.student_email}")
        if record.course_context:
            parts.append(f"course {record.course_context}")
        return " ".join(parts)

    def _format_attachment_summary(
        self,
        record: ConversationMemoryRecord,
        attachment: AttachmentMemoryRecord,
        attachment_index: int,
    ) -> str:
        timestamp = record.created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        return (
            f"[{timestamp}] 材料 {attachment_index + 1}：{attachment.file_name}（{attachment.media_type}）。"
            f" 摘要：{attachment.text_excerpt}"
        )

    def _rank_artifact_candidates(
        self,
        request: ChatRequest,
        *,
        conversation_id: str,
    ) -> list[tuple[float, ConversationMemoryRecord, int, AttachmentMemoryRecord]]:
        query_tokens = self._artifact_tokens(request.question)
        candidates: list[tuple[float, ConversationMemoryRecord, int, AttachmentMemoryRecord]] = []

        for record in self._records.values():
            if not record.attachments or not self._should_include_record(
                record, request, conversation_id
            ):
                continue
            for attachment_index, attachment in enumerate(record.attachments):
                score = self._artifact_overlap_score(query_tokens, record, attachment)
                if score <= 0.0:
                    continue
                candidates.append((score, record, attachment_index, attachment))

        candidates.sort(key=lambda item: (item[0], item[1].created_at.timestamp()), reverse=True)
        return candidates

    def _artifact_overlap_score(
        self,
        query_tokens: set[str],
        record: ConversationMemoryRecord,
        attachment: AttachmentMemoryRecord,
    ) -> float:
        if not query_tokens:
            return 0.0

        attachment_tokens = self._artifact_tokens(
            " ".join(
                [
                    attachment.file_name,
                    attachment.media_type,
                    attachment.text_excerpt,
                    record.question,
                ]
            )
        )
        overlap = query_tokens & attachment_tokens
        if not overlap:
            return 0.0

        file_name_tokens = self._artifact_tokens(attachment.file_name)
        file_overlap = len(query_tokens & file_name_tokens)
        return float(len(overlap) + file_overlap + 1)

    def _artifact_tokens(self, text: str) -> set[str]:
        normalized = text.lower()
        tokens = {
            token
            for token in re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]+", normalized)
            if len(token) >= 2
        }
        return tokens

    def _clip_attachment_excerpt(self, text: str, limit: int = 1200) -> str:
        normalized = re.sub(r"\s+", " ", text).strip()
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: max(limit - 1, 0)].rstrip()}…"

    def _format_summary(self, record: ConversationMemoryRecord) -> str:
        timestamp = record.created_at.astimezone(UTC).strftime("%Y-%m-%d %H:%M UTC")
        lines = [
            f"[{timestamp}] 问：{record.question}",
            f"答：{record.answer}",
        ]
        if record.booking_summary:
            lines.append(f"预约：{record.booking_summary}")
        return "\n".join(lines)

    def _booking_summary(self, booking_result: BookingResponse | None) -> str | None:
        if booking_result is None:
            return None
        if booking_result.booking is None:
            return booking_result.message
        return (
            f"{booking_result.message} | {booking_result.booking.topic} | "
            f"{booking_result.booking.start_at.isoformat()}"
        )
