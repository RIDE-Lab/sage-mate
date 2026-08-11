"""Upsert public text files from the personal homepage repository."""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.models import KnowledgeDocumentCreate

DEFAULT_ROOT = Path("/home/shuhao/shuhaozhangtony.github.io")
TEXT_SUFFIXES = {".md", ".tex", ".yml", ".yaml"}
MAX_CONTENT_CHARS = 18_000
DEFAULT_RELATIVE_PATHS = (
    "contents/current_bio.md",
    "contents/cv_en.tex",
    "contents/talks/2026/report-info/报告信息收集表-张书豪-整理版.md",
    "contents/teaching/intro-to-llm-inference-engines/2026/application/课程规划说明.md",
    "contents/teaching/intro-to-llm-inference-engines/2026/slides/handouts/大模型推理基础设施_案例与练习补充.md",
    "contents/teaching/llm-inference-systems-english/README.md",
    "contents/teaching/llm-inference-systems-english/llm_inference_systems.tex",
)


def sync_homepage(root: Path, relative_paths: tuple[str, ...]) -> tuple[int, int, int]:
    store = LocalKnowledgeStore(AppSettings())
    created = updated = skipped = 0
    for relative in relative_paths:
        path = root / relative
        if not path.is_file() or path.suffix.lower() not in TEXT_SUFFIXES:
            continue
        content = path.read_text(encoding="utf-8", errors="replace").strip()
        if not content:
            skipped += 1
            continue
        title = f"个人主页公开资料 | {relative}"
        payload = KnowledgeDocumentCreate(
            title=title,
            content=content[:MAX_CONTENT_CHARS],
            tags=["homepage", "public", "owner-profile", "audience:public"],
            source_name=f"homepage-raw:{relative}",
            metadata={
                "visibility": "public",
                "homepage_path": relative,
                "synced_at": datetime.now(UTC).isoformat(),
            },
        )
        _, inserted = store.upsert_document(payload, rebuild_indexes=False)
        if inserted:
            created += 1
            print(f"CREATED {relative}")
        else:
            updated += 1
            print(f"UPDATED {relative}")
    store.rebuild_indexes()
    return created, updated, skipped


if __name__ == "__main__":
    root = Path(os.environ.get("SAGE_MATE_HOMEPAGE_ROOT", str(DEFAULT_ROOT)))
    if not root.exists():
        raise SystemExit(f"homepage repository does not exist: {root}")
    configured_paths = os.environ.get("SAGE_MATE_HOMEPAGE_PATHS", "")
    relative_paths = tuple(item.strip() for item in configured_paths.split(",") if item.strip())
    result = sync_homepage(root, relative_paths or DEFAULT_RELATIVE_PATHS)
    print(f"Done. created={result[0]} updated={result[1]} skipped={result[2]}")
