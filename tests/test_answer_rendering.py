from sage_faculty_twin.answer_rendering import (
    grounded_course_fact_line,
    grounded_excerpt,
    render_sage_vllm_comparison,
)
from sage_faculty_twin.models import KnowledgeSearchHit


def _hit(title: str, excerpt: str) -> KnowledgeSearchHit:
    return KnowledgeSearchHit(
        document_id="rendering-test",
        title=title,
        excerpt=excerpt,
        score=1,
        tags=["teaching", "courseware"],
        source_name="test:course",
    )


def test_course_rendering_does_not_expose_worksheet_options() -> None:
    line = grounded_course_fact_line(
        _hit(
            "课件正文｜大模型推理系统与实践课程材料｜Tutorial 8 异构平台适配",
            "题目 1. [单选题] 跨平台之后，最先容易退化的是哪一层能力？",
        )
    )
    assert "Tutorial 8 异构平台适配" in line
    assert "单选题" not in line


def test_excerpt_prefers_fact_sentence_with_query_anchor() -> None:
    hit = _hit("研究资料", "背景说明。当前工作主要围绕大模型推理服务系统展开。附录。")
    assert grounded_excerpt(hit, "老师当前研究方向") == "当前工作主要围绕大模型推理服务系统展开。"


def test_stack_comparison_labels_experiments_as_proposals() -> None:
    answer = render_sage_vllm_comparison("比较 SAGE 与 vLLM-HUST，并给出三个本周实验")

    assert "上下层协作" in answer
    assert "统一 deadline" in answer
    assert "待验证" in answer
    assert "NPU 利用率" in answer
    assert "当前系统已经实现" in answer
