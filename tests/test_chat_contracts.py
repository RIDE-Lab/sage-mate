from __future__ import annotations

from dataclasses import FrozenInstanceError

import pytest

from sage_faculty_twin.chat_contracts import (
    ChatIntake,
    EvidenceBundle,
    InteractionDecision,
    PromptEnvelope,
    PromptMode,
)
from sage_faculty_twin.models import ChatRequest, InteractionIntent


def test_intake_preserves_original_question_as_immutable_data() -> None:
    intake = ChatIntake.from_request(
        ChatRequest(student_name="Visitor", question="  Keep my exact question?  "),
        conversation_id="portable-conversation",
    )

    assert intake.original_question == "  Keep my exact question?  "
    with pytest.raises(FrozenInstanceError):
        intake.original_question = "rewritten"  # type: ignore[misc]


def test_interaction_decision_rejects_conflicting_retrieval_scopes() -> None:
    intent = InteractionIntent(
        action="answer",
        domain="general",
        retrieval_scopes=["profile"],
        exclude_scopes=["profile"],
        decision_mode="direct_answer",
        confidence=0.9,
    )

    with pytest.raises(ValueError, match="same scopes"):
        InteractionDecision(intent=intent, source="test")


def test_prompt_envelope_requires_all_cross_path_invariants() -> None:
    with pytest.raises(ValueError, match="missing required invariants"):
        PromptEnvelope(
            original_question="Question",
            system_prompt="System",
            user_prompt="Question",
            mode=PromptMode.COMPACT,
            evidence=EvidenceBundle(),
            invariants=frozenset({"preserve_original_question"}),
        )
