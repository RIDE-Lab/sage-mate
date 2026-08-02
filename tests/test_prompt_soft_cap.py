"""Regression tests for the Chat Latency Optimizations Task 3 prompt soft cap.

When the assembled chat prompt (system + user) exceeds
``DIGITAL_TWIN_PROMPT_SOFT_CAP`` chars (default 24000), the prompt builder
truncates inputs in this order until back under the cap:
  (a) drop oldest memory hits beyond the top 3,
  (b) cap each knowledge hit excerpt at 1200 chars,
  (c) cap each attachment ``text_content`` at 4000 chars.

A ``prompt_truncated`` boolean is set on the ``ChatWorkflowContext`` and
surfaced via the ``prompt_build`` trace step so the UI can show a
"提示词已截断" badge when the cap kicks in.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

from sage_faculty_twin.chat_contracts import ChatIntake, InteractionDecision
from sage_faculty_twin.config import AppSettings
from sage_faculty_twin.memory_store import ConversationMemoryHit
from sage_faculty_twin.models import (
    ChatAttachment,
    ChatRequest,
    InteractionIntent,
    KnowledgeSearchHit,
)
from sage_faculty_twin.service import (
    _ATTACHMENT_BODY_CAP,
    _KNOWLEDGE_HIT_BODY_CAP,
    _PROMPT_MEMORY_HIT_KEEP,
    _PROMPT_SOFT_CAP,
    ChatWorkflowContext,
    DigitalTwinService,
)


def _make_context(
    request: ChatRequest,
    *,
    settings: AppSettings,
    memory_hits: list[ConversationMemoryHit] | None = None,
    knowledge_hits: list[KnowledgeSearchHit] | None = None,
) -> ChatWorkflowContext:
    conversation_id = request.conversation_id or "conv-prompt-cap"
    return ChatWorkflowContext(
        request=request,
        conversation_id=conversation_id,
        owner_name=settings.owner_name,
        used_model=settings.model_name,
        intake=ChatIntake.from_request(request, conversation_id=conversation_id),
        interaction_decision=InteractionDecision(
            intent=InteractionIntent(
                action="answer",
                domain="general",
                decision_mode="direct_answer",
                confidence=1.0,
            ),
            source="test",
        ),
        memory_hits=memory_hits or [],
        knowledge_hits=knowledge_hits or [],
    )


def test_prompt_under_cap_does_not_truncate(tmp_path: Path) -> None:
    """A small prompt with no oversized inputs leaves the truncation flag
    cleared and emits the normal trace detail."""

    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    support = service._build_support()

    request = ChatRequest(
        student_name="Alice",
        student_email="alice@example.com",
        course_context="科研指导",
        conversation_id="conv-prompt-cap-small",
        question="给我一些下一步建议。",
    )
    context = _make_context(request, settings=settings)

    support.build_prompt(context)

    assert context.prompt_truncated is False
    assert context.user_prompt is not None
    assert context.system_prompt is not None
    total = len(context.system_prompt) + len(context.user_prompt)
    assert total <= _PROMPT_SOFT_CAP

    prompt_step = next(step for step in context.workflow_trace if step.key == "prompt_build")
    assert "字符" in prompt_step.detail
    assert "截断" not in prompt_step.detail


def test_prompt_truncates_oversized_attachments(tmp_path: Path) -> None:
    """Three 10k-char attachments push the prompt past the 24k soft cap.
    The truncation chain caps each attachment body at 4000 chars and the
    final prompt fits under the cap."""

    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    support = service._build_support()

    fat_body = "A" * 10000  # below the per-attachment ``max_length=12000``
    attachments = [
        ChatAttachment(
            file_name=f"paper-{i}.txt",
            media_type="text/plain",
            text_content=fat_body,
            size_bytes=len(fat_body),
        )
        for i in range(3)
    ]
    request = ChatRequest(
        student_name="Alice",
        student_email="alice@example.com",
        course_context="论文修改",
        conversation_id="conv-prompt-cap-attach",
        question="请帮我看一下这三份附件的论文有什么共同问题？",
        attachments=attachments,
    )
    context = _make_context(request, settings=settings)

    support.build_prompt(context)

    assert context.prompt_truncated is True, (
        "Three 10k-char attachments should trigger the soft-cap truncation "
        "chain (combined > 24000 chars)."
    )
    assert context.user_prompt is not None
    assert context.system_prompt is not None
    total = len(context.system_prompt) + len(context.user_prompt)
    assert total <= _PROMPT_SOFT_CAP, (
        f"After truncation the prompt must fit under the soft cap, got {total} > {_PROMPT_SOFT_CAP}"
    )

    # Each attachment body in the rendered prompt is capped at the
    # configured budget (+1 for the ellipsis marker).
    assert fat_body not in context.user_prompt
    # Truncation marker may not appear verbatim because of formatting; just
    # assert the original 10k run is gone.
    assert "A" * (_ATTACHMENT_BODY_CAP + 100) not in context.user_prompt

    # Trace step surfaces the truncation reason.
    prompt_step = next(step for step in context.workflow_trace if step.key == "prompt_build")
    assert "截断" in prompt_step.detail or "truncate" in prompt_step.detail.lower()
    assert f"{_ATTACHMENT_BODY_CAP}" in prompt_step.detail
    assert prompt_step.summary == "已组装回答上下文（已截断）。"


def test_prompt_drops_oldest_memory_hits_first(tmp_path: Path) -> None:
    """When memory hits push the prompt over the cap, only the top
    ``_PROMPT_MEMORY_HIT_KEEP`` (oldest dropped first) survive into the
    rendered prompt."""

    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    support = service._build_support()

    fat_summary = "记忆条目正文" + ("X" * 4000)
    memory_hits = [
        ConversationMemoryHit(
            memory_id=f"mem-{i}",
            conversation_id="conv-prompt-cap-mem",
            summary=f"M{i}-{fat_summary}",
            score=0.9 - 0.05 * i,
            created_at=datetime.now(timezone.utc),
        )
        for i in range(8)
    ]
    request = ChatRequest(
        student_name="Alice",
        student_email="alice@example.com",
        course_context="科研指导",
        conversation_id="conv-prompt-cap-mem",
        question="基于过往记忆给我建议。",
    )
    context = _make_context(request, settings=settings, memory_hits=memory_hits)

    support.build_prompt(context)

    assert context.prompt_truncated is True
    assert context.user_prompt is not None

    # Only the first ``_PROMPT_MEMORY_HIT_KEEP`` summaries survive.
    surviving = [i for i in range(8) if f"M{i}-" in (context.user_prompt or "")]
    assert surviving == list(range(_PROMPT_MEMORY_HIT_KEEP)), surviving

    # Trace step mentions the memory truncation step.
    prompt_step = next(step for step in context.workflow_trace if step.key == "prompt_build")
    assert "memory" in prompt_step.detail


def test_prompt_caps_oversized_knowledge_excerpts(tmp_path: Path) -> None:
    """A handful of huge knowledge excerpts trigger the per-excerpt cap."""

    settings = AppSettings(knowledge_base_dir=tmp_path)
    service = DigitalTwinService(settings)
    support = service._build_support()

    fat_excerpt = "知识条目正文 " + ("Z" * 6000)
    knowledge_hits = [
        KnowledgeSearchHit(
            document_id=f"doc-{i}",
            title=f"老师笔记 {i}",
            excerpt=fat_excerpt,
            score=0.9 - 0.01 * i,
            tags=["科研"],
            source_name=f"notes-{i}",
        )
        for i in range(5)
    ]
    request = ChatRequest(
        student_name="Alice",
        student_email="alice@example.com",
        course_context="科研指导",
        conversation_id="conv-prompt-cap-know",
        question="请基于知识库回答。",
    )
    context = _make_context(request, settings=settings, knowledge_hits=knowledge_hits)

    support.build_prompt(context)

    assert context.prompt_truncated is True
    assert context.user_prompt is not None
    total = len(context.system_prompt or "") + len(context.user_prompt)
    assert total <= _PROMPT_SOFT_CAP

    # No single original 6k Z-run survives; the cap is well below 6000.
    assert "Z" * (_KNOWLEDGE_HIT_BODY_CAP + 100) not in context.user_prompt

    prompt_step = next(step for step in context.workflow_trace if step.key == "prompt_build")
    assert "knowledge" in prompt_step.detail
