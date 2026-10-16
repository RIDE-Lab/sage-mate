"""The Wiki sync should rebuild its index only for real document changes."""

from __future__ import annotations

from dataclasses import dataclass
import sys

from tools import ingest_wiki


@dataclass(frozen=True)
class _Record:
    document_id: str
    title: str
    content: str
    tags: tuple[str, ...]
    source_name: str
    metadata: tuple[tuple[str, str], ...]


class _Store:
    def __init__(self) -> None:
        self.documents: dict[str, _Record] = {}
        self.rebuilds = 0
        self._link_graph: dict[str, list[str]] = {}

    def list_documents(self) -> list[_Record]:
        return list(self.documents.values())

    def upsert_document(self, payload, *, rebuild_indexes: bool = True):
        assert rebuild_indexes is False
        record = _Record(
            document_id=payload.source_name,
            title=payload.title,
            content=payload.content,
            tags=tuple(payload.tags),
            source_name=payload.source_name,
            metadata=tuple(sorted(payload.metadata.items())),
        )
        previous = self.documents.get(record.source_name)
        if previous == record:
            return previous, False
        self.documents[record.source_name] = record
        return record, previous is None

    def rebuild_indexes(self) -> None:
        self.rebuilds += 1

    def delete_documents(self, document_ids: list[str], *, rebuild_indexes: bool = True) -> int:
        assert rebuild_indexes is False
        for document_id in document_ids:
            self.documents.pop(document_id)
        return len(document_ids)


def test_ingest_wiki_rebuilds_once_only_when_pages_change(tmp_path, monkeypatch, capsys) -> None:
    docs = tmp_path / "wiki" / "docs" / "tech-notes"
    docs.mkdir(parents=True)
    page = docs / "kv-cache.md"
    page.write_text("---\ntitle: KV Cache\n---\nFirst version", encoding="utf-8")
    marker = tmp_path / "changed.marker"
    store = _Store()
    monkeypatch.setattr(ingest_wiki, "AppSettings", lambda: object())
    monkeypatch.setattr(ingest_wiki, "LocalKnowledgeStore", lambda _: store)

    def run() -> str:
        monkeypatch.setattr(
            sys,
            "argv",
            ["ingest_wiki.py", "--wiki-dir", str(tmp_path / "wiki"), "--changed-marker", str(marker)],
        )
        ingest_wiki.main()
        return capsys.readouterr().out

    assert "1 created, 0 updated, 0 deleted, 0 skipped" in run()
    assert "https://ride-lab.github.io/sage-wiki/docs/tech-notes/kv-cache" in store.documents["wiki:tech-notes/kv-cache"].content
    assert store.rebuilds == 1
    assert marker.read_text(encoding="utf-8") == "1\n"

    marker.unlink()
    assert "0 created, 0 updated, 0 deleted, 1 skipped" in run()
    assert store.rebuilds == 1
    assert not marker.exists()

    page.write_text("---\ntitle: KV Cache\n---\nRevised version", encoding="utf-8")
    assert "0 created, 1 updated, 0 deleted, 0 skipped" in run()
    assert store.rebuilds == 2
    assert marker.read_text(encoding="utf-8") == "1\n"

    marker.unlink()
    page.unlink()
    assert "0 created, 0 updated, 1 deleted, 0 skipped" in run()
    assert store.rebuilds == 3
    assert not store.documents
    assert marker.read_text(encoding="utf-8") == "1\n"
