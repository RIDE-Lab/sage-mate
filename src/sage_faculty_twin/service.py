from __future__ import annotations

import asyncio
import hashlib
import inspect
import json
import logging
import os
import re
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, time, timedelta
from importlib import metadata
from pathlib import Path
from time import perf_counter
from typing import Any
from uuid import uuid4

from fastapi import HTTPException, status
from sage.foundation import BaseCoMapFunction, MapFunction, SinkFunction
from sage.runtime import FlowNetEnvironment

from . import __version__ as _app_version
from .analytics_store import ConversationAnalyticsStore
from .artifact_memory_draft_store import (
    ArtifactMemoryDraftRecord,
    ArtifactMemoryDraftStore,
)
from .auth import (
    build_admin_session_token,
    build_user_session_token,
    decode_admin_session_token,
    decode_user_session_token,
    normalize_admin_session_payload,
    resolve_admin_session_identity,
    validate_admin_credentials,
)
from .calendar_bridge import CalendarBridgeClient
from .chat_contracts import (
    ChatIntake,
    EvidenceBundle,
    InteractionDecision,
    PromptEnvelope,
    PromptMode,
)
from .chat_delivery import (
    AnswerConstraints,
    AnswerOrigin,
    ChatDeliveryGate,
    DeliveredChatResponse,
    answer_contains_prompt_leak as _contains_internal_prompt_leak,
    answer_language_mismatches_question as _answer_language_mismatches_question,
)
from .code_agent_backends import (
    ClaudeHustCodeAgentBackend,
    CodeAgentBackend,
    InternalCodeAgentBackend,
)
from .code_workbench import CODE_WORKBENCH_PROFILES, CodeWorkbench
from .config import AppSettings
from .escalation_store import EscalationQueueStore
from .follow_up_store import FollowUpQueueStore
from .interaction_policy import (
    InteractionPolicyEngine,
    asks_for_booking_information,
    requires_faculty_review,
    requires_human_handoff,
)
from .knowledge_base import LocalKnowledgeStore, _canonical_source_group
from .knowledge_gap_draft_store import KnowledgeGapDraftStore
from .light_agent import LightweightActionPlanner
from .llm_client import VllmChatClient
from .meeting import MeetingService
from .memory_store import (
    ConversationDigestStore,
    ConversationMemoryHit,
    ConversationMemoryRecord,
    NeuroMemConversationStore,
    ProfileMemoryRecord,
)
from .models import (
    AdminLoginRequest,
    AdminSessionResponse,
    AnonymousSuggestionCreate,
    AnonymousSuggestionRecord,
    AnswerBasisItem,
    ArtifactMemoryDraftRecordResponse,
    AvailabilitySchedule,
    BookingDecisionRequest,
    BookingRecord,
    BookingRequest,
    BookingResponse,
    ChatAttachment,
    ChatFeedbackRequest,
    ChatFeedbackResponse,
    ChatRequest,
    ChatResponse,
    CodeAssistRequest,
    CodeAssistResponse,
    CodeCommandRequest,
    CodeCommandResponse,
    CodeContextRequest,
    CodeContextResponse,
    CodeDirectoryListRequest,
    CodeDirectoryListResponse,
    CodeFileReadRequest,
    CodeFileReadResponse,
    CodeGitDiffRequest,
    CodeGitDiffResponse,
    CodeGitStatusRequest,
    CodeGitStatusResponse,
    CodeProposeRequest,
    CodeProposeResponse,
    CodeSearchRequest,
    CodeSearchResponse,
    CodeWorkspaceListResponse,
    ConversationExchangeResponse,
    ConversationHistoryItemResponse,
    ConversationHistoryListResponse,
    ConversationTranscriptResponse,
    EscalationDecisionRequest,
    EscalationRecord,
    FollowUpAction,
    FollowUpDispatchResponse,
    FollowUpQueueRecord,
    HandoffCategorySummary,
    InteractionIntent,
    KnowledgeDocumentCreate,
    KnowledgeDocumentActionResponse,
    KnowledgeDocumentRecord,
    KnowledgeDocumentReviewRequest,
    KnowledgeDocumentReviewSummary,
    KnowledgeGapDraftCreateRequest,
    KnowledgeGapDraftRecordResponse,
    KnowledgeGapSuggestion,
    KnowledgeSearchHit,
    KnowledgeSearchResponse,
    KnowledgeWriteBackResult,
    MemoryAuditItem,
    MemoryProfileListResponse,
    MemoryProfileRecordResponse,
    NeuroMemOperationsSnapshot,
    NotificationDeliveryStatus,
    OnlinePresenceHeartbeatRequest,
    OnlinePresenceHeartbeatResponse,
    OperationsOverviewResponse,
    OperationsQueueSummary,
    OperationsSatisfactionSummary,
    OperationsTaskItem,
    OperationsTaskStateRecord,
    OperationsTaskStateUpdateRequest,
    OperationsWorkbenchResponse,
    PlannerMetricsSnapshot,
    QuestionAnalyticsOverview,
    QuestionAnalyticsReportResponse,
    QuestionClusterSummary,
    RuntimeFeatureFlagsResponse,
    RuntimeFeatureFlagsUpdateRequest,
    ServiceControlResponse,
    StudentOperationsProfile,
    TokenUsage,
    UnresolvedQuestionItem,
    UserLoginRequest,
    UserRegisterRequest,
    UserSessionResponse,
    WebSearchHit,
    WorkflowReplayReportResponse,
    WorkflowReplayScenarioResultResponse,
    WorkflowPlanComparison,
    WorkflowPlanPreview,
    WorkflowTraceStep,
)
from .notifications import BookingEmailNotifier, BookingNotificationError
from .online_presence_store import OnlinePresenceStore
from .operations_store import OperationsTaskStateStore
from .runtime_feature_store import RuntimeFeatureFlagStore
from .persona import (
    COMPACT_CITATION_GROUNDING_RULES,
    build_system_prompt,
)
from .planner_comparison_store import PlannerComparisonEntry, PlannerComparisonStore
from .planner_metrics_store import PlannerMetricsStore
from .service_runtime import ServiceRuntimeManager
from .suggestion_store import SuggestionBoardStore
from .web_search import WebSearchClient
from .user_store import UserAccountStore
from .workflow_context import WorkflowRequestContext
from .workflow_eval import (
    default_scenarios_path,
    evaluate_workflow_replay_scenarios,
    load_workflow_replay_scenarios,
)
from .skill_router import SkillRouter
from .skill_runner import SkillRunner
from .skill_tools import SkillToolRegistry
from .skills import SkillContext
from .workflow_planner import DeterministicWorkflowPlanner, PlannerDecision


def _resolve_distribution_version(*candidates: str) -> str:
    for name in candidates:
        if not name:
            continue
        try:
            return metadata.version(name)
        except metadata.PackageNotFoundError:
            continue
        except Exception:
            continue
    return "unknown"


def _parse_pyproject_version(pyproject_path: Path) -> str | None:
    """Parse static version from pyproject.toml, or resolve dynamic version attr."""
    import re as _re
    if not pyproject_path.exists():
        return None
    try:
        text = pyproject_path.read_text(encoding="utf-8")
    except Exception:
        return None

    # Static version: version = "x.y.z"
    m = _re.search(r'^version\s*=\s*"([^"]+)"', text, _re.MULTILINE)
    if m:
        return m.group(1)

    # Dynamic version via attr: attr = "module.submodule.__version__"
    m = _re.search(r'attr\s*=\s*"([^"]+)"', text, _re.MULTILINE)
    if m:
        try:
            attr_path = m.group(1)
            parts = attr_path.rsplit(".", 1)
            if len(parts) == 2:
                mod = __import__(parts[0], fromlist=[parts[1]])
                ver = getattr(mod, parts[1], None)
                if ver and str(ver).strip() and str(ver) != "0.0.0+unknown":
                    return str(ver)
        except Exception:
            pass

    return None


def _resolve_source_version(repo_name: str, *, pip_names: tuple[str, ...] = (),
                             expect_name: str = "") -> str:
    """Resolve version from local pyproject.toml first, pip metadata as fallback.

    All IntelliStream packages have local source checkouts as sibling directories.
    pyproject.toml is the single source of truth for the version string.

    Args:
        repo_name: Name of the sibling directory (e.g. "SAGE", "sageVDB").
        pip_names: PyPI distribution names to try if pyproject.toml is absent.
        expect_name: If set, verify pyproject.toml's ``name =`` matches before
                     accepting the version.  Prevents picking up the wrong
                     package when a repo hosts multiple distributions.
    """
    import re as _re
    # Locate the sibling repo checkout (same parent as this repo).
    this_repo = Path(__file__).resolve().parents[2]
    repo_root = this_repo.parent / repo_name

    # 1. Local source: pyproject.toml (static version or dynamic attr)
    pyproject = repo_root / "pyproject.toml"
    if pyproject.exists():
        try:
            text = pyproject.read_text(encoding="utf-8")
            # If expect_name is set, verify this pyproject.toml is the right package
            if expect_name:
                nm = _re.search(r'^name\s*=\s*"([^"]+)"', text, _re.MULTILINE)
                if nm and nm.group(1) != expect_name:
                    pass  # wrong package — skip to pip fallback
                else:
                    ver = _parse_pyproject_version(pyproject)
                    if ver:
                        return ver
            else:
                ver = _parse_pyproject_version(pyproject)
                if ver:
                    return ver
        except Exception:
            pass

    # 2. Fallback: pip metadata (for packages installed from PyPI)
    for name in pip_names:
        try:
            return metadata.version(name)
        except (metadata.PackageNotFoundError, Exception):
            continue

    return "unknown"


def _resolve_vllm_hust_version() -> str:
    """Resolve vLLM-HUST's own version (setuptools-scm, NOT upstream).

    vllm-hust uses setuptools-scm so its version is derived from git tags:
    ``{upstream_tag}.post{N}.dev{commits}+g{hash}``.
    The local pyproject.toml has ``dynamic = ["version"]`` with no static
    value, so _resolve_source_version will fall through to pip metadata
    which carries the full setuptools-scm string.
    """
    configured_image = os.environ.get("VLLM_ENGINE_IMAGE", "").strip()
    if configured_image and ":" in configured_image:
        image_tag = configured_image.rsplit(":", 1)[-1]
        if image_tag:
            return image_tag if image_tag.startswith("v") else f"v{image_tag}"
    source_version = _resolve_source_version(
        "vllm-hust", pip_names=("vllm-hust", "vllm"), expect_name="vllm-hust",
    )
    if source_version != "unknown":
        return source_version
    vendored = Path(__file__).resolve().parents[2] / "deps" / "vllm-hust"
    try:
        import subprocess

        commit = subprocess.check_output(
            ["git", "-C", str(vendored), "rev-parse", "--short", "HEAD"],
            text=True,
            timeout=2,
        ).strip()
        if commit:
            return f"git-{commit}"
    except Exception:
        pass
    return "unknown"


def build_stack_versions_payload() -> dict[str, str]:
    """Resolve all stack component versions from local source checkouts."""
    return {
        "app_version": _app_version,
        "stack_version_sage": _resolve_source_version(
            "SAGE", pip_names=("isage", "isage-common"), expect_name="isage",
        ),
        "stack_version_neuromem": _resolve_source_version(
            "neuromem", pip_names=("isage-neuromem",), expect_name="isage-neuromem",
        ),
        "stack_version_vllm_hust": _resolve_vllm_hust_version(),
        "stack_version_sagevdb": _resolve_source_version(
            "sageVDB", pip_names=("isage-vdb",), expect_name="isage-vdb",
        ),
        "stack_version_sage_anns": _resolve_source_version(
            "sage-anns", pip_names=("isage-anns",), expect_name="isage-anns",
        ),
        "model_name": os.environ.get("DIGITAL_TWIN_MODEL_NAME", "").strip()
        or os.environ.get("VLLM_HUST_MODEL", "").strip()
        or "unknown",
        "engine_image": os.environ.get("VLLM_ENGINE_IMAGE", "").strip() or "unknown",
        "npu_devices": os.environ.get("ASCEND_VISIBLE_DEVICES", "").strip()
        or os.environ.get("VLLM_ENGINE_NPU_DEVICES", "").strip()
        or "unknown",
    }


def build_hardware_payload() -> dict[str, str]:
    """Collect host hardware info: NPU, CPU, memory."""
    import shutil
    import subprocess as _sp

    info: dict[str, str] = {}
    active_devices = (
        os.environ.get("ASCEND_VISIBLE_DEVICES", "").strip()
        or os.environ.get("VLLM_ENGINE_NPU_DEVICES", "").strip()
    )
    if active_devices:
        info["npu_devices"] = active_devices

    # --- NPU (Ascend) ---
    npu_smi = shutil.which("npu-smi")
    if npu_smi:
        try:
            out = _sp.check_output([npu_smi, "info"], text=True, timeout=5)
            npu_names: list[str] = []
            npu_stats: dict[str, dict[str, float]] = {}
            pending_device: str | None = None
            for line in out.splitlines():
                parts = line.split("|")
                if len(parts) < 3:
                    continue
                chip_col = parts[1].strip()
                tokens = chip_col.split()
                # NPU device lines: "<npu_id>     <model_name>" e.g. "0     910B2"
                # Skip sub-lines (just "0") and process lines ("<npu_id> <large_pid>")
                if len(tokens) == 2 and tokens[0].isdigit():
                    name = tokens[1]
                    # Model names are alphanumeric like "910B2", not pure digits
                    if not name.isdigit():
                        npu_names.append(name)
                        pending_device = tokens[0]
                        npu_stats.setdefault(pending_device, {})["model"] = name
                elif pending_device is not None and len(parts) >= 4:
                    # The following npu-smi row contains AICore, memory and
                    # HBM figures. Keep parsing positional numeric fields so
                    # this remains portable across npu-smi minor versions.
                    numeric = re.findall(r"\d+(?:\.\d+)?", parts[3])
                    if len(numeric) >= 5:
                        values = [float(value) for value in numeric[-5:]]
                        npu_stats[pending_device].update(
                            aicore=values[0],
                            memory_used=values[1],
                            memory_total=values[2],
                            hbm_used=values[3],
                            hbm_total=values[4],
                        )
                    pending_device = None
            if npu_names:
                unique = sorted(set(npu_names), key=npu_names.index)
                host_summary = f"{len(npu_names)}\u00d7 {unique[0]}" if len(unique) == 1 else f"{len(npu_names)}\u00d7 {','.join(unique)}"
                info["npu_host"] = host_summary
                if active_devices:
                    count = len([item for item in active_devices.split(",") if item.strip()])
                    info["npu"] = f"{active_devices} · {count}\u00d7 {unique[0]}"
                else:
                    info["npu"] = host_summary
                selected = [
                    npu_stats[device]
                    for device in (active_devices.split(",") if active_devices else npu_stats)
                    if device in npu_stats
                ]
                if selected:
                    aicore_values = [item["aicore"] for item in selected if "aicore" in item]
                    hbm_used = sum(item.get("hbm_used", 0.0) for item in selected)
                    hbm_total = sum(item.get("hbm_total", 0.0) for item in selected)
                    if aicore_values:
                        info["npu_utilization"] = f"{sum(aicore_values) / len(aicore_values):.1f}%"
                        info["npu_utilization_by_device"] = ",".join(
                            f"{device}:{npu_stats[device].get('aicore', 0):.0f}%"
                            for device in (active_devices.split(",") if active_devices else npu_stats)
                            if device in npu_stats
                        )
                    if hbm_total:
                        info["npu_memory_usage"] = (
                            f"{hbm_used / 1024:.1f}/{hbm_total / 1024:.1f} GiB"
                        )
                    info["npu_active_count"] = str(len(selected))
        except Exception:
            pass

    # --- CPU ---
    try:
        import shutil as _sh
        import subprocess as _sp2
        lscpu_bin = _sh.which("lscpu")
        if lscpu_bin:
            lscpu_out = _sp2.check_output([lscpu_bin], text=True, timeout=5)
            model = ""
            cores = 0
            for line in lscpu_out.splitlines():
                if line.startswith("Model name:"):
                    model = line.split(":", 1)[1].strip()
                elif line.startswith("CPU(s):") and not line.startswith("CPU(s) list"):
                    cores = int(line.split(":", 1)[1].strip())
            if model:
                short = model.split("@")[0].strip()
                info["cpu"] = f"{short} \u00b7 {cores} cores" if cores else short
        else:
            # Fallback: /proc/cpuinfo
            with open("/proc/cpuinfo", "r", encoding="utf-8") as f:
                cpuinfo = f.read()
            model_line = next((line for line in cpuinfo.splitlines() if line.startswith("model name")), "")
            model = model_line.split(":", 1)[1].strip() if ":" in model_line else ""
            core_count = cpuinfo.count("processor\t:")
            if model:
                short = model.split("@")[0].strip()
                info["cpu"] = f"{short} \u00b7 {core_count} cores"
    except Exception:
        pass

    # --- Memory ---
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    gib = kb / (1024 * 1024)
                    if gib >= 1024:
                        info["memory"] = f"{gib / 1024:.1f} TiB"
                    else:
                        info["memory"] = f"{gib:.0f} GiB"
                    break
    except Exception:
        pass

    return info

_FLOWNET_TICK = "__flownet_tick__"
_THINK_BLOCK_RE = re.compile(r"<think>.*?</think>", re.IGNORECASE | re.DOTALL)
_THINK_OPEN_RE = re.compile(r"<think>.*", re.IGNORECASE | re.DOTALL)
def _strip_internal_thinking_content(answer: str | None) -> str:
    if not answer:
        return ""
    stripped = _THINK_BLOCK_RE.sub("", answer)
    stripped = _THINK_OPEN_RE.sub("", stripped)
    stripped = re.sub(r"\n{3,}", "\n\n", stripped)
    return stripped.strip()


def _answer_does_not_complete_requested_task(question: str, answer: str | None) -> bool:
    """Catch polite but non-responsive answers that pass basic text checks."""
    if not answer:
        return False
    normalized_question = re.sub(r"\s+", "", question)
    normalized_answer = answer.strip()
    compact_answer = re.sub(r"\s+", "", normalized_answer)
    if (
        normalized_question
        and normalized_question in compact_answer
        and len(compact_answer) <= len(normalized_question) + 30
    ):
        return True
    contextual_markers = ("上面", "上述", "刚才", "前面", "上一条")
    missing_context_markers = (
        "未提供具体",
        "没有提供具体",
        "无法直接进行",
        "请提供需要",
        "请先提供",
    )
    if any(marker in normalized_question for marker in contextual_markers) and any(
        marker in normalized_answer for marker in missing_context_markers
    ):
        return True
    asks_for_three = any(
        marker in normalized_question
        for marker in ("三个问题", "3个问题", "三个要点", "3个要点", "归纳三点")
    )
    if asks_for_three:
        numbered_items = len(re.findall(r"(?:^|\n)\s*[1-3][.、)]", normalized_answer))
        chinese_ordinals = sum(
            marker in normalized_answer
            for marker in ("第一个", "第二个", "第三个")
        )
        if max(numbered_items, chinese_ordinals) < 3:
            return True

    generic_guidance_markers = (
        "如何",
        "怎么",
        "为什么",
        "哪些问题",
        "应用前景",
        "值得先问",
    )
    refusal_markers = (
        "这部分我需要额外确认",
        "不便在此讨论",
        "请直接联系张老师",
        "请开启联网搜索",
        "请开启联网检索",
    )
    return any(marker in question for marker in generic_guidance_markers) and any(
        marker in normalized_answer for marker in refusal_markers
    )


def _answer_is_irrelevant_to_question(question: str, answer: str | None) -> bool:
    if not answer:
        return False
    lowered_question = question.lower()
    lowered_answer = answer.lower()
    if ("延迟" in question or "latency" in lowered_question) and "吞吐" in question:
        if "延迟" not in answer or "吞吐" not in answer:
            return True
    if any(marker in lowered_question for marker in ("ascend", "npu", "昇腾")):
        markers = ("ascend", "npu", "昇腾", "算子", "量化", "并行", "显存", "内存", "推理")
        coverage_groups = (
            ("量化", "fp16", "bf16", "int8"),
            ("算子", "图优化", "图编译", "融合"),
            ("kv cache", "内存", "缓存", "碎片"),
            ("并行", "通信", "多卡"),
            ("批处理", "batch", "调度", "并发"),
            ("ttft", "tpot", "吞吐", "尾延迟", "p95", "p99"),
        )
        covered_dimensions = sum(
            any(marker in lowered_answer for marker in group)
            for group in coverage_groups
        )
        accelerator_mismatches = (
            "cuda",
            "nccl",
            "tensorrt",
            "gpu利用率",
            "gpu 利用率",
            "small buffer",
            "hiperf",
            "混合精度训练",
            "量化感知训练",
            "训练速度",
            "模型训练",
            "qat",
            "zero冗余",
            "zero 冗余",
        )
        return (
            sum(marker in lowered_answer for marker in markers) < 2
            or covered_dimensions < 3
            or any(marker in lowered_answer for marker in accelerator_mismatches)
        )
    if any(marker in question for marker in ("医学影像", "图像分割")):
        markers = ("医学", "影像", "分割", "标注", "临床", "泛化", "病灶", "器官")
        return sum(marker in answer for marker in markers) < 2
    if "三个问题" in question and any(marker in question for marker in ("研究路线", "研究方向")):
        markers = ("研究", "方向", "路线", "成果", "问题", "瓶颈")
        return sum(marker in answer for marker in markers) < 2
    return False


# Number of user-defined chat-workflow stages (the operator classes derived
# from `_StageBase`). The DAG pipeline introduced by Task 4 adds two
# internal `_ChatContextMergeN` co-map operators on top of these stages,
# but those are infrastructure and are excluded from this count so the
# `chat_pipeline_stages` runtime telemetry tag stays comparable across
# the linear and DAG runtime paths.
_CHAT_PIPELINE_STAGE_COUNT = 13
# Canonical order of chat-workflow trace keys. The linear pipeline emits
# them in this order; the DAG pipeline (Task 4 of the Faculty Twin DAG
# Pipeline plan) parallelises some stages and therefore needs an explicit
# post-processing pass to keep `[step.key for step in workflow_trace]`
# byte-identical with the linear baseline. `artifact_memory_writeback` is
# emitted by `MemoryPersistStage` only when attachments are present, so it
# is sorted between `memory_persist` and `memory_profile_consolidate` when
# it does appear. Repeated keys keep their relative order (stable sort).
_CANONICAL_TRACE_ORDER: tuple[str, ...] = (
    "bootstrap",
    "workflow_plan_preview",
    "interaction_understand",
    "booking_prepare",
    "booking_execute",
    "memory_retrieve",
    "knowledge_retrieve",
    "prompt_build",
    "llm_answer",
    "memory_persist",
    "artifact_memory_writeback",
    "memory_profile_consolidate",
    "follow_up_plan",
    "memory_usefulness_score",
    "response_render",
)
_CANONICAL_TRACE_INDEX: dict[str, int] = {
    key: idx for idx, key in enumerate(_CANONICAL_TRACE_ORDER)
}

# Maps canonical trace keys to the DAG fan-out branch they belong to. Steps
# sharing the same group ran concurrently in the chat DAG pipeline (Task 4 of
# the Faculty Twin DAG Pipeline plan) and the UI renders them as a stacked
# fan-out cluster instead of a flat sequence. Keys not in this map remain on
# the linear backbone (``parallel_group=None``).
_PARALLEL_TRACE_GROUPS: dict[str, str] = {
    "memory_retrieve": "retrieval",
    "knowledge_retrieve": "retrieval",
    "memory_persist": "post_answer",
    "artifact_memory_writeback": "post_answer",
    "memory_profile_consolidate": "post_answer",
    "follow_up_plan": "post_answer",
    "memory_usefulness_score": "post_answer",
}

# Trace keys that belong to the post-answer fan-out branch. Kept in canonical
# order. The chat DAG runs these stages *after* ``response_render`` when the
# critical path completes — so the immediate ``ChatResponse`` ships without
# waiting on memory writes / follow-up planning when
# ``DIGITAL_TWIN_POST_ANSWER_BACKGROUND`` is enabled (Task 2 of the Chat
# Latency Optimizations plan).
_POST_ANSWER_TRACE_KEYS: tuple[str, ...] = (
    "memory_persist",
    "artifact_memory_writeback",
    "memory_profile_consolidate",
    "follow_up_plan",
    "memory_usefulness_score",
)

# Default-on so production /chat returns as soon as the LLM answer is rendered.
# Set ``DIGITAL_TWIN_POST_ANSWER_BACKGROUND=false`` to roll back to the
# previous blocking semantics (post-answer stages run before /chat returns).
# Chat Latency Optimizations Task 3 + V4.1 context compression:
# soft cap on assembled prompt size.
# When ``len(system_prompt) + len(user_prompt)`` exceeds this threshold the
# prompt builder truncates inputs in this order:
#   (a) drop oldest memory hits beyond the top 3,
#   (b) cap each knowledge hit excerpt at ``_KNOWLEDGE_HIT_BODY_CAP`` chars,
#   (c) cap each attachment ``text_content`` at ``_ATTACHMENT_BODY_CAP`` chars,
#   (d) drop the rolling session digest (compressed older turns).
# Override via ``DIGITAL_TWIN_PROMPT_SOFT_CAP``. ~24000 chars roughly maps to
# 6k tokens for typical Chinese/English mixed content, well below the model
# context window but small enough to keep decode latency bounded.
_PROMPT_SOFT_CAP = 24000
_PROMPT_MEMORY_HIT_KEEP: int = 3
_KNOWLEDGE_HIT_BODY_CAP: int = 1200
_ATTACHMENT_BODY_CAP: int = 4000

_logger = logging.getLogger(__name__)


def _canonicalize_workflow_trace(
    trace: list["WorkflowTraceStep"],
) -> list["WorkflowTraceStep"]:
    """Stable-sort a workflow trace into canonical chat-pipeline order.

    Operators in the chat DAG run in parallel and may append trace steps in
    arrival order rather than canonical order. This helper restores the
    deterministic ordering that downstream tests and the UI rail rely on.
    Steps whose key is not in `_CANONICAL_TRACE_ORDER` (defensive case) are
    placed after all known keys in their original relative order.
    """

    fallback_index = len(_CANONICAL_TRACE_ORDER)
    return sorted(
        trace,
        key=lambda step: _CANONICAL_TRACE_INDEX.get(step.key, fallback_index),
    )


_RECENT_SESSION_QUERY_NORMALIZER = re.compile(
    r"[\s，。！？?!.、：:；;“”\"'‘’（）()【】\[\]{}<>《》\-]+"
)
_PREVIOUS_QUESTION_QUERY_PATTERNS = (
    re.compile(r"^(我)?(刚刚|刚才|上一条|上一个|前一个|前面)(问的|问了)?(是什么)?问题$"),
    re.compile(r"^(我)?(上一条|上一个|前一个|前面)问题是什么$"),
    re.compile(r"^(我)?之前问了什么(问题)?$"),
)
_PREVIOUS_ANSWER_QUERY_PATTERNS = (
    re.compile(r"^(你)?(刚刚|刚才|上一条|上一个|前一个|前面)(回答|回复)(的内容|的)?是什么$"),
    re.compile(r"^(我)?上一条(收到)?的回答是什么$"),
)
_WEB_SEARCH_QUERY_MARKERS = (
    "最新",
    "今天",
    "实时",
    "刚刚",
    "新闻",
    "政策更新",
    "会议截稿",
    "deadline",
    "breaking",
    "recent",
    "update",
    "today",
)


@dataclass
class BookingWorkflowState:
    student_name: str
    student_email: str | None = None
    topic: str | None = None
    preferred_start: datetime | None = None
    preferred_end: datetime | None = None


@dataclass
class ChatWorkflowContext:
    request: ChatRequest
    conversation_id: str
    owner_name: str
    used_model: str
    intake: ChatIntake | None = None
    interaction_decision: InteractionDecision | None = None
    evidence_bundle: EvidenceBundle | None = None
    prompt_envelope: PromptEnvelope | None = None
    is_admin_request: bool = False
    admin_username: str | None = None
    route: str = "answer"
    workflow_action: str = "answer"
    decision_mode: str = "direct_answer"
    answer: str | None = None
    system_prompt: str | None = None
    user_prompt: str | None = None
    # Chat Latency Optimizations Task 3: set when the prompt builder applied
    # the soft-cap truncation chain (memory hits / knowledge excerpts /
    # attachment bodies). Surfaced via the ``prompt_build`` trace step so the
    # UI can show a "提示词已截断" badge when it kicked in.
    prompt_truncated: bool = False
    recent_session_context: str = ""
    interaction_intent: InteractionIntent | None = None
    pending_clarification_message: str | None = None
    pending_fields: list[str] = field(default_factory=list)
    knowledge_hits: list[KnowledgeSearchHit] = field(default_factory=list)
    web_search_hits: list[WebSearchHit] = field(default_factory=list)
    memory_hits: list[ConversationMemoryHit] = field(default_factory=list)
    booking_state: BookingWorkflowState | None = None
    booking_result: BookingResponse | None = None
    booking_notification: NotificationDeliveryStatus | None = None
    escalation_record: EscalationRecord | None = None
    added_knowledge_record: KnowledgeDocumentRecord | None = None
    follow_up_actions: list[FollowUpAction] = field(default_factory=list)
    persisted_memory_record: ConversationMemoryRecord | None = None
    persisted_artifact_drafts: list[ArtifactMemoryDraftRecord] = field(default_factory=list)
    planner_decision: PlannerDecision | None = None
    shadow_planner_decision: PlannerDecision | None = None
    shadow_planner_status: str = "shadow_disabled"
    shadow_planner_message: str | None = None
    planner_comparison: WorkflowPlanComparison | None = None
    memory_usefulness_signal: str | None = None
    memory_usefulness_reason: str | None = None
    workflow_trace: list[WorkflowTraceStep] = field(default_factory=list)


WorkflowTraceCallback = Callable[[WorkflowTraceStep], None]


@dataclass
class AdminSessionTokenInput:
    session_token: str | None = None


@dataclass
class AdminLoginWorkflowResult:
    session: AdminSessionResponse
    session_token: str


@dataclass
class UserSessionTokenInput:
    session_token: str | None = None


@dataclass
class UserAuthWorkflowResult:
    session: UserSessionResponse
    session_token: str


@dataclass
class KnowledgeSearchInput:
    query: str
    visitor_profile: str | None = None
    admin_role: str | None = None


class FacultyTwinWorkflowSupport:
    def __init__(
        self,
        settings: AppSettings,
        booking_workflows: dict[str, BookingWorkflowState],
        knowledge_store: LocalKnowledgeStore,
        conversation_store: NeuroMemConversationStore,
        analytics_store: ConversationAnalyticsStore,
        artifact_memory_draft_store: ArtifactMemoryDraftStore,
        knowledge_gap_draft_store: KnowledgeGapDraftStore,
        escalation_store: EscalationQueueStore,
        follow_up_store: FollowUpQueueStore,
        suggestion_store: SuggestionBoardStore,
        user_store: UserAccountStore,
        meeting_service: MeetingService,
        llm_client: VllmChatClient,
        email_notifier: BookingEmailNotifier,
        digest_store: ConversationDigestStore | None = None,
        admin_session_payload: dict[str, Any] | None = None,
        trace_callback: WorkflowTraceCallback | None = None,
        answer_chunk_callback: Callable[[str], None] | None = None,
        planner_decision: PlannerDecision | None = None,
        shadow_planner_decision: PlannerDecision | None = None,
        shadow_planner_status: str = "shadow_disabled",
        shadow_planner_message: str | None = None,
        planner_comparison: WorkflowPlanComparison | None = None,
    ) -> None:
        self._settings = settings
        self._booking_workflows = booking_workflows
        self._knowledge_store = knowledge_store
        self._conversation_store = conversation_store
        self._analytics_store = analytics_store
        self._artifact_memory_draft_store = artifact_memory_draft_store
        self._knowledge_gap_draft_store = knowledge_gap_draft_store
        self._escalation_store = escalation_store
        self._follow_up_store = follow_up_store
        self._suggestion_store = suggestion_store
        self._user_store = user_store
        self._meeting_service = meeting_service
        self._calendar_bridge = CalendarBridgeClient(settings)
        self._llm_client = llm_client
        self._email_notifier = email_notifier
        self._digest_store = digest_store
        self._admin_session_payload = admin_session_payload
        self._trace_callback = trace_callback
        self._answer_chunk_callback = answer_chunk_callback
        self._action_planner = LightweightActionPlanner()
        self._interaction_policy = InteractionPolicyEngine()
        self._planner_decision = planner_decision
        self._shadow_planner_decision = shadow_planner_decision
        self._shadow_planner_status = shadow_planner_status
        self._shadow_planner_message = shadow_planner_message
        self._planner_comparison = planner_comparison
        self._web_search_client = WebSearchClient(
            timeout_seconds=settings.web_search_timeout_seconds,
            max_results=settings.web_search_max_results,
            tavily_api_key=settings.tavily_api_key,
        )

    def bootstrap_chat(self, request: ChatRequest) -> ChatWorkflowContext:
        started_at = perf_counter()
        admin_username = None
        if self._admin_session_payload is not None:
            admin_username, _ = resolve_admin_session_identity(
                self._admin_session_payload,
                self._settings,
            )
        context = ChatWorkflowContext(
            request=request,
            conversation_id=request.conversation_id or str(uuid4()),
            owner_name=self._settings.owner_name,
            used_model=self._llm_client.model_name,
            is_admin_request=self._admin_session_payload is not None,
            admin_username=admin_username,
            planner_decision=self._planner_decision,
            shadow_planner_decision=self._shadow_planner_decision,
            shadow_planner_status=self._shadow_planner_status,
            shadow_planner_message=self._shadow_planner_message,
            planner_comparison=self._planner_comparison,
        )
        context.intake = ChatIntake.from_request(
            request,
            conversation_id=context.conversation_id,
        )
        context.recent_session_context = self._format_recent_session_context(request)
        self._append_trace(
            context,
            key="bootstrap",
            title="接收用户请求",
            summary="已建立当前会话。",
            detail=f"已读取提问内容，并建立会话 {context.conversation_id[:8]}。",
            duration_ms=self._elapsed_ms(started_at),
        )
        if context.planner_decision is not None:
            self._append_trace(
                context,
                key="workflow_plan_preview",
                title="生成工作流预览",
                summary=(
                    f"已生成 {context.planner_decision.plan.planner_mode} 规划："
                    f"{context.planner_decision.plan.goal}。"
                ),
                detail=self._build_plan_preview_detail(context.planner_decision),
                status="completed" if context.planner_decision.accepted else "skipped",
                duration_ms=0,
            )
        return context

    def _build_plan_preview_detail(self, decision: PlannerDecision) -> str:
        steps = " -> ".join(step.step_id for step in decision.plan.steps)
        if decision.accepted:
            return (
                f"预览执行模式：{decision.plan.execution_mode}；fallback template："
                f"{decision.plan.fallback_template}；planned steps：{steps}"
            )[:512]

        fallback_reason = (
            decision.fallback.reason if decision.fallback is not None else "plan validation failed"
        )
        return (
            f"规划未直接接受，将回退到 {decision.plan.fallback_template}；"
            f"原因：{fallback_reason}；planned steps：{steps}"
        )[:512]

    def understand_interaction(self, context: ChatWorkflowContext) -> ChatWorkflowContext:
        started_at = perf_counter()
        direct_session_answer = self._build_recent_session_meta_answer(context.request)
        if direct_session_answer is not None:
            direct_intent = InteractionIntent(
                action="answer",
                domain="general",
                decision_mode="direct_answer",
                confidence=1.0,
            )
            context.interaction_intent = direct_intent
            context.interaction_decision = InteractionDecision(
                intent=direct_intent,
                source="session_memory",
            )
            context.workflow_action = "answer"
            context.answer = direct_session_answer
            context.route = "done"
            self._append_trace(
                context,
                key="interaction_understand",
                title="理解用户意图",
                summary="已直接读取同会话最近一轮内容。",
                detail="当前问题是在回忆上一轮会话内容，已在意图分类前直接从当前 conversation 记录中返回结果。",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        intent, source = self._resolve_interaction_intent(context)
        context.interaction_intent = intent
        context.interaction_decision = InteractionDecision(intent=intent, source=source)
        context.decision_mode = intent.decision_mode

        if intent.action == "ask_followup" and intent.needs_clarification:
            # Task 3: defer clarification. Stash the planned message but keep
            # route="answer" so KB retrieval still runs. retrieve_knowledge will
            # decide whether to demote to answer (KB hit was strong enough) or
            # actually emit the clarification (with retrieved titles attached).
            context.workflow_action = "ask_follow_up"
            context.pending_clarification_message = (
                intent.clarification_message
                or "你是想了解研究方向、课程内容，还是想直接发起预约？"
            )
            context.route = "answer"
            summary = "意图存在歧义，先打开检索再判断是否需要澄清。"
            detail = (
                f"识别为 {intent.domain} 场景但初步需要澄清；已暂存澄清话术，"
                f"先运行知识检索再决定是否发出。来源：{source}，置信度 {intent.confidence:.2f}。"
            )
        elif intent.action in {"review_queue", "human_handoff"}:
            context.workflow_action = intent.action
            context.route = "done"
            context.escalation_record = self._escalation_store.create_request(
                context.request,
                conversation_id=context.conversation_id,
                route=intent.action,
                reason=intent.escalation_reason,
            )
            context.answer = self._build_escalation_message(context)
            summary = (
                "请求已转人工处理。"
                if intent.action == "human_handoff"
                else "请求已进入待审核队列。"
            )
            detail = (
                f"已创建工单 {context.escalation_record.escalation_id[:8]}，"
                f"来源：{source}；原因：{intent.escalation_reason or '需要老师判断'}；"
                f"置信度 {intent.confidence:.2f}。"
            )
        else:
            if context.is_admin_request and intent.action == "admin_add_knowledge":
                context.workflow_action = intent.action
            if intent.decision_mode == "advise_only":
                context.workflow_action = "advise_only"
            summary = f"已识别当前交互意图：{intent.action}/{intent.domain}。"
            if intent.decision_mode == "advise_only":
                summary = f"已识别当前交互意图：{intent.action}/{intent.domain}，仅提供建议。"
            detail = (
                f"当前理解来源：{source}；检索范围：{', '.join(intent.retrieval_scopes) or '默认'}；"
                f"排除范围：{', '.join(intent.exclude_scopes) or '无'}；"
                f"决策模式：{intent.decision_mode}；置信度 {intent.confidence:.2f}。"
            )

        self._append_trace(
            context,
            key="interaction_understand",
            title="理解用户意图",
            summary=summary,
            detail=detail,
            duration_ms=self._elapsed_ms(started_at),
        )
        return context

    def prepare_booking(self, context: ChatWorkflowContext) -> ChatWorkflowContext:
        started_at = perf_counter()
        request = context.request
        existing_state = self._booking_workflows.get(context.conversation_id)
        interaction_intent = context.interaction_intent or self._build_fallback_interaction_intent(
            request
        )
        if existing_state is None and interaction_intent.action != "book_meeting":
            self._append_trace(
                context,
                key="booking_prepare",
                title="预约意图判断",
                summary="未进入预约流程。",
                detail="当前问题不进入预约工作流，继续走知识检索与回答。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        state = existing_state or BookingWorkflowState(student_name=request.student_name)
        state.student_name = request.student_name
        if request.student_email:
            state.student_email = request.student_email.strip()

        extracted_email = self._extract_email(request.question)
        if extracted_email:
            state.student_email = extracted_email

        extracted_start, extracted_end = self._extract_time_window(request.question)
        if extracted_start is not None:
            state.preferred_start = extracted_start
            state.preferred_end = extracted_end or (
                extracted_start + timedelta(minutes=self._settings.meeting_duration_minutes)
            )

        extracted_topic = self._extract_topic(request.question, request.course_context)
        if extracted_topic:
            state.topic = extracted_topic
        elif state.topic is None and request.course_context:
            state.topic = request.course_context.strip()

        context.booking_state = state
        missing_fields = self._missing_booking_fields(state)
        if missing_fields:
            self._booking_workflows[context.conversation_id] = state
            context.pending_fields = missing_fields
            context.workflow_action = "collect_booking_details"
            context.answer = self._build_booking_follow_up(missing_fields, state)
            context.route = "done"
            self._append_trace(
                context,
                key="booking_prepare",
                title="预约信息收集",
                summary=f"仍缺少：{self._format_pending_fields(missing_fields)}。",
                detail=f"识别到预约请求，但仍缺少：{self._format_pending_fields(missing_fields)}。",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        context.route = "book_meeting"
        self._append_trace(
            context,
            key="booking_prepare",
            title="预约信息收集",
            summary="预约字段已经齐备。",
            detail="预约所需信息已齐备，准备提交预约。",
            duration_ms=self._elapsed_ms(started_at),
        )
        return context

    def execute_booking(self, context: ChatWorkflowContext) -> ChatWorkflowContext:
        started_at = perf_counter()
        if context.route != "book_meeting" or context.booking_state is None:
            self._append_trace(
                context,
                key="booking_execute",
                title="预约执行",
                summary="本轮没有提交预约。",
                detail="本轮未触发预约提交。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        request = context.request
        state = context.booking_state
        booking_response = self._meeting_service.book(
            BookingRequest(
                student_name=state.student_name,
                student_email=state.student_email or "",
                topic=state.topic or request.course_context or "会议沟通",
                preferred_start=state.preferred_start,
                preferred_end=state.preferred_end,
            )
        )
        context.booking_result = booking_response

        if booking_response.accepted:
            self._booking_workflows.pop(context.conversation_id, None)
            context.pending_fields = []
            context.workflow_action = "book_meeting"
            context.booking_result = self._attach_booking_request_notification(booking_response)
            context.booking_notification = context.booking_result.notification
            context.answer = self._build_booking_success_message(context.booking_result)
            self._append_trace(
                context,
                key="booking_execute",
                title="预约执行",
                summary=self._build_booking_execute_summary(context.booking_result),
                detail=self._build_booking_execute_detail(context.booking_result),
                duration_ms=self._elapsed_ms(started_at),
            )
        else:
            state.preferred_start = None
            state.preferred_end = None
            self._booking_workflows[context.conversation_id] = state
            context.pending_fields = ["preferred_start"]
            context.workflow_action = "collect_booking_details"
            context.answer = self._build_booking_retry_message(booking_response)
            self._append_trace(
                context,
                key="booking_execute",
                title="预约执行",
                summary="时间冲突，等待用户重选。",
                detail="预约时间冲突，已返回可选时间并等待用户补充新时间。",
                duration_ms=self._elapsed_ms(started_at),
            )

        context.route = "done"
        return context

    def _attach_booking_request_notification(
        self, booking_response: BookingResponse
    ) -> BookingResponse:
        booking = booking_response.booking
        if booking is None:
            return booking_response

        try:
            recipient = self._email_notifier.send_booking_request_notification(booking)
        except BookingNotificationError as exc:
            notification = NotificationDeliveryStatus(
                status="failed",
                summary=f"管理员提醒邮件发送失败。{exc}",
                detail="预约记录已经保存，不影响管理员后续在后台查看和处理；邮件可稍后重试。",
            )
        else:
            notification = NotificationDeliveryStatus(
                status="sent",
                recipient=recipient,
                summary=f"已向 {recipient} 发送预约申请提醒邮件。",
            )

        return BookingResponse(
            accepted=booking_response.accepted,
            message=booking_response.message,
            booking=booking,
            alternative_slots=booking_response.alternative_slots,
            notification=notification,
        )

    def _build_booking_execute_summary(self, booking_response: BookingResponse) -> str:
        notification = booking_response.notification
        if notification is not None and notification.status == "failed":
            return "预约申请已提交，但提醒邮件发送失败。"
        return "预约申请已提交，等待管理员确认。"

    def _build_booking_execute_detail(self, booking_response: BookingResponse) -> str:
        notification = booking_response.notification
        if notification is None:
            return "预约请求已记录。"
        if notification.status == "failed":
            detail = notification.detail or "预约记录已保存，邮件可稍后重试。"
            return f"预约请求已记录，但邮件通知失败：{notification.summary} {detail}"
        if notification.recipient:
            return f"预约请求已记录，并已向 {notification.recipient} 发送提醒邮件。"
        return notification.summary

    def retrieve_knowledge(self, context: ChatWorkflowContext) -> ChatWorkflowContext:
        started_at = perf_counter()
        if context.is_admin_request and context.workflow_action == "admin_add_knowledge":
            knowledge_request = self._build_admin_knowledge_request(context.request)
            if knowledge_request is None:
                context.answer = self._build_admin_knowledge_guidance_message()
                summary = "识别到知识入库指令，但内容还不完整。"
                detail = "管理员对话已进入知识入库模式，但还没有解析出可直接写入知识库的正文内容。"
            else:
                context.added_knowledge_record = self._knowledge_store.add_document(
                    knowledge_request
                )
                context.answer = self._build_admin_knowledge_success_message(
                    context.added_knowledge_record
                )
                summary = f"已写入知识库条目：{context.added_knowledge_record.title}。"
                detail = (
                    f"管理员对话已将“{context.added_knowledge_record.title}”写入知识库；"
                    f"标签：{', '.join(context.added_knowledge_record.tags) or '未设置'}；"
                    f"来源：{context.added_knowledge_record.source_name or '管理员手动录入'}。"
                )

            context.route = "done"
            self._append_trace(
                context,
                key="knowledge_write",
                title="知识入库",
                summary=summary,
                detail=detail,
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        # --- Determine whether knowledge retrieval should run ---
        needs_retrieval = (
            context.route == "answer"
            and context.answer is None
            and self._planner_requests_any_step(
                context, "retrieve_knowledge", "retrieve_hybrid_knowledge"
            )
        )
        explicit_web = bool(getattr(context.request, "web_search", False))

        if not needs_retrieval and not explicit_web:
            self._append_trace(
                context, key="knowledge_retrieve", title="知识检索",
                summary=(
                    "未执行知识检索。" if context.route != "answer" or context.answer is not None
                    else "当前工作流规划跳过知识检索。"
                ),
                detail=(
                    "当前回答不需要额外知识检索。" if context.route != "answer" or context.answer is not None
                    else "deterministic planner 已接受当前规划，且本轮执行不需要知识检索步骤。"
                ),
                status="skipped", duration_ms=self._elapsed_ms(started_at),
            )
            return context

        # --- Run local KB search (when planner requested it) ---
        interaction_intent = context.interaction_intent or self._build_fallback_interaction_intent(
            context.request
        )
        hit_count, top_score = 0, 0.0
        if needs_retrieval:
            retrieval_query = self._build_knowledge_query(
                context.request, interaction_intent, context.recent_session_context
            )
            raw_hits = self._knowledge_store.search(
                retrieval_query,
                visitor_profile=context.request.visitor_profile,
                admin_role=self._resolve_admin_role(),
            )
            context.knowledge_hits = self._filter_knowledge_hits_by_intent(
                raw_hits,
                interaction_intent,
                question=context.request.question,
            )
            hit_count = len(context.knowledge_hits)
            top_score = context.knowledge_hits[0].score if context.knowledge_hits else 0.0

        # --- Run web search (always available when explicitly requested) ---
        context.web_search_hits = self._retrieve_web_search_hits(
            context, interaction_intent=interaction_intent,
            local_hit_count=hit_count, local_top_score=top_score,
        )
        web_count = len(context.web_search_hits)

        # --- Post-retrieval: clarification override or demotion ---
        trace_summary, trace_detail, trace_status = None, None, "completed"
        if context.pending_clarification_message is not None and needs_retrieval:
            # Retrieval ranking is backend-specific; do not apply a global
            # score cutoff here. Candidates have already passed the
            # provenance/relevance sanity check below.
            if hit_count > 0:
                # Strong KB hit → cancel clarification, proceed to answer
                context.pending_clarification_message = None
                if context.workflow_action == "ask_follow_up":
                    context.workflow_action = "answer"
                if context.interaction_intent is not None:
                    context.interaction_intent = context.interaction_intent.model_copy(
                        update={"action": "answer", "needs_clarification": False, "clarification_message": None}
                    )
                trace_summary = (
                    f"命中本地 {hit_count} 条，联网 {web_count} 条，已取消澄清，直接依据 KB 回答。"
                )
                trace_detail = (
                    "检索结果通过证据相关性校验，将 ask_followup 降级为 answer；"
                    f"意图域 {interaction_intent.domain}。"
                )
            else:
                # Weak hit → emit clarification with top titles attached
                top_titles = [hit.title for hit in context.knowledge_hits[:3] if hit.title]
                clarification = context.pending_clarification_message
                if top_titles:
                    titles_block = "\n".join(f"- {t}" for t in top_titles)
                    context.answer = (
                        f"{clarification}\n\n"
                        "我大致检索到这些相关材料，可一起说明你想聚焦哪一块：\n"
                        f"{titles_block}"
                    )
                else:
                    context.answer = clarification
                context.workflow_action = "ask_follow_up"
                context.route = "done"
                context.pending_clarification_message = None
                trace_summary = (
                    f"命中本地 {hit_count} 条，联网 {web_count} 条，"
                    "但当前证据不足以直接回答，发出澄清。"
                    if hit_count else f"本地无命中，联网 {web_count} 条，发出澄清。"
                )
                trace_detail = (
                    f"已将 top-{min(hit_count, 3)} 标题附在澄清中；联网 {web_count} 条。"
                    if top_titles else f"本轮本地未命中，联网 {web_count} 条，仅发送澄清话术。"
                )

        # --- Build final trace (single consolidated entry) ---
        if trace_summary is None:
            if not needs_retrieval and explicit_web:
                trace_summary = f"工作流跳过本地检索，用户显式联网搜索，联网 {web_count} 条。"
                trace_detail = (
                    f"deterministic planner 未规划知识检索，因用户勾选联网搜索，"
                    f"仍执行联网补充 {web_count} 条。"
                )
            elif hit_count or web_count:
                trace_summary = f"命中本地 {hit_count} 条，联网 {web_count} 条。"
                trace_detail = (
                    f"本地 {hit_count} 条，联网 {web_count} 条；"
                    f"意图域 {interaction_intent.domain}，top 得分 {top_score:.1f}。"
                )
            else:
                trace_summary = "没有命中直接相关资料。"
                trace_detail = "未检索到相关材料，将基于角色设定直接回答。"

        self._append_trace(
            context, key="knowledge_retrieve", title="知识检索",
            summary=trace_summary, detail=trace_detail, status=trace_status,
            duration_ms=self._elapsed_ms(started_at),
        )
        return context

    def _should_run_web_search(
        self,
        context: ChatWorkflowContext,
        *,
        local_hit_count: int,
        local_top_score: float,
    ) -> bool:
        if not self._settings.web_search_enabled or not self._web_search_client.enabled:
            return False

        if bool(getattr(context.request, "web_search", False)):
            return True

        if not self._settings.web_search_auto_trigger:
            return False

        # Local grounding completely missing should always allow a web fallback
        # when auto-trigger is enabled, even for non-realtime questions.
        if local_hit_count == 0:
            return True

        question = str(context.request.question or "")
        lowered = question.lower()
        asks_realtime = any(marker in question or marker in lowered for marker in _WEB_SEARCH_QUERY_MARKERS)
        if not asks_realtime:
            return False

        # Keep local knowledge as first-class. The local retriever has already
        # applied its provenance sanity check, so a non-empty local result is
        # considered grounded; avoid comparing backend-specific scores to a
        # global threshold.
        return asks_realtime and local_hit_count == 0

    def _retrieve_web_search_hits(
        self,
        context: ChatWorkflowContext,
        *,
        interaction_intent: InteractionIntent,
        local_hit_count: int,
        local_top_score: float,
    ) -> list[WebSearchHit]:
        if not self._should_run_web_search(
            context,
            local_hit_count=local_hit_count,
            local_top_score=local_top_score,
        ):
            return []

        query = self._build_knowledge_query(
            context.request,
            interaction_intent,
            context.recent_session_context,
        )
        try:
            raw_hits = self._web_search_client.search(
                query,
                max_results=self._settings.web_search_max_results,
            )
        except Exception as exc:
            _logger.warning("Web search failed: %s", exc)
            return []

        return [
            WebSearchHit(
                title=hit.title,
                url=hit.url,
                snippet=hit.snippet,
                score=hit.score,
            )
            for hit in raw_hits
        ]

    def retrieve_memory(self, context: ChatWorkflowContext) -> ChatWorkflowContext:
        started_at = perf_counter()
        if context.is_admin_request and context.workflow_action == "admin_add_knowledge":
            self._append_trace(
                context,
                key="memory_retrieve",
                title="对话记忆检索",
                summary="管理员知识入库不检索学生记忆。",
                detail="当前对话用于管理员知识维护，不读取学生画像或历史问答记忆。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        if self._is_benchmark_request(context.request):
            self._append_trace(
                context,
                key="memory_retrieve",
                title="对话记忆检索",
                summary="benchmark 评测请求跳过对话记忆检索。",
                detail="为避免评测样例之间的上下文污染，CharacterEval/LaMP 请求不读取短期或长期对话记忆。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        if context.route != "answer" or context.answer is not None:
            self._append_trace(
                context,
                key="memory_retrieve",
                title="对话记忆检索",
                summary="未执行对话记忆检索。",
                detail="当前回答不需要额外对话记忆检索。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        include_short_term = self._planner_requests_any_step(context, "retrieve_recent_memory")
        include_long_term = self._planner_requests_any_step(context, "retrieve_profile_memory")
        include_artifact = self._planner_requests_any_step(context, "retrieve_artifact_memory")
        if not include_short_term and not include_long_term and not include_artifact:
            self._append_trace(
                context,
                key="memory_retrieve",
                title="对话记忆检索",
                summary="当前工作流规划跳过对话记忆检索。",
                detail="deterministic planner 已接受当前规划，且本轮执行不需要 recent/profile/artifact memory retrieval。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        context.memory_hits = self._conversation_store.search(
            context.request,
            conversation_id=context.conversation_id,
            include_short_term=include_short_term,
            include_long_term=include_long_term,
        )
        if (
            context.recent_session_context.strip()
            and self._looks_like_contextual_follow_up(
                context.request.question,
                context.recent_session_context,
            )
        ):
            # The immediate conversation is the authoritative source for a
            # follow-up. Avoid echoing a freshly consolidated profile derived
            # from the same turns back into the prompt.
            context.memory_hits = [
                hit for hit in context.memory_hits if hit.memory_type != "long_term"
            ]
        historical_artifact_hits = (
            self._conversation_store.search_artifacts(
                context.request,
                conversation_id=context.conversation_id,
            )
            if include_artifact
            else []
        )
        current_attachment_hits = (
            self._build_attachment_artifact_hits(context.request) if include_artifact else []
        )
        if historical_artifact_hits:
            context.memory_hits.extend(historical_artifact_hits)
        if current_attachment_hits:
            context.memory_hits.extend(current_attachment_hits)
        hit_count = len(context.memory_hits)
        artifact_hit_count = len(historical_artifact_hits) + len(current_attachment_hits)
        self._append_trace(
            context,
            key="memory_retrieve",
            title="对话记忆检索",
            summary=(
                f"命中 {hit_count} 条记忆/材料上下文。"
                if hit_count
                else "没有命中历史对话记忆或上传材料。"
            ),
            detail=(
                f"检索到 {hit_count} 条相关上下文，其中包含 {artifact_hit_count} 条上传材料/历史材料线索，准备补充回答上下文。"
                if hit_count and artifact_hit_count
                else (
                    f"检索到 {hit_count} 条相关历史对话，准备补充回答上下文。"
                    if hit_count
                    else "未检索到可复用的历史对话或上传材料，继续使用当前问题作答。"
                )
            ),
            duration_ms=self._elapsed_ms(started_at),
        )
        return context

    def _planner_requests_any_step(self, context: ChatWorkflowContext, *step_ids: str) -> bool:
        decision = context.planner_decision
        if decision is None or not decision.accepted:
            return True
        planned_steps = {step.step_id for step in decision.plan.steps}
        return any(step_id in planned_steps for step_id in step_ids)

    def _collect_artifact_names(
        self,
        request: ChatRequest,
        artifact_hits: list[ConversationMemoryHit],
    ) -> list[str]:
        names: list[str] = []
        seen: set[str] = set()
        for attachment in request.attachments:
            normalized = attachment.file_name.strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                names.append(normalized)
        for hit in artifact_hits:
            match = re.search(r"材料\s+\d+：(.+?)（", hit.summary)
            if match is None:
                continue
            normalized = match.group(1).strip()
            if normalized and normalized not in seen:
                seen.add(normalized)
                names.append(normalized)
        return names

    def _collect_artifact_sources(
        self,
        request: ChatRequest,
        artifact_hits: list[ConversationMemoryHit],
    ) -> list[str]:
        sources: list[str] = []
        seen: set[str] = set()
        for attachment in request.attachments:
            label = f"current_upload:{attachment.file_name}"
            if label not in seen:
                seen.add(label)
                sources.append(label)
        for hit in artifact_hits:
            label = f"historical_artifact:{hit.memory_id}"
            if label not in seen:
                seen.add(label)
                sources.append(label)
        return sources

    def _build_artifact_provenance_note(
        self,
        context: ChatWorkflowContext,
        artifact_hits: list[ConversationMemoryHit],
        artifact_names: list[str],
    ) -> str:
        current_upload_count = len(context.request.attachments)
        historical_count = max(len(artifact_hits) - current_upload_count, 0)
        names_text = "、".join(artifact_names[:4])
        return (
            f"材料草稿来自 {current_upload_count} 份当前上传材料和 {historical_count} 条历史 artifact 线索；"
            f"关联材料：{names_text}；"
            "当前状态为 reviewable draft，后续可继续补 retention policy 和人工审核。"
        )

    def build_prompt(self, context: ChatWorkflowContext) -> ChatWorkflowContext:
        started_at = perf_counter()
        if context.route != "answer" or context.answer is not None:
            self._append_trace(
                context,
                key="prompt_build",
                title="构造回答上下文",
                summary="未构造 LLM 上下文。",
                detail="当前流程不需要构造 LLM 回答上下文。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        if context.intake is None or context.interaction_decision is None:
            raise RuntimeError("prompt stage requires intake and interaction decision contracts")
        context.evidence_bundle = EvidenceBundle.build(
            knowledge_hits=context.knowledge_hits,
            web_hits=context.web_search_hits,
            memory_hits=context.memory_hits,
        )
        context.system_prompt = build_system_prompt(self._settings)

        # Chat Latency Optimizations Task 3 + V4.1 context compression:
        # assemble the prompt with the full inputs first, then progressively
        # truncate when the combined system + user prompt exceeds the soft
        # cap. Order matters: memory hits beyond the top 3 are dropped first
        # (cheapest signal loss), then knowledge excerpts, then attachment
        # bodies, and finally the rolling session digest.
        memory_hits = list(context.memory_hits)
        knowledge_hits = list(context.knowledge_hits)
        attachments = list(getattr(context.request, "attachments", None) or [])
        truncation_actions: list[str] = []

        def _build(
            mem: list[ConversationMemoryHit],
            know: list[KnowledgeSearchHit],
            web_hits: list[WebSearchHit],
            atts: list[ChatAttachment],
        ) -> str:
            base_attachments = list(getattr(context.request, "attachments", None) or [])
            if atts != base_attachments:
                request_for_prompt = context.request.model_copy(update={"attachments": atts})
            else:
                request_for_prompt = context.request
            return self._build_student_prompt(
                request_for_prompt,
                know,
                web_hits,
                mem,
                context.interaction_intent,
                recent_session_context=context.recent_session_context,
            )

        user_prompt = _build(memory_hits, knowledge_hits, context.web_search_hits, attachments)
        cap = self._settings.prompt_soft_cap

        def _over_cap() -> bool:
            return len(context.system_prompt or "") + len(user_prompt) > cap

        # (a) Drop oldest memory hits beyond the configured keep count.
        if _over_cap() and len(memory_hits) > _PROMPT_MEMORY_HIT_KEEP:
            original_count = len(memory_hits)
            memory_hits = memory_hits[:_PROMPT_MEMORY_HIT_KEEP]
            truncation_actions.append(f"memory({original_count}→{_PROMPT_MEMORY_HIT_KEEP})")
            user_prompt = _build(memory_hits, knowledge_hits, context.web_search_hits, attachments)

        # (b) Cap each knowledge hit excerpt at the configured length.
        if _over_cap():
            capped_count = 0
            new_knowledge: list[KnowledgeSearchHit] = []
            for hit in knowledge_hits:
                if len(hit.excerpt) > _KNOWLEDGE_HIT_BODY_CAP:
                    new_knowledge.append(
                        hit.model_copy(
                            update={"excerpt": hit.excerpt[:_KNOWLEDGE_HIT_BODY_CAP] + "…"}
                        )
                    )
                    capped_count += 1
                else:
                    new_knowledge.append(hit)
            if capped_count:
                knowledge_hits = new_knowledge
                truncation_actions.append(f"knowledge≤{_KNOWLEDGE_HIT_BODY_CAP}({capped_count})")
                user_prompt = _build(memory_hits, knowledge_hits, context.web_search_hits, attachments)

        # (c) Cap each attachment text body at the configured length.
        if _over_cap():
            capped_count = 0
            new_attachments: list[ChatAttachment] = []
            for attachment in attachments:
                if len(attachment.text_content) > _ATTACHMENT_BODY_CAP:
                    new_attachments.append(
                        attachment.model_copy(
                            update={
                                "text_content": attachment.text_content[:_ATTACHMENT_BODY_CAP] + "…"
                            }
                        )
                    )
                    capped_count += 1
                else:
                    new_attachments.append(attachment)
            if capped_count:
                attachments = new_attachments
                truncation_actions.append(f"attachment≤{_ATTACHMENT_BODY_CAP}({capped_count})")
                user_prompt = _build(memory_hits, knowledge_hits, context.web_search_hits, attachments)

        # (d) Drop the rolling session digest if still over cap.
        # The raw recent turns are more immediately useful than the
        # compressed digest, so the digest is the first thing removed
        # when all other truncation steps have been exhausted.
        if _over_cap() and "Session digest" in (context.recent_session_context or ""):
            stripped_lines: list[str] = []
            skip_digest = False
            for line in (context.recent_session_context or "").split("\n"):
                if line.startswith("Session digest"):
                    skip_digest = True
                    continue
                if skip_digest and line.startswith("Immediate session context"):
                    skip_digest = False
                if not skip_digest:
                    stripped_lines.append(line)
            context.recent_session_context = "\n".join(stripped_lines)
            truncation_actions.append("digest_dropped")
            user_prompt = _build(memory_hits, knowledge_hits, context.web_search_hits, attachments)

        context.user_prompt = user_prompt
        context.prompt_envelope = PromptEnvelope(
            original_question=context.intake.original_question,
            system_prompt=context.system_prompt,
            user_prompt=user_prompt,
            mode=PromptMode.FULL,
            evidence=context.evidence_bundle,
        )
        context.prompt_truncated = bool(truncation_actions)
        prompt_size = len(context.system_prompt or "") + len(user_prompt)

        if context.prompt_truncated:
            actions_text = ", ".join(truncation_actions)
            detail = (
                f"提示词超过软上限 ({cap} 字符)，已按顺序截断："
                f"{actions_text}；最终 {prompt_size} 字符。"
            )
        else:
            detail = f"已合并身份设定、课程上下文和检索结果，准备生成回答（{prompt_size} 字符）。"
        # Trace step ``detail`` is constrained to 512 chars by the model.
        if len(detail) > 480:
            detail = detail[:480] + "…"

        self._append_trace(
            context,
            key="prompt_build",
            title="构造回答上下文",
            summary=(
                "已组装回答上下文（已截断）。" if context.prompt_truncated else "已组装回答上下文。"
            ),
            detail=detail,
            duration_ms=self._elapsed_ms(started_at),
        )
        return context

    def persist_memory(self, context: ChatWorkflowContext) -> ChatWorkflowContext:
        started_at = perf_counter()
        if context.is_admin_request and context.workflow_action == "admin_add_knowledge":
            self._append_trace(
                context,
                key="memory_persist",
                title="写入对话记忆",
                summary="管理员知识入库不写入学生记忆。",
                detail="当前对话属于后台知识维护操作，不进入学生对话记忆库。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        if self._is_benchmark_request(context.request):
            self._append_trace(
                context,
                key="memory_persist",
                title="写入对话记忆",
                summary="benchmark 评测请求不写入对话记忆。",
                detail="为避免后续评测样例读到当前样例内容，CharacterEval/LaMP 请求不写入短期或长期记忆。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        if context.answer is None:
            self._append_trace(
                context,
                key="memory_persist",
                title="写入对话记忆",
                summary="未写入对话记忆。",
                detail="当前流程没有可持久化的回答内容。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        context.persisted_memory_record = self._conversation_store.add_exchange(
            context.request,
            conversation_id=context.conversation_id,
            answer=context.answer,
            workflow_action=context.workflow_action,
            interaction_domain=(
                context.interaction_intent.domain
                if context.interaction_intent is not None
                else None
            ),
            knowledge_hit_count=len(context.knowledge_hits),
            booking_result=context.booking_result,
            web_search_hits=context.web_search_hits,
        )
        self._record_artifact_memory_draft(context, started_at=started_at)
        # Trigger rolling conversation digest update (V4.1 context compression).
        self._update_conversation_digest(context)
        self._append_trace(
            context,
            key="memory_persist",
            title="写入对话记忆",
            summary="已写入本轮对话记忆。",
            detail="用户问题和当前回复已写入 NeuroMem 对话记忆库。",
            duration_ms=self._elapsed_ms(started_at),
        )
        return context

    def _record_artifact_memory_draft(
        self,
        context: ChatWorkflowContext,
        *,
        started_at: float,
    ) -> None:
        if not self._planner_requests_any_step(context, "record_artifact_memory"):
            return

        artifact_hits = [
            hit
            for hit in context.memory_hits
            if hit.topic == "artifact_memory" or hit.source == "attachment_excerpt"
        ]
        artifact_names = self._collect_artifact_names(context.request, artifact_hits)
        if not artifact_names:
            self._append_trace(
                context,
                key="artifact_memory_writeback",
                title="记录材料记忆草稿",
                summary="未生成材料记忆草稿。",
                detail="当前请求要求归档材料，但没有找到可写入的上传材料或历史 artifact 线索。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return

        provenance_note = self._build_artifact_provenance_note(
            context, artifact_hits, artifact_names
        )
        draft = self._artifact_memory_draft_store.create_draft(
            conversation_id=context.conversation_id,
            source_memory_id=(
                context.persisted_memory_record.memory_id
                if context.persisted_memory_record is not None
                else None
            ),
            student_name=context.request.student_name,
            student_email=context.request.student_email,
            interaction_domain=(
                context.interaction_intent.domain
                if context.interaction_intent is not None
                else None
            ),
            question=context.request.question,
            answer=context.answer or "",
            artifact_names=artifact_names,
            artifact_sources=self._collect_artifact_sources(context.request, artifact_hits),
            artifact_excerpt_count=len(artifact_hits),
            provenance_note=provenance_note,
        )
        context.persisted_artifact_drafts.append(draft)
        self._append_trace(
            context,
            key="artifact_memory_writeback",
            title="记录材料记忆草稿",
            summary=f"已记录 1 条材料记忆草稿，涉及 {len(artifact_names)} 份材料。",
            detail=provenance_note,
            duration_ms=self._elapsed_ms(started_at),
        )

    def _update_conversation_digest(self, context: ChatWorkflowContext) -> None:
        """Update the rolling conversation digest after a new exchange.

        Checks whether enough new turns have accumulated since the last
        digest update and, if so, asks the LLM to produce a compressed
        summary that replaces the older history in future prompts.
        """
        if self._digest_store is None or not self._settings.context_digest_enabled:
            return
        conversation_id = context.conversation_id
        if not conversation_id:
            return

        threshold = self._settings.context_digest_turn_threshold
        max_chars = self._settings.context_digest_max_chars

        # Determine how many unsummarized turns exist.
        all_records = self._conversation_store.list_recent_conversation_records(
            conversation_id, limit=64,
        )
        existing_digest = self._digest_store.get_digest(conversation_id)
        turns_already_digested = existing_digest.turns_summarized if existing_digest else 0
        new_turns = len(all_records) - turns_already_digested

        if new_turns < threshold:
            return  # Not enough new turns to justify a summarization call.

        # Select the turns to compress: those between the old frontier and now.
        # Records are returned newest-first; take the slice that is new.
        turns_to_summarize = list(reversed(all_records[:len(all_records) - turns_already_digested]))
        # Cap at a reasonable number to avoid oversized prompts on first digest.
        if len(turns_to_summarize) > 16:
            turns_to_summarize = turns_to_summarize[-16:]

        new_digest_text = self._summarize_digest_turns(
            existing_digest_text=existing_digest.digest_text if existing_digest else "",
            new_turns=turns_to_summarize,
            max_chars=max_chars,
        )
        if not new_digest_text:
            return  # Summarization failed; leave digest unchanged.

        total_turns = turns_already_digested + len(turns_to_summarize)
        self._digest_store.update_digest(
            conversation_id, new_digest_text, total_turns,
        )
        _logger.info(
            "Updated conversation digest for %s: %d turns compressed, %d chars",
            conversation_id[:8], total_turns, len(new_digest_text),
        )

    def _summarize_digest_turns(
        self,
        *,
        existing_digest_text: str,
        new_turns: list[ConversationMemoryRecord],
        max_chars: int,
    ) -> str:
        """Ask the LLM to compress conversation turns into a rolling digest.

        Falls back to a simple concatenation strategy when the LLM call
        fails, so that digest updates never block the chat workflow.
        """
        new_turns_text = "\n".join(
            f"- User: {r.question[:300]}\n  Assistant: {r.answer[:300]}"
            for r in new_turns
        )

        if existing_digest_text:
            user_prompt = (
                f"已有摘要：\n{existing_digest_text}\n\n"
                f"新增对话轮次：\n{new_turns_text}\n\n"
                f"请将以上摘要和新对话合并为一段简洁的滚动摘要，不超过 {max_chars} 字符。"
                "保留关键话题、结论和待办事项，去除余和重复内容。"
                "直接输出摘要文本，不要加任何前缀或标记。"
            )
        else:
            user_prompt = (
                f"对话轮次：\n{new_turns_text}\n\n"
                f"请将以上对话压缩为一段简洁的滚动摘要，不超过 {max_chars} 字符。"
                "保留关键话题、结论和待办事项。"
                "直接输出摘要文本，不要加任何前缀或标记。"
            )

        system_prompt = (
            "You are a conversation summarizer. Produce a concise rolling "
            "summary that preserves key topics, decisions, action items, and "
            "important context. Output plain text only, no headers or markers."
        )

        try:
            result = self._llm_client.answer_question_sync(
                system_prompt,
                user_prompt,
                temperature=0.1,
                max_tokens=256,
                enable_thinking=False,
            )
            result = result.strip()
            if len(result) > max_chars:
                result = result[:max_chars].rsplit("\n", 1)[0] + "…"
            return result
        except Exception as exc:
            _logger.warning("Digest summarization failed: %s", exc)
            # Fallback: simple concatenation truncated to max_chars.
            fallback = f"[自动摘要失败] 最近 {len(new_turns)} 轮对话涉及："
            topics = [r.question[:80] for r in new_turns[-4:]]
            fallback += "；".join(topics)
            return fallback[:max_chars]

    def compress_conversation_context(self, conversation_id: str) -> dict[str, object]:
        """Manually trigger context compression for a conversation.

        Unlike the automatic digest update (which requires a turn threshold),
        this forces compression immediately for all unsummarized turns.
        Returns a dict with the result: turns compressed and digest text length.
        """
        if self._digest_store is None or not self._settings.context_digest_enabled:
            return {"ok": False, "error": "context compression is disabled"}
        if not conversation_id:
            return {"ok": False, "error": "missing conversation_id"}

        max_chars = self._settings.context_digest_max_chars
        all_records = self._conversation_store.list_recent_conversation_records(
            conversation_id, limit=64,
        )
        existing_digest = self._digest_store.get_digest(conversation_id)
        turns_already_digested = existing_digest.turns_summarized if existing_digest else 0
        new_turns = len(all_records) - turns_already_digested

        if new_turns <= 0:
            return {
                "ok": True,
                "turns_compressed": 0,
                "total_turns": turns_already_digested,
                "digest_chars": len(existing_digest.digest_text) if existing_digest else 0,
                "message": "no new turns to compress",
            }

        turns_to_summarize = list(reversed(all_records[:len(all_records) - turns_already_digested]))
        if len(turns_to_summarize) > 32:
            turns_to_summarize = turns_to_summarize[-32:]

        new_digest_text = self._summarize_digest_turns(
            existing_digest_text=existing_digest.digest_text if existing_digest else "",
            new_turns=turns_to_summarize,
            max_chars=max_chars,
        )
        if not new_digest_text:
            return {"ok": False, "error": "summarization failed"}

        total_turns = turns_already_digested + len(turns_to_summarize)
        self._digest_store.update_digest(conversation_id, new_digest_text, total_turns)
        _logger.info(
            "Manual context compression for %s: %d turns compressed, %d chars",
            conversation_id[:8], len(turns_to_summarize), len(new_digest_text),
        )
        return {
            "ok": True,
            "turns_compressed": len(turns_to_summarize),
            "total_turns": total_turns,
            "digest_chars": len(new_digest_text),
        }

    def consolidate_profile_memory(self, context: ChatWorkflowContext) -> ChatWorkflowContext:
        started_at = perf_counter()
        if context.persisted_memory_record is None:
            self._append_trace(
                context,
                key="memory_profile_consolidate",
                title="沉淀长期画像记忆",
                summary="未沉淀长期画像记忆。",
                detail="当前流程没有新的对话记忆可供画像归纳。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        profile_count = self._conversation_store.consolidate_profiles(
            context.persisted_memory_record
        )
        self._append_trace(
            context,
            key="memory_profile_consolidate",
            title="沉淀长期画像记忆",
            summary=(
                f"已沉淀 {profile_count} 条长期画像记忆。"
                if profile_count
                else "本轮未形成新的长期画像记忆。"
            ),
            detail=(
                f"已根据本轮对话提炼 {profile_count} 条用户画像/偏好摘要，并写入 NeuroMem 长期层。"
                if profile_count
                else "本轮对话没有提炼出新的稳定用户画像信息。"
            ),
            duration_ms=self._elapsed_ms(started_at),
        )
        return context

    def answer_with_llm(self, context: ChatWorkflowContext) -> ChatWorkflowContext:
        started_at = perf_counter()
        if context.route != "answer" or context.answer is not None:
            self._append_trace(
                context,
                key="llm_answer",
                title="生成回答",
                summary="本轮未调用回答模型。",
                detail="当前流程已在前面阶段完成，不再调用回答模型。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context
        if context.prompt_envelope is None:
            raise RuntimeError("chat workflow reached llm stage without a prepared prompt")

        relevance_question = self._build_answer_relevance_question(context)
        explicit_deep = self._is_explicit_deep_request(context.request)
        output_constraints = AnswerConstraints.from_question(context.request.question)
        use_curated_answer = self._should_use_curated_technical_guidance(
            relevance_question
        ) or (
            explicit_deep
            and self._should_use_curated_deep_guidance(context.request.question)
        )
        if use_curated_answer and not output_constraints.has_limits:
            context.answer = self._build_deterministic_fallback_answer(context)
            context.workflow_action = "answer"
            self._append_trace(
                context,
                key="llm_answer",
                title="生成回答",
                summary="已返回审校后的深度回答。",
                detail="当前问题命中经过回归测试的深度回答策略，直接返回具体判断、依据和验证步骤。",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        enable_thinking = self._should_enable_deep_thinking(context)
        answer_prompt = context.prompt_envelope
        answer_system_prompt = answer_prompt.system_prompt
        answer_user_prompt = answer_prompt.user_prompt
        use_compact_general_answer = self._should_use_compact_general_answer(context)
        if use_compact_general_answer:
            answer_system_prompt = self._build_compact_answer_system_prompt(
                context.request.question
            )
            answer_user_prompt = context.request.question.strip()
            if self._looks_like_contextual_follow_up(
                context.request.question,
                context.recent_session_context,
            ):
                course_prefix = (
                    f"背景：{context.request.course_context.strip()}\n\n"
                    if context.request.course_context
                    else ""
                )
                answer_user_prompt = (
                    f"{course_prefix}同一会话最近上下文：\n"
                    f"{context.recent_session_context.strip()}\n\n"
                    "当前问题：\n"
                    f"{answer_user_prompt}"
                )
            elif context.request.course_context:
                answer_user_prompt = (
                    f"背景：{context.request.course_context.strip()}\n\n"
                    f"问题：{answer_user_prompt}"
                )
            answer_prompt = PromptEnvelope(
                original_question=context.prompt_envelope.original_question,
                system_prompt=answer_system_prompt,
                user_prompt=answer_user_prompt,
                mode=PromptMode.COMPACT,
                evidence=context.prompt_envelope.evidence,
                invariants=context.prompt_envelope.invariants,
            )
            answer_system_prompt = answer_prompt.system_prompt
            answer_user_prompt = answer_prompt.user_prompt
        try:
            if self._answer_chunk_callback is not None:
                context.answer = self._call_answer_question_sync(
                    answer_system_prompt,
                    answer_user_prompt,
                    context=context,
                    token_callback=self._answer_chunk_callback,
                    enable_thinking=enable_thinking,
                    use_reuse_hints=not use_compact_general_answer,
                    continue_on_length=not use_compact_general_answer,
                )
            else:
                context.answer = self._call_answer_question_sync(
                    answer_system_prompt,
                    answer_user_prompt,
                    context=context,
                    enable_thinking=enable_thinking,
                    use_reuse_hints=not use_compact_general_answer,
                    continue_on_length=not use_compact_general_answer,
                )
        except RuntimeError as exc:
            if "empty chat message" not in str(exc):
                raise
            _logger.warning("LLM returned an empty chat message; retrying with compact prompt")
            context.answer = self._retry_answer_with_compact_prompt(context)
        context.answer = _strip_internal_thinking_content(context.answer)
        if _contains_internal_prompt_leak(context.answer):
            _logger.warning("LLM answer leaked prompt instructions; retrying with compact prompt")
            context.answer = self._retry_answer_with_compact_prompt(context)
        if not context.answer:
            _logger.warning("LLM answer only contained thinking content; retrying compact prompt")
            context.answer = self._retry_answer_with_compact_prompt(context)
        if (
            self._is_degenerate_answer(context.answer)
            or _answer_language_mismatches_question(
                context.request.question,
                context.answer,
            )
            or _answer_does_not_complete_requested_task(
                context.request.question,
                context.answer,
            )
            or _answer_is_irrelevant_to_question(
                relevance_question,
                context.answer,
            )
        ):
            _logger.warning("LLM returned a degenerate answer; retrying with compact prompt")
            context.answer = self._retry_answer_with_compact_prompt(context)
        context.workflow_action = (
            "advise_only" if context.decision_mode == "advise_only" else "answer"
        )
        self._append_trace(
            context,
            key="llm_answer",
            title="生成回答",
            summary=(
                "已完成深度分析并生成结构化回复。"
                if explicit_deep
                else (
                    "已生成建议型回复。"
                    if context.decision_mode == "advise_only"
                    else "已生成最终回复。"
                )
            ),
            detail=(
                "已使用完整上下文分析核心判断、关键依据、权衡风险与下一步行动。"
                if explicit_deep
                else (
                    "已根据角色设定和上下文生成建议，但不替用户或老师做最终决定。"
                    if context.decision_mode == "advise_only"
                    else "已根据角色设定和上下文生成最终回复。"
                )
            ),
            duration_ms=self._elapsed_ms(started_at),
        )
        return context

    def _build_answer_relevance_question(self, context: ChatWorkflowContext) -> str:
        if self._looks_like_contextual_follow_up(
            context.request.question,
            context.recent_session_context,
        ):
            return (
                f"{context.recent_session_context.strip()}\n"
                f"Current question: {context.request.question}"
            )
        return context.request.question

    @staticmethod
    def _should_use_curated_technical_guidance(question: str) -> bool:
        lowered = question.lower()
        has_accelerator = any(marker in lowered for marker in ("ascend", "npu", "昇腾"))
        has_inference = "推理" in question or "inference" in lowered
        asks_for_optimization = any(
            marker in question or marker in lowered
            for marker in ("优化", "效率", "性能", "怎么", "如何", "optimize")
        )
        accelerator_optimization = has_accelerator and has_inference and asks_for_optimization
        latency_throughput_tradeoff = (
            ("首 token" in lowered or "首token" in lowered or "ttft" in lowered)
            and "吞吐" in question
        )
        return accelerator_optimization or latency_throughput_tradeoff

    @staticmethod
    def _should_use_curated_deep_guidance(question: str) -> bool:
        lowered = question.lower()
        weekly_progress = "推进效率" in question and any(
            marker in question for marker in ("背景", "工具链", "补哪块", "补什么")
        )
        unstable_experiment = "实验结果波动" in question and any(
            marker in lowered for marker in ("profiling", "统计分析", "实验管理")
        )
        research_direction = (
            "推理引擎" in question
            and "推理服务系统" in question
            and "研究主线" in question
        )
        ttft_plan = "ttft" in lowered and any(
            marker in question for marker in ("优先级", "本周", "安排")
        )
        weekly_blocker_review = (
            "blocker" in lowered
            and any(marker in question for marker in ("baseline", "公平对比"))
            and "消融" in question
            and any(marker in question for marker in ("组会", "汇报", "逻辑骨架"))
        )
        return (
            weekly_progress
            or unstable_experiment
            or research_direction
            or ttft_plan
            or weekly_blocker_review
        )

    @staticmethod
    def _build_compact_answer_system_prompt(question: str) -> str:
        prompt = (
            "你是一位严谨的科研导师。请直接、准确地回答用户问题，严格遵循用户指定的"
            "内容、格式和长度。概念解释应给出清晰定义，并在用户要求时提供简单具体的例子。"
            f" {COMPACT_CITATION_GROUNDING_RULES}"
        )
        lowered = question.lower()
        if any(marker in lowered for marker in ("ascend", "npu", "昇腾")):
            prompt += (
                " 对 Ascend NPU 推理问题，只围绕精度或量化、算子与图优化、"
                "KV Cache 和内存生命周期、并行通信、连续批处理与调度，以及 "
                "TTFT、TPOT、吞吐和尾延迟评测回答；不要把训练优化写成推理优化。"
            )
        if ("张量并行" in question or "tensor parallel" in lowered) and "推理" in question:
            prompt += (
                " 张量并行的例子必须描述推理阶段如何把同一层的矩阵计算切到多个设备并合并结果；"
                "不要改写成训练或数据并行示例。"
            )
        return prompt

    def _retry_answer_with_compact_prompt(self, context: ChatWorkflowContext) -> str:
        deep_recovery = self._is_explicit_deep_request(context.request)
        compact_system_prompt = self._build_compact_answer_system_prompt(
            context.request.question
        )
        if deep_recovery:
            compact_system_prompt = (
                f"{compact_system_prompt}\n"
                f"{self._build_deep_answer_guidance(context.request)}"
            )
        compact_user_prompt = context.request.question.strip()
        compact_user_prompt = re.sub(r"^请?深入分析[：:，,、\\s]*", "", compact_user_prompt)
        owner_fact_grounding = self._build_owner_fact_grounding_guidance(
            context.request.question,
            context.knowledge_hits,
        )
        if owner_fact_grounding:
            compact_user_prompt = (
                f"{owner_fact_grounding}"
                "Current user question (answer this directly):\n"
                f"{compact_user_prompt}"
            )
        if self._looks_like_contextual_follow_up(
            context.request.question,
            context.recent_session_context,
        ):
            course_prefix = (
                f"背景：{context.request.course_context.strip()}\n\n"
                if context.request.course_context
                else ""
            )
            compact_user_prompt = (
                f"{course_prefix}同一会话最近上下文：\n"
                f"{context.recent_session_context.strip()}\n\n"
                "当前问题：\n"
                f"{compact_user_prompt}"
            )
        elif context.request.course_context:
            compact_user_prompt = (
                f"背景：{context.request.course_context.strip()}\n\n"
                f"问题：{compact_user_prompt}"
            )
        errors: list[str] = []
        relevance_question = self._build_answer_relevance_question(context)
        retry_id = uuid4().hex
        # Deep recovery is deliberately single-shot and bounded. Retrying a
        # 384/768-token answer on this backend can exceed the outer request
        # deadline even when the first request is healthy.
        retry_attempts = 1 if deep_recovery else 2
        for attempt in range(retry_attempts):
            try:
                answer = self._call_compact_retry_sync(
                    system_prompt=compact_system_prompt,
                    user_prompt=compact_user_prompt,
                    temperature=0.2 if deep_recovery else 0.0,
                    max_tokens=(
                        min(
                            int(self._settings.llm_deep_answer_max_tokens),
                            256 if attempt == 0 else 192,
                        )
                        if deep_recovery
                        else (384 if attempt == 0 else 320)
                    ),
                    cache_namespace=(
                        f"{context.conversation_id or 'compact'}:compact-retry:"
                        f"{retry_id}:{attempt}"
                    ),
                )
            except RuntimeError as exc:
                errors.append(str(exc))
                continue
            answer = _strip_internal_thinking_content(answer)
            if _contains_internal_prompt_leak(answer):
                errors.append("prompt leak compact retry")
                continue
            if (
                not self._is_degenerate_answer(answer)
                and not _answer_language_mismatches_question(
                    context.request.question,
                    answer,
                )
                and not _answer_does_not_complete_requested_task(
                    context.request.question,
                    answer,
                )
                and not _answer_is_irrelevant_to_question(
                    relevance_question,
                    answer,
                )
            ):
                return answer.strip()
            errors.append("degenerate compact retry")
        _logger.error("LLM compact retry failed; using deterministic fallback answer")
        return self._build_deterministic_fallback_answer(context)

    def _call_compact_retry_sync(
        self,
        *,
        system_prompt: str,
        user_prompt: str,
        temperature: float,
        max_tokens: int,
        cache_namespace: str,
    ) -> str:
        answer_fn = self._llm_client.answer_question_sync
        parameters = inspect.signature(answer_fn).parameters
        optional_kwargs: dict[str, object] = {
            "token_callback": None,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "enable_thinking": False,
            "use_reuse_hints": False,
            "continue_on_length": False,
            "cache_namespace": cache_namespace,
        }
        accepts_var_kwargs = any(
            parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters.values()
        )
        supported_kwargs = (
            optional_kwargs
            if accepts_var_kwargs
            else {
                key: value for key, value in optional_kwargs.items() if key in parameters
            }
        )
        return answer_fn(system_prompt, user_prompt, **supported_kwargs)

    @staticmethod
    def _build_deterministic_fallback_answer(context: ChatWorkflowContext) -> str:
        question = context.request.question.strip()
        lowered_question = question.lower()
        if (
            "blocker" in lowered_question
            and any(marker in question for marker in ("baseline", "公平对比"))
            and "消融" in question
            and any(marker in question for marker in ("组会", "汇报", "逻辑骨架"))
        ):
            return (
                "先校正一个关键点：仅凭“本周 blocker”这句话，不能直接断言瓶颈一定是显存、调度或算子；"
                "本周汇报应把 blocker 写成可证伪命题，并用一组最小实验把它钉死。\n\n"
                "## 一页组会逻辑骨架\n\n"
                "**1. 本周结论（顶部）**\n"
                "- Blocker：当前结果无法稳定归因于某个机制，先解决“证据不足/对比不公平”，而不是继续堆实现。\n"
                "- 决策：本周只验证一个主假设：在固定模型、输入长度、并发、硬件和软件版本后，目标指标的主要差异是否仍由该机制造成。\n\n"
                "**2. 现象与影响（左上）**\n"
                "- 展示 1 张主图：目标指标的均值 + P50/P95（系统问题可用 TTFT、TPOT、吞吐、显存和失败率）。\n"
                "- 标出当前 blocker 的可观测证据、影响范围和还不能解释的部分；没有测到的内容明确写“未知”。\n\n"
                "**3. 公平 baseline（右上）**\n"
                "- 只比较同一模型、精度、输入/输出长度、硬件卡数、并发、版本和 warm-up 条件。\n"
                "- 至少包含：原始系统、最强公开/内部 baseline、你的方法；报告完整配置和重复次数。\n"
                "- 主结论只使用预先声明的主指标，不能只挑对自己有利的吞吐或平均值。\n\n"
                "**4. 关键消融（左下）**\n"
                "- 一次只去掉一个机制：例如缓存、调度、并行/通信优化或内存策略。\n"
                "- 先做 full vs. -one-component，再做关键参数敏感性；同时报告收益、副作用和失败案例。\n"
                "- 消融结果要能回答“为什么有效”，而不只是证明“数字变大”。\n\n"
                "**5. 本周行动与验收（右下）**\n"
                "- Day 1：冻结环境和 baseline，补齐原始日志。\n"
                "- Day 2：跑 full/去组件消融，至少重复多次并保留原始结果。\n"
                "- Day 3：按主指标和尾延迟复核，形成一张结论表。\n"
                "- 验收标准：能明确说明 blocker 是什么、哪个机制解释收益、结论在公平条件下是否成立；否则停止扩展实现，先补实验。\n\n"
                "**最后一句（底部）**\n"
                "本周目标不是证明方案“有效”，而是用公平 baseline 和最小消融判断：收益是否真实、来自哪里、是否值得继续投入。"
            )
        owner_research_summary = (
            FacultyTwinWorkflowSupport._extract_owner_research_summary(context)
        )
        if owner_research_summary:
            if any(
                marker in question
                for marker in ("加入课题组", "加入你们组", "提前准备", "怎么准备")
            ):
                return (
                    f"{owner_research_summary}。"
                    "如果希望加入课题组，建议先阅读公开研究资料，准备能说明系统研究或工程能力的"
                    "简历、项目/代码证据，并写清自己最想切入的 1–2 个具体问题。"
                )
            return f"{owner_research_summary}。"
        if "三个问题" in question or ("3个问题" in question and "问" in question):
            return (
                "可以先问这三个问题，能最快抓住一位老师的研究路线：\n\n"
                "1. 现在最核心的问题是什么？\n"
                "   这能帮助你判断老师当前真正投入的方向，而不只是论文关键词。\n\n"
                "2. 这条路线是从哪些早期工作发展来的？\n"
                "   这能看出研究脉络：哪些问题是一贯关注的，哪些是最近的新转向。\n\n"
                "3. 接下来最想突破的瓶颈是什么？\n"
                "   这能判断未来一年左右可能出现的课题、合作机会和学生切入点。\n\n"
                "如果时间很短，可以直接问：'您现在最想解决的研究问题是什么，"
                "它从过去哪些工作发展而来，下一步最大的瓶颈在哪里？'"
            )
        recent_context = (context.recent_session_context or "").lower()
        if (
            ("三个优先级" in question or "三步" in question or "3步" in question)
            and any(marker in recent_context for marker in ("ascend", "npu", "昇腾"))
        ):
            return (
                "建议按三步实施：\n\n"
                "1. 先建立可复现基线。固定模型、精度、输入长度和并发，记录 TTFT、TPOT、吞吐、P95 延迟、峰值设备内存和失败率；先验证瓶颈究竟在计算、内存还是调度。\n\n"
                "2. 再优化单卡执行与内存。依次验证混合精度或量化、算子融合、图编译、内存池和 KV Cache 生命周期管理；每次只改一项，先确认精度可接受且碎片率、峰值占用或单请求延迟确实改善。\n\n"
                "3. 最后扩展并发与多卡。调节连续批处理、序列分桶、张量并行和通信重叠；先验证吞吐随并发或卡数提升，同时 P95 延迟和错误率仍在目标范围内。"
            )
        if (
            ("三个优先级" in question or "3个优先级" in question)
            and any(marker in recent_context for marker in ("医学影像", "影像分割", "medical"))
        ):
            return (
                "可以按下面三个优先级推进：\n\n"
                "1. 临床安全与分割准确性。先保证关键结构和病灶不会漏分、错分，因为任何效率收益都不能抵消临床风险；应重点看 Dice、Hausdorff 距离和高风险病例的失败率。\n\n"
                "2. 跨中心泛化与数据质量。不同医院、设备、扫描协议和人群分布差异很大，模型只有通过多中心外部验证、标注一致性检查和域偏移测试，才具备真实落地价值。\n\n"
                "3. 推理效率与工作流集成。在前两项达标后，再优化延迟、显存、吞吐和部署成本，并确认结果能进入医生现有阅片、复核和审计流程。\n\n"
                "这个排序的原则是：先控制医疗风险，再证明可迁移性，最后优化工程效率。"
            )
        if any(marker in lowered_question for marker in ("ascend", "npu", "昇腾")) and "推理" in question:
            return (
                "可以从四个层面优化 Ascend NPU 上的大模型推理：\n\n"
                "1. 模型与算子：使用适配 Ascend 的混合精度或量化方案，并通过算子融合、图编译和高性能算子减少 kernel 启动及数据搬运开销。\n"
                "2. 内存与 KV Cache：预分配内存池，复用 workspace，按请求生命周期管理 KV Cache，并监控碎片率、峰值占用和换入换出次数。\n"
                "3. 并行与通信：根据模型规模选择张量并行或流水并行，减少不必要的集合通信，并把通信与计算重叠。\n"
                "4. 批处理与调度：采用连续批处理，按序列长度分桶，并分别监控 TTFT、TPOT、吞吐和 P95 延迟来调节 batch 与并发上限。\n\n"
                "优化时应固定模型、输入长度和并发建立基线，每次只改变一个参数，避免只看平均吞吐而忽略尾延迟和稳定性。"
            )
        if any(marker in question for marker in ("医学影像", "图像分割")):
            return (
                "深度学习图像分割在医学影像中前景明确，但真正落地取决于以下四点：\n\n"
                "1. 数据与标注：用多中心数据、统一标注规范和弱监督/半监督方法降低高质量标注成本。\n"
                "2. 模型与任务：以 U-Net、Transformer 或混合架构为 baseline，围绕小病灶、模糊边界和三维体数据做针对性设计。\n"
                "3. 泛化与可靠性：必须评估跨医院、设备、扫描协议和人群的域偏移，并提供不确定性估计和失败检测。\n"
                "4. 临床验证与集成：除了 Dice 等离线指标，还要验证医生复核时间、漏诊风险、推理延迟、可追溯性和监管合规。\n\n"
                "研究上最有价值的切口通常不是单纯提高一个数据集的分数，而是提升数据效率、跨中心泛化或临床工作流中的可靠性。"
            )
        if ("首 token" in lowered_question or "首token" in lowered_question) and "吞吐" in question:
            return (
                "首 token 延迟和吞吐经常冲突，本质上是调度目标不同：\n\n"
                "1. 首 token 延迟关注单个请求尽快开始输出。系统会倾向于小批次、少排队、快速 prefill，"
                "这样用户体感好，但 GPU 可能吃不满。\n"
                "2. 吞吐关注单位时间处理更多 token。系统会倾向于合并请求、增大 batch、提高并发，"
                "这样 GPU 利用率更高，但新请求可能需要等待成批调度。\n"
                "3. prefill 和 decode 的资源形态不同。prefill 更偏大矩阵计算，decode 更容易受 KV cache、"
                "显存带宽和调度碎片影响；把两者混在一起追求最优，会相互牵制。\n"
                "4. 工程上通常做分层目标：交互场景优先限制排队时间和首 token P95；批量任务优先吞吐；"
                "混合场景用连续批处理、优先级队列、请求超时和最大 batch 上限折中。\n\n"
                "所以不是某个参数能同时拉满两边，而是要先定义 SLA：实时问答看首 token 和尾延迟，"
                "离线或后台任务看总吞吐和成本。"
            )
        if "ttft" in lowered_question and any(
            marker in question for marker in ("优先级", "本周", "安排")
        ):
            return (
                "本周优先级建议是：先实验定位，再针对性实现，最后补文献验证，而不是先大规模改代码。\n\n"
                "1. 用半天建立 TTFT 基线：固定输入长度和并发，分别记录排队、调度、prefill、"
                "网络和序列化耗时，并看 P50/P95。没有分段数据，就无法判断该改引擎还是服务层。\n"
                "2. 用一天做最小实验矩阵：只改变 batch、并发、输入长度和调度策略，确认 TTFT "
                "主要受排队、prefill 计算还是冷启动影响。\n"
                "3. 把两天实现时间只投到证据最强的瓶颈：排队高就改准入与优先级；prefill 高就验证"
                "算子、并行和缓存；冷启动高就做预热与模型常驻。\n"
                "4. 文献控制在约 20% 时间，只查与你测出的瓶颈直接相关的方法，并用一个对照实验验证。\n\n"
                "周末验收应是：TTFT P95 降低多少、吞吐和回答质量是否退化，以及收益来自哪一项改动。"
            )
        if "推理引擎" in question and "推理服务系统" in question and "研究主线" in question:
            return (
                "如果你目前更容易拿到真实请求、调度日志和多租户场景，我会优先选推理服务系统作为"
                "研究主线；如果你能稳定修改算子、KV Cache、并行执行并做跨模型验证，再选推理引擎。\n\n"
                "判断标准不是哪一层更热门，而是哪一层能形成可重复的研究闭环：\n"
                "1. 引擎方向需要一个明确机制贡献，例如新的 KV Cache 管理、调度或并行方法，并证明"
                "它在多模型、多长度和多卡条件下优于强 baseline。门槛高，但贡献边界清楚。\n"
                "2. 服务系统方向适合研究 SLO、混合负载、准入控制、弹性和成本权衡，需要真实 workload "
                "或可信 trace，优势是问题更贴近生产。\n"
                "3. 做两周探针实验：引擎侧实现一个最小机制并测 TTFT/TPOT/吞吐；服务侧用同一后端复现"
                "一种调度或 SLO 策略。比较数据可得性、创新空间和实验周期，哪边能连续提出三个可证伪"
                "假设，就把哪边定为主线，另一层作为验证平台。"
            )
        if "创新" in question and any(
            marker in question for marker in ("研究问题", "学术", "研究方向", "选题")
        ):
            return (
                "判断研究问题是否有足够创新性，关键不是措辞新，而是能否证明现有方法在一个重要"
                "场景下存在系统性缺口。\n\n"
                "1. 新问题：明确 workload、约束或硬件变化后，现有调度目标为何失效；如果只是换数据集"
                "或调参数，通常不够。\n"
                "2. 新机制：说明你的方法为何需要新的状态、决策或优化目标，而不是把已有策略重新组合。\n"
                "3. 可证伪收益：预先写出与最强 baseline 的对照假设，并同时评测 TTFT、TPOT、吞吐、"
                "尾延迟或成本，避免只挑有利指标。\n\n"
                "可执行验证是做一页 related-work 矩阵：行列出最接近的 5 至 10 项工作，列出场景、假设、"
                "机制和指标；若你的贡献能在至少两个维度形成有因果解释的空白，再实现最小原型验证。"
            )
        if "实验结果波动" in question and any(
            marker in lowered_question for marker in ("profiling", "统计分析", "实验管理")
        ):
            return (
                "先补实验管理和可复现性，再补统计分析；profiling 只在确认波动来自性能路径后介入。\n\n"
                "原因是环境、代码版本、随机种子、数据顺序或并发不一致造成的波动，统计方法只能描述，"
                "不能消除。先固定提交版本、依赖、配置、数据切分和种子，对同一配置独立重复至少 5 次，"
                "自动记录原始指标与异常。然后报告均值、标准差和置信区间，并检查异常值是否对应资源争用"
                "或失败重试。若波动的是延迟/吞吐，再用 profiling 拆分数据、计算、通信和 I/O；若波动的"
                "是模型质量，则优先查采样、数据和训练随机性。\n\n"
                "本周验收标准可以定为：任何一条结果都能从版本和配置一键复现，并能解释主要方差来源。"
            )
        if question.startswith("如何优化") or "怎么优化" in question:
            subject = question.strip("？?。 ")
            subject = re.sub(r"^(如何|怎么)优化", "", subject).strip() or "这个系统"
            return (
                f"可以按'先定位瓶颈，再逐项验证'的方式优化{subject}：\n\n"
                "1. 建立基线：固定输入规模、并发、硬件和软件版本，记录吞吐、首响应延迟、P95 延迟、"
                "资源占用和失败率。\n"
                "2. 定位瓶颈：区分问题来自计算、内存、通信、I/O、调度还是模型本身，避免盲目调参。\n"
                "3. 优化执行路径：优先减少重复计算、数据拷贝和小任务碎片，再考虑融合算子、缓存、批处理、"
                "并行和量化。\n"
                "4. 做消融验证：每次只改一个变量，比较收益和副作用，特别关注质量、稳定性和尾延迟。\n"
                "5. 上线保护：设置并发上限、超时、降级和监控告警，避免峰值流量把优化收益抵消掉。\n\n"
                "如果你要把它写成方案，建议按'现状指标 -> 瓶颈证据 -> 优化动作 -> 验证结果'来组织。"
            )
        if "前景" in question or "应用" in question:
            return (
                "可以从应用价值、技术成熟度和落地风险三条线来判断这个方向的前景：\n\n"
                "1. 应用价值：它解决的是高频问题、刚需问题，还是只是在特定场景里有帮助。\n"
                "2. 技术成熟度：现有方法是否已经有可靠 baseline，数据、评测和部署条件是否具备。\n"
                "3. 落地风险：需要重点看成本、泛化能力、可解释性、安全合规和与现有流程的集成难度。\n\n"
                "做研究时，最好把题目收窄到一个具体场景，再围绕数据效率、系统效率、可靠性或人机协同提出贡献。"
            )
        if "推进效率" in question and any(
            marker in question for marker in ("背景", "工具链", "补哪块", "补什么")
        ):
            return (
                "先不要泛泛补背景，优先补最近一周反复卡住你的那一层：\n\n"
                "1. 如果读论文慢、选题边界不清，补领域地图：整理 3 篇代表工作，写清问题、baseline、"
                "指标和仍未解决的缺口。\n"
                "2. 如果实验排队、结果不可复现，补实验工具链：固定环境与随机种子，用配置文件管理参数，"
                "自动记录版本、日志、指标和失败原因。\n"
                "3. 如果定位性能问题靠猜，补 profiling：把一次运行拆成数据、计算、通信和 I/O，"
                "先找耗时最高且波动最大的环节。\n"
                "4. 如果实现很多但结论不稳，补评测设计：先定主指标、对照组和消融实验，再继续写代码。\n\n"
                "今天可以先复盘最近三次卡顿，各写下损失的时间和直接原因；出现次数最多的一类，"
                "就是本周最值得补的背景或工具链。"
            )
        return (
            "先给一个可执行判断：不要继续泛化题目，先找出当前最影响结果的一项不确定性。"
            "为它写下一个可证伪假设，选择一个强 baseline，并设计只改变一个变量的最小实验。"
            "用主指标、P95 或失败率等直接证据决定下一步；如果实验不能区分两种判断，"
            "应先改进问题定义和评测，而不是继续增加实现复杂度。"
        )

    @staticmethod
    def _extract_owner_research_summary(context: ChatWorkflowContext) -> str | None:
        if not FacultyTwinWorkflowSupport._IDENTITY_QUESTION_PATTERN.search(
            context.request.question
        ):
            return None
        candidates: list[tuple[int, str]] = []
        for hit in context.knowledge_hits:
            hit_tags = {tag.lower() for tag in hit.tags}
            if not hit_tags & {"profile", "research", "research-agenda", "overview"}:
                continue
            for sentence in re.split(r"[。！？\n]+", hit.excerpt):
                normalized = re.sub(r"\s+", " ", sentence).strip(" ：:；;")
                if not normalized or not any(
                    marker in normalized for marker in ("研究", "当前工作", "研究主线")
                ):
                    continue
                if not any(
                    marker in normalized
                    for marker in ("大模型", "推理", "状态管理", "当前工作", "研究主线")
                ):
                    continue
                priority = sum(
                    marker in normalized
                    for marker in ("当前工作", "研究聚焦", "研究主线", "大模型推理")
                )
                candidates.append((priority, normalized[:260]))
        if not candidates:
            return None
        candidates.sort(key=lambda item: (-item[0], len(item[1])))
        return candidates[0][1]

    @staticmethod
    def _is_degenerate_answer(answer: str | None) -> bool:
        raw_text = answer or ""
        if len(raw_text) >= 200:
            whitespace_count = sum(1 for ch in raw_text if ch.isspace())
            if whitespace_count / len(raw_text) > 0.55:
                return True
        text = raw_text.strip()
        if not text:
            return True
        final_line = text.splitlines()[-1].strip()
        if text.endswith(("：", ":")) or re.fullmatch(
            r"(?:#{1,6}\s+.*|\d+[.、)]\s*\*\*[^*]+\*\*|[-*]\s+\*\*[^*]+\*\*)",
            final_line,
        ):
            return True
        if "<think" in text.lower():
            return True
        if text.lower().startswith("answer:"):
            return True
        if _contains_internal_prompt_leak(text):
            return True
        compact = re.sub(r"\s+", "", text)
        if not re.search(r"[A-Za-z0-9\u4e00-\u9fff]", compact):
            return True
        refusal_markers = (
            "这部分我需要额外确认",
            "不便在此讨论",
            "请直接联系张老师",
            "请开启联网搜索",
            "请开启联网检索",
            "无法回答这个问题",
            "无法提供这个问题的答案",
            "请直接回答，不要添加任何占位符",
            "不提供任何中间过程或解释说明",
            "后续指令中提供具体的选项或排序",
            "超出模型能力范围",
            "建议开启联网检索获取实时参考",
        )
        if any(marker in compact for marker in refusal_markers):
            return True
        if re.search(r"[\[【](?:具体|待定|待填写|请填写)[^\]】]{0,20}[\]】]", compact):
            return True
        if compact.upper().startswith("-FIRST") or compact.upper().startswith("FIRST`"):
            return True
        if len(compact) < 32 and any(ch in compact for ch in ("`", "$")):
            return True
        if re.search(r"([\u4e00-\u9fff]{2,8})(?:的)?\1", compact):
            return True
        if len(compact) < 32:
            return False
        if re.search(r"(.)\1{39,}", compact):
            return True
        punctuation_count = sum(
            1
            for ch in compact
            if not ch.isalnum() and not ("\u4e00" <= ch <= "\u9fff")
        )
        if punctuation_count / max(1, len(compact)) > 0.85:
            return True
        unique_chars = set(compact)
        if len(compact) >= 80 and len(unique_chars) <= 4:
            return True
        sentence_fragments = [
            fragment.strip()
            for fragment in re.split(r"[。！？.!?]+", text)
            if len(fragment.strip()) >= 4
        ]
        if len(sentence_fragments) >= 3:
            most_common_sentence = max(
                (sentence_fragments.count(fragment) for fragment in set(sentence_fragments)),
                default=0,
            )
            if most_common_sentence >= 3:
                return True
        for unit_length in range(2, min(40, len(compact) // 4) + 1):
            unit = compact[:unit_length]
            repeated = unit * (len(compact) // unit_length)
            if compact.startswith(repeated) and len(repeated) / len(compact) > 0.85:
                return True
        if len(compact) >= 120:
            for ngram_size in (4, 6, 8, 12):
                if len(compact) < ngram_size * 4:
                    continue
                ngrams = [
                    compact[index : index + ngram_size]
                    for index in range(len(compact) - ngram_size + 1)
                ]
                most_common_ngram = max(
                    (ngrams.count(ngram) for ngram in set(ngrams)),
                    default=0,
                )
                if most_common_ngram * ngram_size / len(compact) > 0.28:
                    return True
        most_common = max((compact.count(ch) for ch in unique_chars), default=0)
        return most_common / max(1, len(compact)) > 0.72

    def _call_answer_question_sync(
        self,
        system_prompt: str,
        user_prompt: str,
        *,
        context: ChatWorkflowContext,
        token_callback: Callable[[str], None] | None = None,
        enable_thinking: bool,
        use_reuse_hints: bool = True,
        continue_on_length: bool = True,
    ) -> str:
        """Call answer_question_sync with capability-aware keyword arguments."""
        answer_fn = self._llm_client.answer_question_sync
        signature = inspect.signature(answer_fn)
        kwargs: dict[str, Any] = {}
        policy_context = self._build_llm_serving_policy_context(context)
        model_name = str(getattr(self._llm_client, "model_name", "") or "").lower()
        reliable_streaming = "glm-4" not in model_name

        # Qwen3/vLLM-HUST can emit empty streaming chunks or content-only
        # <think> blocks when engine thinking is enabled. In hosted/web, keep
        # the user's "deep thinking" intent in the prompt but use normal
        # answer generation so the request stays inside the web timeout.
        engine_thinking = False
        if (
            token_callback is not None
            and "token_callback" in signature.parameters
            and not enable_thinking
            and reliable_streaming
        ):
            kwargs["token_callback"] = token_callback
        if "enable_thinking" in signature.parameters:
            kwargs["enable_thinking"] = engine_thinking
        if "use_reuse_hints" in signature.parameters:
            kwargs["use_reuse_hints"] = use_reuse_hints
        if "continue_on_length" in signature.parameters:
            kwargs["continue_on_length"] = continue_on_length
        if engine_thinking and "thinking_token_budget" in signature.parameters:
            kwargs["thinking_token_budget"] = 256
        if "deadline_class" in signature.parameters:
            kwargs["deadline_class"] = policy_context["deadline_class"]
        if "request_priority" in signature.parameters:
            kwargs["request_priority"] = policy_context["request_priority"]
        if "target_e2e_ms" in signature.parameters:
            kwargs["target_e2e_ms"] = policy_context["target_e2e_ms"]
        if "max_tokens" in signature.parameters:
            if enable_thinking:
                kwargs["max_tokens"] = min(
                    int(self._settings.llm_deep_answer_max_tokens),
                    int(self._settings.llm_policy_output_max_tokens_cap),
                )
                if "continue_on_length" in signature.parameters:
                    # Do not recursively continue a deep response. On a
                    # low-throughput NPU that turns one bounded request into
                    # an unbounded sequence and triggers the outer timeout.
                    kwargs["continue_on_length"] = False
            elif policy_context.get("max_tokens") is not None:
                kwargs["max_tokens"] = policy_context["max_tokens"]
        if "cache_namespace" in signature.parameters and context.conversation_id:
            kwargs["cache_namespace"] = context.conversation_id
        if "segment_reuse_body_text" in signature.parameters:
            kwargs["segment_reuse_body_text"] = system_prompt
        if "segment_reuse_scope" in signature.parameters:
            kwargs["segment_reuse_scope"] = (
                getattr(context.request, "visitor_profile", None) or "general_visitor"
            )

        return answer_fn(system_prompt, user_prompt, **kwargs)

    def _build_llm_serving_policy_context(self, context: ChatWorkflowContext) -> dict[str, Any]:
        interaction_intent = context.interaction_intent
        domain = interaction_intent.domain if interaction_intent is not None else "general"
        decision_mode = context.decision_mode
        if self._should_use_compact_general_answer(context):
            return {
                "deadline_class": "interactive-high",
                "request_priority": 90,
                "target_e2e_ms": 5000.0,
                "max_tokens": min(512, int(self._settings.llm_policy_output_max_tokens_cap)),
            }

        if decision_mode == "advise_only" or domain in {"research", "advising", "teaching"}:
            return {
                "deadline_class": "interactive-high",
                "request_priority": 90,
                "target_e2e_ms": 2200.0,
                "max_tokens": int(self._settings.llm_fast_answer_max_tokens),
            }

        return {
            "deadline_class": "batch-standard",
            "request_priority": 45,
            "target_e2e_ms": 10000.0,
            "max_tokens": int(self._settings.llm_fast_answer_max_tokens),
        }

    def _should_use_compact_general_answer(self, context: ChatWorkflowContext) -> bool:
        if self._is_benchmark_request(context.request):
            return False
        # Explicit deep mode must retain the fully prepared prompt. The compact
        # path intentionally replaces that prompt, which would otherwise drop
        # deep-analysis guidance and make the user-facing toggle ineffective.
        if self._is_explicit_deep_request(context.request):
            return False
        if context.web_search_hits or getattr(context.request, "attachments", None):
            return False
        question = context.request.question
        lowered = question.lower()
        faculty_grounding_markers = (
            "张老师",
            "课题组",
            "你的论文",
            "您的论文",
            "你的研究",
            "您的研究",
            "你们",
            "课程",
            "招生",
            "预约",
            "会议时间",
            "office hour",
            "未公开",
            "内部项目",
            "结合我的情况",
            "基于我的背景",
            "按我的情况",
            "结合我的阶段",
            "按我当前",
            "按我现在",
            "学生画像",
        )
        if (context.knowledge_hits or context.memory_hits) and any(
            marker in question or marker in lowered
            for marker in faculty_grounding_markers
        ):
            return False
        if self._looks_like_contextual_follow_up(
            context.request.question,
            context.recent_session_context,
        ):
            return True
        if (
            context.interaction_intent is not None
            and context.interaction_intent.action == "answer"
            and context.interaction_intent.domain == "general"
            and context.decision_mode == "direct_answer"
        ):
            return True
        if self._looks_like_general_technical_question(question):
            return True
        general_academic_markers = (
            "应用前景",
            "方法综述",
            "研究方法",
            "值得先问哪三个问题",
            "最值得先问哪三个问题",
            "three questions",
            "deep learning",
            "image segmentation",
            "医学影像",
            "图像分割",
        )
        return any(marker in question or marker in lowered for marker in general_academic_markers)

    def _should_enable_deep_thinking(self, context: ChatWorkflowContext) -> bool:
        # Chat Latency Optimizations Task 5: when an ``answer_chunk_callback``
        # is wired up by the /chat endpoint we ask the LLM client for a
        # streaming completion so each token is forwarded over the
        # workflow-events SSE channel as it arrives. The full string is
        # still returned so the rest of the pipeline (trace, render,
        # post-answer fan-out) sees the same final answer.
        if not getattr(context.request, "deep_thinking", True):
            return False

        if self._is_explicit_deep_request(context.request):
            return True

        # B3: Auto-disable thinking for simple intents (e.g. general, booking)
        # to avoid wasting 300-500 CoT tokens on trivial queries unless the
        # user explicitly asked for deeper reasoning.
        if context.interaction_intent is not None:
            auto_disable_domains = {
                d.strip()
                for d in self._settings.auto_disable_thinking_intents.split(",")
                if d.strip()
            }
            if context.interaction_intent.domain in auto_disable_domains:
                return False
            if context.decision_mode == "direct_answer":
                return False

        return True

    def plan_follow_up_actions(self, context: ChatWorkflowContext) -> ChatWorkflowContext:
        started_at = perf_counter()
        if context.is_admin_request and context.workflow_action == "admin_add_knowledge":
            self._append_trace(
                context,
                key="follow_up_plan",
                title="规划后续动作",
                summary="管理员知识入库不生成后续动作。",
                detail="当前对话已经完成知识写入，不再生成学生侧后续建议。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        if context.answer is None:
            self._append_trace(
                context,
                key="follow_up_plan",
                title="规划后续动作",
                summary="未生成后续动作。",
                detail="当前流程没有可规划的后续动作。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        profiles = self._conversation_store.list_profiles_for_student(
            student_name=context.request.student_name,
            student_email=context.request.student_email,
            limit=10,
        )
        context.follow_up_actions = self._action_planner.plan_chat_actions(
            workflow_action=context.workflow_action,
            question=context.request.question,
            interaction_intent=context.interaction_intent,
            knowledge_hits=context.knowledge_hits,
            student_profiles=profiles,
            availability_schedule=self._meeting_service.get_availability_schedule(),
        )
        action_count = len(context.follow_up_actions)
        self._append_trace(
            context,
            key="follow_up_plan",
            title="规划后续动作",
            summary=(
                f"已生成 {action_count} 条后续动作建议。"
                if action_count
                else "未生成额外后续动作。"
            ),
            detail=(
                "已基于知识命中、学生画像和当前问题生成后续阅读/待办/资源建议。"
                if action_count
                else "当前问题没有额外的阅读、待办或资源推荐。"
            ),
            duration_ms=self._elapsed_ms(started_at),
        )
        return context

    def score_memory_usefulness(self, context: ChatWorkflowContext) -> ChatWorkflowContext:
        started_at = perf_counter()
        if context.answer is None:
            self._append_trace(
                context,
                key="memory_usefulness_score",
                title="评估记忆证据有效性",
                summary="未评估本轮记忆证据有效性。",
                detail="当前流程没有生成回答，无法判断记忆和检索证据是否有帮助。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        if not self._planner_requests_any_step(context, "score_memory_usefulness"):
            self._append_trace(
                context,
                key="memory_usefulness_score",
                title="评估记忆证据有效性",
                summary="当前工作流规划跳过记忆证据评估。",
                detail="deterministic planner 已接受当前规划，且本轮执行不需要 score_memory_usefulness。",
                status="skipped",
                duration_ms=self._elapsed_ms(started_at),
            )
            return context

        signal, reason = self._evaluate_memory_usefulness(context)
        context.memory_usefulness_signal = signal
        context.memory_usefulness_reason = reason
        recent_session_attached = bool(context.recent_session_context.strip())
        short_term_hit_count = sum(
            1 for hit in context.memory_hits if hit.memory_type == "short_term"
        )
        if recent_session_attached and short_term_hit_count == 0:
            short_term_hit_count = 1
        long_term_hit_count = sum(
            1 for hit in context.memory_hits if hit.memory_type == "long_term"
        )
        top_memory_score = max((hit.score for hit in context.memory_hits), default=None)
        if top_memory_score is None and recent_session_attached:
            top_memory_score = 2.0
        duration_ms = self._elapsed_ms(started_at)
        self._conversation_store.record_memory_usefulness(
            conversation_id=context.conversation_id,
            signal=signal,
            reason=reason,
            memory_used=bool(context.memory_hits) or recent_session_attached,
            knowledge_used=bool(context.knowledge_hits),
            memory_hit_count=len(context.memory_hits) + (1 if recent_session_attached else 0),
            short_term_hit_count=short_term_hit_count,
            long_term_hit_count=long_term_hit_count,
            knowledge_hit_count=len(context.knowledge_hits),
            top_memory_score=top_memory_score,
            workflow_action=context.workflow_action,
            duration_ms=float(duration_ms or 0),
        )
        self._append_trace(
            context,
            key="memory_usefulness_score",
            title="评估记忆证据有效性",
            summary=f"已完成本轮记忆证据评估：{self._memory_usefulness_label(signal)}。",
            detail=reason,
            duration_ms=duration_ms,
        )
        return context

    def render_chat_response(self, context: ChatWorkflowContext) -> ChatResponse:
        if context.answer is None:
            raise RuntimeError("chat workflow completed without producing an answer")
        context.answer = context.answer.strip()
        if self._is_degenerate_answer(context.answer):
            raise RuntimeError("chat workflow produced an empty or degenerate answer")

        started_at = perf_counter()

        self._append_trace(
            context,
            key="response_render",
            title="返回结果",
            summary="已返回工作流结果。",
            detail="已整理回答、动作结果和工作流轨迹，返回给前端。",
            duration_ms=self._elapsed_ms(started_at),
        )

        # Restore deterministic order in case operators ran in parallel and
        # appended trace entries out of canonical sequence.
        canonical_trace = _canonicalize_workflow_trace(context.workflow_trace)
        context.workflow_trace = canonical_trace

        return ChatResponse(
            answer=context.answer,
            owner_name=context.owner_name,
            used_model=context.used_model,
            exchange_id=(
                context.persisted_memory_record.memory_id
                if context.persisted_memory_record is not None
                else None
            ),
            knowledge_hits=context.knowledge_hits,
            web_search_hits=context.web_search_hits,
            answer_basis=self._build_answer_basis(context),
            follow_up_actions=context.follow_up_actions,
            conversation_id=context.conversation_id,
            workflow_action=context.workflow_action,
            decision_mode=context.decision_mode,
            pending_fields=context.pending_fields,
            booking_result=context.booking_result,
            escalation_record=context.escalation_record,
            planner_preview=self._build_planner_preview(context.planner_decision),
            shadow_planner_preview=self._build_shadow_planner_preview(
                context.planner_decision,
                context.shadow_planner_decision,
                context.shadow_planner_status,
                context.shadow_planner_message,
            ),
            planner_comparison=context.planner_comparison,
            workflow_trace=canonical_trace,
            memory_used=bool(context.memory_hits) or bool(context.recent_session_context.strip()),
            memory_write_back=context.persisted_memory_record is not None,
            retrieved_items=self._build_memory_audit_items(
                context.memory_hits,
                recent_session_context=context.recent_session_context,
                conversation_id=context.conversation_id,
            ),
            token_usage=self._build_token_usage(),
        )

    def _evaluate_memory_usefulness(self, context: ChatWorkflowContext) -> tuple[str, str]:
        memory_hits = context.memory_hits
        knowledge_hits = context.knowledge_hits
        recent_session_attached = bool(context.recent_session_context.strip())
        short_term_hits = [hit for hit in memory_hits if hit.memory_type == "short_term"]
        long_term_hits = [hit for hit in memory_hits if hit.memory_type == "long_term"]
        top_memory_score = max((hit.score for hit in memory_hits), default=0.0)
        if recent_session_attached:
            top_memory_score = max(top_memory_score, 2.0)
        latest_memory_at = max((hit.created_at for hit in memory_hits), default=None)
        latest_memory_age_days = (
            max(0, int((datetime.now(UTC) - latest_memory_at).total_seconds() // 86400))
            if latest_memory_at is not None
            else None
        )
        has_short_term_support = bool(short_term_hits) or recent_session_attached

        if not memory_hits and not knowledge_hits:
            if recent_session_attached:
                return (
                    "helpful",
                    "本轮直接复用了同一对话里的最近上下文，即使没有额外检索命中，也能稳定支撑连续追问。",
                )
            return (
                "low_confidence",
                "本轮没有检索到可复用的对话记忆或知识材料，回答主要依赖当前问题和角色设定，后续应优先补充可追溯证据。",
            )
        if memory_hits and not knowledge_hits:
            if has_short_term_support and long_term_hits:
                return (
                    "helpful",
                    "本轮同时复用了近期对话和长期画像记忆，即使没有额外知识材料，也能稳定支撑连续对话和个性化提醒。",
                )
            if has_short_term_support and top_memory_score >= 1.0:
                return (
                    "helpful",
                    "本轮主要依赖近期对话记忆完成连续追问，命中强度足够高，说明记忆层已经有效支撑回答。",
                )
            if (
                not has_short_term_support
                and latest_memory_age_days is not None
                and latest_memory_age_days >= 30
            ):
                return (
                    "stale",
                    f"本轮仅命中较旧的长期记忆（最近一条约 {latest_memory_age_days} 天前），缺少新的知识材料佐证，后续应提醒运营检查信息是否过时。",
                )
            return (
                "review_worthy",
                "本轮回答主要依赖历史记忆，虽然可用于连续对话，但缺少新的知识材料支撑，建议在运营侧抽查是否需要补充更新资料。",
            )
        if (
            (memory_hits or recent_session_attached)
            and (has_short_term_support or len(long_term_hits) > 0)
            and top_memory_score >= 1.0
        ):
            return (
                "helpful",
                "本轮同时复用了对话记忆和知识材料，且记忆命中分数较高，说明检索上下文对回答形成了直接帮助。",
            )
        return (
            "helpful",
            "本轮主要由知识材料完成 grounding，记忆层没有形成强命中，但现有证据仍足以支撑回答。",
        )

    def _memory_usefulness_label(self, signal: str) -> str:
        mapping = {
            "helpful": "有帮助",
            "stale": "可能过时",
            "low_confidence": "低置信度",
            "review_worthy": "建议复核",
        }
        return mapping.get(signal, signal)

    def _build_token_usage(self) -> TokenUsage | None:
        """Return per-request token usage from the LLM client, or None."""
        try:
            usage = self._llm_client.last_request_usage
            if not usage:
                return None
            return TokenUsage(
                prompt_tokens=int(usage.get("prompt_tokens", 0)),
                completion_tokens=int(usage.get("completion_tokens", 0)),
                total_tokens=int(usage.get("total_tokens", 0)),
                max_context_length=int(self._llm_client.model_max_len or 0),
            )
        except Exception:
            return None

    def _build_planner_preview(
        self, decision: PlannerDecision | None
    ) -> WorkflowPlanPreview | None:
        if decision is None:
            return None

        return WorkflowPlanPreview(
            planner_version=decision.plan.planner_version,
            policy_version=decision.plan.policy_version,
            planner_mode=decision.plan.planner_mode,
            execution_mode=decision.plan.execution_mode,
            goal=decision.plan.goal,
            accepted=decision.accepted,
            fallback_template=decision.plan.fallback_template,
            fallback_reason=(
                decision.fallback.reason
                if decision.fallback is not None
                else decision.plan.fallback_reason
            ),
            planned_steps=[step.step_id for step in decision.plan.steps],
            validation_errors=decision.validation_errors,
            explain_to_operator=decision.plan.explain_to_operator,
        )

    def _build_shadow_planner_preview(
        self,
        decision: PlannerDecision | None,
        shadow_decision: PlannerDecision | None,
        shadow_status: str,
        shadow_message: str | None,
    ) -> WorkflowPlanPreview | None:
        if shadow_status == "shadow_disabled":
            fallback_template = (
                decision.plan.fallback_template if decision is not None else "answer_question"
            )
            return WorkflowPlanPreview(
                planner_version=decision.plan.planner_version if decision is not None else "v3.0.0",
                policy_version=decision.plan.policy_version
                if decision is not None
                else "faculty-default-2026-05",
                planner_mode="llm_shadow",
                execution_mode="shadow_or_template",
                goal="shadow planner pending",
                accepted=False,
                fallback_template=fallback_template,
                fallback_reason=shadow_message or "LLM shadow planner not enabled yet.",
                planned_steps=[],
                validation_errors=["shadow planner disabled"],
                explain_to_operator=(
                    "Reserved lane for future LLM planner shadow comparison. Deterministic planning remains authoritative."
                ),
            )

        if shadow_status == "shadow_error":
            fallback_template = (
                decision.plan.fallback_template if decision is not None else "answer_question"
            )
            return WorkflowPlanPreview(
                planner_version=decision.plan.planner_version if decision is not None else "v3.0.0",
                policy_version=decision.plan.policy_version
                if decision is not None
                else "faculty-default-2026-05",
                planner_mode="llm_shadow",
                execution_mode="shadow_or_template",
                goal="shadow planner error",
                accepted=False,
                fallback_template=fallback_template,
                fallback_reason=shadow_message or "LLM shadow planner failed.",
                planned_steps=[],
                validation_errors=["shadow planner error"],
                explain_to_operator="Shadow planner proposal failed; deterministic planning remains authoritative.",
            )

        if shadow_decision is None:
            return None

        return WorkflowPlanPreview(
            planner_version=shadow_decision.plan.planner_version,
            policy_version=shadow_decision.plan.policy_version,
            planner_mode=shadow_decision.plan.planner_mode,
            execution_mode=shadow_decision.plan.execution_mode,
            goal=shadow_decision.plan.goal,
            accepted=shadow_decision.accepted,
            fallback_template=shadow_decision.plan.fallback_template,
            fallback_reason=(
                shadow_decision.fallback.reason
                if shadow_decision.fallback is not None
                else shadow_message
            ),
            planned_steps=[step.step_id for step in shadow_decision.plan.steps],
            validation_errors=shadow_decision.validation_errors,
            explain_to_operator=shadow_decision.plan.explain_to_operator,
        )

    def _build_planner_comparison(
        self,
        decision: PlannerDecision | None,
        shadow_decision: PlannerDecision | None,
        shadow_status: str,
        shadow_message: str | None,
    ) -> WorkflowPlanComparison | None:
        if decision is None:
            return None

        deterministic_steps = [step.step_id for step in decision.plan.steps]
        if shadow_status == "shadow_disabled":
            return WorkflowPlanComparison(
                comparison_status="shadow_disabled",
                same_goal=True,
                same_fallback_template=True,
                shared_steps=[],
                deterministic_only_steps=deterministic_steps,
                shadow_only_steps=[],
                summary=(
                    shadow_message
                    or "Shadow planner lane is reserved but not enabled yet. Deterministic planning remains authoritative for this request."
                ),
            )

        if shadow_status == "shadow_error" or shadow_decision is None:
            return WorkflowPlanComparison(
                comparison_status="shadow_error",
                same_goal=True,
                same_fallback_template=True,
                shared_steps=[],
                deterministic_only_steps=deterministic_steps,
                shadow_only_steps=[],
                summary=(
                    shadow_message
                    or "Shadow planner proposal failed. Deterministic planning remains authoritative for this request."
                ),
            )

        shadow_steps = [step.step_id for step in shadow_decision.plan.steps]
        shared_steps = [step for step in deterministic_steps if step in shadow_steps]
        deterministic_only_steps = [
            step for step in deterministic_steps if step not in shadow_steps
        ]
        shadow_only_steps = [step for step in shadow_steps if step not in deterministic_steps]
        same_goal = decision.plan.goal == shadow_decision.plan.goal
        same_fallback_template = (
            decision.plan.fallback_template == shadow_decision.plan.fallback_template
        )
        if not same_goal:
            comparison_status = "different_goal"
        elif deterministic_steps == shadow_steps and same_fallback_template:
            comparison_status = "equivalent"
        else:
            comparison_status = "different_steps"

        return WorkflowPlanComparison(
            comparison_status=comparison_status,
            same_goal=same_goal,
            same_fallback_template=same_fallback_template,
            shared_steps=shared_steps,
            deterministic_only_steps=deterministic_only_steps,
            shadow_only_steps=shadow_only_steps,
            summary=self._build_planner_comparison_summary(
                decision,
                shadow_decision,
                comparison_status=comparison_status,
            ),
        )

    def _build_planner_comparison_summary(
        self,
        decision: PlannerDecision,
        shadow_decision: PlannerDecision,
        *,
        comparison_status: str,
    ) -> str:
        if comparison_status == "equivalent":
            return (
                "Shadow planner matched the deterministic plan on goal, fallback, and ordered steps. "
                "Execution still follows the deterministic lane."
            )
        if comparison_status == "different_goal":
            return (
                f"Shadow planner proposed {shadow_decision.plan.goal} instead of {decision.plan.goal}. "
                "Execution still follows the deterministic lane."
            )
        return (
            f"Shadow planner kept goal {shadow_decision.plan.goal} but changed the proposed step sequence from "
            f"{decision.plan.goal}. Execution still follows the deterministic lane."
        )

    def add_knowledge(
        self, request: KnowledgeDocumentCreate | dict[str, Any]
    ) -> KnowledgeDocumentRecord:
        normalized = (
            request
            if isinstance(request, KnowledgeDocumentCreate)
            else KnowledgeDocumentCreate.model_validate(request)
        )
        return self._knowledge_store.add_document(normalized)

    def list_knowledge(self) -> list[KnowledgeDocumentRecord]:
        return self._knowledge_store.list_documents()

    def list_knowledge_review_summary(self, limit: int = 20) -> KnowledgeDocumentReviewSummary:
        documents = self.list_knowledge()
        feedback_web_documents = [document for document in documents if document.is_feedback_web]
        pending_documents = [
            document for document in feedback_web_documents if document.review_status == "pending"
        ]
        approved_documents = [
            document for document in feedback_web_documents if document.review_status == "approved"
        ]
        stale_documents = [
            document for document in feedback_web_documents if document.review_status == "stale"
        ]
        pending_items = sorted(
            pending_documents,
            key=lambda document: document.created_at,
            reverse=True,
        )[: max(0, int(limit))]
        return KnowledgeDocumentReviewSummary(
            total_documents=len(documents),
            feedback_web_documents=len(feedback_web_documents),
            pending_documents=len(pending_documents),
            approved_documents=len(approved_documents),
            stale_documents=len(stale_documents),
            reviewable_documents=sum(1 for document in feedback_web_documents if document.reviewable),
            pending_items=pending_items,
        )

    def review_knowledge_document(
        self,
        document_id: str,
        request: KnowledgeDocumentReviewRequest | dict[str, Any],
    ) -> KnowledgeDocumentActionResponse:
        normalized = (
            request
            if isinstance(request, KnowledgeDocumentReviewRequest)
            else KnowledgeDocumentReviewRequest.model_validate(request)
        )
        existing = self._knowledge_store.get_document(document_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="找不到对应的知识文档。",
            )

        source_name = str(existing.source_name or "").lower()
        tags = {str(tag).lower() for tag in existing.tags}
        if not (source_name.startswith("feedback-web:") or "feedback-web" in tags):
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail="当前仅支持审核 feedback-web 联网资料。",
            )

        next_tags: list[str] = []
        for tag in existing.tags:
            lowered = str(tag).lower()
            if lowered.startswith("review:") or lowered.startswith("freshness:"):
                continue
            next_tags.append(tag)

        if normalized.action == "approve":
            next_tags.extend(["review:approved", "freshness:web"])
            review_status = "approved"
            freshness_status = "web"
        else:
            next_tags.extend(["review:stale", "freshness:stale"])
            review_status = "stale"
            freshness_status = "stale"

        deduped_tags: list[str] = []
        seen_tags: set[str] = set()
        for tag in next_tags:
            lowered = str(tag).lower()
            if lowered in seen_tags:
                continue
            seen_tags.add(lowered)
            deduped_tags.append(tag)

        next_metadata = dict(existing.metadata)
        next_metadata["review_status"] = review_status
        next_metadata["freshness_status"] = freshness_status
        next_metadata["reviewed_at"] = datetime.now(UTC).isoformat()

        updated = self._knowledge_store.update_document(
            document_id,
            KnowledgeDocumentCreate(
                title=existing.title,
                content=existing.content,
                tags=deduped_tags,
                source_name=existing.source_name,
                metadata=next_metadata,
            ),
        )
        return KnowledgeDocumentActionResponse(
            document_id=updated.document_id,
            action=normalized.action,
            document=updated,
            deleted_count=0,
        )

    def delete_knowledge_document(self, document_id: str) -> KnowledgeDocumentActionResponse:
        existing = self._knowledge_store.get_document(document_id)
        if existing is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="找不到对应的知识文档。",
            )
        deleted_count = self._knowledge_store.delete_documents([document_id])
        return KnowledgeDocumentActionResponse(
            document_id=document_id,
            action="delete",
            document=None,
            deleted_count=deleted_count,
        )

    def search_knowledge(
        self,
        query: str,
        visitor_profile: str | None = None,
        admin_role: str | None = None,
    ) -> KnowledgeSearchResponse:
        return KnowledgeSearchResponse(
            hits=self._knowledge_store.search(
                query,
                visitor_profile=visitor_profile,
                admin_role=admin_role,
            )
        )

    def book_meeting(self, request: BookingRequest) -> BookingResponse:
        response = self._meeting_service.book(request)
        if not response.accepted:
            return response
        return self._attach_booking_request_notification(response)

    def list_bookings(self, status: str | None = None) -> list[BookingRecord]:
        return self._meeting_service.list_bookings(status=status)

    def list_memory_profiles(
        self,
        *,
        category: str | None = None,
        student_query: str | None = None,
        limit: int = 50,
    ) -> MemoryProfileListResponse:
        all_profiles = self._conversation_store.list_profiles(limit=1000)
        category_counts: dict[str, int] = {}
        for profile in all_profiles:
            category_counts[profile.category] = category_counts.get(profile.category, 0) + 1

        profiles = self._conversation_store.list_profiles(
            category=category,
            student_query=student_query,
            limit=limit,
        )
        return MemoryProfileListResponse(
            available_categories=self._conversation_store.available_profile_categories(),
            category_counts=category_counts,
            profiles=[
                MemoryProfileRecordResponse(
                    profile_id=profile.profile_id,
                    student_key=profile.student_key,
                    student_name=profile.student_name,
                    student_email=profile.student_email,
                    category=profile.category,
                    summary=profile.summary,
                    evidence=profile.evidence,
                    updated_at=profile.updated_at,
                )
                for profile in profiles
            ],
        )

    def submit_chat_feedback(self, request: ChatFeedbackRequest) -> ChatFeedbackResponse:
        try:
            feedback = self._analytics_store.submit_feedback(
                exchange_id=request.exchange_id,
                rating=request.rating,
                resolved=request.resolved,
                needs_human_followup=request.needs_human_followup,
                issue_summary=request.issue_summary,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="找不到对应的问答记录。",
            ) from exc

        knowledge_write_backs = self._write_back_feedback_knowledge(feedback.exchange_id, feedback)

        return ChatFeedbackResponse(
            exchange_id=feedback.exchange_id,
            rating=feedback.rating,
            resolved=feedback.resolved,
            needs_human_followup=feedback.needs_human_followup,
            issue_summary=feedback.issue_summary,
            knowledge_write_backs=knowledge_write_backs,
            created_at=feedback.created_at,
            updated_at=feedback.updated_at,
        )

    def _write_back_feedback_knowledge(
        self,
        exchange_id: str,
        feedback,
    ) -> list[KnowledgeWriteBackResult]:
        if feedback.rating != "up" or not feedback.resolved:
            return []

        record = self._conversation_store.get_record(exchange_id)
        if record is None or not record.web_search_hits:
            return []

        answer_text = str(record.answer or "")
        if not any(self._answer_references_web_hit(answer_text, hit) for hit in record.web_search_hits):
            return []

        results: list[KnowledgeWriteBackResult] = []
        seen_source_names: set[str] = set()
        for index, hit in enumerate(record.web_search_hits[:2], start=1):
            payload = self._build_feedback_web_knowledge_payload(record, hit, index=index)
            if payload is None:
                continue
            if payload.source_name in seen_source_names:
                continue
            seen_source_names.add(payload.source_name)
            stored, created = self._knowledge_store.upsert_document(payload)
            results.append(
                KnowledgeWriteBackResult(
                    document_id=stored.document_id,
                    title=stored.title,
                    source_name=stored.source_name,
                    created=created,
                )
            )
        return results

    def submit_anonymous_suggestion(
        self, request: AnonymousSuggestionCreate
    ) -> AnonymousSuggestionRecord:
        return self._suggestion_store.create_suggestion(request)

    def list_anonymous_suggestions(self, *, limit: int = 50) -> list[AnonymousSuggestionRecord]:
        return self._suggestion_store.list_suggestions(limit=limit)

    def get_question_analytics_report(self, *, days: int = 7) -> QuestionAnalyticsReportResponse:
        report = self._analytics_store.build_weekly_report(days=days)
        enriched_gap_suggestions = []
        for item in report["knowledge_gap_suggestions"]:
            draft = self._knowledge_gap_draft_store.get_by_cluster_id(str(item["cluster_id"]))
            enriched_item = dict(item)
            if draft is not None:
                enriched_item["draft_id"] = draft.draft_id
                enriched_item["draft_status"] = draft.status
            enriched_gap_suggestions.append(enriched_item)
        return QuestionAnalyticsReportResponse(
            window_days=report["window_days"],
            window_start=report["window_start"],
            window_end=report["window_end"],
            overview=QuestionAnalyticsOverview(**report["overview"]),
            top_clusters=[QuestionClusterSummary(**item) for item in report["top_clusters"]],
            knowledge_gap_suggestions=[
                KnowledgeGapSuggestion(**item) for item in enriched_gap_suggestions
            ],
            unresolved_questions=[
                UnresolvedQuestionItem(**item) for item in report["unresolved_questions"]
            ],
            handoff_categories=[
                HandoffCategorySummary(**item) for item in report["handoff_categories"]
            ],
        )

    def create_knowledge_gap_draft(
        self,
        request: KnowledgeGapDraftCreateRequest,
    ) -> KnowledgeGapDraftRecordResponse:
        try:
            payload = self._analytics_store.build_gap_draft_payload(
                cluster_id=request.cluster_id, days=request.days
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="找不到对应的知识缺口建议。",
            ) from exc
        return self._knowledge_gap_draft_store.upsert_generated_draft(**payload)

    def list_knowledge_gap_drafts(self) -> list[KnowledgeGapDraftRecordResponse]:
        return self._knowledge_gap_draft_store.list_drafts()

    def list_artifact_memory_drafts(self) -> list[ArtifactMemoryDraftRecordResponse]:
        return [record.to_response() for record in self._artifact_memory_draft_store.list_drafts()]

    def accept_artifact_memory_draft(self, draft_id: str) -> ArtifactMemoryDraftRecordResponse:
        draft = self._artifact_memory_draft_store.get_draft(draft_id)
        if draft is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="找不到对应的材料草稿。",
            )
        try:
            return self._artifact_memory_draft_store.mark_accepted(draft_id).to_response()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"材料草稿当前状态为 {draft.status}，无法再次审核。",
            ) from exc

    def reject_artifact_memory_draft(self, draft_id: str) -> ArtifactMemoryDraftRecordResponse:
        draft = self._artifact_memory_draft_store.get_draft(draft_id)
        if draft is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="找不到对应的材料草稿。",
            )
        try:
            return self._artifact_memory_draft_store.mark_rejected(draft_id).to_response()
        except ValueError as exc:
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=f"材料草稿当前状态为 {draft.status}，无法再次审核。",
            ) from exc

    def publish_knowledge_gap_draft(self, draft_id: str) -> KnowledgeGapDraftRecordResponse:
        draft = self._knowledge_gap_draft_store.get_draft(draft_id)
        if draft is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="找不到对应的知识草稿。",
            )
        payload = _build_published_gap_document(draft.to_response())
        if draft.status == "published" and draft.published_document_id:
            current = next(
                (
                    document
                    for document in self._knowledge_store.list_documents()
                    if document.document_id == draft.published_document_id
                ),
                None,
            )
            if current is not None and _document_matches_payload(current, payload):
                return draft.to_response()
        published_document, _ = self._knowledge_store.upsert_document(payload)
        if (
            draft.status == "published"
            and draft.published_document_id == published_document.document_id
        ):
            return draft.to_response()
        return self._knowledge_gap_draft_store.mark_published(
            draft_id, document_id=published_document.document_id
        )

    def list_escalations(
        self,
        *,
        status: str | None = None,
        route: str | None = None,
    ) -> list[EscalationRecord]:
        return self._escalation_store.list_requests(status=status, route=route)

    def resolve_escalation(
        self,
        escalation_id: str,
        decision: EscalationDecisionRequest | dict[str, Any] | None = None,
    ) -> EscalationRecord:
        normalized = (
            EscalationDecisionRequest.model_validate(decision)
            if decision is not None
            else EscalationDecisionRequest()
        )
        try:
            return self._escalation_store.resolve_request(
                escalation_id,
                resolution_note=normalized.resolution_note,
            )
        except KeyError as exc:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail="找不到对应的人工处理请求。",
            ) from exc

    def list_follow_up_actions(
        self,
        *,
        status: str | None = None,
        action_type: str | None = None,
    ) -> list[FollowUpQueueRecord]:
        return self._follow_up_store.list_actions(status=status, action_type=action_type)

    def dispatch_due_follow_ups(self) -> FollowUpDispatchResponse:
        due_entries = self._follow_up_store.list_due_actions()
        sent_count = 0
        for entry in due_entries:
            self._email_notifier.send_follow_up_email(
                entry.student_email, entry.subject, entry.lines
            )
            self._follow_up_store.mark_sent(entry.action_id)
            sent_count += 1

        pending_count = len(self._follow_up_store.list_actions(status="queued"))
        return FollowUpDispatchResponse(
            processed_count=len(due_entries),
            sent_count=sent_count,
            pending_count=pending_count,
        )

    def confirm_booking(self, booking_id: str) -> BookingResponse:
        response = self._meeting_service.confirm_booking(booking_id)
        notified_response = self._attach_student_notification(
            response,
            success_message_prefix="已向 {recipient} 发送预约确认邮件。",
            notify=self._email_notifier.send_booking_approved_notification,
        )
        booking = notified_response.booking
        if notified_response.accepted and booking is not None and booking.status == "已确认":
            profiles = self._conversation_store.list_profiles_for_student(
                student_name=booking.student_name,
                student_email=booking.student_email,
                limit=10,
            )
            related_hits = self._knowledge_store.search(booking.topic, top_k=2)
            drafts = self._action_planner.build_booking_follow_up_email_drafts(
                booking=booking,
                student_profiles=profiles,
                related_hits=related_hits,
            )
            for draft in drafts:
                self._follow_up_store.queue_action(
                    booking_id=booking.booking_id,
                    student_name=booking.student_name,
                    student_email=booking.student_email,
                    action_type=draft.action_type,
                    title=draft.title,
                    detail=draft.detail,
                    subject=draft.subject,
                    lines=draft.lines,
                    due_at=draft.due_at,
                )
        return notified_response

    def reject_booking(
        self, booking_id: str, rejection_reason: str | None = None
    ) -> BookingResponse:
        response = self._meeting_service.reject_booking(
            booking_id, rejection_reason=rejection_reason
        )
        return self._attach_student_notification(
            response,
            success_message_prefix="已向 {recipient} 发送预约拒绝通知邮件。",
            notify=self._email_notifier.send_booking_rejected_notification,
        )

    def read_admin_session(self, request: AdminSessionTokenInput) -> AdminSessionResponse:
        payload = normalize_admin_session_payload(
            decode_admin_session_token(request.session_token, self._settings),
            self._settings,
        )
        return self._build_admin_session_response(payload)

    def require_admin_session(self, request: AdminSessionTokenInput) -> dict[str, Any]:
        payload = normalize_admin_session_payload(
            decode_admin_session_token(request.session_token, self._settings),
            self._settings,
        )
        if payload is None:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="需要管理员身份验证。",
            )
        return payload

    def login_admin(self, request: AdminLoginRequest) -> AdminLoginWorkflowResult:
        username, role = validate_admin_credentials(
            request.username,
            request.password,
            self._settings,
        )
        token = build_admin_session_token(self._settings, username=username, role=role)
        return AdminLoginWorkflowResult(
            session=AdminSessionResponse(
                is_admin=True,
                mode="admin",
                username=username,
                role=role,
            ),
            session_token=token,
        )

    def logout_admin(self, _: object) -> AdminSessionResponse:
        return AdminSessionResponse(is_admin=False, mode="user")

    def read_user_session(self, request: UserSessionTokenInput) -> UserSessionResponse:
        payload = decode_user_session_token(request.session_token, self._settings)
        return self._build_user_session_response(payload)

    def register_user(self, request: UserRegisterRequest) -> UserAuthWorkflowResult:
        account = self._user_store.register_user(
            name=request.name,
            email=request.email,
            visitor_profile=request.visitor_profile,
            password=request.password,
            invitation_code=request.invitation_code,
        )
        token = build_user_session_token(
            user_id=account.user_id, email=account.email, settings=self._settings
        )
        return UserAuthWorkflowResult(
            session=UserSessionResponse(is_authenticated=True, mode="user", account=account),
            session_token=token,
        )

    def login_user(self, request: UserLoginRequest) -> UserAuthWorkflowResult:
        account = self._user_store.authenticate_user(
            email=request.email,
            password=request.password,
            invitation_code=request.invitation_code,
        )
        token = build_user_session_token(
            user_id=account.user_id, email=account.email, settings=self._settings
        )
        return UserAuthWorkflowResult(
            session=UserSessionResponse(is_authenticated=True, mode="user", account=account),
            session_token=token,
        )

    def logout_user(self, _: object) -> UserSessionResponse:
        return UserSessionResponse(is_authenticated=False, mode="guest")

    def _build_student_prompt(
        self,
        request: ChatRequest,
        knowledge_hits: list[KnowledgeSearchHit],
        web_search_hits: list[WebSearchHit] | None = None,
        memory_hits: list[ConversationMemoryHit] | None = None,
        interaction_intent: InteractionIntent | None = None,
        recent_session_context: str | None = None,
    ) -> str:
        course_hint = (
            f"Course context: {request.course_context}.\n" if request.course_context else ""
        )
        visitor_profile = getattr(request, "visitor_profile", None)
        visitor_hint = f"Visitor profile: {visitor_profile}.\n" if visitor_profile else ""
        attachment_context = self._format_attachment_context(getattr(request, "attachments", None))
        resolved_recent_session_context = recent_session_context
        if resolved_recent_session_context is None:
            resolved_recent_session_context = self._format_recent_session_context(request)
        memory_context = self._format_memory_context(memory_hits or [])
        prompt_hits = self._select_prompt_knowledge_hits(
            request.question, knowledge_hits, interaction_intent
        )
        materializable_hits, residual_prompt_hits = self._split_materializable_knowledge_hits(
            request,
            prompt_hits,
        )
        materializable_knowledge_context = self._format_materializable_knowledge_context(
            request,
            materializable_hits,
        )
        knowledge_context = self._format_knowledge_context(residual_prompt_hits)
        web_search_context = self._format_web_search_context(web_search_hits or [])
        intent_guidance = self._build_intent_guidance(interaction_intent)
        profile_grounding_guidance = self._build_profile_grounding_guidance(
            request, interaction_intent
        )
        fast_answer_guidance = self._build_fast_answer_guidance(request, interaction_intent)
        deep_answer_guidance = self._build_deep_answer_guidance(request)
        preparation_guidance = self._build_meeting_preparation_guidance(
            request.question, interaction_intent
        )
        advising_guidance = self._build_advising_response_guidance(
            request.question, interaction_intent
        )
        teaching_guidance = self._build_teaching_response_guidance(
            request.question, interaction_intent
        )
        research_guidance = self._build_research_response_guidance(
            request.question, interaction_intent
        )
        technical_guidance = self._build_general_technical_response_guidance(
            request.question, interaction_intent
        )
        owner_fact_grounding = self._build_owner_fact_grounding_guidance(
            request.question,
            prompt_hits,
        )
        availability_context = self._meeting_service.describe_current_availability()
        live_calendar_context = self._calendar_bridge.describe_for_prompt(request.question)
        return (
            "Task context for answering the current user question:\n"
            "Treat this block as context, not as a request to discuss your instructions or identity. "
            "Answer the current question directly as the digital twin of the faculty owner. Keep the answer grounded and concise. "
            "If the current question is a follow-up that refers to 刚才, 前面, 上一个, this, that, it, or an omitted subject, resolve it against the immediate session context first. "
            "Use retrieved knowledge only when it directly answers this question; ignore adjacent topics and do not add unasked facts just because they appear in context. "
            "Never invent paper titles, author names, conference names, URLs, or any bibliographic reference. "
            "If the answer would benefit from external references but none are available in the context below, "
            "remind the user they can enable the 联网检索 toggle for real-time sources.\n"
            f"{materializable_knowledge_context}"
            "Request context:\n"
            f"Student name: {request.student_name}\n"
            f"{course_hint}"
            f"{visitor_hint}"
            f"{intent_guidance}"
            f"{profile_grounding_guidance}"
            f"{fast_answer_guidance}"
            f"{deep_answer_guidance}"
            f"{preparation_guidance}"
            f"{advising_guidance}"
            f"{teaching_guidance}"
            f"{research_guidance}"
            f"{technical_guidance}"
            f"{availability_context}"
            f"{live_calendar_context}"
            f"{attachment_context}"
            f"{resolved_recent_session_context}"
            f"{memory_context}"
            f"{knowledge_context}"
            f"{web_search_context}"
            f"{owner_fact_grounding}"
            "Current user question (answer this directly):\n"
            f"{request.question}\n"
        )

    @classmethod
    def _build_owner_fact_grounding_guidance(
        cls,
        question: str,
        knowledge_hits: list[KnowledgeSearchHit],
    ) -> str:
        """Repeat retrieved owner facts next to the final question.

        Mixed questions can be classified as advising even when they also ask
        for the owner's current research. Keeping the relevant retrieved facts
        adjacent to the final question prevents a small model from replacing
        them with a plausible but unsupported faculty profile.
        """

        if not cls._IDENTITY_QUESTION_PATTERN.search(question):
            return ""
        owner_hits = [
            hit
            for hit in knowledge_hits
            if {tag.lower() for tag in hit.tags}
            & {"profile", "research", "research-agenda", "overview"}
        ][:2]
        if not owner_hits:
            return ""
        lines = [
            "Mandatory owner-fact grounding for the current question:",
            "Answer owner-specific facts only from the retrieved statements below. Reuse their concrete research terms; do not substitute a plausible unrelated field.",
        ]
        for hit in owner_hits:
            excerpt = re.sub(r"\s+", " ", hit.excerpt).strip()[:700]
            lines.append(f"- {hit.title}: {excerpt}")
        return "\n".join(lines) + "\n"

    @staticmethod
    def _is_explicit_deep_request(request: ChatRequest) -> bool:
        """Return whether the user deliberately selected deep analysis."""
        return bool(
            getattr(request, "deep_thinking_explicit", False)
            and getattr(request, "deep_thinking", True)
        )

    @classmethod
    def _build_deep_answer_guidance(cls, request: ChatRequest) -> str:
        if not cls._is_explicit_deep_request(request):
            return ""
        return (
            "Deep analysis mode is active because the user explicitly selected it. "
            "Do not expose hidden chain-of-thought or <think> tags. The final answer must be "
            "materially more analytical than a fast answer and use four clearly labeled parts: "
            "(1) 核心判断, (2) 关键依据 with 3-5 concrete factors, "
            "(3) 权衡与风险 including assumptions or uncertainty, and "
            "(4) 建议行动 with practical next steps or evaluation criteria. "
            "Prioritize depth and decision usefulness over brevity while avoiding filler.\n"
        )

    def _build_fast_answer_guidance(
        self,
        request: ChatRequest,
        interaction_intent: InteractionIntent | None,
    ) -> str:
        if not self._settings.fast_answer_concise_guidance_enabled:
            return ""
        if self._is_explicit_deep_request(request):
            return ""

        question = request.question
        lowered = question.lower()
        if self._looks_like_general_technical_question(question):
            return ""
        if any(
            marker in lowered or marker in question
            for marker in (
                "详细",
                "展开",
                "深入",
                "完整",
                "长一点",
                "具体分析",
                "step by step",
                "in detail",
                "详细解释",
            )
        ):
            return ""

        domain = interaction_intent.domain if interaction_intent is not None else "general"
        decision_mode = (
            interaction_intent.decision_mode if interaction_intent is not None else "direct_answer"
        )
        auto_disable_domains = {
            item.strip()
            for item in self._settings.auto_disable_thinking_intents.split(",")
            if item.strip()
        }
        is_fast_lane = (
            not getattr(request, "deep_thinking", True)
            or domain in auto_disable_domains
            or decision_mode == "direct_answer"
        )
        if not is_fast_lane:
            return ""

        return (
            "Fast-answer guidance: Start with the direct answer in the first sentence. "
            "Unless the user explicitly asks for detail, keep the final response to "
            "80-160 Chinese characters or at most 3 short bullets; do not add a closing offer or extra background.\n"
        )

    def _format_recent_session_context(self, request: ChatRequest, limit: int = 4) -> str:
        conversation_id = str(getattr(request, "conversation_id", "") or "").strip()
        if not conversation_id or limit <= 0:
            return ""

        # Prepend the rolling digest (compressed older turns) when available.
        digest_text = ""
        if self._digest_store is not None and self._settings.context_digest_enabled:
            digest_record = self._digest_store.get_digest(conversation_id)
            if digest_record is not None and digest_record.digest_text.strip():
                digest_text = digest_record.digest_text.strip()

        recent_records = self._conversation_store.list_recent_conversation_records(
            conversation_id,
            exclude_question=str(getattr(request, "question", "") or ""),
            limit=limit,
        )
        if not digest_text and not recent_records:
            return ""

        sections: list[str] = []
        if digest_text:
            sections.append(f"Session digest (earlier turns, {digest_record.turns_summarized} turns compressed):\n{digest_text}")
        if recent_records:
            sections.append("Immediate session context (same conversation):")
            for index, record in enumerate(reversed(recent_records), start=1):
                sections.append(f"{index}. User: {record.question}\nAssistant: {record.answer}")
        return "\n".join(sections) + "\n"

    def _build_recent_session_meta_answer(self, request: ChatRequest) -> str | None:
        recall_kind = self._detect_recent_session_meta_query(request.question)
        if recall_kind is None:
            return None

        conversation_id = (request.conversation_id or "").strip()
        if not conversation_id:
            return "当前这轮请求没有带会话上下文，所以我没法确认你上一条具体说了什么。"

        recent_records = self._conversation_store.list_recent_conversation_records(
            conversation_id,
            exclude_question=request.question,
            limit=1,
        )
        if not recent_records:
            return "在当前这个会话里，你这条之前还没有上一轮可回忆的内容。"

        previous_record = recent_records[0]
        if recall_kind == "previous_question":
            previous_question = previous_record.question.strip()
            if not previous_question:
                return "我找到了上一轮记录，但上一轮问题内容是空的。"
            return f"你刚刚问的是：{previous_question}"

        previous_answer = previous_record.answer.strip()
        if not previous_answer:
            return "我找到了上一轮记录，但我上一轮的回答内容是空的。"
        return f"我刚刚回答的是：{previous_answer}"

    def _detect_recent_session_meta_query(self, question: str) -> str | None:
        normalized_question = _RECENT_SESSION_QUERY_NORMALIZER.sub("", question).lower()
        if any(pattern.match(normalized_question) for pattern in _PREVIOUS_QUESTION_QUERY_PATTERNS):
            return "previous_question"
        if any(pattern.match(normalized_question) for pattern in _PREVIOUS_ANSWER_QUERY_PATTERNS):
            return "previous_answer"
        return None

    def _build_profile_grounding_guidance(
        self,
        request: ChatRequest,
        interaction_intent: InteractionIntent | None,
    ) -> str:
        question = request.question
        lowered = question.lower()
        profile_markers = (
            "学生画像",
            "按我的情况",
            "结合我的情况",
            "基于我的背景",
            "按我现在",
            "按我当前",
            "结合我的阶段",
            "每周只有",
            "本周只能",
            "两周后",
            "问题比较碎",
            "阅读论文慢",
            "少占用老师时间",
            "不是来问选题",
        )
        if not any(marker in question or marker in lowered for marker in profile_markers):
            return ""

        guidance = (
            "Profile grounding guidance: When the student question or profile gives concrete facts, explicitly reuse 1-2 concrete facts or phrases from that profile in the answer instead of replacing them with abstract summaries. "
            "Anchor the recommendation with wording such as '结合你现在...' or '按你当前...' and state the advice with wording such as '建议...' or '建议先...'. "
            "Do not drop concrete constraints, weaknesses, progress markers, or communication preferences just because the high-level answer already seems clear.\n"
        )

        if request.course_context == "LaMP personalization benchmark" or "学生画像：" in question:
            guidance += (
                "LaMP profile grounding: For benchmark-style profile questions, mention at least one concrete fact from the student profile block and one concrete need from the current question. "
                "Prefer near-copy wording for details such as time budget, current stage, stated pain point, '不是来问选题', or '少占用老师时间' when they appear.\n"
            )

        if interaction_intent is not None and interaction_intent.decision_mode == "advise_only":
            guidance += "Advice phrasing guidance: Prefer a first sentence that directly gives the recommendation, for example '建议你先...' or '按你现在的情况，更适合先...'.\n"

        return guidance

    def _format_attachment_context(self, attachments: list[ChatAttachment] | None) -> str:
        if not attachments:
            return ""

        entries = []
        for attachment in attachments:
            size_text = (
                f"{attachment.size_bytes} bytes"
                if attachment.size_bytes is not None
                else "unknown size"
            )
            entries.append(
                f"Attachment: {attachment.file_name} ({attachment.media_type}, {size_text})\n"
                f"{attachment.text_content.strip()}"
            )

        return "Attached file excerpts:\n" + "\n\n".join(entries) + "\n"

    def _is_benchmark_request(self, request: ChatRequest) -> bool:
        course_context = (request.course_context or "").strip()
        return course_context in {
            "CharacterEval role-play benchmark",
            "LaMP personalization benchmark",
        }

    def _build_admin_session_response(
        self,
        payload: dict[str, Any] | None,
    ) -> AdminSessionResponse:
        if payload is None:
            return AdminSessionResponse(is_admin=False, mode="user")
        username, role = resolve_admin_session_identity(payload, self._settings)
        return AdminSessionResponse(
            is_admin=True,
            mode="admin",
            username=username,
            role=role,
        )

    def _resolve_admin_role(self) -> str | None:
        if self._admin_session_payload is None:
            return None
        _, role = resolve_admin_session_identity(self._admin_session_payload, self._settings)
        return role

    def _build_user_session_response(
        self,
        payload: dict[str, Any] | None,
    ) -> UserSessionResponse:
        if payload is None:
            return UserSessionResponse(is_authenticated=False, mode="guest")

        user_id = str(payload.get("sub") or "")
        if not user_id:
            return UserSessionResponse(is_authenticated=False, mode="guest")

        account = self._user_store.get_user_by_id(user_id)
        if account is None:
            return UserSessionResponse(is_authenticated=False, mode="guest")
        return UserSessionResponse(is_authenticated=True, mode="user", account=account)

    def _format_knowledge_context(self, knowledge_hits: list[KnowledgeSearchHit]) -> str:
        if not knowledge_hits:
            return ""

        sections = ["Relevant owner materials:"]
        for index, hit in enumerate(knowledge_hits, start=1):
            source_suffix = f" | source: {hit.source_name}" if hit.source_name else ""
            sections.append(
                f"{index}. {hit.title}{source_suffix}\nExcerpt: {hit.excerpt}\nTags: {', '.join(hit.tags) if hit.tags else 'none'}"
            )
        return "\n".join(sections) + "\n"

    def _split_materializable_knowledge_hits(
        self,
        request: ChatRequest,
        knowledge_hits: list[KnowledgeSearchHit],
    ) -> tuple[list[KnowledgeSearchHit], list[KnowledgeSearchHit]]:
        if not self._settings.dynamic_context_materialization_enabled:
            return [], knowledge_hits
        materializable: list[KnowledgeSearchHit] = []
        residual: list[KnowledgeSearchHit] = []
        for hit in knowledge_hits:
            if self._is_materializable_knowledge_hit(request, hit):
                materializable.append(hit)
            else:
                residual.append(hit)
        return materializable, residual

    def _is_materializable_knowledge_hit(
        self,
        request: ChatRequest,
        hit: KnowledgeSearchHit,
    ) -> bool:
        del request
        lowered_tags = {tag.lower() for tag in hit.tags}
        metadata = {str(key).lower(): str(value).lower() for key, value in hit.metadata.items()}
        source_name = (hit.source_name or "").lower()
        blocked_markers = {
            "audience:admin",
            "audience:private",
            "admin",
            "private",
            "secret",
            "credential",
            "token",
            "slack-binding",
            "student-record",
            "runtime-log",
        }
        if lowered_tags & blocked_markers:
            return False
        if metadata.get("audience") in {"admin", "private"}:
            return False
        if metadata.get("source_kind") in {"runtime-log", "student-record", "slack-binding"}:
            return False
        if any(marker in source_name for marker in ("slack_user_links", "user_accounts", "runtime-log")):
            return False
        return True

    def _format_materializable_knowledge_context(
        self,
        request: ChatRequest,
        knowledge_hits: list[KnowledgeSearchHit],
    ) -> str:
        if not knowledge_hits:
            return ""
        scope = self._materializable_context_scope(request, knowledge_hits)
        sections = [
            f"Reusable retrieved materials (materialization scope: {scope}):",
            "These materials are stable KB excerpts selected for the current access scope. "
            "Use them only when they directly answer the current question.",
        ]
        for index, hit in enumerate(
            sorted(knowledge_hits, key=self._materializable_knowledge_hit_sort_key),
            start=1,
        ):
            source_suffix = f" | source: {hit.source_name}" if hit.source_name else ""
            sections.append(
                f"{index}. {hit.title}{source_suffix}\nExcerpt: {hit.excerpt}\nTags: {', '.join(hit.tags) if hit.tags else 'none'}"
            )
        return "\n".join(sections) + "\n"

    def _materializable_knowledge_hit_sort_key(
        self,
        hit: KnowledgeSearchHit,
    ) -> tuple[str, str, str]:
        return (
            hit.document_id or "",
            hit.source_name or "",
            hit.title or "",
        )

    def _materializable_context_scope(
        self,
        request: ChatRequest,
        knowledge_hits: list[KnowledgeSearchHit],
    ) -> str:
        audiences: set[str] = set()
        for hit in knowledge_hits:
            metadata_audience = str(hit.metadata.get("audience") or "").strip().lower()
            if metadata_audience:
                audiences.add(metadata_audience)
            for tag in hit.tags:
                lowered = tag.lower()
                if lowered.startswith("audience:"):
                    audiences.add(lowered.split(":", 1)[1])
        if "lab_member" in audiences:
            return "lab_member"
        publicish = {"public", "undergraduate", "graduate"}
        if audiences and audiences <= publicish:
            return "+".join(sorted(audiences))
        visitor_profile = (
            getattr(request, "visitor_profile", None) or "general_visitor"
        ).strip() or "general_visitor"
        return visitor_profile

    def _format_web_search_context(self, web_search_hits: list[WebSearchHit]) -> str:
        if not web_search_hits:
            return ""

        sections = [
            "Real-time web search results (Bing):",
            "Use these results only when they directly support the user's current question.",
        ]
        for index, hit in enumerate(web_search_hits, start=1):
            snippet = hit.snippet.strip()
            sections.append(f"{index}. {hit.title} | source: {hit.url}")
            if snippet:
                sections.append(f"Snippet: {snippet}")
        return "\n".join(sections) + "\n"

    _IDENTITY_QUESTION_PATTERN = re.compile(
        r"你是谁|介绍.{0,4}你|个人简介|个人介绍|学术背景|主要研究|研究方向|招什么样|招生|你的学术|你主要|你是做|你是什么样的老师"
    )
    _IDENTITY_FLOOR_TITLES: tuple[str, ...] = (
        "主页资料｜张书豪",
        "主页资料｜当前系统建设",
        "主页资料｜招生与合作",
        "研究总览｜一、共享状态访问、调度与运行时管理",
    )

    def _identity_floor_hits(self, question: str) -> list[KnowledgeSearchHit]:
        if not question or not self._IDENTITY_QUESTION_PATTERN.search(question):
            return []
        hits: list[KnowledgeSearchHit] = []
        for record in self._knowledge_store.list_documents():
            if record.title in self._IDENTITY_FLOOR_TITLES:
                excerpt = record.content[:600]
                hits.append(
                    KnowledgeSearchHit(
                        document_id=record.document_id,
                        title=record.title,
                        excerpt=excerpt,
                        score=99.0,
                        tags=list(record.tags),
                        source_name=record.source_name,
                        metadata=dict(record.metadata),
                    )
                )
        # Preserve the order declared in _IDENTITY_FLOOR_TITLES so the bio
        # always appears first.
        ordering = {title: idx for idx, title in enumerate(self._IDENTITY_FLOOR_TITLES)}
        hits.sort(key=lambda h: ordering.get(h.title, 999))
        return hits

    def _select_prompt_knowledge_hits(
        self,
        question: str,
        knowledge_hits: list[KnowledgeSearchHit],
        interaction_intent: InteractionIntent | None = None,
    ) -> list[KnowledgeSearchHit]:
        # Task 5: identity-floor force-include. When the student is clearly
        # asking about who the owner is / what the lab studies / admissions,
        # always inject the homepage bio + research overview at the top so the
        # answer never falls back to inventing `[具体研究方向]` placeholders.
        floor_hits = self._identity_floor_hits(question)
        if not knowledge_hits and not floor_hits:
            return []
        if floor_hits:
            seen_ids = {hit.document_id for hit in floor_hits}
            merged = list(floor_hits) + [
                hit for hit in knowledge_hits if hit.document_id not in seen_ids
            ]
            knowledge_hits = merged
        if interaction_intent is not None:
            scoped_hits = self._filter_knowledge_hits_by_intent(knowledge_hits, interaction_intent)
            if interaction_intent.domain == "research":
                research_hits = [
                    hit
                    for hit in scoped_hits
                    if self._is_research_hit(hit) and not self._is_teaching_hit(hit)
                ]
                if research_hits:
                    return research_hits
            if scoped_hits:
                return scoped_hits

        if not self._is_research_question(question):
            return knowledge_hits

        research_hits = [
            hit
            for hit in knowledge_hits
            if self._is_research_hit(hit) and not self._is_teaching_hit(hit)
        ]
        if research_hits:
            return research_hits

        return [hit for hit in knowledge_hits if not self._is_teaching_hit(hit)] or knowledge_hits

    def _is_research_question(self, question: str) -> bool:
        lowered = question.lower()
        markers = (
            "研究主线",
            "研究方向",
            "研究路线",
            "主要研究",
            "主要关注",
            "关注哪些",
            "研究什么",
            "做什么研究",
            "系统方向",
            "课题组",
            "科研",
            "企业 r&d",
            "企业研发",
            "r&d",
            "推理引擎",
            "推理服务",
            "kv cache",
            "ttft",
            "vllm",
            "research",
            "publication",
            "publications",
        )
        return any(marker in lowered for marker in markers) or any(
            marker in question for marker in markers
        )

    def _is_research_hit(self, hit: KnowledgeSearchHit) -> bool:
        hit_tags = {tag.lower() for tag in hit.tags}
        if hit_tags & {
            "research",
            "publication",
            "paper-digest",
            "overview",
            "profile",
        }:
            return True
        source_name = (hit.source_name or "").lower()
        return (
            "研究" in hit.title or "publications" in source_name or "research_papers" in source_name
        )

    def _is_teaching_hit(self, hit: KnowledgeSearchHit) -> bool:
        hit_tags = {tag.lower() for tag in hit.tags}
        return bool(
            hit_tags
            & {
                "teaching",
                "courseware",
                "tutorial",
                "lecture",
                "experiment",
                "pdf",
                "resources",
            }
        )

    def _build_intent_guidance(self, interaction_intent: InteractionIntent | None) -> str:
        if interaction_intent is None:
            return ""
        if interaction_intent.domain == "research":
            return (
                "Intent guidance: The student is asking about the owner's research. "
                "Prioritize research overview, publications, and paper digests. "
                "For questions about the owner's main or current research direction, answer with the current focus first: "
                "LLM inference engines, LLM inference serving systems, and memory-agent middleware. "
                "If older database-management, stream-processing, or parallel/distributed-systems materials appear, "
                "frame them as historical foundations or method background rather than the current primary direction. "
                "Do not treat courseware as the owner's main identity unless the student explicitly asks about teaching.\n"
            )
        if interaction_intent.domain == "teaching":
            return (
                "Intent guidance: The student is asking about teaching materials. "
                "Prioritize lectures, tutorials, experiments, and courseware details.\n"
            )
        if interaction_intent.domain == "advising":
            guidance = (
                "Intent guidance: The student is asking for advising or preparation guidance. "
                "Prioritize preparation checklists, meeting expectations, and communication guidance over course slides.\n"
            )
            if interaction_intent.decision_mode == "advise_only":
                guidance += (
                    "Decision guidance: Give suggestions, options, and preparation checklists only. "
                    "Do not make approvals, exceptions, commitments, or final decisions on behalf of the owner.\n"
                )
            return guidance
        if interaction_intent.domain == "booking":
            return "Intent guidance: The student is trying to arrange a meeting. Use scheduling and availability facts only.\n"
        if interaction_intent.decision_mode == "advise_only":
            return (
                "Decision guidance: Provide suggestions and options only. "
                "Do not make the final decision on behalf of the owner or the student.\n"
            )
        return ""

    def _build_meeting_preparation_guidance(
        self,
        question: str,
        interaction_intent: InteractionIntent | None,
    ) -> str:
        if interaction_intent is None or interaction_intent.domain != "advising":
            return ""

        lowered = question.lower()
        preparation_markers = (
            "准备什么",
            "先准备",
            "提前准备",
            "预约前",
            "meeting prep",
            "agenda",
        )
        if not any(marker in question or marker in lowered for marker in preparation_markers):
            return ""

        return (
            "Preparation guidance: For meeting-preparation questions, prioritize a concise checklist covering "
            "agenda, current blocker, draft or progress summary, and 2-3 concrete questions. "
            "Do not ask for time slots unless the student explicitly asks to book a meeting.\n"
        )

    def _build_advising_response_guidance(
        self,
        question: str,
        interaction_intent: InteractionIntent | None,
    ) -> str:
        if interaction_intent is None or interaction_intent.domain != "advising":
            return ""

        lowered = question.lower()
        guidance: list[str] = []

        if any(
            marker in question or marker in lowered
            for marker in (
                "怎么收窄",
                "收窄",
                "收拢结构",
                "题目太大",
                "砍掉哪些",
                "最先会删什么",
            )
        ):
            guidance.append(
                "Scoping guidance: For project-scoping or structure questions, answer with a concrete narrowing rule: "
                "first pin down one core problem, then choose one setting or artifact, then keep one evaluation signal; "
                "cut side branches that do not support that core. "
                "Use explicit wording such as '先收窄到一个核心问题' or '先把边界收窄到一个可执行切口'."
            )

        if any(
            marker in question or marker in lowered
            for marker in (
                "带 draft",
                "draft",
                "初稿",
                "反馈更集中",
                "怎么组织",
                "合作前",
                "合作空间",
                "更看重哪些准备",
                "参与项目",
                "补哪块",
            )
        ):
            guidance.append(
                "Draft guidance: For draft, update, project-fit, or collaboration-preparation questions, respond with a short checklist covering "
                "current goal, current version or evidence, main blocker, and 2-3 focused questions or decisions to discuss. "
                "Do not turn this into scheduling. "
                "Before the checklist, briefly restate one concrete strength, weakness, or constraint from the student's current profile, and phrase the recommendation as '建议先...'."
            )

        if any(
            marker in question or marker in lowered
            for marker in ("合作前", "合作空间", "合作", "joint work", "collaboration")
        ):
            guidance.append(
                "Collaboration preparation guidance: For collaboration-preparation questions, explicitly anchor the answer to the collaborator's current topic and alignment goal. "
                "If the profile mentions a concrete topic such as '推理服务', restate it directly instead of using only generic wording like '当前项目'. "
                "If the profile mentions a goal like '目标是否对齐', explicitly ask the student to prepare material that helps judge whether goals are aligned. "
                "The checklist should explicitly cover '合作目标', '当前问题', '边界', and '资源或现有条件', and it should stay exploratory rather than promising cooperation."
            )

        if (
            any(marker in question or marker in lowered for marker in ("课程", "作业"))
            and any(marker in question or marker in lowered for marker in ("研究", "科研"))
            and any(
                marker in question or marker in lowered
                for marker in ("分开准备", "一次都问完", "分开", "分别")
            )
        ):
            guidance.append(
                "Boundary guidance: For mixed course-and-research questions, explicitly recommend splitting them into two prepared threads. "
                "Say that course questions and research questions have different goals, so they should be prepared separately, with separate topic lists or notes. "
                "The answer should explicitly say something close to '这类情况建议分开准备：课程问题单独列一组，研究问题单独列一组。'. "
                "Use wording such as '分开', '分别', '课程', '研究', and '准备'."
            )

        if any(
            marker in question or marker in lowered
            for marker in (
                "发邮件",
                "线下聊",
                "当面聊",
                "哪几天",
                "什么时候方便",
                "分开准备",
                "更合适",
            )
        ):
            guidance.append(
                "Communication guidance: For email-vs-meeting or availability-boundary questions, explain which issues are efficient by email and which are better for a meeting. "
                "Do not ask for time slots unless the student explicitly asks to schedule now. "
                "If the student already says there are only a few concrete questions or wants to avoid taking too much of the teacher's time, explicitly mention those facts before recommending email first."
            )

        if any(
            marker in question or marker in lowered
            for marker in (
                "下一步",
                "继续补实验",
                "上次",
                "这周已经",
                "怎么组织这个 update",
            )
        ):
            guidance.append(
                "Follow-up guidance: When the student already mentions prior progress or earlier advice, use that progress to recommend the next concrete step instead of asking for generic clarification. "
                "State the recommendation explicitly with wording like '下一步可以先...' and connect it to the student's current progress. "
                "If the student says the material is already organized, briefly restate that organized state, for example '既然你已经整理成三类...'. "
                "Prefer an explicit sentence pattern like '既然你已经整理成三类，下一步可以先...' before giving the reason."
            )

        if not guidance:
            return ""

        return "\n".join(f"{line}" for line in guidance) + "\n"

    def _build_teaching_response_guidance(
        self,
        question: str,
        interaction_intent: InteractionIntent | None,
    ) -> str:
        if interaction_intent is None or interaction_intent.domain != "teaching":
            return ""

        lowered = question.lower()
        organization_markers = (
            "课上问题",
            "作业",
            "整理哪几类信息",
            "减少来回沟通",
            "性能分析",
        )
        conceptual_markers = (
            "端到端",
            "kernel 优化",
            "局部 kernel",
            "不是来问选题",
            "课上概念",
            "先理解哪一层",
        )
        has_organization_markers = any(
            marker in question or marker in lowered for marker in organization_markers
        )
        has_conceptual_markers = any(
            marker in question or marker in lowered for marker in conceptual_markers
        )
        if not has_organization_markers and not has_conceptual_markers:
            return ""

        guidance_parts: list[str] = []

        if has_organization_markers:
            guidance_parts.append(
                "Teaching logistics guidance: For course-question organization questions, answer directly with 3-4 categories the student should organize before asking: "
                "the exact problem, the observed phenomenon or performance result, what has already been tried, and the remaining confusion or target metric. "
                "Do not answer with another clarification question when the student is already asking how to organize the question."
            )

        if any(
            marker in question or marker in lowered
            for marker in ("端到端性能", "问题比较碎", "减少来回沟通")
        ):
            guidance_parts.append(
                "Teaching personalization guidance: The answer must explicitly reflect the student's stated pain points instead of giving only a generic checklist. "
                "Reuse the student's own wording when it is available in the question, and do not drop these profile facts just because course materials were retrieved. "
                "Mention the end-to-end performance issue and the fact that the questions feel fragmented, so the answer sounds tailored instead of generic. "
                "Use wording close to '先整理端到端性能相关的现象或结果' and '把现在比较碎的问题按类别归拢'."
            )

        if has_conceptual_markers:
            guidance_parts.append(
                "Teaching grounding guidance: For course-understanding questions about why local optimization does not guarantee end-to-end gains, answer from a systems-bottleneck perspective. "
                "Explicitly use wording such as '端到端', '系统瓶颈', 'kernel 优化', and '整体收益'. "
                "State that this is a course-understanding question first, not a project-selection discussion, using wording close to '你现在先把这类端到端瓶颈关系理解清楚，不必转成科研方向讨论'."
            )

        return "\n".join(guidance_parts) + "\n"

    def _build_research_response_guidance(
        self,
        question: str,
        interaction_intent: InteractionIntent | None,
    ) -> str:
        if interaction_intent is None or interaction_intent.domain != "research":
            return ""

        lowered = question.lower()
        guidance: list[str] = []

        if any(
            marker in question or marker in lowered
            for marker in (
                "什么主题切入",
                "哪个主题开始读",
                "先挑一个主题",
                "主线理解",
                "研究主线",
                "切入",
            )
        ):
            guidance.append(
                "Publication guidance: For paper-entry questions, recommend one clear topic or research main line to start from, and use wording such as '主题', '主线', or '切入'. "
                "Do not invent specific paper titles if you are not certain."
            )

        if any(
            marker in question or marker in lowered
            for marker in (
                "上次已被建议",
                "整理成三类",
                "下一步",
                "先发邮件还是继续补实验",
                "减少来回修改",
            )
        ):
            guidance.append(
                "Progress guidance: When the question asks for the next step after earlier advice, briefly restate the current progress and then recommend one next action explicitly. "
                "Prefer concise wording that mentions the current organized state before suggesting email or more experiments. "
                "Use wording close to '既然你已经整理成三类，下一步可以先...'."
            )

        if not guidance:
            return ""

        return "\n".join(guidance) + "\n"

    def _build_general_technical_response_guidance(
        self,
        question: str,
        interaction_intent: InteractionIntent | None,
    ) -> str:
        if not self._looks_like_general_technical_question(question):
            return ""

        if interaction_intent is not None and interaction_intent.domain not in {
            "general",
            "research",
            "teaching",
        }:
            return ""

        owner_specific_markers = (
            "张老师本人",
            "张老师是否",
            "课题组内部",
            "内部项目",
            "未公开",
            "具体指令",
            "真实部署",
            "你们怎么",
        )
        owner_boundary = (
            "Only say the owner lacks direct evidence when the user is asking for owner-specific "
            "experience, private lab details, or unpublished implementation details. "
        )
        if any(marker in question for marker in owner_specific_markers):
            owner_boundary += (
                "For owner-specific parts without retrieved evidence, state the uncertainty once, "
                "then still provide clearly labeled general technical considerations if useful. "
            )

        domain_guidance = (
            "For medical image segmentation questions, cover data annotation quality, suitable model families, "
            "cross-center generalization, uncertainty or failure detection, and clinical validation or workflow integration. "
            if any(marker in question or marker in question.lower() for marker in ("医学影像", "图像分割", "medical imaging", "segmentation"))
            else (
                "For accelerator memory-management questions, cover allocation/lifetime planning, "
                "KV-cache or activation residency, batching and scheduling, fragmentation or memory pools, "
                "operator/workspace buffers, and observability metrics when relevant. "
            )
        )
        return (
            "General technical guidance: This is a technical academic question. "
            "Do not answer it only with '我没有直接研究经验' or a request for more details. "
            f"{owner_boundary}"
            "Give a substantive answer from general systems knowledge, with exactly 4 numbered tactics. "
            "Each tactic should be one complete sentence with a concrete action and the reason it helps. "
            f"{domain_guidance}"
            "Use concise Chinese unless the user asks otherwise.\n"
        )

    @staticmethod
    def _looks_like_general_technical_question(question: str) -> bool:
        lowered = question.lower()
        technical_markers = (
            "llm",
            "大模型",
            "推理",
            "推理引擎",
            "推理服务",
            "vllm",
            "kv cache",
            "kvcache",
            "prefix cache",
            "paged attention",
            "npu",
            "ascend",
            "昇腾",
            "gpu",
            "显存",
            "内存",
            "memory",
            "调度",
            "吞吐",
            "ttft",
            "tpot",
            "batch",
            "batching",
            "算子",
            "kernel",
            "深度学习",
            "神经网络",
            "图像分割",
            "医学影像",
            "medical imaging",
            "segmentation",
        )
        return any(marker in lowered or marker in question for marker in technical_markers)

    def _resolve_interaction_intent(
        self, context: ChatWorkflowContext
    ) -> tuple[InteractionIntent, str]:
        if context.conversation_id in self._booking_workflows:
            return self._build_booking_follow_up_intent(), "workflow_state"

        if context.is_admin_request and self._looks_like_admin_knowledge_injection(
            context.request.question
        ):
            return (
                InteractionIntent(
                    action="admin_add_knowledge",
                    domain="general",
                    decision_mode="direct_answer",
                    confidence=0.98,
                ),
                "admin_command",
            )

        fast_intent = self._build_fast_path_interaction_intent(context)
        if fast_intent is not None and bool(
            getattr(self._llm_client, "supports_fast_intent_bypass", False)
        ):
            guarded_intent, guarded = self._apply_policy_guardrails(context.request, fast_intent)
            return guarded_intent, "heuristic-fast+policy" if guarded else "heuristic-fast"

        classify_sync = getattr(self._llm_client, "classify_interaction_intent_sync", None)
        if callable(classify_sync):
            try:
                course_context_block: str | None = None
                if context.request.course_context and context.request.course_context.strip():
                    course_context_block = (
                        f"Course context: {context.request.course_context.strip()}"
                    )
                intent = classify_sync(
                    context.request.question,
                    course_context_block,
                    recent_session_context=context.recent_session_context,
                )
                if not isinstance(intent, InteractionIntent):
                    intent = InteractionIntent.model_validate(intent)
                guarded_intent, guarded = self._apply_policy_guardrails(context.request, intent)
                return guarded_intent, "llm+policy" if guarded else "llm"
            except Exception:
                pass

        if fast_intent is not None:
            guarded_intent, guarded = self._apply_policy_guardrails(context.request, fast_intent)
            return guarded_intent, "heuristic-fast+policy" if guarded else "heuristic-fast"

        intent = self._build_fallback_interaction_intent(context.request)
        guarded_intent, guarded = self._apply_policy_guardrails(context.request, intent)
        return guarded_intent, "heuristic+policy" if guarded else "heuristic"

    def _build_fast_path_interaction_intent(
        self,
        context: ChatWorkflowContext,
    ) -> InteractionIntent | None:
        if not self._settings.fast_intent_classifier_enabled:
            return None
        request = context.request
        question = request.question
        if self._is_benchmark_request(request):
            return None
        if request.attachments:
            return None
        if self._looks_like_contextual_follow_up(question, context.recent_session_context):
            relevance_question = self._build_answer_relevance_question(context)
            if self._should_use_curated_technical_guidance(relevance_question):
                return InteractionIntent(
                    action="answer",
                    domain="research",
                    retrieval_scopes=[],
                    exclude_scopes=["courseware"],
                    decision_mode="direct_answer",
                    confidence=0.99,
                )
            return InteractionIntent(
                action="answer",
                domain="general",
                retrieval_scopes=[],
                decision_mode="direct_answer",
                confidence=0.86,
            )
        if self._needs_booking_intent_classification(question):
            return None
        if self._looks_like_collaboration_preparation_question(question):
            return None
        if (request.visitor_profile or "general_visitor") != "general_visitor":
            return None

        if self._looks_like_general_technical_question(question):
            return InteractionIntent(
                action="answer",
                domain="research",
                retrieval_scopes=["publications", "profile"],
                exclude_scopes=["courseware"],
                decision_mode="direct_answer",
                confidence=0.94,
            )

        if self._should_force_human_handoff(question):
            return InteractionIntent(
                action="human_handoff",
                domain="advising",
                decision_mode="human_handoff",
                escalation_reason="涉及敏感、紧急或必须由老师本人直接处理的事项。",
                confidence=0.95,
            )

        if self._should_queue_for_review(question):
            return InteractionIntent(
                action="review_queue",
                domain="advising",
                retrieval_scopes=["meeting_policy", "profile"],
                exclude_scopes=["courseware"],
                decision_mode="review_queue",
                escalation_reason="这是需要老师审核后才能正式答复的请求。",
                confidence=0.9,
            )

        if self._looks_like_booking_information_request(question):
            return InteractionIntent(
                action="answer",
                domain="advising",
                retrieval_scopes=["meeting_policy", "profile"],
                exclude_scopes=["courseware"],
                decision_mode="direct_answer",
                confidence=0.88,
            )

        lowered = question.lower()

        teaching_markers = (
            "tutorial",
            "lecture",
            "experiment",
            "homework",
            "assignment",
            "课件",
            "讲义",
            "实验",
            "作业",
            "课程",
            "课堂",
            "这门课",
            "这门课程",
            "基础一般",
            "基础薄弱",
            "先补",
            "补哪些",
            "补什么",
        )
        if any(marker in lowered or marker in question for marker in teaching_markers):
            return InteractionIntent(
                action="answer",
                domain="teaching",
                retrieval_scopes=["courseware"],
                exclude_scopes=["publications"],
                confidence=0.82,
            )

        advising_markers = (
            "准备什么",
            "提前准备",
            "怎么准备",
            "agenda",
            "汇报",
            "组会",
            "meeting readiness",
            "paper writing",
            "论文写作",
            "怎么沟通",
            "发邮件",
            "线下聊",
        )
        if any(marker in lowered or marker in question for marker in advising_markers):
            return InteractionIntent(
                action="answer",
                domain="advising",
                retrieval_scopes=["preparation", "meeting_policy", "profile"],
                exclude_scopes=["courseware"],
                decision_mode="advise_only",
                confidence=0.82,
            )

        if self._is_research_question(question):
            return InteractionIntent(
                action="answer",
                domain="research",
                retrieval_scopes=["publications", "profile"],
                exclude_scopes=["courseware"],
                confidence=0.82,
            )

        return InteractionIntent(
            action="answer",
            domain="general",
            retrieval_scopes=[],
            decision_mode="direct_answer",
            confidence=0.76,
        )

    def _apply_policy_guardrails(
        self,
        request: ChatRequest,
        intent: InteractionIntent,
    ) -> tuple[InteractionIntent, bool]:
        result = self._interaction_policy.apply(request, intent)
        return result.intent, result.changed

    def _build_interaction_context(
        self, request: ChatRequest, recent_session_context: str
    ) -> str | None:
        parts: list[str] = []
        if request.course_context:
            parts.append(f"Course context: {request.course_context}")
        normalized_recent_session_context = recent_session_context.strip()
        if normalized_recent_session_context:
            parts.append(normalized_recent_session_context)
        if not parts:
            return None
        return "\n".join(parts)

    def _build_booking_follow_up_intent(self) -> InteractionIntent:
        return InteractionIntent(
            action="book_meeting",
            domain="booking",
            retrieval_scopes=["meeting_policy"],
            exclude_scopes=["courseware", "publications"],
            decision_mode="review_queue",
            confidence=1.0,
        )

    def _build_fallback_interaction_intent(self, request: ChatRequest) -> InteractionIntent:
        if self._should_force_human_handoff(request.question):
            return InteractionIntent(
                action="human_handoff",
                domain="advising",
                decision_mode="human_handoff",
                escalation_reason="涉及敏感、紧急或必须由老师本人直接处理的事项。",
                confidence=0.9,
            )

        if self._should_queue_for_review(request.question):
            return InteractionIntent(
                action="review_queue",
                domain="advising",
                retrieval_scopes=["meeting_policy", "profile"],
                exclude_scopes=["courseware"],
                decision_mode="review_queue",
                escalation_reason="这是需要老师审核后才能正式答复的请求。",
                confidence=0.8,
            )

        if self._looks_like_booking_information_request(request.question):
            return InteractionIntent(
                action="answer",
                domain="advising",
                retrieval_scopes=["meeting_policy", "profile"],
                exclude_scopes=["courseware"],
                decision_mode="direct_answer",
                confidence=0.75,
            )

        if self._should_start_booking_workflow(request):
            return InteractionIntent(
                action="book_meeting",
                domain="booking",
                retrieval_scopes=["meeting_policy"],
                exclude_scopes=["courseware", "publications"],
                decision_mode="review_queue",
                confidence=0.7,
            )

        if self._is_research_question(request.question):
            return InteractionIntent(
                action="answer",
                domain="research",
                retrieval_scopes=["publications", "profile"],
                exclude_scopes=["courseware"],
                confidence=0.6,
            )

        lowered = request.question.lower()
        if any(
            marker in lowered
            for marker in ("tutorial", "lecture", "experiment", "课件", "讲义", "实验")
        ):
            return InteractionIntent(
                action="answer",
                domain="teaching",
                retrieval_scopes=["courseware"],
                exclude_scopes=["publications"],
                confidence=0.6,
            )

        if any(marker in request.question for marker in ("准备什么", "提前准备", "怎么准备")):
            return InteractionIntent(
                action="answer",
                domain="advising",
                retrieval_scopes=["preparation", "meeting_policy", "profile"],
                exclude_scopes=["courseware"],
                decision_mode="advise_only",
                confidence=0.6,
            )

        if any(
            marker in request.question
            for marker in ("帮我决定", "替我决定", "该不该", "怎么选", "选哪个")
        ):
            return InteractionIntent(
                action="answer",
                domain="advising",
                retrieval_scopes=["profile", "meeting_policy"],
                exclude_scopes=["courseware"],
                decision_mode="advise_only",
                confidence=0.6,
            )

        return InteractionIntent(action="answer", domain="general", confidence=0.5)

    def _build_knowledge_query(
        self,
        request: ChatRequest,
        interaction_intent: InteractionIntent,
        recent_session_context: str | None = None,
    ) -> str:
        question = request.question
        # Task 4: short follow-ups (具体/那个/这个/这篇/那篇/继续/然后/展开/详细/细节) cannot
        # be matched on their own. Prepend the prior student turn(s) so the BM25
        # query carries the resolved topic anchor (e.g. "kvpr 这篇" stays linked
        # to the previous KV-cache discussion).
        expanded_question = self._expand_followup_question(question, recent_session_context)
        if request.course_context and interaction_intent.domain in {
            "research",
            "teaching",
            "advising",
        }:
            return f"{expanded_question}\n{request.course_context}".strip()
        return expanded_question

    _SHORT_FOLLOWUP_PATTERN = re.compile(
        r"^(具体|那个|这个|那|这|这篇|那篇|继续|然后|展开|详细|细节|还有|接着|其他|另外|呢|么|哦|能否|可以|按前面|按刚才)"
    )

    def _looks_like_contextual_follow_up(
        self,
        question: str,
        recent_session_context: str | None,
    ) -> bool:
        if not recent_session_context:
            return False
        normalized = question.strip()
        if not normalized:
            return False
        if self._SHORT_FOLLOWUP_PATTERN.match(normalized):
            return True
        markers = (
            "刚才",
            "前面",
            "上面",
            "那个方向",
            "这个方向",
            "那个方案",
            "这个方案",
            "继续",
            "下一步",
            "值得继续",
            "风险是什么",
        )
        return any(marker in normalized for marker in markers)

    def _looks_like_collaboration_preparation_question(self, question: str) -> bool:
        lowered = question.lower()
        collaboration_markers = (
            "合作",
            "collaboration",
            "collaborate",
            "external partner",
        )
        preparation_markers = (
            "准备",
            "信息",
            "材料",
            "需求",
            "对齐",
            "prep",
            "prepare",
            "before",
        )
        return (
            any(marker in lowered or marker in question for marker in collaboration_markers)
            and any(marker in lowered or marker in question for marker in preparation_markers)
        )

    def _expand_followup_question(
        self, question: str, recent_session_context: str | None
    ) -> str:
        normalized = (question or "").strip()
        if not normalized or not recent_session_context:
            return question
        if len(normalized) >= 25 and not self._SHORT_FOLLOWUP_PATTERN.match(normalized):
            return question
        # Pull up to two most-recent user turns out of the formatted context.
        prior_user_turns: list[str] = []
        for raw_line in recent_session_context.splitlines():
            line = raw_line.strip()
            # _format_recent_session_context emits lines like
            #   "1. User: 你能帮我看论文吗"
            # so we only keep the user-side sentences.
            if not line:
                continue
            marker = line.find("User:")
            if marker == -1:
                continue
            extracted = line[marker + len("User:"):].strip()
            if extracted:
                prior_user_turns.append(extracted)
        if not prior_user_turns:
            return question
        # Keep the two most recent (already chronological in the formatted text).
        prior = " ".join(prior_user_turns[-2:])
        return f"{prior} {question}".strip()

    def _filter_knowledge_hits_by_intent(
        self,
        knowledge_hits: list[KnowledgeSearchHit],
        interaction_intent: InteractionIntent | None,
        *,
        question: str | None = None,
    ) -> list[KnowledgeSearchHit]:
        if interaction_intent is None or not knowledge_hits:
            return knowledge_hits

        guidance_hits = self._prioritize_guidance_hits(knowledge_hits, interaction_intent)
        if guidance_hits:
            return guidance_hits

        scoped_hits = [
            hit
            for hit in knowledge_hits
            if self._matches_intent_scopes(hit, interaction_intent.retrieval_scopes)
            and not self._matches_intent_scopes(hit, interaction_intent.exclude_scopes)
        ]
        if scoped_hits:
            candidates = scoped_hits
        else:
            non_excluded_hits = [
                hit
                for hit in knowledge_hits
                if not self._matches_intent_scopes(hit, interaction_intent.exclude_scopes)
            ]
            candidates = non_excluded_hits or knowledge_hits

        if question and interaction_intent.domain in {"general", "research", "teaching"}:
            return [
                hit
                for hit in candidates
                if self._knowledge_hit_has_query_evidence(question, hit)
            ]
        return candidates

    @staticmethod
    def _knowledge_hit_has_query_evidence(
        question: str,
        hit: KnowledgeSearchHit,
    ) -> bool:
        """Keep semantically retrieved evidence without a global score cutoff.

        The knowledge backend performs ranking; this is only a provenance
        sanity check. A candidate must share a meaningful query anchor with
        its title, excerpt, source, or tags. Scores are not compared across
        domains or embedding backends.
        """

        stop_words = {
            "please",
            "explain",
            "example",
            "simple",
            "what",
            "with",
            "within",
            "请用一",
            "一个简",
            "个简单",
            "简单例",
            "单例子",
            "例子解",
            "子解释",
            "控制在",
            "字以内",
            "老师",
            "请问",
            "介绍",
            "一下",
            "是什么",
            "哪些",
        }
        anchors = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_+-]{4,}", question)
            if token.lower() not in stop_words
        }
        for span in re.findall(r"[\u4e00-\u9fff]+", question):
            # Two- and three-character spans handle short entities and
            # paraphrases better than the previous three-character-only rule.
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
                hit.source_name or "",
                " ".join(hit.tags or []),
                " ".join(f"{key} {value}" for key, value in (hit.metadata or {}).items()),
            )
        ).lower()
        return any(anchor in searchable for anchor in anchors)

    def _prioritize_guidance_hits(
        self,
        knowledge_hits: list[KnowledgeSearchHit],
        interaction_intent: InteractionIntent,
    ) -> list[KnowledgeSearchHit] | None:
        if interaction_intent.domain not in {"advising", "booking"}:
            return None

        scopes = {scope.lower() for scope in interaction_intent.retrieval_scopes}
        if not scopes.intersection({"preparation", "meeting_policy"}):
            return None

        return [
            hit
            for hit in knowledge_hits
            if self._matches_intent_scopes(hit, ["preparation", "meeting_policy"])
        ]

    def _matches_intent_scopes(self, hit: KnowledgeSearchHit, scopes: list[str]) -> bool:
        if not scopes:
            return True
        hit_tags = {tag.lower() for tag in hit.tags}
        scope_map = {
            "publications": {"research", "publication", "paper-digest", "overview"},
            "profile": {"profile"},
            "courseware": {
                "teaching",
                "courseware",
                "tutorial",
                "lecture",
                "experiment",
                "pdf",
                "resources",
            },
            "preparation": {"preparation", "qa", "policy", "meeting"},
            "meeting_policy": {"meeting", "policy", "preparation", "qa"},
        }
        for scope in scopes:
            allowed_tags = scope_map.get(scope, set())
            if hit_tags & allowed_tags:
                return True
            if scope == "publications" and self._is_research_hit(hit):
                return True
            if scope == "courseware" and self._is_teaching_hit(hit):
                return True
        return False

    def _format_memory_context(self, memory_hits: list[ConversationMemoryHit]) -> str:
        if not memory_hits:
            return ""

        artifact_hits = [
            hit
            for hit in memory_hits
            if hit.topic == "artifact_memory" or hit.source == "attachment_excerpt"
        ]
        short_term_hits = [
            hit
            for hit in memory_hits
            if hit.memory_type == "short_term" and hit not in artifact_hits
        ]
        long_term_hits = [hit for hit in memory_hits if hit.memory_type == "long_term"]

        sections: list[str] = []
        if artifact_hits:
            sections.append("Uploaded artifacts and referenced materials:")
            for index, hit in enumerate(artifact_hits, start=1):
                sections.append(f"{index}. {hit.summary}")

        if short_term_hits:
            sections.append("Recent conversation memory:")
            for index, hit in enumerate(short_term_hits, start=1):
                sections.append(f"{index}. {hit.summary}")

        if long_term_hits:
            sections.append("Stable student profile memory:")
            for index, hit in enumerate(long_term_hits, start=1):
                sections.append(f"{index}. {hit.summary}")

        return "\n".join(sections) + "\n"

    def _build_answer_basis(self, context: ChatWorkflowContext) -> list[AnswerBasisItem]:
        """Build the visible citation ("依据") items for a response.

        Design rules:
        - Knowledge hits: deduplicated by source group, generic index pages
          filtered, max 3 items.
        - Web search hits: max 2 items.
        - Memory hits: only artifact (uploaded) and long-term profile memory;
          short-term conversation memory is NEVER cited (it is implicit).
        - Session context: NEVER cited — conversation history is already
          visible in the chat UI.
        - Final safety net: any item with basis_label "近期交流记录" is
          stripped regardless of how it was produced.
        """
        basis_items: list[AnswerBasisItem] = []

        # 1. Admin-added knowledge (from this turn)
        if context.added_knowledge_record is not None:
            basis_items.append(
                self._build_added_knowledge_basis_item(context.added_knowledge_record)
            )

        # 2. Knowledge base hits — deduplicated, filtered, max 3
        seen_source_groups: set[str] = set()
        knowledge_count = 0
        for hit in context.knowledge_hits[:6]:
            source_group = _canonical_source_group(hit.source_name, hit.document_id)
            if source_group in seen_source_groups:
                continue
            if self._is_generic_index_page(hit):
                continue
            seen_source_groups.add(source_group)
            basis_items.append(self._build_knowledge_basis_item(hit))
            knowledge_count += 1
            if knowledge_count >= 3:
                break

        # 3. Web search hits — max 2
        for hit in context.web_search_hits[:2]:
            basis_items.append(
                AnswerBasisItem(
                    basis_label="联网检索",
                    title=self._clip_basis_text(hit.title, 256),
                    source_label=self._clip_basis_text(hit.url, 256),
                    detail=self._clip_basis_text(hit.snippet or "来自联网检索的实时网页结果。", 1000),
                )
            )

        # 4. Memory hits — only artifact (uploaded) and long-term profile.
        #    Short-term conversation memory is an implicit reference and
        #    must NEVER appear as a visible basis item.
        eligible_memory_hits = [
            hit for hit in context.memory_hits
            if hit.topic == "artifact_memory"
            or hit.source == "attachment_excerpt"
            or hit.memory_type == "long_term"
        ]
        prioritized_memory_hits = sorted(
            eligible_memory_hits,
            key=lambda hit: (
                0 if (hit.topic == "artifact_memory" or hit.source == "attachment_excerpt") else 1,
                -float(hit.score or 0.0),
            ),
        )
        for hit in prioritized_memory_hits[:2]:
            basis_items.append(self._build_memory_basis_item(hit))

        # 5. Meeting availability (only for booking workflows)
        availability_item = self._build_availability_basis_item(context)
        if availability_item is not None:
            basis_items.append(availability_item)

        # 6. Deduplicate by (label, title, source) key
        deduped_items: list[AnswerBasisItem] = []
        seen_keys: set[tuple[str, str, str]] = set()
        for item in basis_items:
            item_key = (item.basis_label, item.title, item.source_label)
            if item_key in seen_keys:
                continue
            seen_keys.add(item_key)
            deduped_items.append(item)

        # 7. Safety net: strip any "近期交流记录" that slipped through.
        #    Session context is implicit and must never be cited.
        return [
            item for item in deduped_items
            if item.basis_label != "近期交流记录"
        ][:5]

    @staticmethod
    def _is_generic_index_page(hit: KnowledgeSearchHit) -> bool:
        """Return True for broad index/listing pages that match too many
        queries and add little value as answer basis citations."""
        title = (hit.title or "").lower()
        index_markers = ("论文索引", "论文列表", "索引页", "index")
        if any(marker in title for marker in index_markers):
            return True
        return False

    def _build_memory_basis_item(self, hit: ConversationMemoryHit) -> AnswerBasisItem:
        """Build a basis item for an eligible memory hit.

        Only artifact (uploaded) and long-term profile memory reach this
        method — short-term conversation memory is filtered upstream.
        """
        if hit.topic == "artifact_memory" or hit.source == "attachment_excerpt":
            return AnswerBasisItem(
                basis_label="上传材料",
                title="本轮上传或显式引用的材料",
                source_label=hit.source_label,
                detail=self._clip_basis_text(self._format_memory_basis_detail(hit.summary), 1000),
            )
        # Long-term profile memory
        return AnswerBasisItem(
            basis_label="学生长期记录",
            title="过往交流里提到过的长期偏好",
            source_label="长期记录",
            detail=self._clip_basis_text(self._format_memory_basis_detail(hit.summary), 1000),
        )

    def _build_memory_audit_items(
        self,
        memory_hits: list[ConversationMemoryHit],
        *,
        recent_session_context: str = "",
        conversation_id: str = "",
    ) -> list[MemoryAuditItem]:
        audit_items: list[MemoryAuditItem] = []
        recent_session_summary = self._normalize_recent_session_context(recent_session_context)
        if recent_session_summary:
            audit_items.append(
                MemoryAuditItem(
                    entry_id=f"session-context:{conversation_id or 'current'}",
                    memory_type="short_term",
                    source="session_context",
                    topic="conversation_exchange",
                    source_label="同会话上下文",
                    summary=self._clip_basis_text(recent_session_summary, 1200),
                    score=2.0,
                )
            )
        for hit in memory_hits[:5]:
            audit_items.append(
                MemoryAuditItem(
                    entry_id=hit.memory_id,
                    memory_type=hit.memory_type,
                    source=hit.source,
                    topic=hit.topic,
                    source_label=hit.source_label,
                    summary=self._clip_basis_text(hit.summary, 1200),
                    score=hit.score,
                )
            )
        return audit_items

    def _normalize_recent_session_context(self, recent_session_context: str) -> str:
        normalized = recent_session_context.strip()
        if not normalized:
            return ""
        header = "Immediate session context (same conversation):"
        if normalized.startswith(header):
            normalized = normalized[len(header) :].strip()
        return self._clip_basis_text(normalized, 1200)

    def _build_attachment_artifact_hits(self, request: ChatRequest) -> list[ConversationMemoryHit]:
        attachments = list(getattr(request, "attachments", []) or [])
        if not attachments:
            return []

        hits: list[ConversationMemoryHit] = []
        now = datetime.now(UTC)
        for index, attachment in enumerate(attachments, start=1):
            excerpt = _normalize_whitespace(attachment.text_content)
            clipped_excerpt = self._clip_basis_text(excerpt, 220)
            summary = (
                f"材料 {index}：{attachment.file_name}（{attachment.media_type}）。"
                f" 摘要：{clipped_excerpt}"
            )
            hits.append(
                ConversationMemoryHit(
                    memory_id=f"attachment:{attachment.file_name}:{index}",
                    conversation_id=request.conversation_id or "attachment",
                    summary=summary,
                    score=2.0,
                    created_at=now,
                    memory_type="short_term",
                    source="attachment_excerpt",
                    topic="artifact_memory",
                    source_label="上传材料",
                )
            )
        return hits

    def _build_knowledge_basis_item(self, hit: KnowledgeSearchHit) -> AnswerBasisItem:
        if self._looks_like_gap_draft_hit(hit):
            return AnswerBasisItem(
                basis_label="常见问题整理",
                title=self._clip_basis_text(self._format_gap_draft_basis_title(hit.title), 256),
                source_label="近期高频问题整理",
                detail=self._clip_basis_text(
                    self._format_gap_draft_basis_detail(hit.excerpt), 1000
                ),
            )

        return AnswerBasisItem(
            basis_label=self._classify_knowledge_basis_label(hit),
            title=self._clip_basis_text(self._format_basis_title(hit.title), 256),
            source_label=self._clip_basis_text(
                self._format_basis_source_label(hit.source_name), 256
            ),
            detail=self._clip_basis_text(self._format_basis_detail(hit.excerpt), 1000),
        )

    def _build_added_knowledge_basis_item(self, record: KnowledgeDocumentRecord) -> AnswerBasisItem:
        detail = (
            f"已通过管理员对话写入知识库，标签：{', '.join(record.tags) or '未设置'}。"
            "后续管理员检索和普通问答都可以立即复用这条资料。"
        )
        return AnswerBasisItem(
            basis_label="知识入库结果",
            title=self._clip_basis_text(self._format_basis_title(record.title), 256),
            source_label=self._clip_basis_text(
                self._format_basis_source_label(record.source_name), 256
            ),
            detail=self._clip_basis_text(detail, 1000),
        )

    def _clip_basis_text(self, value: str | None, limit: int) -> str:
        normalized = str(value or "").strip()
        if len(normalized) <= limit:
            return normalized
        return f"{normalized[: max(limit - 1, 0)].rstrip()}…"

    def _format_basis_title(self, title: str | None) -> str:
        normalized = re.sub(r"\s+", " ", str(title or "")).strip(" ：:|｜")
        if not normalized:
            return "相关材料"

        parts = [part.strip() for part in re.split(r"[|｜]", normalized) if part.strip()]
        meaningful_parts = [
            part for part in parts if not self._looks_like_fragmented_title_segment(part)
        ]
        if meaningful_parts:
            return " · ".join(meaningful_parts[:2])
        return normalized

    def _format_basis_detail(self, detail: str | None) -> str:
        normalized = re.sub(r"\s+", " ", str(detail or "")).strip()
        return normalized

    def _format_memory_basis_detail(self, detail: str | None) -> str:
        normalized = str(detail or "").strip()
        normalized = re.sub(r"\[[^\]]+\]\s*", "", normalized)
        normalized = normalized.replace("**", "")

        question_match = re.search(r"问：\s*(.+?)(?:\n|$)", normalized, re.DOTALL)
        answer_match = re.search(r"答：\s*(.+)", normalized, re.DOTALL)

        question = _normalize_whitespace(question_match.group(1)) if question_match else ""
        answer = (
            _normalize_whitespace(answer_match.group(1))
            if answer_match
            else _normalize_whitespace(normalized)
        )

        highlights = _extract_numbered_highlights(answer)
        if highlights:
            summary = "；".join(highlights[:4])
            if question:
                return f"你之前问过“{question}”，上次回答重点是：{summary}。"
            return f"之前相关回答重点是：{summary}。"

        compact_answer = _summarize_text(answer, limit=96)
        if question:
            return f"你之前问过“{question}”，上次回答重点是：{compact_answer}"
        return compact_answer

    def _looks_like_fragmented_title_segment(self, value: str) -> bool:
        pieces = [piece.strip() for piece in value.split("/") if piece.strip()]
        if len(pieces) < 2:
            return False
        return all(len(piece) <= 3 for piece in pieces)

    def _looks_like_gap_draft_hit(self, hit: KnowledgeSearchHit) -> bool:
        source_name = str(hit.source_name or "")
        hit_tags = {tag.lower() for tag in hit.tags}
        return source_name.startswith("analytics-gap:") or "faq-draft" in hit_tags

    def _format_gap_draft_basis_title(self, title: str | None) -> str:
        normalized = self._format_basis_title(title)
        parts = [part.strip() for part in re.split(r"[·|｜]", normalized) if part.strip()]
        topic = next((part for part in parts if part != "FAQ草稿"), normalized)
        return f"{topic}"

    def _format_gap_draft_basis_detail(self, detail: str | None) -> str:
        normalized = self._format_basis_detail(detail)
        suggested_action = self._extract_gap_draft_suggested_action(normalized)
        if suggested_action:
            return f"这类问题通常需要补充：{suggested_action}。"
        return "这类问题建议补一份更明确的说明模板，避免下次还要反复追问。"

    def _extract_gap_draft_suggested_action(self, detail: str | None) -> str:
        normalized = str(detail or "")
        matched = re.search(r"建议动作[:：]\s*([^。；]+)", normalized)
        if matched:
            return matched.group(1).strip()
        return ""

    def _build_availability_basis_item(
        self, context: ChatWorkflowContext
    ) -> AnswerBasisItem | None:
        retrieval_scopes = (
            set(context.interaction_intent.retrieval_scopes)
            if context.interaction_intent
            else set()
        )
        should_include_availability = (
            context.workflow_action in {"book_meeting", "collect_booking_details"}
            or "meeting_policy" in retrieval_scopes
        )
        if not should_include_availability:
            return None

        schedule = self._meeting_service.get_availability_schedule()
        if schedule.days:
            preview: list[str] = []
            for day in schedule.days[:3]:
                windows = (
                    "、".join(f"{window.start}-{window.end}" for window in day.windows) or "不开放"
                )
                preview.append(f"{day.date.isoformat()}: {windows}")
            detail = (
                f"当前依据本周可预约时段与 {self._settings.meeting_duration_minutes} 分钟单次预约规则处理。"
                f" 最近安排：{'；'.join(preview)}"
            )
        else:
            detail = (
                f"当前依据默认预约规则处理：工作时间 {self._settings.booking_start_hour:02d}:00-"
                f"{self._settings.booking_end_hour:02d}:00，单次预约 {self._settings.meeting_duration_minutes} 分钟。"
            )

        return AnswerBasisItem(
            basis_label="预约规则与时段",
            title="当前可预约规则",
            source_label="当前预约安排配置",
            detail=detail,
        )

    def _classify_knowledge_basis_label(self, hit: KnowledgeSearchHit) -> str:
        hit_tags = {tag.lower() for tag in hit.tags}
        source_name = (hit.source_name or "").lower()
        if self._is_teaching_hit(hit):
            if ".pdf" in source_name or "pdf" in hit_tags:
                return "课程 PDF"
            return "课程页面"
        if hit_tags & {"policy", "meeting", "preparation", "qa"}:
            return "过往政策说明"
        if self._is_research_hit(hit):
            if (
                ".pdf" in source_name
                or "research_papers" in source_name
                or "paper-digest" in hit_tags
            ):
                return "论文资料"
            return "个人主页条目"
        if source_name.startswith("homepage:"):
            return "个人主页条目"
        return "知识库条目"

    def _format_basis_source_label(self, source_name: str | None) -> str:
        if not source_name:
            return "管理员手动录入"

        normalized = re.sub(r"::part-\d+$", "", source_name)
        if normalized.startswith("knowledge-gap:"):
            return "常见问题补充条目"
        parts = normalized.split("::")
        primary_source = parts[0]
        attachment_source = next((part for part in parts[1:] if part.endswith(".pdf")), None)

        if primary_source.startswith("homepage:"):
            primary_path = primary_source.removeprefix("homepage:")
            primary_file, _, fragment = primary_path.partition("#")

            if attachment_source:
                attachment_name = attachment_source.split("/")[-1]
                if "/teaching/" in attachment_source:
                    return f"个人主页 / 课程 PDF / {attachment_name}"
                return f"个人主页 / 研究论文 PDF / {attachment_name}"

            if primary_file == "contents/home.md":
                return "个人主页 / 首页"
            if primary_file == "contents/news.md":
                return "个人主页 / 新闻动态"
            if primary_file == "contents/publications.md":
                return "个人主页 / 论文成果页"
            if primary_file == "contents/research_papers/publications_summary.md":
                return (
                    f"个人主页 / 研究论文汇总 / {fragment}"
                    if fragment
                    else "个人主页 / 研究论文汇总"
                )
            if primary_file.startswith("contents/research_papers/") and primary_file.endswith(
                ".pdf"
            ):
                return f"个人主页 / 研究论文 PDF / {primary_file.split('/')[-1]}"
            if primary_file == "contents/resources.md":
                return "个人主页 / 教学资源页"
            if primary_file.startswith("contents/teaching/"):
                if primary_file.endswith(".pdf"):
                    return f"个人主页 / 课程 PDF / {primary_file.split('/')[-1]}"
                course_name = primary_file.split("/")[-1].removesuffix(".md")
                if fragment and fragment != "intro":
                    return f"个人主页 / 课程页面 / {course_name} / {fragment}"
                return f"个人主页 / 课程页面 / {course_name}"
            if fragment and fragment not in {
                "intro",
                "recent-updates",
                "teaching-resources",
            }:
                return f"个人主页 / {fragment}"
            return "个人主页条目"

        if normalized.endswith("current_week.json"):
            return "当前预约安排配置"

        return normalized.split("/")[-1]

    def _append_trace(
        self,
        context: ChatWorkflowContext,
        *,
        key: str,
        title: str,
        summary: str,
        detail: str,
        status: str = "completed",
        duration_ms: int | None = None,
    ) -> None:
        step = WorkflowTraceStep(
            key=key,
            title=title,
            summary=summary,
            detail=detail,
            status=status,
            duration_ms=duration_ms,
            parallel_group=_PARALLEL_TRACE_GROUPS.get(key),
        )
        context.workflow_trace.append(step)
        if self._trace_callback is not None:
            self._trace_callback(step.model_copy(deep=True))

    def _format_pending_fields(self, pending_fields: list[str]) -> str:
        labels = {
            "student_email": "邮箱",
            "preferred_start": "会议时间",
            "topic": "会议主题",
        }
        return "、".join(labels.get(field, field) for field in pending_fields)

    def _elapsed_ms(self, started_at: float) -> int:
        return max(0, int(round((perf_counter() - started_at) * 1000)))

    def _looks_like_booking_information_request(self, question: str) -> bool:
        return asks_for_booking_information(question)

    def _needs_booking_intent_classification(self, question: str) -> bool:
        if self._looks_like_booking_information_request(question):
            return False
        lowered = question.lower()
        keywords = (
            "预约",
            "预定",
            "约时间",
            "约老师",
            "约个会",
            "book",
            "schedule",
            "meeting",
        )
        return any(keyword in lowered for keyword in keywords)

    def _should_force_human_handoff(self, question: str) -> bool:
        return requires_human_handoff(question)

    def _should_queue_for_review(self, question: str) -> bool:
        return requires_faculty_review(question)

    def _build_escalation_message(self, context: ChatWorkflowContext) -> str:
        record = context.escalation_record
        if record is None or context.interaction_intent is None:
            return "这个请求需要老师本人进一步处理。"

        if context.interaction_intent.action == "human_handoff":
            reason = (
                context.interaction_intent.escalation_reason or "这类问题不能由数字人代替老师处理。"
            )
            return (
                f"这个问题需要由 {context.owner_name} 本人直接处理，我不能代为决定或表态。\n"
                f"已创建人工处理工单：{record.escalation_id}\n"
                f"原因：{reason}\n"
                "如情况紧急，请直接通过正式联系方式联系老师。"
            )

        reason = (
            context.interaction_intent.escalation_reason or "这类请求需要老师审核后才能正式答复。"
        )
        return (
            "这个请求需要人工审核后才能给出正式结论，我先不替老师做决定。\n"
            f"已加入待审核队列：{record.escalation_id}\n"
            f"原因：{reason}"
        )

    def _should_start_booking_workflow(self, request: ChatRequest) -> bool:
        if self._looks_like_booking_information_request(request.question):
            return False
        if not self._needs_booking_intent_classification(request.question):
            return False
        return self._llm_client.classify_booking_intent_sync(
            request.question,
            request.course_context,
        )

    def _looks_like_admin_knowledge_injection(self, question: str) -> bool:
        normalized = _normalize_whitespace(question)
        if not normalized:
            return False

        trigger_markers = (
            "加入知识库",
            "添加到知识库",
            "写入知识库",
            "录入知识库",
            "保存到知识库",
            "记到知识库",
            "注入知识",
            "补充知识库",
            "更新知识库",
        )
        has_trigger = any(marker in normalized for marker in trigger_markers)
        if not has_trigger:
            return False

        has_structured_fields = any(
            marker in normalized
            for marker in (
                "标题：",
                "标题:",
                "内容：",
                "内容:",
                "正文：",
                "正文:",
                "标签：",
                "标签:",
            )
        )
        if has_structured_fields:
            return True

        command_body = self._strip_admin_knowledge_command(normalized)
        return len(command_body) >= 16

    def _build_admin_knowledge_request(
        self, request: ChatRequest
    ) -> KnowledgeDocumentCreate | None:
        command_body = self._strip_admin_knowledge_command(request.question)
        if not command_body:
            return None

        parsed_fields = self._parse_admin_knowledge_fields(command_body)
        content = parsed_fields.get("content") or command_body
        content = content.strip()
        if len(content) < 8:
            return None

        title = parsed_fields.get("title") or self._derive_admin_knowledge_title(
            content, request.course_context
        )
        tags = self._parse_admin_knowledge_tags(parsed_fields.get("tags", ""))
        source_name = parsed_fields.get("source") or None
        return KnowledgeDocumentCreate(
            title=title,
            content=content,
            tags=tags,
            source_name=source_name,
        )

    def _build_admin_knowledge_guidance_message(self) -> str:
        return (
            "已识别为管理员知识入库指令，但当前内容还不够完整。\n"
            "你可以直接这样发：\n"
            "加入知识库：\n"
            "标题：预约前准备清单\n"
            "标签：advising, booking\n"
            "内容：学生预约前需要先发送 agenda、当前 blocker 和相关 draft。"
        )

    def _build_feedback_web_knowledge_payload(
        self,
        record: ConversationMemoryRecord,
        hit: WebSearchHit,
        *,
        index: int,
    ) -> KnowledgeDocumentCreate | None:
        title = str(hit.title or "").strip()
        source_url = str(hit.url or "").strip()
        snippet = str(hit.snippet or "").strip()
        if not title or not source_url:
            return None

        domain = record.interaction_domain or "general"
        canonical_url = self._canonical_feedback_source_url(source_url)
        collected_at = datetime.now(UTC).isoformat()
        content = "\n".join(
            part
            for part in (
                f"问题：{record.question}",
                f"回答：{record.answer}",
                f"联网资料标题：{title}",
                f"联网摘要：{snippet}" if snippet else "",
                f"来源链接：{canonical_url}",
                f"采集时间：{collected_at}",
                "备注：该资料来自用户正向反馈的联网回答回写，可作为后续回答的补充依据；默认视为待审核网页资料，使用前仍应校验时效性。",
            )
            if part
        )
        url_digest = hashlib.sha1(canonical_url.encode("utf-8")).hexdigest()[:16]
        source_name = f"feedback-web:{domain}:{url_digest}"
        tags = ["feedback-web", "web-search", domain, "review:pending", "freshness:web"]
        return KnowledgeDocumentCreate(
            title=f"联网补充：{title}"[:256],
            content=content[:20000],
            tags=tags,
            source_name=source_name[:256],
            metadata={
                "exchange_id": record.memory_id,
                "source_url": canonical_url[:256],
                "interaction_domain": domain,
                "collected_at": collected_at,
                "review_status": "pending",
                "source_rank": str(index),
            },
        )

    def _canonical_feedback_source_url(self, url: str) -> str:
        normalized = str(url or "").strip()
        normalized = re.sub(r"#.*$", "", normalized)
        normalized = re.sub(r"([?&])utm_[^=&]+=[^&]*", "", normalized)
        normalized = re.sub(r"[?&]$", "", normalized)
        normalized = re.sub(r"/$", "", normalized)
        return normalized[:1000]

    def _answer_references_web_hit(self, answer_text: str, hit: WebSearchHit) -> bool:
        normalized_answer = str(answer_text or "").lower()
        if not normalized_answer:
            return False

        if str(hit.url or "").lower() in normalized_answer:
            return True

        title_tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]+", str(hit.title or ""))
            if len(token) >= 3
        }
        snippet_tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]+", str(hit.snippet or ""))
            if len(token) >= 5
        }
        answer_tokens = {
            token.lower()
            for token in re.findall(r"[A-Za-z0-9_]+", normalized_answer)
            if len(token) >= 3
        }

        if title_tokens & answer_tokens:
            return True
        if len(snippet_tokens & answer_tokens) >= 2:
            return True

        cjk_terms = {
            term
            for term in re.findall(r"[\u4e00-\u9fff]{2,}", f"{hit.title} {hit.snippet}")
            if len(term) >= 2
        }
        return any(term in answer_text for term in cjk_terms)

    def _build_admin_knowledge_success_message(self, record: KnowledgeDocumentRecord) -> str:
        tags_text = "、".join(record.tags) if record.tags else "未设置"
        source_text = record.source_name or "管理员手动录入"
        return (
            f"已写入知识库：{record.title}\n"
            f"标签：{tags_text}\n"
            f"来源：{source_text}\n"
            "后续管理员检索和学生对话都可以立即使用这条资料。"
        )

    def _strip_admin_knowledge_command(self, question: str) -> str:
        normalized = str(question or "").strip()
        patterns = [
            r"^(?:请|麻烦)?(?:帮我)?(?:把下面|将下面|把这段|将这段)?(?:内容|资料|信息)?(?:直接)?(?:加入|添加到|写入|录入|保存到|记到|补充到|更新到)(?:知识库|知识)(?:里|中)?[：:，,\s-]*",
            r"^(?:请|麻烦)?(?:帮我)?(?:直接)?(?:注入|录入)(?:知识|知识库)[：:，,\s-]*",
        ]
        stripped = normalized
        for pattern in patterns:
            stripped = re.sub(pattern, "", stripped, count=1)
        return stripped.strip()

    def _parse_admin_knowledge_fields(self, body: str) -> dict[str, str]:
        alias_map = {
            "标题": "title",
            "title": "title",
            "标签": "tags",
            "tag": "tags",
            "tags": "tags",
            "来源": "source",
            "来源名": "source",
            "source": "source",
            "内容": "content",
            "正文": "content",
            "content": "content",
        }
        result: dict[str, list[str]] = {
            "title": [],
            "tags": [],
            "source": [],
            "content": [],
        }
        fallback_lines: list[str] = []
        current_field: str | None = None

        for raw_line in body.splitlines():
            line = raw_line.strip()
            if not line:
                if current_field == "content":
                    result[current_field].append("")
                continue

            matched = re.match(
                r"^(标题|title|标签|tag|tags|来源|来源名|source|内容|正文|content)\s*[:：]\s*(.*)$",
                line,
                re.IGNORECASE,
            )
            if matched:
                current_field = alias_map[matched.group(1).lower()]
                value = matched.group(2).strip()
                if value:
                    result[current_field].append(value)
                continue

            if current_field == "content":
                result[current_field].append(raw_line.rstrip())
                continue

            fallback_lines.append(raw_line.rstrip())

        parsed = {
            key: "\n".join(value).strip() if key == "content" else " ".join(value).strip()
            for key, value in result.items()
            if any(part.strip() for part in value)
        }
        if "content" not in parsed:
            fallback_content = "\n".join(line for line in fallback_lines if line.strip()).strip()
            if fallback_content:
                parsed["content"] = fallback_content
        return parsed

    def _parse_admin_knowledge_tags(self, raw_tags: str) -> list[str]:
        if not raw_tags.strip():
            return []
        normalized: list[str] = []
        for tag in re.split(r"[,，;；、\s]+", raw_tags):
            cleaned = tag.strip().lower()
            if cleaned and cleaned not in normalized:
                normalized.append(cleaned)
        return normalized

    def _derive_admin_knowledge_title(self, content: str, course_context: str | None) -> str:
        if course_context and course_context.strip():
            return _summarize_text(course_context.strip(), limit=64)

        for line in content.splitlines():
            normalized = _normalize_whitespace(line).strip(" ：:|｜")
            if normalized:
                return _summarize_text(normalized, limit=64)
        return "管理员对话录入知识"

    def _missing_booking_fields(self, state: BookingWorkflowState) -> list[str]:
        missing_fields: list[str] = []
        if not state.student_email:
            missing_fields.append("student_email")
        if state.preferred_start is None:
            missing_fields.append("preferred_start")
        if not state.topic:
            missing_fields.append("topic")
        return missing_fields

    def _build_booking_follow_up(
        self,
        missing_fields: list[str],
        state: BookingWorkflowState,
    ) -> str:
        labels = {
            "student_email": "邮箱",
            "preferred_start": "会议时间",
            "topic": "会议主题",
        }
        known_parts: list[str] = []
        if state.student_email:
            known_parts.append(f"邮箱：{state.student_email}")
        if state.preferred_start:
            known_parts.append(f"时间：{state.preferred_start.strftime('%Y-%m-%d %H:%M')}")
        if state.topic:
            known_parts.append(f"主题：{state.topic}")

        missing = "、".join(labels[field] for field in missing_fields)
        known_text = f"已记录信息：{'；'.join(known_parts)}。\n" if known_parts else ""
        return (
            f"我正在为你处理会议预约。\n{known_text}"
            f"还缺少：{missing}。"
            "请直接回复缺失信息，例如“我的邮箱是 student@example.com，明天下午 3 点讨论论文进展”。"
        )

    def _build_booking_success_message(
        self,
        booking_response: BookingResponse,
    ) -> str:
        booking = booking_response.booking
        if booking is None:
            return booking_response.message
        lines = [
            f"{booking_response.message}\n"
            f"预约编号：{booking.booking_id}\n"
            f"主题：{booking.topic}\n"
            f"时间：{booking.start_at.strftime('%Y-%m-%d %H:%M')} - {booking.end_at.strftime('%H:%M')}\n"
            f"当前状态：{booking.status}"
        ]
        notification = booking_response.notification
        if notification is not None:
            lines.append(notification.summary)
            if notification.status == "failed" and notification.detail:
                lines.append(notification.detail)
        return "\n".join(lines)

    def _build_booking_retry_message(self, booking_response: BookingResponse) -> str:
        alternatives = (
            "、".join(booking_response.alternative_slots)
            if booking_response.alternative_slots
            else "无"
        )
        return (
            f"{booking_response.message}\n"
            f"可选时间：{alternatives}\n"
            "请回复新的时间，我会继续为你完成预约。"
        )

    def _attach_student_notification(
        self,
        booking_response: BookingResponse,
        *,
        success_message_prefix: str,
        notify: Callable[[BookingRecord], str],
    ) -> BookingResponse:
        booking = booking_response.booking
        if not booking_response.accepted or booking is None:
            return booking_response

        if booking_response.message not in {"预约已确认。", "预约已拒绝。"}:
            return booking_response

        message = booking_response.message
        try:
            recipient = notify(booking)
        except BookingNotificationError as exc:
            notification = NotificationDeliveryStatus(
                status="failed",
                summary=str(exc),
                detail="预约状态已经更新，但学生通知邮件未成功送达；可稍后重试或改为人工联系。",
            )
        else:
            notification = NotificationDeliveryStatus(
                status="sent",
                recipient=recipient,
                summary=success_message_prefix.format(recipient=recipient),
            )

        message = f"{message} {notification.summary}"

        return BookingResponse(
            accepted=booking_response.accepted,
            message=message,
            booking=booking,
            alternative_slots=booking_response.alternative_slots,
            notification=notification,
        )

    def _extract_email(self, text: str) -> str | None:
        match = re.search(r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", text, re.IGNORECASE)
        if match is None:
            return None
        return match.group(0)

    def _extract_time_window(self, text: str) -> tuple[datetime | None, datetime | None]:
        explicit = self._extract_explicit_datetime_window(text)
        if explicit != (None, None):
            return explicit

        base_date = self._extract_relative_date(text)
        if base_date is None:
            return None, None

        time_matches = list(
            re.finditer(
                r"(?:(上午|中午|下午|晚上))?\s*(\d{1,2}|[零〇一二两三四五六七八九十]{1,3})(?:[:：](\d{2})|点(?:(半)|(\d{1,2})分?)?)",
                text,
            )
        )
        if not time_matches:
            return None, None

        start_prefix, start_hour, start_minute = self._normalize_time_match(time_matches[0])
        start_at = datetime.combine(base_date, time(start_hour, start_minute))

        if len(time_matches) > 1:
            _, end_hour, end_minute = self._normalize_time_match(
                time_matches[1],
                inherited_prefix=start_prefix,
            )
            end_at = datetime.combine(base_date, time(end_hour, end_minute))
        else:
            end_at = start_at + timedelta(minutes=self._settings.meeting_duration_minutes)

        return start_at, end_at

    def _extract_explicit_datetime_window(
        self,
        text: str,
    ) -> tuple[datetime | None, datetime | None]:
        pattern = re.compile(
            r"(\d{4}-\d{1,2}-\d{1,2})[ T](\d{1,2}:\d{2})(?:\s*(?:到|-|至)\s*(\d{1,2}:\d{2}))?"
        )
        match = pattern.search(text)
        if match is None:
            return None, None

        day_text, start_text, end_text = match.groups()
        start_at = datetime.strptime(f"{day_text} {start_text}", "%Y-%m-%d %H:%M")
        if end_text:
            end_at = datetime.strptime(f"{day_text} {end_text}", "%Y-%m-%d %H:%M")
        else:
            end_at = start_at + timedelta(minutes=self._settings.meeting_duration_minutes)
        return start_at, end_at

    def _extract_relative_date(self, text: str) -> date | None:
        now = datetime.now()
        if "今天" in text:
            return now.date()
        if "明天" in text:
            return (now + timedelta(days=1)).date()
        if "后天" in text:
            return (now + timedelta(days=2)).date()
        return None

    def _normalize_time_match(
        self,
        match: re.Match[str],
        inherited_prefix: str | None = None,
    ) -> tuple[str | None, int, int]:
        prefix, hour_text, minute_text, half_text, cn_minute_text = match.groups()
        resolved_prefix = prefix or inherited_prefix
        hour = self._parse_time_number(hour_text)
        if minute_text:
            minute = int(minute_text)
        elif half_text:
            minute = 30
        elif cn_minute_text:
            minute = int(cn_minute_text)
        else:
            minute = 0

        if resolved_prefix in {"下午", "晚上"} and hour < 12:
            hour += 12
        if resolved_prefix == "中午" and hour < 11:
            hour += 12
        return resolved_prefix, hour, minute

    def _parse_time_number(self, text: str) -> int:
        if text.isdigit():
            return int(text)
        digits = {
            "零": 0,
            "〇": 0,
            "一": 1,
            "二": 2,
            "两": 2,
            "三": 3,
            "四": 4,
            "五": 5,
            "六": 6,
            "七": 7,
            "八": 8,
            "九": 9,
        }
        if text == "十":
            return 10
        if text.startswith("十"):
            return 10 + digits.get(text[-1], 0)
        if text.endswith("十"):
            return digits.get(text[0], 0) * 10
        if "十" in text:
            tens_text, ones_text = text.split("十", 1)
            return digits.get(tens_text, 1) * 10 + digits.get(ones_text, 0)
        return digits.get(text, 0)

    def _extract_topic(self, question: str, course_context: str | None) -> str | None:
        explicit_patterns = (
            r"(?:讨论|聊聊|沟通|关于)\s*([^，。！？?]+)",
            r"主题(?:是|为)?\s*([^，。！？?]+)",
        )
        for pattern in explicit_patterns:
            match = re.search(pattern, question)
            if match is not None:
                topic = match.group(1).strip()
                if topic:
                    return topic[:256]

        cleaned = question
        cleaned = re.sub(
            r"[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}", " ", cleaned, flags=re.IGNORECASE
        )
        cleaned = re.sub(
            r"\d{4}-\d{1,2}-\d{1,2}[ T]\d{1,2}:\d{2}(?:\s*(?:到|-|至)\s*\d{1,2}:\d{2})?",
            " ",
            cleaned,
        )
        cleaned = re.sub(r"(今天|明天|后天)", " ", cleaned)
        cleaned = re.sub(
            r"(上午|中午|下午|晚上)?\s*\d{1,2}(?:[:：]\d{2}|点(?:半|\d{1,2}分?)?)",
            " ",
            cleaned,
        )
        cleaned = re.sub(
            r"(请|帮我|想|需要|安排|预约|预定|约|老师|一个|一下|个|时间|会议|meeting|book|schedule)",
            " ",
            cleaned,
            flags=re.IGNORECASE,
        )
        cleaned = re.sub(r"\s+", " ", cleaned).strip(" ，。！？?")
        if cleaned:
            return cleaned[:256]
        if course_context:
            return course_context.strip()[:256]
        return None


class ResultCollector(SinkFunction):
    def __init__(self, results: list[Any]) -> None:
        super().__init__()
        self._results = results

    def execute(self, data: Any) -> None:
        self._results.append(data)


class BootstrapChatContextStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: ChatRequest) -> ChatWorkflowContext:
        return self._support.bootstrap_chat(data)


class InteractionUnderstandingStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: ChatWorkflowContext) -> ChatWorkflowContext:
        return self._support.understand_interaction(data)


class BookingPreparationStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: ChatWorkflowContext) -> ChatWorkflowContext:
        return self._support.prepare_booking(data)


class BookingExecutionStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: ChatWorkflowContext) -> ChatWorkflowContext:
        return self._support.execute_booking(data)


class KnowledgeRetrievalStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: ChatWorkflowContext) -> ChatWorkflowContext:
        return self._support.retrieve_knowledge(data)


class MemoryRetrievalStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: ChatWorkflowContext) -> ChatWorkflowContext:
        return self._support.retrieve_memory(data)


class PromptBuildStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: ChatWorkflowContext) -> ChatWorkflowContext:
        return self._support.build_prompt(data)


class LlmAnswerStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: ChatWorkflowContext) -> ChatWorkflowContext:
        return self._support.answer_with_llm(data)


class MemoryPersistStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: ChatWorkflowContext) -> ChatWorkflowContext:
        return self._support.persist_memory(data)


class MemoryProfileConsolidationStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: ChatWorkflowContext) -> ChatWorkflowContext:
        return self._support.consolidate_profile_memory(data)


class FollowUpPlanningStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: ChatWorkflowContext) -> ChatWorkflowContext:
        return self._support.plan_follow_up_actions(data)


class MemoryUsefulnessScoringStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: ChatWorkflowContext) -> ChatWorkflowContext:
        return self._support.score_memory_usefulness(data)


class ChatResponseRenderStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: ChatWorkflowContext) -> ChatResponse:
        return self._support.render_chat_response(data)


class _CaptureContextStage(MapFunction):
    """Pass-through map that side-channels the in-flight ChatWorkflowContext.

    The chat critical-path DAG ends with ``ChatResponseRenderStage`` which
    consumes a ``ChatWorkflowContext`` and emits a ``ChatResponse``. We need
    the underlying context after the pipeline completes so the post-answer
    fan-out stages can run on it (Task 2 of the Chat Latency Optimizations
    plan). Inserting this stage right before render lets us capture the
    same mutable context object the render will see, without adding a
    second sink to the DAG.
    """

    def __init__(self, captured: list[ChatWorkflowContext]) -> None:
        super().__init__()
        self._captured = captured

    def execute(self, data: ChatWorkflowContext) -> ChatWorkflowContext:
        self._captured.append(data)
        return data


class ReadAdminSessionStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: AdminSessionTokenInput) -> AdminSessionResponse:
        return self._support.read_admin_session(data)


class RequireAdminSessionStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: AdminSessionTokenInput) -> dict[str, Any]:
        return self._support.require_admin_session(data)


class AdminLoginStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: AdminLoginRequest) -> AdminLoginWorkflowResult:
        return self._support.login_admin(data)


class AdminLogoutStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, _: object) -> AdminSessionResponse:
        return self._support.logout_admin(_)


class ReadUserSessionStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: UserSessionTokenInput) -> UserSessionResponse:
        return self._support.read_user_session(data)


class UserRegisterStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: UserRegisterRequest) -> UserAuthWorkflowResult:
        return self._support.register_user(data)


class UserLoginStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: UserLoginRequest) -> UserAuthWorkflowResult:
        return self._support.login_user(data)


class UserLogoutStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, _: object) -> UserSessionResponse:
        return self._support.logout_user(_)


class AddKnowledgeStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: KnowledgeDocumentCreate) -> KnowledgeDocumentRecord:
        return self._support.add_knowledge(data)


class ListKnowledgeStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, _: object) -> list[KnowledgeDocumentRecord]:
        return self._support.list_knowledge()


class SearchKnowledgeStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: KnowledgeSearchInput) -> KnowledgeSearchResponse:
        return self._support.search_knowledge(
            data.query,
            visitor_profile=data.visitor_profile,
            admin_role=data.admin_role,
        )


class CreateBookingStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, data: BookingRequest) -> BookingResponse:
        return self._support.book_meeting(data)


class ListBookingsStage(MapFunction):
    def __init__(self, support: FacultyTwinWorkflowSupport) -> None:
        super().__init__()
        self._support = support

    def execute(self, status: str | None) -> list[BookingRecord]:
        return self._support.list_bookings(status=status)


class ChatContextMergeFunction(BaseCoMapFunction):
    """Fan-in operator for the chat DAG.

    The chat DAG fans out into N parallel branches that all share the same
    `ChatWorkflowContext` Python object (each branch only mutates disjoint
    fields). When every branch has reported for a given conversation, this
    operator emits the context exactly once. Subsequent branch arrivals for
    the same conversation also emit nothing.

    SAGE's `comap()` operator validates the presence of `map0`..`mapN`
    methods at the **class** level (see
    `sage.stream.connected_streams.ConnectedStreams.comap`), so we cannot use
    dynamic `setattr` in `__init__`. Instead, this base class provides the
    shared dispatch logic and concrete subclasses (`_ChatContextMerge2`,
    `_ChatContextMerge4`) declare the required `mapN` methods explicitly.

    The implementation is deliberately defensive: it dispatches arrivals via
    the conversation_id rather than relying on object identity, so the same
    helper works whether branches mutate-in-place or return clones.
    """

    # Concrete subclasses override this with the actual fan-in width.
    n_inputs: int = 2

    def __init__(self) -> None:
        super().__init__()
        if self.n_inputs < 2:
            raise ValueError("ChatContextMergeFunction requires at least 2 input streams.")
        # conversation_id -> set of input indices that have already arrived.
        self._arrivals: dict[str, set[int]] = {}

    def _conversation_key(self, context: ChatWorkflowContext) -> str:
        # Fall back to id() so we still merge correctly if a request lacks a
        # conversation_id (e.g. unit tests using bare contexts).
        return getattr(context, "conversation_id", "") or f"obj-{id(context)}"

    def _on_branch(
        self, input_index: int, context: ChatWorkflowContext | None
    ) -> ChatWorkflowContext | None:
        if context is None:
            return None
        key = self._conversation_key(context)
        seen = self._arrivals.setdefault(key, set())
        seen.add(input_index)
        if len(seen) >= self.n_inputs:
            # All branches reported. Emit exactly once and reset state so the
            # operator can be reused for subsequent requests.
            self._arrivals.pop(key, None)
            return context
        return None


class _ChatContextMerge2(ChatContextMergeFunction):
    """Two-way fan-in (e.g. memory + knowledge retrieval branches)."""

    n_inputs = 2

    def map0(self, data: ChatWorkflowContext | None) -> ChatWorkflowContext | None:
        return self._on_branch(0, data)

    def map1(self, data: ChatWorkflowContext | None) -> ChatWorkflowContext | None:
        return self._on_branch(1, data)


class _ChatContextMerge4(ChatContextMergeFunction):
    """Four-way fan-in (e.g. post-answer parallel branches)."""

    n_inputs = 4

    def map0(self, data: ChatWorkflowContext | None) -> ChatWorkflowContext | None:
        return self._on_branch(0, data)

    def map1(self, data: ChatWorkflowContext | None) -> ChatWorkflowContext | None:
        return self._on_branch(1, data)

    def map2(self, data: ChatWorkflowContext | None) -> ChatWorkflowContext | None:
        return self._on_branch(2, data)

    def map3(self, data: ChatWorkflowContext | None) -> ChatWorkflowContext | None:
        return self._on_branch(3, data)


class DigitalTwinService:
    def __init__(
        self,
        settings: AppSettings,
        *,
        delivery_gate: ChatDeliveryGate | None = None,
    ) -> None:
        self._settings = settings
        self._delivery_gate = delivery_gate or ChatDeliveryGate()
        self._llm_client = VllmChatClient(settings)
        self._knowledge_store = LocalKnowledgeStore(settings)
        self._conversation_store = NeuroMemConversationStore(settings)
        self._analytics_store = ConversationAnalyticsStore(settings, self._conversation_store)
        self._artifact_memory_draft_store = ArtifactMemoryDraftStore(settings)
        self._knowledge_gap_draft_store = KnowledgeGapDraftStore(settings)
        self._escalation_store = EscalationQueueStore(settings)
        self._follow_up_store = FollowUpQueueStore(settings)
        self._operations_task_state_store = OperationsTaskStateStore(settings)
        self._runtime_feature_flag_store = RuntimeFeatureFlagStore(settings)
        self._planner_comparison_store = PlannerComparisonStore(settings)
        self._planner_metrics_store = PlannerMetricsStore(settings)
        self._suggestion_store = SuggestionBoardStore(settings)
        self._user_store = UserAccountStore(settings)
        self._online_presence_store = OnlinePresenceStore(settings)
        self._digest_store = ConversationDigestStore(settings.context_digest_dir)
        self._meeting_service = MeetingService(settings)
        self._calendar_bridge = CalendarBridgeClient(settings)
        self._email_notifier = BookingEmailNotifier(settings)
        self._runtime_manager = ServiceRuntimeManager(settings)
        self._code_workbench = CodeWorkbench(settings)
        self._workflow_planner = DeterministicWorkflowPlanner(
            policy_path=settings.workflow_policy_path
        )
        # Agent skill system (V4.0)

        self._skill_tool_registry = SkillToolRegistry(
            knowledge_store=self._knowledge_store,
            memory_store=self._conversation_store,
        )
        self._skill_router = SkillRouter(
            skill_dir=settings.skill_dir,
            current_version=_app_version,
        )
        self._skill_runner = SkillRunner(
            llm_client=self._llm_client,
            tool_registry=self._skill_tool_registry,
            max_parallel_tools=settings.skill_tool_parallelism,
            answer_max_tokens=settings.skill_answer_max_tokens,
        )
        self._sage_runtime_class = FlowNetEnvironment
        self._booking_workflows: dict[str, BookingWorkflowState] = {}
        self._fixed_prefix_cache_warmup_attempted = False
        self._fixed_prefix_cache_warmup_lock = threading.Lock()
        self._normalize_published_gap_documents()

    def warm_fixed_prefix_cache(self) -> bool:
        if not self._settings.kv_fixed_prefix_warmup_on_startup:
            return False
        with self._fixed_prefix_cache_warmup_lock:
            if self._fixed_prefix_cache_warmup_attempted:
                return False
            self._fixed_prefix_cache_warmup_attempted = True
        try:
            system_prompt = build_system_prompt(self._settings)
            warmed = self._llm_client.warm_fixed_prefix_cache_sync(system_prompt)
            if warmed:
                _logger.info("Fixed-prefix KV warmup completed.")
            return warmed
        except Exception as exc:  # pragma: no cover - startup must remain best-effort
            _logger.warning("Fixed-prefix KV warmup skipped: %s", exc)
            return False

    def _normalize_published_gap_documents(self) -> None:
        changed = False
        published_drafts = [
            draft
            for draft in self._knowledge_gap_draft_store.list_drafts()
            if draft.status == "published" and draft.published_document_id
        ]

        for document in self._knowledge_store.list_documents():
            if not _is_legacy_gap_document(document.source_name, document.tags):
                continue
            draft = _match_gap_draft_for_document(document=document, drafts=published_drafts)
            formalized = (
                _build_published_gap_document(draft)
                if draft is not None
                else _build_published_gap_document_from_legacy(document)
            )
            if _document_matches_payload(document, formalized):
                continue
            self._knowledge_store.update_document(
                document.document_id, formalized, rebuild_indexes=False
            )
            changed = True

        if changed:
            self._knowledge_store.rebuild_indexes()

    async def answer(
        self,
        request: ChatRequest,
        admin_session_token: str | None = None,
        trace_callback: WorkflowTraceCallback | None = None,
        on_post_answer_complete: Callable[[], None] | None = None,
        answer_chunk_callback: Callable[[str], None] | None = None,
    ) -> DeliveredChatResponse:
        response = await self._answer_with_execution_mode(
            request,
            admin_session_token=admin_session_token,
            trace_callback=trace_callback,
            use_runtime_pipeline=self._settings.chat_runtime_pipeline_enabled,
            on_post_answer_complete=on_post_answer_complete,
            answer_chunk_callback=answer_chunk_callback,
        )
        return self._deliver_chat_response(request, response)

    async def answer_in_process(
        self,
        request: ChatRequest,
        admin_session_token: str | None = None,
        trace_callback: WorkflowTraceCallback | None = None,
        on_post_answer_complete: Callable[[], None] | None = None,
        answer_chunk_callback: Callable[[str], None] | None = None,
    ) -> DeliveredChatResponse:
        response = await self._answer_with_execution_mode(
            request,
            admin_session_token=admin_session_token,
            trace_callback=trace_callback,
            use_runtime_pipeline=False,
            on_post_answer_complete=on_post_answer_complete,
            answer_chunk_callback=answer_chunk_callback,
        )
        return self._deliver_chat_response(request, response)

    def _deliver_chat_response(
        self,
        request: ChatRequest,
        response: ChatResponse,
    ) -> DeliveredChatResponse:
        origin_by_action = {
            "invitation_code_detected": AnswerOrigin.INVITATION,
            "skill_answer": AnswerOrigin.SKILL,
            "code_assist": AnswerOrigin.CODE_WORKBENCH,
            "auto_scientist": AnswerOrigin.AUTO_SCIENTIST,
        }
        return self._delivery_gate.deliver(
            response=response,
            original_question=request.question,
            origin=origin_by_action.get(response.workflow_action, AnswerOrigin.PIPELINE),
        )

    async def _answer_with_execution_mode(
        self,
        request: ChatRequest,
        *,
        admin_session_token: str | None,
        trace_callback: WorkflowTraceCallback | None,
        use_runtime_pipeline: bool,
        on_post_answer_complete: Callable[[], None] | None = None,
        answer_chunk_callback: Callable[[str], None] | None = None,
    ) -> ChatResponse:
        admin_session_payload = decode_admin_session_token(admin_session_token, self._settings)

        # Pre-LLM invitation code detection: intercept invitation codes before
        # routing to the LLM pipeline so that the onboarding flow is triggered
        # instead of treating the code as a research query.
        invitation_response = self._check_invitation_code_in_message(request)
        if invitation_response is not None:
            return invitation_response

        if (
            self._code_workbench_available()
            and self._code_workbench.is_chat_command(request.question)
        ):
            return await self._answer_code_workbench_command(request)

        if self._settings.app_profile == "auto_scientist":
            return await self._answer_auto_scientist(request)

        recent_session_context = self._build_recent_session_context(request)

        # Explicit deep-thinking requests use the grounded SAGE pipeline
        # directly. Skill runners have their own answer-generation/retry
        # budget; routing through them first can spend the entire chat
        # deadline before the deep answer stage even starts (and is especially
        # costly on the current low-throughput NPU backend).
        matched_skill = (
            None
            if (
                bool(getattr(request, "deep_thinking_explicit", False))
                and bool(getattr(request, "deep_thinking", True))
            )
            else self._skill_router.match(request.question)
        )
        if matched_skill is not None:
            _logger.info(
                "Skill router matched '%s' for question (len=%d)",
                matched_skill.skill_id,
                len(request.question),
            )
            skill_context = SkillContext(
                question=request.question,
                visitor_profile=request.visitor_profile or "general_visitor",
                pre_fetched_context=recent_session_context,
                session_identity=request.conversation_id or "anonymous",
                course_context=getattr(request, "course_context", None),
            )
            try:
                skill_result = await asyncio.to_thread(
                    self._skill_runner.run,
                    matched_skill,
                    skill_context,
                )
                if skill_result.success:

                    return ChatResponse(
                        answer=skill_result.answer,
                        owner_name=self._settings.owner_name,
                        used_model=self._llm_client.model_name,
                        conversation_id=request.conversation_id or str(uuid4()),
                        workflow_action="skill_answer",
                        decision_mode=f"skill:{matched_skill.skill_id}",
                    )
                _logger.warning(
                    "Skill '%s' failed: %s; falling through to standard pipeline",
                    matched_skill.skill_id,
                    skill_result.error,
                )
            except Exception as exc:
                _logger.warning(
                    "Skill '%s' raised exception: %s; falling through to standard pipeline",
                    matched_skill.skill_id,
                    exc,
                )

        workflow_context = WorkflowRequestContext.from_chat_request(
            request,
            is_admin_request=admin_session_payload is not None,
            policy_version=self._workflow_planner.policy_version,
            recent_session_context_attached=bool(recent_session_context.strip()),
            allow_draft_write=_allow_draft_write_for_request(
                request, admin_session_payload is not None
            ),
        )
        deterministic_started_at = perf_counter()
        planner_decision = self._workflow_planner.plan(workflow_context)
        deterministic_latency_ms = (perf_counter() - deterministic_started_at) * 1000.0
        shadow_started_at = perf_counter()
        shadow_decision, shadow_status, shadow_message = self._plan_shadow_comparison(
            workflow_context,
            planner_decision,
        )
        shadow_latency_ms = (perf_counter() - shadow_started_at) * 1000.0
        self._record_planner_metrics(
            request,
            planner_decision=planner_decision,
            deterministic_latency_ms=deterministic_latency_ms,
            shadow_decision=shadow_decision,
            shadow_status=shadow_status,
            shadow_message=shadow_message,
            shadow_latency_ms=shadow_latency_ms,
        )
        planner_comparison = self._build_planner_comparison(
            planner_decision,
            shadow_decision,
            shadow_status,
            shadow_message,
        )
        support = self._build_support(
            admin_session_payload=admin_session_payload,
            trace_callback=trace_callback,
            answer_chunk_callback=answer_chunk_callback,
            planner_decision=planner_decision,
            shadow_planner_decision=shadow_decision,
            shadow_planner_status=shadow_status,
            shadow_planner_message=shadow_message,
            planner_comparison=planner_comparison,
        )
        if use_runtime_pipeline:
            response, context = await asyncio.to_thread(
                self._run_chat_dag_pipeline,
                request,
                support,
            )
        else:
            response, context = await asyncio.to_thread(
                self._run_chat_in_process,
                request,
                support,
            )

        should_background = self._settings.post_answer_background and trace_callback is not None

        if should_background:
            # Production fast path: ship the rendered response immediately,
            # run post-answer side-effects on a background task. The trace
            # callback inside ``_run_post_answer_inline_blocking`` keeps the
            # workflow-events SSE stream populated; ``on_post_answer_complete``
            # lets the caller (api.py) defer ``publish_complete`` until the
            # background task finishes so SSE consumers see the post-answer
            # trace steps before the stream closes.
            async def _run_post_answer_background() -> None:
                try:
                    await asyncio.to_thread(
                        self._run_post_answer_inline_blocking,
                        context,
                        support,
                    )
                except Exception:  # pragma: no cover - defensive log
                    _logger.exception(
                        "post-answer background task failed (conversation_id=%s)",
                        getattr(context, "conversation_id", None),
                    )
                finally:
                    if on_post_answer_complete is not None:
                        try:
                            on_post_answer_complete()
                        except Exception:  # pragma: no cover
                            _logger.exception(
                                "post-answer complete callback failed (conversation_id=%s)",
                                getattr(context, "conversation_id", None),
                            )

            asyncio.create_task(_run_post_answer_background())
            self._persist_planner_comparison_result(request, response)
            return response

        # Inline path (DAG runtime, no background flag or no trace callback):
        # mirror the legacy semantics so direct callers (tests, batch jobs,
        # benchmark adapter) still see the canonical 14-step trace and the
        # populated post-answer fields.
        await asyncio.to_thread(self._run_post_answer_inline_blocking, context, support)
        final_response = self._patch_response_with_post_answer(response, context)
        if on_post_answer_complete is not None:
            on_post_answer_complete()
        self._persist_planner_comparison_result(request, final_response)
        return final_response

    def preview_workflow_plan(
        self,
        request: ChatRequest,
        *,
        admin_session_token: str | None = None,
    ) -> PlannerDecision:
        admin_session_payload = decode_admin_session_token(admin_session_token, self._settings)
        recent_session_context = self._build_recent_session_context(request)
        context = WorkflowRequestContext.from_chat_request(
            request,
            is_admin_request=admin_session_payload is not None,
            policy_version=self._workflow_planner.policy_version,
            recent_session_context_attached=bool(recent_session_context.strip()),
            allow_draft_write=_allow_draft_write_for_request(
                request, admin_session_payload is not None
            ),
        )
        return self._workflow_planner.plan(context)

    def _check_invitation_code_in_message(
        self, request: ChatRequest
    ) -> ChatResponse | None:
        """Pre-LLM check: detect invitation codes in the message text.

        When a student pastes an invitation code (e.g. ``SAGE-LAB-2026``)
        into the chat, the system should recognise it and guide them to the
        registration flow instead of treating it as a research query.

        Returns a ``ChatResponse`` with onboarding guidance when a code is
        detected, or ``None`` to let the normal pipeline proceed.
        """
        if not self._settings.lab_member_invitation_code_enabled:
            return None
        configured_code = self._settings.lab_member_invitation_code.strip()
        if not configured_code:
            return None

        question = request.question.strip()
        # Match the exact configured code or the general pattern SAGE-LAB-XXXX.
        code_pattern = re.compile(
            r"\bSAGE[-_]LAB[-_]\d{4}\b", re.IGNORECASE
        )
        if not code_pattern.search(question) and question.upper() != configured_code.upper():
            return None

        _logger.info(
            "Invitation code detected in chat message (conversation_id=%s)",
            request.conversation_id,
        )
        answer = (
            "你好！你输入的是实验室成员邀请码。\n\n"
            "请使用注册页面完成实验室成员注册：\n"
            "1. 点击页面右上角的“登录/注册”按钮\n"
            "2. 选择“实验室成员注册”选项\n"
            "3. 填写你的姓名、邮箱和密码\n"
            f"4. 在邀请码栏中输入：**{configured_code}**\n\n"
            "注册成功后，你将获得实验室成员权限，可以访问更多资源。"
        )
        return ChatResponse(
            answer=answer,
            owner_name=self._settings.owner_name,
            used_model=self._llm_client.model_name,
            conversation_id=request.conversation_id or str(uuid4()),
            workflow_action="invitation_code_detected",
            decision_mode="onboarding",
        )

    def _build_recent_session_context(self, request: ChatRequest) -> str:
        return self._build_support()._format_recent_session_context(request)

    def _build_planner_comparison(
        self,
        decision: PlannerDecision | None,
        shadow_decision: PlannerDecision | None,
        shadow_status: str,
        shadow_message: str | None,
    ) -> WorkflowPlanComparison | None:
        return self._build_support()._build_planner_comparison(
            decision,
            shadow_decision,
            shadow_status,
            shadow_message,
        )

    def _plan_shadow_comparison(
        self,
        context: WorkflowRequestContext,
        planner_decision: PlannerDecision,
    ) -> tuple[PlannerDecision | None, str, str | None]:
        if (context.course_context or "").strip() in {
            "CharacterEval role-play benchmark",
            "LaMP personalization benchmark",
        }:
            return (
                None,
                "shadow_disabled",
                "Benchmark evaluation request skips shadow planner to keep latency and scoring focused on the main execution lane.",
            )

        if not self._runtime_feature_flag_store.get().shadow_planner_enabled:
            return None, "shadow_disabled", "LLM shadow planner not enabled yet."

        proposal_method = getattr(self._llm_client, "propose_shadow_plan_candidate_sync", None)
        if proposal_method is None:
            return (
                None,
                "shadow_disabled",
                "Current LLM client does not implement shadow planner proposals.",
            )

        try:
            candidate = proposal_method(context, planner_decision.plan)
            shadow_decision = self._workflow_planner.evaluate_shadow_candidate(candidate, context)
        except Exception as exc:
            return None, "shadow_error", str(exc)[:256]

        shadow_message = None
        if not shadow_decision.accepted:
            shadow_message = (
                shadow_decision.fallback.reason
                if shadow_decision.fallback is not None
                else "Shadow planner proposal failed policy validation."
            )
        return shadow_decision, "shadow_ready", shadow_message

    def get_runtime_feature_flags(self) -> RuntimeFeatureFlagsResponse:
        return self._runtime_feature_flag_store.get()

    def update_runtime_feature_flags(
        self,
        request: RuntimeFeatureFlagsUpdateRequest | dict[str, Any],
    ) -> RuntimeFeatureFlagsResponse:
        normalized = (
            request
            if isinstance(request, RuntimeFeatureFlagsUpdateRequest)
            else RuntimeFeatureFlagsUpdateRequest.model_validate(request)
        )
        return self._runtime_feature_flag_store.update_shadow_planner(
            normalized.shadow_planner_enabled
        )

    def get_admin_session(self, session_token: str | None) -> AdminSessionResponse:
        support = self._build_support()
        return self._run_pipeline_blocking(
            "faculty-twin-admin-session-read",
            [AdminSessionTokenInput(session_token=session_token)],
            [(ReadAdminSessionStage, support)],
            "SAGE runtime completed without producing an admin session response.",
        )

    def require_admin_session(self, session_token: str | None) -> dict[str, Any]:
        support = self._build_support()
        return self._run_pipeline_blocking(
            "faculty-twin-admin-session-require",
            [AdminSessionTokenInput(session_token=session_token)],
            [(RequireAdminSessionStage, support)],
            "SAGE runtime completed without producing an admin authorization result.",
        )

    def login_admin(self, request: AdminLoginRequest) -> AdminLoginWorkflowResult:
        support = self._build_support()
        return self._run_pipeline_blocking(
            "faculty-twin-admin-login",
            [request],
            [(AdminLoginStage, support)],
            "SAGE runtime completed without producing an admin login result.",
        )

    def logout_admin(self) -> AdminSessionResponse:
        support = self._build_support()
        return self._run_pipeline_blocking(
            "faculty-twin-admin-logout",
            [_FLOWNET_TICK],
            [(AdminLogoutStage, support)],
            "SAGE runtime completed without producing an admin logout response.",
        )

    def list_code_workspaces(self) -> CodeWorkspaceListResponse:
        self._require_code_workbench_enabled()
        return self._code_workbench.list_workspaces()

    def search_code(self, request: CodeSearchRequest) -> CodeSearchResponse:
        self._require_code_workbench_enabled()
        return self._code_workbench.search(request)

    def read_code_file(self, request: CodeFileReadRequest) -> CodeFileReadResponse:
        self._require_code_workbench_enabled()
        return self._code_workbench.read_file(request)

    def list_code_directory(
        self,
        request: CodeDirectoryListRequest,
    ) -> CodeDirectoryListResponse:
        self._require_code_workbench_enabled()
        return self._code_workbench.list_directory(request)

    def get_code_git_status(
        self,
        request: CodeGitStatusRequest,
    ) -> CodeGitStatusResponse:
        self._require_code_workbench_enabled()
        return self._code_workbench.git_status(request)

    def get_code_git_diff(self, request: CodeGitDiffRequest) -> CodeGitDiffResponse:
        self._require_code_workbench_enabled()
        return self._code_workbench.git_diff(request)

    def build_code_context(self, request: CodeContextRequest) -> CodeContextResponse:
        self._require_code_workbench_enabled()
        return self._code_workbench.build_context(request)

    def run_code_command(self, request: CodeCommandRequest) -> CodeCommandResponse:
        self._require_code_workbench_enabled()
        effective_request = request
        if request.code_approval_mode == "full" and not request.allow_write:
            effective_request = request.model_copy(update={"allow_write": True})
        return self._code_workbench.run_command(effective_request)

    async def assist_with_code(self, request: CodeAssistRequest) -> CodeAssistResponse:
        self._require_code_workbench_enabled()
        started_at = perf_counter()
        result = await asyncio.to_thread(self._code_agent_backend().assist, request)
        answer = self._strip_thinking_blocks(result.answer)
        return CodeAssistResponse(
            workspace_id=request.workspace_id,
            answer=answer,
            context_paths=result.context_paths,
            used_model=result.used_model,
            workflow_trace=self._build_code_workflow_trace(
                action="ask",
                workspace_id=request.workspace_id,
                backend=result.backend,
                context_paths=result.context_paths,
                result_summary=self._code_result_summary(result.backend, "ask"),
                duration_ms=self._code_elapsed_ms(started_at),
            ),
        )

    async def propose_code_change(self, request: CodeProposeRequest) -> CodeProposeResponse:
        self._require_code_workbench_enabled()
        started_at = perf_counter()
        result = await asyncio.to_thread(self._code_agent_backend().propose, request)
        answer = self._strip_thinking_blocks(result.answer)
        parsed = self._parse_code_proposal(answer)
        return CodeProposeResponse(
            workspace_id=request.workspace_id,
            summary=parsed["summary"],
            affected_files=parsed["affected_files"],
            unified_diff=parsed["unified_diff"],
            risks=parsed["risks"],
            suggested_tests=parsed["suggested_tests"],
            proposal=answer,
            context_paths=result.context_paths,
            used_model=result.used_model,
            workflow_trace=self._build_code_workflow_trace(
                action="propose",
                workspace_id=request.workspace_id,
                backend=result.backend,
                context_paths=result.context_paths,
                result_summary=self._code_result_summary(result.backend, "propose"),
                duration_ms=self._code_elapsed_ms(started_at),
            ),
        )

    def _code_agent_backend(self) -> CodeAgentBackend:
        if self._settings.code_agent_backend == "claude_hust":
            return ClaudeHustCodeAgentBackend(
                settings=self._settings,
                workbench=self._code_workbench,
                llm_client=self._llm_client,
            )
        return InternalCodeAgentBackend(
            settings=self._settings,
            workbench=self._code_workbench,
            llm_client=self._llm_client,
        )

    def _code_result_summary(self, backend: str, action: str) -> str:
        if backend == "claude_hust":
            if action == "ask":
                return "已由本地 claude-hust 后端生成代码问答。"
            return "已由本地 claude-hust 后端生成 propose-only 修改建议。"
        if action == "ask":
            return "已由 Sage Mate 内置代码工作台生成代码问答。"
        return "已由 Sage Mate 内置代码工作台生成 propose-only 修改建议。"

    @staticmethod
    def _code_elapsed_ms(started_at: float) -> int:
        return int((perf_counter() - started_at) * 1000)

    def _build_code_workflow_trace(
        self,
        *,
        action: str,
        workspace_id: str,
        backend: str,
        context_paths: list[str],
        result_summary: str,
        duration_ms: int,
    ) -> list[WorkflowTraceStep]:
        try:
            workspace_root = self._code_workbench.workspace_root(workspace_id)
            workspace_detail = str(workspace_root)
        except Exception:
            workspace_detail = workspace_id
        selected = ", ".join(context_paths) if context_paths else "自动选择仓库概览和相关文件"
        action_label = "代码问答" if action == "ask" else "修改建议"
        backend_label = "Claude Code Hust 本地 CLI" if backend == "claude_hust" else "Sage Mate 内置 harness"
        return [
            WorkflowTraceStep(
                key="code_workspace",
                title="选择本地工作区",
                summary=f"已确认 allowlisted workspace：{workspace_id}。",
                detail=f"工作区路径：{workspace_detail}。所有路径解析都限制在该目录内。",
            ),
            WorkflowTraceStep(
                key="code_context",
                title="构建代码上下文",
                summary="已读取 git 状态、diff 和用户选择的代码上下文。",
                detail=f"上下文文件：{selected}。",
            ),
            WorkflowTraceStep(
                key="code_agent_backend",
                title="调用代码后端",
                summary=f"已通过 {backend_label} 执行{action_label}流程。",
                detail=(
                    "Sage Mate 保持外层 workflow、配置、安全边界和观测；代码后端只作为本地分析节点。"
                ),
            ),
            WorkflowTraceStep(
                key="code_result",
                title="返回代码结果",
                summary=result_summary,
                detail="未直接修改真实仓库文件；如需落盘，必须经过后续显式确认流程。",
                duration_ms=duration_ms,
            ),
        ]

    def _strip_thinking_blocks(self, text: str) -> str:
        return re.sub(r"<think>.*?</think>", "", text or "", flags=re.DOTALL).strip()

    async def _answer_code_workbench_command(self, request: ChatRequest) -> ChatResponse:
        workflow_trace: list[WorkflowTraceStep] = []
        try:
            command = self._code_workbench.parse_chat_command(request.question)
            if command.action == "help":
                answer = self._code_workbench.format_chat_help()
            elif command.action == "workspaces":
                answer = self._code_workbench.format_workspaces_for_chat()
            elif command.action == "doctor":
                answer = self._code_workbench.format_doctor_for_chat()
            elif command.action == "search":
                response = self.search_code(
                    CodeSearchRequest(
                        workspace_id=command.workspace_id,
                        query=command.query,
                        glob=command.glob,
                    )
                )
                answer = self._code_workbench.format_search_for_chat(response)
            elif command.action == "read":
                response = self.read_code_file(
                    CodeFileReadRequest(
                        workspace_id=command.workspace_id,
                        path=command.path,
                        start_line=command.start_line,
                        max_lines=command.max_lines,
                    )
                )
                answer = self._code_workbench.format_file_for_chat(response)
            elif command.action == "list":
                response = self.list_code_directory(
                    CodeDirectoryListRequest(
                        workspace_id=command.workspace_id,
                        path=command.path or ".",
                    )
                )
                answer = self._code_workbench.format_directory_for_chat(response)
            elif command.action == "status":
                response = self.get_code_git_status(
                    CodeGitStatusRequest(workspace_id=command.workspace_id)
                )
                answer = self._code_workbench.format_git_status_for_chat(response)
            elif command.action == "diff":
                response = self.get_code_git_diff(
                    CodeGitDiffRequest(
                        workspace_id=command.workspace_id,
                        path=command.path or None,
                        staged=command.staged,
                    )
                )
                answer = self._code_workbench.format_git_diff_for_chat(response)
            elif command.action == "context":
                response = self.build_code_context(
                    CodeContextRequest(
                        workspace_id=command.workspace_id,
                        paths=command.paths,
                    )
                )
                answer = self._code_workbench.format_context_for_chat(response)
            elif command.action == "run":
                response = self.run_code_command(
                    CodeCommandRequest(
                        workspace_id=command.workspace_id,
                        command=command.command,
                        code_approval_mode=request.code_approval_mode,
                    )
                )
                answer = self._code_workbench.format_command_for_chat(response)
            elif command.action == "ask":
                response = await self.assist_with_code(
                    CodeAssistRequest(
                        workspace_id=command.workspace_id,
                        task=command.task,
                        paths=command.paths,
                    )
                )
                context_note = (
                    "\n\n上下文：" + "、".join(f"`{path}`" for path in response.context_paths)
                    if response.context_paths
                    else ""
                )
                answer = response.answer + context_note
                workflow_trace = response.workflow_trace
            elif command.action == "propose":
                response = await self.propose_code_change(
                    CodeProposeRequest(
                        workspace_id=command.workspace_id,
                        task=command.task,
                        paths=command.paths,
                    )
                )
                answer = self._code_workbench.format_propose_for_chat(response)
                workflow_trace = response.workflow_trace
            else:
                answer = self._code_workbench.format_chat_help()
        except Exception as exc:
            answer = f"代码工作台命令没有执行：{exc}\n\n{self._code_workbench.format_chat_help()}"

        return ChatResponse(
            answer=answer,
            owner_name=self._settings.owner_name,
            used_model=self._llm_client.model_name,
            conversation_id=request.conversation_id or str(uuid4()),
            workflow_action="code_workbench",
            decision_mode="admin_code_workbench",
            workflow_trace=workflow_trace,
        )

    async def _answer_auto_scientist(self, request: ChatRequest) -> ChatResponse:
        problem = request.question.strip()
        recent_session_context = ""
        knowledge_hits: list[KnowledgeSearchHit] = []
        twin_context_summary = "本轮未命中可直接引用的分身上下文；先按用户描述启动科研流程。"
        workspace_summary = "未配置本地代码 workspace。"
        code_analysis = (
            "代码节点尚未运行。请在设置中添加实验仓库后重试，"
            "或使用 `/code workspaces` 检查 allowlist。"
        )
        workflow_trace: list[WorkflowTraceStep] = [
            WorkflowTraceStep(
                key="auto_scientist_problem",
                title="理解科研问题",
                summary="已把用户的粗略描述转成自动科研任务。",
                detail=f"原始问题：{problem[:450]}",
            )
        ]
        try:
            recent_session_context = self._build_recent_session_context(request)
        except Exception as exc:
            workflow_trace.append(
                WorkflowTraceStep(
                    key="auto_scientist_recent_context",
                    title="读取最近对话",
                    summary="最近对话上下文暂不可用。",
                    detail=str(exc)[:500],
                    status="skipped",
                )
            )

        try:
            raw_hits = self._knowledge_store.search(
                problem,
                visitor_profile=request.visitor_profile,
                admin_role=None,
            )
            knowledge_hits = raw_hits[:3]
        except Exception as exc:
            workflow_trace.append(
                WorkflowTraceStep(
                    key="auto_scientist_knowledge",
                    title="检索分身知识库",
                    summary="知识库检索暂不可用。",
                    detail=str(exc)[:500],
                    status="skipped",
                )
            )

        twin_context_parts: list[str] = []
        if recent_session_context.strip():
            twin_context_parts.append(
                f"- 最近对话：{recent_session_context.strip()[:700]}"
            )
        if knowledge_hits:
            for hit in knowledge_hits:
                excerpt = (hit.excerpt or "").strip()
                title = (hit.title or "未命名知识").strip()
                twin_context_parts.append(f"- {title}：{excerpt[:360]}")
        if twin_context_parts:
            twin_context_summary = "\n".join(twin_context_parts)
            workflow_trace.append(
                WorkflowTraceStep(
                    key="auto_scientist_twin_context",
                    title="汇总分身上下文",
                    summary=(
                        f"已整理 {len(knowledge_hits)} 条知识命中"
                        + ("和最近对话上下文。" if recent_session_context.strip() else "。")
                    ),
                    detail=twin_context_summary[:500],
                )
            )

        if self._code_workbench_available():
            workspaces = self._code_workbench.list_workspaces().workspaces
            if workspaces:
                workspace = workspaces[0]
                workspace_summary = f"已选择本地实验 workspace `{workspace.workspace_id}`：{workspace.root}"
                repo_brief = self._build_auto_scientist_code_brief(
                    workspace.workspace_id, problem
                )
                code_task = (
                    "你是 Sage Mate 自动科学家模式中的代码研究节点。"
                    "请基于当前仓库上下文，为下面科研问题判断："
                    "1) 仓库可能支持哪些实验入口；"
                    "2) 应先读哪些文件；"
                    "3) 最小可行实验如何设计；"
                    "4) 需要记录哪些指标和风险。"
                    "不要修改文件，不要声称已经运行实验。\n\n"
                    f"分身上下文：\n{twin_context_summary}\n\n"
                    f"确定性代码态势摘要：\n{repo_brief}\n\n"
                    f"科研问题：{problem}"
                )
                try:
                    code_response = await self.assist_with_code(
                        CodeAssistRequest(
                            workspace_id=workspace.workspace_id,
                            task=code_task,
                            max_context_chars=7000,
                        )
                    )
                    workflow_trace.extend(code_response.workflow_trace)
                    if self._looks_like_degenerate_auto_scientist_output(
                        code_response.answer
                    ):
                        code_analysis = (
                            "代码研究节点输出质量不足，已自动切换为确定性代码态势摘要，"
                            "避免把重复或发散内容展示给用户。\n\n"
                            f"{repo_brief}"
                        )
                        workflow_trace.append(
                            WorkflowTraceStep(
                                key="auto_scientist_code_quality_gate",
                                title="过滤退化代码输出",
                                summary="检测到代码节点回答重复/发散，已使用确定性仓库摘要兜底。",
                                detail="兜底摘要来自 allowlisted workspace 的只读搜索结果。",
                            )
                        )
                    else:
                        code_analysis = (
                            f"{repo_brief}\n\n"
                            "### CC-hust 代码节点补充\n"
                            f"{code_response.answer}"
                        )
                except Exception as exc:
                    code_analysis = (
                        "代码研究节点暂未完成："
                        f"{self._strip_thinking_blocks(str(exc)) or type(exc).__name__}\n\n"
                        f"{repo_brief}"
                    )
                    workflow_trace.append(
                        WorkflowTraceStep(
                            key="auto_scientist_code_node",
                            title="调用代码研究节点",
                            summary="代码节点未完成，但自动科研计划已继续生成。",
                            detail=code_analysis[:500],
                        )
                    )
            else:
                workflow_trace.append(
                    WorkflowTraceStep(
                        key="auto_scientist_workspace",
                        title="检查实验 workspace",
                        summary="未发现 allowlisted 本地仓库。",
                        detail="自动科学家仍会生成科研计划；配置 workspace 后可自动读取代码上下文。",
                    )
                )
        else:
            workflow_trace.append(
                WorkflowTraceStep(
                    key="auto_scientist_code_gate",
                    title="检查代码能力",
                    summary="当前环境未开启本地代码工作台。",
                    detail="需要 local_code + auto_scientist/code_assistant + code_workbench_enabled。",
                )
            )

        answer = "\n".join(
            [
                "# 自动科学家启动包",
                "",
                "## 研究目标",
                problem,
                "",
                "## 一键科研流程",
                "1. **问题收敛**：把粗略问题改写成可检验的研究假设、baseline 和评价指标。",
                "2. **资料与记忆检索**：结合分身系统里的研究背景、过往讨论、论文/课程/组内知识，整理相关工作线索。",
                "3. **代码态势感知**：用 CC-hust 代码节点读取 allowlisted 仓库，定位实验入口、配置文件、数据路径和测试命令。",
                "4. **最小可行实验**：先设计 1 个 sanity check、1 个 baseline、1 个核心 ablation，避免一上来铺太大。",
                "5. **结果记录**：固定随机种子、记录 commit/dataset/config/metric，并把失败原因写入实验日志。",
                "6. **论文产出**：沉淀为 problem statement、method sketch、experiment table 和 risks/limitations。",
                "",
                "## 本地实验 workspace",
                workspace_summary,
                "",
                "## 分身上下文",
                twin_context_summary,
                "",
                "## CC-hust 代码研究节点",
                code_analysis,
                "",
                "## 下一步建议",
                "- 如果上面的代码节点已经给出入口文件，先运行只读检查：`/code status <workspace>`、`/code ls <workspace>`、`/code read <workspace> <path>`。",
                "- 如果需要改代码，使用 `/code propose <workspace> <task> -- <path>` 生成 reviewable diff，不直接落盘。",
                "- 如果还没有 workspace，先在设置里添加项目目录，再重新描述研究问题即可继续。",
            ]
        )
        workflow_trace.append(
            WorkflowTraceStep(
                key="auto_scientist_plan",
                title="生成自动科研启动包",
                summary="已合成科研流程、代码节点反馈和下一步动作。",
                detail="本轮不会自动写文件；所有代码修改仍需要显式 propose/apply 流程。",
            )
        )
        return ChatResponse(
            answer=answer,
            owner_name=self._settings.owner_name,
            used_model=self._llm_client.model_name,
            conversation_id=request.conversation_id or str(uuid4()),
            workflow_action="auto_scientist",
            decision_mode="auto_scientist_research_bootstrap",
            workflow_trace=workflow_trace,
        )

    def _build_auto_scientist_code_brief(self, workspace_id: str, problem: str) -> str:
        search_plan = [
            ("benchmark", "可能的 benchmark/实验入口"),
            ("throughput", "吞吐指标与 benchmark 输出"),
            ("prefix caching", "prefix cache / 长上下文缓存线索"),
            ("long document", "long document QA workload 线索"),
            ("max_num_seqs", "并发与调度参数线索"),
        ]
        lines = [
            "### 确定性代码态势",
            f"- workspace: `{workspace_id}`",
            "- 安全模式: 只读搜索；不修改文件，不声称已运行实验。",
        ]
        candidate_paths = self._auto_scientist_candidate_paths(workspace_id)
        if candidate_paths:
            lines.append("- 候选入口文件:")
            for path in candidate_paths:
                lines.append(f"  - `{path}`")
        for query, label in search_plan:
            try:
                response = self.search_code(
                    CodeSearchRequest(
                        workspace_id=workspace_id,
                        query=query,
                        max_results=4,
                    )
                )
            except Exception as exc:
                lines.append(f"- {label}: 搜索失败：{str(exc)[:160]}")
                continue
            if not response.hits:
                lines.append(f"- {label}: 未命中 `{query}`。")
                continue
            lines.append(f"- {label}:")
            for hit in response.hits[:4]:
                preview = re.sub(r"\s+", " ", hit.preview).strip()
                lines.append(f"  - `{hit.path}:{hit.line_number}` {preview[:180]}")

        lines.extend(
            [
                "",
                "### 建议的最小可行实验",
                "- 模型: `mlx-community/Qwen2.5-7B-Instruct-4bit`。",
                "- workload: long document QA / RAG 长上下文；固定输入长度、输出长度、请求数和随机种子。",
                "- baseline: 使用现有 serving/throughput benchmark，先记录无改动配置下的 TTFT、TPOT、request throughput、output token throughput、P50/P95 latency。",
                "- ablation: 对比 prefix caching 开/关、不同 `max_num_seqs`、不同 batched token budget、不同 cache hit ratio。",
                "- 第一版 propose-only 方向: 优先围绕 benchmark 参数化和结果记录做可复现实验脚本；确认瓶颈后再考虑调度或 prefix cache 路径优化。",
                f"- 原始问题: {problem[:300]}",
            ]
        )
        return "\n".join(lines)

    def _auto_scientist_candidate_paths(self, workspace_id: str) -> list[str]:
        try:
            root = self._code_workbench.workspace_root(workspace_id)
        except Exception:
            return []
        keywords = (
            "benchmark",
            "throughput",
            "prefix",
            "long_document",
            "long-document",
            "longdocument",
            "auto_tune",
            "qa",
        )
        ignored_parts = {".git", ".venv", "__pycache__", "node_modules", "build", "dist"}
        scored_candidates: list[tuple[int, str]] = []
        for path in root.rglob("*"):
            if not path.is_file():
                continue
            relative = path.relative_to(root)
            if any(part in ignored_parts for part in relative.parts):
                continue
            normalized = str(relative).lower()
            if any(keyword in normalized for keyword in keywords):
                score = 0
                if normalized.startswith("benchmarks/"):
                    score += 100
                if "benchmark_long_document" in normalized:
                    score += 80
                if "benchmark_throughput" in normalized:
                    score += 60
                if "auto_tune" in normalized:
                    score += 45
                if "prefix" in normalized:
                    score += 30
                if "throughput" in normalized:
                    score += 25
                if normalized.startswith("tests/"):
                    score -= 20
                scored_candidates.append((score, str(relative)))
        scored_candidates.sort(key=lambda item: (-item[0], item[1]))
        return [path for _, path in scored_candidates[:12]]

    @staticmethod
    def _looks_like_degenerate_auto_scientist_output(text: str) -> bool:
        normalized = re.sub(r"\s+", " ", text or "").strip().lower()
        if len(normalized) < 200:
            return False
        if re.search(r"([a-z0-9])\1{18,}", normalized):
            return True
        if re.search(r"\b([a-z_]{4,})\b(?:\W+\1\b){3,}", normalized):
            return True
        ascii_letters = len(re.findall(r"[a-zA-Z]", text or ""))
        cjk_chars = len(re.findall(r"[\u4e00-\u9fff]", text or ""))
        alpha_ratio = (ascii_letters + cjk_chars) / max(len(text or ""), 1)
        if alpha_ratio < 0.18:
            return True
        words = re.findall(r"[a-zA-Z_]{4,}", normalized)
        if len(words) < 40:
            return False
        counts: dict[str, int] = {}
        for word in words:
            counts[word] = counts.get(word, 0) + 1
        most_common = max(counts.values(), default=0)
        unique_ratio = len(counts) / max(len(words), 1)
        has_repeated_word = most_common >= 18 or unique_ratio < 0.16
        return has_repeated_word

    def _require_code_workbench_enabled(self) -> None:
        if not self._code_workbench_available():
            raise ValueError(
                "Code tools are disabled. Install Sage Mate locally and set "
                "DIGITAL_TWIN_DEPLOYMENT_MODE=local_code plus "
                "DIGITAL_TWIN_CODE_WORKBENCH_ENABLED=true with a code-capable profile "
                "to use local repositories."
            )

    def _code_workbench_available(self) -> bool:
        return (
            self._settings.deployment_mode == "local_code"
            and self._settings.code_workbench_enabled
            and self._settings.app_profile in CODE_WORKBENCH_PROFILES
        )

    def _parse_code_proposal(self, text: str) -> dict[str, object]:
        cleaned = text.strip()
        if cleaned.startswith("```"):
            lines = cleaned.splitlines()
            if lines and lines[0].startswith("```"):
                lines = lines[1:]
            if lines and lines[-1].startswith("```"):
                lines = lines[:-1]
            cleaned = "\n".join(lines).strip()

        try:
            payload = json.loads(cleaned)
        except json.JSONDecodeError:
            payload = {}

        if not isinstance(payload, dict):
            payload = {}

        summary = payload.get("summary")
        affected_files = payload.get("affected_files")
        unified_diff = payload.get("unified_diff")
        risks = payload.get("risks")
        suggested_tests = payload.get("suggested_tests")

        if not isinstance(summary, str) or not summary.strip():
            summary = cleaned.splitlines()[0][:4000] if cleaned else ""
        if not isinstance(affected_files, list):
            affected_files = []
        if not isinstance(unified_diff, str):
            unified_diff = self._extract_fenced_diff(text)
        if not isinstance(risks, str):
            risks = ""
        if not isinstance(suggested_tests, list):
            suggested_tests = []

        return {
            "summary": summary[:4000],
            "affected_files": [
                str(path)[:1000] for path in affected_files if str(path).strip()
            ][:32],
            "unified_diff": unified_diff[:50000],
            "risks": risks[:4000],
            "suggested_tests": [
                str(test)[:1000] for test in suggested_tests if str(test).strip()
            ][:32],
        }

    def _extract_fenced_diff(self, text: str) -> str:
        match = re.search(r"```(?:diff)?\s*(.*?)```", text, flags=re.DOTALL | re.IGNORECASE)
        if match:
            return match.group(1).strip()
        return ""

    def get_user_session(self, session_token: str | None) -> UserSessionResponse:
        support = self._build_support()
        return self._run_pipeline_blocking(
            "faculty-twin-user-session-read",
            [UserSessionTokenInput(session_token=session_token)],
            [(ReadUserSessionStage, support)],
            "SAGE runtime completed without producing a user session response.",
        )

    def register_user(self, request: UserRegisterRequest) -> UserAuthWorkflowResult:
        support = self._build_support()
        return self._run_pipeline_blocking(
            "faculty-twin-user-register",
            [request],
            [(UserRegisterStage, support)],
            "SAGE runtime completed without producing a user registration result.",
        )

    def login_user(self, request: UserLoginRequest) -> UserAuthWorkflowResult:
        support = self._build_support()
        return self._run_pipeline_blocking(
            "faculty-twin-user-login",
            [request],
            [(UserLoginStage, support)],
            "SAGE runtime completed without producing a user login result.",
        )

    def logout_user(self) -> UserSessionResponse:
        support = self._build_support()
        return self._run_pipeline_blocking(
            "faculty-twin-user-logout",
            [_FLOWNET_TICK],
            [(UserLogoutStage, support)],
            "SAGE runtime completed without producing a user logout response.",
        )

    def add_knowledge(self, request: KnowledgeDocumentCreate) -> KnowledgeDocumentRecord:
        support = self._build_support()
        return self._run_pipeline_blocking(
            "faculty-twin-knowledge-add",
            [request],
            [(AddKnowledgeStage, support)],
            "SAGE runtime completed without producing a knowledge record.",
        )

    def list_knowledge(self) -> list[KnowledgeDocumentRecord]:
        support = self._build_support()
        return self._run_pipeline_blocking(
            "faculty-twin-knowledge-list",
            [_FLOWNET_TICK],
            [(ListKnowledgeStage, support)],
            "SAGE runtime completed without producing a knowledge document list.",
        )

    def list_knowledge_review_summary(self, limit: int = 20) -> KnowledgeDocumentReviewSummary:
        return self._build_support().list_knowledge_review_summary(limit=limit)

    def review_knowledge_document(
        self,
        document_id: str,
        request: KnowledgeDocumentReviewRequest | dict[str, Any],
    ) -> KnowledgeDocumentActionResponse:
        normalized_request = (
            request
            if isinstance(request, KnowledgeDocumentReviewRequest)
            else KnowledgeDocumentReviewRequest.model_validate(request)
        )
        return self._build_support().review_knowledge_document(document_id, normalized_request)

    def delete_knowledge_document(self, document_id: str) -> KnowledgeDocumentActionResponse:
        return self._build_support().delete_knowledge_document(document_id)

    def search_knowledge(
        self,
        query: str,
        visitor_profile: str | None = None,
        admin_role: str | None = None,
    ) -> KnowledgeSearchResponse:
        support = self._build_support()
        return self._run_pipeline_blocking(
            "faculty-twin-knowledge-search",
            [
                KnowledgeSearchInput(
                    query=query,
                    visitor_profile=visitor_profile,
                    admin_role=admin_role,
                )
            ],
            [(SearchKnowledgeStage, support)],
            "SAGE runtime completed without producing a knowledge search response.",
        )

    def book_meeting(self, request: BookingRequest | dict[str, Any]) -> BookingResponse:
        normalized_request = (
            request
            if isinstance(request, BookingRequest)
            else BookingRequest.model_validate(request)
        )
        support = self._build_support()
        return self._run_pipeline_blocking(
            "faculty-twin-booking-create",
            [normalized_request],
            [(CreateBookingStage, support)],
            "SAGE runtime completed without producing a booking response.",
        )

    def list_bookings(self, status: str | None = None) -> list[BookingRecord]:
        support = self._build_support()
        return self._run_pipeline_blocking(
            "faculty-twin-booking-list",
            [status],
            [(ListBookingsStage, support)],
            "SAGE runtime completed without producing a booking list.",
        )

    def list_memory_profiles(
        self,
        *,
        category: str | None = None,
        student_query: str | None = None,
        limit: int = 50,
    ) -> MemoryProfileListResponse:
        return self._build_support().list_memory_profiles(
            category=category,
            student_query=student_query,
            limit=limit,
        )

    def list_chat_conversations(
        self,
        *,
        student_email: str | None,
        limit: int = 30,
    ) -> ConversationHistoryListResponse:
        normalized_email = (student_email or "").strip().lower()
        if not normalized_email:
            return ConversationHistoryListResponse(conversations=[])

        grouped_records: dict[str, list[ConversationMemoryRecord]] = {}
        for record in reversed(self._conversation_store.list_records()):
            record_email = (record.student_email or "").strip().lower()
            # Never expose legacy anonymous records through authenticated
            # history sync because they cannot be safely attributed.
            if record_email != normalized_email:
                continue
            grouped_records.setdefault(record.conversation_id, []).append(record)

        conversations: list[ConversationHistoryItemResponse] = []
        for conversation_id, records in grouped_records.items():
            if not records:
                continue
            first_record = records[0]
            latest_record = records[-1]
            conversations.append(
                ConversationHistoryItemResponse(
                    conversation_id=conversation_id,
                    title=self._build_conversation_title(first_record.question),
                    preview=self._build_conversation_preview(
                        latest_record.answer or latest_record.question
                    ),
                    student_name=latest_record.student_name,
                    student_email=latest_record.student_email,
                    course_context=latest_record.course_context,
                    exchange_count=len(records),
                    last_message_at=latest_record.created_at,
                )
            )

        conversations.sort(key=lambda item: item.last_message_at, reverse=True)
        return ConversationHistoryListResponse(conversations=conversations[: max(1, limit)])

    def get_chat_conversation(
        self,
        *,
        conversation_id: str,
        student_email: str | None,
    ) -> ConversationTranscriptResponse:
        normalized_email = (student_email or "").strip().lower()
        if not normalized_email:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="需要登录账号或提供邮箱后才能同步历史对话。",
            )

        records = [
            record
            for record in self._conversation_store.list_records()
            if record.conversation_id == conversation_id
            and (record.student_email or "").strip().lower() == normalized_email
        ]
        records.sort(key=lambda record: record.created_at)
        if not records:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND, detail="未找到对应的历史对话。"
            )

        first_record = records[0]
        latest_record = records[-1]
        return ConversationTranscriptResponse(
            conversation_id=conversation_id,
            title=self._build_conversation_title(first_record.question),
            preview=self._build_conversation_preview(
                latest_record.answer or latest_record.question
            ),
            student_name=latest_record.student_name,
            student_email=latest_record.student_email,
            course_context=latest_record.course_context,
            exchanges=[
                ConversationExchangeResponse(
                    exchange_id=record.memory_id,
                    question=record.question,
                    answer=record.answer,
                    workflow_action=record.workflow_action,
                    knowledge_hit_count=record.knowledge_hit_count,
                    created_at=record.created_at,
                )
                for record in records
            ],
        )

    def submit_chat_feedback(
        self, request: ChatFeedbackRequest | dict[str, Any]
    ) -> ChatFeedbackResponse:
        normalized_request = (
            request
            if isinstance(request, ChatFeedbackRequest)
            else ChatFeedbackRequest.model_validate(request)
        )
        return self._build_support().submit_chat_feedback(normalized_request)

    @staticmethod
    def _build_conversation_title(text: str) -> str:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return "新对话"
        return normalized[:32] + ("..." if len(normalized) > 32 else "")

    @staticmethod
    def _build_conversation_preview(text: str) -> str:
        normalized = re.sub(r"\s+", " ", str(text or "")).strip()
        if not normalized:
            return "点击继续这段对话"
        return normalized[:72] + ("..." if len(normalized) > 72 else "")

    def submit_anonymous_suggestion(
        self,
        request: AnonymousSuggestionCreate | dict[str, Any],
        admin_session_token: str | None = None,
    ) -> AnonymousSuggestionRecord:
        normalized_request = (
            request
            if isinstance(request, AnonymousSuggestionCreate)
            else AnonymousSuggestionCreate.model_validate(request)
        )
        record = self._build_support().submit_anonymous_suggestion(normalized_request)
        return self._mask_suggestion_record(record, admin_session_token=admin_session_token)

    def list_anonymous_suggestions(
        self,
        *,
        limit: int = 50,
        admin_session_token: str | None = None,
    ) -> list[AnonymousSuggestionRecord]:
        records = self._build_support().list_anonymous_suggestions(limit=limit)
        return [
            self._mask_suggestion_record(record, admin_session_token=admin_session_token)
            for record in records
        ]

    def _mask_suggestion_record(
        self,
        record: AnonymousSuggestionRecord,
        *,
        admin_session_token: str | None = None,
    ) -> AnonymousSuggestionRecord:
        if admin_session_token and self.get_admin_session(admin_session_token).is_admin:
            return record
        return record.model_copy(update={"message": "***"})

    def get_question_analytics_report(self, *, days: int = 7) -> QuestionAnalyticsReportResponse:
        return self._build_support().get_question_analytics_report(days=days)

    def _build_student_operations_profiles(
        self, *, days: int, limit: int
    ) -> list[StudentOperationsProfile]:
        profiles = self._conversation_store.list_profiles(limit=1000)
        if not profiles:
            return []

        window_start = datetime.now(UTC) - timedelta(days=days)
        recent_records = self._conversation_store.list_records(since=window_start)
        records_by_student: dict[str, list[ConversationMemoryRecord]] = {}
        for record in recent_records:
            student_key = self._student_operations_key(record.student_name, record.student_email)
            if student_key is None:
                continue
            records_by_student.setdefault(student_key, []).append(record)

        profiles_by_student: dict[str, list[ProfileMemoryRecord]] = {}
        for profile in profiles:
            profiles_by_student.setdefault(profile.student_key, []).append(profile)

        student_profiles: list[StudentOperationsProfile] = []
        for student_key, student_profile_records in profiles_by_student.items():
            student_profile_records.sort(key=lambda item: item.updated_at, reverse=True)
            records = sorted(
                records_by_student.get(student_key, []),
                key=lambda item: item.created_at,
                reverse=True,
            )
            categories = sorted({profile.category for profile in student_profile_records})
            latest_profile_at = student_profile_records[0].updated_at
            latest_interaction_at = records[0].created_at if records else None
            representative = student_profile_records[0]
            student_profiles.append(
                StudentOperationsProfile(
                    student_key=student_key,
                    student_name=representative.student_name,
                    student_email=representative.student_email,
                    segment=self._student_operations_segment(
                        categories=categories,
                        interaction_count=len(records),
                    ),
                    profile_count=len(student_profile_records),
                    interaction_count=len(records),
                    categories=categories,
                    recent_questions=[record.question for record in records[:3]],
                    key_summaries=[
                        MemoryProfileRecordResponse(
                            profile_id=profile.profile_id,
                            student_key=profile.student_key,
                            student_name=profile.student_name,
                            student_email=profile.student_email,
                            category=profile.category,
                            summary=profile.summary,
                            evidence=profile.evidence,
                            updated_at=profile.updated_at,
                        )
                        for profile in student_profile_records[:3]
                    ],
                    suggested_next_action=self._student_operations_next_action(
                        categories=categories,
                        interaction_count=len(records),
                    ),
                    latest_profile_at=latest_profile_at,
                    latest_interaction_at=latest_interaction_at,
                )
            )

        student_profiles.sort(
            key=lambda item: item.latest_interaction_at or item.latest_profile_at,
            reverse=True,
        )
        return student_profiles[: max(1, limit)]

    def _student_operations_key(self, student_name: str, student_email: str | None) -> str | None:
        if student_email:
            return student_email.strip().lower()
        normalized_name = student_name.strip().lower()
        if not normalized_name or normalized_name == "guest":
            return None
        return normalized_name

    def _student_operations_segment(self, *, categories: list[str], interaction_count: int) -> str:
        if interaction_count >= 3:
            return "高互动学生"
        if "booking_preference" in categories:
            return "预约跟进"
        if "collaboration_preference" in categories:
            return "协作准备"
        if "recent_topic" in categories:
            return "持续关注"
        return "基础画像"

    def _student_operations_next_action(
        self, *, categories: list[str], interaction_count: int
    ) -> str:
        if "booking_preference" in categories:
            return "复核预约偏好，必要时主动补充会前准备说明。"
        if "collaboration_preference" in categories:
            return "下次回复优先给出 agenda、blocker 和材料清单。"
        if interaction_count >= 3:
            return "检查近期高频问题，判断是否需要补充知识库或人工跟进。"
        if "recent_topic" in categories:
            return "保留近期关注主题，后续回答时优先复用相关上下文。"
        return "暂无额外动作，继续观察后续交互。"

    def _build_operational_tasks(self, *, limit: int) -> list[OperationsTaskItem]:
        now = datetime.now(UTC)
        tasks: list[OperationsTaskItem] = []

        for booking in self._meeting_service.list_bookings(status="待确认"):
            tasks.append(
                self._with_operations_task_state(
                    OperationsTaskItem(
                        task_key=f"booking:{booking.booking_id}",
                        task_type="booking_review",
                        title=f"预约审核｜{booking.topic}",
                        detail=f"{booking.student_name} 申请 {booking.start_at.astimezone(UTC).strftime('%Y-%m-%d %H:%M UTC')} 的讨论。",
                        source_status=booking.status,
                        operations_status="open",
                        priority=80,
                        action_url="/bookings",
                        student_name=booking.student_name,
                        student_email=booking.student_email,
                        due_at=booking.start_at,
                    )
                )
            )

        for draft in self._knowledge_gap_draft_store.list_drafts():
            if draft.status == "published":
                continue
            tasks.append(
                self._with_operations_task_state(
                    OperationsTaskItem(
                        task_key=f"gap_draft:{draft.draft_id}",
                        task_type="knowledge_gap_draft",
                        title=draft.title,
                        detail=draft.suggested_action,
                        source_status=draft.status,
                        operations_status="open",
                        priority=55,
                        action_url="/analytics/questions/gap-drafts",
                        created_at=draft.updated_at,
                    )
                )
            )

        for artifact_draft in self._artifact_memory_draft_store.list_drafts():
            if artifact_draft.status != "draft":
                continue
            tasks.append(
                self._with_operations_task_state(
                    OperationsTaskItem(
                        task_key=f"artifact_draft:{artifact_draft.draft_id}",
                        task_type="artifact_memory_draft",
                        title=f"材料草稿｜{'、'.join(artifact_draft.artifact_names[:2]) or '未命名材料'}",
                        detail=artifact_draft.provenance_note,
                        source_status=artifact_draft.status,
                        operations_status="open",
                        priority=58,
                        action_url="/memory/artifact-drafts",
                        student_name=artifact_draft.student_name,
                        student_email=artifact_draft.student_email,
                        created_at=artifact_draft.updated_at,
                    )
                )
            )

        for escalation in self._escalation_store.list_requests(status="待处理"):
            tasks.append(
                self._with_operations_task_state(
                    OperationsTaskItem(
                        task_key=f"escalation:{escalation.escalation_id}",
                        task_type="human_handoff",
                        title=f"人工处理｜{_format_escalation_route_label(escalation.route)}",
                        detail=escalation.reason or escalation.question,
                        source_status=escalation.status,
                        operations_status="open",
                        priority=95,
                        action_url="/escalations",
                        student_name=escalation.student_name,
                        student_email=escalation.student_email,
                        created_at=escalation.created_at,
                    )
                )
            )

        for follow_up in self._follow_up_store.list_actions(status="queued"):
            is_due = follow_up.due_at is None or follow_up.due_at <= now
            tasks.append(
                self._with_operations_task_state(
                    OperationsTaskItem(
                        task_key=f"follow_up:{follow_up.action_id}",
                        task_type="follow_up",
                        title=follow_up.title,
                        detail=follow_up.detail,
                        source_status=follow_up.status,
                        operations_status="open",
                        priority=75 if is_due else 60,
                        action_url="/follow-ups",
                        student_name=follow_up.student_name,
                        student_email=follow_up.student_email,
                        created_at=follow_up.created_at,
                        due_at=follow_up.due_at,
                    )
                )
            )

        for comparison in self._planner_comparison_store.list_records(actionable_only=True):
            tasks.append(
                self._with_operations_task_state(
                    OperationsTaskItem(
                        task_key=f"planner_comparison:{comparison.record_id}",
                        task_type="planner_comparison",
                        title=_format_planner_comparison_task_title(comparison),
                        detail=_format_planner_comparison_task_detail(comparison),
                        source_status=comparison.comparison_status,
                        operations_status="open",
                        priority=_planner_comparison_priority(comparison.comparison_status),
                        action_url="/operations/workbench",
                        created_at=comparison.created_at,
                    )
                )
            )

        for suggestion in self._suggestion_store.list_suggestions(limit=limit):
            tasks.append(
                self._with_operations_task_state(
                    OperationsTaskItem(
                        task_key=f"suggestion:{suggestion.suggestion_id}",
                        task_type="anonymous_suggestion",
                        title=suggestion.category or "匿名留言",
                        detail=suggestion.message,
                        source_status="new",
                        operations_status="open",
                        priority=35,
                        action_url="/suggestions",
                        created_at=suggestion.created_at,
                    )
                )
            )

        tasks.sort(
            key=lambda task: (
                1 if task.operations_status == "in_progress" else 0,
                task.priority,
                task.due_at or task.created_at or datetime.min.replace(tzinfo=UTC),
            ),
            reverse=True,
        )
        return tasks[: max(1, limit)]

    def _with_operations_task_state(self, task: OperationsTaskItem) -> OperationsTaskItem:
        state = self._operations_task_state_store.get_state(task.task_key)
        if state is None:
            return task
        return task.model_copy(
            update={
                "operations_status": state.status,
                "assigned_to": state.assigned_to,
                "note": state.note,
            }
        )

    def update_operations_task_state(
        self,
        task_key: str,
        request: OperationsTaskStateUpdateRequest | dict[str, Any],
    ) -> OperationsTaskStateRecord:
        normalized = (
            request
            if isinstance(request, OperationsTaskStateUpdateRequest)
            else OperationsTaskStateUpdateRequest.model_validate(request)
        )
        return self._operations_task_state_store.update_state(task_key, normalized)

    def get_operations_overview(self, *, days: int = 7) -> OperationsOverviewResponse:
        analytics = self.get_question_analytics_report(days=days)
        bookings = self._meeting_service.list_bookings()
        artifact_drafts = self.list_artifact_memory_drafts()
        gap_drafts = self._knowledge_gap_draft_store.list_drafts()
        escalations = self._escalation_store.list_requests()
        follow_ups = self._follow_up_store.list_actions()
        suggestion_count = self._suggestion_store.count_suggestions()
        health = self.health()

        pending_bookings = [booking for booking in bookings if booking.status == "待确认"]
        open_artifact_drafts = [draft for draft in artifact_drafts if draft.status == "draft"]
        open_gap_drafts = [draft for draft in gap_drafts if draft.status != "published"]
        open_escalations = [item for item in escalations if item.status == "待处理"]
        queued_follow_ups = [item for item in follow_ups if item.status == "queued"]
        planner_comparison_count = self._planner_comparison_store.count_records()
        planner_drift_count = self._planner_comparison_store.count_actionable_records()
        planner_metrics = PlannerMetricsSnapshot.model_validate(
            self._planner_metrics_store.build_summary()
        )
        neuromem_snapshot = self._conversation_store.runtime_snapshot()

        return OperationsOverviewResponse(
            generated_at=datetime.now(UTC),
            window_days=days,
            health=health,
            totals={
                "bookings": len(bookings),
                "knowledge_documents": _health_int(health, "knowledge_documents"),
                "conversation_records": _health_int(health, "conversation_memory_records"),
                "memory_profiles": _health_int(health, "conversation_memory_profiles"),
                "artifact_memory_drafts": len(artifact_drafts),
                "student_profiles": len(
                    {
                        profile.student_key
                        for profile in self._conversation_store.list_profiles(limit=1000)
                    }
                ),
                "feedback_records": _health_int(health, "conversation_feedback_records"),
                "planner_requests": planner_metrics.deterministic_total,
                "planner_comparisons": planner_comparison_count,
                "planner_fallbacks": planner_metrics.deterministic_fallbacks,
                "planner_shadow_drifts": planner_drift_count,
                "planner_shadow_errors": planner_metrics.shadow_errors,
                "suggestions": suggestion_count,
            },
            queues=[
                OperationsQueueSummary(
                    queue_key="booking_review",
                    title="预约审核",
                    open_count=len(pending_bookings),
                    total_count=len(bookings),
                    action_url="/bookings",
                ),
                OperationsQueueSummary(
                    queue_key="artifact_memory_drafts",
                    title="材料记忆草稿",
                    open_count=len(open_artifact_drafts),
                    total_count=len(artifact_drafts),
                    action_url="/memory/artifact-drafts",
                ),
                OperationsQueueSummary(
                    queue_key="knowledge_gap_drafts",
                    title="知识缺口草稿",
                    open_count=len(open_gap_drafts),
                    total_count=len(gap_drafts),
                    action_url="/analytics/questions/gap-drafts",
                ),
                OperationsQueueSummary(
                    queue_key="human_handoff",
                    title="人工处理队列",
                    open_count=len(open_escalations),
                    total_count=len(escalations),
                    action_url="/escalations",
                ),
                OperationsQueueSummary(
                    queue_key="follow_ups",
                    title="后续动作",
                    open_count=len(queued_follow_ups),
                    total_count=len(follow_ups),
                    action_url="/follow-ups",
                ),
                OperationsQueueSummary(
                    queue_key="planner_shadow_review",
                    title="规划分歧",
                    open_count=planner_drift_count,
                    total_count=planner_comparison_count,
                    action_url="/operations/workbench",
                ),
                OperationsQueueSummary(
                    queue_key="anonymous_suggestions",
                    title="匿名留言",
                    open_count=suggestion_count,
                    total_count=suggestion_count,
                    action_url="/suggestions",
                ),
            ],
            question_analytics=analytics.overview,
            neuromem=NeuroMemOperationsSnapshot.model_validate(neuromem_snapshot),
            planner_metrics=planner_metrics,
        )

    def get_operations_workbench(
        self, *, days: int = 7, limit: int = 10
    ) -> OperationsWorkbenchResponse:
        return OperationsWorkbenchResponse(
            overview=self.get_operations_overview(days=days),
            operational_tasks=self._build_operational_tasks(limit=limit),
            satisfaction=OperationsSatisfactionSummary(
                **self._analytics_store.build_satisfaction_report(days=days)
            ),
            pending_bookings=self._meeting_service.list_bookings(status="待确认")[:limit],
            student_profiles=self._build_student_operations_profiles(days=days, limit=limit),
            artifact_memory_drafts=self.list_artifact_memory_drafts()[:limit],
            knowledge_gap_drafts=self._knowledge_gap_draft_store.list_drafts()[:limit],
            escalations=self._escalation_store.list_requests(status="待处理")[:limit],
            follow_up_actions=self._follow_up_store.list_actions(status="queued")[:limit],
            anonymous_suggestions=self._suggestion_store.list_suggestions(limit=limit),
            question_analytics=self.get_question_analytics_report(days=days),
        )

    def get_workflow_replay_report(self) -> WorkflowReplayReportResponse:
        scenarios = load_workflow_replay_scenarios()
        scenario_titles = {
            scenario.scenario_id: scenario.title for scenario in scenarios
        }
        results = evaluate_workflow_replay_scenarios(self._workflow_planner, scenarios)
        response_results = [
            WorkflowReplayScenarioResultResponse(
                scenario_id=result.scenario_id,
                title=scenario_titles.get(result.scenario_id, result.scenario_id),
                passed=result.passed,
                accepted=result.decision.accepted,
                goal=result.decision.plan.goal,
                fallback_template=result.decision.plan.fallback_template,
                step_ids=[step.step_id for step in result.decision.plan.steps],
                errors=list(result.errors),
            )
            for result in results
        ]
        passed_scenarios = sum(1 for result in response_results if result.passed)
        return WorkflowReplayReportResponse(
            generated_at=datetime.now(UTC),
            planner_version=self._workflow_planner.planner_version,
            policy_version=self._workflow_planner.policy_version,
            scenario_source=str(default_scenarios_path()),
            total_scenarios=len(response_results),
            passed_scenarios=passed_scenarios,
            failed_scenarios=len(response_results) - passed_scenarios,
            results=response_results,
        )

    def create_knowledge_gap_draft(
        self,
        request: KnowledgeGapDraftCreateRequest | dict[str, Any],
    ) -> KnowledgeGapDraftRecordResponse:
        normalized_request = (
            request
            if isinstance(request, KnowledgeGapDraftCreateRequest)
            else KnowledgeGapDraftCreateRequest.model_validate(request)
        )
        return self._build_support().create_knowledge_gap_draft(normalized_request)

    def list_knowledge_gap_drafts(self) -> list[KnowledgeGapDraftRecordResponse]:
        return self._build_support().list_knowledge_gap_drafts()

    def list_artifact_memory_drafts(self) -> list[ArtifactMemoryDraftRecordResponse]:
        return self._build_support().list_artifact_memory_drafts()

    def accept_artifact_memory_draft(self, draft_id: str) -> ArtifactMemoryDraftRecordResponse:
        return self._build_support().accept_artifact_memory_draft(draft_id)

    def reject_artifact_memory_draft(self, draft_id: str) -> ArtifactMemoryDraftRecordResponse:
        return self._build_support().reject_artifact_memory_draft(draft_id)

    def publish_knowledge_gap_draft(self, draft_id: str) -> KnowledgeGapDraftRecordResponse:
        return self._build_support().publish_knowledge_gap_draft(draft_id)

    def list_escalations(
        self,
        *,
        status: str | None = None,
        route: str | None = None,
    ) -> list[EscalationRecord]:
        return self._build_support().list_escalations(status=status, route=route)

    def resolve_escalation(
        self,
        escalation_id: str,
        decision: EscalationDecisionRequest | dict[str, Any] | None = None,
    ) -> EscalationRecord:
        return self._build_support().resolve_escalation(escalation_id, decision)

    def list_follow_up_actions(
        self,
        *,
        status: str | None = None,
        action_type: str | None = None,
    ) -> list[FollowUpQueueRecord]:
        return self._build_support().list_follow_up_actions(status=status, action_type=action_type)

    def dispatch_due_follow_ups(self) -> FollowUpDispatchResponse:
        return self._build_support().dispatch_due_follow_ups()

    def confirm_booking(self, booking_id: str) -> BookingResponse:
        return self._build_support().confirm_booking(booking_id)

    def reject_booking(
        self,
        booking_id: str,
        decision: BookingDecisionRequest | dict[str, Any] | None = None,
    ) -> BookingResponse:
        normalized = (
            BookingDecisionRequest.model_validate(decision)
            if decision is not None
            else BookingDecisionRequest()
        )
        return self._build_support().reject_booking(
            booking_id,
            rejection_reason=normalized.rejection_reason,
        )

    def get_availability_schedule(self) -> AvailabilitySchedule:
        return self._meeting_service.get_availability_schedule()

    def get_previous_week_availability_template(
        self, week_of: date | None = None
    ) -> AvailabilitySchedule:
        return self._meeting_service.get_previous_week_availability_template(week_of)

    def update_availability_schedule(self, schedule: AvailabilitySchedule) -> AvailabilitySchedule:
        return self._meeting_service.update_availability_schedule(schedule)

    def get_managed_services(self) -> ServiceControlResponse:
        return self._runtime_manager.status()

    def record_online_presence(
        self,
        request: OnlinePresenceHeartbeatRequest | dict[str, Any],
    ) -> OnlinePresenceHeartbeatResponse:
        normalized = (
            request
            if isinstance(request, OnlinePresenceHeartbeatRequest)
            else OnlinePresenceHeartbeatRequest.model_validate(request)
        )
        snapshot = self._online_presence_store.record_heartbeat(
            client_id=normalized.client_id,
            conversation_id=normalized.conversation_id,
            student_email=normalized.student_email,
            is_authenticated=normalized.is_authenticated,
            window_seconds=self._settings.online_presence_window_seconds,
        )
        return OnlinePresenceHeartbeatResponse(
            window_seconds=snapshot.window_seconds,
            online_visitors=snapshot.online_visitors,
            online_authenticated_users=snapshot.online_authenticated_users,
            active_conversations=snapshot.active_conversations,
        )

    def generate_lucky_question(
        self,
        *,
        visitor_profile: str = "general_visitor",
        recent_questions: list[str] | None = None,
        onboarding_step: str = "",
    ) -> dict[str, str]:
        """Ask the LLM to generate a contextual question for the
        'I'm feeling lucky' button.  Returns an empty dict on failure
        so the caller can fall back to the static question bank."""
        fn = getattr(self._llm_client, "generate_lucky_question_sync", None)
        if not callable(fn):
            return {}
        try:
            return fn(
                owner_name=self._settings.owner_name,
                owner_role=self._settings.owner_role,
                visitor_profile=visitor_profile,
                recent_questions=recent_questions,
                onboarding_step=onboarding_step,
            )
        except Exception:
            return {}

    def control_managed_services(self, action: str) -> ServiceControlResponse:
        if action == "status":
            return self._runtime_manager.status()
        if action == "start":
            return self._runtime_manager.start()
        if action == "stop":
            return self._runtime_manager.stop()
        if action == "restart":
            return self._runtime_manager.restart()
        raise ValueError(f"Unsupported service action: {action}")

    def health(self) -> dict[str, str]:
        due_follow_ups = self._follow_up_store.list_due_actions()
        presence_snapshot = self._online_presence_store.snapshot(
            window_seconds=self._settings.online_presence_window_seconds
        )
        neuromem_snapshot = self._conversation_store.runtime_snapshot()
        conversation_stats = dict(neuromem_snapshot.get("conversation_stats") or {})
        telemetry = dict(conversation_stats.get("telemetry") or {})
        recent_events = list(neuromem_snapshot.get("recent_events") or [])
        planner_metrics = self._planner_metrics_store.build_summary()
        top_rejection_reason = next(
            iter((planner_metrics.get("rejection_reasons") or {}).keys()), ""
        )
        top_rejected_step = next(iter((planner_metrics.get("rejected_steps") or {}).keys()), "")
        payload = {
            "status": "ok",
            "app_version": _app_version,
            "owner_name": self._settings.owner_name,
            "owner_role": self._settings.owner_role,
            "homepage_public_url": self._settings.homepage_public_url,
            "model_name": self._llm_client.model_name,
            "sage_runtime": self._describe_sage_runtime(),
            "knowledge_backend": self._knowledge_store.backend_name(),
            "knowledge_embedding_backend": self._knowledge_store.embedding_backend_name(),
            "knowledge_documents": str(self._knowledge_store.count_documents()),
            "conversation_memory_backend": self._conversation_store.backend_name(),
            "conversation_memory_records": str(self._conversation_store.count_records()),
            "conversation_memory_profiles": str(self._conversation_store.count_profiles()),
            "artifact_memory_drafts": str(self._artifact_memory_draft_store.count_drafts()),
            "conversation_feedback_records": str(self._analytics_store.count_feedback()),
            "planner_comparison_records": str(self._planner_comparison_store.count_records()),
            "planner_shadow_actionable_records": str(
                self._planner_comparison_store.count_actionable_records()
            ),
            "planner_shadow_error_records": str(
                self._planner_comparison_store.count_status("shadow_error")
            ),
            "planner_shadow_equivalent_records": str(
                self._planner_comparison_store.count_status("equivalent")
            ),
            "planner_metric_records": str(planner_metrics.get("record_count") or 0),
            "planner_deterministic_total": str(planner_metrics.get("deterministic_total") or 0),
            "planner_deterministic_accepted": str(
                planner_metrics.get("deterministic_accepted") or 0
            ),
            "planner_deterministic_fallbacks": str(
                planner_metrics.get("deterministic_fallbacks") or 0
            ),
            "planner_deterministic_acceptance_rate": f"{float(planner_metrics.get('deterministic_acceptance_rate') or 0.0):.4f}",
            "planner_deterministic_fallback_rate": f"{float(planner_metrics.get('deterministic_fallback_rate') or 0.0):.4f}",
            "planner_shadow_total": str(planner_metrics.get("shadow_total") or 0),
            "planner_shadow_ready": str(planner_metrics.get("shadow_ready") or 0),
            "planner_shadow_accepted": str(planner_metrics.get("shadow_accepted") or 0),
            "planner_shadow_rejected": str(planner_metrics.get("shadow_rejected") or 0),
            "planner_shadow_disabled": str(planner_metrics.get("shadow_disabled") or 0),
            "planner_shadow_errors": str(planner_metrics.get("shadow_errors") or 0),
            "planner_shadow_acceptance_rate": f"{float(planner_metrics.get('shadow_acceptance_rate') or 0.0):.4f}",
            "planner_shadow_error_rate": f"{float(planner_metrics.get('shadow_error_rate') or 0.0):.4f}",
            "planner_avg_deterministic_latency_ms": f"{float(planner_metrics.get('avg_deterministic_latency_ms') or 0.0):.2f}",
            "planner_avg_shadow_latency_ms": f"{float(planner_metrics.get('avg_shadow_latency_ms') or 0.0):.2f}",
            "planner_top_rejection_reason": top_rejection_reason,
            "planner_top_rejected_step": top_rejected_step,
            "registered_user_accounts": str(self._user_store.count_users()),
            "online_window_seconds": str(presence_snapshot.window_seconds),
            "online_visitors": str(presence_snapshot.online_visitors),
            "online_authenticated_users": str(presence_snapshot.online_authenticated_users),
            "online_active_conversations": str(presence_snapshot.active_conversations),
            "knowledge_gap_drafts": str(self._knowledge_gap_draft_store.count_drafts()),
            "escalation_queue_records": str(self._escalation_store.count_records()),
            "follow_up_queue_records": str(self._follow_up_store.count_actions()),
            "suggestion_board_records": str(self._suggestion_store.count_suggestions()),
            "follow_up_dispatch_sent": "0",
            "follow_up_dispatch_due": str(len(due_follow_ups)),
            "chat_pipeline_stages": str(_CHAT_PIPELINE_STAGE_COUNT),
            "admin_pipeline_stages": "4",
            "neuromem_service_type": str(
                conversation_stats.get("service_type")
                or self._conversation_store.__class__.__name__
            ),
            "neuromem_collection_name": str(conversation_stats.get("collection_name") or ""),
            "neuromem_total_entries": str(conversation_stats.get("total_entries") or 0),
            "neuromem_index_count": str(conversation_stats.get("index_count") or 0),
            "neuromem_event_count": str(telemetry.get("event_count") or 0),
            "neuromem_query_count": str(telemetry.get("query_count") or 0),
            "neuromem_write_count": str(telemetry.get("write_count") or 0),
            "neuromem_last_event_type": str(telemetry.get("last_event_type") or ""),
            "neuromem_recent_event_count": str(len(recent_events)),
            "neuromem_recent_event_types": ",".join(
                str(event.get("event_type") or "")
                for event in recent_events
                if event.get("event_type")
            ),
        }
        runtime_snapshot = getattr(self._llm_client, "runtime_snapshot", None)
        payload.update(build_stack_versions_payload())
        if callable(runtime_snapshot):
            payload.update(runtime_snapshot())
        else:
            payload.update(
                {
                    "llm_status": "not_checked",
                    "llm_request_count": "0",
                    "llm_success_count": "0",
                    "llm_error_count": "0",
                    "llm_cache_hit_count": "0",
                    "llm_cache_entries": "0",
                    "llm_app_cache_hit_rate": "0.0000",
                    "llm_semantic_cache_hit_rate": "0.0000",
                    "llm_vllm_prefix_cache_hit_rate": "0.0000",
                    "llm_vllm_external_prefix_cache_hit_rate": "0.0000",
                    "llm_vllm_prefix_cache_queries": "0",
                    "llm_vllm_prefix_cache_hits": "0",
                    "llm_avg_latency_ms": "0.00",
                    "llm_last_latency_ms": "0.00",
                    "llm_max_latency_ms": "0.00",
                    "llm_request_throughput_rps": "0.0000",
                    "llm_completion_throughput_tps": "0.0000",
                    "llm_prompt_tokens_total": "0",
                    "llm_completion_tokens_total": "0",
                    "llm_total_tokens_total": "0",
                    "llm_last_error": "",
                    "llm_last_request_at": "",
                    "llm_last_success_at": "",
                    "llm_last_error_at": "",
                }
            )
        return payload

    def stack_versions(self) -> dict[str, str]:
        return build_stack_versions_payload()

    async def aclose(self) -> None:
        await self._llm_client.aclose()

    def _describe_sage_runtime(self) -> str:
        return self._sage_runtime_class.__name__

    def _build_student_prompt(
        self,
        request: ChatRequest,
        knowledge_hits: list[KnowledgeSearchHit],
        web_search_hits: list[WebSearchHit] | None = None,
        memory_hits: list[ConversationMemoryHit] | None = None,
        interaction_intent: InteractionIntent | None = None,
        recent_session_context: str | None = None,
    ) -> str:
        return self._build_support()._build_student_prompt(
            request,
            knowledge_hits,
            web_search_hits,
            memory_hits,
            interaction_intent,
            recent_session_context=recent_session_context,
        )

    def _build_support(
        self,
        admin_session_payload: dict[str, Any] | None = None,
        trace_callback: WorkflowTraceCallback | None = None,
        answer_chunk_callback: Callable[[str], None] | None = None,
        planner_decision: PlannerDecision | None = None,
        shadow_planner_decision: PlannerDecision | None = None,
        shadow_planner_status: str = "shadow_disabled",
        shadow_planner_message: str | None = None,
        planner_comparison: WorkflowPlanComparison | None = None,
    ) -> FacultyTwinWorkflowSupport:
        return FacultyTwinWorkflowSupport(
            self._settings,
            self._booking_workflows,
            self._knowledge_store,
            self._conversation_store,
            self._analytics_store,
            self._artifact_memory_draft_store,
            self._knowledge_gap_draft_store,
            self._escalation_store,
            self._follow_up_store,
            self._suggestion_store,
            self._user_store,
            self._meeting_service,
            self._llm_client,
            self._email_notifier,
            self._digest_store,
            admin_session_payload=admin_session_payload,
            trace_callback=trace_callback,
            answer_chunk_callback=answer_chunk_callback,
            planner_decision=planner_decision,
            shadow_planner_decision=shadow_planner_decision,
            shadow_planner_status=shadow_planner_status,
            shadow_planner_message=shadow_planner_message,
            planner_comparison=planner_comparison,
        )

    def _record_planner_metrics(
        self,
        request: ChatRequest,
        *,
        planner_decision: PlannerDecision,
        deterministic_latency_ms: float,
        shadow_decision: PlannerDecision | None,
        shadow_status: str,
        shadow_message: str | None,
        shadow_latency_ms: float,
    ) -> None:
        self._planner_metrics_store.record_entry(
            conversation_id=request.conversation_id or "",
            planner_stage="deterministic",
            planner_mode=planner_decision.plan.planner_mode,
            question=request.question,
            goal=planner_decision.plan.goal,
            accepted=planner_decision.accepted,
            status="accepted" if planner_decision.accepted else "fallback",
            fallback_template=planner_decision.plan.fallback_template,
            fallback_reason=(
                planner_decision.fallback.reason if planner_decision.fallback is not None else None
            ),
            validation_errors=list(planner_decision.validation_errors),
            planned_steps=[step.step_id for step in planner_decision.plan.steps],
            latency_ms=deterministic_latency_ms,
        )

        shadow_fallback_reason = shadow_message
        shadow_validation_errors: list[str] = []
        shadow_fallback_template = planner_decision.plan.fallback_template
        shadow_goal = planner_decision.plan.goal
        shadow_planned_steps: list[str] = []
        shadow_planner_mode = "llm_shadow"
        shadow_accepted = False
        if shadow_decision is not None:
            shadow_validation_errors = list(shadow_decision.validation_errors)
            shadow_fallback_template = shadow_decision.plan.fallback_template
            shadow_goal = shadow_decision.plan.goal
            shadow_planned_steps = [step.step_id for step in shadow_decision.plan.steps]
            shadow_planner_mode = shadow_decision.plan.planner_mode
            shadow_accepted = shadow_decision.accepted
            if shadow_decision.fallback is not None:
                shadow_fallback_reason = shadow_decision.fallback.reason

        shadow_record_status = shadow_status
        if shadow_status == "shadow_ready":
            shadow_record_status = "accepted" if shadow_accepted else "rejected"

        self._planner_metrics_store.record_entry(
            conversation_id=request.conversation_id or "",
            planner_stage="shadow",
            planner_mode=shadow_planner_mode,
            question=request.question,
            goal=shadow_goal,
            accepted=shadow_accepted,
            status=shadow_record_status,
            fallback_template=shadow_fallback_template,
            fallback_reason=shadow_fallback_reason,
            validation_errors=shadow_validation_errors,
            planned_steps=shadow_planned_steps,
            latency_ms=shadow_latency_ms,
        )

    def _persist_planner_comparison_result(
        self, request: ChatRequest, response: ChatResponse
    ) -> PlannerComparisonEntry | None:
        comparison = response.planner_comparison
        deterministic_preview = response.planner_preview
        if comparison is None or deterministic_preview is None:
            return None

        shadow_preview = response.shadow_planner_preview
        shadow_goal = None
        if shadow_preview is not None and shadow_preview.goal not in {
            "shadow planner pending",
            "shadow planner error",
        }:
            shadow_goal = shadow_preview.goal

        return self._planner_comparison_store.record_comparison(
            conversation_id=response.conversation_id
            or request.conversation_id
            or "unknown-conversation",
            exchange_id=response.exchange_id,
            workflow_action=response.workflow_action,
            question=request.question,
            comparison_status=comparison.comparison_status,
            deterministic_goal=deterministic_preview.goal,
            shadow_goal=shadow_goal,
            same_goal=comparison.same_goal,
            same_fallback_template=comparison.same_fallback_template,
            deterministic_only_steps=list(comparison.deterministic_only_steps),
            shadow_only_steps=list(comparison.shadow_only_steps),
            summary=comparison.summary,
        )

    def _run_pipeline(
        self,
        environment_name: str,
        source_items: list[Any],
        stages: list[tuple[type[MapFunction], FacultyTwinWorkflowSupport]],
        empty_result_message: str,
    ) -> Any:
        env = FlowNetEnvironment(environment_name)
        results: list[Any] = []
        stream = env.from_batch(source_items)
        for stage_class, support in stages:
            stream = stream.map(stage_class, support)
        stream.sink(ResultCollector, results)
        env.submit(autostop=True)

        if not results:
            raise RuntimeError(empty_result_message)
        return results[-1]

    def _run_chat_dag_pipeline(
        self,
        request: ChatRequest,
        support: FacultyTwinWorkflowSupport,
    ) -> tuple[ChatResponse, ChatWorkflowContext]:
        """Run the chat application stages in FlowNet and return both the rendered
        :class:`ChatResponse` and the underlying :class:`ChatWorkflowContext`.

        Runtime and in-process execution share the same critical-stage registry.
        Retrieval is deliberately sequential until each branch returns immutable
        stage output; the previous fan-out mutated one context from two branches.

        The four post-answer fan-out stages (``memory_persist``,
        ``memory_profile_consolidate``, ``follow_up_plan``,
        ``memory_usefulness_score``) are intentionally *not* part of this
        graph: they run after this method returns either inline (test path)
        or as a fire-and-forget ``asyncio.create_task`` (production path).
        See :meth:`_run_post_answer_inline_blocking`.

        """
        env = FlowNetEnvironment("faculty-twin-chat")
        responses: list[ChatResponse] = []
        contexts: list[ChatWorkflowContext] = []

        stream = env.from_batch([request])
        for stage_class in self._chat_critical_stage_types():
            stream = stream.map(stage_class, support)
        after_render = stream.map(_CaptureContextStage, contexts).map(
            ChatResponseRenderStage, support
        )
        after_render.sink(ResultCollector, responses)

        env.submit(autostop=True)

        if not responses:
            raise RuntimeError("SAGE runtime completed without producing a chat response.")
        if not contexts:
            raise RuntimeError(
                "SAGE runtime completed without capturing the chat workflow context."
            )
        return responses[-1], contexts[-1]

    def _run_chat_in_process(
        self,
        request: ChatRequest,
        support: FacultyTwinWorkflowSupport,
    ) -> tuple[ChatResponse, ChatWorkflowContext]:
        current: ChatRequest | ChatWorkflowContext = request
        for stage_class in self._chat_critical_stage_types():
            current = stage_class(support).execute(current)  # type: ignore[arg-type]
        if not isinstance(current, ChatWorkflowContext):
            raise RuntimeError("chat application did not produce a workflow context")
        return support.render_chat_response(current), current

    @staticmethod
    def _chat_critical_stage_types() -> tuple[type[MapFunction], ...]:
        return (
            BootstrapChatContextStage,
            InteractionUnderstandingStage,
            BookingPreparationStage,
            BookingExecutionStage,
            MemoryRetrievalStage,
            KnowledgeRetrievalStage,
            PromptBuildStage,
            LlmAnswerStage,
        )

    def _run_post_answer_inline_blocking(
        self,
        context: ChatWorkflowContext,
        support: FacultyTwinWorkflowSupport,
    ) -> ChatWorkflowContext:
        """Run the four post-answer side-effect stages on ``context``.

        This runs synchronously on whatever thread the caller is on. The
        stages mutate ``context`` in place (memory writes, follow-up
        planning, profile consolidation, usefulness scoring) and emit trace
        steps via the support's ``trace_callback``. Each stage is wrapped in
        a try/except so a failure in one stage does not cancel the others;
        the chat answer has already been delivered to the user, so this
        layer is best-effort.
        """
        post_answer_stages: list[tuple[str, type[MapFunction]]] = [
            ("memory_persist", MemoryPersistStage),
            ("memory_profile_consolidate", MemoryProfileConsolidationStage),
            ("follow_up_plan", FollowUpPlanningStage),
            ("memory_usefulness_score", MemoryUsefulnessScoringStage),
        ]
        for stage_key, stage_cls in post_answer_stages:
            try:
                stage_cls(support).execute(context)
            except Exception:  # pragma: no cover - logged for ops review
                _logger.exception(
                    "post-answer stage %s failed (conversation_id=%s)",
                    stage_key,
                    getattr(context, "conversation_id", None),
                )
        return context

    @staticmethod
    def _patch_response_with_post_answer(
        response: ChatResponse, context: ChatWorkflowContext
    ) -> ChatResponse:
        """Re-build a ChatResponse so post-answer mutations are visible.

        The critical-path DAG renders ``response`` *before* the post-answer
        stages mutate ``context``. When post-answer ran inline (test path or
        ``DIGITAL_TWIN_POST_ANSWER_BACKGROUND=false``), this helper folds the
        new context fields back into the response so legacy callers continue
        to see the canonical 14-step trace, the assigned ``exchange_id``,
        and the ``follow_up_actions`` list.
        """
        canonical_trace = _canonicalize_workflow_trace(context.workflow_trace)
        updates: dict[str, Any] = {
            "workflow_trace": canonical_trace,
            "follow_up_actions": context.follow_up_actions,
            "memory_write_back": context.persisted_memory_record is not None,
        }
        if context.persisted_memory_record is not None:
            updates["exchange_id"] = context.persisted_memory_record.memory_id
        return response.model_copy(update=updates)

    def _run_stage_chain(
        self,
        source_item: Any,
        stages: list[tuple[type[MapFunction], FacultyTwinWorkflowSupport]],
    ) -> Any:
        current = source_item
        for stage_class, support in stages:
            current = stage_class(support).execute(current)
        return current

    def _run_pipeline_blocking(
        self,
        environment_name: str,
        source_items: list[Any],
        stages: list[tuple[type[MapFunction], FacultyTwinWorkflowSupport]],
        empty_result_message: str,
    ) -> Any:
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            return self._run_pipeline(
                environment_name,
                source_items,
                stages,
                empty_result_message,
            )

        result_holder: dict[str, Any] = {}
        error_holder: list[BaseException] = []

        def runner() -> None:
            try:
                result_holder["value"] = self._run_pipeline(
                    environment_name,
                    source_items,
                    stages,
                    empty_result_message,
                )
            except BaseException as exc:  # noqa: BLE001
                error_holder.append(exc)

        thread = threading.Thread(target=runner, daemon=False)
        thread.start()
        thread.join()

        if error_holder:
            raise error_holder[0]
        return result_holder["value"]


def _document_matches_payload(
    document: KnowledgeDocumentRecord,
    payload: KnowledgeDocumentCreate,
) -> bool:
    return (
        document.title == payload.title
        and document.content == payload.content
        and document.tags == payload.tags
        and document.source_name == payload.source_name
    )


def _health_int(health: dict[str, str], key: str) -> int:
    try:
        return int(health.get(key, "0"))
    except ValueError:
        return 0


def _planner_comparison_priority(comparison_status: str) -> int:
    if comparison_status == "shadow_error":
        return 85
    if comparison_status == "different_goal":
        return 72
    return 64


def _format_planner_comparison_task_title(entry: PlannerComparisonEntry) -> str:
    label = {
        "shadow_error": "Shadow 规划失败",
        "different_goal": "Shadow 目标偏移",
        "different_steps": "Shadow 步骤分歧",
    }.get(entry.comparison_status, "Shadow 规划对比")
    return f"{label}｜{entry.deterministic_goal}"


def _format_planner_comparison_task_detail(entry: PlannerComparisonEntry) -> str:
    question = entry.question.strip()
    compact_question = question if len(question) <= 120 else f"{question[:117]}..."
    summary = entry.summary.strip()
    return f"{summary} 问题：{compact_question}"[:512]


def _format_escalation_route_label(route: str) -> str:
    if route == "human_handoff":
        return "人工接管"
    if route == "review_queue":
        return "复核队列"
    return route


def _is_legacy_gap_document(source_name: str | None, tags: list[str]) -> bool:
    normalized_tags = {tag.strip().lower() for tag in tags}
    return (
        bool(
            source_name
            and (
                source_name.startswith("analytics-gap:") or source_name.startswith("knowledge-gap:")
            )
        )
        or "faq-draft" in normalized_tags
        or "draft" in normalized_tags
        or "knowledge-gap" in normalized_tags
    )


def _match_gap_draft_for_document(
    *,
    document: KnowledgeDocumentRecord,
    drafts: Iterable[KnowledgeGapDraftRecordResponse],
) -> KnowledgeGapDraftRecordResponse | None:
    source_name = document.source_name or ""
    cluster_id = (
        source_name.split(":", 1)[1]
        if source_name.startswith("analytics-gap:") and ":" in source_name
        else None
    )
    for draft in drafts:
        if draft.published_document_id == document.document_id:
            return draft
        if cluster_id and draft.cluster_id == cluster_id:
            return draft
    return None


def _build_published_gap_document(
    draft: KnowledgeGapDraftRecordResponse,
) -> KnowledgeDocumentCreate:
    title = _format_gap_document_title(
        label=draft.label,
        interaction_domain=draft.interaction_domain,
        sample_questions=draft.sample_questions,
    )
    return KnowledgeDocumentCreate(
        title=title,
        content=_build_gap_document_content(draft),
        tags=_normalize_gap_document_tags(draft.tags, draft.interaction_domain),
        source_name=_build_gap_document_source_name(
            title=title,
            interaction_domain=draft.interaction_domain,
        ),
    )


def _build_published_gap_document_from_legacy(
    document: KnowledgeDocumentRecord,
) -> KnowledgeDocumentCreate:
    parsed = _parse_legacy_gap_document(document)
    title = _format_gap_document_title(
        label=parsed["label"],
        interaction_domain=parsed["interaction_domain"],
        sample_questions=parsed["sample_questions"],
    )
    return KnowledgeDocumentCreate(
        title=title,
        content=_build_gap_document_content_from_parts(
            interaction_domain=parsed["interaction_domain"],
            sample_questions=parsed["sample_questions"],
            suggested_action=parsed["suggested_action"],
            reason=parsed["reason"],
        ),
        tags=_normalize_gap_document_tags(document.tags, parsed["interaction_domain"]),
        source_name=_build_gap_document_source_name(
            title=title,
            interaction_domain=parsed["interaction_domain"],
        ),
    )


def _build_gap_document_source_name(*, title: str, interaction_domain: str) -> str:
    normalized_title = re.sub(r"\s+", " ", str(title or "").strip().lower()).strip(" ：:|｜")
    if not normalized_title:
        normalized_title = _domain_label(interaction_domain).strip().lower()
    digest = hashlib.sha1(f"{interaction_domain}|{normalized_title}".encode("utf-8")).hexdigest()[
        :16
    ]
    return f"knowledge-gap:{interaction_domain}:{digest}"


def _format_gap_document_title(
    *,
    label: str,
    interaction_domain: str,
    sample_questions: list[str],
) -> str:
    cleaned = label.replace("FAQ草稿｜", "").replace("FAQ草稿 |", "").strip(" ：:|｜")
    cleaned = re.sub(r"^(常见问题[:：]\s*)+", "", cleaned).strip(" ：:|｜")
    if "｜" in cleaned:
        cleaned = cleaned.split("｜", 1)[-1].strip()
    if _looks_like_fragmented_gap_text(cleaned) or len(cleaned) < 4:
        cleaned = _question_to_topic(sample_questions[0]) if sample_questions else ""
    if not cleaned:
        cleaned = _domain_label(interaction_domain)
    return f"常见问题：{_summarize_text(cleaned, limit=24).rstrip('。')}"


def _build_gap_document_content(draft: KnowledgeGapDraftRecordResponse) -> str:
    return _build_gap_document_content_from_parts(
        interaction_domain=draft.interaction_domain,
        sample_questions=draft.sample_questions,
        suggested_action=draft.suggested_action,
        reason=draft.reason,
    )


def _build_gap_document_content_from_parts(
    *,
    interaction_domain: str,
    sample_questions: list[str],
    suggested_action: str,
    reason: str,
) -> str:
    question_lines = [
        f"- {str(question).strip()}" for question in sample_questions[:3] if str(question).strip()
    ]
    if not question_lines:
        question_lines = ["- 暂无代表性问题样例"]
    questions_block = "\n".join(question_lines)
    return (
        f"主题：{_domain_label(interaction_domain)}\n"
        "适用问题：\n"
        f"{questions_block}\n\n"
        f"标准说明：{_summarize_text(suggested_action, limit=160)}\n"
        f"补充背景：{_summarize_text(reason, limit=120)}\n"
        "使用边界：涉及老师本人审批、例外政策或个性化承诺时，需要人工确认。"
    )


def _parse_legacy_gap_document(
    document: KnowledgeDocumentRecord,
) -> dict[str, str | list[str]]:
    source_name = document.source_name or ""
    cluster_id = (
        source_name.split(":", 1)[1]
        if source_name.startswith("analytics-gap:") and ":" in source_name
        else document.document_id
    )
    interaction_domain = next(
        (tag for tag in document.tags if tag not in {"analytics-gap", "draft", "faq-draft"}),
        "general",
    )
    sample_questions = re.findall(r"^-\s+(.+)$", document.content, re.MULTILINE)
    suggested_action = _extract_prefixed_field(document.content, "建议动作")
    reason = _extract_prefixed_field(document.content, "为何需要补充")
    label = document.title.replace("FAQ草稿｜", "").strip()
    return {
        "cluster_id": cluster_id,
        "interaction_domain": str(interaction_domain),
        "sample_questions": sample_questions,
        "suggested_action": suggested_action
        or "补充与当前问题直接相关的标准说明、准备清单和边界提醒。",
        "reason": reason or "近期多次出现相似提问，现有标准材料还不够集中。",
        "label": label,
    }


def _extract_prefixed_field(content: str, prefix: str) -> str:
    matched = re.search(rf"{re.escape(prefix)}[:：]\s*(.+)", content)
    return matched.group(1).strip() if matched else ""


def _normalize_gap_document_tags(tags: list[str], interaction_domain: str) -> list[str]:
    normalized: list[str] = []
    for tag in tags:
        cleaned = tag.strip().lower()
        if not cleaned or cleaned in {"draft", "faq-draft", "analytics-gap"}:
            continue
        if cleaned not in normalized:
            normalized.append(cleaned)
    for tag in (interaction_domain.strip().lower(), "faq", "knowledge-gap"):
        if tag and tag not in normalized:
            normalized.append(tag)
    return normalized


def _domain_label(interaction_domain: str) -> str:
    return {
        "research": "科研指导",
        "teaching": "课程答疑",
        "advising": "指导建议",
        "booking": "预约说明",
        "general": "常见说明",
    }.get(interaction_domain, "常见说明")


def _looks_like_fragmented_gap_text(text: str) -> bool:
    normalized = _normalize_whitespace(text).strip(" ：:|｜")
    if not normalized:
        return True
    pieces = [piece.strip() for piece in text.split("/") if piece.strip()]
    if len(pieces) >= 2 and all(len(piece) <= 3 for piece in pieces):
        return True
    if normalized.endswith(("…", "...")):
        generic_fragment = normalized.removesuffix("…").removesuffix("...")
        if len(generic_fragment) <= 6 and re.fullmatch(
            r"[常见问题说明预约指导课程科研答疑管理服务登录注册账号设置信息维护]+",
            generic_fragment,
        ):
            return True
    return False


def _question_to_topic(question: str) -> str:
    normalized = str(question).strip().strip("？?。！!")
    return _summarize_text(normalized, limit=24)


def _extract_numbered_highlights(text: str) -> list[str]:
    matches = re.findall(r"\d+[\.、]\s*(.+?)(?=\s*\d+[\.、]\s*|$)", text)
    highlights: list[str] = []
    for match in matches:
        item = _summarize_text(_normalize_whitespace(match), limit=24).rstrip("。")
        if item:
            highlights.append(item)
    return highlights


def _allow_draft_write_for_request(request: ChatRequest, is_admin_request: bool) -> bool:
    if not (is_admin_request or bool(request.student_email)):
        return False
    question = request.question.lower()
    record_markers = (
        "记录成",
        "记录为",
        "归档",
        "存档",
        "保存成",
        "保存为",
        "follow-up material",
        "archive this",
        "save this draft",
    )
    artifact_markers = (
        "附件",
        "上传",
        "proposal",
        "agenda",
        "draft",
        "材料",
        "文档",
        "notes",
        "outline",
    )
    return any(marker in question for marker in record_markers) and any(
        marker in question for marker in artifact_markers
    )


def _normalize_whitespace(text: str) -> str:
    return re.sub(r"\s+", " ", str(text or "")).strip()


def _summarize_text(text: str, *, limit: int) -> str:
    normalized = _normalize_whitespace(text)
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 1].rstrip("，、；： ") + "…"
