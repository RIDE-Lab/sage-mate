"""Pure conversation-context routing helpers."""

from __future__ import annotations

import re
from typing import Any


SHORT_FOLLOWUP_PATTERN = re.compile(
    r"^(具体|那个|这个|那|这|这篇|那篇|继续|然后|展开|详细|细节|还有|接着|其他|另外|呢|么|哦|能否|可以|按前面|按刚才)"
)


def question_needs_recent_context(question: str) -> bool:
    normalized = str(question or "").strip()
    return any(
        marker in normalized
        for marker in (
            "刚才", "前面", "上次", "之前", "上一轮", "继续", "下一步",
            "结合我", "我提到", "刚刚说", "那个方向", "这个方向",
            "那个方案", "这个方案", "值得继续", "按前面", "那个", "这个",
        )
    )


def is_light_request(request: Any) -> bool:
    question = str(getattr(request, "question", "") or "").strip()
    if (
        not question
        or len(question) > 120
        or getattr(request, "deep_thinking_explicit", False)
        or getattr(request, "web_search", False)
        or getattr(request, "attachments", None)
        or getattr(request, "course_context", None)
    ):
        return False
    if any(marker in question for marker in ("预约", "约时间", "office hour", "meeting", "合作准备")):
        return False
    return not question_needs_recent_context(question) or len(question) <= 32


def looks_like_contextual_follow_up(question: str, recent_session_context: str | None) -> bool:
    if not recent_session_context:
        return False
    normalized = str(question or "").strip()
    if not normalized:
        return False
    if SHORT_FOLLOWUP_PATTERN.match(normalized):
        return True
    return any(
        marker in normalized
        for marker in ("刚才", "前面", "上面", "那个方向", "这个方向", "那个方案", "这个方案", "继续", "下一步", "值得继续", "风险是什么")
    )


def expand_followup_question(question: str, recent_session_context: str | None) -> str:
    normalized = (question or "").strip()
    if not normalized or not recent_session_context:
        return question
    if len(normalized) >= 25 and not SHORT_FOLLOWUP_PATTERN.match(normalized):
        return question
    prior_user_turns: list[str] = []
    for raw_line in recent_session_context.splitlines():
        line = raw_line.strip()
        marker = line.find("User:")
        if marker == -1:
            continue
        extracted = line[marker + len("User:"):].strip()
        if extracted:
            prior_user_turns.append(extracted)
    if not prior_user_turns:
        return question
    return f"{' '.join(prior_user_turns[-2:])} {question}".strip()
