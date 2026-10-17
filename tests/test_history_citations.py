from types import SimpleNamespace
from unittest.mock import Mock

from sage_faculty_twin.service import DigitalTwinService


def test_exact_public_source_recovery_includes_all_chunks_without_generation():
    service = object.__new__(DigitalTwinService)
    service._knowledge_retriever = Mock()
    service._knowledge_retriever.public_documents.return_value = [
        SimpleNamespace(metadata={'wiki_url': 'https://example.org/page/', 'wiki_chunk': '2'}, content='## Second\n\nTail'),
        SimpleNamespace(metadata={'wiki_url': 'https://example.org/page/', 'wiki_chunk': '1'}, content='# First'),
    ]
    result = service.recover_public_citation('https://example.org/page/#section')
    assert result['content'] == '# First\n\n## Second\n\nTail'
    assert result['status'] == 'restored'
    assert '当前' in result['notice'] and '原回答未改动' in result['notice']
    for url in ['https://example.org/other', 'http://127.0.0.1/secrets', 'file:///etc/passwd', '']:
        assert service.recover_public_citation(url)['status'] == 'unavailable'


def test_revoked_source_is_not_recovered_from_a_cached_identity():
    service = object.__new__(DigitalTwinService)
    service._knowledge_retriever = Mock()
    service._knowledge_retriever.public_documents.return_value = []
    assert service.recover_public_citation('https://example.org/previously-public')['status'] == 'unavailable'
