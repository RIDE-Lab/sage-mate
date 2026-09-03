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

_PROMPT_LEAK_MARKERS = (
    "response instructions",
    "expert response instructions",
    "fast-answer guidance",
    "deep-answer guidance",
    "request context:",
    "student name:",
    "visitor profile:",
    "specific instruction details are not disclosed",
    "my apologies for the roundabout question",
    "my name is zhang, and i am a digital assistant",
    "questions about my operational limits",
    "please allow me to assist you further",
    "based on the current conversation context",
    "there is no new specific request",
    "action-oriented answer in the user's language",
    "action-oriented answer in user's language",
    "omit generic introductions and repeated background",
    "700 个汉字以内",
    "基于课题组公开资料和知识库为您提供学术答疑",
    "我的回答基于课题组公开资料和知识库，具体指令细节不便透露",
    "这类内部信息不便在此讨论",
    "用户已开启深度思考",
    "只展示结论，不展示思维链",
)


def answer_has_substantive_content(text: str) -> bool:
    """Return False for link-, image-, markup-, or punctuation-only output."""
    without_markdown_links = re.sub(r"!?\[[^\]]*\]\([^)]*\)", "", text)
    without_html_images = re.sub(
        r"<img\b[^>]*>", "", without_markdown_links, flags=re.IGNORECASE
    )
    without_urls = re.sub(r"https?://\S+", "", without_html_images)
    return bool(re.search(r"[A-Za-z0-9\u4e00-\u9fff]", without_urls))


def answer_contains_prompt_leak(text: str | None) -> bool:
    if not text:
        return False
    head = text.strip()[:900].lower()
    return any(marker in head for marker in _PROMPT_LEAK_MARKERS)


def answer_language_mismatches_question(question: str, answer: str | None) -> bool:
    """Reject long English boilerplate for a substantive Chinese question."""
    if not answer:
        return False
    question_cjk = len(re.findall(r"[\u4e00-\u9fff]", question))
    answer_text = answer.strip()
    # URLs and markdown source labels are transport metadata, not answer
    # language.  Counting their latin characters made otherwise valid Chinese
    # web-search answers fail the delivery gate (and surface as HTTP 500).
    language_text = re.sub(r"https?://\S+", " ", answer_text)
    language_text = re.sub(r"\[[^\]]*\]\([^)]*\)", " ", language_text)
    if question_cjk < 4:
        return False
    answer_cjk = len(re.findall(r"[\u4e00-\u9fff]", language_text))
    if (
        answer_cjk == 0
        and answer_text.upper() != "OK"
        and sum(character.isalpha() for character in language_text) >= 2
    ):
        return True
    if len(answer_text) < 80:
        return False
    answer_letters = len(re.findall(r"[A-Za-z]", language_text))
    # A Chinese answer may legitimately mention product names, acronyms and
    # English source titles.  Once it contains a substantive Chinese body,
    # those terms must not turn a valid response into a server error.
    if answer_cjk >= 20:
        return False
    return answer_letters >= 40 and (answer_cjk < 8 or answer_letters > answer_cjk * 2)


def answer_has_decode_artifacts(text: str | None) -> bool:
    if not text:
        return False
    head = text[:1200]
    escaped_unicode_count = len(re.findall(r"\\u[0-9a-fA-F]{4}", head))
    backslash_count = head.count("\\")
    return escaped_unicode_count >= 2 or backslash_count >= max(12, len(head) // 8)


def answer_quality_issues(question: str, answer: str | None) -> tuple[str, ...]:
    normalized = (answer or "").strip()
    issues: list[str] = []
    if not normalized:
        issues.append("empty_answer")
        return tuple(issues)
    if not answer_has_substantive_content(normalized):
        issues.append("non_substantive_answer")
    if answer_contains_prompt_leak(normalized):
        issues.append("internal_prompt_leak")
    if answer_language_mismatches_question(question, normalized):
        issues.append("answer_language_mismatch")
    if answer_has_decode_artifacts(normalized):
        issues.append("decode_artifacts")
    return tuple(issues)


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
        # Item-local limits must never become limits on the entire response.
        # In particular, “三项，每项用一句话” does not mean “one sentence total”.
        question = re.sub(
            r"每(?:一)?(?:项|条|点|步|个[^，。；;\n]{0,8}?)[^，。；;\n]*",
            "",
            question,
        )
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
    return len(split_answer_sentences(text))


def split_answer_sentences(text: str) -> list[str]:
    """Sentence boundaries, not Markdown list numbers, decimals or URL dots."""
    return [part.strip() for part in re.split(
        r"(?<=[。！？!?])\s*|(?<=[A-Za-z]\.)(?=\s+[A-Z])", text
    ) if part.strip()]


def requested_list_size(question: str) -> int | None:
    """Extract explicit list cardinality, not arbitrary numbers in the topic."""
    match = re.search(
        r"(?:列(?:出)?|给出|提出|提供|归纳|整理|排成)[^。！？\n]{0,20}?"
        r"([一二两三四五六七八九十\d]{1,2})\s*(?:项|条|点|步|个(?:问题|要点|行动|建议))",
        question,
    )
    return _parse_small_number(match.group(1)) if match else None


def answer_list_size(answer: str) -> int:
    return len(re.findall(
        r"(?m)^\s*(?:[-*•]\s+|\d+[.、)）]\s*|[（(]\d+[)）]\s*|[一二三四五六七八九十]+[、.])\S",
        answer,
    ))


class ChatDeliveryGate:
    """Validate and mint the only response type accepted by public transports."""

    def validate(self, candidate: AnswerCandidate) -> AnswerValidationReport:
        normalized = candidate.text.strip()
        issues: list[str] = []
        if not candidate.original_question.strip():
            issues.append("missing_original_question")
        issues.extend(answer_quality_issues(candidate.original_question, normalized))
        lowered = normalized.lower()
        scaffold_marker_count = sum(
            marker in lowered for marker in _INTERNAL_SCAFFOLD_MARKERS
        )
        exposes_privileged_message = any(
            line.strip().startswith(("system prompt:", "developer message:"))
            for line in lowered.splitlines()
        )
        if (
            scaffold_marker_count >= 2 or exposes_privileged_message
        ) and "internal_prompt_leak" not in issues:
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
