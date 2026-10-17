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
    memory_result = support.retrieve_memory(support.prepare_memory_retrieval(context))
    assert memory_result.status == "skipped"
    assert memory_result.hits == ()
    support._knowledge_retriever.search.assert_not_called()
    support._conversation_store.search.assert_not_called()
    assert not context.knowledge_hits
    assert not context.memory_hits
    assert not support._build_answer_basis(context)
    assert all(step.status == "skipped" for step in context.workflow_trace)
    context.answer = "收到"
    assert support.render_chat_response(context).external_evidence_required is False


def test_memory_branch_returns_owned_result_without_mutating_context(tmp_path):
    service = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))
    support = service._build_support()
    support._conversation_store = Mock()
    hits = [Mock(memory_type="short_term")]
    support._conversation_store.search.return_value = hits
    support._conversation_store.search_artifacts.return_value = []
    support._planner_requests_any_step = lambda *args: True
    support._trace_callback = Mock()
    context = context_for(InteractionIntent(retrieval_scopes=["profile"]))
    inputs = support.prepare_memory_retrieval(context)
    original_question = context.request.question
    context.request.question = "changed after planning"
    result = support.retrieve_memory(inputs)
    assert support._conversation_store.search.call_args.args[0].question == original_question
    assert result.hits == tuple(hits)
    assert result.status == "completed"
    assert context.memory_hits == []
    assert context.workflow_trace == []
    support._trace_callback.assert_not_called()
    assert len(hits) == 1


def test_memory_stage_applies_result_and_trace_once(tmp_path):
    from sage_faculty_twin.service import MemoryRetrievalStage

    support = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))._build_support()
    support._trace_callback = Mock()
    context = context_for(InteractionIntent(self_contained=True))
    assert MemoryRetrievalStage(support).execute(context) is context
    assert [step.key for step in context.workflow_trace] == ["memory_retrieve"]
    support._trace_callback.assert_called_once()


def test_read_only_knowledge_branch_never_executes_admin_write(tmp_path):
    support = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))._build_support()
    support._knowledge_store = Mock()
    context = context_for(InteractionIntent(action="admin_add_knowledge"))
    context.is_admin_request = True
    context.workflow_action = "admin_add_knowledge"
    support.retrieve_knowledge(context)
    support._knowledge_store.add_document.assert_not_called()
    assert context.added_knowledge_record is None


def test_knowledge_write_stage_requires_authenticated_admin(tmp_path):
    from sage_faculty_twin.service import KnowledgeWriteStage

    support = DigitalTwinService(AppSettings(knowledge_base_dir=tmp_path))._build_support()
    support._knowledge_store = Mock()
    context = context_for(InteractionIntent(action="admin_add_knowledge"))
    context.workflow_action = "admin_add_knowledge"
    assert KnowledgeWriteStage(support).execute(context) is context
    support._knowledge_store.add_document.assert_not_called()


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
