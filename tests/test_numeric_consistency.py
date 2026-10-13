from __future__ import annotations

import pytest

from sage_faculty_twin.chat_delivery import AnswerDeliveryRejected, ChatDeliveryGate
from sage_faculty_twin.models import ChatResponse
from sage_faculty_twin.numeric_consistency import (
    explicit_percent_series,
    numeric_answer_issues,
    verified_percent_summary,
)


QUESTION = (
    "研究声称显存写入减少 50%，端到端延迟平均降低 1%。"
    "五次配对在线延迟变化是 -2%、+4%、-1%、+3%、0%。"
    "请计算均值和极差。"
)


def test_single_explicit_series_is_computed_without_unrelated_percentages() -> None:
    series = explicit_percent_series(QUESTION)
    assert series is not None
    assert str(series.mean) == "0.8"
    assert str(series.span) == "6"
    assert "+0.8%" in verified_percent_summary(QUESTION)
    assert "6 个百分点" in verified_percent_summary(QUESTION)


def test_clear_mean_and_range_mistakes_are_rejected() -> None:
    assert numeric_answer_issues(QUESTION, "算术平均值为 +0.8%，极差为 7 个百分点。") == (
        "explicit_percent_range_mismatch",
    )
    assert numeric_answer_issues(QUESTION, "平均变化为 -1%，极差为 6 个百分点。") == (
        "explicit_percent_mean_mismatch",
    )
    assert numeric_answer_issues(QUESTION, "算术平均值为 (-2+4-1+3+0)/5 = +0.8%，极差为 6 个百分点。") == ()


def test_ambiguous_or_unstructured_numbers_do_not_trigger_gate() -> None:
    assert explicit_percent_series("甲组 -2%、+4%、0%；乙组 -1%、+3%、0%。") is None
    assert numeric_answer_issues("组件降 50%，端到端升 1%。", "极差为 7 个百分点。") == ()
    assert numeric_answer_issues(QUESTION, "论文声称平均降低 1%，但这与数据矛盾。") == ()


def test_delivery_gate_fails_closed_on_explicit_range_error() -> None:
    with pytest.raises(AnswerDeliveryRejected, match="explicit_percent_range_mismatch"):
        ChatDeliveryGate().deliver(
            response=ChatResponse(
                answer="算术平均值为 +0.8%，波动范围达 7 个百分点。",
                owner_name="Owner",
                used_model="model",
            ),
            original_question=QUESTION,
        )
