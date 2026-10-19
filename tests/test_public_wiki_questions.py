from types import SimpleNamespace
from unittest.mock import Mock
from sage_faculty_twin.service import DigitalTwinService


def test_suggestions_use_current_public_catalog_and_deduplicate_chunks():
    service = object.__new__(DigitalTwinService)
    service._llm_client = Mock()
    service._knowledge_retriever = SimpleNamespace(public_documents=lambda: [
        SimpleNamespace(title='Wiki | Research guide', source_name='wiki:guide', metadata={'wiki_url':'https://example.org/guide'}),
        SimpleNamespace(title='Wiki | Research guide', source_name='wiki:guide#chunk-2', metadata={'wiki_page_source':'wiki:guide'}),
        SimpleNamespace(title='Wiki | Writing', source_name='wiki:writing', metadata={}),
    ])
    questions = service.public_wiki_questions()
    assert len(questions) == 2
    assert all('依据' not in item['question'] and '原文链接' not in item['question'] for item in questions)
    assert all(item['question'].endswith('适合解决哪些问题？') for item in questions)
    assert service.generate_lucky_question(recent_questions=[questions[0]['question']]) == questions[1]
    service._llm_client.generate_lucky_question_sync.assert_not_called()
    service._knowledge_retriever.public_documents = lambda: []
    assert service.public_wiki_questions() == []
    assert service.generate_lucky_question() == {}
