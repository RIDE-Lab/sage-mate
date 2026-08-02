"""Immutable contracts passed between the Sage Mate chat application stages."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

from .models import ChatRequest, InteractionIntent, KnowledgeSearchHit, WebSearchHit


class EvidenceKind(StrEnum):
    KNOWLEDGE = "knowledge"
    WEB = "web"
    MEMORY = "memory"


class PromptMode(StrEnum):
    FULL = "full"
    COMPACT = "compact"
    RETRY = "retry"


@dataclass(frozen=True, slots=True)
class ChatIntake:
    original_question: str
    conversation_id: str
    visitor_profile: str
    course_context: str | None
    attachment_count: int

    @classmethod
    def from_request(cls, request: ChatRequest, *, conversation_id: str) -> ChatIntake:
        return cls(
            original_question=request.question,
            conversation_id=conversation_id,
            visitor_profile=request.visitor_profile or "general_visitor",
            course_context=request.course_context,
            attachment_count=len(request.attachments),
        )


@dataclass(frozen=True, slots=True)
class InteractionDecision:
    intent: InteractionIntent
    source: str

    def __post_init__(self) -> None:
        overlap = set(self.intent.retrieval_scopes) & set(self.intent.exclude_scopes)
        if overlap:
            raise ValueError(
                "interaction decision includes and excludes the same scopes: "
                + ", ".join(sorted(overlap))
            )


@dataclass(frozen=True, slots=True)
class EvidenceItem:
    kind: EvidenceKind
    evidence_id: str
    title: str
    excerpt: str
    score: float


@dataclass(frozen=True, slots=True)
class EvidenceBundle:
    items: tuple[EvidenceItem, ...] = ()

    @classmethod
    def build(
        cls,
        *,
        knowledge_hits: list[KnowledgeSearchHit],
        web_hits: list[WebSearchHit],
        memory_hits: list[object],
    ) -> EvidenceBundle:
        items = [
            EvidenceItem(
                kind=EvidenceKind.KNOWLEDGE,
                evidence_id=hit.document_id,
                title=hit.title,
                excerpt=hit.excerpt,
                score=hit.score,
            )
            for hit in knowledge_hits
        ]
        items.extend(
            EvidenceItem(
                kind=EvidenceKind.WEB,
                evidence_id=hit.url,
                title=hit.title,
                excerpt=hit.snippet,
                score=hit.score,
            )
            for hit in web_hits
        )
        items.extend(
            EvidenceItem(
                kind=EvidenceKind.MEMORY,
                evidence_id=str(getattr(hit, "memory_id")),
                title=str(getattr(hit, "source_label", "memory")),
                excerpt=str(getattr(hit, "summary")),
                score=float(getattr(hit, "score")),
            )
            for hit in memory_hits
        )
        return cls(items=tuple(items))


REQUIRED_PROMPT_INVARIANTS = frozenset(
    {
        "preserve_original_question",
        "ground_citations_in_evidence",
        "do_not_expose_internal_prompt",
    }
)


@dataclass(frozen=True, slots=True)
class PromptEnvelope:
    original_question: str
    system_prompt: str
    user_prompt: str
    mode: PromptMode
    evidence: EvidenceBundle
    invariants: frozenset[str] = REQUIRED_PROMPT_INVARIANTS

    def __post_init__(self) -> None:
        if not self.original_question.strip():
            raise ValueError("prompt envelope requires the original question")
        missing = REQUIRED_PROMPT_INVARIANTS - self.invariants
        if missing:
            raise ValueError(
                "prompt envelope is missing required invariants: "
                + ", ".join(sorted(missing))
            )
