import json

import pytest
from sage.runtime.source_audit import SourceAuditRejected
from sage.runtime.flownet import FlowRequestOutcomeError

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import ChatRequest, KnowledgeSearchHit, WebSearchHit
from sage_faculty_twin.service import ChatWorkflowContext, FacultyTwinWorkflowSupport


def setup_gate(tmp_path):
    support = object.__new__(FacultyTwinWorkflowSupport)
    support._settings = AppSettings(knowledge_base_dir=tmp_path, web_search_enabled=False)
    support._trace_callback = None
    support._admin_session_payload = None
    context = ChatWorkflowContext(
        request=ChatRequest(student_name="访客", question="请根据 Wiki 说明状态"),
        conversation_id="audit-test", owner_name="test", used_model="test",
        answer=None,
        knowledge_hits=[KnowledgeSearchHit(
            document_id="wiki-test", title="状态", excerpt="摘要",
            source_content="系统仍在研究。本文不展示测量数据，完整结果见技术报告。",
            score=1, metadata={"wiki_url": "https://example.org/full/path/"},
        )],
    )
    return support, context


def plan_response(payload):
    data = json.loads(payload)
    if "units" in data:
        from tests.natural_answer_stub import response
        from sage.runtime.natural_answer import NATURAL_AUDIT_PROMPT
        return response(NATURAL_AUDIT_PROMPT, payload)
    if "requirements" not in data and "sources" in data:
        from tests.natural_answer_stub import response
        from sage.runtime.natural_answer import NATURAL_ANSWER_PROMPT
        return response(NATURAL_ANSWER_PROMPT, payload)
    if "requirements" in data:
        return json.dumps({"digest": data["digest"], **({"source_roles": [dict(source_id=s["source_id"], studied_subject="fixture", role="direct") for s in data["sources"]]} if data["purpose"] == "source_support" else {}), "items": [
            {"requirement_id": r["requirement_id"], "witnesses": [p["id"] for p in data["sources"][0]["paragraphs"]]}
            for r in data["requirements"]]})
    return json.dumps({"digest": data["digest"], "source_ids": ["knowledge:0"], "inferences": []})


def test_render_preserves_full_source_and_receipt(tmp_path):
    support, context = setup_gate(tmp_path)
    calls = []
    def plan(**kwargs):
        calls.append(kwargs)
        return plan_response(kwargs["user_prompt"])
    support._call_compact_retry_sync = plan
    response = support.render_chat_response(context)
    assert response.evidence_coverage.request_digest == context.evidence_work.request.digest
    assert response.evidence_coverage.missing_parts == []
    answer = response.answer
    from sage_faculty_twin.models import ChatResponse
    restored = ChatResponse.model_validate_json(response.model_dump_json())
    assert restored.answer_blocks == response.answer_blocks
    assert restored.answer_blocks[0].kind == "answer" and restored.answer_blocks[0].source_url is None
    assert "\n\n".join(block.markdown for block in restored.answer_blocks).strip() == answer
    corrupted = response.model_dump(mode="json")
    corrupted["answer"] += "unsupported"
    with pytest.raises(ValueError, match="canonical answer"):
        ChatResponse.model_validate(corrupted)
    assert context.knowledge_hits[0].source_content in answer
    assert "https://example.org/full/path/" not in answer
    assert response.answer_basis[0].source_url == "https://example.org/full/path/"
    assert context.knowledge_hits[0].source_content in response.answer_basis[0].detail
    assert "作者没有测量" not in answer
    support._validate_source_answer(context)
    assert len(calls) == 4  # Source coverage, natural writing, per-unit audit, answer completeness.
    assert context.source_audit_digest
    context.answer += "作者没有测量"
    with pytest.raises(SourceAuditRejected):
        support._validate_source_answer(context)


def test_changed_source_invalidates_receipt(tmp_path):
    support, context = setup_gate(tmp_path)
    support._call_compact_retry_sync = lambda **kw: plan_response(kw["user_prompt"])
    support._prepare_source_answer(context)
    context.knowledge_hits[0].source_content += "changed"
    with pytest.raises(SourceAuditRejected):
        support._validate_source_answer(context)


def test_web_candidates_reach_same_sage_coverage_plan_and_selected_basis(tmp_path):
    support, context = setup_gate(tmp_path)
    context.request.question = "ExampleRelease 当前版本是什么？请提供发布链接。"
    context.knowledge_hits = []
    context.web_search_hits = [WebSearchHit(title="ExampleRelease release", url="https://example.org/releases/v2",
        snippet="ExampleRelease latest non-prerelease release: v2. Published at 2026-10-20.",
        retrieved_at="2026-10-21T00:00:00+00:00")]
    def call(**kwargs):
        data = json.loads(kwargs["user_prompt"])
        return plan_response(kwargs["user_prompt"])
    support._call_compact_retry_sync = call
    support._prepare_source_answer(context)
    result = support.render_chat_response(context)
    assert context.web_search_hits[0].snippet in result.answer
    assert context.web_search_hits[0].url not in result.answer
    assert result.answer_basis[0].source_url == context.web_search_hits[0].url
    assert "2026-10-21" in result.answer_basis[0].detail
    context.web_search_hits[0].snippet += "changed"
    with pytest.raises(SourceAuditRejected):
        support._validate_source_answer(context)


def test_selected_sources_keep_complete_candidate_binding_without_manual_overflow(tmp_path):
    support, context = setup_gate(tmp_path)
    context.request.question = "ExampleState 是什么？"
    context.knowledge_hits[0].title = "Wiki | ExampleState"
    context.knowledge_hits[0].source_content = "ExampleState 管理状态。"
    context.knowledge_hits.insert(0, KnowledgeSearchHit(document_id="manual", title="Wiki | unrelated manual",
        excerpt="preview", source_content="unrelated tutorial\n"*20000, score=0.5))
    calls = []
    def call(**kwargs):
        calls.append(kwargs)
        assert "unrelated tutorial" not in kwargs["user_prompt"]
        data = json.loads(kwargs["user_prompt"])
        return plan_response(kwargs["user_prompt"])
    support._call_compact_retry_sync = call
    response = support.render_chat_response(context)
    assert len(calls) == 4
    assert len(context.evidence_work.candidates) == 2
    assert context.source_answer_plan.source_ids == ("knowledge:1",)
    assert "unrelated tutorial" not in response.answer
    assert len(response.answer_basis) == 1
    assert response.knowledge_hits[0].evidence_source_id == "knowledge:1"
    support._validate_source_answer(context)
    context.knowledge_hits[0].source_content += "changed"
    with pytest.raises(SourceAuditRejected):
        support._validate_source_answer(context)


@pytest.mark.parametrize("raw", ["OK", "{}", "null"])
def test_invalid_plan_never_publishes_or_repairs(tmp_path, raw):
    support, context = setup_gate(tmp_path)
    support._call_compact_retry_sync = lambda **kw: raw
    with pytest.raises(FlowRequestOutcomeError) as caught:
        support.render_chat_response(context)
    assert caught.value.error_type == "InvalidEvidenceCoverage"
    assert context.answer is None
    assert not context.persisted_memory_record


def test_transport_error_propagates(tmp_path):
    support, context = setup_gate(tmp_path)
    def unavailable(**kw):
        raise TimeoutError("test")
    support._call_compact_retry_sync = unavailable
    with pytest.raises(FlowRequestOutcomeError) as caught:
        support.render_chat_response(context)
    assert caught.value.error_type == "TimeoutError"


def test_source_plan_bypasses_free_generation_and_draft_stream(tmp_path):
    support, context = setup_gate(tmp_path)
    support._answer_chunk_callback = lambda text: pytest.fail("draft token published")
    support._call_answer_question_sync = lambda *a, **kw: pytest.fail("free factual generation")
    support._call_compact_retry_sync = lambda **kw: plan_response(kw["user_prompt"])
    support.answer_with_llm(context)
    assert context.source_answer_plan is not None
    assert context.knowledge_hits[0].source_content in context.answer


def test_unselected_sources_are_not_cited(tmp_path):
    support, context = setup_gate(tmp_path)
    context.knowledge_hits.append(context.knowledge_hits[0].model_copy(update={
        "document_id": "unrelated", "title": "不相关", "source_content": "不相关材料",
    }))
    support._call_compact_retry_sync = lambda **kw: plan_response(kw["user_prompt"])
    result = support.render_chat_response(context)
    assert len(result.knowledge_hits) == 1
    assert len(result.answer_basis) == 1
    assert len(context.knowledge_hits) == 2
    support._validate_source_answer(context)


def test_source_grounded_route_with_zero_hits_repairs_then_reports_unknown_not_free_generation(tmp_path):
    from types import SimpleNamespace
    from sage.runtime.adaptive_workflow import WorkflowCapabilities, WorkflowRequirements, plan_workflow
    support, context = setup_gate(tmp_path)
    context.knowledge_hits = []
    context.adaptive_plan = plan_workflow(WorkflowRequirements(requested_sources=("wiki",)),
        WorkflowCapabilities("guest", ("wiki",), False))
    calls = []
    def model(**kwargs):
        calls.append(kwargs)
        # Retrieval planning may propose search terms, never assess nonexistent
        # evidence or generate an ungrounded answer.
        assert kwargs["cache_namespace"].startswith("evidence-search-plan:")
        payload = json.loads(kwargs["user_prompt"])
        return json.dumps({"digest": payload["digest"], "queries": ["alternative source terms"]})
    support._call_compact_retry_sync = model
    support._call_answer_question_sync = lambda *a, **kw: pytest.fail("ungrounded free generation")
    support._knowledge_retriever = SimpleNamespace(search=lambda *a, **kw: [])
    support.answer_with_llm(context)
    result = support.render_chat_response(context)
    assert context.evidence_work.repair_attempted
    assert len(calls) == 1 and len(context.workflow_trace) == 3  # Includes explicit skipped web capability; no source audit/model answer.
    assert "仍待补充的依据" in result.answer and context.request.question in result.answer
    assert result.knowledge_hits == []
    assert "\n\n".join(block.markdown for block in result.answer_blocks).strip() == result.answer
    support._validate_source_answer(context)


@pytest.mark.parametrize("transport", ["http", "slack"])
@pytest.mark.parametrize("error_type", ["InvalidSourcePlan", "InvalidEvidenceCoverage", "InvalidResearchPlan"])
def test_wrapped_invalid_source_plan_is_terminal(transport, error_type):
    from types import SimpleNamespace
    from sage.runtime.flownet.client.runtime_client import FlowRequestOutcomeError
    from sage_faculty_twin.http_workflow import HttpWorkflow
    from sage_faculty_twin.slack_workflow import SlackWorkflow
    error = FlowRequestOutcomeError(
        request_id="test", outcome_status="failed", error_type=error_type,
        message="stale", error_stage="generate", outcome_metadata={}, outputs=[],
    )
    workflow = object.__new__(HttpWorkflow if transport == "http" else SlackWorkflow)
    classify = workflow._failure if transport == "http" else workflow._classify_failure
    assert classify(SimpleNamespace(attempt=1), error).retry_delay is None


@pytest.mark.parametrize("wrapped", [False, True])
def test_audit_rejection_never_retries_durable_generation(wrapped):
    from types import SimpleNamespace
    from sage.runtime.flownet.client.runtime_client import FlowRequestOutcomeError
    from sage_faculty_twin.http_workflow import HttpWorkflow
    workflow = object.__new__(HttpWorkflow)
    error = SourceAuditRejected("unsupported")
    if wrapped:
        error = FlowRequestOutcomeError(
            request_id="test", outcome_status="failed", error_type="SourceAuditRejected",
            message="unsupported", error_stage="render", outcome_metadata={}, outputs=[],
        )
    failure = workflow._failure(SimpleNamespace(attempt=1), error)
    assert failure.code == "source_audit_rejected"
    assert failure.retry_delay is None


@pytest.mark.parametrize("wrapped", [False, True])
@pytest.mark.parametrize("transport", ["http", "slack"])
def test_quality_rejection_is_terminal_for_all_transports(wrapped, transport):
    from types import SimpleNamespace
    from sage.runtime.source_audit import AnswerValidationRejected
    from sage.runtime.flownet.client.runtime_client import FlowRequestOutcomeError
    from sage_faculty_twin.http_workflow import HttpWorkflow
    from sage_faculty_twin.slack_workflow import SlackWorkflow
    error = AnswerValidationRejected("repair exhausted")
    if wrapped:
        error = FlowRequestOutcomeError(
            request_id="test", outcome_status="failed", error_type="AnswerValidationRejected",
            message="repair exhausted", error_stage="generate", outcome_metadata={}, outputs=[],
        )
    workflow = object.__new__(HttpWorkflow if transport == "http" else SlackWorkflow)
    classify = workflow._failure if transport == "http" else workflow._classify_failure
    failure = classify(SimpleNamespace(attempt=1), error)
    assert failure.code == "answer_validation_rejected"
    assert failure.retry_delay is None


def test_captured_dialogue_reaches_sage_and_visible_basis_without_becoming_wiki(tmp_path):
    from sage.runtime.source_answer_plan import PlanSource
    support, context = setup_gate(tmp_path)
    context.request.question = "那段建议新增了什么解释？"
    context.knowledge_hits = []
    source = PlanSource("conversation:turn", "本会话先前问答", "", "Assistant: 30% 是实验目标，不是实测结果。", kind="conversation")
    context.conversation_sources = (source,)
    assert support._requires_source_audit(context)  # Dialogue-only evidence must not bypass verification.
    kinds = []
    def call(**kwargs):
        data = json.loads(kwargs["user_prompt"])
        for item in data.get("sources", []):
            if item.get("source_id", item.get("id")) == source.source_id:
                kinds.append(item["kind"])
        return plan_response(kwargs["user_prompt"])
    support._call_compact_retry_sync = call
    support._prepare_source_answer(context)
    response = support.render_chat_response(context)
    assert kinds and set(kinds) == {"conversation"}
    assert response.answer_basis[0].source_url is None
    assert response.answer_basis[0].basis_label == "对话上下文"
    assert "不是外部事实来源" in response.answer_basis[0].source_label
    assert response.answer_basis[0].detail == source.content
    context.conversation_sources = (PlanSource(source.source_id, source.title, "", "altered", kind="conversation"),)
    with pytest.raises(SourceAuditRejected):
        support._validate_source_answer(context)


def test_conversation_evidence_requires_exact_owner_and_session(tmp_path):
    from types import SimpleNamespace
    support, context = setup_gate(tmp_path)
    context.request.owner_id = "account:caller"
    context.request.conversation_id = "session"
    calls = []
    def records(session, **kwargs):
        calls.append((session, kwargs))
        return [SimpleNamespace(memory_id="ok", owner_id="account:caller", conversation_id="session", question="Q", answer="A"*20000),
                SimpleNamespace(memory_id="other-owner", owner_id="account:other", conversation_id="session", question="secret", answer="secret"),
                SimpleNamespace(memory_id="other-session", owner_id="account:caller", conversation_id="other", question="secret", answer="secret")]
    support._conversation_store = SimpleNamespace(list_recent_conversation_records=records)
    sources = support._recent_conversation_sources(context.request)
    assert calls[0][0] == "session" and calls[0][1]["owner_id"] == "account:caller"
    assert len(sources) == 1 and sources[0].kind == "conversation"
    assert sources[0].content.endswith("A"*20000)  # No display-preview truncation.
    context.request.owner_id = None
    assert support._recent_conversation_sources(context.request) == ()


def test_bootstrap_keeps_history_for_followup_without_magic_keywords(tmp_path):
    from sage_faculty_twin.service import DigitalTwinService
    from sage_faculty_twin.context_routing import question_needs_recent_context
    import asyncio
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path / 'kb', conversation_memory_dir=tmp_path / 'memory'))
    try:
        support = service._build_support()
        request = ChatRequest(student_name='test', owner_id='account:caller', conversation_id='session', question='推论／建议（非来源事实）新增了什么解释')
        assert not question_needs_recent_context(request.question)
        captured = []
        support._format_recent_session_context = lambda req: captured.append(req.conversation_id) or 'Previous full answer'
        support._recent_conversation_sources = lambda req: ()
        context = support.bootstrap_chat(request)
        assert captured == ['session']
        assert context.recent_session_context == 'Previous full answer'
    finally:
        asyncio.run(service.aclose())


def test_dialogue_receipt_survives_adapter_grouping_order_but_not_content_changes(tmp_path):
    from dataclasses import replace
    from sage.runtime.source_answer_plan import PlanSource
    support, context = setup_gate(tmp_path)
    context.conversation_sources = (PlanSource("conversation:turn", "Prior turn", "", "User: Status?", kind="conversation"),)
    support._call_compact_retry_sync = lambda **kw: plan_response(kw["user_prompt"])
    support._prepare_source_answer(context)
    # The supplement workflow appends new documents after existing conversation
    # records, while Twin groups documents first. Order alone is not a mutation.
    original_sources = support._source_plan_sources
    support._source_plan_sources = lambda ctx: tuple(reversed(original_sources(ctx)))
    support._validate_source_answer(context)
    context.conversation_sources = (replace(context.conversation_sources[0], content="mutated"),)
    with pytest.raises(SourceAuditRejected):
        support._validate_source_answer(context)


def test_advisory_branch_does_not_become_fact_answer_just_because_history_exists(tmp_path):
    from sage.runtime.source_answer_plan import PlanSource
    support, context = setup_gate(tmp_path)
    context.conversation_sources = (PlanSource("c", "Prior", "", "User: Meeting preparation", kind="conversation"),)
    context.knowledge_hits = []
    context.workflow_action = "advise_only"
    assert not support._requires_source_audit(context)


def test_evidence_composer_uses_configured_sampling_but_classifier_stays_neutral():
    from types import SimpleNamespace
    from sage_faculty_twin.evidence_ports import TwinEvidencePorts
    from sage.runtime.natural_answer import prepare_natural_answer, validate_natural_answer
    from sage.runtime.source_answer_plan import PlanSource
    ports = object.__new__(TwinEvidencePorts)
    calls = []
    def model(**kw):
        calls.append(kw)
        data = json.loads(kw['user_prompt'])
        if 'rejected_answer' in data:
            return json.dumps(dict(digest=data['digest'], replacements={'1': None}, additional_paragraphs=[]))
        return json.dumps(dict(digest=data['digest'], paragraphs=['Supported.']))
    ports.support = SimpleNamespace(_settings=AppSettings(llm_answer_temperature=.7), _call_compact_retry_sync=model)
    request = prepare_natural_answer('Question?', (PlanSource('s', 'Doc', '', 'Supported.'),))
    ports.write_answer(request)
    result = ports.write_answer(request, 'Supported. Unsupported.', ('unit:1',))
    assert validate_natural_answer(request, result) == 'Supported.'
    assert all(c['temperature'] == .7 and c['neutral_sampling'] is False for c in calls)
    ports._call('classifier', '{"digest":"test"}', namespace='test', response_format={})
    assert calls[-1]['temperature'] == 0 and calls[-1]['neutral_sampling'] is True


def test_native_audit_requirement_cannot_silently_fall_back():
    from types import SimpleNamespace
    from sage_faculty_twin.evidence_ports import TwinEvidencePorts
    from sage.runtime.source_audit import prepare_source_audit
    ports = object.__new__(TwinEvidencePorts)
    ports.support = SimpleNamespace(_settings=AppSettings(llm_evidence_audit_thinking=True),
        _llm_client=SimpleNamespace(supports_native_thinking=False),
        _call_compact_retry_sync=lambda **_: pytest.fail('must not call unsupported endpoint'))
    request = prepare_source_audit('Status?', 'Fact.', {'s':'Fact.'})
    with pytest.raises(RuntimeError, match='verified native thinking'):
        ports.audit_answer(request)
