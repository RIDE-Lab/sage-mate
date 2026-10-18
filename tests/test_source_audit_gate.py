import json

import pytest
from sage.runtime.source_audit import SourceAuditRejected

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.models import ChatRequest, KnowledgeSearchHit
from sage_faculty_twin.service import ChatWorkflowContext, FacultyTwinWorkflowSupport


def setup_gate(tmp_path):
    support = object.__new__(FacultyTwinWorkflowSupport)
    support._settings = AppSettings(knowledge_base_dir=tmp_path)
    support._trace_callback = None
    context = ChatWorkflowContext(
        request=ChatRequest(student_name="访客", question="请根据 Wiki 说明状态"),
        conversation_id="audit-test", owner_name="test", used_model="test",
        answer="系统仍在研究。",
        knowledge_hits=[KnowledgeSearchHit(
            document_id="wiki-test", title="状态", excerpt="摘要",
            source_content="系统仍在研究。", score=1,
            metadata={"wiki_url": "https://example.org/full/path/"},
        )],
    )
    return support, context


def audit_response(payload, supported=True):
    data = json.loads(payload)
    return json.dumps({"digest": data["digest"], "units": [
        {"id": unit["id"], "verdict": "supported" if supported else "unsupported",
         "evidence": [{"source_id": "knowledge:0", "quote": "系统仍在研究。"}]
         if supported else []}
        for unit in data["units"]
    ]})


def test_render_audits_full_sources_and_exact_input_cache(tmp_path):
    support, context = setup_gate(tmp_path)
    calls = []
    def audit(**kwargs):
        calls.append(kwargs)
        return audit_response(kwargs["user_prompt"])
    support._call_compact_retry_sync = audit
    assert support.render_chat_response(context).answer == context.answer
    data = json.loads(calls[0]["user_prompt"])
    assert "系统仍在研究。" in data["sources"][0]["text"]
    assert "https://example.org/full/path/" in data["sources"][0]["text"]
    assert calls[0]["cache_namespace"] == "source-audit:" + data["digest"]
    support._validate_source_answer(context)
    assert len(calls) == 1
    context.answer = "不同回答"
    support._validate_source_answer(context)
    assert len(calls) == 2
    assert calls[0]["cache_namespace"] != calls[1]["cache_namespace"]


def test_repair_is_reaudited_not_published_on_second_failure(tmp_path):
    support, context = setup_gate(tmp_path)
    calls = []
    def audit(**kwargs):
        calls.append(kwargs)
        return audit_response(kwargs["user_prompt"], False)
    support._call_compact_retry_sync = audit
    support._retry_answer_with_compact_prompt = lambda context, **kwargs: "修复后仍不支持"
    with pytest.raises(SourceAuditRejected, match="未通过来源事实"):
        support.render_chat_response(context)
    assert len(calls) == 2
    assert calls[0]["cache_namespace"] != calls[1]["cache_namespace"]
    assert context.source_audit_digest is None
    assert not context.persisted_memory_record


def test_supported_repair_passes_with_new_digest(tmp_path):
    support, context = setup_gate(tmp_path)
    calls = []
    def audit(**kwargs):
        calls.append(kwargs)
        return audit_response(kwargs["user_prompt"], len(calls) == 2)
    support._call_compact_retry_sync = audit
    support._retry_answer_with_compact_prompt = lambda context, **kwargs: "系统仍在研究。修复版"
    support._validate_source_answer(context)
    assert context.source_audit_digest == json.loads(calls[1]["user_prompt"])["digest"]


@pytest.mark.parametrize("raw", ["OK", "{}", "null"])
def test_malformed_auditor_never_triggers_answer_rewrite(tmp_path, raw):
    support, context = setup_gate(tmp_path)
    support._call_compact_retry_sync = lambda **kwargs: raw
    def forbidden(context):
        pytest.fail("malformed audit must not rewrite answer")
    support._retry_answer_with_compact_prompt = forbidden
    with pytest.raises(SourceAuditRejected, match="校验暂不可用"):
        support.render_chat_response(context)
    assert context.source_audit_digest is None


def test_transport_failure_propagates(tmp_path):
    support, context = setup_gate(tmp_path)
    def unavailable(**kwargs):
        raise TimeoutError("test")
    support._call_compact_retry_sync = unavailable
    with pytest.raises(TimeoutError):
        support.render_chat_response(context)


def test_changed_evidence_requires_new_audit(tmp_path):
    support, context = setup_gate(tmp_path)
    calls = []
    def audit(**kwargs):
        calls.append(kwargs)
        return audit_response(kwargs["user_prompt"])
    support._call_compact_retry_sync = audit
    support._validate_source_answer(context)
    context.knowledge_hits[0].source_content += "新的版本"
    support._validate_source_answer(context)
    assert len(calls) == 2
    assert calls[0]["cache_namespace"] != calls[1]["cache_namespace"]


def test_previously_repaired_answer_cannot_trigger_another_repair(tmp_path):
    support, context = setup_gate(tmp_path)
    context.answer_repair_attempted = True
    support._call_compact_retry_sync = lambda **kwargs: audit_response(kwargs["user_prompt"], False)
    def forbidden(*args, **kwargs):
        pytest.fail("second repair must not run")
    support._retry_answer_with_compact_prompt = forbidden
    with pytest.raises(SourceAuditRejected, match="未通过来源事实"):
        support._validate_source_answer(context)


def test_full_source_generation_does_not_publish_draft_tokens(tmp_path):
    from sage_faculty_twin.service import DigitalTwinService
    from sage_faculty_twin.chat_contracts import PromptEnvelope, PromptMode, EvidenceBundle
    support, context = setup_gate(tmp_path)
    context.answer = None
    actual = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path / "stream"))._build_support()
    actual._should_enable_deep_thinking = lambda context: False
    actual._answer_chunk_callback = lambda text: pytest.fail("unvalidated token published")
    calls = []
    def generate(system, user, **kwargs):
        calls.append(kwargs)
        assert kwargs.get("token_callback") is None
        return "系统仍在研究。"
    actual._call_answer_question_sync = generate
    context.prompt_envelope = PromptEnvelope(
        original_question=context.request.question, system_prompt="Use supplied source",
        user_prompt="系统仍在研究。", mode=PromptMode.FULL, evidence=EvidenceBundle(),
    )
    actual.answer_with_llm(context)
    assert calls
    assert context.source_audit_digest is None


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
