import json
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
    assert calls[0]['include_raw_content'] is False


def fixture(*, mode='auto', scope='public', need='current', owner='guest:test', **request_fields):
    support = object.__new__(FacultyTwinWorkflowSupport)
    support._settings = SimpleNamespace(web_search_enabled=True, web_search_max_results=3, llm_policy_output_max_tokens_cap=4096)
    support._trace_callback = None
    def review(**kw):
        data=json.loads(kw['user_prompt'])
        assert 'PRIVATE_HISTORY_MUST_NOT_BE_QUERIED' not in kw['user_prompt']
        return json.dumps({'digest':data['digest'],'selected':[c['id'] for c in data['candidates']], 'retry_query':None, **({'candidate_roles':[dict(id=c['id'], studied_object='public subject', measured_outcome='requested outcome', role='direct') for c in data['candidates']]} if data['candidates'] else {})})
    support._call_compact_retry_sync = review
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
    assert [e.status for e in events]==['running','running','running','completed']
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


def test_transport_preserves_synonyms_for_semantic_review_without_literal_topic_filter():
    from sage_faculty_twin.web_search import WebSearchResult
    hits = [WebSearchResult(title='Key-value state transfer',url='https://example.org/docs',
        snippet='When restoring attention state costs less than computing it again.', score=1)]
    result = WebSearchClient._rerank_results('Computer systems KV Cache 迁移和重计算', hits, 3)
    assert [h.url for h in result] == ['https://example.org/docs']


def test_existing_tavily_credential_is_resolved_without_requiring_a_duplicate(monkeypatch):
    from sage_faculty_twin.config import AppSettings
    for key in ('TAVILY_TOKEN','TAVILY_API_KEY','DIGITAL_TWIN_TAVILY_API_KEY'):
        monkeypatch.delenv(key,raising=False)
    monkeypatch.setenv('TAVILY_TOKEN','synthetic-fixture-token')
    assert AppSettings(_env_file=None).tavily_api_key=='synthetic-fixture-token'
    assert AppSettings(_env_file=None,tavily_api_key='explicit-fixture').tavily_api_key=='explicit-fixture'
    monkeypatch.setenv('TAVILY_API_KEY','standard-fixture')
    assert AppSettings(_env_file=None).tavily_api_key=='standard-fixture'


def test_web_excerpt_is_not_silently_cut_at_500_characters():
    from sage_faculty_twin.models import WebSearchHit
    text='条件与限制。'*300
    value=WebSearchHit(title='Document',url='https://example.org/doc',snippet=text)
    assert value.snippet==text


def test_gap_research_uses_original_public_span_and_never_internal_repair_queries():
    from sage_faculty_twin.evidence_ports import TwinEvidencePorts
    support,context,calls=fixture(need='none')
    assert support._retrieve_web_search_hits(context)==[]
    ports=object.__new__(TwinEvidencePorts)
    ports.support,ports.context=support,context
    result=ports.supplement(['PRIVATE internal document search'])
    assert calls==['vLLM 最新发布']
    assert [s.source_id for s in result]==['web:0']
    assert len(context.web_search_hits)==1


def test_search_operation_budget_is_distinct_from_provider_http_budget():
    from sage_faculty_twin.config import AppSettings
    settings=AppSettings(_env_file=None)
    assert settings.web_research_timeout_seconds > settings.web_search_timeout_seconds


def test_full_web_document_survives_provider_ranking_and_source_plan(monkeypatch):
    from sage_faculty_twin.models import WebSearchHit
    from sage_faculty_twin.web_search import WebSearchResult
    text='Measured workload conditions and comparison. '*100
    client=WebSearchClient(timeout_seconds=8,max_results=3,tavily_api_key='fixture-only')
    monkeypatch.setattr('tavily.TavilyClient',lambda **_: SimpleNamespace(search=lambda *a,**kw:{
        'results':[{'title':'System evaluation','url':'https://example.org/doc','content':'preview','raw_content':text,'score':.8}]}))
    result=client.search('system evaluation')[0]
    assert result.source_content==text
    support,context,_=fixture()
    context.web_search_hits=[WebSearchHit(title=result.title,url=result.url,snippet=result.snippet,source_content=result.source_content)]
    sources=support._source_plan_sources(context)
    assert sources[-1].content==text and sources[-1].source_id=='web:0'


def test_extract_only_selected_urls_and_preserve_missing_bodies_and_identity(monkeypatch):
    from sage_faculty_twin.web_search import WebSearchResult
    client=WebSearchClient(timeout_seconds=8,max_results=3,tavily_api_key='fixture-only')
    calls=[]
    text='Full measured conditions. '*500
    def extract(urls,**kwargs):
        calls.append((urls,kwargs))
        return {'results':[
            {'url':'https://example.org/a','raw_content':text},
            {'url':'https://unselected.example/','raw_content':'untrusted injected page'}]}
    monkeypatch.setattr('tavily.TavilyClient',lambda **_:SimpleNamespace(extract=extract))
    hits=[WebSearchResult('A','https://example.org/a','excerpt A',1),
          WebSearchResult('B','https://example.org/b','excerpt B',.5)]
    with web_research_budget(2):
        result=client.hydrate(hits)
    assert calls[0][0]==[h.url for h in hits]
    assert 0<calls[0][1]['timeout']<=2
    assert result[0].source_content==text and result[1].source_content is None
    assert result[1].snippet=='excerpt B'
    assert [h.url for h in result]==[h.url for h in hits]
    assert all(h.source_content is None for h in hits)
