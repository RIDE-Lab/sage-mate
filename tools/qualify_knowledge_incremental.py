"""Real configured embedding + SageVDB, three isolated process lifetimes.

Use the application's environment and interpreter. No dotenv files are read,
no credentials are printed/persisted, and no existing corpus is opened. Only a
fresh temporary synthetic corpus is mutated. Artifacts are retained for audit.
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path


def qualify_phase(root: Path, phase: str) -> None:
    from sage_faculty_twin.config import AppSettings
    from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
    from sage_faculty_twin.models import KnowledgeDocumentCreate

    assert (root / ".incremental-qualification").read_text() == "synthetic-only-v1"
    config = AppSettings(
        _env_file=None, runtime_dir=root, knowledge_base_dir=root / "knowledge",
        knowledge_namespace="private", knowledge_sagevdb_persistence_dir=root / "index",
    )
    assert config.knowledge_backend == "sagevdb", "requires configured SageVDB"
    assert config.sagevdb_embedding_backend.lower() == "openai", "requires real embedding API"
    store = LocalKnowledgeStore(config)
    manifest = root / "acceptance.json"

    def payload(text, source):
        return KnowledgeDocumentCreate(
            title=text, content=text, source_name="incremental-qualification-" + source,
            metadata={"visibility": "public"},
        )

    try:
        if phase == "seed":
            first = store.add_document(payload("合成验收：旧版实验流程，标识 ALPHA。", "kept"))
            removed = store.add_document(payload("合成验收：待删除的旧笔记，标识 OMEGA。", "removed"))
            state = {"kept": first.document_id, "removed": removed.document_id}
        else:
            state = json.loads(manifest.read_text())
            assert store._sagevdb_last_sync is None, "cold open rebuilt persisted index"
            assert store._sagevdb_persistence.generation == state["generation"]
        if phase == "mutate":
            store.update_document(state["kept"], payload("合成验收：新版实验流程，标识 BETA。", "kept"))
            assert store.delete_documents([state["removed"]]) == 1
        if phase == "verify":
            assert store.get_document(state["removed"]) is None
            assert "BETA" in store.get_document(state["kept"]).content
            assert set(store._document_id_to_vector_id) == {state["kept"]}
            assert store._catalog_revision == store._indexed_catalog_revision == 4
        query = "合成验收新版实验流程 BETA" if phase != "seed" else "合成验收旧版实验流程 ALPHA"
        hits = store.search(query, admin_role="super_admin")
        assert any(hit.document_id == state["kept"] for hit in hits)
        if phase != "seed":
            assert all(hit.document_id != state["removed"] for hit in hits)
        records = store._catalog.journal.records()[0]
        assert len(records) == store._catalog_revision
        assert all(store._catalog.journal.read(row["key"])
                   ["tasks"]["knowledge_index"]["status"] == "done" for row in records)
        assert store._sagevdb_persistence.source_revision == store._catalog_revision
        state["generation"] = store._sagevdb_persistence.generation
        manifest.write_text(json.dumps(state))
        print(json.dumps({"phase": phase, "revision": store._catalog_revision,
                          "native_index_tasks": len(records), "passed": True}), flush=True)
    finally:
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--parent-dir", type=Path, help="Existing artifact parent; defaults to temp directory")
    parser.add_argument("--child-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--phase", choices=("seed", "mutate", "verify"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.child_root is not None:
        if args.phase is None:
            parser.error("child phase required")
        qualify_phase(args.child_root, args.phase)
        return
    if args.phase is not None:
        parser.error("phase requires child root")
    root = Path(tempfile.mkdtemp(prefix="knowledge-incremental-", dir=args.parent_dir)).resolve()
    (root / ".incremental-qualification").write_text("synthetic-only-v1")
    for phase in ("seed", "mutate", "verify"):
        subprocess.run([sys.executable, str(Path(__file__).resolve()), "--child-root", str(root),
                        "--phase", phase], cwd=root, check=True)
    print("INDEPENDENT_PROCESS_INCREMENTAL_RESTART_OK", flush=True)


if __name__ == "__main__":
    main()
