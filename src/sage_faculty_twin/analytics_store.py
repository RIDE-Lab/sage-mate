from __future__ import annotations

import json
import hashlib
import math
import re
from collections import Counter
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from .config import AppSettings
from .memory_store import ConversationMemoryRecord, NeuroMemConversationStore


@dataclass(slots=True)
class ConversationFeedbackRecord:
    exchange_id: str
    rating: str
    resolved: bool
    needs_human_followup: bool
    issue_summary: str | None
    created_at: datetime
    updated_at: datetime

    def to_dict(self) -> dict[str, object]:
        return {
            "exchange_id": self.exchange_id,
            "rating": self.rating,
            "resolved": self.resolved,
            "needs_human_followup": self.needs_human_followup,
            "issue_summary": self.issue_summary,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, payload: dict[str, object]) -> ConversationFeedbackRecord:
        created_at_raw = (
            payload.get("created_at") or payload.get("updated_at") or datetime.now(UTC).isoformat()
        )
        updated_at_raw = payload.get("updated_at") or created_at_raw
        return cls(
            exchange_id=str(payload["exchange_id"]),
            rating=str(payload["rating"]),
            resolved=bool(payload.get("resolved", False)),
            needs_human_followup=bool(payload.get("needs_human_followup", False)),
            issue_summary=(str(payload["issue_summary"]) if payload.get("issue_summary") else None),
            created_at=datetime.fromisoformat(str(created_at_raw)),
            updated_at=datetime.fromisoformat(str(updated_at_raw)),
        )


@dataclass(slots=True)
class ExchangeAnalyticsSample:
    record: ConversationMemoryRecord
    feedback: ConversationFeedbackRecord | None
    token_set: frozenset[str]


@dataclass(slots=True)
class ExchangeCluster:
    cluster_id: str
    label: str
    interaction_domain: str
    token_set: set[str]
    samples: list[ExchangeAnalyticsSample]


_STOP_TOKENS = {
    "老师",
    "可以",
    "一下",
    "一个",
    "这个",
    "那个",
    "什么",
    "怎么",
    "请问",
    "帮我",
    "请帮",
    "一下",
    "一下子",
    "是否",
    "如何",
    "需要",
    "应该",
    "student",
    "question",
}

_DOMAIN_LABELS = {
    "general": "通用问答",
    "research": "科研问题",
    "teaching": "教学问题",
    "advising": "指导建议",
    "booking": "预约事项",
}


class ConversationAnalyticsStore:
    def __init__(
        self, settings: AppSettings, conversation_store: NeuroMemConversationStore
    ) -> None:
        self._settings = settings
        self._conversation_store = conversation_store
        self._base_dir = settings.conversation_memory_dir
        self._feedback_dir = self._base_dir / "feedback"
        self._feedback_dir.mkdir(parents=True, exist_ok=True)
        self._state = conversation_store.state_store
        self._slot = ("twin-feedback-v1", "feedback", "records")
        self._load_feedback_from_disk()

    @staticmethod
    def _collection(value):
        if value is None:
            return {"version": 1, "records": {}, "imports": {}}
        if value.get("version") != 1:
            raise ValueError("unsupported_feedback_state")
        return value

    def submit_feedback(
        self,
        *,
        exchange_id: str,
        rating: str,
        resolved: bool | None,
        needs_human_followup: bool,
        issue_summary: str | None,
        operation_id: str | None = None,
        commit_work=None,
    ) -> ConversationFeedbackRecord:
        """Commit feedback, optionally joining SAGE work on this same state store.

        An operation receipt preserves its original response across lost
        acknowledgements and later feedback edits. commit_work must atomically
        apply the supplied pure mutations with its task on this state store.
        No external work may run inside the mutation.
        """
        if self._conversation_store.get_canonical_record(exchange_id) is None:
            raise KeyError(exchange_id)

        operation_id = uuid4().hex if operation_id is None else operation_id
        if (not isinstance(operation_id, str) or not operation_id
                or operation_id != operation_id.strip()):
            raise ValueError("invalid_feedback_operation_id")

        now = datetime.now(UTC)
        feedback = ConversationFeedbackRecord(
            exchange_id=exchange_id,
            rating=rating,
            resolved=(rating == "up") if resolved is None else resolved,
            needs_human_followup=needs_human_followup,
            issue_summary=(issue_summary.strip() if issue_summary else None),
            created_at=now,
            updated_at=now,
        )
        identity = feedback.to_dict()
        del identity["created_at"], identity["updated_at"]
        fingerprint = hashlib.sha256(json.dumps(
            identity, sort_keys=True, ensure_ascii=False).encode()).hexdigest()

        def apply(value):
            state = self._collection(value)
            receipts = state.setdefault("receipts", {})
            receipt = receipts.get(operation_id)
            if receipt is not None:
                if receipt["fingerprint"] != fingerprint:
                    raise ValueError("feedback_operation_conflict")
                return state
            payload = feedback.to_dict()
            existing = state["records"].get(exchange_id)
            if existing is not None:
                payload["created_at"] = existing["created_at"]
            state["records"][exchange_id] = payload
            receipts[operation_id] = {"fingerprint": fingerprint, "feedback": payload}
            return state

        if commit_work is None:
            state = self._state.update(self._slot, apply)
        else:
            commit_work({self._slot: apply})
            state = self._collection(self._state.peek(self._slot))
        receipt = state.get("receipts", {}).get(operation_id)
        if receipt is None or receipt["fingerprint"] != fingerprint:
            raise RuntimeError("feedback_admission_not_committed")
        return ConversationFeedbackRecord.from_dict(receipt["feedback"])

    def get_feedback(self, exchange_id: str) -> ConversationFeedbackRecord | None:
        payload = self._collection(self._state.peek(self._slot))["records"].get(exchange_id)
        return ConversationFeedbackRecord.from_dict(payload) if payload is not None else None

    def count_feedback(self) -> int:
        return len(self._collection(self._state.peek(self._slot))["records"])

    def build_weekly_report(
        self, *, days: int = 7, cluster_limit: int = 6, unresolved_limit: int = 10
    ) -> dict[str, object]:
        window_end = datetime.now(UTC)
        window_start = window_end - timedelta(days=days)
        samples = self._build_samples(window_start)
        clusters = self._build_clusters(samples)
        top_clusters = sorted(clusters, key=self._cluster_sort_key, reverse=True)[:cluster_limit]

        positive_count = sum(
            1 for sample in samples if sample.feedback and sample.feedback.rating == "up"
        )
        negative_count = sum(
            1 for sample in samples if sample.feedback and sample.feedback.rating == "down"
        )
        unresolved_samples = [sample for sample in samples if self._is_unresolved(sample)]
        handoff_samples = [
            sample for sample in samples if sample.feedback and sample.feedback.needs_human_followup
        ]

        return {
            "window_days": days,
            "window_start": window_start,
            "window_end": window_end,
            "overview": {
                "total_exchanges": len(samples),
                "feedback_count": sum(1 for sample in samples if sample.feedback is not None),
                "positive_feedback_count": positive_count,
                "negative_feedback_count": negative_count,
                "unresolved_count": len(unresolved_samples),
                "human_handoff_count": len(handoff_samples),
            },
            "top_clusters": [self._serialize_cluster(cluster) for cluster in top_clusters],
            "knowledge_gap_suggestions": [
                self._build_knowledge_gap_suggestion(cluster)
                for cluster in top_clusters
                if self._cluster_gap_score(cluster) > 0
            ],
            "unresolved_questions": [
                self._serialize_unresolved(sample)
                for sample in unresolved_samples[:unresolved_limit]
            ],
            "handoff_categories": self._build_handoff_categories(handoff_samples),
        }

    def build_satisfaction_report(self, *, days: int = 7) -> dict[str, object]:
        window_end = datetime.now(UTC)
        window_start = window_end - timedelta(days=days)
        samples = self._build_samples(window_start)
        feedback_samples = [sample for sample in samples if sample.feedback is not None]
        feedback_count = len(feedback_samples)
        positive_count = sum(
            1 for sample in feedback_samples if sample.feedback and sample.feedback.rating == "up"
        )
        negative_count = sum(
            1 for sample in feedback_samples if sample.feedback and sample.feedback.rating == "down"
        )
        unresolved_count = sum(1 for sample in feedback_samples if self._is_unresolved(sample))
        handoff_count = sum(
            1
            for sample in feedback_samples
            if sample.feedback and sample.feedback.needs_human_followup
        )
        return {
            "feedback_count": feedback_count,
            "positive_count": positive_count,
            "negative_count": negative_count,
            "positive_rate": self._safe_ratio(positive_count, feedback_count),
            "unresolved_rate": self._safe_ratio(unresolved_count, feedback_count),
            "human_handoff_rate": self._safe_ratio(handoff_count, feedback_count),
            "feedback_coverage_rate": self._safe_ratio(feedback_count, len(samples)),
            "reason_summaries": self._build_satisfaction_reason_summaries(feedback_samples),
            "trend": self._build_satisfaction_trend(feedback_samples),
        }

    def build_gap_draft_payload(self, *, cluster_id: str, days: int = 7) -> dict[str, object]:
        report = self.build_weekly_report(days=days, cluster_limit=12)
        gap = next(
            (
                item
                for item in report["knowledge_gap_suggestions"]
                if str(item["cluster_id"]) == cluster_id
            ),
            None,
        )
        if gap is None:
            raise KeyError(cluster_id)

        interaction_domain = str(gap["interaction_domain"])
        label = str(gap["label"])
        sample_questions = [str(item) for item in gap.get("sample_questions", [])]
        tags = ["analytics-gap", "draft", "faq-draft", interaction_domain]
        title = f"FAQ草稿｜{label}"
        content = self._build_gap_draft_content(
            label=label,
            interaction_domain=interaction_domain,
            reason=str(gap["reason"]),
            suggested_action=str(gap["suggested_action"]),
            sample_questions=sample_questions,
        )
        return {
            "cluster_id": cluster_id,
            "interaction_domain": interaction_domain,
            "label": label,
            "reason": str(gap["reason"]),
            "suggested_action": str(gap["suggested_action"]),
            "sample_questions": sample_questions,
            "title": title,
            "content": content,
            "tags": tags,
            "source_name": f"analytics-gap:{cluster_id}",
        }

    def _load_feedback_from_disk(self) -> None:
        sources = [(path, path.read_text(encoding="utf-8"))
                   for path in sorted(self._feedback_dir.glob("*.json"))]
        if not sources:
            return
        records = [(path, raw, ConversationFeedbackRecord.from_dict(json.loads(raw)).to_dict())
                   for path, raw in sources]

        def migrate(value):
            state = self._collection(value)
            for path, raw, payload in records:
                fingerprint = hashlib.sha256(raw.encode()).hexdigest()
                imported = state["imports"].get(path.name)
                if imported is not None:
                    if imported != fingerprint:
                        raise ValueError("feedback_legacy_source_changed")
                    continue
                if payload["exchange_id"] in state["records"]:
                    raise ValueError("feedback_legacy_identity_conflict")
                state["records"][payload["exchange_id"]] = payload
                state["imports"][path.name] = fingerprint
            return state

        self._state.update(self._slot, migrate)
        archive = self._feedback_dir / "legacy-imported"
        archive.mkdir(exist_ok=True)
        for path, raw, _ in records:
            target = archive / path.name
            if target.exists() and target.read_text(encoding="utf-8") != raw:
                raise ValueError("feedback_legacy_archive_conflict")
            try:
                if path.read_text(encoding="utf-8") != raw:
                    raise ValueError("feedback_legacy_source_changed")
                path.replace(target)
            except FileNotFoundError:
                if not target.exists() or target.read_text(encoding="utf-8") != raw:
                    raise

    def _build_samples(self, window_start: datetime) -> list[ExchangeAnalyticsSample]:
        samples: list[ExchangeAnalyticsSample] = []
        feedback = self._collection(self._state.peek(self._slot))["records"]
        for record in self._conversation_store.list_records(since=window_start):
            if self._should_exclude_from_question_report(record):
                continue
            samples.append(
                ExchangeAnalyticsSample(
                    record=record,
                    feedback=(ConversationFeedbackRecord.from_dict(feedback[record.memory_id])
                              if record.memory_id in feedback else None),
                    token_set=frozenset(self._question_tokens(record.question)),
                )
            )
        samples.sort(key=lambda sample: sample.record.created_at, reverse=True)
        return samples

    def _should_exclude_from_question_report(self, record: ConversationMemoryRecord) -> bool:
        interaction_domain = (record.interaction_domain or "").strip().lower()
        workflow_action = (record.workflow_action or "").strip().lower()
        return interaction_domain == "booking" or workflow_action in {
            "book_meeting",
            "collect_booking_details",
        }

    def _build_clusters(self, samples: list[ExchangeAnalyticsSample]) -> list[ExchangeCluster]:
        clusters: list[ExchangeCluster] = []
        for sample in samples:
            best_cluster: ExchangeCluster | None = None
            best_score = 0.0
            for cluster in clusters:
                if cluster.interaction_domain != (sample.record.interaction_domain or "general"):
                    continue
                score = self._token_similarity(sample.token_set, cluster.token_set)
                if score > best_score:
                    best_cluster = cluster
                    best_score = score
            if best_cluster is not None and best_score >= 0.28:
                best_cluster.samples.append(sample)
                best_cluster.token_set.update(sample.token_set)
                continue
            representative = sample.record.question.strip()
            clusters.append(
                ExchangeCluster(
                    cluster_id=sample.record.memory_id,
                    label=representative[:80],
                    interaction_domain=sample.record.interaction_domain or "general",
                    token_set=set(sample.token_set),
                    samples=[sample],
                )
            )
        return clusters

    def _cluster_sort_key(self, cluster: ExchangeCluster) -> tuple[float, int]:
        return (self._cluster_priority_score(cluster), len(cluster.samples))

    def _cluster_priority_score(self, cluster: ExchangeCluster) -> float:
        unresolved_count = sum(1 for sample in cluster.samples if self._is_unresolved(sample))
        handoff_count = sum(
            1
            for sample in cluster.samples
            if sample.feedback and sample.feedback.needs_human_followup
        )
        negative_count = sum(
            1 for sample in cluster.samples if sample.feedback and sample.feedback.rating == "down"
        )
        return len(cluster.samples) + unresolved_count * 1.5 + handoff_count * 2.0 + negative_count

    def _cluster_gap_score(self, cluster: ExchangeCluster) -> float:
        unresolved_count = sum(1 for sample in cluster.samples if self._is_unresolved(sample))
        negative_count = sum(
            1 for sample in cluster.samples if sample.feedback and sample.feedback.rating == "down"
        )
        handoff_count = sum(
            1
            for sample in cluster.samples
            if sample.feedback and sample.feedback.needs_human_followup
        )
        zero_knowledge_count = sum(
            1 for sample in cluster.samples if sample.record.knowledge_hit_count == 0
        )
        return (
            negative_count * 2.0
            + unresolved_count * 1.5
            + handoff_count * 2.5
            + zero_knowledge_count * 0.5
        )

    def _serialize_cluster(self, cluster: ExchangeCluster) -> dict[str, object]:
        unresolved_count = sum(1 for sample in cluster.samples if self._is_unresolved(sample))
        negative_count = sum(
            1 for sample in cluster.samples if sample.feedback and sample.feedback.rating == "down"
        )
        handoff_count = sum(
            1
            for sample in cluster.samples
            if sample.feedback and sample.feedback.needs_human_followup
        )
        return {
            "cluster_id": cluster.cluster_id,
            "label": self._cluster_label(cluster),
            "interaction_domain": cluster.interaction_domain,
            "interaction_domain_label": _DOMAIN_LABELS.get(
                cluster.interaction_domain, cluster.interaction_domain
            ),
            "count": len(cluster.samples),
            "unresolved_count": unresolved_count,
            "negative_feedback_count": negative_count,
            "human_handoff_count": handoff_count,
            "sample_questions": [sample.record.question for sample in cluster.samples[:3]],
        }

    def _build_knowledge_gap_suggestion(self, cluster: ExchangeCluster) -> dict[str, object]:
        zero_knowledge_count = sum(
            1 for sample in cluster.samples if sample.record.knowledge_hit_count == 0
        )
        unresolved_count = sum(1 for sample in cluster.samples if self._is_unresolved(sample))
        handoff_count = sum(
            1
            for sample in cluster.samples
            if sample.feedback and sample.feedback.needs_human_followup
        )
        negative_count = sum(
            1 for sample in cluster.samples if sample.feedback and sample.feedback.rating == "down"
        )
        if zero_knowledge_count >= max(1, math.ceil(len(cluster.samples) / 2)):
            reason = "这类问题经常没有命中现成资料。"
            suggested_action = "优先补充离线知识材料或FAQ，并给该主题增加更明确的标签。"
        elif cluster.interaction_domain == "teaching":
            reason = "这类教学问题反复出现，但用户仍反馈没答好。"
            suggested_action = "补充对应讲次/Tutorial 摘要，并为讲次与主题建立更强绑定。"
        elif cluster.interaction_domain == "research":
            reason = "研究类问题出现重复追问或点踩。"
            suggested_action = "补充论文提炼、研究主线概述，并增强论文实体命中。"
        elif cluster.interaction_domain == "advising":
            reason = "指导建议类问题经常需要追问或人工接管。"
            suggested_action = "补充会前准备模板、议程模板和常见指导边界说明。"
        else:
            reason = "这类问题近期重复出现且反馈不稳定。"
            suggested_action = "整理成结构化FAQ，并补充管理员审核过的标准回复。"
        return {
            "cluster_id": cluster.cluster_id,
            "label": self._cluster_label(cluster),
            "interaction_domain": cluster.interaction_domain,
            "reason": reason,
            "suggested_action": suggested_action,
            "supporting_signals": {
                "unresolved_count": unresolved_count,
                "negative_feedback_count": negative_count,
                "human_handoff_count": handoff_count,
                "zero_knowledge_hit_count": zero_knowledge_count,
            },
            "sample_questions": [sample.record.question for sample in cluster.samples[:3]],
        }

    def _serialize_unresolved(self, sample: ExchangeAnalyticsSample) -> dict[str, object]:
        return {
            "exchange_id": sample.record.memory_id,
            "student_name": sample.record.student_name,
            "question": sample.record.question,
            "interaction_domain": sample.record.interaction_domain or "general",
            "interaction_domain_label": _DOMAIN_LABELS.get(
                sample.record.interaction_domain or "general",
                sample.record.interaction_domain or "general",
            ),
            "issue_summary": sample.feedback.issue_summary if sample.feedback else None,
            "needs_human_followup": bool(sample.feedback and sample.feedback.needs_human_followup),
            "created_at": sample.record.created_at,
        }

    def _build_handoff_categories(
        self, handoff_samples: list[ExchangeAnalyticsSample]
    ) -> list[dict[str, object]]:
        grouped: dict[str, list[ExchangeAnalyticsSample]] = {}
        for sample in handoff_samples:
            key = sample.record.interaction_domain or "general"
            grouped.setdefault(key, []).append(sample)
        total = len(handoff_samples) or 1
        categories = []
        for key, items in sorted(grouped.items(), key=lambda pair: len(pair[1]), reverse=True):
            categories.append(
                {
                    "category": key,
                    "category_label": _DOMAIN_LABELS.get(key, key),
                    "count": len(items),
                    "share": round(len(items) / total, 3),
                    "sample_questions": [sample.record.question for sample in items[:3]],
                }
            )
        return categories

    def _build_satisfaction_reason_summaries(
        self, feedback_samples: list[ExchangeAnalyticsSample]
    ) -> list[dict[str, object]]:
        problem_samples = [
            sample for sample in feedback_samples if self._is_problem_feedback(sample)
        ]
        total = len(problem_samples) or 1
        grouped: dict[str, list[ExchangeAnalyticsSample]] = {}
        for sample in problem_samples:
            key = self._satisfaction_reason_key(sample)
            grouped.setdefault(key, []).append(sample)
        summaries = []
        for key, samples in sorted(grouped.items(), key=lambda pair: len(pair[1]), reverse=True):
            summaries.append(
                {
                    "reason_key": key,
                    "reason_label": self._satisfaction_reason_label(key),
                    "count": len(samples),
                    "share": round(len(samples) / total, 3),
                    "sample_issues": [
                        sample.feedback.issue_summary or sample.record.question
                        for sample in samples[:3]
                        if sample.feedback is not None
                    ],
                }
            )
        return summaries

    def _build_satisfaction_trend(
        self, feedback_samples: list[ExchangeAnalyticsSample]
    ) -> list[dict[str, object]]:
        grouped: dict[date, list[ExchangeAnalyticsSample]] = {}
        for sample in feedback_samples:
            grouped.setdefault(sample.record.created_at.astimezone(UTC).date(), []).append(sample)
        trend = []
        for day, samples in sorted(grouped.items()):
            count = len(samples)
            positive_count = sum(
                1 for sample in samples if sample.feedback and sample.feedback.rating == "up"
            )
            unresolved_count = sum(1 for sample in samples if self._is_unresolved(sample))
            handoff_count = sum(
                1 for sample in samples if sample.feedback and sample.feedback.needs_human_followup
            )
            trend.append(
                {
                    "date": day,
                    "feedback_count": count,
                    "positive_rate": self._safe_ratio(positive_count, count),
                    "unresolved_rate": self._safe_ratio(unresolved_count, count),
                    "human_handoff_rate": self._safe_ratio(handoff_count, count),
                }
            )
        return trend

    def _is_problem_feedback(self, sample: ExchangeAnalyticsSample) -> bool:
        if sample.feedback is None:
            return False
        return (
            sample.feedback.rating == "down"
            or not sample.feedback.resolved
            or sample.feedback.needs_human_followup
        )

    def _satisfaction_reason_key(self, sample: ExchangeAnalyticsSample) -> str:
        feedback = sample.feedback
        text = " ".join(
            part
            for part in (feedback.issue_summary if feedback else None, sample.record.question)
            if part
        ).lower()
        if feedback and feedback.needs_human_followup:
            return "needs_human_followup"
        if any(keyword in text for keyword in ("知识", "资料", "命中", "材料", "faq", "reference")):
            return "knowledge_gap"
        if any(
            keyword in text
            for keyword in ("预约", "时间", "会议", "calendar", "booking", "schedule")
        ):
            return "booking_flow"
        if any(
            keyword in text
            for keyword in ("具体", "清单", "步骤", "不够", "缺少", "模糊", "detail")
        ):
            return "answer_specificity"
        return "other"

    def _satisfaction_reason_label(self, key: str) -> str:
        labels = {
            "needs_human_followup": "需要人工接管",
            "knowledge_gap": "知识/资料不足",
            "booking_flow": "预约流程问题",
            "answer_specificity": "回答不够具体",
            "other": "其他反馈",
        }
        return labels.get(key, key)

    def _safe_ratio(self, numerator: int, denominator: int) -> float:
        if denominator <= 0:
            return 0.0
        return round(numerator / denominator, 3)

    def _build_gap_draft_content(
        self,
        *,
        label: str,
        interaction_domain: str,
        reason: str,
        suggested_action: str,
        sample_questions: list[str],
    ) -> str:
        answer_outline = {
            "research": "先点名具体研究主题/论文，再用 2-3 句说明核心问题、方法和适用边界。",
            "teaching": "先定位讲次/Tutorial/实验，再概括本讲核心概念、关键术语和可继续看的材料。",
            "advising": "先给 checklist，再说明建议顺序，并明确哪些事项需要学生进一步补充上下文。",
            "booking": "先说明预约流程和当前状态，再列会前准备材料与管理员确认边界。",
            "general": "先用一段短答复回应，再补充 FAQ 化的标准说明与边界。",
        }
        examples = "\n".join(f"- {question}" for question in sample_questions[:3]) or "- 暂无样例"
        return (
            f"主题：{label}\n"
            f"问题域：{interaction_domain}\n\n"
            "适用问题样例：\n"
            f"{examples}\n\n"
            f"为何需要补充：{reason}\n"
            f"建议动作：{suggested_action}\n\n"
            "建议 FAQ/知识正文草稿：\n"
            f"1. {answer_outline.get(interaction_domain, answer_outline['general'])}\n"
            "2. 回答里尽量引用已有资料名、讲次名、论文名或流程步骤。\n"
            "3. 如果涉及老师本人审批、例外政策或个性化承诺，明确说明需要人工确认。\n\n"
            "建议补充标签：\n"
            f"- {interaction_domain}\n"
            "- analytics-gap\n"
            "- faq-draft\n"
        )

    def _cluster_label(self, cluster: ExchangeCluster) -> str:
        token_counter: Counter[str] = Counter()
        for sample in cluster.samples:
            token_counter.update(sample.token_set)
        keywords = [token for token, _ in token_counter.most_common(3) if len(token) >= 2]
        if keywords:
            return f"{_DOMAIN_LABELS.get(cluster.interaction_domain, cluster.interaction_domain)}｜{' / '.join(keywords)}"
        return cluster.label

    def _is_unresolved(self, sample: ExchangeAnalyticsSample) -> bool:
        feedback = sample.feedback
        if feedback is None:
            return False
        return feedback.rating == "down" or not feedback.resolved or feedback.needs_human_followup

    def _token_similarity(
        self, left: frozenset[str] | set[str], right: frozenset[str] | set[str]
    ) -> float:
        if not left or not right:
            return 0.0
        left_set = set(left)
        right_set = set(right)
        intersection = left_set & right_set
        union = left_set | right_set
        return len(intersection) / len(union)

    def _question_tokens(self, question: str) -> list[str]:
        lowered = question.lower()
        tokens = set(re.findall(r"[a-z0-9][a-z0-9_+-]{1,}", lowered))
        for chunk in re.findall(r"[\u4e00-\u9fff]{2,}", question):
            if chunk in _STOP_TOKENS:
                continue
            if len(chunk) <= 4:
                tokens.add(chunk)
            for index in range(len(chunk) - 1):
                token = chunk[index : index + 2]
                if token not in _STOP_TOKENS:
                    tokens.add(token)
        return sorted(token for token in tokens if token not in _STOP_TOKENS)
