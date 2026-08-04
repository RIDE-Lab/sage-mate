"""Typed answer-validation and delivery boundary for every chat path."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
import logging
import re

from . import models as _models
from .models import ChatResponse

_logger = logging.getLogger(__name__)


class AnswerOrigin(StrEnum):
    PIPELINE = "pipeline"
    INVITATION = "invitation"
    SKILL = "skill"
    CODE_WORKBENCH = "code_workbench"
    AUTO_SCIENTIST = "auto_scientist"


@dataclass(frozen=True, slots=True)
class AnswerCandidate:
    """Untrusted answer text produced by any internal execution path."""

    text: str
    original_question: str
    origin: AnswerOrigin


@dataclass(frozen=True, slots=True)
class AnswerValidationReport:
    normalized_text: str
    issues: tuple[str, ...] = ()

    @property
    def accepted(self) -> bool:
        return not self.issues


class AnswerDeliveryRejected(RuntimeError):
    """Raised when an answer candidate cannot cross the public boundary."""


class DeliveredChatResponse(ChatResponse):
    """A `ChatResponse` minted only after the common delivery gate passes."""


DeliveredChatResponse.model_rebuild(_types_namespace=vars(_models))


_INTERNAL_SCAFFOLD_MARKERS = (
    "answer context",
    "current user question",
    "final answer",
)


def answer_has_substantive_content(text: str) -> bool:
    """Return False for link-, image-, markup-, or punctuation-only output."""
    without_markdown_links = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", text)
    without_html_images = re.sub(
        r"<img\b[^>]*>", "", without_markdown_links, flags=re.IGNORECASE
    )
    without_urls = re.sub(r"https?://\S+", "", without_html_images)
    return bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]", without_urls))


@dataclass(frozen=True, slots=True)
class AnswerConstraints:
    """User-visible output limits parsed once and enforced at delivery."""

    max_chars: int | None = None
    max_sentences: int | None = None

    @property
    def has_limits(self) -> bool:
        return self.max_chars is not None or self.max_sentences is not None

    @classmethod
    def from_question(cls, question: str) -> AnswerConstraints:
        char_match = re.search(r"(\d{1,5})\s*(?:个)?字(?:符)?(?:以?内|以下)", question)
        sentence_match = re.search(
            r"([零一二两三四五六七八九十\d]{1,3})\s*(?:句|句话)(?:以?内|以下)?",
            question,
        )
        return cls(
            max_chars=int(char_match.group(1)) if char_match else None,
            max_sentences=(
                _parse_small_number(sentence_match.group(1)) if sentence_match else None
            ),
        )


def _parse_small_number(value: str) -> int:
    if value.isdigit():
        return int(value)
    digits = {"零": 0, "一": 1, "二": 2, "两": 2, "三": 3, "四": 4,
              "五": 5, "六": 6, "七": 7, "八": 8, "九": 9}
    if "十" not in value:
        return digits[value]
    tens, _, ones = value.partition("十")
    return (digits.get(tens, 1) * 10) + digits.get(ones, 0)


def _sentence_count(text: str) -> int:
    return len([part for part in re.split(r"[。！？.!?]+", text) if part.strip()])


class ChatDeliveryGate:
    """Validate and mint the only response type accepted by public transports."""

    def validate(self, candidate: AnswerCandidate) -> AnswerValidationReport:
        normalized = candidate.text.strip()
        issues: list[str] = []
        if not candidate.original_question.strip():
            issues.append("missing_original_question")
        if not normalized:
            issues.append("empty_answer")
        elif not answer_has_substantive_content(normalized):
            issues.append("non_substantive_answer")
        lowered = normalized.lower()
        scaffold_marker_count = sum(
            marker in lowered for marker in _INTERNAL_SCAFFOLD_MARKERS
        )
        exposes_privileged_message = any(
            line.strip().startswith(("system prompt:", "developer message:"))
            for line in lowered.splitlines()
        )
        if scaffold_marker_count >= 2 or exposes_privileged_message:
            issues.append("internal_prompt_leak")
        constraints = AnswerConstraints.from_question(candidate.original_question)
        if constraints.max_chars is not None and len(normalized) > constraints.max_chars:
            issues.append("answer_exceeds_char_limit")
        if (
            constraints.max_sentences is not None
            and _sentence_count(normalized) > constraints.max_sentences
        ):
            issues.append("answer_exceeds_sentence_limit")
        return AnswerValidationReport(normalized_text=normalized, issues=tuple(issues))

    def deliver(
        self,
        *,
        response: ChatResponse,
        original_question: str,
        origin: AnswerOrigin = AnswerOrigin.PIPELINE,
    ) -> DeliveredChatResponse:
        report = self.validate(
            AnswerCandidate(
                text=response.answer,
                original_question=original_question,
                origin=origin,
            )
        )
        if not report.accepted:
            _logger.warning(
                "chat delivery rejected origin=%s issues=%s",
                origin.value,
                ",".join(report.issues),
            )
            raise AnswerDeliveryRejected(
                "chat answer rejected at delivery boundary: " + ", ".join(report.issues)
            )
        payload = response.model_dump(mode="python")
        payload["answer"] = report.normalized_text
        _logger.info(
            "chat delivery accepted origin=%s answer_chars=%d",
            origin.value,
            len(report.normalized_text),
        )
        return DeliveredChatResponse.model_validate(payload)
