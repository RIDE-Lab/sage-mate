"""Application handler for SAGE post-answer tasks, not an execution runtime."""

from __future__ import annotations

from collections.abc import Callable

from .light_agent import LightweightActionPlanner
from .memory_store import ConversationMemoryRecord
from .models import WorkflowTraceStep
from .post_answer_contract import PostAnswerTask


class PostAnswerHandler:
    """Only the idempotent profile writer and pure scorer are injected.

    There is deliberately no model, booking, knowledge-write or notification
    port. Results are returned to SAGE for atomic publication with task finish.
    Handler failures propagate so the journal keeps the task recoverable.
    """

    def __init__(self, *, consolidate_profiles: Callable[[ConversationMemoryRecord], int],
                 score: Callable[[PostAnswerTask], tuple[str, str]]):
        self._consolidate_profiles = consolidate_profiles
        self._score = score
        self._planner = LightweightActionPlanner()

    def __call__(self, lease):
        task = PostAnswerTask.model_validate(lease.payload)
        if lease.task_id != "post_answer" or lease.request_id != task.request_id:
            raise ValueError("post_answer_lease_identity_conflict")
        traces = []

        def trace(key, title, summary, detail, *, skipped=False):
            traces.append(WorkflowTraceStep(
                key=key, title=title, summary=summary, detail=detail,
                status="skipped" if skipped else "completed", parallel_group="post_answer",
            ))

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
        trace("memory_usefulness_score", "评估记忆证据有效性",
              "已完成本轮记忆证据评估。" if task.evaluate_memory else "本轮跳过记忆证据评估。",
              reason or "遵循已保存的评估决策。", skipped=not task.evaluate_memory)

        keys = {item.key for item in traces}
        response = task.response.model_copy(deep=True, update={
            "follow_up_actions": actions,
            "workflow_trace": [item for item in task.response.workflow_trace if item.key not in keys] + traces,
        })
        return {"response": response.model_dump(mode="json"),
                "profile_count": profile_count,
                "memory_usefulness": {"signal": signal, "reason": reason}}
