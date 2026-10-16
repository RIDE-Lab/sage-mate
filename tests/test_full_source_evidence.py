import pytest
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import ChatRequest, KnowledgeDocumentCreate, KnowledgeSearchHit
from sage_faculty_twin.service import DigitalTwinService, ChatWorkflowContext
from sage_faculty_twin.chat_contracts import EvidenceBundle


def fixture(tmp_path, *, materialize=True, audience='public'):
    service = DigitalTwinService(AppSettings(
        _env_file=None, knowledge_backend='local',
        conversation_memory_index_type='segment',
        knowledge_base_dir=tmp_path / 'private', wiki_index_dir=tmp_path / 'wiki',
        dynamic_context_materialization_enabled=materialize,
    ))
    content = '# Seven perspectives\n' + 'intro details ' * 120 + '\n6. 编辑视角\n7. 作者自己视角\nTAIL_FACT'
    store = service._wiki_store if audience == 'public' else service._knowledge_store
    origin = 'wiki' if audience == 'public' else 'private'
    record = store.add_document(KnowledgeDocumentCreate(
        title='Seven perspectives', content=content,
        tags=[f'audience:{audience}'], source_name=f'{origin}:test-seven',
    ))
    hit = KnowledgeSearchHit(document_id=f'{origin}::{record.document_id}', title=record.title,
        source_name=record.source_name, excerpt=content[:500], score=1, tags=record.tags)
    request = ChatRequest(student_name='test', question='List seven perspectives', visitor_profile='general_visitor')
    return service, record, hit, request


@pytest.mark.parametrize('materialize', [False, True])
def test_both_prompt_paths_receive_tail_not_preview(tmp_path, materialize):
    service, record, hit, request = fixture(tmp_path, materialize=materialize)
    support = service._build_support()
    prompt = support._build_student_prompt(request, [hit], [], [])
    assert record.content in prompt
    assert 'TAIL_FACT' in prompt
    assert len(hit.excerpt) == 500
    resolved = support._resolve_knowledge_evidence(request, [hit])
    assert resolved[0].source_content == record.content
    assert 'source_content' not in resolved[0].model_dump()
    assert EvidenceBundle.build(knowledge_hits=resolved, web_hits=[], memory_hits=[]).items[0].excerpt == record.content


@pytest.mark.parametrize('case', ['deleted', 'revoked', 'wrong_source'])
def test_stale_preview_cannot_disclose_full_source(tmp_path, case):
    service, record, hit, request = fixture(tmp_path)
    if case == 'deleted':
        service._wiki_store.delete_documents([record.document_id])
    elif case == 'revoked':
        service._wiki_store.update_document(record.document_id, KnowledgeDocumentCreate(
            title=record.title, content=record.content, source_name=record.source_name,
            tags=['audience:admin']))
    else:
        hit = hit.model_copy(update={'source_name':'wiki:wrong'})
    support = service._build_support()
    assert support._resolve_knowledge_evidence(request, [hit]) == []
    prompt = support._build_student_prompt(request, [hit], [], [])
    assert 'TAIL_FACT' not in prompt
    assert hit.excerpt not in prompt


def test_private_evidence_rechecks_role(tmp_path):
    service, record, hit, request = fixture(tmp_path, audience='lab_member')
    support = service._build_support()
    assert support._resolve_knowledge_evidence(request, [hit]) == []
    member_request = request.model_copy(update={'visitor_profile':'lab_member'})
    assert support._resolve_knowledge_evidence(member_request, [hit])[0].source_content == record.content


def test_updated_source_refreshes_preview_and_full_evidence(tmp_path):
    service, record, hit, request = fixture(tmp_path)
    hit = hit.model_copy(update={'excerpt':'STALE_REMOVED_FACT'})
    resolved = service._build_support()._resolve_knowledge_evidence(request, [hit])[0]
    assert 'STALE_REMOVED_FACT' not in resolved.excerpt
    assert resolved.source_content == record.content


def test_repair_prompt_keeps_same_full_evidence(tmp_path, monkeypatch):
    service, record, hit, request = fixture(tmp_path)
    support = service._build_support()
    context = ChatWorkflowContext(
        request=request, conversation_id='full-evidence-repair',
        owner_name='test', used_model='test',
        knowledge_hits=support._resolve_knowledge_evidence(request, [hit]),
    )
    def capture(**kwargs):
        assert record.content in kwargs['user_prompt']
        assert 'TAIL_FACT' in kwargs['user_prompt']
        raise RuntimeError('captured repair input')
    monkeypatch.setattr(support, '_call_compact_retry_sync', capture)
    with pytest.raises(RuntimeError, match='captured repair input'):
        support._retry_answer_with_compact_prompt(context)
