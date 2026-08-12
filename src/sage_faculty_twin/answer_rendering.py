"""Pure conversion of retrieved evidence into bounded answer text."""

from __future__ import annotations

import re
from collections.abc import Iterable

from .models import KnowledgeSearchHit


def grounded_excerpt(hit: KnowledgeSearchHit, question: str) -> str:
    normalized = re.sub(r"\s+", " ", hit.excerpt or "").strip()
    if not normalized:
        return ""
    anchors = [token for token in re.findall(r"[\u4e00-\u9fff]{2,}|[A-Za-z][A-Za-z0-9+-]{3,}", question)]
    sentences = [part.strip() for part in re.split(r"(?<=[。！？.!?])\s*", normalized) if part.strip()]
    factual_markers = ("研究", "当前工作", "大模型", "推理", "状态管理", "课程", "prefill", "decode", "KV cache")
    metadata_markers = ("来源：", "可见性：", "说明：", "用途：", "维护稿")
    ranked: list[tuple[int, int, str]] = []
    for sentence in sentences:
        if any(marker in sentence for marker in metadata_markers):
            continue
        anchor_score = sum(anchor.lower() in sentence.lower() for anchor in anchors)
        fact_score = sum(marker.lower() in sentence.lower() for marker in factual_markers)
        if anchor_score or fact_score:
            ranked.append((fact_score * 3 + anchor_score, fact_score, sentence))
    if ranked:
        ranked.sort(key=lambda item: (-item[0], -item[1], len(item[2])))
        return ranked[0][2][:360]
    return normalized[:360]


def grounded_course_fact_line(hit: KnowledgeSearchHit) -> str:
    title = re.sub(r"\s+", " ", hit.title or "").strip()
    excerpt = re.sub(r"\s+", " ", hit.excerpt or "").strip()
    lowered = f"{title} {excerpt}".lower()
    if "prefill" in lowered and "decode" in lowered:
        return "资料明确覆盖 LLM 推理的 Prefill 与 Decode 两个核心阶段。"
    if "benchmark" in lowered:
        return "资料包含推理系统 Benchmark 指南，说明课程/资源也覆盖性能评测方法。"
    if "三个研究方向" in excerpt:
        return "课程组织方式是背景、三个研究方向、代表论文展开和开放问题。"
    clean_title = re.sub(r"^(?:课件正文|课程资料)｜[^｜]+｜", "", title)
    if "tutorial" in lowered or "教程" in lowered:
        return f"课程资料包含教程主题：{clean_title}。"
    if "实验" in lowered or "project sheet" in lowered or "experimental sheet" in lowered:
        return f"课程资料包含实验/项目材料：{clean_title}。"
    if "讲" in title or "lecture" in lowered:
        return f"课程资料包含讲次：{clean_title}。"
    return grounded_excerpt(hit, title)


def extract_research_summary(hit: KnowledgeSearchHit) -> list[tuple[int, str]]:
    tags = {str(tag).lower() for tag in hit.tags}
    if not tags & {"profile", "research", "research-agenda", "overview"}:
        return []
    candidates: list[tuple[int, str]] = []
    for sentence in re.split(r"[。！？\n]+", hit.excerpt):
        normalized = re.sub(r"\s+", " ", sentence).strip(" ：:；;")
        if not normalized or not any(marker in normalized for marker in ("研究", "当前工作", "研究主线")):
            continue
        if not any(marker in normalized for marker in ("大模型", "推理", "状态管理", "当前工作", "研究主线")):
            continue
        priority = sum(marker in normalized for marker in ("当前工作", "研究聚焦", "研究主线", "大模型推理"))
        candidates.append((priority, normalized[:260]))
    return candidates
