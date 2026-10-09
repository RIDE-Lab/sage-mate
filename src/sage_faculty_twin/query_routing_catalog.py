"""Shared query markers for deterministic Sage Mate routing.

The service used to repeat these literals in identity detection, fast intent
classification, local evidence answers, and the pre-admission fast lane.  Keep
the catalog data-only so adding a user-facing alias changes one place and can
be covered by a single contract test.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

OWNER_IDENTITY_ALIASES: tuple[str, ...] = (
    "张老师",
    "张书豪老师",
    "张书豪教授",
    "张书豪是谁",
    "张书豪老师是谁",
    "老师",
    "老师是谁",
)

OWNER_IDENTITY_MARKERS: tuple[str, ...] = (
    "你是谁",
    "介绍一下你",
    "介绍张老师",
    "介绍张书豪",
    "张老师是谁",
    "张老师简介",
    "张书豪简介",
    "课题组主要做什么",
    "课题组研究",
    "实验室主要做什么",
    "个人简介",
    "个人介绍",
    "学术背景",
)

OWNER_CONTEXT_MARKERS: tuple[str, ...] = (
    "主要研究",
    "研究方向",
    "招什么样",
    "招生",
    "加入课题组",
    "加入你们组",
    "需要什么准备",
    "提前准备",
)

SYSTEM_PROJECT_MARKERS: tuple[str, ...] = (
    "有哪些系统",
    "哪些系统",
    "系统或开源项目",
    "开源项目",
    "系统建设",
    "代表性系统",
    "课题组目前有",
    "实验室目前有",
)

COURSE_FACT_MARKERS: tuple[str, ...] = (
    "课程主要学习",
    "课程主要学什么",
    "课程学什么",
    "课程内容",
    "课程介绍",
    "课程覆盖",
    "大模型推理基础设施课程",
    "这门课学什么",
    "学哪些内容",
    "会讲什么",
)


def normalize_query(value: str) -> str:
    """Normalize punctuation/whitespace for exact alias checks."""

    return re.sub(r"[\s。！？!?，,：:]+$", "", str(value or "").strip())


def contains_marker(value: str, markers: Iterable[str]) -> bool:
    """Return whether a query contains any configured marker."""

    query = str(value or "")
    lowered = query.lower()
    return any(marker in query or marker.lower() in lowered for marker in markers)


def is_owner_identity_query(value: str) -> bool:
    """Recognize the owner's aliases without matching unrelated teachers."""

    normalized = normalize_query(value)
    if is_explicit_other_teacher_query(normalized):
        return False
    if normalized in OWNER_IDENTITY_ALIASES:
        return True
    if any(marker in normalized for marker in OWNER_IDENTITY_MARKERS):
        return True
    return "张书豪" in normalized and any(
        marker in normalized for marker in ("是谁", "简介", "介绍")
    )


def is_explicit_other_teacher_query(value: str) -> bool:
    query = str(value or "")
    return any(marker in query for marker in ("另一个张老师", "其他张老师", "某位张老师", "某个张老师"))
