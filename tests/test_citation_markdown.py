from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import KnowledgeSearchHit, ChatRequest
from sage_faculty_twin.service import DigitalTwinService, ChatWorkflowContext


def test_citation_preserves_complete_markdown(tmp_path):
    support = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))._build_support()
    markdown = (
        '# Heading\n\n- first\n- second\n\n'
        '| Name | Value |\n| --- | --- |\n| a | b |\n\n'
        '```python\n    print("hello")\n```\n\n'
        + '完整材料\n' * 300
        + '\n[原文](https://example.org/wiki)'
    )
    hit = KnowledgeSearchHit(document_id='wiki::test', title='Wiki test',
        source_name='test', excerpt='collapsed retrieval preview', source_content=markdown, score=1.0,
        metadata={'retrieval_source': 'wiki'})
    basis = support._build_knowledge_basis_item(hit)
    assert basis.detail == markdown
    assert basis.model_dump()['detail'].endswith('[原文](https://example.org/wiki)')


def test_same_source_chunks_are_merged_without_dropping_later_sources(tmp_path):
    support = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))._build_support()
    chunks = [KnowledgeSearchHit(document_id=f'wiki::{n}', title='Same page', source_name=f'wiki:page::part-{n}',
        excerpt='preview', source_content=f'chunk {n}', score=1,
        metadata={'wiki_chunk': str(n), 'wiki_page_source': 'wiki:page', 'wiki_url': 'https://example.org/page'}) for n in [2,1]]
    others = [KnowledgeSearchHit(document_id=f'other-{n}', title=f'Other {n}', source_name=f'other-{n}', excerpt=f'body {n}', score=1) for n in range(6)]
    context = ChatWorkflowContext(request=ChatRequest(student_name='test', question='Compare these sources'),
        conversation_id='test', owner_name='Owner', used_model='test', knowledge_hits=chunks+others)
    basis = support._build_answer_basis(context)
    assert len(basis) == 7
    assert basis[0].detail == 'chunk 1\n\nchunk 2'
    assert basis[0].source_url == 'https://example.org/page'
