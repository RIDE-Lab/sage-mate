from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import KnowledgeSearchHit
from sage_faculty_twin.service import DigitalTwinService


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
        source_name='test', excerpt=markdown, score=1.0,
        metadata={'retrieval_source': 'wiki'})
    basis = support._build_knowledge_basis_item(hit)
    assert basis.detail == markdown
    assert basis.model_dump()['detail'].endswith('[原文](https://example.org/wiki)')
