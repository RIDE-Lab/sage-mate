"""Prospective work is a semantic requested task, not a research topic."""
import json
from types import SimpleNamespace

import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.chat_contracts import ChatIntake, InteractionDecision
from sage_faculty_twin.llm_client import VllmChatClient
from sage_faculty_twin.models import ChatRequest, InteractionIntent
from sage_faculty_twin.service import ChatWorkflowContext, DigitalTwinService

FACT_QUERY = "公开 Wiki 是否报告了 Twin 在 2026 年 10 月 18 日用 9973 个真实研究问题测得的准确率？请给出测量条件；如果没有这些资料请明确指出。"


@pytest.mark.parametrize("mode,directive", [
    ("facts", None), ("research", "请设计对照实验"),
])
def test_semantic_task_contract_survives_classification(mode, directive):
    client = object.__new__(VllmChatClient)
    client._request_intent_classification = lambda system, user: json.dumps({
        "action": "answer", "domain": "research", "task_mode": mode,
        "task_directive": directive, "knowledge_sources": ["wiki"],
    })
    question = FACT_QUERY if mode == "facts" else FACT_QUERY + "另外，请设计对照实验。"
    intent = client.classify_interaction_intent_sync(question)
    assert intent.task_mode == mode
    assert intent.requests_research(question) == (mode != "facts")


def test_research_topic_fast_path_cannot_override_semantic_fact_task(tmp_path):
    service = DigitalTwinService(AppSettings(_env_file=None, knowledge_base_dir=tmp_path))
    support = service._build_support()
    calls = []
    def classify(question, context, **kwargs):
        calls.append(question)
        return InteractionIntent(domain="research", task_mode="facts", knowledge_sources=["wiki"])
    support._llm_client = SimpleNamespace(supports_fast_intent_bypass=True,
                                         classify_interaction_intent_sync=classify)
    context = ChatWorkflowContext(request=ChatRequest(student_name="synthetic", question=FACT_QUERY,
        knowledge_sources=["wiki"]), conversation_id="synthetic-task", owner_name="owner", used_model="test")
    intent, source = support._resolve_interaction_intent(context)
    assert calls == [FACT_QUERY] and source.startswith("llm")
    assert not intent.requests_research(FACT_QUERY)
    context.interaction_intent = intent
    context.intake = ChatIntake.from_request(context.request, conversation_id=context.conversation_id)
    context.interaction_decision = InteractionDecision(intent=intent, source=source)
    support.build_prompt(context)
    assert "科研评审契约" not in context.system_prompt


@pytest.mark.parametrize("payload", [
    {"task_mode": "research", "task_directive": "invented request"},
    {"task_mode": "facts", "task_directive": "研究问题"},
])
def test_invalid_directive_is_not_silently_promoted(payload):
    client = object.__new__(VllmChatClient)
    client._request_intent_classification = lambda *_: json.dumps(payload)
    with pytest.raises(ValueError):
        client.classify_interaction_intent_sync(FACT_QUERY)


def test_missing_semantic_task_cannot_be_invented_by_shape_repair():
    client = object.__new__(VllmChatClient)
    client._repair_interaction_intent_payload = lambda *_: pytest.fail("not a syntax-only repair")
    with pytest.raises(ValueError, match="answer task missing"):
        client._parse_interaction_intent_payload('{"domain":"research"}')


def test_classifier_failure_cannot_fall_back_to_topic_heuristic(tmp_path):
    service = DigitalTwinService(AppSettings(_env_file=None, knowledge_base_dir=tmp_path))
    support = service._build_support()
    def fail(*args, **kwargs):
        raise RuntimeError("synthetic classifier failure")
    support._llm_client = SimpleNamespace(supports_fast_intent_bypass=True,
                                         classify_interaction_intent_sync=fail)
    context = ChatWorkflowContext(request=ChatRequest(student_name="synthetic", question=FACT_QUERY,
        knowledge_sources=["wiki"]), conversation_id="synthetic-failure", owner_name="owner", used_model="test")
    with pytest.raises(RuntimeError, match="answer_task_classification_unresolved"):
        support._resolve_interaction_intent(context)
