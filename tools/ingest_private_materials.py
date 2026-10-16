"""Curated ingestion from the sibling private-materials repository.

The private-materials repo is a mixed archive: some files are reusable public
profile or advising material, while many others contain student records,
project budgets, server resources, review notes, or internal operations.  This
script intentionally uses an explicit allow-list and lightweight sensitive-term
guard before writing anything to the twin KB.

Usage:
    cd /home/shuhao/sage-mate
    PYTHONPATH=src:/home/shuhao/SAGE/src:/home/shuhao/sageVDB:/home/shuhao/neuromem \
        /home/shuhao/miniconda3/envs/vllm-hust-dev/bin/python3.12 tools/ingest_private_materials.py
"""
from __future__ import annotations

import argparse
import re
import sys
from dataclasses import dataclass
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.models import KnowledgeDocumentCreate
from sage_faculty_twin.source_ownership import SOURCE_OWNERSHIP

PRIVATE_ROOT = Path("/home/shuhao/private-materials")
MAX_CONTENT_CHARS = 18000

# Reviewed public material now has a maintained Wiki source. Do not reintroduce
# older private-file copies into the searchable KB after a cleanup.
WIKI_REPLACED_SOURCES = frozenset({
    "private-materials:paper-revision-lessons",
    "private-materials:cccf-survey-revision-lessons",
    "private-materials:curated-state-management-research-line",
}) | frozenset(SOURCE_OWNERSHIP)

SENSITIVE_PATH_PARTS = {
    ".git",
    ".vscode",
    "审稿",
    "学生信息管理",
    "服务器与资源信息.md",
    "机器与资源",
    "重要",
    "考评",
    "出国",
}

SENSITIVE_TERMS = re.compile(
    r"(身份证|护照|手机号|电话|微信|银行卡|银行|账号|密码|token|secret|api[_-]?key|"
    r"服务器|公网|内网|IP地址|报销|津贴|工资|预算|经费|评分|成绩|学生信息|"
    r"review|审稿|confidential)",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class PrivateMaterialSource:
    path: Path
    title: str
    tags: list[str]
    source_name: str
    audience: str = "public"
    max_chars: int = MAX_CONTENT_CHARS
    allow_sensitive_terms: tuple[str, ...] = ()


SOURCES = [
    PrivateMaterialSource(
        path=PRIVATE_ROOT / "人才项目/常用材料/个人简介_奖励与任职主稿.md",
        title="私有材料精选｜张书豪公开个人简介、奖励与学术服务",
        tags=["profile", "bio", "awards", "service", "private-materials", "audience:public"],
        source_name="private-materials:curated-bio-awards-service",
        allow_sensitive_terms=("Review Board", "项目经费"),
    ),
    PrivateMaterialSource(
        path=PRIVATE_ROOT / "人才项目/常用材料/公开主页素材_2026-05.md",
        title="私有材料精选｜公开主页素材同步边界",
        tags=["profile", "homepage", "source-boundary", "private-materials", "audience:admin"],
        source_name="private-materials:public-homepage-sync-boundary",
        allow_sensitive_terms=("项目经费",),
    ),
    PrivateMaterialSource(
        path=PRIVATE_ROOT / "论文写作/paper_revision_lessons.md",
        title="论文写作方法｜系统论文修改与打磨经验",
        tags=["paper-writing", "research-advising", "revision", "systems-paper", "private-materials", "audience:public"],
        source_name="private-materials:paper-revision-lessons",
        allow_sensitive_terms=("review",),
    ),
    PrivateMaterialSource(
        path=PRIVATE_ROOT / "论文写作/cccf_chinese_survey_revision_lessons.md",
        title="论文写作方法｜中文综述论文修改经验",
        tags=["paper-writing", "survey", "chinese-writing", "cccf", "private-materials", "audience:public"],
        source_name="private-materials:cccf-survey-revision-lessons",
        allow_sensitive_terms=("审稿人",),
    ),
    PrivateMaterialSource(
        path=PRIVATE_ROOT / "演讲材料/_抽取文本/如何思考研究课题.md",
        title="科研指导方法｜如何确定一个好的研究课题",
        tags=["research-advising", "topic-selection", "literature-review", "experiment-design", "private-materials", "audience:lab_member"],
        source_name="private-materials:how-to-think-about-research-topic",
    ),
]


CURATED_PAYLOADS = [
    KnowledgeDocumentCreate(
        title="论文写作方法｜系统论文修改中文速查清单：避免实验比较不公平",
        content=(
            "来源：private-materials/论文写作/paper_revision_lessons.md\n"
            "可见性：public\n"
            "说明：该条目是英文系统论文修改经验的中文检索摘要，用于回答学生的中文写作问题。\n\n"
            "常见学生问题：论文修改时怎么避免实验比较不公平？什么是不公平实验比较？如何判断实验设置是否可比？\n\n"
            "系统论文修改时，第一原则是科学有效性优先于视觉好看。不要把不可比较的实验设置放在同一个面板里当作公平对比；"
            "例如 zero-reject 和有拒绝率的策略不应被画成同一类 operating regime。只要低延迟依赖拒绝、延迟转移、admission control 或输出预算调整，"
            "论文里就必须把服务成本、牺牲对象和适用边界说清楚。图表语义必须和表格行名、caption、正文 claim 保持一致，不能为了图好看保留和真实数据矛盾的版本。"
            "正文结构上，要避免一句话单独成段、避免无必要的 bullet list、避免 callout box 反复重复同一个 thesis。"
            "写 survey 或 related work 时，要说明证据成熟度、引用来源和自引比例；如果讨论恢复或安全边界，应明确 fault model，"
            "例如 crash-stop、crash-recovery、network partition 或 silent degradation。"
        ),
        tags=[
            "paper-writing",
            "research-advising",
            "revision",
            "systems-paper",
            "experiment-design",
            "fair-comparison",
            "private-materials",
            "audience:public",
        ],
        source_name="private-materials:paper-revision-lessons-zh-summary",
    ),
    KnowledgeDocumentCreate(
        title="私有材料精选｜状态管理到大模型推理基础设施研究主线",
        content=(
            "来源：private-materials/演讲材料/学术汇报/学术汇报-张书豪.md\n"
            "可见性：public\n"
            "说明：该条目是从私有演讲材料中提炼出的公开问答摘要，已移除项目金额、申报口径和内部图表。\n\n"
            "张书豪老师的研究主线可以概括为：在复杂硬件与动态负载下做高效状态管理，并把这条线进一步收束到大模型推理基础设施。"
            "这里的状态管理不是简单的数据存储，而是围绕共享状态持续回答三个问题：第一，状态访问如何被组织、观测和调度；"
            "第二，状态相关执行如何与底层硬件约束协同优化；第三，状态如何持续写入、演化并被后续任务稳定复用。"
            "放到大模型系统里，对应的问题包括请求合批与排队、prefill/decode 执行、KV cache 复用、RAG 与长期记忆如何参与后续推理。"
            "因此，学生如果想围绕老师方向找课题，可以从“访问调度、执行优化、状态复用、记忆增强推理”四个入口切入，并明确自己要优化的指标，"
            "例如吞吐、P99 时延、TTFT、显存占用、服务稳定性或跨轮一致性。"
        ),
        tags=[
            "profile",
            "research-agenda",
            "state-management",
            "llm-inference",
            "private-materials",
            "audience:public",
        ],
        source_name="private-materials:curated-state-management-research-line",
    ),
    KnowledgeDocumentCreate(
        title="私有材料精选｜国产算力大模型推理服务系统研究框架",
        content=(
            "来源：private-materials/演讲材料/企业演讲/大模型推理服务系统.md\n"
            "可见性：lab_member\n"
            "说明：该条目是从私有演讲材料中提炼出的组内问答摘要，已移除项目金额、申报细节和未公开合作信息。\n\n"
            "面向国产算力的大模型推理服务系统，可以拆成三条技术线：算力调度编排、记忆检索中间件、推理优化执行。"
            "算力调度编排关注异构资源抽象、任务排队、请求路由、并行执行、弹性容错以及 SLA 约束下的全局优化；"
            "记忆检索中间件关注向量索引、会话记忆、RAG 上下文组装、多源知识接入、在线更新与一致性治理；"
            "推理优化执行关注计算图与算子优化、量化、KV cache、批处理、多卡并行、通信优化，以及性能、精度和稳定性的联合调优。"
            "对学生选题来说，可以把一个想法落到这三条线之一，再进一步明确 workload、硬件约束、baseline、指标和可复现实验。"
        ),
        tags=[
            "research-agenda",
            "inference-serving",
            "domestic-hardware",
            "kv-cache",
            "rag",
            "private-materials",
            "audience:lab_member",
        ],
        source_name="private-materials:curated-domestic-inference-serving-framework",
    ),
]


def _relative_path(path: Path) -> str:
    try:
        return path.relative_to(PRIVATE_ROOT).as_posix()
    except ValueError:
        return path.as_posix()


def _has_sensitive_path(path: Path) -> bool:
    parts = set(path.relative_to(PRIVATE_ROOT).parts)
    return bool(parts & SENSITIVE_PATH_PARTS)


def _read_source(source: PrivateMaterialSource) -> tuple[str, str | None]:
    if not source.path.exists():
        return "", f"missing file: {_relative_path(source.path)}"
    if _has_sensitive_path(source.path):
        return "", f"sensitive path skipped: {_relative_path(source.path)}"
    if source.path.suffix.lower() != ".md":
        return "", f"unsupported non-markdown file: {_relative_path(source.path)}"

    text = source.path.read_text(encoding="utf-8", errors="replace")
    cleaned = _sanitize_markdown(text)
    redacted_for_scan = cleaned
    for allowed in source.allow_sensitive_terms:
        redacted_for_scan = re.sub(re.escape(allowed), "", redacted_for_scan, flags=re.IGNORECASE)
    if SENSITIVE_TERMS.search(redacted_for_scan):
        return "", f"sensitive term skipped: {_relative_path(source.path)}"

    header = (
        f"来源：private-materials/{_relative_path(source.path)}\n"
        f"可见性：{source.audience}\n"
        "说明：该条目来自私有材料白名单，已按低风险问答用途筛选。\n\n"
    )
    return (header + cleaned)[: source.max_chars], None


def _sanitize_markdown(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        if line.startswith("!["):
            continue
        if "/data/temp/" in line or line.startswith("<!-- Slide number:"):
            continue
        lines.append(line)
    cleaned = "\n".join(lines)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def build_payloads() -> tuple[list[KnowledgeDocumentCreate], list[str]]:
    payloads: list[KnowledgeDocumentCreate] = []
    skipped: list[str] = []
    for source in SOURCES:
        if source.source_name in WIKI_REPLACED_SOURCES:
            skipped.append(f"superseded by Wiki: {source.source_name}")
            continue
        content, reason = _read_source(source)
        if reason is not None:
            skipped.append(reason)
            continue
        payloads.append(
            KnowledgeDocumentCreate(
                title=source.title,
                content=content,
                tags=source.tags,
                source_name=source.source_name,
            )
        )
    payloads.extend(
        payload for payload in CURATED_PAYLOADS
        if payload.source_name not in WIKI_REPLACED_SOURCES
    )
    return payloads, skipped


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--dry-run", action="store_true", help="Inspect what would be ingested")
    args = parser.parse_args()

    payloads, skipped = build_payloads()
    print(f"Prepared {len(payloads)} private-material payloads.")
    for payload in payloads:
        print(f"  READY: {payload.title} [{payload.source_name}]")
    for reason in skipped:
        print(f"  SKIP: {reason}")

    if args.dry_run:
        return 0

    settings = AppSettings()
    store = LocalKnowledgeStore(settings)
    created = updated = unchanged = 0
    changed = False
    for payload in payloads:
        before = store._find_document_by_source_name(payload.source_name)  # noqa: SLF001
        record, was_created = store.upsert_document(payload, rebuild_indexes=False)
        if was_created:
            created += 1
            changed = True
            print(f"  CREATED: {record.title} ({record.document_id[:8]})")
        elif before is not None and (
            before.title != payload.title
            or before.content != payload.content
            or before.tags != payload.tags
            or before.source_name != payload.source_name
        ):
            updated += 1
            changed = True
            print(f"  UPDATED: {record.title} ({record.document_id[:8]})")
        else:
            unchanged += 1
            print(f"  EXISTS: {record.title} ({record.document_id[:8]})")

    if changed:
        store.rebuild_indexes()
    print(f"Done. created={created}, updated={updated}, unchanged={unchanged}, skipped={len(skipped)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
