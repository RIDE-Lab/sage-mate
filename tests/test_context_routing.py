from types import SimpleNamespace

from sage_faculty_twin.context_routing import (
    expand_followup_question,
    is_light_request,
    looks_like_contextual_follow_up,
    question_needs_recent_context,
)


def test_context_followup_helpers_expand_only_short_followups() -> None:
    context = "1. User: 张老师目前研究什么\n2. Assistant: 研究推理系统"
    assert question_needs_recent_context("继续说说")
    assert looks_like_contextual_follow_up("继续说说", context)
    assert expand_followup_question("继续说说", context).startswith("张老师目前研究什么")
    long_question = "请问一个完整的新问题，不应拼接历史上下文，而且它会明确描述新的研究目标、数据集、评测指标和预期贡献"
    assert expand_followup_question(long_question, context).startswith("请问")


def test_light_request_rejects_contextual_or_expensive_options() -> None:
    assert is_light_request(SimpleNamespace(question="张老师是谁", attachments=None, course_context=None, web_search=False, deep_thinking_explicit=False))
    assert is_light_request(SimpleNamespace(question="继续", attachments=None, course_context=None, web_search=False, deep_thinking_explicit=False))
    assert not is_light_request(SimpleNamespace(question="张老师是谁", attachments=None, course_context=None, web_search=True, deep_thinking_explicit=False))
