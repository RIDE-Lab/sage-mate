"""The Wiki sync should rebuild its index only for real document changes."""

from __future__ import annotations

from dataclasses import dataclass
import sys

from tools import ingest_wiki
import pytest


@pytest.fixture(autouse=True)
def settings_projection(monkeypatch):
    # These parser tests use a fake store; real directory isolation is tested separately.
    monkeypatch.setattr(ingest_wiki, 'wiki_index_settings', lambda settings: settings)


def test_long_wiki_page_is_losslessly_partitioned(tmp_path, monkeypatch):
    docs = tmp_path/'docs'
    docs.mkdir()
    body = ('Full paragraph with evidence.\n\n' * 2500) + 'FINAL_TAIL_FACT'
    (docs/'long.md').write_text(body)
    store = _Store()
    monkeypatch.setattr(ingest_wiki, 'AppSettings', lambda: object())
    monkeypatch.setattr(ingest_wiki, 'LocalKnowledgeStore', lambda _: store)
    monkeypatch.setattr(sys, 'argv', ['ingest_wiki.py','--wiki-dir',str(tmp_path)])
    ingest_wiki.main()
    chunks = sorted(store.documents.values(), key=lambda d:int(dict(d.metadata)['wiki_chunk']))
    assert len(chunks)>1
    assert ''.join(d.content.split('\n\n',1)[1] for d in chunks)==body
    assert chunks[-1].content.endswith('FINAL_TAIL_FACT')
    assert all(len(d.content)<=ingest_wiki._MAX_CONTENT_LEN for d in chunks)


@pytest.mark.parametrize('audience,expected', [('', 'audience:public'), ('public', 'audience:public'), ('invalid', 'audience:public')])
def test_reviewed_wiki_visibility_and_course_metadata(tmp_path, monkeypatch, audience, expected):
    docs = tmp_path / 'docs' / 'standards'
    docs.mkdir(parents=True)
    (docs / 'guide.md').write_text(f'---\ntitle: Guide\naudience: {audience}\ndomain: teaching\ncourse_id: paper-writing\nmaterial_type: lecture\nordinal_type: lecture\nordinal: 5\n---\nPublic guide')
    store = _Store()
    monkeypatch.setattr(ingest_wiki, 'AppSettings', lambda: object())
    monkeypatch.setattr(ingest_wiki, 'LocalKnowledgeStore', lambda _: store)
    monkeypatch.setattr(sys, 'argv', ['ingest_wiki.py','--wiki-dir',str(tmp_path)])
    ingest_wiki.main()
    record = store.documents['wiki:standards/guide']
    assert [tag for tag in record.tags if tag.startswith('audience:')] == [expected]
    assert dict(record.metadata)['course_id'] == 'paper-writing'
    assert dict(record.metadata)['ordinal_type'] == 'lecture'
    assert dict(record.metadata)['ordinal'] == '5'
    assert {'teaching', 'lecture', 'course:paper-writing'} <= set(record.tags)


def test_research_tutorial_retains_topic_without_visibility_escalation(tmp_path, monkeypatch):
    docs = tmp_path / 'docs' / 'standards'
    docs.mkdir(parents=True)
    (docs / 'guide.md').write_text('---\ntitle: Research\ndomain: research\ntags: [audience:public]\n---\nResearch onboarding')
    store = _Store()
    monkeypatch.setattr(ingest_wiki, 'AppSettings', lambda: object())
    monkeypatch.setattr(ingest_wiki, 'LocalKnowledgeStore', lambda _: store)
    monkeypatch.setattr(sys, 'argv', ['ingest_wiki.py', '--wiki-dir', str(tmp_path)])
    ingest_wiki.main()
    tags = store.documents['wiki:standards/guide'].tags
    assert 'research' in tags
    assert 'audience:lab_member' not in tags
    assert 'audience:public' in tags


def test_research_evaluation_guide_keeps_semantic_scope_without_changing_acl(tmp_path, monkeypatch):
    from sage_faculty_twin.models import InteractionIntent, KnowledgeSearchHit
    from sage_faculty_twin.service import FacultyTwinWorkflowSupport

    docs = tmp_path / 'docs' / 'tech-notes'
    docs.mkdir(parents=True)
    (docs / 'evaluation.md').write_text(
        '---\ntitle: RAG 系统设计与实践\ndomain: research\nmaterial_type: guide\n---\n'
        'RAG 系统需要测量效果、性能和完整请求延迟。'
    )
    store = _Store()
    monkeypatch.setattr(ingest_wiki, 'AppSettings', lambda: object())
    monkeypatch.setattr(ingest_wiki, 'LocalKnowledgeStore', lambda _: store)
    monkeypatch.setattr(sys, 'argv', ['ingest_wiki.py', '--wiki-dir', str(tmp_path)])
    ingest_wiki.main()
    document = store.documents['wiki:tech-notes/evaluation']
    assert {'research', 'guide', 'audience:public'} <= set(document.tags)
    assert not set(document.tags) & {'audience:lab_member', 'audience:private'}
    assert dict(document.metadata)['domain'] == 'research'
    hit = KnowledgeSearchHit(document_id=document.document_id, title=document.title,
        source_name=document.source_name, tags=list(document.tags), score=1,
        excerpt='RAG 系统需要测量效果、性能和完整请求延迟。')
    support = object.__new__(FacultyTwinWorkflowSupport)
    intent = InteractionIntent(action='answer', domain='research',
        retrieval_scopes=['publications', 'research_methodology'], exclude_scopes=['courseware'])
    question = '评测 RAG 系统时，怎样同时检查效果和性能？'
    assert support._filter_knowledge_hits_by_intent([hit], intent, question=question) == [hit]


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
    assert "https://lab.sage.org.ai/sage-wiki/docs/tech-notes/kv-cache" in store.documents["wiki:tech-notes/kv-cache"].content
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


def test_relative_wiki_links_become_public_urls(tmp_path) -> None:
    docs = tmp_path / "docs"
    tutorials = docs / "tutorials"
    resources = docs / "resources"
    tutorials.mkdir(parents=True)
    resources.mkdir()
    target = tutorials / "ascend-npu-setup.md"
    target.write_text("# Setup", encoding="utf-8")
    source = resources / "reading.md"
    body = "See [setup](../tutorials/ascend-npu-setup.md#software) and [official](https://example.com/guide)."

    rendered = ingest_wiki._public_wiki_links(body, source, docs)

    assert "https://lab.sage.org.ai/sage-wiki/docs/tutorials/ascend-npu-setup/#software" in rendered
    assert "[official](https://example.com/guide)" in rendered
    assert "../tutorials/ascend-npu-setup.md" not in rendered
