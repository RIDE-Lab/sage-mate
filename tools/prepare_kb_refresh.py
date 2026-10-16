"""Prepare a refreshed, reversible KB snapshot without touching the live store.

Run from the Sage Mate repository with the same environment files as the app.
The caller must separately stop the app, verify the live JSON manifest has not
changed, swap the prepared directory into place, and restart/verify the app.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.knowledge_import import import_homepage_materials


OBSOLETE_SOURCES = frozenset({
    # Pages removed or renamed in the canonical Wiki.
    "wiki:tech-notes/batch",
    "wiki:tech-notes/kv",
    "wiki:tech-notes/npu",
    "wiki:tutorials/inference",
    # Reviewed public copies now covered by the canonical Wiki.
    "private-materials:cccf-survey-revision-lessons",
    "private-materials:paper-revision-lessons",
    "private-materials:lecture-ecnu-inference-infra",
    "private-materials:curated-state-management-research-line",
    "private-materials:publication-venue-stats",
    # Dated duplicate summaries; the maintained public homepage is authoritative.
    "homepage:publications-list",
    "public-profile:bio-awards-service",
})


def document_manifest(directory: Path) -> tuple[str, dict[str, str], int]:
    digest = hashlib.sha256()
    source_to_id: dict[str, str] = {}
    count = 0
    for path in sorted(directory.glob("*.json")):
        count += 1
        stat = path.stat()
        digest.update(f"{path.name}:{stat.st_size}:{stat.st_mtime_ns}\n".encode())
        payload = json.loads(path.read_text(encoding="utf-8"))
        source_name = str(payload.get("source_name") or "")
        if not source_name:
            continue
        if source_name in source_to_id:
            raise RuntimeError(f"Duplicate KB source: {source_name}")
        source_to_id[source_name] = str(payload["document_id"])
    return digest.hexdigest(), source_to_id, count


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live-dir", required=True, type=Path)
    parser.add_argument("--shadow-dir", required=True, type=Path)
    parser.add_argument("--homepage-dir", required=True, type=Path)
    parser.add_argument("--env-file", required=True, type=Path)
    parser.add_argument("--prepare", action="store_true", help="Create and build shadow KB")
    args = parser.parse_args()

    live_dir = args.live_dir.resolve(strict=True)
    homepage_dir = args.homepage_dir.resolve(strict=True)
    if not (homepage_dir / "contents" / "home.md").is_file():
        raise RuntimeError("Homepage snapshot has no contents/home.md")
    if args.shadow_dir.exists():
        raise RuntimeError("Shadow directory already exists; refusing to overwrite")

    before_digest, live_sources, live_count = document_manifest(live_dir)
    missing = OBSOLETE_SOURCES - live_sources.keys()
    if missing:
        raise RuntimeError(f"Expected obsolete sources missing: {sorted(missing)}")
    print(f"Live KB: {live_count} documents; manifest {before_digest}")
    print(f"Obsolete sources to remove: {len(OBSOLETE_SOURCES)}")
    for source in sorted(OBSOLETE_SOURCES):
        print(f"  {source}")
    if not args.prepare:
        return

    shadow_dir = args.shadow_dir.resolve()
    if shadow_dir == live_dir or live_dir in shadow_dir.parents:
        raise RuntimeError("Shadow must be a separate sibling, not live KB or its child")
    shutil.copytree(live_dir, shadow_dir)
    settings = AppSettings(_env_file=args.env_file)
    settings.knowledge_base_dir = shadow_dir
    settings.knowledge_sagevdb_persistence_dir = shadow_dir / ".sagevdb-index"
    store = LocalKnowledgeStore(settings)

    report = import_homepage_materials(store, homepage_dir)
    obsolete_after_import = {
        document.source_name: document.document_id
        for document in store.list_documents()
        if document.source_name in OBSOLETE_SOURCES
    }
    already_pruned = OBSOLETE_SOURCES - obsolete_after_import.keys()
    if already_pruned != {"homepage:publications-list"}:
        raise RuntimeError(f"Unexpected obsolete sources pruned by homepage import: {sorted(already_pruned)}")
    obsolete_ids = list(obsolete_after_import.values())
    removed = store.delete_documents(obsolete_ids, rebuild_indexes=True)
    if removed + len(already_pruned) != len(OBSOLETE_SOURCES) or not store.index_is_complete():
        raise RuntimeError("Shadow KB deletion or index validation failed")

    after_digest, _, _ = document_manifest(live_dir)
    if after_digest != before_digest:
        raise RuntimeError("Live KB changed during preparation; do not activate this shadow")
    print(
        "Prepared shadow: "
        f"homepage created={report.created_count}, updated={report.updated_count}, "
        f"skipped={report.skipped_count}; obsolete removed={removed}, "
        f"already pruned={len(already_pruned)}; "
        f"documents={store.count_documents()}, indexed={store.indexed_document_count()}"
    )
    print(f"Live manifest to recheck before activation: {before_digest}")


if __name__ == "__main__":
    main()
