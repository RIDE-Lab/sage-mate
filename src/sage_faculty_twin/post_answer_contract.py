"""Versioned data boundary for post-answer work, never a replay of the chat DAG.

Only the enumerated post-answer inputs cross this boundary. Executable stage
ports, prompts, credentials, booking state and request-local Slack history are
not task inputs. The already validated response is the immutable answer source.
"""

from __future__ import annotations

from datetime import UTC, datetime
import hashlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .memory_store import ConversationMemoryHit, ConversationMemoryRecord, ProfileMemoryRecord
from .models import AvailabilitySchedule, ChatRequest, ChatResponse, InteractionIntent


class ArtifactDraftIntent(BaseModel):
    """Only captured artifact evidence; turn identity comes from the task."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    artifact_names: list[str] = Field(min_length=1)
    artifact_sources: list[str]
    artifact_excerpt_count: int = Field(ge=0)
    provenance_note: str


class DigestIntent(BaseModel):
    """Captured generation policy; conversation identity comes from the owner task."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    threshold: int = Field(ge=1)
    target_chars: int = Field(ge=1)
    use_llm: bool


class DigestWorkTask(BaseModel):
    """Manual refresh uses the same durable worker and digest destination."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    version: Literal[1] = 1
    task_kind: Literal["context_digest"] = "context_digest"
    request_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    owner_id: str | None = Field(default=None, pattern=r"^(account|api_token|guest|slack):[A-Za-z0-9_-]{1,128}$")
    intent: DigestIntent

    @model_validator(mode="after")
    def validate_identity(self):
        if any(value != value.strip() for value in (self.request_id, self.conversation_id)):
            raise ValueError("digest_task_identity_not_trimmed")
        return self

    def journal_inputs(self):
        snapshot = type(self).model_validate_json(self.model_dump_json()).model_dump(mode="json")
        return {"result": {"conversation_id": self.conversation_id, "owner_id": self.owner_id},
                "tasks": {"context_digest": snapshot}}


class DeliveryIntent(BaseModel):
    """Server-captured channel identity, never accepted from a chat body."""

    model_config = ConfigDict(extra="forbid", frozen=True)
    owner_id: str = Field(pattern=r"^(account|api_token|guest|slack):[A-Za-z0-9_-]{1,128}$")
    request_id: str = Field(min_length=1)

    @property
    def stream_id(self):
        return hashlib.sha256(json.dumps([self.owner_id, self.request_id], separators=(",", ":")).encode()).hexdigest()


class PostAnswerTask(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    version: Literal[1] = 1
    task_kind: Literal["post_answer"] = "post_answer"
    request_id: str = Field(min_length=1)
    request: ChatRequest
    response: ChatResponse
    record: ConversationMemoryRecord | None = None
    interaction_intent: InteractionIntent | None = None
    memory_hits: list[ConversationMemoryHit] = Field(default_factory=list)
    recent_session_context: str = ""
    profile_snapshot: list[ProfileMemoryRecord] = Field(default_factory=list)
    availability: AvailabilitySchedule
    evaluate_memory: bool
    score_skip_reason: Literal["planner", "current_date"] = "planner"
    evaluated_at: datetime
    skip_profile: bool = False
    skip_follow_up: bool = False
    artifact_draft: ArtifactDraftIntent | None = None
    digest: DigestIntent | None = None
    delivery: DeliveryIntent | None = None

    @model_validator(mode="after")
    def validate_identity(self):
        if self.request_id != self.request_id.strip():
            raise ValueError("post_answer_request_id_not_trimmed")
        if self.delivery is not None and self.delivery.owner_id != self.request.owner_id:
            raise ValueError("post_answer_delivery_owner_conflict")
        if not self.response.conversation_id:
            raise ValueError("post_answer_missing_conversation")
        if self.request.slack_channel_context is not None:
            raise ValueError("post_answer_ephemeral_slack_context_forbidden")
        if self.request.conversation_id not in (None, self.response.conversation_id):
            raise ValueError("post_answer_conversation_conflict")
        if self.evaluated_at.tzinfo is None or self.evaluated_at.utcoffset() is None:
            raise ValueError("post_answer_evaluation_time_must_be_aware")
        if any(profile.owner_id != self.request.owner_id for profile in self.profile_snapshot):
            raise ValueError("post_answer_profile_owner_conflict")
        if self.record is not None:
            if (self.record.memory_id != self.request_id
                    or self.response.exchange_id != self.request_id
                    or self.record.conversation_id != self.response.conversation_id
                    or self.record.question != self.request.question
                    or self.record.student_name != self.request.student_name
                    or self.record.student_email != self.request.student_email
                    or self.record.owner_id != self.request.owner_id
                    or self.record.workflow_action != self.response.workflow_action
                    or self.record.answer != self.response.answer):
                raise ValueError("post_answer_record_identity_conflict")
        elif self.response.exchange_id is not None or self.response.memory_write_back:
            raise ValueError("post_answer_missing_committed_record")
        if self.artifact_draft is not None and self.record is None:
            raise ValueError("artifact_draft_requires_source_record")
        if self.digest is not None and self.record is None:
            raise ValueError("digest_requires_source_record")
        return self

    @classmethod
    def capture(cls, *, request_id, request, response, availability,
                evaluate_memory, record=None, interaction_intent=None,
                memory_hits=(), recent_session_context="", profile_snapshot=(),
                skip_profile=False, skip_follow_up=False, evaluated_at=None,
                score_skip_reason="planner", artifact_draft=None, digest=None, delivery=None):
        # Use the request's explicit serialization policy, then detach every
        # nested object through JSON before handing it to a durable worker.
        task = cls(
            request_id=request_id, request=ChatRequest.model_validate(request.model_dump(mode="json")),
            response=response, availability=availability, evaluate_memory=evaluate_memory,
            record=record, interaction_intent=interaction_intent, memory_hits=list(memory_hits),
            recent_session_context=recent_session_context, profile_snapshot=list(profile_snapshot),
            skip_profile=skip_profile, skip_follow_up=skip_follow_up,
            score_skip_reason=score_skip_reason,
            artifact_draft=artifact_draft,
            digest=digest,
            delivery=delivery,
            evaluated_at=evaluated_at or datetime.now(UTC),
        )
        return cls.model_validate_json(task.model_dump_json())

    def journal_inputs(self):
        """Inputs to SAGE WorkJournal.commit; no alternative persistence layer."""
        # Nested Pydantic/dataclass values can be mutated despite frozen=True.
        # Revalidate at the actual persistence boundary, not just at capture.
        snapshot = type(self).model_validate_json(self.model_dump_json()).model_dump(mode="json")
        return {"result": snapshot["response"], "tasks": {"post_answer": snapshot}}
