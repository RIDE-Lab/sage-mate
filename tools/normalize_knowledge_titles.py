#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from sage_faculty_twin.knowledge_identity import (
    build_search_aliases,
    canonicalize_knowledge_title,
    is_generic_knowledge_title,
)
from sage_faculty_twin.models import KnowledgeDocumentRecord


def normalize_knowledge_titles(knowledge_dir: Path, *, apply: bool) -> dict[str, int]:
    paths = sorted(knowledge_dir.glob("*.json"))
    records: list[tuple[Path, dict[str, object]]] = []
    changed = 0
    title_updates = 0
    alias_updates = 0
    generic_before = 0

    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        KnowledgeDocumentRecord.model_validate(payload)
        records.append((path, payload))
        title = str(payload.get("title") or "")
        source_name = str(payload.get("source_name") or "")
        if is_generic_knowledge_title(title):
            generic_before += 1
        if not source_name.startswith("homepage:"):
            continue

        canonical_title = canonicalize_knowledge_title(title, source_name)
        metadata = dict(payload.get("metadata") or {})
        aliases = build_search_aliases(canonical_title, source_name)
        record_changed = False
        if canonical_title != title:
            payload["title"] = canonical_title
            title_updates += 1
            record_changed = True
        if metadata.get("search_aliases") != aliases:
            metadata["search_aliases"] = aliases
            payload["metadata"] = metadata
            alias_updates += 1
            record_changed = True
        if record_changed:
            KnowledgeDocumentRecord.model_validate(payload)
            changed += 1
            if apply:
                path.write_text(
                    json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )

    final_titles = [
        str(payload.get("title") or "")
        if not str(payload.get("source_name") or "").startswith("homepage:")
        else canonicalize_knowledge_title(
            str(payload.get("title") or ""), str(payload.get("source_name") or "")
        )
        for _, payload in records
    ]
    title_counts = Counter(final_titles)
    return {
        "documents": len(records),
        "changed": changed,
        "title_updates": title_updates,
        "alias_updates": alias_updates,
        "generic_titles_before": generic_before,
        "generic_titles_after": sum(is_generic_knowledge_title(title) for title in final_titles),
        "duplicate_title_documents_after": sum(
            count for count in title_counts.values() if count > 1
        ),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Normalize generated knowledge titles and source-aware search aliases."
    )
    parser.add_argument("--knowledge-dir", required=True, type=Path)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    report = normalize_knowledge_titles(args.knowledge_dir, apply=args.apply)
    print(json.dumps(report, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
