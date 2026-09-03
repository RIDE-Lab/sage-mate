"""User-observed completeness/evidence failures; no model or private data required."""
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from sage_faculty_twin.chat_delivery import AnswerConstraints, ChatDeliveryGate
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.evidence_policy import has_query_evidence
from sage_faculty_twin.models import ChatRequest, ChatResponse, InteractionIntent, KnowledgeSearchHit
from sage_faculty_twin.knowledge_base import LocalKnowledgeStore
from sage_faculty_twin.skill_runner import SkillRunner
from sage_faculty_twin.skill_tools import SkillToolRegistry
from sage_faculty_twin.skills import SkillContext, SkillDefinition, SkillResult, SkillToolDefinition, SkillToolParameter
from sage_faculty_twin.service import (
    ChatWorkflowContext, DigitalTwinService, FacultyTwinWorkflowSupport,
    _answer_does_not_complete_requested_task,
)


@pytest.mark.parametrize("question", [
    "请给出三项公平实验检查，每项用一句话说明。",
    "请列三条建议，每条一句话。",
    "请给出三项建议，每一项控制在30字以内。",
])
def test_item_limit_cannot_truncate_whole_answer(question):
    assert not AnswerConstraints.from_question(question).has_limits
    response = ChatResponse(
        answer="1. 固定硬件与精度。\n2. 回放相同负载。\n3. 对齐延迟约束。",
        owner_name="Owner", used_model="test-model",
    )
    service = object.__new__(DigitalTwinService)
    service._delivery_gate = ChatDeliveryGate()
    result = service._deliver_chat_response(ChatRequest(student_name="test", question=question), response)
    assert result.answer == response.answer


def test_whole_answer_limit_still_applies_separately():
    constraints = AnswerConstraints.from_question("列三项，每项一句话，总计100字以内")
    assert constraints.max_chars == 100
    assert constraints.max_sentences is None


def test_numbered_markdown_is_not_an_extra_sentence():
    response = ChatResponse(answer="1. 固定模型。\n2. 固定负载。", owner_name="Owner", used_model="test")
    assert ChatDeliveryGate().deliver(response=response, original_question="用两句话说明").answer == response.answer


def test_explicit_three_actions_rejects_incomplete_paragraph():
    question = "请给出可检验的三项行动。"
    assert _answer_does_not_complete_requested_task(question, "核心判断：需要公平对比。")
    assert not _answer_does_not_complete_requested_task(question, "1. 固定硬件。\n2. 回放负载。\n3. 比较分位延迟。")


def hit(doc_id, tags, *, excerpt="长上下文推理性能需要公平对比。", metadata=None):
    return KnowledgeSearchHit(document_id=doc_id, title=doc_id, excerpt=excerpt,
                              score=99, tags=tags, metadata=metadata or {})


def test_scope_empty_does_not_promote_recruitment_as_research_evidence():
    support = object.__new__(FacultyTwinWorkflowSupport)
    intent = InteractionIntent(action="answer", domain="research",
                               retrieval_scopes=["publications", "profile"], exclude_scopes=["courseware"])
    job = hit("job", ["recruitment", "job-opening", "audience:public"])
    assert support._filter_knowledge_hits_by_intent([job], intent, question="长上下文推理公平对比") == []
    paper = hit("paper", ["research", "publication", "pdf"])
    assert support._filter_knowledge_hits_by_intent([job, paper], intent, question="长上下文推理公平对比") == [paper]


def test_empty_exclusion_scope_does_not_exclude_everything():
    support = object.__new__(FacultyTwinWorkflowSupport)
    paper = hit("paper", ["research"])
    intent = InteractionIntent(action="answer", domain="research", retrieval_scopes=["publications"])
    assert support._filter_knowledge_hits_by_intent([paper], intent, question="长上下文") == [paper]


def test_document_purpose_applies_when_planner_leaves_scopes_empty():
    support = object.__new__(FacultyTwinWorkflowSupport)
    job = hit("公开招聘｜老师的团队", ["recruitment", "job-opening"], excerpt="研究状态复用的工程师")
    intent = InteractionIntent(action="answer", domain="general")
    assert support._filter_knowledge_hits_by_intent([job], intent, question="老师的系统研究涉及状态复用吗") == []
    assert support._filter_knowledge_hits_by_intent([job], intent, question="团队招聘什么工程师") == [job]


def test_metadata_overlap_does_not_establish_factual_relevance():
    document = hit("unrelated", ["public"], excerpt="会议预约政策", metadata={"note": "长上下文推理公平对比"})
    assert not has_query_evidence("长上下文推理公平对比", document)


def test_excerpt_selects_supporting_passage_not_first_title_match():
    store = object.__new__(LocalKnowledgeStore)
    content = "推理系统综述。作者：某某。" + "其它背景材料。" * 100 + "公平baseline需对齐模型与输入长度。关键消融应逐项关闭优化，记录吞吐和尾延迟。" + "其它背景材料。" * 100
    excerpt = store._build_excerpt(content, {"推理", "公平", "baseline", "关键消融", "输入长度"})
    assert "公平baseline需对齐" in excerpt
    assert "关键消融应逐项" in excerpt
    assert "作者：某某" not in excerpt


def test_constrained_fact_query_requires_model_formatting(tmp_path):
    service = DigitalTwinService(AppSettings(_env_file=None, knowledge_base_dir=tmp_path))
    assert service._build_lightweight_fact_response(ChatRequest(
        student_name="test", question="用两句话介绍张书豪老师研究方向", deep_thinking=False
    )) is None


def test_compact_path_never_drops_selected_evidence(tmp_path):
    support = object.__new__(FacultyTwinWorkflowSupport)
    support._settings = AppSettings(_env_file=None, knowledge_base_dir=tmp_path)
    context = ChatWorkflowContext(
        request=ChatRequest(student_name="test", question="请用两句话介绍张书豪老师的研究方向", deep_thinking=False),
        conversation_id="test", owner_name="Owner", used_model="test",
        interaction_intent=InteractionIntent(action="answer", domain="general", decision_mode="direct_answer"),
        knowledge_hits=[hit("owner", ["profile", "research"])],
    )
    assert not support._should_use_compact_general_answer(context)


def test_caveat_does_not_delete_valid_support():
    support = object.__new__(FacultyTwinWorkflowSupport)
    support._build_availability_basis_item = lambda _: None
    context = SimpleNamespace(
        answer="已知结论来自这篇论文。对其它模型的资料不足，需要进一步实验。",
        added_knowledge_record=None, knowledge_hits=[hit("paper", ["publication"])],
        web_search_hits=[], memory_hits=[],
    )
    assert len(support._build_answer_basis(context)) == 1


def test_structured_fast_answer_has_complete_budget_and_failed_repair_has_no_fake_sources(tmp_path):
    support = object.__new__(FacultyTwinWorkflowSupport)
    support._settings = AppSettings(_env_file=None, knowledge_base_dir=tmp_path)
    calls = []

    def fail_answer(system_prompt, user_prompt, **kwargs):
        calls.append(user_prompt)
        return "。"

    support._llm_client = SimpleNamespace(answer_question_sync=fail_answer)
    context = ChatWorkflowContext(
        request=ChatRequest(student_name="test", question="列三项可执行检查", deep_thinking=False),
        conversation_id="test", owner_name="Owner", used_model="test",
        interaction_intent=InteractionIntent(action="answer", domain="research"),
        knowledge_hits=[hit("paper", ["research"])],
    )
    assert support._build_llm_serving_policy_context(context)["max_tokens"] == 1024
    answer = support._retry_answer_with_compact_prompt(context)
    assert "未能生成通过完整性与引用校验" in answer
    assert len(calls) == 1
    assert "长上下文推理" in calls[0]  # repair preserved its original evidence
    assert context.knowledge_hits == []
    assert context.decision_mode == "answer_quality_unavailable"


@pytest.mark.parametrize("native", [False, True])
def test_skill_tool_provenance_survives_both_transports(native):
    document = hit("paper", ["research"], metadata={"visibility": "public", "private_note": "not a citation"})
    store = SimpleNamespace(search=lambda **_: [document])
    llm = MagicMock()
    llm.supports_native_tool_calling = native
    llm.answer_question_sync.return_value = "推理实验需要控制变量。"
    llm.chat_with_tools_sync.side_effect = [
        {"tool_calls": [{"id": "1", "name": "search", "arguments": {"query": "推理"}}]},
        {"tool_calls": [], "content": "推理实验需要控制变量。", "finish_reason": "stop"},
    ]
    skill = SkillDefinition(
        skill_id="test", name="test", system_prompt="Answer from sources.",
        user_prompt_template="{question}", enabled=True,
        tools=[SkillToolDefinition(tool_id="kb", name="search", description="Search",
            handler="knowledge_search", parameters={"query": SkillToolParameter(type="string", description="query")})],
    )
    result = SkillRunner(llm, SkillToolRegistry(knowledge_store=store)).run(
        skill, SkillContext(question="推理实验", visitor_profile="general_visitor")
    )
    assert result.success
    assert [h.document_id for h in result.knowledge_hits] == ["paper"]
    assert result.knowledge_hits[0].metadata == {"visibility": "public"}


@pytest.mark.asyncio
async def test_skill_answer_uses_same_support_builder_as_workflow(tmp_path, monkeypatch):
    service = DigitalTwinService(AppSettings(_env_file=None, knowledge_base_dir=tmp_path))
    monkeypatch.setattr(service, "_build_lightweight_chat_response", lambda _: None)
    monkeypatch.setattr(service, "_build_lightweight_fact_response", lambda _: None)
    monkeypatch.setattr(service, "_is_light_request", lambda _: False)
    skill = SimpleNamespace(skill_id="profile")
    service._skill_router = SimpleNamespace(match=lambda _: skill)
    service._skill_runner = SimpleNamespace(run=lambda *_: SkillResult(
        skill_id="profile", answer="研究方向是推理系统。", knowledge_hits=[
            hit("研究主页", ["profile", "research"], excerpt="老师的研究方向是推理系统。")
        ]))
    response = await service.answer_in_process(ChatRequest(student_name="test", question="研究方向是什么？", deep_thinking=False))
    assert response.workflow_action == "skill_answer"
    assert response.knowledge_hits
    assert response.answer_basis[0].title == "研究主页"
