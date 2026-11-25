"""Small, conservative guards for questions without an experiment topic."""

from __future__ import annotations

import re


_GENERIC_RESULT_QUESTION = re.compile(
    r"^(?:如果|假如|当)?(?:我的|这次)?实验(?:结果|效果)"
    r"(?:比较|不太)?(?:一般|不理想|未达预期|不显著|不明显|波动很大)"
    r"[，,]?(?:该|应该)?(?:怎么|如何|怎样).{0,30}"
    r"(?:分析|解释|组织|论证|说服).{0,16}$"
)


def is_underspecified_experiment_question(question: str) -> bool:
    """True only for topic-free method prompts, not named experiments."""
    normalized = re.sub(r"\s+", "", question).strip("。？?!！")
    if _GENERIC_RESULT_QUESTION.fullmatch(normalized):
        return True
    return (
        normalized.startswith("请先问我的研究问题、基线、指标和实际结果")
        and "实验分析" in normalized
        and "不要假设实验主题" in normalized
    )
