#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.knowledge_identity import is_generic_knowledge_title
from sage_faculty_twin.models import KnowledgeDocumentRecord


def audit_knowledge_corpus(
    knowledge_dir: Path,
    *,
    backend: str,
    representative_search: bool,
) -> tuple[dict[str, object], bool]:
    records: list[KnowledgeDocumentRecord] = []
    invalid: list[str] = []
    for path in sorted(knowledge_dir.glob("*.json")):
        try:
            records.append(
                KnowledgeDocumentRecord.model_validate_json(
                    path.read_text(encoding="utf-8")
                )
            )
        except Exception as exc:  # pragma: no cover - diagnostic CLI boundary
            invalid.append(f"{path.name}: {exc}")

    ids = Counter(record.document_id for record in records)
    sources = Counter(record.source_name for record in records if record.source_name)
    titles = Counter(record.title for record in records)
    homepage_records = [
        record
        for record in records
        if str(record.source_name or "").startswith("homepage:")
    ]
    missing_aliases = [
        record.document_id
        for record in homepage_records
        if not str(record.metadata.get("search_aliases") or "").strip()
    ]

    store = LocalKnowledgeStore(
        AppSettings(
            knowledge_base_dir=knowledge_dir,
            knowledge_backend=backend,
            neuromem_index_type="segment",
        )
    )
    representative_groups: dict[str, list[KnowledgeDocumentRecord]] = {}
    for record in records:
        source = str(record.source_name or "")
        if source.startswith("homepage:") and "::part-" in source:
            representative_groups.setdefault(
                re.sub(r"::part-\d+$", "", source), []
            ).append(record)

    missed_groups: list[str] = []
    if representative_search:
        for source, group in sorted(representative_groups.items()):
            # A source group is reachable when its user-facing title retrieves at
            # least one chunk.  Do not concatenate every stored alias into a query:
            # path fragments and part ordinals can distort intent classification.
            query = re.sub(r"（第\s*\d+\s*部分）$", "", group[0].title)
            group_ids = {record.document_id for record in group}
            hits = store.search(
                query,
                top_k=20,
                visitor_profile="lab_member",
                admin_role="manager",
            )
            if not group_ids.intersection(hit.document_id for hit in hits):
                missed_groups.append(source)

    report: dict[str, object] = {
        "documents": len(records),
        "invalid_documents": len(invalid),
        "invalid_examples": invalid[:10],
        "duplicate_document_ids": sum(count > 1 for count in ids.values()),
        "duplicate_source_names": sum(count > 1 for count in sources.values()),
        "duplicate_title_documents": sum(
            count for count in titles.values() if count > 1
        ),
        "generic_titles": sum(
            is_generic_knowledge_title(record.title) for record in records
        ),
        "homepage_documents": len(homepage_records),
        "homepage_documents_without_aliases": len(missing_aliases),
        "loaded_documents": store.count_documents(),
        "indexed_documents": store.indexed_document_count(),
        "index_complete": store.index_is_complete(),
        "representative_source_groups": len(representative_groups),
        "representative_groups_checked": (
            len(representative_groups) if representative_search else 0
        ),
        "representative_group_misses": len(missed_groups),
        "representative_miss_examples": missed_groups[:10],
    }
    passed = not any(
        (
            invalid,
            [key for key, count in ids.items() if count > 1],
            [key for key, count in sources.items() if count > 1],
            missing_aliases,
            [record for record in records if is_generic_knowledge_title(record.title)],
            missed_groups,
        )
    ) and store.index_is_complete()
    return report, passed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Validate corpus identities and active search-index completeness."
    )
    parser.add_argument("--knowledge-dir", required=True, type=Path)
    parser.add_argument(
        "--backend", choices=("local", "neuromem"), default="local"
    )
    parser.add_argument("--representative-search", action="store_true")
    args = parser.parse_args()
    report, passed = audit_knowledge_corpus(
        args.knowledge_dir,
        backend=args.backend,
        representative_search=args.representative_search,
    )
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
