from unittest.mock import Mock

import pytest

from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.llm_client import VllmChatClient, _InteractionIntentPayload
from sage_faculty_twin.models import ChatRequest, InteractionIntent
from sage_faculty_twin.service import ChatWorkflowContext, DigitalTwinService


def context_for(intent, **request_fields):
    return ChatWorkflowContext(
        request=ChatRequest(student_name="guest", question="请只回复收到。", **request_fields),
        conversation_id="self-contained-test", owner_name="Owner", used_model="test",
        interaction_intent=intent,
    )


@pytest.mark.parametrize("background", [None, "初次来访", "计算机课程"])
def test_semantic_self_contained_skips_both_stores_and_basis(tmp_path, background):
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))
    support = service._build_support()
    support._knowledge_retriever = Mock()
    support._conversation_store = Mock()
    context = context_for(InteractionIntent(self_contained=True), course_context=background)
    support.retrieve_knowledge(context)
    support.retrieve_memory(context)
    support._knowledge_retriever.search.assert_not_called()
    support._conversation_store.search.assert_not_called()
    assert not context.knowledge_hits
    assert not context.memory_hits
    assert not support._build_answer_basis(context)
    assert all(step.status == "skipped" for step in context.workflow_trace)
    context.answer = "收到"
    assert support.render_chat_response(context).external_evidence_required is False


@pytest.mark.parametrize("intent,fields", [
    (InteractionIntent(), {}),
    (InteractionIntent(self_contained=True, domain="research"), {}),
    (InteractionIntent(self_contained=True, retrieval_scopes=["profile"]), {}),
    (InteractionIntent(self_contained=True, needs_clarification=True), {}),
    (InteractionIntent(self_contained=True), {"web_search": True}),
])
def test_unknown_or_grounded_requests_do_not_skip(tmp_path, intent, fields):
    support = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))._build_support()
    assert not support._is_self_contained_request(context_for(intent, **fields))


def test_classifier_preserves_explicit_decision_and_defaults_to_retrieval():
    client = object.__new__(VllmChatClient)
    assert client._coerce_interaction_intent(_InteractionIntentPayload(self_contained=True)).self_contained
    assert not client._coerce_interaction_intent(_InteractionIntentPayload()).self_contained


def test_broad_retrieval_scope_is_not_proof_of_booking_evidence(tmp_path):
    support = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))._build_support()
    context = context_for(InteractionIntent(retrieval_scopes=["meeting_policy"]))
    context.request.question = "介绍 Wiki 内容更新的完整流程"
    assert support._build_availability_basis_item(context) is None
    context.workflow_action = "book_meeting"
    assert support._build_availability_basis_item(context) is not None
    context.workflow_action = "answer"
    context.request.question = "如何预约老师？"
    assert support._build_availability_basis_item(context) is not None
