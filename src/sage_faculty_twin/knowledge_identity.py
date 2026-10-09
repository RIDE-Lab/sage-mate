from __future__ import annotations

import re
from pathlib import PurePosixPath

_CHUNK_SUFFIX_RE = re.compile(r"（第\s*(\d+)\s*部分）$")
_GENERIC_SECTION_LABELS = frozenset({"文档", "正文", "简介", "document", "full"})


def canonicalize_knowledge_title(title: str, source_name: str | None) -> str:
    """Make generated knowledge titles source-aware without changing provenance.

    Raw paper OCR files often have no Markdown title or section headings.  The
    importer historically collapsed all of them to ``论文页面｜文档（第N部分）``.
    The stable source path is the authoritative differentiator, so include its
    filename in paper-page titles while preserving meaningful extracted labels.
    """

    normalized_title = re.sub(r"\s+", " ", str(title or "")).strip()
    if not normalized_title.startswith("论文页面｜"):
        return normalized_title

    suffix_match = _CHUNK_SUFFIX_RE.search(normalized_title)
    suffix = suffix_match.group(0) if suffix_match else ""
    body = _CHUNK_SUFFIX_RE.sub("", normalized_title).removeprefix("论文页面｜").strip()
    source_label = knowledge_source_label(source_name)
    if not source_label:
        return normalized_title

    body_key = _identity_key(body)
    source_key = _identity_key(source_label)
    if body_key in _GENERIC_SECTION_LABELS:
        parts = [source_label]
    elif source_key and source_key not in body_key and body_key not in source_key:
        parts = [body, source_label]
    else:
        parts = [body or source_label]

    base = f"论文页面｜{'｜'.join(part for part in parts if part)}"
    max_base_length = 256 - len(suffix)
    return f"{base[:max_base_length].rstrip('｜ ')}{suffix}"


def build_search_aliases(title: str, source_name: str | None) -> str:
    """Return deterministic, human-readable aliases for generated records."""

    aliases: list[str] = []
    canonical_title = canonicalize_knowledge_title(title, source_name)
    display_title = _CHUNK_SUFFIX_RE.sub("", canonical_title)
    if "｜" in display_title:
        display_title = " ".join(part.strip() for part in display_title.split("｜")[1:])
    _append_unique(aliases, display_title)
    _append_unique(aliases, knowledge_source_label(source_name))

    source = str(source_name or "").strip()
    if source:
        source_group = re.sub(r"::part-\d+$", "", source)
        _append_unique(aliases, _clean_source_text(source_group))
        part_match = re.search(r"::part-(\d+)$", source)
        if part_match:
            part_number = part_match.group(1)
            _append_unique(aliases, f"第{part_number}部分 part {part_number}")
    return " | ".join(aliases)


def knowledge_source_label(source_name: str | None) -> str:
    source = str(source_name or "").strip()
    if not source:
        return ""
    source_group = re.sub(r"::part-\d+$", "", source)
    if source_group.startswith("homepage:"):
        source_group = source_group.removeprefix("homepage:")
    primary_path = source_group.split("#", 1)[0]
    stem = PurePosixPath(primary_path).stem
    return _clean_source_text(stem)


def is_generic_knowledge_title(title: str) -> bool:
    normalized = _CHUNK_SUFFIX_RE.sub("", str(title or "")).strip()
    if not normalized.startswith("论文页面｜"):
        return False
    body = normalized.removeprefix("论文页面｜").strip()
    return _identity_key(body) in _GENERIC_SECTION_LABELS


def _clean_source_text(value: str) -> str:
    cleaned = re.sub(r"[_\-]+", " ", str(value or ""))
    cleaned = re.sub(r"\s+", " ", cleaned)
    return cleaned.strip()


def _identity_key(value: str) -> str:
    return re.sub(r"[^0-9a-z\u4e00-\u9fff]+", "", str(value or "").lower())


def _append_unique(values: list[str], value: str) -> None:
    normalized = re.sub(r"\s+", " ", str(value or "")).strip(" |")
    if normalized and normalized not in values:
        values.append(normalized)
