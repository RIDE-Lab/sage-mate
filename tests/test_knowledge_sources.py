import pytest
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.knowledge_sources import KnowledgeSources, SourceSelection, wiki_index_settings
from sage_faculty_twin.models import KnowledgeDocumentCreate


def stores(tmp_path):
    settings = AppSettings(_env_file=None, knowledge_base_dir=tmp_path/'private',
        wiki_index_dir=tmp_path/'wiki', knowledge_backend='local')
    private = LocalKnowledgeStore(settings)
    wiki = LocalKnowledgeStore(wiki_index_settings(settings))
    for store, origin in [(private, 'private'), (wiki, 'wiki')]:
        store.add_document(KnowledgeDocumentCreate(title='quartz research',
            content=f'quartz research {origin} complete body TAIL',
            source_name=f'{origin}:quartz', tags=['audience:public']))
    return settings, private, wiki, KnowledgeSources(private, wiki)


def test_guest_only_wiki_member_both_and_full_evidence(tmp_path):
    _, private, wiki, sources = stores(tmp_path)
    guest = sources.search('quartz research', visitor_profile='general_visitor')
    assert guest and {h.metadata['retrieval_source'] for h in guest} == {'wiki'}
    member = sources.search('quartz research', visitor_profile='lab_member')
    assert {h.metadata['retrieval_source'] for h in member} == {'wiki', 'private'}
    for hit in member:
        record = sources.get_visible_document(hit.document_id, visitor_profile='lab_member')
        assert record.content.endswith('complete body TAIL')
        assert record.document_id == hit.document_id
        if hit.metadata['retrieval_source'] == 'private':
            assert sources.get_visible_document(hit.document_id, visitor_profile='general_visitor') is None
    assert sources.get_visible_document(private.list_documents()[0].document_id) is None
    assert sources.search('quartz', admin_role='super_admin')
    assert len(private.list_documents()) == len(wiki.list_documents()) == 1


@pytest.mark.parametrize('role', [None, 'manager', 'super_admin'])
def test_requested_wiki_scope_blocks_private_search_and_hydration(tmp_path, monkeypatch, role):
    _, private, _, sources = stores(tmp_path)
    private_id = 'private::' + private.list_documents()[0].document_id
    def forbidden(*args, **kwargs):
        pytest.fail('Unrequested private source was accessed')
    monkeypatch.setattr(private, 'search', forbidden)
    monkeypatch.setattr(private, 'get_visible_document', forbidden)
    selection = SourceSelection(frozenset({'wiki'}))
    hits = sources.search('quartz', visitor_profile='lab_member', admin_role=role, selection=selection)
    assert hits and all(hit.document_id.startswith('wiki::') for hit in hits)
    assert sources.get_visible_document(private_id, visitor_profile='lab_member',
                                        admin_role=role, selection=selection) is None


def test_requested_private_scope_never_grants_guest_access(tmp_path):
    _, private, _, sources = stores(tmp_path)
    selection = SourceSelection(frozenset({'private'}))
    assert sources.search('quartz', visitor_profile='general_visitor', selection=selection) == []
    assert sources.get_visible_document('private::' + private.list_documents()[0].document_id,
                                        visitor_profile='general_visitor', selection=selection) is None
    assert sources.public_documents(selection=selection) == []


def test_empty_selection_is_not_a_default_or_named_title_override(tmp_path):
    _, _, _, sources = stores(tmp_path)
    selection = SourceSelection(frozenset())
    assert sources.search('quartz', admin_role='super_admin', selection=selection) == []
    assert sources.search('Wiki《quartz research》', admin_role='super_admin', selection=selection) == []
    assert sources.public_documents(selection=selection) == []


@pytest.mark.parametrize('origins', [{'unknown'}, {'wiki', 'unknown'}, 'wiki'])
def test_source_selection_rejects_unknown_or_scalar_origins(origins):
    with pytest.raises(ValueError):
        SourceSelection(origins)


def test_explicit_wiki_title_keeps_all_chunks_without_unrelated_padding(tmp_path):
    _, _, wiki, sources = stores(tmp_path)
    for i in range(1, 4):
        wiki.add_document(KnowledgeDocumentCreate(title='Wiki | 完整资料',
            content=f'完整资料 part {i} TAIL', source_name=f'wiki:guide:{i}',
            tags=['audience:public'], metadata={'wiki_chunk': str(i)}))
    hits = sources.search('请依据 Wiki《完整资料》介绍内容', top_k=1)
    assert len(hits) == 3
    assert all(h.title == 'Wiki | 完整资料' for h in hits)
    assert all(sources.get_visible_document(h.document_id).content.endswith('TAIL') for h in hits)


@pytest.mark.parametrize('profile', ['general_visitor', 'lab_member'])
def test_missing_explicit_wiki_title_never_pads_with_semantic_neighbours(tmp_path, profile):
    _, _, _, sources = stores(tmp_path)
    assert sources.search('请介绍 Wiki《不存在的 quartz research 文档》', visitor_profile=profile) == []


@pytest.mark.parametrize('method', ['add_document', 'upsert_document', 'update_document'])
def test_write_namespaces_are_enforced(tmp_path, method):
    _, private, wiki, _ = stores(tmp_path)
    for store, wrong in [(private, 'wiki:wrong'), (wiki, 'private:wrong')]:
        payload = KnowledgeDocumentCreate(title='wrong', content='wrong', source_name=wrong)
        args = ('id', payload) if method == 'update_document' else (payload,)
        with pytest.raises(ValueError):
            getattr(store, method)(*args)


@pytest.mark.parametrize('relative', ['', 'private', 'private/wiki'])
def test_paths_cannot_overlap(tmp_path, relative):
    settings = AppSettings(_env_file=None, knowledge_base_dir=tmp_path/'private',
        wiki_index_dir=tmp_path/relative)
    with pytest.raises(ValueError, match='disjoint'):
        wiki_index_settings(settings)


def test_missing_wiki_path_never_falls_back_to_private(tmp_path):
    with pytest.raises(ValueError, match='must be configured'):
        wiki_index_settings(AppSettings(_env_file=None, knowledge_base_dir=tmp_path))


def test_legacy_wiki_records_not_retrievable_through_private_lane(tmp_path):
    _, private, wiki, sources = stores(tmp_path)
    record = wiki.list_documents()[0]
    private._documents[record.document_id] = record
    hits = sources.search('quartz', visitor_profile='lab_member')
    assert all(not h.source_name.startswith('wiki:') for h in hits if h.metadata['retrieval_source']=='private')
    assert sources.get_visible_document('private::'+record.document_id, visitor_profile='lab_member') is None
