"""Conservative checks for arithmetic that is fully specified in a question.

This deliberately does not try to parse arbitrary research prose or infer which
experimental measurements are comparable. It only checks a single explicit
comma-separated series of percentage changes and explicit mean/range claims.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal
import re

_PERCENT = r"[+-]?\d+(?:\.\d+)?\s*%"
_SERIES = re.compile(rf"(?<![\d.]){_PERCENT}(?:\s*[、,，]\s*{_PERCENT}){{2,}}")
_NUMBER = r"[+-]?\d+(?:\.\d+)?"
_RANGE_CLAIM = re.compile(
    rf"(?:极差|波动范围|波动幅度|跨度)\s*(?:达|达到|为|是|约为)?\s*"
    rf"(?P<value>{_NUMBER})\s*(?:个)?百分点"
)
_MEAN_CLAIM = re.compile(
    rf"(?:算术平均(?:值)?|平均变化(?:值)?)\s*(?:为|是|等于|=)\s*"
    rf"(?:\([^)]{{1,80}}\)\s*/\s*\d+\s*=\s*)?"
    rf"(?P<value>{_NUMBER})\s*%"
)


@dataclass(frozen=True, slots=True)
class PercentSeries:
    values: tuple[Decimal, ...]

    @property
    def mean(self) -> Decimal:
        return sum(self.values) / len(self.values)

    @property
    def span(self) -> Decimal:
        return max(self.values) - min(self.values)


def explicit_percent_series(question: str) -> PercentSeries | None:
    matches = list(_SERIES.finditer(question))
    if len(matches) != 1:
        return None
    values = tuple(
        Decimal(token.replace("%", "").strip())
        for token in re.findall(_PERCENT, matches[0].group())
    )
    return PercentSeries(values) if len(values) >= 3 else None


def numeric_answer_issues(question: str, answer: str) -> tuple[str, ...]:
    series = explicit_percent_series(question)
    if series is None:
        return ()
    issues: list[str] = []
    for match in _RANGE_CLAIM.finditer(answer):
        if Decimal(match.group("value")) != series.span:
            issues.append("explicit_percent_range_mismatch")
            break
    for match in _MEAN_CLAIM.finditer(answer):
        if Decimal(match.group("value")) != series.mean:
            issues.append("explicit_percent_mean_mismatch")
            break
    return tuple(issues)


def verified_percent_summary(question: str) -> str:
    series = explicit_percent_series(question)
    if series is None:
        return ""
    return (
        "服务端已对题目中唯一明确的百分比序列做精确算术："
        f"算术平均变化为 {series.mean:+}%，极差为 {series.span} 个百分点。"
        "回答中如提及这两个数，必须与核算结果一致。"
    )
