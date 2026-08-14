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


def render_sage_vllm_comparison(question: str) -> str:
    """Render an evidence-bounded stack comparison and optional experiment plan.

    The component responsibilities are stable knowledge-base facts. Experiment
    ideas are labelled as proposals so they cannot be mistaken for implemented
    runtime capabilities.
    """

    lines = [
        "基于当前公开资料，二者是上下层协作关系，而不是同一组件：",
        "- SAGE 位于应用与工作流层，组织检索、记忆、工具、决策和回答，并负责跨阶段的状态、截止时间与可观测性。",
        "- vLLM-HUST 位于推理引擎层，负责模型服务、批处理、KV Cache、并行执行和国产异构硬件适配。",
        "- 联合边界应是明确的请求契约：优先级、deadline、缓存/状态复用提示、取消信号，以及逐阶段指标回传。",
    ]
    if any(marker in question for marker in ("实验", "验证", "评测", "优化", "本周")):
        lines.extend(
            [
                "\n以下是待验证的联合实验建议，不代表当前系统已经实现：",
                "1. 端到端预算实验：固定同一批简单/复杂问题，对比仅有局部 timeout 与统一 deadline；记录 TTFT、总延迟、超时率、取消后任务回收时间和引用完整率。",
                "2. 状态复用实验：按冷启动、固定前缀命中、会话片段命中三组运行；记录 prompt tokens、首 token 延迟、KV/前缀命中率、吞吐与回答一致性。",
                "3. SLO 联合调度实验：构造前台短问答与后台深度任务混合负载，传播优先级和取消信号；记录短请求 p95、深度任务完成率、NPU 利用率与孤儿任务数。",
            ]
        )
    lines.append("\n本轮引用的公开资料列在 Support 中；版本和能力以对应仓库及真实运行收据为准。")
    return "\n".join(lines)
