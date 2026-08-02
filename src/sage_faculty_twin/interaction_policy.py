from __future__ import annotations


_REVIEW_DECISION_MARKERS = (
    "破例",
    "例外",
    "延期",
    "审批",
    "审核",
    "批准",
    "推荐信",
    "推荐一下",
    "能不能给我",
)

_LAB_JOINING_MARKERS = (
    "加入课题组",
    "加入你们组",
    "加入您的组",
    "加入您的课题组",
)

_DIRECT_LAB_ACCEPTANCE_MARKERS = (
    "能收我吗",
    "能不能收我",
    "可以收我吗",
    "愿意收我吗",
)

_LAB_ACCEPTANCE_DECISION_MARKERS = (
    "可以吗",
    "行吗",
    "能吗",
    "同意",
    "批准",
    "收我",
    "接收我",
    "录取我",
    "给我名额",
    "给个名额",
    "保证名额",
    "承诺",
)


def requires_faculty_review(question: str) -> bool:
    """Return whether the user is asking the faculty owner for a decision.

    Questions about preparing to join a lab are answerable advising requests.
    A lab-joining question enters review only when it also asks for acceptance,
    permission, a slot, or another commitment that the assistant cannot make.
    """

    normalized = question.strip().lower()
    if any(marker in normalized for marker in _REVIEW_DECISION_MARKERS):
        return True
    if any(marker in normalized for marker in _DIRECT_LAB_ACCEPTANCE_MARKERS):
        return True
    asks_to_join = any(marker in normalized for marker in _LAB_JOINING_MARKERS)
    asks_for_decision = any(
        marker in normalized for marker in _LAB_ACCEPTANCE_DECISION_MARKERS
    )
    return asks_to_join and asks_for_decision
