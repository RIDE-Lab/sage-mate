import pytest
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.models import KnowledgeDocumentCreate
from sage_faculty_twin.source_ownership import SOURCE_OWNERSHIP


def private_store(path):
    return LocalKnowledgeStore(AppSettings(knowledge_base_dir=path, knowledge_backend='local'))


@pytest.mark.parametrize('source,target', SOURCE_OWNERSHIP.items())
def test_old_import_rejected_without_mutation(source,target,tmp_path):
    wiki = LocalKnowledgeStore(AppSettings(knowledge_base_dir=tmp_path/'wiki',
        knowledge_namespace='wiki', knowledge_backend='local'))
    canonical = wiki.add_document(KnowledgeDocumentCreate(title='Wiki', content='Current Wiki',source_name=target))
    store = private_store(tmp_path/'private')
    before = store._catalog.catalog.snapshot()
    payload = KnowledgeDocumentCreate(title='Old',content='Old private copy',source_name=source)
    with pytest.raises(ValueError, match='independent Wiki index'):
        store.upsert_document(payload)
    assert wiki.get_document(canonical.document_id).content == 'Current Wiki'
    assert wiki.count_documents() == 1
    assert store._catalog.catalog.snapshot() == before
    assert store._catalog.journal.records()[0] == []


@pytest.mark.parametrize('method', ['add_document','upsert_document','update_document'])
def test_missing_canonical_never_recreates_legacy(method,tmp_path):
    store = private_store(tmp_path/'private')
    payload = KnowledgeDocumentCreate(title='Old',content='Old',source_name=next(iter(SOURCE_OWNERSHIP)))
    args = ('id',payload) if method == 'update_document' else (payload,)
    with pytest.raises(ValueError):
        getattr(store,method)(*args)
    assert store.count_documents() == 0
    assert store._catalog.journal.records()[0] == []


def test_unique_material_not_retired():
    assert 'private-materials:how-to-think-about-research-topic' not in SOURCE_OWNERSHIP
    assert 'workspace/neuromem' not in SOURCE_OWNERSHIP


@pytest.mark.parametrize('ordinal', range(5, 9))
def test_public_course_is_wiki_owned(ordinal):
    assert SOURCE_OWNERSHIP[f'graduate-paper-writing-course:lecture-{ordinal}'] == f'wiki:tutorials/paper-writing-lecture-{ordinal}'
