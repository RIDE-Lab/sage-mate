"""Canonical knowledge-source identifiers used by answer policies.

These identifiers describe authority, not factual content.  Keeping them in a
small module prevents source-title drift across retrieval and rendering code.
"""

from __future__ import annotations

OWNER_PROFILE_TITLE = "主页资料｜张书豪"
OWNER_SYSTEM_OVERVIEW_TITLE = "主页资料｜当前系统建设"
OWNER_RESEARCH_TITLE = "主页资料｜研究板块"
OWNER_ADMISSIONS_TITLE = "主页资料｜招生与合作"
OWNER_PROFILE_SOURCE = "homepage:contents/home.md#张书豪"
PUBLIC_COURSE_TAGS = frozenset({"teaching", "courseware"})

IDENTITY_FLOOR_TITLES: tuple[str, ...] = (
    OWNER_PROFILE_TITLE,
    OWNER_RESEARCH_TITLE,
    OWNER_SYSTEM_OVERVIEW_TITLE,
    OWNER_ADMISSIONS_TITLE,
    "研究总览｜一、共享状态访问、调度与运行时管理",
)


def is_owner_profile_source(title: str, source_name: str | None = None) -> bool:
    return title == OWNER_PROFILE_TITLE or OWNER_PROFILE_SOURCE in (source_name or "")
