"""Pure evidence-policy helpers used by retrieval and answer rendering."""

from __future__ import annotations

import re

from .models import KnowledgeSearchHit


def is_public_evidence_hit(hit: KnowledgeSearchHit) -> bool:
    metadata = {
        str(key).lower(): str(value).lower()
        for key, value in (hit.metadata or {}).items()
    }
    if metadata.get("audience", "") in {"admin", "private", "lab_member"}:
        return False
    tags = {str(tag).lower() for tag in hit.tags}
    return not bool(tags & {"audience:admin", "audience:private", "audience:lab_member"})


def is_research_hit(hit: KnowledgeSearchHit) -> bool:
    tags = {str(tag).lower() for tag in hit.tags}
    if tags & {"research", "publication", "paper-digest", "overview", "profile"}:
        return True
    source_name = (hit.source_name or "").lower()
    return "研究" in hit.title or "publications" in source_name or "research_papers" in source_name


def is_teaching_hit(hit: KnowledgeSearchHit) -> bool:
    tags = {str(tag).lower() for tag in hit.tags}
    # File format is not subject matter: research PDFs are not courseware.
    return bool(tags & {"teaching", "courseware", "tutorial", "lecture", "experiment"})


def has_query_evidence(question: str, hit: KnowledgeSearchHit) -> bool:
    """Check that a retrieved hit shares a meaningful query anchor."""

    stop_words = {
        "please", "explain", "example", "simple", "what", "with", "within",
        "请用一", "一个简", "个简单", "简单例", "单例子", "例子解", "子解释",
        "控制在", "字以内", "老师", "请问", "介绍", "一下", "是什么", "哪些",
    }
    anchors = {
        token.lower()
        for token in re.findall(r"[A-Za-z0-9_+-]{4,}", question)
        if token.lower() not in stop_words
    }
    for span in re.findall(r"[\u4e00-\u9fff]+", question):
        for width in (2, 3):
            anchors.update(
                span[index : index + width]
                for index in range(max(len(span) - width + 1, 0))
                if span[index : index + width] not in stop_words
            )
    if not anchors:
        return True
    searchable = "\n".join(
        (
            hit.title or "",
            hit.excerpt or "",
            # Provenance/visibility metadata can match words such as “公开”,
            # but cannot support a factual claim. Only document text counts.
        )
    ).lower()
    return any(anchor in searchable for anchor in anchors)


def matches_document_purpose(question: str, hit: KnowledgeSearchHit) -> bool:
    """A shared author/technical term does not turn a job ad into research evidence.

    Honor the KB's document-purpose tags even when the planner leaves retrieval
    scopes empty. This is independent of rank/score and never grants visibility.
    """
    tags = {tag.casefold() for tag in hit.tags}
    if tags & {"recruitment", "job-opening"}:
        return bool(re.search(
            r"招聘|岗位|求职|应聘|招收|招生|加入|工作机会|实习|职位|工程师|"
            r"\b(?:job|hiring|recruitment|career|internship|vacanc\w*)\b", question,
            re.IGNORECASE,
        ))
    return True


def has_unsupported_source_quote(answer: str, excerpts: list[str]) -> bool:
    """Check literal attributions, not general semantic entailment."""
    quotes = re.findall(
        r"(?:论文|文献|资料|附件|原文)[^。！？\n“”]{0,20}"
        r"(?:提到|指出|强调|写道|表示|认为)[^。！？\n“”]{0,8}“([^”]+)”", answer,
    )

    def normalize(value: str) -> str:
        return re.sub(r"\s+", "", value).casefold()

    sources = [normalize(excerpt) for excerpt in excerpts]
    return any(not any(normalize(quote) in source for source in sources) for quote in quotes)
