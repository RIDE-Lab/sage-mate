"""Application handler for SAGE post-answer tasks, not an execution runtime."""

from __future__ import annotations

from collections.abc import Callable
from time import perf_counter

from .light_agent import LightweightActionPlanner
from .memory_store import ConversationMemoryRecord
from .models import WorkflowTraceStep
from .post_answer_contract import DigestWorkTask, PostAnswerTask


def memory_usefulness_label(signal):
    return {"helpful": "有帮助", "stale": "可能过时", "low_confidence": "低置信度",
            "review_worthy": "建议复核"}.get(signal, signal)


class PostAnswerHandler:
    """Restricted memory writers, a digest projection and a pure scorer.

    There is deliberately no generic model, booking, knowledge-write or notification
    port. Results are returned to SAGE for atomic publication with task finish.
    Handler failures propagate so the journal keeps the task recoverable.
    """

    def __init__(self, *, consolidate_profiles: Callable[[ConversationMemoryRecord], int],
                 score: Callable[[PostAnswerTask], tuple[str, str]], create_artifact_draft=None,
                 refresh_digest=None):
        self._consolidate_profiles = consolidate_profiles
        self._score = score
        self._create_artifact_draft = create_artifact_draft
        self._refresh_digest = refresh_digest
        self._planner = LightweightActionPlanner()

    def __call__(self, lease):
        if lease.task_id == "context_digest":
            task = DigestWorkTask.model_validate(lease.payload)
            if lease.request_id != task.request_id:
                raise ValueError("digest_lease_identity_conflict")
            return self._digest(task.conversation_id, task.intent, task.owner_id)
        task = PostAnswerTask.model_validate(lease.payload)
        if lease.task_id != "post_answer" or lease.request_id != task.request_id:
            raise ValueError("post_answer_lease_identity_conflict")
        traces = []
        started = perf_counter()

        def trace(key, title, summary, detail, *, skipped=False):
            nonlocal started
            traces.append(WorkflowTraceStep(
                key=key, title=title, summary=summary, detail=detail,
                status="skipped" if skipped else "completed",
                duration_ms=max(0, round((perf_counter() - started) * 1000)),
            ))
            started = perf_counter()

        artifact_draft_id = None
        if task.artifact_draft is not None:
            if self._create_artifact_draft is None:
                raise RuntimeError("artifact_draft_destination_unavailable")
            draft = self._create_artifact_draft(
                **task.artifact_draft.model_dump(),
                conversation_id=task.record.conversation_id, source_memory_id=task.record.memory_id,
                student_name=task.record.student_name, student_email=task.record.student_email,
                interaction_domain=task.record.interaction_domain,
                question=task.record.question, answer=task.record.answer,
            )
            artifact_draft_id = draft.draft_id
            trace("artifact_memory_writeback", "记录材料记忆草稿",
                  f"已记录 1 条材料记忆草稿，涉及 {len(task.artifact_draft.artifact_names)} 份材料。",
                  task.artifact_draft.provenance_note)

        if task.skip_profile or task.record is None:
            profile_count = 0
            trace("memory_profile_consolidate", "沉淀长期画像记忆", "本轮跳过长期画像写入。",
                  "按已提交的工作流决策跳过；不重新判断或触发其他主流程动作。", skipped=True)
        else:
            profile_count = self._consolidate_profiles(task.record)
            trace("memory_profile_consolidate", "沉淀长期画像记忆", f"已处理 {profile_count} 条画像记录。",
                  "画像通过版本化原子存储提交，重放不会覆盖更新版本。")

        actions = []
        if not task.skip_follow_up:
            actions = self._planner.plan_chat_actions(
                workflow_action=task.response.workflow_action, question=task.request.question,
                interaction_intent=task.interaction_intent,
                knowledge_hits=task.response.knowledge_hits,
                student_profiles=task.profile_snapshot, availability_schedule=task.availability,
            )
        trace("follow_up_plan", "规划后续动作", f"已生成 {len(actions)} 条后续建议。",
              "建议仅使用本轮保存的证据、画像和日程快照，不执行预约或发送消息。",
              skipped=task.skip_follow_up)

        signal = reason = None
        if task.evaluate_memory:
            signal, reason = self._score(task)
        skip_summary, skip_detail = (
            ("系统日期回答无需评估记忆证据。", "本轮答案来自服务配置时区的系统日期，不依赖对话记忆或检索材料。")
            if task.score_skip_reason == "current_date"
            else ("当前工作流规划跳过记忆证据评估。", "遵循已保存的评估决策。")
        )
        trace("memory_usefulness_score", "评估记忆证据有效性",
              f"已完成本轮记忆证据评估：{memory_usefulness_label(signal)}。" if task.evaluate_memory else skip_summary,
              reason or skip_detail, skipped=not task.evaluate_memory)

        digest_result = None
        if task.digest is not None:
            digest_result = self._digest(task.record.conversation_id, task.digest, task.record.owner_id)
            changed = digest_result.get("turns_incorporated", 0)
            trace("context_digest", "整理会话上下文",
                  f"已纳入 {changed} 轮来源。" if changed else "暂无需要纳入的新来源。",
                  "来源进度与结果已原子提交；无损保留不等于语义压缩。", skipped=not changed)

        keys = {item.key for item in traces}
        response = task.response.model_copy(deep=True, update={
            "follow_up_actions": actions,
            "workflow_trace": [item for item in task.response.workflow_trace if item.key not in keys] + traces,
        })
        return {"response": response.model_dump(mode="json"),
                "artifact_draft_id": artifact_draft_id,
                "profile_count": profile_count,
                "digest": digest_result,
                "memory_usefulness": {"signal": signal, "reason": reason}}

    def _digest(self, conversation_id, intent, owner_id):
        if self._refresh_digest is None:
            raise RuntimeError("digest_destination_unavailable")
        result = self._refresh_digest(conversation_id, intent, owner_id=owner_id)
        if not result.get("ok"):
            # Failure is not task completion. Persisted intent remains retryable.
            raise RuntimeError("digest_refresh_incomplete")
        return result
