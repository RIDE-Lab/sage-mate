"""One-time ingestion of workspace repo READMEs + curated FAQ into the
sage-mate knowledge base.

Usage:
    cd /home/shuhao/sage-mate
    PYTHONPATH=src:/home/shuhao/SAGE/src:/home/shuhao/sageVDB:/home/shuhao/neuromem \
        python tools/ingest_workspace_repos.py
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.models import KnowledgeDocumentCreate

_MAX_CONTENT_LEN = 18000

_REPO_SOURCES = [
    {"path": "/home/shuhao/SAGE/README.md", "title": "Lab System | SAGE README", "tags": ["research", "publication", "system", "sage"], "source_name": "workspace/SAGE"},
    {"path": "/home/shuhao/neuromem/README.md", "title": "Lab System | Neuromem README", "tags": ["research", "publication", "system", "neuromem", "memory"], "source_name": "workspace/neuromem"},
    {"path": "/home/shuhao/sageVDB/README.md", "title": "Lab System | SageVDB README", "tags": ["research", "system", "vector-db", "sagevdb"], "source_name": "workspace/sageVDB"},
    {"path": str(Path(__file__).resolve().parent.parent / "deps" / "vllm-hust" / "README.md"), "title": "Lab System | vLLM-HUST README", "tags": ["research", "system", "vllm", "inference-engine"], "source_name": "deps/vllm-hust"},
    {"path": "/home/shuhao/vamos/README.md", "title": "Current Research | VAMOS README", "tags": ["research", "agenda", "current-focus", "vamos"], "source_name": "workspace/vamos"},
    {"path": "/home/shuhao/vamos/roadmap.md", "title": "Current Research | VAMOS Roadmap", "tags": ["research", "agenda", "current-focus", "vamos", "roadmap"], "source_name": "workspace/vamos/roadmap"},
    {"path": "/home/shuhao/vamos/docs/proposal/2026-ccf-ant-proposal.md", "title": "Current Research | 2026 CCF-Ant Proposal (VAMOS)", "tags": ["research", "agenda", "current-focus", "vamos", "proposal"], "source_name": "workspace/vamos/proposal"},
    {"path": "/home/shuhao/sage-tutorials/QUICK_START.md", "title": "Teaching | Sage-Tutorials Quick Start", "tags": ["teaching", "tutorial", "sage-tutorials"], "source_name": "workspace/sage-tutorials"},
    # Homepage pages have a dedicated changed-content importer. Private-material
    # belongs to the curated, audience-reviewed importer and never to this broad
    # workspace README job.
]


def _read_file(path):
    p = Path(path)
    if not p.exists():
        return ""
    return p.read_text(encoding="utf-8", errors="replace")[:_MAX_CONTENT_LEN]


def main():
    settings = AppSettings()
    store = LocalKnowledgeStore(settings)

    payloads = []
    for src in _REPO_SOURCES:
        content = _read_file(src["path"])
        if not content.strip():
            print(f"  SKIP (empty): {src['path']}")
            continue
        payloads.append(KnowledgeDocumentCreate(title=src["title"], content=content, tags=src["tags"], source_name=src["source_name"]))

    faq_path = Path(__file__).resolve().parent.parent / "data" / "_seed_faq.json"
    if faq_path.exists():
        for item in json.loads(faq_path.read_text(encoding="utf-8")):
            payloads.append(KnowledgeDocumentCreate(title=item["title"], content=item["content"], tags=item.get("tags", []), source_name=item.get("source_name", "curated_faq")))
    else:
        print(f"  WARN: FAQ file not found at {faq_path}")

    print(f"Prepared {len(payloads)} documents for ingestion.")
    created = updated = skipped = 0
    changed = False
    for payload in payloads:
        existing = store._find_document_by_source_name(payload.source_name)  # noqa: SLF001
        record, was_created = store.upsert_document(payload, rebuild_indexes=False)
        if was_created:
            print(f"  CREATED: {record.title} ({record.document_id[:8]})")
            created += 1
            changed = True
        elif record != existing:
            print(f"  UPDATED: {record.title} ({record.document_id[:8]})")
            updated += 1
            changed = True
        else:
            print(f"  EXISTS: {payload.title}")
            skipped += 1
    if changed:
        store.rebuild_indexes()
    print(f"\nDone. created={created}, updated={updated}, skipped={skipped}")


if __name__ == "__main__":
    main()
