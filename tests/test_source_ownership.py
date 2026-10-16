from types import SimpleNamespace
import pytest
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.models import KnowledgeDocumentCreate
from sage_faculty_twin.source_ownership import SOURCE_OWNERSHIP


def fake_store(documents):
    store = LocalKnowledgeStore.__new__(LocalKnowledgeStore)
    store._documents = {str(i):doc for i,doc in enumerate(documents)}
    return store


@pytest.mark.parametrize('source,target', SOURCE_OWNERSHIP.items())
def test_old_import_reuses_canonical_without_mutation(source,target):
    canonical = SimpleNamespace(source_name=target, content='Current Wiki',created_at=0,document_id='canonical')
    store = fake_store([canonical])
    payload = KnowledgeDocumentCreate(title='Old',content='Old private copy',source_name=source)
    assert store.upsert_document(payload) == (canonical,False)
    assert canonical.content == 'Current Wiki'
    assert len(store._documents) == 1


@pytest.mark.parametrize('method', ['add_document','upsert_document','update_document'])
def test_missing_canonical_never_recreates_legacy(method):
    store = fake_store([])
    payload = KnowledgeDocumentCreate(title='Old',content='Old',source_name=next(iter(SOURCE_OWNERSHIP)))
    args = ('id',payload) if method == 'update_document' else (payload,)
    with pytest.raises(ValueError):
        getattr(store,method)(*args)
    assert not store._documents


def test_unique_material_not_retired():
    assert 'private-materials:how-to-think-about-research-topic' not in SOURCE_OWNERSHIP
    assert 'workspace/neuromem' not in SOURCE_OWNERSHIP


@pytest.mark.parametrize('ordinal', range(5, 9))
def test_public_course_is_wiki_owned(ordinal):
    assert SOURCE_OWNERSHIP[f'graduate-paper-writing-course:lecture-{ordinal}'] == f'wiki:tutorials/paper-writing-lecture-{ordinal}'
