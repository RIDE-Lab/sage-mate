"""A topic-free starter must not be treated as a named course assignment."""

from sage_faculty_twin.question_relevance import is_underspecified_experiment_question


def test_generic_experiment_advice_requires_topic_before_using_course_materials() -> None:
    assert is_underspecified_experiment_question(
        "如果实验结果一般，怎么组织分析才更有说服力？"
    )
    assert is_underspecified_experiment_question(
        "如果实验结果不理想，如何解释这些结果？"
    )
    assert is_underspecified_experiment_question(
        "请先问我的研究问题、基线、指标和实际结果，再帮我组织实验分析；在这些信息不足时不要假设实验主题。"
    )


def test_named_experiment_keeps_its_topic() -> None:
    assert not is_underspecified_experiment_question(
        "如果 KV 实验结果一般，怎么组织分析？"
    )
    assert not is_underspecified_experiment_question(
        "数据库实验结果一般，怎么分析？"
    )
