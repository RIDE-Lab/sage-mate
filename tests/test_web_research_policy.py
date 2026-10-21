from types import SimpleNamespace
import pytest
from sage_faculty_twin.models import ChatRequest, InteractionIntent
from sage_faculty_twin.service import ChatWorkflowContext, FacultyTwinWorkflowSupport
from sage_faculty_twin.web_search import WebSearchClient
from sage_faculty_twin.llm_client import VllmChatClient
from sage.runtime.web_research import web_research_budget


def test_tavily_receives_remaining_sage_deadline_not_sdk_default(monkeypatch):
    calls=[]
    transport=WebSearchClient(timeout_seconds=8,max_results=3,tavily_api_key='fixture-only')
    def search(*args,**kwargs):
        calls.append(kwargs)
        return {'results':[]}
    monkeypatch.setattr('tavily.TavilyClient',lambda **_:SimpleNamespace(search=search))
    with web_research_budget(2):
        assert transport._search_tavily('public subject',3)==[]
    assert 0<calls[0]['timeout']<=2


def fixture(*, mode='auto', scope='public', need='current', owner='guest:test', **request_fields):
    support = object.__new__(FacultyTwinWorkflowSupport)
    support._settings = SimpleNamespace(web_search_enabled=True, web_search_max_results=3)
    support._trace_callback = None
    calls = []
    support._web_search_client = SimpleNamespace(enabled=True, search=lambda query, **_: calls.append(query) or [
        SimpleNamespace(title='Public source',url='https://example.org/full/path',snippet='Full evidence',score=1)])
    context = ChatWorkflowContext(request=ChatRequest(student_name='test',owner_id=owner,
        question='vLLM 最新发布',web_mode=mode,**request_fields),conversation_id='test',owner_name='test',used_model='test',
        interaction_intent=InteractionIntent(web_intent=dict(need=need,disclosure=scope,
            query='vLLM 最新发布' if scope=='public' else None)))
    context.recent_session_context = 'PRIVATE_HISTORY_MUST_NOT_BE_QUERIED'
    return support,context,calls


@pytest.mark.parametrize('owner',['guest:test','account:test','api_token:test','slack:test'])
def test_actual_adapter_search_uses_only_public_current_span_and_streams_real_status(owner):
    support,context,calls=fixture(owner=owner)
    events=[]
    support._trace_callback=events.append
    hits=support._retrieve_web_search_hits(context)
    assert calls==['vLLM 最新发布']
    assert hits[0].retrieved_at and hits[0].url=='https://example.org/full/path'
    assert [e.status for e in events]==['running','completed']
    assert events[-1].sources[0].url == hits[0].url
    assert events[-1].sources[0].retrieved_at == hits[0].retrieved_at
    assert all(e.key=='web_search' for e in events)


@pytest.mark.parametrize('kwargs',[
    dict(mode='never'),dict(mode='always',scope='private'),dict(mode='always',scope='unknown'),
    dict(need='none'),dict(need='forbidden'),dict(knowledge_sources=['wiki']),
    dict(knowledge_sources=['private']),dict(slack_channel_context='PRIVATE_CHANNEL_HISTORY'),
])
def test_no_external_request_when_not_authorized_or_not_needed(kwargs):
    support,context,calls=fixture(**kwargs)
    assert support._retrieve_web_search_hits(context)==[]
    assert calls==[] and context.workflow_trace[-1].status=='skipped'


def test_real_failure_is_not_reported_as_completed_or_empty_search():
    support,context,calls=fixture()
    def broken(*args,**kwargs):
        raise TimeoutError('private request details')
    support._web_search_client.search=broken
    assert support._retrieve_web_search_hits(context)==[]
    assert context.workflow_trace[-1].status=='failed'
    support._web_search_client.search=lambda *args,**kwargs:[SimpleNamespace(
        title='source',url='javascript:alert(1)',snippet='invalid',score=1)]
    assert support._retrieve_web_search_hits(context)==[]
    assert context.workflow_trace[-1].status=='failed' and not context.workflow_trace[-1].sources
    assert 'private request details' not in str(context.workflow_trace)


def test_long_source_urls_are_not_silently_truncated_and_invalid_candidates_are_not_completed():
    support,context,calls=fixture()
    url='https://example.org/source?ref='+'a'*2000
    support._web_search_client.search=lambda *args,**kwargs:[SimpleNamespace(
        title='source',url=url,snippet='evidence',score=1)]
    assert support._retrieve_web_search_hits(context)[0].url==url
    support._web_search_client.search=lambda *args,**kwargs:[SimpleNamespace(
        title='',url=url,snippet='invalid',score=1)]
    assert support._retrieve_web_search_hits(context)==[]
    assert context.workflow_trace[-1].status=='failed'


def test_control_contract_and_legacy_boolean_do_not_override_explicit_mode():
    assert ChatRequest(student_name='t',question='q').web_mode=='auto'
    assert ChatRequest(student_name='t',question='q',web_search=True).web_mode=='always'
    assert ChatRequest(student_name='t',question='q',web_search=False).web_mode=='never'
    assert ChatRequest(student_name='t',question='q',web_search=False,web_mode='auto').web_mode=='auto'


def test_transport_distinguishes_provider_outage_from_successful_empty_response(monkeypatch):
    client=WebSearchClient(timeout_seconds=2,max_results=3)
    def unavailable(*args,**kwargs):
        raise TimeoutError()
    monkeypatch.setattr(client,'_search_bing_rss',unavailable)
    monkeypatch.setattr(client,'_search_bing_html',unavailable)
    with pytest.raises(RuntimeError,match='providers_unavailable'):
        client.search('public documentation')
    monkeypatch.setattr(client,'_search_bing_rss',lambda *args: [])
    assert client.search('public documentation')==[]


def test_intent_shape_repair_never_calls_answer_generation_or_its_thinking_defaults():
    client=object.__new__(VllmChatClient)
    calls=[]
    client._request_intent_classification=lambda system,user: calls.append((system,user)) or '{"task_mode":"facts"}'
    client.answer_question_sync=lambda *args,**kwargs: pytest.fail('repair must stay on intent protocol')
    assert client._repair_interaction_intent_payload('{broken','fixture schema error')=='{"task_mode":"facts"}'
    assert len(calls)==1 and 'web_intent' in calls[0][0]
